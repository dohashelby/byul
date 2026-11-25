"""The real Gateway record writer feeds the OBS-11 preparation path and consumer."""

import json
import os
import stat

import pytest

from guard.executors import log_collection_gap
from guard.executors import kubernetes_gateway_observation as gateway
from guard.models import canonical_json, sha256_hex
from tests.test_kubernetes_gateway_observation import ordinary_gateway_record  # noqa: F401
from tests.test_kubernetes_request_binding import env, target
from tools import obs11_collection_status as tool


@pytest.mark.parametrize("case", ["ordinary", "late", "future", "clock_step", "wrong_source", "wrong_mount", "changed_read"])
def test_owned_gateway_status_is_admitted_only_for_same_scope_timely_arrival(
    tmp_path, monkeypatch, ordinary_gateway_record, case,
):
    raw, final, source = ordinary_gateway_record
    environment = env()
    selected = target(environment)
    selected["lab"] = False
    selected["kubernetes"]["run_id"] = "guard-fixture"
    engine, probe = (selected["kubernetes"][key] for key in ("engine", "probe"))
    probe.update(namespace=engine["namespace"], namespace_uid=engine["namespace_uid"], node=engine["node"])
    selected["kubernetes_gateway"] = {**engine, "container": "gateway", "pod": "gateway-pod",
                                    "pod_ip": "127.0.0.3", "container_id": "containerd://" + "3" * 64,
                                    "pod_uid": "10000000-0000-0000-0000-000000000003"}
    mount = tmp_path.resolve() / "status"
    mount.mkdir()
    mount.chmod(0o755)
    pod = {"spec": {"containers": [{"name": probe["container"], "volumeMounts": [
        {"name": "collection", "mountPath": "/guard-collection", "readOnly": case != "wrong_mount"}]}],
        "volumes": [{"name": "collection", "hostPath": {"path": str(mount), "type": "Directory"}}]}}
    monkeypatch.setattr(tool.KubernetesHTTP, "_verify", lambda self: {"probe": pod})
    kernel = {"machine_id": "1" * 32, "boot_id": "10000000-0000-0000-0000-000000000001",
              "system_uuid": "10000000-0000-0000-0000-000000000002", "architecture": "amd64", "os": "linux"}
    node = {"kind": "Node", "metadata": {"name": engine["node"], "uid": "10000000-0000-0000-0000-000000000004"},
            "status": {"nodeInfo": {"machineID": kernel["machine_id"], "bootID": kernel["boot_id"],
                                    "systemUUID": kernel["system_uuid"], "architecture": "amd64",
                                    "operatingSystem": "linux", "containerRuntimeVersion": "containerd://1"}}}
    monkeypatch.setattr(tool.KubernetesHTTP, "_call", lambda self, args, *, budget: node)
    monkeypatch.setattr(tool.node_image_proof, "kernel_identity", lambda: kernel)

    def read_file(self, client, target_, *, container, image, path):
        assert target_ == selected and self.binding["engine"] == selected["kubernetes_gateway"]
        assert path == gateway.RECORD_PATH
        self.rows.append({"program": "file"})
        digest = "0" * 64 if case == "changed_read" else sha256_hex(raw)
        return raw, {"capture_sha256": digest, "bytes": len(raw), "owner_binding_verified": True}

    monkeypatch.setattr(gateway.GatewayRecordReader, "read_file", read_file)
    published_ms = (tool.parse_record_time(final["recorded_at"]) - tool._EPOCH) // tool._MILLISECOND
    delay_ms = {"late": 5100, "future": -10}.get(case, 100)
    arrival = (published_ms + delay_ms) * tool._NS_PER_MS
    elapsed = 500 * tool._NS_PER_MS if case == "clock_step" else 10 * tool._NS_PER_MS
    before, after = (0, arrival - elapsed, 0), (10_000_000, arrival, 10_000_000)
    samples = iter([before, after, before, after])
    monkeypatch.setattr(tool, "_clock_sample", lambda: next(samples))
    records = ["access", "request", "policy", "execution", "result"]
    values = {"required_records": records, "collection_deadline_ms": dict.fromkeys(records, 5000),
              "max_clock_uncertainty_ms": dict.fromkeys(records, 100),
              "detection_deadline_ms": dict.fromkeys(records, 5000),
              "alert_deadline_ms": dict.fromkeys(records, 10000)}
    collection_session = "f" * 32
    args = {"mount": mount, "source": source if case != "wrong_source" else {**source, "request_sha256": "0" * 64},
            "gateway_session": "b" * 32, "collection_session": collection_session,
            "actor": gateway.principal_sha256("fixture-principal"), "values": values}
    status_file = mount / "data" / collection_session / "collection-status.json"
    faults = {"late": "OBS11_TIMELY_ARRIVAL_UNVERIFIED", "future": "OBS11_TIMELY_ARRIVAL_UNVERIFIED",
              "clock_step": "OBS11_TIMELY_ARRIVAL_UNVERIFIED", "wrong_source": "GATEWAY_AUDIT_FINAL_NOT_UNIQUE",
              "wrong_mount": "OBS11_PROBE_STATUS_MOUNT_UNVERIFIED", "changed_read": "OBS11_GATEWAY_READ_UNVERIFIED"}
    if case in faults:
        with pytest.raises(ValueError, match=faults[case]):
            tool.collect_gateway_result(selected, environment, **args)
        assert not status_file.exists()
        return

    original_umask = os.umask(0o077)
    try:
        receipt = tool.collect_gateway_result(selected, environment, **args)
    finally:
        os.umask(original_umask)
    raw_status = status_file.read_bytes()
    packet = log_collection_gap.validate_record(json.loads(raw_status),
        environment_ref=environment.ref().model_dump(mode="json"), session=collection_session)
    assert log_collection_gap.evaluate(packet, values) == {
        "violations": [], "unverified": [], "missing": records[:-1], "normal": ["result"],
    }
    assert packet["events"] == [{"record": "result", "event_at_ms": published_ms,
        "arrived_at_ms": arrival // tool._NS_PER_MS, "clock_uncertainty_ms": 1, "gap_marked_at_ms": None,
        "alert_at_ms": None, "dependent_result": None}]
    assert receipt["selected_result_sha256"] == sha256_hex(canonical_json(final))
    assert receipt["status_file_sha256"] == sha256_hex(raw_status) and receipt["official"] is False
    assert b"EXAMPLE-PRIVATE" not in raw_status and final["request_id"].encode() not in raw_status
    assert stat.S_IMODE(status_file.stat().st_mode) == 0o444
    assert stat.S_IMODE(status_file.parent.stat().st_mode) == 0o755
    with pytest.raises(FileExistsError):
        tool.collect_gateway_result(selected, environment, **args)
    assert status_file.read_bytes() == raw_status


def test_publish_retries_same_session_after_failed_link(tmp_path, monkeypatch):
    mount = tmp_path.resolve()
    session = "c" * 32
    packet = {"events": []}
    output = mount / "data" / session / "collection-status.json"

    def failed_link(*args, **kwargs):
        raise OSError("TEST_LINK_FAILED")

    with monkeypatch.context() as failed:
        failed.setattr(tool.os, "link", failed_link)
        with pytest.raises(OSError, match="TEST_LINK_FAILED"):
            tool._publish(mount, session, packet)

    assert not output.parent.exists()
    assert tool._publish(mount, session, packet) == sha256_hex(output.read_bytes())
    assert json.loads(output.read_bytes()) == packet
