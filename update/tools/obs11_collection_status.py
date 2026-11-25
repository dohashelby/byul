"""Publish one observed Gateway result arrival for an owned OBS-11 EXAMPLE drill.

The Gateway must already have handled the selected harmless request. Run this
collector on the owned Linux node with the probe's read-only status hostPath.
The journal's original timestamp is the result event time. This collector's
first successful bounded read is the arrival. Gap, alert and dependent verdicts
are outside this collector's scope, so it only publishes a timely arrival.

Call the collector immediately after each side's selected response. In the
side's Guard configuration use the same target, set
`check_config.OBS-11.collection_status_snapshot_only` to true, and bind
`run_binding.OBS-11.record_session` to the published collection session.
"""
from __future__ import annotations

import argparse
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile
from time import monotonic_ns, time_ns

from guard.collectors.file import load_environment, load_policy
from guard.executors import log_collection_gap, node_image_proof
from guard.executors.kubernetes_gateway_observation import RECORD_PATH, GatewayRecordReader, snapshot_owner, snapshot_record
from guard.executors.observation_contract import _time as parse_record_time
from guard.executors.observation_contract import fingerprint
from guard.models import DataOrigin, PolicyStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import REQUEST_BINDING_PROFILE, KubernetesHTTP
from tools.mvp_a_input_preflight import _read

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MILLISECOND = timedelta(milliseconds=1)
_NS_PER_MS = 1_000_000
_SESSION = re.compile(r"[0-9a-f]{32}")
_SOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_STATUS_PATH = "/guard-collection"


def _validate_selection(source, gateway_session, actor, values, collection_session):
    source_ids = {"environment", "target_id"}
    hashes = {"runtime_policy_sha256", "request_sha256"}
    if (type(source) is not dict or source.keys() != source_ids | hashes
            or any(type(source[key]) is not str or not _SOURCE_ID.fullmatch(source[key]) for key in source_ids)
            or any(not fingerprint(source[key]) for key in hashes) or not fingerprint(actor)
            or any(type(session) is not str or not _SESSION.fullmatch(session)
                   for session in (gateway_session, collection_session))
            or gateway_session == collection_session):
        raise ValueError("OBS11_SELECTION_INVALID")
    effective = {**values, "collection_session": collection_session}
    log_collection_gap.validate_policy(effective)
    if "result" not in effective["required_records"]:
        raise ValueError("OBS11_RESULT_OUT_OF_SCOPE")
    return effective


def _status_mount(pod, probe, mount):
    try:
        container, = [row for row in pod["spec"]["containers"] if row["name"] == probe["container"]]
        selected, = [row for row in container.get("volumeMounts", []) if row["mountPath"] == _STATUS_PATH]
        volume, = [row for row in pod["spec"]["volumes"] if row["name"] == selected["name"]]
    except (KeyError, TypeError, ValueError):
        raise ValueError("OBS11_PROBE_STATUS_MOUNT_UNVERIFIED") from None
    if (selected.get("readOnly") is not True or "subPath" in selected or "subPathExpr" in selected
            or selected.get("mountPropagation", "None") != "None"
            or volume.get("hostPath") != {"path": str(mount), "type": "Directory"}):
        raise ValueError("OBS11_PROBE_STATUS_MOUNT_UNVERIFIED")


def _local_owned_directory(path):
    if path.resolve(strict=True) != path or not path.is_dir():
        raise ValueError("OBS11_STATUS_DIRECTORY_UNVERIFIED")
    info = path.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o022 or not (info.st_mode & stat.S_IXOTH):
        raise ValueError("OBS11_STATUS_DIRECTORY_UNVERIFIED")
    return path


def _publish(mount, session, packet):
    data = mount / "data"
    try:
        data.mkdir(mode=0o755)
    except FileExistsError:
        pass
    else:
        data.chmod(0o755)
    _local_owned_directory(data)
    directory = data / session
    directory.mkdir(mode=0o755)
    try:
        directory.chmod(0o755)
        output = directory / "collection-status.json"
        raw = canonical_json(packet) + b"\n"
        with NamedTemporaryFile(dir=directory, prefix=".status-", delete_on_close=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), 0o444)  # The probe can only read this metadata.
            os.fsync(stream.fileno())
            os.link(stream.name, output, follow_symlinks=False)
    except BaseException:
        # A published file prevents rmdir. A failed write can retry the same session.
        with suppress(OSError):
            directory.rmdir()
        raise
    return sha256_hex(raw)


def _clock_sample():
    before = monotonic_ns()
    wall = time_ns()
    return before, wall, monotonic_ns()


