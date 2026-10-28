"""PLT-07: one explicitly forbidden UID/network-namespace TCP operation.

The owned sender and independent owned receiver must agree. Denial, missing
evidence and all other egress paths remain UNKNOWN; this is not a worker sensor
or an engine-wide default-deny test. No containers, images or models are started.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
from pathlib import Path
import re
import secrets
import time
from types import SimpleNamespace

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.docker import Docker
from . import egress_probe, egress_receiver
from .base import EvidenceDraft
from .owned_capture import read_owned_jsonl, strict_json
from .pressure_resources import capture_resource_sample
from .stream_pressure import _network_binding, _owner, _source_cutoff


PROFILE = "explicit-forbidden-egress-v1"
VERSION = "0.1.0"
_SHA = re.compile(r"[0-9a-f]{64}")
_POLICY_KEYS = {"subject_scope", "subject_container_id", "subject_uid", "subject_net_namespace_inode",
                "forbidden_receiver_container_id", "forbidden_receiver_ip", "forbidden_receiver_port",
                "forbidden_protocol", "forbidden_operation", "validity_start", "validity_end"}
_CODES = {"EGRESS_POLICY_BINDING_UNVERIFIED", "EGRESS_SOURCE_UNVERIFIED", "EGRESS_RECEIVER_UNVERIFIED",
          "EGRESS_RECEIVER_COMMAND_UNVERIFIED", "EGRESS_RECEIVER_PROCESS_UNVERIFIED", "EGRESS_FILE_CHANGED",
          "EGRESS_RECEIVER_RECORD_UNVERIFIED", "EGRESS_TRANSPORT_INCOMPLETE", "EGRESS_PACKET_INVALID",
          "EGRESS_OPERATION_UNVERIFIED", "EGRESS_CLOCK_WINDOW_UNVERIFIED", "EGRESS_INSTANCE_CHANGED",
          "EGRESS_COLLECTION_UNAVAILABLE", "EGRESS_DEADLINE", "EGRESS_ARTIFACT_UNAVAILABLE",
          "EGRESS_SOURCE_DEADLINE_UNVERIFIED"}


def _require(value, code):
    if not value:
        raise ValueError(code)


def _integer(value, low=1, high=2**63 - 1):
    return type(value) is int and low <= value <= high


def _when(value):
    _require(type(value) is str and 1 <= len(value) <= 40, "EGRESS_CLOCK_WINDOW_UNVERIFIED")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.tzinfo is not None and parsed.utcoffset() is not None, "EGRESS_CLOCK_WINDOW_UNVERIFIED")
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("EGRESS_CLOCK_WINDOW_UNVERIFIED") from None


def _wall_now():
    # Evidence object timestamps use the repository's second precision. Source
    # event windows retain microseconds, matching both independent helpers.
    return datetime.now(timezone.utc)


def normalize_policy(values):
    """Only the explicit human-entered rule; no inferred prohibition/defaults."""
    _require(type(values) is dict and set(values) == _POLICY_KEYS, "EGRESS_POLICY_BINDING_UNVERIFIED")
    _require(values["subject_scope"] == "all_processes_in_container_uid_netns"
             and values["forbidden_protocol"] == "tcp" and values["forbidden_operation"] == "tcp_connect"
             and all(type(values[k]) is str and _SHA.fullmatch(values[k])
                     for k in ("subject_container_id", "forbidden_receiver_container_id"))
             and values["subject_container_id"] != values["forbidden_receiver_container_id"]
             and _integer(values["subject_uid"], 0, 2**31 - 1)
             and _integer(values["subject_net_namespace_inode"])
             and _integer(values["forbidden_receiver_port"], 1, 65535), "EGRESS_POLICY_BINDING_UNVERIFIED")
    try:
        address = ipaddress.IPv4Address(values["forbidden_receiver_ip"])
        _require(type(values["forbidden_receiver_ip"]) is str and str(address) == values["forbidden_receiver_ip"]
                 and not address.is_unspecified and not address.is_multicast, "EGRESS_POLICY_BINDING_UNVERIFIED")
    except (ValueError, TypeError):
        raise ValueError("EGRESS_POLICY_BINDING_UNVERIFIED") from None
    start, end = _when(values["validity_start"]), _when(values["validity_end"])
    _require(start < end, "EGRESS_POLICY_BINDING_UNVERIFIED")
    return {"schema": "guard-explicit-forbidden-egress/1",
            "subject": {"scope": values["subject_scope"], "container_id": values["subject_container_id"],
                        "uid": values["subject_uid"], "net_namespace_inode": values["subject_net_namespace_inode"]},
            "destination": {"container_id": values["forbidden_receiver_container_id"], "ip": str(address),
                            "port": values["forbidden_receiver_port"], "protocol": "tcp", "operation": "tcp_connect"},
            "validity": {"start": values["validity_start"], "end": values["validity_end"]}}


def _identity(value):
    return (type(value) is dict and set(value) == {"pid", "start_time_ticks", "uid", "net_namespace_inode"}
            and _integer(value["pid"], 1, 2**31 - 1) and _integer(value["start_time_ticks"])
            and _integer(value["uid"], 0, 2**31 - 1) and _integer(value["net_namespace_inode"]))


def _endpoint(value):
    if type(value) is not dict or set(value) != {"ip", "port"} or not _integer(value["port"], 0, 65535):
        return False
    try:
        return type(value["ip"]) is str and str(ipaddress.IPv4Address(value["ip"])) == value["ip"]
    except ValueError:
        return False


def _packet(result):
    capture = getattr(result, "capture", None)
    _require(result.returncode == 0 and type(capture) is dict
             and capture.get("complete") is True and capture.get("bounded_at_source") is True
             and capture.get("truncated") is False and capture.get("timed_out") is False,
             "EGRESS_TRANSPORT_INCOMPLETE")
    raw = getattr(result, "stdout_bytes", None)
    if type(raw) is not bytes:
        raw = result.stdout.encode("utf-8")
    return strict_json(raw), hashlib.sha256(raw).hexdigest()


def _sender(value):
    keys = {"schema", "identity_before", "identity_after", "local", "peer", "socket_inode", "correlation_sha256",
            "connected", "marker_sent", "ack_verified", "sent_bytes", "received_bytes", "outcome", "observed_at",
            "process_identity_stable", "deadline_guard_verified", "connected_at", "marker_sent_at", "connection_guard_verified"}
    _require(type(value) is dict and set(value) == keys and value["schema"] == "guard-owned-egress-probe/1"
             and all(type(value[k]) is bool for k in ("connected", "marker_sent", "ack_verified", "process_identity_stable",
                                                     "deadline_guard_verified", "connection_guard_verified"))
             and all(value[k] is None or _identity(value[k]) for k in ("identity_before", "identity_after"))
             and all(value[k] is None or _endpoint(value[k]) for k in ("local", "peer"))
             and (value["socket_inode"] is None or _integer(value["socket_inode"]))
             and type(value["correlation_sha256"]) is str and _SHA.fullmatch(value["correlation_sha256"])
             and _integer(value["sent_bytes"], 0, 32) and _integer(value["received_bytes"], 0, 66)
             and type(value["outcome"]) is str and value["outcome"] in {
                 "identity_unavailable", "identity_mismatch", "connect_refused", "timeout", "transport_error", "ack_mismatch", "completed",
                 "source_deadline", "source_clock_unavailable", "source_clock_mismatch"},
             "EGRESS_PACKET_INVALID")
    for key in ("connected_at", "marker_sent_at", "observed_at"):
        if value[key] is not None:
            _when(value[key])
    return value


def _records(records):
    keys = {"schema", "kind", "identity", "local", "peer", "socket_inode", "observed_at",
            "correlation_sha256", "received_bytes", "ack_sent"}
    _require(type(records) is list and 1 <= len(records) <= 9, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
    for index, row in enumerate(records):
        _require(type(row) is dict and set(row) == keys and row["schema"] == "guard-owned-egress-receiver/1"
                 and row["kind"] == ("startup" if index == 0 else "receipt") and _identity(row["identity"])
                 and row["identity"] == records[0]["identity"] and _endpoint(row["local"])
                 and _integer(row["socket_inode"]) and _integer(row["received_bytes"], 0, 33)
                 and type(row["ack_sent"]) is bool, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
        if index == 0:
            _require(row["peer"] is None and row["correlation_sha256"] is None
                     and row["received_bytes"] == 0 and row["ack_sent"] is False, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
        else:
            _require(_endpoint(row["peer"]) and (row["correlation_sha256"] is None
                     or type(row["correlation_sha256"]) is str and _SHA.fullmatch(row["correlation_sha256"])),
                     "EGRESS_RECEIVER_RECORD_UNVERIFIED")
        _when(row["observed_at"])
    return records


# Bounded process metadata independently binds the receiver log's claimed writer
# to the known source command. It never emits argv, file contents or exceptions.
_RECEIVER_PROCESS = r'''
import hashlib,json,os,re,sys
from pathlib import Path
def read(path,limit):
 with path.open('rb') as f: value=f.read(limit+1)
 if len(value)>limit: raise ValueError()
 return value
def identity(root,pid):
 stat=read(root/'stat',8192).decode('ascii')
 if not stat.startswith(str(pid)+' ('): raise ValueError()
 start=int(stat.rsplit(')',1)[1].split()[19])
 rows=[r for r in read(root/'status',65536).decode('ascii').splitlines() if r.startswith('Uid:')]
 uids=rows[0].split()[1:] if len(rows)==1 else []
 if len(uids)!=4 or len(set(uids))!=1: raise ValueError()
 ns=re.fullmatch(r'net:\[([1-9][0-9]*)\]',os.readlink(root/'ns'/'net'))
 if ns is None: raise ValueError()
 return dict(pid=pid,start_time_ticks=start,uid=int(uids[0]),net_namespace_inode=int(ns[1]))
try:
 pid=int(sys.argv[1]);root=Path('/proc')/str(pid);before=identity(root,pid)
 raw=read(root/'cmdline',65536)
 if not raw.endswith(b'\0'): raise ValueError()
 argv=[v.decode('utf-8') for v in raw[:-1].split(b'\0')]
 command=hashlib.sha256(json.dumps(argv,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
 with os.scandir(root/'fd') as entries:
  fds=[]
  for entry in entries:
   if len(fds)>=128: raise ValueError()
   fds.append(entry.path)
 sockets=[]
 for path in fds:
  match=re.fullmatch(r'socket:\[([1-9][0-9]*)\]',os.readlink(path))
  if match:sockets.append(int(match[1]))
 after=identity(root,pid)
 out=dict(schema='guard-egress-receiver-process/1',identity=after,command_sha256=command,socket_inodes=sorted(set(sockets)),complete=before==after)
except Exception:
 out=dict(schema='guard-egress-receiver-process/1',complete=False)
print(json.dumps(out,sort_keys=True,separators=(',',':')))
'''


def _write_summary(workdir, summary):
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / "boundary_egress.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
    return path


class BoundaryEgressExecutor:
    name = "boundary_egress"
    version = VERSION

    def __init__(self, cfg, *, docker=None):
        self.cfg, self.injected = cfg, docker is not None
        self.docker = docker if docker is not None else Docker("egress" + secrets.token_hex(6))

    def run(self, check, env, policy_values, adapter, workdir):
        rule = normalize_policy(policy_values)
        _require(type(self.cfg) is dict and not set(self.cfg) - {
            "target", "receiver_target", "connect_timeout", "max_duration_seconds"}, "EGRESS_POLICY_BINDING_UNVERIFIED")
        timeout, duration = self.cfg.get("connect_timeout", 0.5), self.cfg.get("max_duration_seconds", 30)
        _require(all(type(v) in (int, float) and math.isfinite(v) and 0 < v <= limit
                     for v, limit in ((timeout, 2), (duration, 30))), "EGRESS_POLICY_BINDING_UNVERIFIED")
        def load(key):
            with Path(self.cfg[key]).open("rb") as stream:
                target = strict_json(stream.read(65537))
            _require(type(target) is dict and type(target.get("lab")) is bool
                     and type(target.get("engine")) is dict, "EGRESS_POLICY_BINDING_UNVERIFIED")
            return target
        target, receiver = load("target"), load("receiver_target")
        _require(env.hash_ok(), "EGRESS_POLICY_BINDING_UNVERIFIED")
        started, deadline = utcnow(), time.monotonic() + duration
        errors, successes, trials = [], [], []
        trial = {"profile": PROFILE, "policy_rule_sha256": sha256_hex(canonical_json(rule)), "verified": False}
        trials.append(trial)
        def remaining():
            value = deadline - time.monotonic()
            _require(value > 0, "EGRESS_DEADLINE")
            return min(5, value)
        receiver_env = SimpleNamespace(engine=SimpleNamespace(image_digest=receiver["engine"].get("image_digest")))
        source_code = Path(egress_probe.__file__).read_text(encoding="utf-8")
        receiver_code = Path(egress_receiver.__file__).read_text(encoding="utf-8")
        receiver_command = ["python3", "-u", "-c", receiver_code, str(rule["destination"]["port"])]
        def snapshots():
            source_owner = _owner(self.docker, env, target, deadline)
            receiver_owner = _owner(self.docker, receiver_env, receiver, deadline)
            source_binding = _network_binding(self.docker, target, source_owner, deadline)
            receiver_binding = _network_binding(self.docker, receiver, receiver_owner, deadline)
            _require(source_owner["container_id"] == rule["subject"]["container_id"]
                     and receiver_owner["container_id"] == rule["destination"]["container_id"]
                     and source_owner["container_id"] != receiver_owner["container_id"]
                     and source_binding["network_id"] == receiver_binding["network_id"]
                     and receiver_binding["host"] == rule["destination"]["ip"], "EGRESS_POLICY_BINDING_UNVERIFIED")
            inspected = self.docker.inspect_json(receiver["engine"]["container"], timeout=remaining())
            _require(inspected.get("Id") == receiver_owner["container_id"]
                     and inspected.get("Image") == receiver_owner["image_id"]
                     and inspected.get("State", {}).get("StartedAt") == receiver_owner["started_at"]
                     and inspected.get("Config", {}).get("Cmd") == receiver_command
                     and inspected["Config"].get("Entrypoint") in (None, []), "EGRESS_RECEIVER_COMMAND_UNVERIFIED")
            return source_owner, receiver_owner, source_binding, receiver_binding
        def records():
            rows, provenance = read_owned_jsonl(self.docker, receiver,
                container=receiver["engine"]["container"], image=receiver["engine"]["image_digest"],
                path="/guard-observations/egress.jsonl", timeout=remaining())
            return _records(rows), provenance
        def receiver_process(startup):
            result = self.docker.exec_bounded(receiver["engine"]["container"],
                ["python3", "-c", _RECEIVER_PROCESS, str(startup["identity"]["pid"])],
                timeout=remaining(), max_bytes=65536)
            packet, digest = _packet(result)
            _require(type(packet) is dict and set(packet) == {"schema", "identity", "command_sha256", "socket_inodes", "complete"}
                     and packet["schema"] == "guard-egress-receiver-process/1" and packet["complete"] is True
                     and packet["identity"] == startup["identity"]
                     and packet["command_sha256"] == sha256_hex(canonical_json(receiver_command))
                     and type(packet["socket_inodes"]) is list and len(packet["socket_inodes"]) <= 128
                     and all(_integer(v) for v in packet["socket_inodes"])
                     and startup["socket_inode"] in packet["socket_inodes"], "EGRESS_RECEIVER_PROCESS_UNVERIFIED")
            return digest
        try:
            before = snapshots()
            initial, provenance_before = records()
            startup = initial[0]
            _require(startup["local"]["ip"] in {"0.0.0.0", rule["destination"]["ip"]}
                     and startup["local"]["port"] == rule["destination"]["port"]
                     and startup["identity"]["net_namespace_inode"] != rule["subject"]["net_namespace_inode"]
                     and _when(startup["observed_at"]) <= _wall_now(), "EGRESS_RECEIVER_RECORD_UNVERIFIED")
            trial["receiver_process_before_sha256"] = receiver_process(startup)
            clock_window_start = _wall_now()
            clock_sample = capture_resource_sample(self.docker, target, env, deadline=deadline,
                                                   port=rule["destination"]["port"])
            received_at = time.monotonic()  # Receipt budget is recorded before other processing.
            clock_window_end = _wall_now()
            guard = _source_cutoff(clock_sample, received_at, deadline, before[0])
            _require(guard is not None, "EGRESS_SOURCE_DEADLINE_UNVERIFIED")
            _require(clock_window_start <= _when(clock_sample.get("timestamp")) <= clock_window_end,
                     "EGRESS_CLOCK_WINDOW_UNVERIFIED")
            trial["source_deadline_guard_sha256"] = sha256_hex(canonical_json(guard))
            nonce = secrets.token_hex(16)
            marker_hash = hashlib.sha256(nonce.encode("ascii")).hexdigest()
            window_start = _wall_now()
            _require(_when(rule["validity"]["start"]) <= window_start < _when(rule["validity"]["end"]),
                     "EGRESS_CLOCK_WINDOW_UNVERIFIED")
            result = self.docker.exec_bounded(target["engine"]["container"],
                ["python3", "-c", source_code, rule["destination"]["ip"], str(rule["destination"]["port"]),
                 nonce, str(min(timeout, remaining())), str(rule["subject"]["uid"]),
                 str(rule["subject"]["net_namespace_inode"]), str(guard["time_namespace_inode"]),
                 str(guard["deadline_monotonic_ns"])], uid=rule["subject"]["uid"],
                timeout=remaining(), max_bytes=65536)
            sender_packet, trial["sender_packet_sha256"] = _packet(result)
            sender = _sender(sender_packet)
            trial.update(sender_outcome=sender["outcome"], sender_ack_verified=sender["ack_verified"],
                sender_received_bytes=sender["received_bytes"], final_deadline_guard_verified=sender["deadline_guard_verified"],
                sender_observed_at=_when(sender["observed_at"]).isoformat() if sender["observed_at"] is not None else None)
            final, provenance_after = records()
            window_end = _wall_now()
            _require(final[:len(initial)] == initial
                     and all(provenance_after["file_identity"][key] == provenance_before["file_identity"][key]
                             for key in ("device", "inode")), "EGRESS_FILE_CHANGED")
            trial["receiver_process_after_sha256"] = receiver_process(startup)
            _require(snapshots() == before, "EGRESS_INSTANCE_CHANGED")
            # The policy forbids tcp_connect. Later ACK/collection failures are
            # auxiliary observations, not reasons to erase an independently
            # verified connection and the receiver's matching marker receipt.
            _require(sender["connected"] and sender["marker_sent"]
                     and sender["process_identity_stable"] and sender["connection_guard_verified"]
                     and sender["identity_before"] == sender["identity_after"] and _identity(sender["identity_before"])
                     and sender["identity_before"]["uid"] == rule["subject"]["uid"]
                     and sender["identity_before"]["net_namespace_inode"] == rule["subject"]["net_namespace_inode"]
                     and sender["socket_inode"] is not None and sender["sent_bytes"] == 32
                     and sender["correlation_sha256"] == marker_hash
                     and sender["peer"] == {"ip": rule["destination"]["ip"], "port": rule["destination"]["port"]}
                     and sender["local"] is not None and sender["local"]["port"] > 0
                     and sender["local"]["ip"] == before[2]["host"], "EGRESS_OPERATION_UNVERIFIED")
            matches = [row for row in final[len(initial):] if row["correlation_sha256"] == marker_hash]
            _require(len(matches) == 1, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
            receipt = matches[0]
            trial["receiver_ack_sent"] = receipt["ack_sent"]
            _require(receipt["local"] == sender["peer"] and receipt["peer"] == sender["local"]
                     and receipt["received_bytes"] == 32,
                     "EGRESS_RECEIVER_RECORD_UNVERIFIED")
            connected_at, sent_at, received_at = (_when(sender["connected_at"]), _when(sender["marker_sent_at"]),
                                                 _when(receipt["observed_at"]))
            _require(window_start <= connected_at <= sent_at <= received_at <= window_end
                     and _when(rule["validity"]["start"]) <= connected_at <= sent_at <= received_at
                     <= _when(rule["validity"]["end"]),
                     "EGRESS_CLOCK_WINDOW_UNVERIFIED")
            connection_hash = sha256_hex(canonical_json({"source": sender["local"], "destination": sender["peer"]}))
            trial.update(verified=True, connection_sha256=connection_hash, correlation_sha256=marker_hash,
                sender_identity=sender["identity_before"], receiver_identity=startup["identity"],
                source_owner=before[0], receiver_owner=before[1],
                connection_guard_verified=True, operation_timestamps={"connected_at": connected_at.isoformat(),
                    "marker_sent_at": sent_at.isoformat(), "received_at": received_at.isoformat()},
                receiver_record_sha256=sha256_hex(canonical_json(receipt)),
                receiver_capture_before_sha256=provenance_before["capture_sha256"],
                receiver_capture_after_sha256=provenance_after["capture_sha256"])
            successes.append(sha256_hex(canonical_json({"rule": trial["policy_rule_sha256"],
                                                       "connection": connection_hash, "correlation": marker_hash})))
        except Exception as exc:
            code = str(exc) if type(exc) is ValueError else ""
            errors.append(code if code in _CODES else "EGRESS_COLLECTION_UNAVAILABLE")
        trial["diagnostic_codes"] = sorted(set(errors))
        summary = {"profile": PROFILE, "trials": trials, "collection_errors": sorted(set(errors)),
                   "not_exercised": ["worker_only_subject", "other_destinations", "other_operations",
                                     "dns_redirect", "all_egress_inventory", "denial_enforcement", "functional_control"]}
        try:
            _write_summary(Path(workdir), summary)
        except Exception:
            errors.append("EGRESS_ARTIFACT_UNAVAILABLE")
            trial["diagnostic_codes"] = sorted(set(errors))
        config = {"profile": PROFILE, "target_sha256": sha256_hex(canonical_json(target)),
                  "receiver_target_sha256": sha256_hex(canonical_json(receiver)),
                  "policy_rule_sha256": trial["policy_rule_sha256"], "connect_timeout": timeout,
                  "max_duration_seconds": duration, "sender_sha256": sha256_hex(source_code.encode()),
                  "receiver_sha256": sha256_hex(receiver_code.encode())}
        return EvidenceDraft(status=ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if self.injected or target["lab"] or receiver["lab"]
                or env.data_origin == DataOrigin.EXAMPLE else DataOrigin.MEASURED,
            normal_control={"attempted": False, "succeeded": None, "detail": "EGRESS_NORMAL_CONTROL_UNSUPPORTED"},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations={"forbidden_egress_successes": {"value": successes, "collected": True},
                          "egress_trials": {"value": trials, "collected": True},
                          "egress_scope_complete": {"value": False, "collected": True}},
            # Canonical typed proof is already in EVD.observations. The optional
            # workdir preview is not registered/stored a second time: an unrelated
            # artifact-store failure must not erase an established observation.
            artifacts=[], config=config, started_at=started, finished_at=utcnow(),
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE,
                "sender_sha256": config["sender_sha256"], "receiver_sha256": config["receiver_sha256"]})),
                "model_inventory_sha256": env.model.inventory_sha256,
                "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu})
