"""PLT-09: 기록 전송 구간별 TLS 핸드셰이크와 잘못된 상대 거부를 소유 컨테이너에서 관측한다.

자격 원문은 쓰지 않는다. 연결 시험은 핸드셰이크까지이며 기록을 보내지 않는다.
실제 송신 도구의 동작과 sink 수신은 이 수집으로 확인되지 않는다.
"""
from __future__ import annotations

import re

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.docker import Docker
from .base import EvidenceDraft
from .owned_helper import run_helper

PROFILE = "record-transport-segments-v1"
VERSION = "0.2.0"
_NAME = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_PROBE = r'''
import json,socket,ssl,sys
host,port,name,wrong,ca,timeout=sys.argv[1:7];port=int(port);timeout=float(timeout)
def attempt(server):
 sock=socket.create_connection((host,port),timeout)
 try:
  context=ssl.create_default_context(cafile=ca or None)
  with context.wrap_socket(sock,server_hostname=server) as tls:
   return dict(tcp=True,tls=True,version=tls.version())
 except ssl.SSLCertVerificationError:return dict(tcp=True,tls=False,reason='peer_rejected')
 except ssl.SSLError:return dict(tcp=True,tls=False,reason='no_tls')
 finally:sock.close()
def safe(server):
 try:return attempt(server)
 except OSError:return dict(tcp=False,tls=False,reason='unreachable')
print(json.dumps(dict(expected=safe(name),wrong=safe(wrong)),sort_keys=True,separators=(',',':')))
'''


def validate_policy(values):
    names = values.get("record_transport_segments") if type(values) is dict else None
    required = values.get("record_tls_required_segments") if type(values) is dict else None
    if (type(names) is not list or not 1 <= len(names) <= 8 or len(set(map(str, names))) != len(names)
            or any(type(n) is not str or not _NAME.fullmatch(n) for n in names)):
        raise ValueError("RECORD_TRANSPORT_POLICY_INVALID")
    if type(required) is not list or any(type(n) is not str or n not in names for n in required):
        raise ValueError("RECORD_TRANSPORT_POLICY_INVALID")


def _segments(cfg, names):
    rows = cfg.get("record_segments")
    if type(rows) is not list or {row.get("name") if type(row) is dict else None for row in rows} != set(names):
        raise ValueError("RECORD_TRANSPORT_CONFIGURATION_INVALID")
    for row in rows:
        if (set(row) - {"name", "container", "host", "port", "server_name", "wrong_server_name", "ca_file"}
                or any(type(row.get(k)) is not str or not row[k] for k in ("container", "host", "server_name", "wrong_server_name"))
                or type(row.get("port")) is not int or not 1 <= row["port"] <= 65535
                or row["server_name"] == row["wrong_server_name"]):
            raise ValueError("RECORD_TRANSPORT_CONFIGURATION_INVALID")
    return rows


def classify(row, reply, required):
    expected, wrong = reply["expected"], reply["wrong"]
    state = "unreachable" if not expected["tcp"] else "protected" if expected["tls"] else expected["reason"]
    return {"name": row["name"], "state": state, "tls_required": required,
            "tls_version": expected.get("version"), "wrong_peer_state": "accepted" if wrong["tls"] else wrong.get("reason"),
            "endpoint_sha256": sha256_hex(canonical_json([row["host"], row["port"], row["server_name"]]))}


def project(rows):
    unprotected = [r["name"] for r in rows if r["tls_required"] and r["state"] in {"no_tls", "peer_rejected"}]
    wrong = [r["name"] for r in rows if r["tls_required"] and r["wrong_peer_state"] == "accepted"]
    unknown = [r["name"] for r in rows if r["state"] == "unreachable"]
    return unprotected, wrong, unknown


def run(check, env, values, cfg, *, docker=None):
    started = utcnow()
    validate_policy(values)
    names = values["record_transport_segments"]
    segments = _segments(cfg, names)
    timeout = cfg.get("connect_timeout", 3)
    if type(timeout) not in (int, float) or not 0 < timeout <= 10:
        raise ValueError("RECORD_TRANSPORT_CONFIGURATION_INVALID")
    docker = docker or Docker("rectls" + sha256_hex(started.isoformat().encode())[:8])
    rows, diagnostics = [], []
    for row in segments:
        try:
            reply = run_helper(docker, row["container"], _PROBE,
                [row["host"], row["port"], row["server_name"], row["wrong_server_name"], row.get("ca_file", ""), timeout],
                timeout=4 * timeout + 5, max_bytes=4096)
            rows.append(classify(row, reply, row["name"] in values["record_tls_required_segments"]))
        except Exception:
            diagnostics.append("RECORD_TRANSPORT_SEGMENT_UNAVAILABLE")
    unprotected, wrong, unknown = project(rows)
    observations = {
        "record_transport_trials": {"value": rows, "collected": True},
        "record_transport_segments_planned": {"value": names, "collected": True},
        "record_transport_segments_tested": {"value": [r["name"] for r in rows if r["state"] != "unreachable"], "collected": True},
        "record_transport_unprotected": {"value": unprotected, "collected": True},
        "record_transport_wrong_peer_accepted": {"value": wrong, "collected": True},
        "record_transport_unreachable": {"value": unknown, "collected": True},
        "record_transport_sender_verified": {"value": False, "collected": True}}
    return EvidenceDraft(
        status=ExecStatus.ABORTED if diagnostics else ExecStatus.COMPLETED,
        data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
        normal_control={"attempted": bool(rows), "succeeded": None,
                        "detail": "핸드셰이크 대조만 수행했다. 승인 기록의 실제 전달은 확인하지 않았다."},
        challenges=[{"step": step, "attempted": bool(rows)} for step in check.challenge_steps],
        observations=observations, config={"profile": PROFILE, "segments_sha256": sha256_hex(canonical_json(segments))},
        started_at=started, finished_at=utcnow(), abort_reason="; ".join(sorted(set(diagnostics))) or None)
