"""Mount prohibition and worker-subject metadata are separate contracts."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from guard.executors.credential_candidates import (MAX_OUTPUT_BYTES, METADATA_PROBE, PROBE_TIMEOUT,
                                                  candidate_paths)
from guard.executors.credential_exposure import CredentialExposureExecutor, ENV_CMD
from guard.models.checkdef import FunctionalProbe, ObsSpec
from guard.runtime.docker import CmdResult
from tests.builders import baseline_pack, completion_response, environment

IMAGE = "example.invalid/engine@sha256:" + "a" * 64
RUN = "EXAMPLE-credential-owner"


class FakeDocker:
    def __init__(self, *, mounts=(), state="absent", environment_error=False, metadata_error=False,
                 process_names=(), subject_matches=True, source_changed=False):
        self.mounts = list(mounts)
        self.state = state
        self.environment_error, self.metadata_error = environment_error, metadata_error
        self.process_names, self.subject_matches = process_names, subject_matches
        self.source_changed, self.inspections = source_changed, 0
        self.requests = []
        self.exec_targets = []
        self.payload_changes = {}
        self.capture_changes = {}

    def inspect_json(self, container, *, timeout):
        self.inspections += 1
        return {"Id": "b" * 64 if self.source_changed and self.requests else "a" * 64,
                "Name": "/engine", "Config": {"Image": IMAGE, "Labels": {"guard.run": RUN}},
                "Image": "sha256:" + "b" * 64, "State": {"Running": True, "StartedAt": "EXAMPLE-T0"},
                "Mounts": deepcopy(self.mounts)}

    def exec(self, container, argv, *, timeout):
        self.exec_targets.append(container)
        assert argv == ["sh", "-c", ENV_CMD]
        if self.environment_error:
            raise RuntimeError("EXAMPLE private environment reader detail")
        return CmdResult(0, "PID 1\nPATH\n" + "".join(name + "\n" for name in self.process_names)
                         + "GUARD_READ_OK\n", "")

    def exec_bounded(self, container, argv, *, timeout, max_bytes):
        self.exec_targets.append(container)
        assert argv[:3] == ["python3", "-c", METADATA_PROBE]
        assert timeout == PROBE_TIMEOUT and max_bytes == MAX_OUTPUT_BYTES
        paths = json.loads(argv[3])
        self.requests.append(paths)
        if self.metadata_error:
            raise TimeoutError("EXAMPLE private candidate reader detail")
        basis = {"absent": "PATH_ABSENT", "denied": "EFFECTIVE_READ_PERMISSION",
                 "candidate_readable": "EFFECTIVE_READ_PERMISSION", "unknown": "SYMLINK_NOT_FOLLOWED"}
        payload = {"schema": "guard-credential-metadata/1",
                   "worker_subject_matches": self.subject_matches, "worker_inventory_stable": True,
                   "workers_examined": 2, "errors": [],
                   "paths": {path: {"state": self.state, "stable": True,
                                    "basis": basis.get(self.state, "EXAMPLE_UNREVIEWED")} for path in paths}}
        payload.update(deepcopy(self.payload_changes))
        capture = {"bounded_at_source": True, "complete": True, "truncated": False, "timed_out": False}
        capture.update(self.capture_changes)
        raw = json.dumps(payload).encode()
        return CmdResult(0, raw.decode(), "", stdout_bytes=raw, stderr_bytes=b"", capture=capture)


def context(values, *, legacy=False, functional_error=False):
    probe = FunctionalProbe(method="POST", path="/v1/completions", body={"model": "{model}",
                            "prompt": "ping", "max_tokens": 1}, expect=[200], via="engine")
    observation = "exposed_credential_paths" if legacy else "forbidden_mount_violations"
    check = SimpleNamespace(required_observations=[ObsSpec(key=observation, type="list[str]")],
                            functional_probes=[probe], challenge_steps=["mounts", "environment", "control"])

    def request(rid, base, prepared, key, want_body=False):
        return {"id": rid, "url": base + prepared.path, "method": prepared.method,
                "body": prepared.body, "want_body": want_body}

    def functional(network, requests, timeout):
        if functional_error:
            raise RuntimeError("EXAMPLE private functional reader detail")
        return [completion_response(request) for request in requests]

    env = environment(engine={"name": "engine-x", "version": "1.0.0", "image_digest": IMAGE}, model={"id": "test"})
    return SimpleNamespace(check=check, policy_values={"forbidden_env_names": ["FORBIDDEN_CANARY"],
        "forbidden_mount_paths": [], **values}, target={"engine": {"container": "engine"},
        "run_id": RUN, "environment_ref": env.ref().model_dump(mode="json")},
        env=env, request=request,
        engine_url="http://engine:8000", keys={}, trusted_net="trusted", timeout=3, cprobe=functional)


def collect(docker, values=None, **kwargs):
    ctx = context(values or {}, **kwargs)
    return CredentialExposureExecutor({}, docker=docker).collect(ctx)


@pytest.mark.parametrize("destination", ["/credentials", "/credentials/key", "/"])
def test_explicit_mount_ban_is_recorded_without_claiming_credential_access(destination):
    docker = FakeDocker(mounts=[{"Type": "bind", "Destination": destination}], state="absent")
    observations, _, _, detail = collect(docker, {"forbidden_mount_paths": ["/credentials"],
                                                "credential_candidate_paths": ["/credentials/key"]})
    assert observations["forbidden_mount_violations"] == {"value": ["/credentials"], "collected": True}
    assert observations["credential_candidate_results"]["value"] == {"/credentials/key": "absent"}
    assert "exposed_credential_paths" not in observations
    assert detail["forbidden_mount_violations"] == ["/credentials"]


def test_missing_candidate_policy_is_unknown_and_explicit_empty_does_not_probe():
    missing, empty = FakeDocker(), FakeDocker()
    observations, _, _, detail = collect(missing)
    assert observations["credential_candidate_results"] == {"value": {}, "collected": True}
    assert observations["credential_candidates_planned"] == {"value": [], "collected": True}
    assert observations["credential_candidates_complete"]["value"] is False
    assert detail["candidate_request"] == "missing"
    assert detail["candidate_configured"] is False
    observations, _, _, detail = collect(empty, {"credential_candidate_paths": []})
    assert observations["credential_candidate_results"] == {"value": {}, "collected": True}
    assert observations["credential_candidates_complete"]["value"] is True
    assert detail["candidate_request"] == "explicit_empty"
    assert detail["candidate_configured"] is True
    assert missing.requests == empty.requests == []
    assert missing.inspections == empty.inspections == 7


@pytest.mark.parametrize("state, complete", [("absent", True), ("denied", True),
                                           ("candidate_readable", False), ("unknown", False),
                                           ("canary_readable", False)])
def test_worker_subject_metadata_states_and_unproved_canary_are_distinct(state, complete):
    docker = FakeDocker(state=state)
    observations, _, _, detail = collect(docker, {"credential_candidate_paths": ["/credentials/key"]})
    expected = "unknown" if state == "canary_readable" else state
    assert observations["credential_candidate_results"]["value"] == {"/credentials/key": expected}
    assert observations["credential_candidates_complete"]["value"] is complete
    assert detail["candidate_metadata"]["scope"].startswith("effective read permission only")


@pytest.mark.parametrize("change", ["subject", "inventory", "truncated", "unbounded", "timeout", "source"])
def test_missing_worker_identity_or_complete_transport_never_establishes_access(change):
    docker = FakeDocker(state="candidate_readable", subject_matches=change != "subject", source_changed=change == "source")
    if change == "inventory": docker.payload_changes["worker_inventory_stable"] = False
    if change == "truncated": docker.capture_changes["truncated"] = True
    if change == "unbounded": docker.capture_changes["bounded_at_source"] = False
    if change == "timeout": docker.capture_changes["timed_out"] = True
    observations, _, _, detail = collect(docker, {"credential_candidate_paths": ["/credentials/key"]})
    assert observations["credential_candidate_results"]["value"] == {"/credentials/key": "unknown"}
    assert observations["credential_candidates_complete"]["value"] is False
    assert detail["candidate_metadata"]["code"] != "WORKER_SUBJECT_METADATA_VERIFIED"


def test_nested_mount_is_shadowed_instead_of_claiming_underlying_file_absence():
    docker = FakeDocker(mounts=[{"Type": "bind", "Destination": "/credentials"},
                                {"Type": "volume", "Destination": "/credentials/hidden"}])
    observations, _, _, _ = collect(docker, {"credential_candidate_paths": ["/credentials/hidden/key"]})
    assert observations["credential_candidate_results"]["value"] == {"/credentials/hidden/key": "shadowed"}
    assert observations["credential_candidates_complete"]["value"] is False
    assert docker.requests == []


@pytest.mark.parametrize("environment_error, metadata_error, functional_error", [(True, False, False),
                            (False, True, False), (False, False, True)])
def test_independent_mount_and_environment_facts_survive_other_reader_errors(environment_error, metadata_error, functional_error):
    docker = FakeDocker(mounts=[{"Type": "bind", "Destination": "/credentials"}],
                        process_names=["FORBIDDEN_CANARY"], environment_error=environment_error,
                        metadata_error=metadata_error)
    observations, normal, _, detail = collect(docker, {"forbidden_mount_paths": ["/credentials"],
                    "credential_candidate_paths": ["/credentials/key"]}, functional_error=functional_error)
    assert observations["forbidden_mount_violations"]["value"] == ["/credentials"]
    if not environment_error:
        assert "FORBIDDEN_CANARY" in observations["worker_env_names"]["value"]
    if functional_error: assert normal["succeeded"] is False
    assert "private" not in json.dumps([observations, detail])


def test_saved_definition_keeps_legacy_exposure_observation_and_makes_no_candidate_call():
    docker = FakeDocker(mounts=[{"Type": "bind", "Destination": "/credentials"}])
    observations, _, _, _ = collect(docker, {"forbidden_mount_paths": ["/credentials/key"]}, legacy=True)
    assert observations["exposed_credential_paths"]["value"] == ["/credentials/key"]
    assert "forbidden_mount_violations" not in observations and "credential_candidate_results" not in observations
    assert docker.requests == []


def test_known_environment_violation_survives_missing_mount_inventory():
    class MissingMounts(FakeDocker):
        def inspect_json(self, container, *, timeout):
            row = super().inspect_json(container, timeout=timeout)
            # Fail the inventory source, after the initial owner check. The
            # environment interval still has its own verified owner checks.
            if self.inspections == 2:
                raise RuntimeError("EXAMPLE private inspect detail")
            return row
    observations, _, _, detail = collect(MissingMounts(process_names=["FORBIDDEN_CANARY"]),
                                          {"credential_candidate_paths": ["/credentials/key"]})
    assert observations["forbidden_mount_violations"] == {"value": None, "collected": False}
    assert "FORBIDDEN_CANARY" in observations["worker_env_names"]["value"]
    assert observations["credential_candidate_results"]["value"] == {"/credentials/key": "unknown"}
    assert "private" not in json.dumps(detail)


@pytest.mark.parametrize("bad", [None, {}, {"Type": "bind", "Destination": "relative"},
    {"Type": "bind", "Destination": "/credentials/../other"},
    {"Type": "bind", "Destination": "/credentials//key"},
    {"Type": "bind", "Destination": "/credentials\x00/key"}])
@pytest.mark.parametrize("destination", ["/credentials", "/credentials/key", "/"])
def test_partial_mount_list_preserves_independent_violation_without_candidate_absence(bad, destination):
    from guard.core.judge import judge_evidence
    from guard.models import Verdict
    from tests.builders import T0, evidence, policy

    docker = FakeDocker(mounts=[bad, {"Type": "bind", "Destination": destination}])
    values = {"forbidden_env_names": [], "forbidden_mount_paths": ["/credentials"],
              "credential_candidate_paths": ["/credentials/key"]}
    observations, normal, _, detail = collect(docker, values)
    assert observations["forbidden_mount_violations"] == {"value": ["/credentials"], "collected": True}
    assert observations["credential_candidate_results"]["value"] == {"/credentials/key": "unknown"}
    assert observations["credential_candidates_complete"]["value"] is False and docker.requests == []
    assert "MOUNT_INVENTORY_INCOMPLETE" in detail["collection_errors"]
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-04"], evidence(check_id="PLT-04", observations=observations,
        normal=normal), policy(pack.ref(), values={"PLT-04": values}), T0)
    assert result.verdicts.security == Verdict.FAIL


@pytest.mark.parametrize("mounts", [[None], [{"Type": "bind", "Destination": "/credentials//key"}],
    [{"Type": "bind", "Destination": "/models"}, None]])
def test_partial_mount_list_without_known_violation_cannot_prove_empty_safe_inventory(mounts):
    from guard.core.judge import judge_evidence
    from guard.models import Verdict
    from tests.builders import T0, evidence, policy

    values = {"forbidden_env_names": [], "forbidden_mount_paths": ["/credentials"],
              "credential_candidate_paths": []}
    observations, normal, _, detail = collect(FakeDocker(mounts=mounts), values)
    assert observations["forbidden_mount_violations"] == {"value": None, "collected": False}
    assert detail["forbidden_mount_violations"] is None
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-04"], evidence(check_id="PLT-04", observations=observations,
        normal=normal), policy(pack.ref(), values={"PLT-04": values}), T0)
    assert result.verdicts.security == Verdict.UNKNOWN


def test_functional_preparation_failure_preserves_mount_and_environment_facts(monkeypatch):
    import guard.executors.credential_exposure as collector

    def unavailable(*args, **kwargs):
        raise ValueError("EXAMPLE_PRIVATE_PREPARATION_CANARY")

    monkeypatch.setattr(collector, "functional_requests", unavailable)
    observations, normal, _, detail = collect(FakeDocker(
        mounts=[{"Type": "bind", "Destination": "/credentials"}], process_names=["FORBIDDEN_CANARY"]),
        {"forbidden_mount_paths": ["/credentials"]})
    assert observations["forbidden_mount_violations"]["value"] == ["/credentials"]
    assert "FORBIDDEN_CANARY" in observations["worker_env_names"]["value"]
    assert observations["functional_probe_results"]["value"] == {"POST /v1/completions": "no_response"}
    assert normal["attempted"] is False and normal["succeeded"] is False
    assert detail["collection_errors"] == ["FUNCTIONAL_PROBE_UNAVAILABLE"]
    assert "EXAMPLE_PRIVATE_PREPARATION_CANARY" not in json.dumps([observations, detail])


@pytest.mark.parametrize("artifact_failure", [False, True])
def test_partial_mount_product_storage_report_and_exact_ref_replay(tmp_path, monkeypatch, artifact_failure):
    from pathlib import Path
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.executors import registry
    from guard.models import ExecStatus, Verdict, canonical_json
    from guard.store import Store
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack, put_pack
    from tests.builders import PACK_DIR, environment, policy

    env = environment(engine={"name": "engine-x", "version": "1.0.0", "image_digest": IMAGE}, model={"id": "test"})
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab": True, "probe_image": "EXAMPLE-unused",
        "run_id": RUN, "environment_ref": env.ref().model_dump(mode="json"),
        "engine": {"container": "engine", "trusted_url": "http://engine:8000", "ready_timeout": 1},
        "origins": {"trusted": {"network": "trusted"}}, "gateway": {"url": "http://gateway:8080"}}))
    docker = FakeDocker(mounts=[{"Type": "bind", "Destination": "/credentials"}, None],
                        environment_error=True)
    cfg = {"target": str(target)}
    def probe(network, requests, timeout):
        return [completion_response(request) for request in requests]
    monkeypatch.setitem(registry.EXECUTORS, "credential_exposure:partial-sim",
        lambda raw: CredentialExposureExecutor(cfg, container_probe=probe, docker=docker))
    if artifact_failure:
        original_open = Path.open
        def open_path(path, *args, **kwargs):
            if path.name == "probe_log.jsonl" and args and args[0] == "w":
                raise OSError("EXAMPLE_PRIVATE_ARTIFACT_CANARY")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_path)
    store, pack = Store(tmp_path / "store"), baseline_pack()
    values = {"forbidden_env_names": [], "forbidden_mount_paths": ["/credentials"],
              "credential_candidate_paths": ["/credentials/key"]}
    pol = policy(pack.ref(), scope={"include": ["PLT-04"], "exclude": []}, values={"PLT-04": values})
    store.put_many([env, pol]); plan = select(pack, [env], pol); store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"credential_exposure": "partial-sim"}, adapter="vllm"))
    result, = judge_plan(store, pack, plan, as_of=env.created_at)
    assert evd.execution.status == (ExecStatus.ABORTED if artifact_failure else ExecStatus.COMPLETED)
    assert evd.data_origin.value == "EXAMPLE" and result.verdicts.security == Verdict.FAIL
    assert evd.observations["forbidden_mount_violations"].value == ["/credentials"]
    assert evd.execution.executor.version == "0.5.1"
    assert "EXAMPLE_PRIVATE_ARTIFACT_CANARY" not in canonical_json(evd.to_dict()).decode()
    assert bool(evd.artifacts) is not artifact_failure
    report = build_report(store, pack, plan, as_of=env.created_at)
    row, = [row for row in report.by_check if row.check_id == "PLT-04"]
    assert row.single.security == Verdict.FAIL
    assert result.ref() in report.inputs_manifest and evd.ref() in report.inputs_manifest
    store.put(report)
    put_pack(store, PACK_DIR)
    original = evd.to_dict()
    export_bundle(store.root, tmp_path / "partial.zip")
    restore_bundle(tmp_path / "partial.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.verify() == [] and restored.resolve(evd.ref()).to_dict() == original
    assert restored.resolve(report.ref()).to_dict() == report.to_dict()
    replayed, = judge_plan(restored, get_pack(restored, pack.ref()), restored.resolve(plan.ref()), as_of=env.created_at)
    assert replayed.ref() == result.ref() and replayed.verdicts.security == Verdict.FAIL


def test_environment_name_with_marker_prefix_is_retained_and_remains_fail():
    from guard.core.judge import judge_evidence
    from guard.models import Verdict
    from tests.builders import T0, baseline_pack, evidence, policy

    docker = FakeDocker(process_names=["GUARD_READ_SECRET"])
    values = {"forbidden_env_names": ["GUARD_READ_SECRET"], "forbidden_mount_paths": [],
              "credential_candidate_paths": []}
    observations, normal, _, _ = collect(docker, values)
    assert observations["process_scan_complete"]["value"] is True
    assert "GUARD_READ_SECRET" in observations["worker_env_names"]["value"]
    pack = baseline_pack()
    evd = evidence(check_id="PLT-04", observations=observations, normal=normal)
    result = judge_evidence(pack.checks["PLT-04"], evd, policy(pack.ref(), values={"PLT-04": values}), T0)
    assert result.verdicts.security == Verdict.FAIL


def test_unreviewed_metadata_basis_is_not_persisted_as_evidence():
    docker = FakeDocker(state="candidate_readable")
    docker.payload_changes["paths"] = {"/credentials/key": {"state": "candidate_readable", "stable": True,
                                                              "basis": "EXAMPLE private raw error"}}
    observations, _, _, detail = collect(docker, {"credential_candidate_paths": ["/credentials/key"]})
    assert observations["credential_candidate_results"]["value"] == {"/credentials/key": "unknown"}
    assert "private" not in json.dumps(detail)


@pytest.mark.parametrize("paths", [["relative"], ["/a/../b"], ["/a//b"], ["/a", "/a"],
                                   ["/a\x00b"], ["/" + "a" * 129], ["/a"] * 33, "[]"])
def test_candidate_path_bounds_reject_before_any_metadata_command(paths):
    with pytest.raises(ValueError): candidate_paths(paths)


def test_candidate_path_list_preserves_complete_paths_order_and_caller_input():
    original = ["/credentials/second", "/credentials/first"]
    copied = candidate_paths(original)
    assert copied == original
    assert copied is not original
    copied.reverse()
    assert original == ["/credentials/second", "/credentials/first"]


def test_fixed_probe_has_valid_python_syntax_without_executing_any_file_probe():
    import ast
    ast.parse(METADATA_PROBE)


@pytest.mark.parametrize("path", ["/credentials/./key", "/credentials//key", "//credentials/key",
                                  "/credentials/key/", "/credentials/key\x00"])
def test_new_mount_ban_rejects_noncanonical_paths_instead_of_missing_overlap(path):
    with pytest.raises(ValueError, match="canonical"):
        CredentialExposureExecutor({}).validate_policy(baseline_pack().checks["PLT-04"],
            {"forbidden_env_names": [], "forbidden_mount_paths": [path]})


@pytest.mark.parametrize("invalid", ["relative_candidate", "relative_mount", "invalid_env_name"])
@pytest.mark.parametrize("claim", ["PASS", "FAIL"])
def test_manual_invalid_policy_caps_only_security_pass_and_preserves_fail_and_function(invalid, claim):
    from tests.test_response_semantics_integration import judge, observations, values

    pack = baseline_pack()
    supplied, data = values("PLT-04"), observations("PLT-04")
    if invalid == "relative_candidate":
        supplied["credential_candidate_paths"] = data["credential_candidates_planned"] = ["relative"]
        data["credential_candidate_results"] = {"relative": "absent"}
    elif invalid == "relative_mount":
        supplied["forbidden_mount_paths"] = ["../credentials"]
    else:
        supplied["forbidden_env_names"] = ["INVALID=ENVIRONMENT_VALUE"]
    with pytest.raises(ValueError):
        CredentialExposureExecutor({}).validate_policy(pack.checks["PLT-04"], supplied)
    _evd, result = judge(pack, pack.checks["PLT-04"], data, supplied=supplied, claimed=claim)
    assert result.verdicts.security.value == ("UNKNOWN" if claim == "PASS" else "FAIL")
    assert result.verdicts.functional.value == "PASS"
    assert "INVALID=ENVIRONMENT_VALUE" not in " ".join(result.reasons)


def test_manual_invalid_policy_cannot_raise_registry_error_or_leak_details(monkeypatch):
    from guard import policy_ceilings
    from tests.test_response_semantics_integration import judge, observations

    def broken(*args):
        raise RuntimeError("EXAMPLE_PRIVATE_POLICY_DETAIL")
    monkeypatch.setitem(policy_ceilings.MANUAL_PASS_CEILINGS, "credential_exposure", broken)
    pack = baseline_pack()
    for claim in ("PASS", "FAIL"):
        _evd, result = judge(pack, pack.checks["PLT-04"], observations("PLT-04"), claimed=claim)
        assert result.verdicts.security.value == ("UNKNOWN" if claim == "PASS" else "FAIL")
        assert result.verdicts.functional.value == "PASS"
        assert "EXAMPLE_PRIVATE_POLICY_DETAIL" not in " ".join(result.reasons)


def test_manual_policy_ceiling_does_not_reinterpret_saved_legacy_definition():
    from guard.executors.credential_exposure import manual_pass_ceiling
    from tests.response_semantics_support import with_legacy_check

    old = with_legacy_check(baseline_pack(), "PLT-04").checks["PLT-04"]
    assert manual_pass_ceiling(old, {"forbidden_mount_paths": ["relative"]}, None) == {}


def test_credential_policy_callbacks_are_pure_and_deterministic(monkeypatch):
    import builtins
    import io
    import os
    from pathlib import Path
    import socket
    import subprocess
    import time

    from guard import policy_ceilings
    from guard.credential_policy import candidate_paths, validate_credential_policy

    check = baseline_pack().checks["PLT-04"]
    values = {"forbidden_env_names": ["EXAMPLE_SECRET"], "forbidden_mount_paths": ["/credentials"],
              "credential_candidate_paths": ["/credentials/key"]}
    invalid = [{**values, "credential_candidate_paths": ["relative"]},
               {**values, "forbidden_mount_paths": ["relative"]},
               {**values, "forbidden_env_names": ["INVALID=VALUE"]}]
    before = deepcopy([values, *invalid])
    side_effects, outcomes, validation_errors = [], [], []

    def blocked(*args, **kwargs):
        side_effects.append("unexpected filesystem/network/process/clock operation")
        raise AssertionError("pure policy callback attempted a side effect")

    with monkeypatch.context() as guard:
        for owner, names in (
            (builtins, ("open",)), (io, ("open",)),
            (Path, ("open", "read_text", "read_bytes", "stat")),
            (os, ("open", "stat", "lstat", "access", "listdir", "scandir", "readlink")),
            (socket, ("socket", "create_connection")),
            (subprocess, ("run", "Popen")),
            (time, ("time", "time_ns", "monotonic", "monotonic_ns", "perf_counter", "perf_counter_ns")),
        ):
            for name in names:
                guard.setattr(owner, name, blocked)
        for _ in range(2):
            validate_credential_policy(check, values)
            outcomes.append((candidate_paths(values["credential_candidate_paths"]),
                             policy_ceilings.manual_pass_ceiling(check, values, None)))
            for supplied in invalid:
                try:
                    validate_credential_policy(check, supplied)
                except ValueError:
                    validation_errors.append(True)
                else:
                    validation_errors.append(False)
                outcomes.append(policy_ceilings.manual_pass_ceiling(check, supplied, None))

    assert outcomes == [(["/credentials/key"], {}), *[{"security": "UNKNOWN"}] * 3] * 2
    assert validation_errors == [True] * 6
    assert side_effects == []
    assert [values, *invalid] == before
