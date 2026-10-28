"""EXAMPLE packet ambiguity must not establish credential absence/access."""
import json
from types import SimpleNamespace

import pytest

from guard.executors.credential_candidates import decode_metadata
from tests.test_credential_mount_semantics import FakeDocker, context

PATH = "/EXAMPLE-credentials/key"
CANARY = "EXAMPLE_PRIVATE_PACKET_CANARY"


def packet(state="absent"):
    basis = "PATH_ABSENT" if state == "absent" else "EFFECTIVE_READ_PERMISSION"
    return json.dumps({"schema": "guard-credential-metadata/1", "worker_subject_matches": True,
        "worker_inventory_stable": True, "workers_examined": 2, "errors": [],
        "paths": {PATH: {"state": state, "basis": basis, "stable": True}}}, separators=(",", ":")).encode()


def malformed(kind):
    raw = packet().decode()
    replacements = {
        "subject": ('"worker_subject_matches":true', '"worker_subject_matches":false,"worker_subject_matches":true'),
        "inventory": ('"worker_inventory_stable":true', '"worker_inventory_stable":false,"worker_inventory_stable":true'),
        "workers": ('"workers_examined":2', '"workers_examined":0,"workers_examined":2'),
        "errors": ('"errors":[]', '"errors":["EXAMPLE_SUBJECT_GAP"],"errors":[]'),
        "state": ('"state":"absent"', '"state":"candidate_readable","state":"absent"'),
        "basis": ('"basis":"PATH_ABSENT"', '"basis":"UNVERIFIED","basis":"PATH_ABSENT"'),
        "stable": ('"stable":true', '"stable":false,"stable":true'),
        "path": ('"paths":{', '"paths":{' + json.dumps(PATH) + ':null,'),
    }
    if kind in replacements:
        old, new = replacements[kind]
        assert old in raw
        return raw.replace(old, new, 1).encode()
    if kind in {"NaN", "Infinity", "-Infinity"}:
        return ('{"untrusted_detail":' + json.dumps(CANARY) + ',"untrusted_number":' + kind + ',' + raw[1:]).encode()
    if kind == "depth":
        return ('{"untrusted_detail":' + '[' * 1500 + '0' + ']' * 1500 + ',' + raw[1:]).encode()
    if kind == "utf8":
        return b'{"untrusted_detail":"\xff",' + raw.encode()[1:]
    raise AssertionError(kind)


def transport(raw):
    return SimpleNamespace(returncode=0, stdout_bytes=raw,
        capture={"bounded_at_source": True, "complete": True, "truncated": False, "timed_out": False})


def reject_nested_payload(monkeypatch):
    # A fixed nesting count does not exhaust every pytest/Python recursion
    # configuration. Exercise the existing parser's actual failure boundary
    # without introducing a new production nesting limit.
    from guard.executors import owned_capture
    original = json.loads
    def rejected(value, *args, **kwargs):
        if type(value) is str and value.startswith('{"untrusted_detail":['):
            raise RecursionError(CANARY)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(owned_capture.json, "loads", rejected)


@pytest.mark.parametrize("kind", ["subject", "inventory", "workers", "errors", "path", "state", "basis", "stable",
                                  "NaN", "Infinity", "-Infinity", "depth", "utf8"])
def test_ambiguous_or_invalid_packet_is_unknown_with_fixed_diagnostic(kind, monkeypatch):
    if kind == "depth":
        reject_nested_payload(monkeypatch)
    observed, detail = decode_metadata(transport(malformed(kind)), [PATH])
    assert observed == {PATH: "unknown"}
    assert detail == {"profile": "credential-candidate-metadata-v1", "code": "CANDIDATE_METADATA_UNAVAILABLE"}
    assert CANARY not in json.dumps(detail)


@pytest.mark.parametrize("state", ["absent", "denied", "candidate_readable"])
def test_valid_fixed_producer_packet_keeps_existing_state_meaning(state):
    observed, detail = decode_metadata(transport(packet(state)), [PATH])
    assert observed == {PATH: state}
    assert detail["code"] == "WORKER_SUBJECT_METADATA_VERIFIED"
    assert detail["scope"].startswith("effective read permission only")


class PacketDocker(FakeDocker):
    def __init__(self, raw, **kwargs):
        super().__init__(**kwargs)
        self.raw = raw

    def exec_bounded(self, *args, **kwargs):
        original = super().exec_bounded(*args, **kwargs)
        return SimpleNamespace(returncode=original.returncode, capture=original.capture, stdout_bytes=self.raw)


