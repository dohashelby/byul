"""PLT-02의 제한 Unix SOCK_STREAM zero-payload 접근 관측.

비허용 UID의 커널 연결 근거만 독립 FAIL 후보로 남긴다. 명령 소비·동일 UID의
비허용 프로세스·SHM/TCP·전체 IPC inventory를 검증하지 않아 보안 PASS는 없다.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import secrets
import time
from types import SimpleNamespace

from guard.runtime.docker import Docker
from . import ipc_probe
from .http_common import HttpCheckExecutor, functional_requests, functional_results


PROFILE = "unix-ipc-access-v1"
VERSION = "0.1.0"
_PIN = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")
_NS = re.compile(r"(mnt|net|pid|user):\[[0-9]+\]")
_ID = re.compile(r"[0-9a-f]{64}")


def policy_shape(values):
    paths, allowed = values.get("protected_unix_paths"), values.get("allowed_ipc_uids")
    if (type(paths) is not list or not 1 <= len(paths) <= 8
            or any(type(p) is not str or not p.startswith("/") or len(p.encode()) > 100
                   or "\x00" in p or ".." in PurePosixPath(p).parts or "//" in p for p in paths)
            or len(set(paths)) != len(paths)):
        raise ValueError("protected_unix_paths requires 1..8 distinct absolute bounded paths")
    if (type(allowed) is not list or not allowed or len(allowed) > 16
            or any(type(u) is not str or not re.fullmatch(r"0|[1-9][0-9]{0,9}", u)
                   or int(u) > 2**31 - 1 for u in allowed)
            or len(set(allowed)) != len(allowed)):
        raise ValueError("allowed_ipc_uids requires explicit distinct numeric UID strings")


def _integers(value, keys):
    return type(value) is dict and set(value) == set(keys) and all(
        type(value[k]) is int and value[k] >= 0 for k in keys)


def _parse(result, mode, uid, count):
    try:
        if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 65536:
            return None
        def pairs(items):
            row = {}
            for key, value in items:
                if key in row:
                    raise ValueError("duplicate key")
                row[key] = value
            return row
        value = json.loads(result.stdout, object_pairs_hook=pairs)
        ns = value.get("namespaces")
        if (value.get("schema") != "guard-unix-ipc-probe/1" or value.get("mode") != mode
                or type(value.get("uid")) is not int or value["uid"] != uid
                or type(ns) is not dict or set(ns) != {"mnt", "net", "pid", "user"}
                or any(type(v) is not str or not _NS.fullmatch(v) or not v.startswith(k + ":")
                       for k, v in ns.items())
                or type(value.get("rows")) is not list or len(value["rows"]) != count):
            return None
        if mode == "connect" and (type(value.get("groups")) is not list
                or any(type(g) is not int or g != uid for g in value["groups"])):
            return None
        for index, row in enumerate(value["rows"]):
            if type(row) is not dict or type(row.get("index")) is not int or row["index"] != index:
                return None
            if mode == "inspect" and row.get("complete") is True:
                owners = row.get("owners")
                if (not _integers(row.get("identity"), ("device", "inode", "uid", "gid", "mode", "ctime_ns"))
                        or type(row.get("socket_inode")) is not str or not row["socket_inode"].isdigit()
                        or type(owners) is not list or not 1 <= len(owners) <= 16
                        or any(type(owner) is not dict or set(owner) != {"pid", "start", "uid"}
                               or type(owner["pid"]) is not int or owner["pid"] <= 0
                               or type(owner["uid"]) is not int or owner["uid"] < 0
                               or type(owner["start"]) is not str or not owner["start"].isdigit()
                               for owner in owners)):
                    return None
            if mode == "connect" and row.get("outcome") == "connected":
                if (not _integers(row.get("identity"), ("device", "inode", "uid", "gid", "mode", "ctime_ns"))
                        or not _integers(row.get("peer"), ("pid", "uid", "gid"))
                        or row["peer"]["pid"] <= 0 or type(row.get("path_stable")) is not bool):
                    return None
        return value
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        return None


class UnixIPCExecutor(HttpCheckExecutor):
    name = "unix_ipc"
    version = VERSION
    requires_roles = False
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg, *args, docker=None, **kwargs):
        super().__init__(cfg, *args, **kwargs)
        self.docker = docker or Docker("ipc" + secrets.token_hex(4))

    def run(self, check, env, policy_values, adapter, workdir):
        # Unlike the generic HTTP preflight, this check must establish ownership before /health.
        self.validate_policy(check, policy_values)
        target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
        if self._identity(SimpleNamespace(target=target, env=env), time.monotonic() + 5) is None:
            raise ValueError("IPC_TARGET_OWNERSHIP_UNVERIFIED")
        return super().run(check, env, policy_values, adapter, workdir)

    def validate_policy(self, check, values):
        policy_shape(values)
        uids = self.cfg.get("probe_uids")
        if (type(uids) is not list or not 2 <= len(uids) <= 4
                or any(type(u) is not int or not 0 <= u <= 2**31 - 1 for u in uids)
                or len(set(uids)) != len(uids)
                or not any(str(u) in values["allowed_ipc_uids"] for u in uids)
                or not any(str(u) not in values["allowed_ipc_uids"] for u in uids)):
            raise ValueError("probe_uids needs explicit allowed and disallowed numeric actors")
        for key, default, maximum in (("connect_timeout", 0.5, 2), ("max_duration_seconds", 60, 300)):
            value = self.cfg.get(key, default)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError("invalid bounded IPC budget")
        if len(check.functional_probes) != 1:
            raise ValueError("one bounded engine functional control is required")
        probe = check.functional_probes[0]
        if (probe.method != "POST" or probe.path != "/v1/completions" or probe.via != "engine"
                or probe.expect != [200] or probe.body != {"model": "{model}", "prompt": "ping", "max_tokens": 1}
                or type(probe.body.get("max_tokens")) is not int):
            raise ValueError("only the fixed bounded completion control is supported")

    def _identity(self, ctx, deadline):
        try:
            if deadline <= time.monotonic():
                return None
            engine = ctx.target["engine"]
            value = self.docker.inspect_json(engine["container"], timeout=min(5, max(0.01, deadline - time.monotonic())))
            cfg, state, host = value["Config"], value["State"], value.get("HostConfig", {})
            mounts = value.get("Mounts", [])
            if (type(mounts) is not list or any(type(m) is not dict
                    or type(m.get("Destination")) is not str or m["Destination"] == "/"
                    or m["Destination"] == "/proc" or m["Destination"].startswith("/proc/") for m in mounts)):
                return None
            if (not ctx.target.get("run_id") or cfg.get("Labels", {}).get("guard.run") != ctx.target["run_id"]
                    or type(value.get("Id")) is not str or not _ID.fullmatch(value["Id"])
                    or (value.get("Name") != "/" + engine["container"] and value["Id"] != engine["container"])
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", value.get("Image", ""))
                    or not _PIN.fullmatch(ctx.env.engine.image_digest or "")
                    or cfg.get("Image") != ctx.env.engine.image_digest
                    or engine.get("image_digest") != ctx.env.engine.image_digest
                    or state.get("Running") is not True or type(state.get("StartedAt")) is not str
                    or not state["StartedAt"] or host.get("PidMode") not in (None, "")):
                return None
            return {"container_id": value["Id"], "image_id": value["Image"],
                    "started_at": state["StartedAt"], "image": cfg["Image"]}
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, AttributeError):
            return None

    def collect(self, ctx):
        paths = ctx.policy_values["protected_unix_paths"]
        uids = self.cfg["probe_uids"]
        script = Path(ipc_probe.__file__).read_text(encoding="utf-8")
        script_sha = hashlib.sha256(script.encode()).hexdigest()
        deadline = time.monotonic() + self.cfg.get("max_duration_seconds", 60)
        errors, trials, violations = [], [], []
        connected_controls = set()
        control_attempted = False
        before = self._identity(ctx, deadline)

        def capture(mode, uid):
            nonlocal control_attempted
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                if mode == "connect" and str(uid) in ctx.policy_values["allowed_ipc_uids"]:
                    control_attempted = True
                result = self.docker.exec_as_uid(ctx.target["engine"]["container"], uid,
                    ["python3", "-c", script, mode, json.dumps(paths), "--timeout",
                     str(self.cfg.get("connect_timeout", 0.5))], timeout=min(15, remaining))
                return _parse(result, mode, uid, len(paths))
            except (OSError, ValueError, RuntimeError):
                return None

        if before is not None:
            for uid in uids:
                prior = capture("inspect", 0)
                attempt = capture("connect", uid) if prior is not None else None
                after = capture("inspect", 0) if attempt is not None else None
                identity = self._identity(ctx, deadline)
                if not prior or not attempt or not after or identity != before:
                    errors.append("IPC_SOURCE_OR_IDENTITY_UNVERIFIED")
                    continue
                if prior["namespaces"] != attempt["namespaces"] or prior["namespaces"] != after["namespaces"]:
                    errors.append("IPC_NAMESPACE_MISMATCH")
                    continue
                for index, (p, trial, a) in enumerate(zip(prior["rows"], attempt["rows"], after["rows"])):
                    stable = p.get("complete") is True and a == p
                    peer = trial.get("peer", {})
                    verified = (stable and trial.get("path_stable") is True
                                and trial.get("identity") == p.get("identity")
                                and any(owner["pid"] == peer.get("pid") and owner["uid"] == peer.get("uid")
                                        for owner in p.get("owners", [])))
                    key = hashlib.sha256(paths[index].encode()).hexdigest()
                    connected = verified and trial.get("outcome") == "connected"
                    allowed = str(uid) in ctx.policy_values["allowed_ipc_uids"]
                    trials.append({"path_sha256": key, "actor_uid": uid, "allowed": allowed,
                                   "outcome": trial.get("outcome", "unknown"), "connection_verified": connected})
                    if connected and not allowed:
                        violations.append(f"{key}:uid={uid}")
                    if connected and allowed:
                        connected_controls.add(index)
                    if not stable:
                        errors.append("IPC_ENDPOINT_IDENTITY_UNVERIFIED")
        else:
            errors.append("IPC_TARGET_OWNERSHIP_UNVERIFIED")
        # Independent connection violations above survive later functional/control errors.
        normal_ok, planned, functional = False, [], {}
        try:
            if before is not None and deadline > time.monotonic():
                requests, probes = functional_requests(ctx, via="engine")
                planned = list(probes)
                control_attempted = bool(requests) or control_attempted
                responses = ctx.cprobe(ctx.trusted_net, requests, min(ctx.timeout, deadline - time.monotonic()))
                functional, _, _ = functional_results(probes, dict(zip(probes, responses)), ctx.env.model.id)
                normal_ok = bool(len(connected_controls) == len(paths)
                                 and functional and all(v == "pass" for v in functional.values())
                                 and self._identity(ctx, deadline) == before)
        except Exception:
            errors.append("IPC_NORMAL_CONTROL_UNVERIFIED")
        observations = {
            "unix_ipc_violations": {"value": sorted(set(violations)), "collected": True},
            "unix_ipc_trials": {"value": trials, "collected": bool(trials)},
            "ipc_scope_complete": {"value": False, "collected": True},
            "functional_probes_planned": {"value": planned, "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(functional)},
        }
        return observations, {"attempted": control_attempted, "succeeded": normal_ok,
                "detail": "Unix UID connection control and normal engine request; full IPC scope unverified"}, [
            {"step": step, "attempted": False} for step in ctx.check.challenge_steps
        ], {"profile": PROFILE, "trials": trials, "identity_before": before,
            "collection_errors": sorted(set(errors)), "not_exercised": ["shared_memory", "tcp_ipc",
                "same_uid_unapproved_process", "command_consumption", "all_ipc_inventory"],
            "fixture": {"profile": PROFILE, "probe_sha256": script_sha}}

    def preview_reuse_inputs(self, *args):
        return None  # kernel endpoint/process identities are discovered at execution time
