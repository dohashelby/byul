"""OBS-01 Gateway 경로: Gateway 기록 변환·정책·전송 검증·파생 팩."""
import json

import pytest

from guard.executors import kubernetes_gateway_observation as module
from guard.executors import observation_contract as base
from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import GATEWAY_EVENT_PROFILE, KubernetesBindingError, KubernetesHTTP


def record(request_id, result='ALLOWED', principal='dev1-general', status=200, phase='final', run='run-1'):
    return json.dumps({'schema_version': 'runtime-mvp-record/1.0', 'phase': phase, 'request_id': request_id, 'run_id': run,
                       'target_id': 'lab-target', 'policy': {'sha256': 'a' * 64},
                       'auth': {'state': 'authenticated' if principal else 'invalid', 'principal_id': principal},
                       'decision': {'result': result, 'http_status': status}})


def test_final_gateway_rows_become_closed_required_event_rows_without_invented_values():
    lines = [record('r1', phase='started'), record('r1'), record('r2', 'DENIED', 'dev1-observer', 403), record('r3', 'ERROR'),
             record('r4', run='other'), record('r5', 'DENIED', None, 401)]
    events = module.record_events(lines, run_id='run-1')
    assert [e['event'] for e in events] == ['ACCESS_ALLOWED', 'ACCESS_DENIED', 'ACCESS_DENIED']
    first, denied, anonymous = events
    assert first['actor_sha256'] == module.principal_sha256('dev1-general') and first['request_sha256'] == sha256_hex(b'r1')
    assert first['result_sha256'] == sha256_hex(canonical_json({'outcome': 200})) and first['approval_sha256'] is None and first['source_sha256'] is None
    assert denied['actor_sha256'] == module.principal_sha256('dev1-observer') and anonymous['actor_sha256'] is None
    packet = {'schema': base.SCHEMA, 'session': 'b' * 32, 'environment_ref': {'x': 1}, 'target_sha256': 'c' * 64,
              'producer_source_sha256': 'd' * 64, 'producer_authenticated': False, 'source_ended': False, 'dropped_events': 0, 'events': events}
    base.validate_record(packet, environment_ref={'x': 1}, target_sha256='c' * 64, session='b' * 32, expected_source_sha256='d' * 64)
    with pytest.raises(ValueError, match='GATEWAY_RECORD_SCHEMA_UNSUPPORTED'):
        module.record_events(['{"schema_version": "other"}'], run_id='run-1')


def test_policy_needs_the_gateway_target_id_on_top_of_the_original_keys():
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    values = {'observation_environment_sha256': 'a' * 64, 'observation_target_sha256': 'b' * 64,
              'observation_validity_start': now.isoformat(), 'observation_validity_end': (now + timedelta(minutes=5)).isoformat(),
              'observation_max_delay_ms': 5000, 'observation_required_events': ['ACCESS_ALLOWED', 'ACCESS_DENIED'],
              'observation_required_fields': ['actor_sha256', 'target_sha256', 'run_sha256', 'policy_sha256'],
              'observation_session': 'c' * 32, 'observation_expected_source_sha256': 'd' * 64,
              'observation_event_policy_sha256': 'e' * 64, 'observation_allowed_actor_sha256': 'f' * 64,
              'observation_denied_actor_sha256': '1' * 64, 'observation_gateway_target_id': 'byul-lab-run-1'}
    module.normalize_policy(values)
    for change in ({'observation_gateway_target_id': 'bad id'}, {'observation_gateway_target_id': None}):
        with pytest.raises(ValueError, match='GATEWAY_OBSERVATION_POLICY_INVALID'):
            module.normalize_policy({**values, **change})
    with pytest.raises(ValueError, match='GATEWAY_OBSERVATION_POLICY_INVALID'):
        module.normalize_policy({k: v for k, v in values.items() if k != 'observation_gateway_target_id'})


