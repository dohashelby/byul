"""The existing bounded normal transport produces the offline REQ-13 input."""

import base64
from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

from guard.executors import kubernetes_response_snapshot as snapshot
from guard.executors.kubernetes_gateway_observation import GatewayRecordReader
from guard.executors.kubernetes_response_content import KubernetesResponseContentExecutor
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_binding import verify_owned_pod
from tests.builders import policy
from tests.test_kubernetes_response_snapshot import PRIVATE, VALUES, snapshot_inputs
from tests.test_mvp_gateway_preparation import MODEL, NOW, _gateway_owner, gateway_pair, sources  # noqa: F401
from tools import mvp_gateway_preparation as gateway
from tools import mvp_req13_gateway_inputs as assembler


def _forbidden(*args, **kwargs):
    pytest.fail("Offline preparation cannot contact the Gateway or start a child")


def _put(directory, name, value):
    raw = value if isinstance(value, bytes) else canonical_json(value)
    path = directory / name
    path.write_bytes(raw)
    return {"path": str(path), "sha256": sha256_hex(raw)}


def _sources(tmp_path, gateway_pair, pack, monkeypatch, failure, *, side="baseline", response_only=False,
             loopback=False, capture_packet=False):
    assert not (loopback or capture_packet) or response_only
    pol = policy(pack.ref(), scope={"include": ["REQ-13"]}, values={"REQ-13": VALUES})
    prior, target_path, _, prepared, owner = _gateway_owner(gateway_pair, side)
    if response_only:
        authority = {} if failure == "draft-policy" else {"status": "CONFIRMED", "confirmed_by": "EXAMPLE approver",
                                                             "confirmed_at": NOW - timedelta(minutes=2)}
        ordinary_values = {**VALUES, "allowed_response_fields": [*VALUES["allowed_response_fields"], "object"]}
        pol = policy(pack.ref(), scope={"include": ["REQ-13"]}, values={"REQ-13": ordinary_values},
                     valid_from=NOW - timedelta(minutes=1),
                     valid_until=NOW if failure == "policy-window" else NOW + timedelta(minutes=1), **authority)
        target = gateway.bind_gateway(target_path, prior, prepared, **owner)
        request_id = "fixture-req13-" + side
        request_sha = sha256_hex(request_id.encode())
        body = canonical_json({"id": request_id, "model": MODEL, "object": "chat.completion",
            "choices": [{"message": {"role": "assistant", "content": PRIVATE}, "finish_reason": "length"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}})
    else:
        prior, old_selection, _ = snapshot_inputs(tmp_path, gateway_pair, side, pol.ref().sha256)
        target = json.loads(Path(old_selection["target"]).read_bytes())
        source = old_selection[snapshot.INPUT]
        old_manifest = json.loads(Path(source["path"]).read_bytes())
        request_id = json.loads(Path(old_manifest["response_receipt"]["path"]).read_bytes())["request_id"]
        body = Path(old_manifest["body"]["path"]).read_bytes()
        journal = Path(old_manifest["journal"]["path"]).read_bytes()
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    env = prior.model_copy(update={"data_origin": DataOrigin.EXAMPLE}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["model_mapping"]["environment_ref"] = target["environment_ref"]
    engine_target = {key: value for key, value in target.items() if key != "kubernetes_gateway"}
    engine_file = _put(sources_dir, "engine-target.json", engine_target)
    gateway_binding = target["kubernetes_gateway"]
    prepared.update(environment_ref=target["environment_ref"], engine_target=engine_file)
    prepared_document = {"image": gateway.GATEWAY_IMAGE, "archive_sha256": gateway.ARCHIVE_SHA256,
                         "producer_sha256": gateway.PRODUCER_SHA256, "gateway_environment": gateway.GATEWAY_ENV,
                         "sides": {side: prepared}}
    if failure == "unmodeled-response-field":
        body = canonical_json({**json.loads(body), "fixture_extra_field": "example"})
    source_row = {"id": "obs01-before", "mode": "json", "method": "POST", "outcome": 200,
                  "body": body.decode("utf-8"), "body_bytes": len(body), "body_sha256": sha256_hex(body),
                  "body_truncated": False, "utf8_valid": True, "capture_complete": failure != "incomplete",
                  "eof": failure != "incomplete", "content_type": "application/json",
                  "response_headers": {"x-request-id": request_id}}
    transport = http.KubernetesHTTP(engine_target, env, duration=1, runner=_forbidden,
                                    profile=http.GATEWAY_EVENT_PROFILE, retain_gateway_response=True)
    start = int(NOW.timestamp()) * 10**9
    finish = start + 100_000_000
    run = target["kubernetes"]["run_id"]
    projection = verify_owned_pod(owner["pod"], owner["namespace"]["metadata"], gateway_binding, run)
    gateway_owners = {"before": {"owner": projection, "observed_at_ns": start - 1},
                      "after": {"owner": projection, "observed_at_ns": finish + 1}}
    calls = []
    auth = "Bearer EXAMPLE-private-auth"
    with monkeypatch.context() as patch:
        patch.setattr(transport, "_verify", lambda: None)
        patch.setattr(transport, "_call", lambda *args, **kwargs: calls.append(True) or [source_row])
        if loopback or capture_packet:
            observations = iter(gateway_owners.values())
            patch.setattr(transport, "_gateway_owner_snapshot", lambda *args, **kwargs: next(observations))
        patch.setattr(http.time, "time_ns", iter((start, finish)).__next__)
        if loopback or capture_packet:
            captured = transport.capture_retained_gateway_response(gateway_binding, auth, timeout=1, loopback=loopback)
            response = captured["response"]
            report, gateway_owners = captured["transport_report"], captured["gateway_owners"]
        else:
            endpoint = f"http://{gateway_binding['pod_ip']}:8080"
            transport.declare_gateway(endpoint)
            response, = transport("kubernetes-probe", [{"id": "obs01-before", "mode": "json", "method": "POST",
                "url": endpoint + "/v1/chat/completions", "headers": {"Authorization": auth},
                "body": {"model": MODEL, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1,
                         "stream": False}}], 1)
            report = transport.report()
    assert calls == [True]
    report_raw = canonical_json(report)
    assert PRIVATE.encode() not in report_raw and b"EXAMPLE-private-auth" not in report_raw
    assert report["requests"][0]["retained_normal_response"]["response_sha256"] == sha256_hex(canonical_json(response))
    if failure == "source-absent":
        del report["requests"][0]["retained_normal_response"]
    if failure == "cleanup":
        report["cleanup_errors"] = ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]

    if failure == "owner":
        gateway_owners["after"]["owner"] = {**projection, "container_id": "EXAMPLE-unrelated"}
    if failure == "owner-before-generation":
        gateway_owners["before"]["observed_at_ns"] = snapshot._epoch_ns(gateway_binding["started_at"]) - 1
    if response_only:
        if failure == "owner-after-early":
            gateway_owners["after"]["observed_at_ns"] = finish - 1
        content = {"environment": env.to_dict(), "target": target, "guard_policy": pol.to_dict(),
                   "preparation": prepared_document}
        schema = assembler.RESPONSE_ONLY_SOURCE_SCHEMA
        if capture_packet:
            content["capture"] = captured
            schema = assembler.RESPONSE_CAPTURE_SOURCE_SCHEMA
        else:
            content.update(response=response, transport_report=report, gateway_owners=gateway_owners)
        files = {name: _put(sources_dir, name + ".saved", value) for name, value in content.items()}
        inputs = _put(sources_dir, "inputs.json", {"schema": schema, "files": files,
            "side": side, "request_sha256": "0" * 64 if failure == "wrong-request" else request_sha})
        return env, pol, inputs, body
    reader = GatewayRecordReader({**transport.binding, "gateway": gateway_binding}, target, source["record_session"],
                                 binary="kubectl", duration=1, runner=_forbidden, snapshot_only=True)
    identity = {"device": 1, "inode": 2, "size": len(journal), "mtime_ns": start, "ctime_ns": start}
    file_packet = canonical_json({
        "schema": "guard-container-file/2", "source_complete": True, "file_identity_stable": True,
        "truncated": False, "nofollow_components": True, "regular_file": True,
        "raw_base64": base64.b64encode(journal).decode("ascii"),
        "raw_retained_bytes": len(journal), "raw_retained_sha256": sha256_hex(journal),
        "file_before": identity, "file_after": identity, "path_after": identity,
    })
    with monkeypatch.context() as patch:
        patch.setattr(reader, "_owner", lambda timeout: projection)
        patch.setattr(reader, "_request", lambda *args, **kwargs: file_packet)
        raw, recorded_read = reader.read_record(target)
    assert raw == journal
    journal_receipt = {"file": recorded_read, "transport": reader.report()}
    journal_read, = journal_receipt["transport"]["reads"]
    if failure == "journal":
        journal += b"\n"
    elif failure == "journal-transport-unbound":
        journal_read.pop("file_path_sha256", None)
        journal_read.pop("file_bytes_sha256", None)
    elif failure == "journal-transport-path":
        journal_read["file_path_sha256"] = sha256_hex(b"unrelated file path")
    elif failure == "journal-transport-bytes":
        journal_read["file_bytes_sha256"] = sha256_hex(b"different saved read")
    content = {"environment": env.to_dict(), "target": target, "guard_policy": pol.to_dict(),
               "preparation": prepared_document, "response": response, "transport_report": report,
               "gateway_owners": gateway_owners, "journal": journal, "journal_receipt": journal_receipt}
    files = {name: _put(sources_dir, name + ".saved", value) for name, value in content.items()}
    inputs = _put(sources_dir, "inputs.json", {"schema": assembler.SCHEMA, "files": files, "side": side,
        "actor_sha256": source["actor_sha256"], "request_sha256": source["request_sha256"],
        "record_session": source["record_session"]})
    return env, pol, inputs, body


@pytest.mark.parametrize("failure", [
    None, "source-absent", "incomplete", "owner", "owner-before-generation", "cleanup", "journal",
    "journal-transport-unbound", "journal-transport-path", "journal-transport-bytes", "unmodeled-response-field",
])
def test_prepares_only_an_original_owned_normal_capture(tmp_path, gateway_pair, monkeypatch, failure):
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    env, pol, inputs, body = _sources(tmp_path, gateway_pair, pack, monkeypatch, failure)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(http.KubernetesHTTP, "__call__", _forbidden)
    output = tmp_path / "retained"
    if failure is not None:
        reservations = []
        if failure == "unmodeled-response-field":
            reserve = assembler.reserve_output

            def record_reservation(path, *, protected):
                reservations.append(path)
                return reserve(path, protected=protected)

            monkeypatch.setattr(assembler, "reserve_output", record_reservation)
        with pytest.raises(ValueError, match="REQ13_GATEWAY_CAPTURE_SOURCE_INVALID"):
            assembler.prepare(inputs["path"], inputs["sha256"], output, pack=pack)
        assert not reservations and not output.exists()
        return

    result = assembler.prepare(inputs["path"], inputs["sha256"], output, pack=pack)
    assert json.loads((output / "selection.json").read_bytes()) == result
    assert (output / "response.bin").read_bytes() == body and (output / "response.bin").stat().st_mode & 0o777 == 0o600
    assert (output.stat().st_mode & 0o777) == 0o700
    check = pack.checks["REQ-13"]
    target_path = Path(result["target"])
    target = target_path.read_bytes()
    authority = {
        "expected_target_sha256": sha256_hex(canonical_json(json.loads(target))),
        "expected_manifest_sha256": result[snapshot.INPUT]["sha256"],
        "expected_policy_sha256": pol.ref().sha256,
    }
    executor = KubernetesResponseContentExecutor(
        result, runner=_forbidden, expected_target_file_sha256=sha256_hex(target),
        expected_policy_validity=(pol.valid_from, pol.valid_until), **authority,
    )
    evidence = executor.run(check, env, VALUES, None, tmp_path / "work")
    assert evidence.data_origin is DataOrigin.EXAMPLE
    assert evidence.observations["response_cases_tested"].value == ["gateway:normal"]
    artifact, = evidence.artifacts
    assert PRIVATE.encode() not in artifact.path.read_bytes()

    # A later raw pin cannot override the capture pin in a published selection.
    replacement = target + b"\n"
    target_path.write_bytes(replacement)
    changed = KubernetesResponseContentExecutor(
        result, runner=_forbidden, expected_target_file_sha256=sha256_hex(replacement),
    )
    with pytest.raises(ValueError, match=snapshot.INVALID):
        changed.prepared_response_snapshot(check, env, VALUES,
            policy_validity=(pol.valid_from, pol.valid_until), **authority)
    assert result[snapshot.INPUT]["target_file_sha256"] == sha256_hex(target)


@pytest.mark.parametrize("side,version,failure", [
    ("baseline", "0.30.0", None), ("candidate", "0.31.0", None),
    ("baseline", "0.30.0", "source-absent"), ("candidate", "0.31.0", "incomplete"),
    ("baseline", "0.30.0", "owner"), ("candidate", "0.31.0", "owner-after-early"),
    ("baseline", "0.30.0", "draft-policy"), ("candidate", "0.31.0", "policy-window"),
    ("baseline", "0.30.0", "wrong-request"),
])
def test_prepares_owned_gateway_response_without_an_audit(tmp_path, gateway_pair, monkeypatch, side, version, failure):
    from guard.core.judge import judge_evidence
    from guard.core.run import _normalize
    from guard.models import Evidence
    from tests.builders import dummy_ref

    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    env, pol, inputs, body = _sources(tmp_path, gateway_pair, pack, monkeypatch, failure,
                                     side=side, response_only=True)
    assert env.engine.version == version
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(http.KubernetesHTTP, "__call__", _forbidden)
    output = tmp_path / "retained"
    if failure:
        with pytest.raises(ValueError, match="REQ13_GATEWAY_CAPTURE_SOURCE_INVALID"):
            assembler.prepare(inputs["path"], inputs["sha256"], output, pack=pack)
        assert not output.exists()
        return

    result = assembler.prepare(inputs["path"], inputs["sha256"], output, pack=pack)
    selection = result[snapshot.INPUT]
    assert selection["source_scope"] == snapshot.RESPONSE_ONLY_SCHEMA
    assert set(path.name for path in output.iterdir()) == {"manifest.json", "selection.json"}
    manifest = json.loads(Path(selection["path"]).read_bytes())
    assert set(manifest) == snapshot.RESPONSE_ONLY_FILES | {"schema"}
    response = json.loads(Path(manifest["response"]["path"]).read_bytes())
    assert response["body"].encode() == body
    target = Path(result["target"]).read_bytes()
    executor = KubernetesResponseContentExecutor(result, runner=_forbidden,
        expected_target_sha256=sha256_hex(canonical_json(json.loads(target))),
        expected_target_file_sha256=sha256_hex(target), expected_manifest_sha256=selection["sha256"],
        expected_policy_sha256=pol.ref().sha256, expected_policy_validity=(pol.valid_from, pol.valid_until))
    check = pack.checks["REQ-13"]
    draft = executor.run(check, env, pol.values["REQ-13"], None, tmp_path / "work")
    assert draft.status.value == "COMPLETED" and draft.data_origin is DataOrigin.EXAMPLE
    assert draft.observations["response_cases_tested"].value == ["gateway:normal"]
    assert draft.observations["response_coverage_complete"].value is False
    assert all(not step.attempted for step in draft.challenges)
    artifact, = draft.artifacts
    raw_artifact = artifact.path.read_bytes()
    assert PRIVATE.encode() not in raw_artifact and b"EXAMPLE-private-auth" not in raw_artifact
    proof = json.loads(raw_artifact)["snapshot"]
    assert proof["profile"] == snapshot.RESPONSE_ONLY_PROFILE
    assert proof["guard_policy_sha256"] == pol.ref().sha256 and proof["request_sha256"] == selection["request_sha256"]
    assert proof["gateway_owner_before_ns"] <= proof["observed_started_ns"] < proof["observed_finished_ns"] <= proof["gateway_owner_after_ns"]
    assert proof["response_file_sha256"] == manifest["response"]["sha256"]
    assert "selected_row_sha256" not in proof and "actor_sha256" not in proof
    evidence = Evidence(id="EVD-REQ13-RESPONSE-ONLY", data_origin=draft.data_origin, plan=dummy_ref("PLAN"),
        environment=env.ref(), check_id=check.id, side="single", execution={"status": draft.status},
        **_normalize(check, draft)).seal()
    assert judge_evidence(check, evidence, pol, NOW).verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("side,failure", [
    ("baseline", None), ("candidate", None), ("baseline", "legacy-origin"), ("candidate", "owner"),
    ("baseline", "wrong-request"), ("baseline", "stale-pin"), ("candidate", "noncanonical"),
    ("candidate", "missing-member"), ("baseline", "late-change"),
])
def test_retain_response_cli_accepts_only_the_exact_owned_loopback_packet(
        tmp_path, gateway_pair, monkeypatch, side, failure):
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    env, pol, inputs, body = _sources(tmp_path, gateway_pair, pack, monkeypatch, failure, side=side,
                                     response_only=True, loopback=failure != "legacy-origin", capture_packet=True)
    source = json.loads(Path(inputs["path"]).read_bytes())
    capture = Path(source["files"]["capture"]["path"])
    original = capture.read_bytes()
    packet = json.loads(original)
    if failure == "stale-pin":
        packet["transport_report"]["limitations"].append("EXAMPLE changed after pin")
        capture.write_bytes(canonical_json(packet))
    elif failure in {"noncanonical", "missing-member"}:
        if failure == "missing-member":
            del packet["gateway_owners"]
        raw = original + b"\n" if failure == "noncanonical" else canonical_json(packet)
        source["files"]["capture"] = _put(tmp_path, "changed-packet.json", raw)
        inputs = _put(tmp_path, "changed-inputs.json", source)
    elif failure == "late-change":
        read_prepared = KubernetesResponseContentExecutor.prepared_response_snapshot

        def after_qualification(self, *args, **kwargs):
            result = read_prepared(self, *args, **kwargs)
            capture.write_bytes(original + b"\n")
            return result

        monkeypatch.setattr(KubernetesResponseContentExecutor, "prepared_response_snapshot", after_qualification)
    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(http.KubernetesHTTP, "capture_retained_gateway_response", _forbidden)
    output = tmp_path / "packet-selected"
    monkeypatch.setattr(sys, "argv", ["mvp_gateway_preparation", "retain-response", "--inputs", inputs["path"],
                                    "--inputs-sha256", inputs["sha256"], "--output", str(output)])
    if failure is not None:
        with pytest.raises(SystemExit) as exc:
            gateway.main()
        assert exc.value.code == 2 and not output.exists()
        return

    gateway.main()
    assert output.stat().st_mode & 0o777 == 0o700
    selection = json.loads((output / "selection.json").read_bytes())
    binding = selection[snapshot.INPUT]
    assert binding["transport_origin"] == http.GATEWAY_LOOPBACK_RESPONSE_ORIGIN
    assert binding["request_sha256"] == source["request_sha256"]
    assert binding["guard_policy_sha256"] == pol.ref().sha256
    manifest_raw = Path(binding["path"]).read_bytes()
    assert sha256_hex(manifest_raw) == binding["sha256"]
    manifest = json.loads(manifest_raw)
    assert manifest["guard_policy"] == source["files"]["guard_policy"]
    assert Path(selection["target"]) == Path(source["files"]["target"]["path"])
    assert capture.read_bytes() == original
    for name, value in packet.items():
        path = Path(manifest[name]["path"])
        member = path.read_bytes()
        assert path.parent == output and path.stat().st_mode & 0o777 == 0o600
        assert member == canonical_json(value) and manifest[name]["sha256"] == sha256_hex(member)
        assert b"EXAMPLE-private-auth" not in member
        if name != "response":
            assert PRIVATE.encode() not in member
    assert packet["response"]["body"].encode("utf-8") == body
    assert PRIVATE.encode() not in manifest_raw
    assert env.data_origin is DataOrigin.EXAMPLE


@pytest.mark.parametrize("side,failure", [
    ("baseline", None), ("candidate", None), ("baseline", "legacy-origin"), ("candidate", "owner"),
    ("baseline", "wrong-request"), ("candidate", "target-pin"), ("baseline", "draft-policy"),
    ("candidate", "policy-window"), ("baseline", "late-policy-drift"),
])
def test_save_loopback_capture_hands_original_packet_to_retain_response(
        tmp_path, gateway_pair, monkeypatch, side, failure):
    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    _, _, inputs, _ = _sources(tmp_path, gateway_pair, pack, monkeypatch, failure, side=side,
                              response_only=True, loopback=failure != "legacy-origin", capture_packet=True)
    source = json.loads(Path(inputs["path"]).read_bytes())
    pinned = source["files"]
    original_path = Path(pinned["capture"]["path"])
    original = original_path.read_bytes()
    packet = json.loads(original)
    bindings = {name: pinned[name] for name in ("environment", "target", "guard_policy")}
    if failure == "target-pin":
        bindings["target"] = {**pinned["target"], "sha256": "0" * 64}
    elif failure == "late-policy-drift":
        write_original = assembler._write
        policy_path = Path(pinned["guard_policy"]["path"])

        def after_write(path, raw):
            pointer = write_original(path, raw)
            policy_path.write_bytes(policy_path.read_bytes() + b"\n")
            return pointer

        monkeypatch.setattr(assembler, "_write", after_write)

    monkeypatch.setattr(socket, "socket", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    monkeypatch.setattr(http.KubernetesHTTP, "capture_retained_gateway_response", _forbidden)
    output = tmp_path / "saved-capture"
    settings = dict(**bindings, request_sha256=source["request_sha256"], pack=pack)
    if failure is not None:
        with pytest.raises(ValueError, match="REQ13_GATEWAY_CAPTURE_SOURCE_INVALID"):
            assembler.save_loopback_capture(packet, output, **settings)
        assert not output.exists() and original_path.read_bytes() == original
        return

    pointer = assembler.save_loopback_capture(packet, output, **settings)
    capture = output / "capture.json"
    assert pointer == {"path": str(capture), "sha256": sha256_hex(original)}
    assert capture.read_bytes() == original_path.read_bytes() == original
    assert output.stat().st_mode & 0o777 == 0o700 and capture.stat().st_mode & 0o777 == 0o600
    assert b"EXAMPLE-private-auth" not in original
    with pytest.raises(ValueError, match="existing evidence is never overwritten"):
        assembler.save_loopback_capture(packet, output, **settings)
    assert capture.read_bytes() == original

    pinned["capture"] = pointer
    source_ref = _put(tmp_path, "new-source.json", source)
    prepared = assembler.prepare(source_ref["path"], source_ref["sha256"], tmp_path / "retained", pack=pack)
    selection = prepared[snapshot.INPUT]
    assert selection["transport_origin"] == http.GATEWAY_LOOPBACK_RESPONSE_ORIGIN
    assert selection["request_sha256"] == source["request_sha256"]
    manifest = json.loads(Path(selection["path"]).read_bytes())
    for name, document in packet.items():
        assert Path(manifest[name]["path"]).read_bytes() == canonical_json(document)
