"""The PLT04 consumer must not infer absence from a malformed process scan."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.core.judge import judge_evidence
from guard.executors import registry
from guard.executors.credential_exposure import CredentialExposureExecutor, parse_environment_names
from guard.models import ExecStatus, Verdict, canonical_json
from guard.models.checkdef import FunctionalProbe
from guard.runtime.docker import CmdResult
from guard.store import Store
from tests.builders import T0, baseline_pack, completion_response, environment, evidence, policy
from tests.test_credential_mount_semantics import FakeDocker, IMAGE, RUN, context


PRIVATE = "EXAMPLE_PRIVATE_ENVIRONMENT_VALUE_71a4"
VALUES = {"forbidden_env_names": ["FORBIDDEN_CANARY"], "forbidden_mount_paths": ["/credentials"],
          "credential_candidate_paths": []}


@pytest.mark.parametrize("text,names", [
    ("PID 1\nPATH\nGUARD_READ_OK\n", ["PATH"]),
    ("PID 1\n\nGUARD_READ_OK\n", []),
    ("PID 1\nZ\nA\nZ\nGUARD_READ_OK\nPID 42\nB\nGUARD_READ_OK\n", ["A", "B", "Z"]),
    ("PID 1\nPID\nPIDFILE\nPID_CONFIG\nGUARD_READ_SECRET\nGUARD_READ_OK\n",
     ["GUARD_READ_SECRET", "PID", "PIDFILE", "PID_CONFIG"]),
])
def test_identified_complete_records_keep_names_including_marker_prefixes(text, names):
    assert parse_environment_names(text, 0) == (names, True)


@pytest.mark.parametrize("text", [
    "", "PATH\nGUARD_READ_OK\n", "PID -1\nPATH\nGUARD_READ_OK\n",
    "PID 01\nPATH\nGUARD_READ_OK\n", "PID 2147483648\nPATH\nGUARD_READ_OK\n",
    "PID 1\nPATH\n", "PID 1\nGUARD_READ_ERROR\n",
    "PID 1\nPATH\nGUARD_READ_OK\nGUARD_READ_OK\n",
    "PID 1\nPATH\nGUARD_READ_OK\nPID 1\nPATH\nGUARD_READ_OK\n",
    "PID 1\nPATH\nPID 2\nGUARD_READ_OK\nGUARD_READ_OK\n",
    "PID 1\nPATH\nGUARD_READ_OK\nAFTER_COMPLETION\n",
    "PID 1\nPATH\nVALUE=" + PRIVATE + "\nGUARD_READ_OK\n",
    "PID 1\nPATH\nPID\t2\nFORBIDDEN_CANARY\nGUARD_READ_OK\n",
])
def test_incomplete_or_ambiguous_scan_cannot_prove_environment_absence(text):
    names, complete = parse_environment_names(text, 0)
    assert complete is False and PRIVATE not in json.dumps(names)


@pytest.mark.parametrize("tail", [
    "PID -1\nUNATTRIBUTED\nGUARD_READ_OK\n",
    "PID 1\nUNATTRIBUTED\nGUARD_READ_OK\n",
    "PID 2\nVALUE=" + PRIVATE + "\nGUARD_READ_ERROR\n",
    "PID 2\n",
])
def test_later_failure_keeps_prior_identified_names_without_borrowing_a_record(tail):
    names, complete = parse_environment_names("PID 1\nFORBIDDEN_CANARY\nGUARD_READ_OK\n" + tail, 0)
    assert names == ["FORBIDDEN_CANARY"] and complete is False


@pytest.mark.parametrize("returncode", [1, None, False])
def test_non_success_status_keeps_positive_names_but_no_complete_scan(returncode):
    assert parse_environment_names("PID 1\nFORBIDDEN_CANARY\nGUARD_READ_OK\n", returncode) == (
        ["FORBIDDEN_CANARY"], False)


class NameDocker(FakeDocker):
    def __init__(self, text, **kwargs):
        super().__init__(**kwargs)
        self.text = text

    def exec(self, *args, **kwargs):
        super().exec(*args, **kwargs)  # existing immutable container selection and command assertions
        return CmdResult(0, self.text, PRIVATE)


@pytest.mark.parametrize("text,expected,known_names", [
    ("PID -1\nFORBIDDEN_CANARY\nGUARD_READ_OK\n", Verdict.UNKNOWN, None),
    ("PID 1\nFORBIDDEN_CANARY\nGUARD_READ_OK\nPID -1\nVALUE=" + PRIVATE + "\n",
     Verdict.FAIL, ["FORBIDDEN_CANARY"]),
])
def test_consumer_and_existing_judge_distinguish_unattributed_from_prior_positive(text, expected, known_names):
    obs, normal, _, detail = CredentialExposureExecutor({}, docker=NameDocker(text)).collect(context(VALUES))
    assert obs["process_scan_complete"] == {"value": False, "collected": True}
    assert obs["worker_env_names"] == {"value": known_names, "collected": known_names is not None}
    assert "PROCESS_ENVIRONMENT_INCOMPLETE" in detail["collection_errors"]
    assert PRIVATE not in json.dumps([obs, detail])
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-04"], evidence(check_id="PLT-04", observations=obs, normal=normal),
                            policy(pack.ref(), values={"PLT-04": VALUES}), T0)
    assert result.verdicts.security == expected


@pytest.mark.parametrize("mode", ["wrong-id", "missing-id", "missing", "extra", "not-dict"])
def test_uncorrelated_normal_response_never_establishes_success_or_erases_prior_violations(mode):
    ctx = context(VALUES)
    if mode == "missing-id":
        ctx.check.functional_probes.append(FunctionalProbe(method="GET", path="/health", expect=[200], via="engine"))
    def probe(network, requests, timeout):
        rows = [{"id": req["id"], **completion_response(req)} for req in requests]
        if mode == "wrong-id": rows[0]["id"] = "EXAMPLE-other-request"
        elif mode == "missing-id": rows[0].pop("id")
        elif mode == "missing": rows = []
        elif mode == "extra": rows.append(deepcopy(rows[0]))
        else: rows = [None]
        return rows
    ctx.cprobe = probe
    obs, normal, _, detail = CredentialExposureExecutor({}, docker=NameDocker(
        "PID 1\nFORBIDDEN_CANARY\nGUARD_READ_OK\n", mounts=[{"Type": "bind", "Destination": "/credentials"}])).collect(ctx)
    assert normal["attempted"] is True and normal["succeeded"] is False
    assert obs["functional_probe_results"]["value"] == {fp.id: "no_response" for fp in ctx.check.functional_probes}
    assert obs["worker_env_names"]["value"] == ["FORBIDDEN_CANARY"]
    assert obs["forbidden_mount_violations"]["value"] == ["/credentials"]
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-04"], evidence(check_id="PLT-04", observations=obs, normal=normal),
                            policy(pack.ref(), values={"PLT-04": VALUES}), T0)
    assert result.verdicts.security == Verdict.FAIL and result.verdicts.functional == Verdict.UNKNOWN
    assert detail["collection_errors"] == ["FUNCTIONAL_PROBE_UNAVAILABLE"]


@pytest.mark.parametrize("known_violation,attachment_failure", [(False, False), (True, False), (True, True)])
def test_registered_consumer_cli_preserves_original_refs_and_partial_fact_verdicts(
        tmp_path, monkeypatch, known_violation, attachment_failure):
    text = ("PID 1\nFORBIDDEN_CANARY\nGUARD_READ_OK\n" if known_violation else "") + (
        "PID -1\nVALUE=" + PRIVATE + "\nGUARD_READ_OK\n")
    docker = NameDocker(text)
    def probe(network, requests, timeout):
        rows = [completion_response(req) for req in requests]
        rows[0]["id"] = "EXAMPLE-unrelated-response"
        return rows
    env = environment(engine={"name": "engine-x", "version": "1.0.0", "image_digest": IMAGE}, model={"id": "test"})
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab": True, "probe_image": "EXAMPLE-unused", "run_id": RUN,
        "environment_ref": env.ref().model_dump(mode="json"),
        "engine": {"container": "engine", "trusted_url": "http://engine:8000", "ready_timeout": 1},
        "origins": {"trusted": {"network": "trusted"}}, "gateway": {"url": "http://gateway:8080"}}))
    monkeypatch.setitem(registry.EXECUTORS, "credential_exposure:real",
        lambda raw: CredentialExposureExecutor({"target": str(target)}, container_probe=probe, docker=docker))
    if attachment_failure:
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name == "probe_log.jsonl" and args and args[0] == "w":
                raise OSError(PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_file)
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={"include": ["PLT-04"]}, values={"PLT-04": VALUES})
    for name, obj in (("env", env), ("pol", pol)):
        (tmp_path / (name + ".json")).write_bytes(canonical_json(obj.to_dict()))
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\ncredential_exposure="real"\n[adapter]\nname="vllm"\n', encoding="utf-8")
    store_path, restored_path = tmp_path / "store", tmp_path / "restored"
    common = ["--config", str(config), "--store", str(store_path)]
    runner = CliRunner()
    def cli(argv):
        value = runner.invoke(app, list(map(str, argv)))
        assert value.exit_code == 0, (argv, value.output)
    cli(["select", "--pack", pack.path, "--env", tmp_path / "env.json", "--policy", tmp_path / "pol.json", *common])
    plan, = Store.read_only(store_path).iter_kind("PLAN")
    cli(["run", "--plan", plan.id, "--pack", pack.path, *common])
    cli(["judge", "--plan", plan.id, "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan.id, "--as-of", T0.isoformat(), "--json", tmp_path / "report.json", *common])
    cli(["verify", *common])
    original_store = Store.read_only(store_path)
    evd, = original_store.iter_kind("EVD")
    res, = original_store.iter_kind("RES")
    assert evd.data_origin.value == "EXAMPLE"
    assert evd.execution.status == (ExecStatus.ABORTED if attachment_failure else ExecStatus.COMPLETED)
    assert res.verdicts.security == (Verdict.FAIL if known_violation else Verdict.UNKNOWN)
    assert res.verdicts.functional == Verdict.UNKNOWN
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    assert evd.observations["process_scan_complete"].value is False
    objects = [obj for kind in ("ENV", "POL", "PLAN", "EVD", "RES", "RPT") for obj in original_store.iter_kind(kind)]
    assert len(objects) == 6
    cli(["evidence", "export", "--store", store_path, "--output", tmp_path / "bundle.zip"])
    cli(["evidence", "restore", "--bundle", tmp_path / "bundle.zip", "--destination", restored_path])
    assert all(Store.read_only(restored_path).resolve(obj.ref()).to_dict() == obj.to_dict() for obj in objects)
    cli(["judge", "--plan", plan.id, "--as-of", T0.isoformat(), "--store", restored_path, "--config", config])
    assert [obj.ref() for obj in Store.read_only(restored_path).iter_kind("RES")] == [res.ref()]
    cli(["verify", "--store", restored_path, "--config", config])