@pytest.mark.parametrize("kind", ["subject", "path", "state", "depth"])
@pytest.mark.parametrize("violation", ["none", "mount", "environment"])
def test_invalid_candidate_packet_preserves_independent_sources(kind, violation, monkeypatch):
    from guard.executors.credential_exposure import CredentialExposureExecutor

    if kind == "depth":
        reject_nested_payload(monkeypatch)

    values = {"forbidden_env_names": ["EXAMPLE_FORBIDDEN"], "forbidden_mount_paths": ["/credentials"],
              "credential_candidate_paths": [PATH]}
    docker = PacketDocker(malformed(kind),
        mounts=[{"Type": "bind", "Destination": "/credentials"}] if violation == "mount" else [],
        process_names=["EXAMPLE_FORBIDDEN"] if violation == "environment" else [])
    observations, normal, _, summary = CredentialExposureExecutor({}, docker=docker).collect(context(values))
    assert observations["credential_candidate_results"]["value"] == {PATH: "unknown"}
    assert observations["credential_candidates_complete"]["value"] is False
    assert observations["forbidden_mount_violations"]["value"] == (["/credentials"] if violation == "mount" else [])
    assert ("EXAMPLE_FORBIDDEN" in observations["worker_env_names"]["value"]) is (violation == "environment")
    assert normal["succeeded"] is True
    assert CANARY not in json.dumps([observations, summary])


@pytest.mark.parametrize("mode,expected", [("normal", "PASS"), ("missing", "UNKNOWN"),
    ("violation", "FAIL"), ("later_attachment_failure", "FAIL")])
def test_packet_product_storage_report_restore_and_exact_rejudge(tmp_path, monkeypatch, mode, expected):
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.executors import registry
    from guard.executors.credential_exposure import CredentialExposureExecutor
    from guard.models import canonical_json
    from guard.store import Store
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack, put_pack
    from tests.builders import baseline_pack, environment, policy, completion_response

    violated = mode in {"violation", "later_attachment_failure"}
    docker = PacketDocker(packet() if mode == "normal" else malformed("subject"),
        mounts=[{"Type": "bind", "Destination": "/credentials"}] if violated else [])
    from tests.test_credential_mount_semantics import IMAGE, RUN
    env = environment(engine={"name": "engine-x", "version": "1.0.0", "image_digest": IMAGE}, model={"id": "test"})
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab": True, "probe_image": "EXAMPLE-unused",
        "run_id": RUN, "environment_ref": env.ref().model_dump(mode="json"),
        "engine": {"container": "engine", "trusted_url": "http://engine:8000", "ready_timeout": 1},
        "origins": {"trusted": {"network": "trusted"}}, "gateway": {"url": "http://gateway:8080"}}))
    cfg = {"target": str(target)}
    def probe(network, requests, timeout):
        return [completion_response(request) for request in requests]
    monkeypatch.setitem(registry.EXECUTORS, "credential_exposure:real",
        lambda raw: CredentialExposureExecutor(cfg, container_probe=probe, docker=docker))
    store, pack = Store(tmp_path / "store"), baseline_pack()
    values = {"forbidden_env_names": [], "forbidden_mount_paths": ["/credentials"], "credential_candidate_paths": [PATH]}
    pol = policy(pack.ref(), scope={"include": ["PLT-04"], "exclude": []}, values={"PLT-04": values})
    store.put_many([env, pol]); plan = select(pack, [env], pol); store.put(plan)
    put_pack(store, pack.path)
    with monkeypatch.context() as later:
        if mode == "later_attachment_failure":
            def unavailable(*args, **kwargs):
                raise OSError(CANARY)
            later.setattr(Store, "put_artifact", unavailable)
        evd, = run_plan(store, pack, plan, RunConfig(executors={"credential_exposure": "real"}, adapter="vllm"))
    result, = judge_plan(store, pack, plan, as_of=env.created_at)
    assert evd.data_origin.value == "EXAMPLE" and result.verdicts.security.value == expected
    assert evd.execution.executor.version == "0.5.0"
    assert evd.execution.status.value == ("ABORTED" if mode == "later_attachment_failure" else "COMPLETED")
    assert evd.observations["credential_candidates_complete"].value is (mode == "normal")
    if mode == "later_attachment_failure":
        assert not evd.artifacts and "ARTIFACT_PERSISTENCE_INCOMPLETE" in evd.execution.abort_reason
    report = build_report(store, pack, plan, as_of=env.created_at); store.put(report)
    row, = [row for row in report.by_check if row.check_id == "PLT-04"]
    assert row.single.security.value == expected and report.official is False
    original = canonical_json(evd.to_dict())
    assert CANARY not in original.decode()
    export_bundle(store.root, tmp_path / "packet.zip")
    restore_bundle(tmp_path / "packet.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.verify() == [] and canonical_json(restored.resolve(evd.ref()).to_dict()) == original
    assert restored.resolve(report.ref()).to_dict() == report.to_dict()
    replayed, = judge_plan(restored, get_pack(restored, pack.ref()), restored.resolve(plan.ref()), as_of=env.created_at)
    assert replayed.ref() == result.ref() and replayed.verdicts.security.value == expected