def _result_packet(environment_ref, values, recorded_at, before, after):
    elapsed_wall = after[1] - before[1]
    elapsed_min = after[0] - before[2]
    elapsed_max = after[2] - before[0]
    difference = max(abs(elapsed_wall - elapsed_min), abs(elapsed_wall - elapsed_max))
    # Retain the observed read-interval clock disagreement and millisecond rounding.
    uncertainty = (difference + _NS_PER_MS - 1) // _NS_PER_MS + 1
    event = {"record": "result", "event_at_ms": (parse_record_time(recorded_at) - _EPOCH) // _MILLISECOND,
             "arrived_at_ms": after[1] // _NS_PER_MS, "gap_marked_at_ms": None, "alert_at_ms": None,
             "dependent_result": None, "clock_uncertainty_ms": uncertainty}
    session = values["collection_session"]
    packet = {"schema": log_collection_gap.SCHEMA, "session": session,
              "environment_ref": environment_ref, "events": [event]}
    log_collection_gap.validate_record(packet, environment_ref=environment_ref, session=session)
    if log_collection_gap.evaluate(packet, values)["normal"] != ["result"]:
        # Null gap/alert times would imply a violation for a late or missing
        # arrival. This collector has no observation of either channel.
        raise ValueError("OBS11_TIMELY_ARRIVAL_UNVERIFIED")
    return packet


def collect_gateway_result(target, env, *, mount, source, gateway_session, actor, values, collection_session,
                           kubectl_binary="kubectl"):
    """Read the explicitly selected result and write the exact probe status path."""
    effective = _validate_selection(source, gateway_session, actor, values, collection_session)
    mount = _local_owned_directory(Path(mount).absolute())
    transport = KubernetesHTTP(target, env, duration=30, binary=kubectl_binary, profile=REQUEST_BINDING_PROFILE)
    gateway = snapshot_owner(target, transport.binding)
    probe = transport.binding["probe"]
    if target["lab"] is not False or probe["node"] != gateway["node"]:
        raise ValueError("OBS11_SHARED_NODE_REQUIRED")
    _status_mount(transport._verify()["probe"], probe, mount)
    node = transport._call(["get", "node", gateway["node"], "-o", "json"], budget=5)
    node_identity = node_image_proof.validate_node(node, gateway["node"], node_image_proof.kernel_identity())
    reader = GatewayRecordReader({**transport.binding, "gateway": gateway}, target, gateway_session,
                                 binary=transport.binary, duration=30, runner=None, snapshot_only=True)
    before = _clock_sample()
    raw, provenance = reader.read_file(reader, target, container=gateway["container"], image=gateway["image"],
                                       path=RECORD_PATH)
    after = _clock_sample()
    _status_mount(transport._verify()["probe"], probe, mount)
    file_sha = sha256_hex(raw)
    if (provenance.get("owner_binding_verified") is not True or provenance.get("capture_sha256") != file_sha
            or provenance.get("bytes") != len(raw) or reader.gap or transport.cleanup_errors):
        raise ValueError("OBS11_GATEWAY_READ_UNVERIFIED")
    selection = {"observation_session": gateway_session, "observation_allowed_actor_sha256": actor}
    _, record_sha, recorded_at = snapshot_record(raw, source=source, values=selection,
                                                  run_id=transport.binding["run_id"])
    packet = _result_packet(env.ref().model_dump(mode="json"), effective, recorded_at, before, after)
    status_sha = _publish(mount, collection_session, packet)
    return {"environment_ref": packet["environment_ref"], "target_sha256": transport.target_sha256,
            "gateway_journal_sha256": file_sha, "selected_result_sha256": record_sha,
            "status_file_sha256": status_sha, "shared_node_boot_sha256": node_identity["boot_id_sha256"],
            "collection_status_snapshot_only": True, "record_session": collection_session, "official": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("env", "target", "pack", "policy", "status-mount"):
        parser.add_argument(f"--{option}", type=Path, required=True)
    for option in ("source-environment", "source-target-id", "runtime-policy-sha256", "request-sha256",
                   "allowed-actor-sha256", "gateway-session", "collection-session"):
        parser.add_argument(f"--{option}", required=True)
    parser.add_argument("--kubectl-binary", default="kubectl")
    args = parser.parse_args()
    pack = load_pack(args.pack)
    policy = load_policy(args.policy, pack)
    if policy.status != PolicyStatus.DRAFT or policy.data_origin != DataOrigin.EXAMPLE:
        parser.error("OBS-11 collector requires a DRAFT EXAMPLE policy")
    source = {"environment": args.source_environment, "target_id": args.source_target_id,
              "runtime_policy_sha256": args.runtime_policy_sha256, "request_sha256": args.request_sha256}
    target_bytes, _ = _read(args.target, max_bytes=65536)
    receipt = collect_gateway_result(strict_json(target_bytes, limit=65536), load_environment(args.env),
                                     mount=args.status_mount, source=source, gateway_session=args.gateway_session,
                                     actor=args.allowed_actor_sha256, values=policy.values.get("OBS-11", {}),
                                     collection_session=args.collection_session, kubectl_binary=args.kubectl_binary)
    print(canonical_json(receipt).decode("utf-8"))


if __name__ == "__main__":
    main()
