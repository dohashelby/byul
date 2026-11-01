"""Dedicated consumers use the live common process reader and its injected runner."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.core.judge import judge_evidence
from guard.executors import registry
from guard.executors.container_config import collect_kubernetes_process_permissions, read_kubernetes_process_observations
from guard.executors.http_common import Ctx
from guard.executors.kubernetes_credential_exposure import KubernetesCredentialExposureExecutor
from guard.models import ExecStatus, Verdict, canonical_json, sha256_hex
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.runtime.kubernetes_process import SCHEMA, SOURCE_SHA256
from guard.store import Store
from tests.builders import T0, baseline_pack, evidence, policy
from tests.test_model_preparation_integration import FullWorld, full_inputs


PRIVATE = "EXAMPLE_PRIVATE_PROCESS_VALUE_63b"
AT = "2026-10-29T00:00:00+00:00"
PERMISSION_VALUES = {"allowed_capabilities": [], "allowed_mounts": [], "allowed_devices": [],
                     "require_non_root": True, "allowed_exec_writable_paths": []}
CREDENTIAL_VALUES = {"forbidden_env_names": ["FORBIDDEN_CANARY"], "forbidden_mount_paths": ["/models"]}


def packet(selected, kind="normal", mode=None):
    def process(pid, uid, cap):
        return {"pid": pid, "start_ticks": 100 + pid, "name": "EXAMPLE-process",
                "uids": [uid] * 4, "uid_effective": uid, "cap_eff": cap, "mount_namespace_inode": 10,
                "environment_names": ["FORBIDDEN_CANARY", "PATH"], "environment_names_complete": True,
                "pid_identity_stable": True, "started_at": AT, "finished_at": AT, "diagnostic_codes": []}
    rows = [process(1, 0 if kind == "root" else 2000, "200000" if kind == "capability" else "0"),
            process(2, 2000, "0")]
    value = {"schema": SCHEMA, "started_at": AT, "finished_at": AT,
        "capture_subject": {"pid": 9999, "uid_effective": 0, "pid_namespace_inode": 20,
                            "mount_namespace_inode": 10, "identity_stable": True},
        "capture_process_excluded": True, "processes": rows, "pid_count_before": 2, "pid_count_after": 2,
        "pid_inventory_stable": True, "process_metadata_complete": True,
        "candidate_metadata": [{"path": path, "state": "absent", "metadata": None,
            "metadata_identity_stable": True, "path_chain_sha256": "a" * 64, "access_tested": False,
            "started_at": AT, "finished_at": AT, "diagnostic_codes": ["PATH_ABSENT"]} for path in selected],
        "worker_subject_verified": False, "worker_inventory_complete": False, "diagnostic_codes": []}
    if mode == "later-row-gap":
        rows[1].update(name=None, uids=None, uid_effective=None, cap_eff=None,
            environment_names=[], environment_names_complete=False,
            diagnostic_codes=["PROCESS_STATUS_UNAVAILABLE", "PROCESS_ENVIRONMENT_NAMES_UNAVAILABLE"])
        value["process_metadata_complete"] = False
    elif mode == "pid-change":
        rows[0].update(pid_identity_stable=False, diagnostic_codes=["PROCESS_IDENTITY_CHANGED"])
        value.update(pid_inventory_stable=False, process_metadata_complete=False,
                     diagnostic_codes=["PROCESS_SET_CHANGED"])
    elif mode == "capture-change":
        value["capture_subject"]["identity_stable"] = False
        value.update(pid_inventory_stable=False, process_metadata_complete=False,
                     diagnostic_codes=["CAPTURE_SUBJECT_CHANGED"])
    elif mode == "forged-worker": value["worker_subject_verified"] = True
    elif mode == "extra-raw": value["environment_values"] = "TOKEN=" + PRIVATE
    return value


class ProcessWorld(FullWorld):
    def __init__(self, target, *, kind="normal", mode=None, normal_mode=None):
        super().__init__(target)
        self.kind, self.mode, self.process_reads = kind, mode, 0
        self.normal_mode = normal_mode

    def runner(self, argv, **kwargs):
        if "exec" in argv and "input_bytes" in kwargs and "candidate_paths" in json.loads(kwargs["input_bytes"]):
            assert sha256_hex(argv[-2].encode()) == SOURCE_SHA256
            assert "--container=" + self.target["kubernetes"]["engine"]["container"] in argv
            self.process_reads += 1
            selected = json.loads(kwargs["input_bytes"])["candidate_paths"]
            value = packet(selected, self.kind, self.mode)
            result = {"returncode": 0, "stdout_bytes": canonical_json(value), "stderr_bytes": PRIVATE.encode(),
                      "capture": {"complete": True}}
            if self.mode == "replacement": self.replaced = True
            if self.mode == "cleanup":
                result["capture"].update(complete=False, pipes_closed=False, read_errors=["child_reap"])
            return result
        if "exec" in argv and "input_bytes" in kwargs:
            spec = json.loads(kwargs["input_bytes"])
            if spec.get("requests", [{}])[0].get("id") == "functional-control":
                requests = spec["requests"]
                assert len(requests) == 1
                assert requests[0]["url"] == self.target["engine"]["trusted_url"] + "/v1/completions"
                assert requests[0]["body"] == {"model": "fixture-model", "prompt": "ping", "max_tokens": 1}
                self.packets.append(deepcopy(requests))
                if self.normal_mode == "exception": raise OSError(PRIVATE)
                text = json.dumps({"model": "other-model" if self.normal_mode == "wrong-model" else "fixture-model",
                    "choices": [{"text": PRIVATE, "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
                row = {"id": "functional-control", "mode": "json", "method": "POST", "outcome": 200,
                    "body": text, "body_bytes": len(text.encode()), "body_sha256": sha256_hex(text.encode()),
                    "capture_complete": True, "eof": True, "utf8_valid": True, "body_truncated": False,
                    "stop_reason": "eof", "body_preview": PRIVATE, "headers": {"secret": PRIVATE}}
                if self.normal_mode == "denied": row["outcome"] = 403
                elif self.normal_mode in {"refused", "timeout"}: row["outcome"] = self.normal_mode
                elif self.normal_mode == "partial": row["eof"] = False
                elif self.normal_mode == "replacement": self.replaced = True
                result = {"returncode": 0, "stdout_bytes": canonical_json([row]), "stderr_bytes": PRIVATE.encode(),
                          "capture": {"complete": True}}
                if self.normal_mode == "cleanup":
                    result["capture"].update(complete=False, pipes_closed=False, read_errors=["child_reap"])
                return result
        result = super().runner(argv, **kwargs)
        if "get" in argv:
            value = json.loads(result["stdout_bytes"])
            if value.get("kind") == "Pod":
                value["metadata"]["annotations"] = {"private": PRIVATE}
                value["spec"]["containers"][0]["env"] = [{"name": "TOKEN", "value": PRIVATE}]
                result["stdout_bytes"] = canonical_json(value)
        return result


def context(tmp_path, *, kind="normal", mode=None, normal_mode=None):
    env, target = full_inputs()
    target["lab"] = False  # An injected runner still cannot claim MEASURED.
    world = ProcessWorld(target, kind=kind, mode=mode, normal_mode=normal_mode)
    transport = KubernetesHTTP(target, env, duration=30, profile=REQUEST_BINDING_PROFILE, runner=world.runner)
    ctx = Ctx(check=baseline_pack().checks["PLT-01"], env=env, adapter=VllmAdapter(), target=target,
              policy_values=PERMISSION_VALUES, timeout=1, allow_mutating=False, keys={}, secrets=[],
              cprobe=transport, workdir=tmp_path, started=T0)
    return ctx, world


@pytest.mark.parametrize("kind,expected", [("normal", Verdict.UNKNOWN), ("root", Verdict.FAIL),
                                            ("capability", Verdict.FAIL)])
def test_stable_container_facts_keep_known_violations_without_whole_worker_or_host_claim(tmp_path, kind, expected):
    ctx, world = context(tmp_path, kind=kind)
    obs, normal, challenges, summary = collect_kubernetes_process_permissions(ctx)
    assert world.process_reads == 1 and len(world.packets) == 1
    assert obs["permission_scan_complete"]["value"] is obs["process_scan_complete"]["value"] is False
    assert normal["attempted"] is True and normal["succeeded"] is True
    assert all(obs[name] == {"value": None, "collected": False} for name in
               ("mounts", "devices", "privileged", "host_namespaces", "sentinel_results", "exec_writable_mounts"))
    assert obs["root_uid_present"]["collected"] is (kind == "root")
    assert summary["worker_subject_verified"] is False and PRIVATE not in json.dumps(summary)
    assert [row["attempted"] for row in challenges] == [True, False, False]
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-01"], evidence(check_id="PLT-01", observations=obs, normal=normal),
                            policy(pack.ref(), values={"PLT-01": PERMISSION_VALUES}), T0)
    assert obs["functional_probe_results"]["value"] == {"POST /v1/completions": "pass"}
    assert result.verdicts.security == expected and result.verdicts.functional == Verdict.UNKNOWN
    assert any("functional: PASS withheld" in reason for reason in result.reasons)


def test_partial_later_process_field_failure_keeps_stable_prior_root_fact(tmp_path):
    ctx, _ = context(tmp_path, kind="root", mode="later-row-gap")
    obs, normal, _, summary = collect_kubernetes_process_permissions(ctx)
    assert obs["root_uid_present"] == {"value": True, "collected": True}
    assert obs["permission_scan_complete"]["value"] is False
    assert summary["process_observation"]["packet"]["process_metadata_complete"] is False
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-01"], evidence(check_id="PLT-01", observations=obs, normal=normal),
                            policy(pack.ref(), values={"PLT-01": PERMISSION_VALUES}), T0)
    assert result.verdicts.security == Verdict.FAIL


@pytest.mark.parametrize("kind", ["root", "capability"])
def test_existing_explicit_policy_still_decides_known_process_fact(kind, tmp_path):
    ctx, _ = context(tmp_path, kind=kind)
    values = {**PERMISSION_VALUES, "require_non_root": False, "allowed_capabilities": ["SYS_ADMIN"]}
    ctx.policy_values = values
    obs, normal, _, _ = collect_kubernetes_process_permissions(ctx)
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-01"], evidence(check_id="PLT-01", observations=obs, normal=normal),
                            policy(pack.ref(), values={"PLT-01": values}), T0)
    assert result.verdicts.security == Verdict.UNKNOWN  # Known allowed facts do not complete the whole scope.


@pytest.mark.parametrize("mode", ["pid-change", "capture-change", "replacement", "forged-worker", "extra-raw", "cleanup"])
def test_unstable_subject_or_unbound_packet_cannot_supply_a_root_violation(tmp_path, mode):
    ctx, _ = context(tmp_path, kind="root", mode=mode)
    obs, _, _, detail = collect_kubernetes_process_permissions(ctx)
    assert obs["root_uid_present"]["collected"] is False
    assert detail["worker_subject_verified"] is False and PRIVATE not in json.dumps(detail)


def test_supplied_dictionary_or_lookalike_transport_cannot_admit_process_facts(tmp_path):
    from types import SimpleNamespace
    ctx, world = context(tmp_path)
    ctx.cprobe = SimpleNamespace(profile=REQUEST_BINDING_PROFILE, target_sha256=sha256_hex(canonical_json(ctx.target)),
                                 process_snapshot=lambda paths: packet([]))
    with pytest.raises(ValueError, match="^KUBERNETES_PROCESS_COMMON_INTERFACE_UNAVAILABLE$"):
        read_kubernetes_process_observations(ctx)
    assert world.process_reads == 0


@pytest.mark.parametrize("paths", [None, [], ["/EXAMPLE-credentials/key"]])
def test_credential_names_and_lstat_absence_are_not_worker_environment_or_access(tmp_path, paths):
    env, target = full_inputs()
    world = ProcessWorld(target)
    path = tmp_path / "target.json"
    path.write_bytes(canonical_json(target))
    values = {**CREDENTIAL_VALUES, **({"credential_candidate_paths": paths} if paths is not None else {})}
    draft = KubernetesCredentialExposureExecutor({"target": str(path)}, runner=world.runner).run(
        baseline_pack().checks["PLT-04"], env, values, VllmAdapter(), tmp_path / "work")
    assert draft.data_origin.value == "EXAMPLE" and draft.status == ExecStatus.COMPLETED
    assert draft.observations["worker_env_names"].collected is False
    assert draft.observations["forbidden_mount_violations"].collected is False
    assert draft.observations["credential_candidate_results"].value == {p: "unknown" for p in paths or []}
    assert draft.observations["credential_candidates_complete"].value is (paths == [])
    assert world.process_reads == 1 and len(world.packets) == 1
    body = next(a.path for a in draft.artifacts if a.name == "probe_log.jsonl").read_text(encoding="utf-8")
    assert PRIVATE not in body and '"container_environment_names_observed": ["FORBIDDEN_CANARY", "PATH"]' in body


@pytest.mark.parametrize("invalid", ["check", "policy"])
def test_unsupported_profile_or_invalid_credential_policy_fails_before_process_read(tmp_path, invalid):
    env, target = full_inputs()
    world = ProcessWorld(target)
    path = tmp_path / "target.json"
    path.write_bytes(canonical_json(target))
    check = baseline_pack().checks["SUP-03" if invalid == "check" else "PLT-04"]
    values = {} if invalid == "policy" else CREDENTIAL_VALUES
    with pytest.raises(ValueError):
        KubernetesCredentialExposureExecutor({"target": str(path)}, runner=world.runner).run(
            check, env, values, VllmAdapter(), tmp_path / "work")
    assert world.process_reads == 0 and not world.calls


@pytest.mark.parametrize("mode,attachment_failure", [(None, False), ("replacement", False), (None, True)])
def test_proposed_registry_cli_keeps_metadata_scope_unknown_and_original_refs(tmp_path, monkeypatch, mode, attachment_failure):
    env, target = full_inputs()
    target["lab"] = False
    world = ProcessWorld(target, mode=mode)
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={"include": ["PLT-04"]},
                 values={"PLT-04": {**CREDENTIAL_VALUES, "credential_candidate_paths": ["/EXAMPLE-credentials/key"]}})
    for name, value in (("environment", env.to_dict()), ("policy", pol.to_dict()), ("target", target)):
        (tmp_path / (name + ".json")).write_bytes(canonical_json(value))
    cfg = tmp_path / "guard.toml"
    cfg.write_text('[executors]\ncredential_exposure="kubernetes"\n[adapter]\nname="vllm"\n'
                   '[credential_exposure]\ntarget=' + json.dumps(str(tmp_path / "target.json")) + '\n', encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS, "credential_exposure:kubernetes", lambda raw:
        KubernetesCredentialExposureExecutor(raw["credential_exposure"], runner=world.runner,
            expected_target_sha256=raw.get("_kubernetes_target_sha256")))
    def forbidden(*args, **kwargs):
        raise AssertionError("injected common boundary only; no actual subprocess or network")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    if attachment_failure:
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name == "probe_log.jsonl" and args and args[0] == "w": raise OSError(PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_file)
    common = ["--config", str(cfg), "--store", str(tmp_path / "store")]
    runner = CliRunner()
    def cli(args):
        result = runner.invoke(app, list(map(str, args)))
        assert result.exit_code == 0, (args, result.output)
    cli(["select", "--pack", pack.path, "--env", tmp_path / "environment.json", "--policy", tmp_path / "policy.json", *common])
    store = Store.read_only(tmp_path / "store")
    plan, = store.iter_kind("PLAN")
    cli(["run", "--plan", plan.id, "--pack", pack.path, *common])
    cli(["judge", "--plan", plan.id, "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan.id, "--as-of", T0.isoformat(), "--json", tmp_path / "report.json", *common])
    cli(["verify", *common])
    store = Store.read_only(tmp_path / "store")
    evd, = store.iter_kind("EVD")
    res, = store.iter_kind("RES")
    assert world.process_reads == 1 and len(world.packets) == (1 if mode is None else 0)
    assert evd.data_origin.value == "EXAMPLE"
    assert evd.execution.status == (ExecStatus.ABORTED if attachment_failure else ExecStatus.COMPLETED)
    assert res.verdicts.security == Verdict.UNKNOWN
    assert evd.normal_control.succeeded is (mode is None)
    assert evd.observations["functional_probe_results"].value == {
        "POST /v1/completions": "pass" if mode is None else "no_response"}
    assert res.verdicts.functional == Verdict.UNKNOWN
    if mode is None:
        assert any("functional: PASS withheld" in reason for reason in res.reasons)
    assert res.verdicts.performance == Verdict.UNKNOWN
    if not attachment_failure:
        artifact, = [a for a in evd.artifacts if a.name == "probe_log.jsonl"]
        summary = json.loads((tmp_path / "store" / artifact.path).read_text(encoding="utf-8").splitlines()[-1])
        assert summary["process_observation"]["available"] is (mode is None)
        assert summary["process_observation"]["worker_subject_verified"] is False
    objects = [obj for kind in ("ENV", "POL", "PLAN", "EVD", "RES", "RPT") for obj in store.iter_kind(kind)]
    assert len(objects) == 6 and PRIVATE not in canonical_json(evd.to_dict()).decode()
    cli(["evidence", "export", "--store", tmp_path / "store", "--output", tmp_path / "bundle.zip"])
    cli(["evidence", "restore", "--bundle", tmp_path / "bundle.zip", "--destination", tmp_path / "restored"])
    restored = Store.read_only(tmp_path / "restored")
    assert all(restored.resolve(obj.ref()).to_dict() == obj.to_dict() for obj in objects)
    cli(["judge", "--plan", plan.id, "--as-of", T0.isoformat(), "--store", tmp_path / "restored", "--config", cfg])
    assert [obj.ref() for obj in restored.iter_kind("RES")] == [res.ref()]
    cli(["verify", "--store", tmp_path / "restored", "--config", cfg])


def test_example_environment_origin_survives_default_runner_slot_and_real_target_flag(tmp_path, monkeypatch):
    from guard.runtime import kubernetes_http
    env, target = full_inputs()
    assert env.data_origin.value == "EXAMPLE"
    target["lab"] = False
    world = ProcessWorld(target)
    monkeypatch.setattr(kubernetes_http, "capture_pipes", world.runner)
    path = tmp_path / "target.json"
    path.write_bytes(canonical_json(target))
    draft = KubernetesCredentialExposureExecutor({"target": str(path)}).run(
        baseline_pack().checks["PLT-04"], env, CREDENTIAL_VALUES, VllmAdapter(), tmp_path / "work")
    assert draft.data_origin.value == "EXAMPLE"
    assert draft.normal_control.succeeded is True
    assert world.process_reads == 1 and len(world.packets) == 1


@pytest.mark.parametrize("normal_mode", ["denied", "refused", "timeout", "partial", "wrong-model",
                                        "replacement", "cleanup", "exception"])
def test_later_control_gap_keeps_earlier_root_violation_and_safe_report(tmp_path, normal_mode):
    ctx, world = context(tmp_path, kind="root", normal_mode=normal_mode)
    obs, normal, _, summary = collect_kubernetes_process_permissions(ctx)
    assert obs["root_uid_present"] == {"value": True, "collected": True}
    assert normal["attempted"] is True and normal["succeeded"] is False
    assert len(world.packets) == world.process_reads == 1
    assert summary["process_observation"]["available"] is True
    assert PRIVATE not in json.dumps([summary, ctx.log])
    pack = baseline_pack()
    result = judge_evidence(pack.checks["PLT-01"], evidence(check_id="PLT-01", observations=obs, normal=normal),
                            policy(pack.ref(), values={"PLT-01": PERMISSION_VALUES}), T0)
    assert result.verdicts.security == Verdict.FAIL
    assert result.verdicts.functional == (Verdict.FAIL if normal_mode == "denied" else Verdict.UNKNOWN)
    if normal_mode == "cleanup":
        assert ctx.cprobe.report()["cleanup_errors"] == ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]