def test_transport_accepts_only_the_three_gateway_trials_with_one_credential_header():
    stub = type('S', (), {'_gateway_netloc': '10.0.0.9:8080'})()
    good = {'id': 'obs01-denied', 'method': 'POST', 'url': 'http://10.0.0.9:8080/v1/chat/completions',
            'headers': {'Authorization': 'Bearer token-0123456789'},
            'body': {'model': 'm', 'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 1, 'stream': False}}
    KubernetesHTTP._gateway_event_request(stub, good)
    for change in ({'id': 'other'}, {'headers': {}}, {'headers': {'Authorization': 'Bearer t-0123456789', 'X-User': 'a'}},
                   {'body': {**good['body'], 'temperature': 0}}, {'body': {**good['body'], 'messages': [{'role': 'user', 'content': 'free'}]}}):
        with pytest.raises(KubernetesBindingError):
            KubernetesHTTP._gateway_event_request(stub, {**good, **change})
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP._gateway_event_request(type('S', (), {'_gateway_netloc': None})(), good)
    assert GATEWAY_EVENT_PROFILE == 'kubernetes-owned-gateway-event-http-v1'


def test_derived_pack_changes_only_obs01_and_keeps_the_seed_pack_untouched(tmp_path):
    from pathlib import Path
    from guard.models import load_pack
    from tools import build_dev1_gateway_obs01_pack as tool
    result = tool.derive(tmp_path / 'pack')
    derived, original = load_pack(tmp_path / 'pack'), load_pack(Path(tool.__file__).parents[1] / 'reference/pack-seeds/PACK-BASELINE-33')
    assert original.ref().model_dump(mode='json') == tool.SEED_REF and result['pack_ref']['id'] == 'PACK-DEV1-GATEWAY-OBS01'
    assert result['pack_ref']['revision'] == tool.DERIVED_REVISION == 2
    assert derived.checks['OBS-01'].collection_profile == module.PROFILE
    assert {cid for cid in original.checks if original.checks[cid] != derived.checks[cid]} == {'OBS-01'}
    with pytest.raises(ValueError, match='NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED'):
        tool.derive(tmp_path / 'pack')


@pytest.fixture
def ordinary_gateway_record(tmp_path, request):
    from types import SimpleNamespace
    from serving_runtime.gateway.request_records import RequestRecords
    from serving_runtime.gateway.validator import Decision

    source = {"environment": "fixture-env", "target_id": "fixture-target", "runtime_policy_sha256": "a" * 64,
              "request_sha256": sha256_hex(b"c" * 32)}
    policy = SimpleNamespace(target_id=source["target_id"], schema_version="runtime-mvp/1.2", profile=None,
                             policy_id="fixture-policy", policy_version="1", cache_isolation=None)
    state = SimpleNamespace(policy=policy, sha256=source["runtime_policy_sha256"], path="EXAMPLE-PRIVATE-POLICY-PATH")
    target = {"target_id": source["target_id"], "registration": "ok"}
    writer = RequestRecords(tmp_path, "b" * 32, lambda: state, "vllm", environment=source["environment"],
                            run_id="guard-fixture", target_context=lambda: target)
    writer.clear_context("POST /v1/completions")
    writer.bind_context(state)
    writer.set_auth({"state": "authenticated", "principal_id": "fixture-principal"})
    writer.set_context({"private": "EXAMPLE-PRIVATE-REQUEST-DATA"})
    model = ("EXAMPLE-PRIVATE-MODEL\u0085NEXT\u2028LINE\u2029PARAGRAPH"
             if getattr(request, "param", False) else "fixture-model")
    normal = Decision("ALLOWED", 200, "ALLOWED", None, "allowed", model, 1)
    started = writer.build("started", "c" * 32, normal, attempted=False, backend_receipt="not_available",
                           response="none", error_code=None)
    assert writer.append(started)
    row = writer.build("final", "c" * 32, normal, attempted=True, backend_receipt="not_available",
                       response="completed", error_code=None, final_outcome="completed")
    assert writer.append(row)
    raw = (tmp_path / "gateway_records.jsonl").read_bytes()
    return raw, row, source


@pytest.mark.parametrize("fault", [None, "session", "request", "duplicate", "duplicate-key", "unterminated", "cr-only",
                                    "target", "actor", "policy"])
def test_snapshot_keeps_only_the_prespecified_original_final(ordinary_gateway_record, fault):
    raw, row, source = ordinary_gateway_record
    values = {"observation_session": "b" * 32, "observation_allowed_actor_sha256": module.principal_sha256("fixture-principal")}
    if fault == "session":
        values["observation_session"] = "0" * 32
    if fault == "request":
        source["request_sha256"] = "0" * 64
    if fault == "duplicate":
        raw += raw
    if fault == "duplicate-key":
        raw = raw.replace(b'"phase": "final"', b'"phase": "final", "phase": "final"')
    if fault == "unterminated":
        raw = raw.removesuffix(b"\n")
    if fault == "cr-only":
        raw = raw.replace(b"\n", b"\r", 1)
    if fault == "target":
        source["target_id"] = "other-target"
    if fault == "actor":
        values["observation_allowed_actor_sha256"] = "0" * 64
    if fault == "policy":
        source["runtime_policy_sha256"] = "0" * 64
    if fault:
        expected = {"duplicate-key": r"^RUNTIME_RECORD_LINE_INVALID:2$",
                    "cr-only": r"^RUNTIME_RECORD_LINE_INVALID:1$"}.get(fault, r"^GATEWAY_AUDIT_")
        with pytest.raises(ValueError, match=expected):
            module.snapshot_record(raw, source=source, values=values, run_id="guard-fixture")
        return
    result, digest, recorded_at = module.snapshot_record(raw, source=source, values=values, run_id="guard-fixture")
    assert result["event"] == "ACCESS_ALLOWED" and digest == sha256_hex(canonical_json(row))
    assert result["request_sha256"] == source["request_sha256"]
    assert result["policy_sha256"] == source["runtime_policy_sha256"]
    assert result["source_sha256"] is None and result["approval_sha256"] is None
    assert base._time(recorded_at) == base._time(row["recorded_at"])
    assert "EXAMPLE-PRIVATE" not in json.dumps(result)


def test_archived_gateway_final_without_selected_run_cannot_be_adopted():
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[2] / "runtime/tests/fixtures/aws-request-detail-20261106.json"
    originals = json.loads(fixture.read_bytes())["gateway_records"]
    final, = (row for row in originals if row["phase"] == "final")
    assert final["decision"]["result"] == "ALLOWED" and final["run_id"] is None
    raw = b"".join(canonical_json(row) + b"\n" for row in originals)
    source = {"environment": final["environment"], "target_id": final["target_id"],
              "runtime_policy_sha256": final["policy"]["sha256"],
              "request_sha256": sha256_hex(final["request_id"].encode())}
    values = {"observation_session": final["observation_session_id"],
              "observation_allowed_actor_sha256": module.principal_sha256(final["auth"]["principal_id"])}
    with pytest.raises(ValueError, match="^GATEWAY_AUDIT_FINAL_NOT_UNIQUE$"):
        module.snapshot_record(raw, source=source, values=values, run_id="current-owned-run")


@pytest.mark.parametrize("ordinary_gateway_record,line_ending", [(False, b"\n"), (True, b"\n"), (True, b"\r\n")],
                         indirect=["ordinary_gateway_record"], ids=["ascii", "unicode", "unicode-crlf"])
@pytest.mark.parametrize("include_denied_actor", [True, False], ids=["both-actors", "allowed-only"])
def test_canonical_snapshot_reads_one_owned_file_without_http(
        tmp_path, monkeypatch, ordinary_gateway_record, line_ending, include_denied_actor):
    from datetime import timedelta
    from guard.core.run import _normalize
    from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
    from guard.models import DataOrigin, ExecStatus, utcnow
    from tests.test_kubernetes_request_binding import env, target
    from tests.test_supply_boot_record_access import PACK

    raw, row, source = ordinary_gateway_record
    assert raw.count(b"\n") == 2
    if row["request"]["model"] != "fixture-model":
        assert all(separator.encode() in raw for separator in ("\u0085", "\u2028", "\u2029"))
    raw = raw.replace(b"\n", line_ending)
    environment = env()
    selected = target(environment)
    selected["lab"] = False
    kubernetes = selected["kubernetes"]
    engine = kubernetes["engine"]
    kubernetes["probe"].update(namespace=engine["namespace"], namespace_uid=engine["namespace_uid"])
    selected["kubernetes_gateway"] = {**engine, "container": "gateway", "pod": "gateway-pod", "pod_ip": "127.0.0.3",
                                      "container_id": "containerd://" + "3" * 64,
                                      "pod_uid": "10000000-0000-0000-0000-000000000003"}
    target_file = tmp_path / "selected.json"
    target_file.write_bytes(canonical_json(selected))
    digest = sha256_hex(target_file.read_bytes())
    now = utcnow()
    values = {"observation_environment_sha256": environment.ref().sha256, "observation_target_sha256": digest,
              "observation_validity_start": (now - timedelta(seconds=1)).isoformat(),
              "observation_validity_end": (now + timedelta(minutes=2)).isoformat(),
              "observation_max_delay_ms": 5000, "observation_required_events": ["ACCESS_ALLOWED", "ACCESS_DENIED"],
              "observation_required_fields": ["actor_sha256", "target_sha256", "run_sha256", "policy_sha256", "source_sha256"],
              "observation_session": "b" * 32, "observation_expected_source_sha256": "d" * 64,
              "observation_event_policy_sha256": "e" * 64,
              "observation_allowed_actor_sha256": module.principal_sha256("fixture-principal")}
    if include_denied_actor:
        values["observation_denied_actor_sha256"] = "f" * 64
    calls = []
    monkeypatch.setattr(KubernetesHTTP, "_verify", lambda self: calls.append("verify-engine-and-probe"))

    def read_file(self, client, target, *, container, image, path):
        calls.append(path)
        assert self.binding["engine"] == selected["kubernetes_gateway"] and target == selected
        return raw, {"capture_sha256": sha256_hex(raw), "bytes": len(raw), "owner_binding_verified": True}

    def unexpected(*args, **kwargs):
        raise AssertionError("audit snapshot cannot run requests or activate the target")

    monkeypatch.setattr(module.GatewayRecordReader, "read_file", read_file)
    monkeypatch.setattr(KubernetesHTTP, "__call__", unexpected)
    executor = module.KubernetesGatewayRecordSnapshotExecutor(
        {"target": str(target_file), module.SNAPSHOT_OPTION: True, module.SNAPSHOT_SOURCE: source},
        runner=unexpected, expected_target_sha256=digest)
    check = PACK.checks["OBS-01"]
    if not include_denied_actor:
        with pytest.raises(ValueError, match="^OBSERVATION_POLICY_INVALID$"):
            KubernetesRequiredEventExecutor({"target": str(target_file)}).validate_policy(check, values)
        with pytest.raises(ValueError, match="^GATEWAY_OBSERVATION_POLICY_INVALID$"):
            module.normalize_policy({**values, "observation_gateway_target_id": source["target_id"]})
        with pytest.raises(ValueError, match="^OBSERVATION_POLICY_INVALID$"):
            executor.validate_policy(check, {**values, "observation_denied_actor_sha256": values["observation_allowed_actor_sha256"]})
    draft = executor.run(check, environment, values, object(), tmp_path / "evidence")
    assert calls == ["verify-engine-and-probe", module.RECORD_PATH, "verify-engine-and-probe"]
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    observed = _normalize(check, draft)["observations"]
    capture, = observed["required_event_collection"]["value"]
    assert capture["file_bytes_sha256"] == sha256_hex(raw) and capture["record_sha256"] == sha256_hex(canonical_json(row))
    assert capture["file_bytes"] == len(raw)
    assert base._time(capture["source_reported_recorded_at"]) == base._time(row["recorded_at"])
    assert capture["target_sha256"] == digest and capture["environment_sha256"] == environment.ref().sha256
    assert capture["declared_producer_sha256"] == values["observation_expected_source_sha256"]
    assert not capture["event_policy_matches"] and not capture["event_arrival_time_verified"]
    record, = observed["required_event_records"]["value"]
    assert record["actor_sha256"] == values["observation_allowed_actor_sha256"]
    assert {item["event"]: item["reason"] for item in observed["required_event_missing"]["value"]} == {
        "ACCESS_ALLOWED": "independent_trigger_and_collection_time_unverified",
        "ACCESS_DENIED": "no_selected_gateway_final_record",
    }
    assert not observed["required_event_producer_authenticated"]["value"]
    assert not observed["required_event_independent_trigger_verified"]["value"]
    assert not observed["required_event_scope_complete"]["value"]
    assert not draft.normal_control.attempted and all(not step.attempted for step in draft.challenges)
    assert "EXAMPLE-PRIVATE" not in draft.artifacts[0].path.read_text()
