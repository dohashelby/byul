"""container_config 실행기: 엔진 컨테이너의 실제 실행 권한 수집 (PLT-01). 판정하지 않는다(INV-14).

엔진에 HTTP 관리 요청을 보내지 않는다. 수집은 읽기 전용:
1. `docker inspect <container>` → 특권 모드, 장치, 마운트(목적지:모드), 호스트 namespace 공유, 사용자, 보안 옵션
2. 컨테이너 안 **모든 프로세스**의 `/proc/<pid>/status`(Name·Uid·CapEff)를 읽어 유효 capability 합집합과 UID 집합
   (PID 1이 엔진 본체가 아닐 수 있고 worker는 자식 프로세스이므로)
3. sentinel 쓰기 시도: 설정한 경로마다 작은 파일을 쓰고 성공하면 즉시 지운다. 마운트 목적지(호스트 자원)인 경로만
   판정 관측(sentinel_results)에 넣고, 컨테이너 자체 파일시스템("/")은 증거 파일에만 남긴다.
   쓰기 가능 + 실행 가능 마운트: `/proc/1/mounts`에서 rw·noexec 아님인 실제 파일시스템을 후보로 잡고 같은 쓰기 시도로
   확인한 목록(exec_writable_mounts). 실행 가능한 쓰기 영역은 그 자체가 권한이다.
4. 정상 대조: CheckDef.functional_probes(via: engine) — 신뢰 출발점에서 엔진으로 추론 1회

설정 (guard.toml [container_config]):
    target = "path/to/target.json"   # 신뢰 출발점·엔진 URL (lab)
    container = "<컨테이너 이름>"      # 대상 컨테이너(없으면 target.engine.container). 실험실 밖 환경에도 그대로 쓴다
    sentinel_paths = ["/models", "/", "/guard-sentinel"]
"""

from __future__ import annotations

import re
import secrets
from typing import Any

from guard.models import canonical_json, sha256_hex
from .http_common import Ctx, HttpCheckExecutor, functional_requests, functional_results
from .owned_capture import engine_identity
from .running_image_approval import (PROFILE as SUPPLY_IMAGE_PROFILE,
                                     collect_running_image_approval, validate_image_approval_policy)

# Linux capability 번호 → 이름 (include/uapi/linux/capability.h)
CAP_NAMES = [
    "CHOWN", "DAC_OVERRIDE", "DAC_READ_SEARCH", "FOWNER", "FSETID", "KILL", "SETGID", "SETUID", "SETPCAP",
    "LINUX_IMMUTABLE", "NET_BIND_SERVICE", "NET_BROADCAST", "NET_ADMIN", "NET_RAW", "IPC_LOCK", "IPC_OWNER",
    "SYS_MODULE", "SYS_RAWIO", "SYS_CHROOT", "SYS_PTRACE", "SYS_PACCT", "SYS_ADMIN", "SYS_BOOT", "SYS_NICE",
    "SYS_RESOURCE", "SYS_TIME", "SYS_TTY_CONFIG", "MKNOD", "LEASE", "AUDIT_WRITE", "AUDIT_CONTROL", "SETFCAP",
    "MAC_OVERRIDE", "MAC_ADMIN", "SYSLOG", "WAKE_ALARM", "BLOCK_SUSPEND", "AUDIT_READ", "PERFMON", "BPF",
    "CHECKPOINT_RESTORE",
]
HOST_NAMESPACE_KEYS = (("pid", "PidMode"), ("ipc", "IpcMode"), ("net", "NetworkMode"), ("uts", "UTSMode"))
DEFAULT_SENTINELS = ["/models", "/", "/guard-sentinel"]

# 읽기 전용: 모든 프로세스의 status에서 Name·Uid·CapEff만
PROC_CMD = ('for d in /proc/[0-9]*; do echo "PID ${d#/proc/}"; '
            'grep -E "^(Name|Uid|CapEff):" "$d/status" 2>/dev/null || echo GUARD_READ_ERROR; done')
# 마운트 표(읽기 전용). 쓰기 가능(rw)이고 noexec가 아닌 마운트를 후보로, 실제 쓰기 시도로 확인한다
MOUNTS_CMD = "cat /proc/1/mounts"
PSEUDO_FS = frozenset({"proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "securityfs", "debugfs", "tracefs",
                       "pstore", "bpf", "configfs", "fusectl", "binfmt_misc", "hugetlbfs", "autofs", "nsfs"})
# sentinel: 디렉토리가 없으면 absent, 쓰기 성공이면 즉시 지우고 succeeded, 실패면 denied
SENTINEL_CMD = ('p="$1/.guard-sentinel-$2"; if [ ! -d "$1" ]; then echo absent; '
                'elif (echo guard > "$p") 2>/dev/null; then rm -f "$p"; echo succeeded; else echo denied; fi')


def decode_caps(hexstr: str) -> list[str]:
    value = int(hexstr, 16)
    names = [CAP_NAMES[i] if i < len(CAP_NAMES) else f"CAP_{i}" for i in range(64) if value >> i & 1]
    return sorted(names)


def parse_proc_status(text: str) -> list[dict]:
    procs: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        if line.startswith("PID"):
            # A broken header must end the preceding process record. Its
            # following fields cannot be attributed to the last valid PID.
            value = line[4:].strip()
            cur = {"pid": int(value)} if line.startswith("PID ") and re.fullmatch(r"[1-9][0-9]{0,9}", value) else {"read_error": True}
            if "pid" in cur and cur["pid"] > 2147483647:
                cur = {"read_error": True}
            if any(p.get("pid") == cur.get("pid") for p in procs) and "pid" in cur:
                cur["read_error"] = True
            procs.append(cur)
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if key == "Name":
                if "name" in cur:
                    cur["read_error"] = True
                if "pid" in cur:
                    cur["name"] = val
            elif key == "Uid":
                fields = val.split()
                if "uid_effective" in cur or len(fields) != 4 or any(not re.fullmatch(r"[0-9]{1,10}", v) for v in fields):
                    cur["read_error"] = True
                elif any(int(v) > 4294967295 for v in fields):
                    cur["read_error"] = True
                elif "pid" in cur:
                    cur["uid_effective"] = int(fields[1])
            elif key == "CapEff":
                if "caps" in cur or not re.fullmatch(r"[0-9A-Fa-f]{1,16}", val):
                    cur["read_error"] = True
                elif "pid" in cur:
                    cur["caps"] = decode_caps(val)
        elif cur is not None and line == "GUARD_READ_ERROR":
            cur["read_error"] = True
        elif line.strip():
            if cur is None:
                cur = {"read_error": True}
                procs.append(cur)
            else:
                cur["read_error"] = True
    return procs


def exec_candidates(mounts_text: str) -> list[dict]:
    """/proc/<pid>/mounts → 쓰기 가능(rw)·실행 가능(noexec 아님)한 실제 파일시스템 마운트 후보."""
    out = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) != 6 or not parts[1].startswith("/"):
            continue
        _, target, fstype, opts = parts[:4]
        flags = set(opts.split(","))
        if fstype in PSEUDO_FS or "rw" not in flags or "noexec" in flags:
            continue
        out.append({"target": target.replace("\\040", " "), "fstype": fstype, "options": opts})
    return out


def _mount_key(m: dict) -> str:
    return f"{m.get('Destination')}:{'rw' if m.get('RW') else 'ro'}"


class ContainerConfigExecutor(HttpCheckExecutor):
    name = "container_config"
    version = "0.6.2"
    requires_roles = False
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg: dict, container_probe=None, host_probe=None, environ=None, docker=None):
        kw = {} if host_probe is None else {"host_probe": host_probe}
        super().__init__(cfg, container_probe=container_probe, environ=environ, **kw)
        self._docker = docker  # inspect_json(container, timeout=), exec(container, argv, timeout=)

    def validate_policy(self, check, values):
        profile = getattr(check, "collection_profile", None)
        if profile == SUPPLY_IMAGE_PROFILE:
            validate_image_approval_policy(values)

    def _docker_obj(self):
        if self._docker is None:
            from guard.runtime.docker import Docker

            self._docker = Docker(f"c{secrets.token_hex(4)}")  # inspect·exec만 쓴다(컨테이너를 만들지 않음)
        return self._docker

    def collect(self, ctx: Ctx):
        if getattr(ctx.check, "collection_profile", None) == SUPPLY_IMAGE_PROFILE:
            return collect_running_image_approval(self._docker_obj(), ctx, self.cfg.get("container"))
        completeness_gate = any(obs.key == "permission_scan_complete" for obs in ctx.check.required_observations)
        container = self.cfg.get("container") or ctx.target.get("engine", {}).get("container")
        if type(container) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
            raise ValueError("CONTAINER_TARGET_UNVERIFIED")
        docker = self._docker_obj()
        anchor, intervals = None, {}
        source_unverified = False
        source_container = container
        try:
            image = ctx.env.engine.image_digest
            anchor = engine_identity(docker, ctx.target, ctx.env, container, image, 30)
            if not anchor["started_at"]:
                raise ValueError("missing source generation")
        except Exception:
            raise ValueError("CONTAINER_TARGET_UNVERIFIED") from None
        source_container = anchor["container_id"]

        def check_owner():
            nonlocal source_unverified
            try:
                current = engine_identity(docker, ctx.target, ctx.env, container, image, 5)
                if current != anchor:
                    raise ValueError("changed generation")
            except Exception:
                source_unverified = True
                raise ValueError("CONTAINER_SOURCE_UNVERIFIED") from None

        def bound(name, action):
            try:
                check_owner()
                result = action()
                check_owner()
            except Exception:
                intervals[name] = False
                raise
            intervals.setdefault(name, True)
            return result

        def inventory():
            row = docker.inspect_json(source_container, timeout=30)
            if type(row) is not dict:
                raise ValueError("invalid inspect envelope")
            state, config = row.get("State"), row.get("Config")
            if (type(state) is not dict or type(config) is not dict
                    or {"container_id": row.get("Id"), "image_id": row.get("Image"),
                        "started_at": state.get("StartedAt"), "image": config.get("Image")} != anchor):
                raise ValueError("CONTAINER_SOURCE_UNVERIFIED")
            return row

        # Each permission source is independent. A later read failure must not
        # replace an already observed permission with an empty ABORTED record.
        errors, ins = [], {}
        try:
            ins = bound("inventory", inventory)
        except Exception:
            errors.append("CONTAINER_INSPECTION_UNAVAILABLE")
        hc = ins.get("HostConfig") if type(ins.get("HostConfig")) is dict else {}
        mounts_raw = ins.get("Mounts")
        mounts_all = [m for m in mounts_raw if type(m) is dict
                      and type(m.get("Type")) is str and m["Type"] in {"bind", "volume", "tmpfs"}
                      and type(m.get("Destination")) is str
                      and m["Destination"].startswith("/") and ".." not in m["Destination"].split("/")
                      and (m["Type"] not in {"bind", "volume"} or type(m.get("RW")) is bool)] \
            if type(mounts_raw) is list else []
        mounts_complete = type(mounts_raw) is list and len(mounts_all) == len(mounts_raw)
        host_mounts = [m for m in mounts_all if m["Type"] in ("bind", "volume")]
        mounts = sorted(_mount_key(m) for m in host_mounts)
        devices_raw = hc.get("Devices")
        device_rows = [d for d in devices_raw if type(d) is dict
                       and all(type(d.get(key)) is str and d[key].startswith("/")
                               for key in ("PathOnHost", "PathInContainer"))
                       and type(d.get("CgroupPermissions")) is str
                       and re.fullmatch(r"[rwm]+", d["CgroupPermissions"])] \
            if type(devices_raw) is list else []
        devices_complete = "Devices" in hc and (devices_raw is None or
                           type(devices_raw) is list and len(device_rows) == len(devices_raw))
        devices = sorted(f"{d['PathOnHost']}:{d['PathInContainer']}:{d['CgroupPermissions']}" for d in device_rows)
        privileged = hc.get("Privileged") if type(hc.get("Privileged")) is bool else None
        namespaces_complete = all(type(hc.get(key)) is str for _, key in HOST_NAMESPACE_KEYS)
        host_ns = sorted(ns for ns, key in HOST_NAMESPACE_KEYS if hc.get(key) == "host")
        inspect_complete = mounts_complete and devices_complete and privileged is not None and namespaces_complete
        if not inspect_complete:
            errors.append("CONTAINER_PERMISSION_INVENTORY_INCOMPLETE")
        raw_config = ins.get("Config") if type(ins.get("Config")) is dict else {}
        def string_list(field):
            value = hc.get(field)
            return value or [] if value is None or type(value) is list and all(type(v) is str for v in value) else None

        tmpfs = hc.get("Tmpfs")
        config = {
            "user": raw_config.get("User") if type(raw_config.get("User")) is str else None,
            "readonly_rootfs": hc.get("ReadonlyRootfs") if type(hc.get("ReadonlyRootfs")) is bool else None,
            "cap_add": string_list("CapAdd"), "cap_drop": string_list("CapDrop"),
            "security_opt": string_list("SecurityOpt"),
            "tmpfs": [] if tmpfs is None else sorted(tmpfs) if type(tmpfs) is dict and all(type(k) is str for k in tmpfs) else None,
            "image": raw_config.get("Image") if type(raw_config.get("Image")) is str else None,
            # Config.Env는 기록하지 않는다(비밀이 들어 있을 수 있음)
        }

        # 2. 모든 프로세스의 유효 capability·UID
        procs, proc_rc = [], None
        try:
            proc = bound("process", lambda: docker.exec(source_container, ["sh", "-c", PROC_CMD], timeout=30))
            proc_rc = proc.returncode
            if type(proc.stdout) is not str:
                raise ValueError("invalid process output")
            procs = parse_proc_status(proc.stdout)
        except Exception:
            errors.append("PROCESS_PERMISSION_SCAN_UNAVAILABLE")
        caps = sorted({cap for p in procs for cap in p.get("caps", [])})
        uids = sorted({p["uid_effective"] for p in procs if "uid_effective" in p})
        complete = proc_rc == 0 and bool(procs) and all("pid" in p and "caps" in p and "uid_effective" in p
                                                     and not p.get("read_error") for p in procs)
        if not complete:
            errors.append("PROCESS_PERMISSION_SCAN_INCOMPLETE")

        def write_probe(path, source="sentinel"):
            try:
                result = bound(source, lambda: docker.exec(source_container,
                    ["sh", "-c", SENTINEL_CMD, "_", path, secrets.token_hex(4)], timeout=30))
                value = result.stdout.strip()
                if result.returncode == 0 and value in {"absent", "succeeded", "denied"}:
                    return value
            except Exception:
                pass
            errors.append("PERMISSION_SENTINEL_UNAVAILABLE")
            return "error"

        # 3. sentinel 쓰기 시도
        mount_dests = {m.get("Destination") for m in host_mounts}
        writes: dict[str, str] = {}
        for path in dict.fromkeys(self.cfg.get("sentinel_paths", DEFAULT_SENTINELS)):
            writes[path] = write_probe(path)
        sentinel_results = {p: o for p, o in writes.items() if p in mount_dests or o == "absent"}
        container_fs_writes = {p: o for p, o in writes.items() if p not in sentinel_results}

        # 3-b. 쓰기 가능 + 실행 가능 마운트(그 자체가 권한): 마운트 표의 후보마다 실제 쓰기 시도
        mount_text, mount_rc = "", None
        try:
            mt = bound("mount_table", lambda: docker.exec(source_container, ["sh", "-c", MOUNTS_CMD], timeout=30))
            mount_rc = mt.returncode
            if type(mt.stdout) is not str:
                raise ValueError("invalid mount output")
            mount_text = mt.stdout
        except Exception:
            errors.append("PROCESS_MOUNT_SCAN_UNAVAILABLE")
        mount_lines = [line.split() for line in mount_text.splitlines() if line.strip()]
        process_mounts_complete = mount_rc == 0 and bool(mount_lines) and all(
            len(line) == 6 and line[1].startswith("/") for line in mount_lines)
        if not process_mounts_complete:
            errors.append("PROCESS_MOUNT_SCAN_INCOMPLETE")
        candidates = exec_candidates(mount_text)
        exec_checks: dict[str, str] = {}
        for c in candidates:
            if c["target"] not in exec_checks:
                exec_checks[c["target"]] = write_probe(c["target"], "exec_sentinel")
        exec_writable = sorted(t for t, o in exec_checks.items() if o == "succeeded")
        permission_complete = (inspect_complete and complete and process_mounts_complete
                               and all(value != "error" for value in (*writes.values(), *exec_checks.values())))
        # Older saved definitions retain their original complete-list contract.
        # The new definition has an explicit whole-permission PASS gate so known
        # partial values can remain collected without manufacturing a PASS.

        # 4. 정상 대조(신뢰 출발점 → 엔진)
        probes = {fp.id: fp for fp in ctx.check.functional_probes}
        func_raw = {}
        functional_attempted = False
        try:
            reqs, probes = functional_requests(ctx, via="engine")
            def normal_probe():
                nonlocal functional_attempted
                functional_attempted = bool(reqs)
                return ctx.cprobe(ctx.trusted_net, reqs, max(ctx.timeout, 30.0)) if reqs else []
            res = bound("normal_control", normal_probe)
            if (type(res) is not list or len(res) != len(reqs) or any(type(r) is not dict for r in res)
                    or [r.get("id") for r in res] != [r["id"] for r in reqs]):
                raise ValueError("incomplete functional response correlation")
            for (pid, fp), r in zip(probes.items(), res):
                func_raw[pid] = r
                ctx.record("trusted", "functional-engine", pid, fp.as_role, r)
        except Exception:
            errors.append("CONTAINER_FUNCTIONAL_PROBE_UNAVAILABLE")
        functional, functional_status, normal = functional_results(probes, func_raw, ctx.env.model.id)
        normal["attempted"] = functional_attempted
        if source_unverified:
            permission_complete = False
        observations = {
            "process_scan_complete": {"value": complete, "collected": True},
            "capabilities_effective": {"value": caps, "collected": any("caps" in p for p in procs)},
            "mounts": {"value": mounts, "collected": mounts_complete or completeness_gate and bool(host_mounts)},
            "devices": {"value": devices, "collected": devices_complete or completeness_gate and bool(device_rows)},
            "privileged": {"value": privileged, "collected": privileged is not None},
            "host_namespaces": {"value": host_ns, "collected": namespaces_complete or completeness_gate and bool(host_ns)},
            "root_uid_present": {"value": 0 in uids, "collected": 0 in uids or complete},
            "sentinel_results": {"value": sentinel_results,
                                 "collected": mounts_complete and all(v != "error" for v in sentinel_results.values())
                                 or completeness_gate and bool(sentinel_results)},
            "exec_writable_mounts": {"value": exec_writable,
                                     "collected": process_mounts_complete and all(v != "error" for v in exec_checks.values())
                                     or completeness_gate and bool(exec_writable)},
            "functional_probes_planned": {"value": list(probes), "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(probes)},
        }
        if completeness_gate:
            observations["permission_scan_complete"] = {"value": permission_complete, "collected": True}
        steps = ctx.check.challenge_steps
        challenges = [
            {"step": steps[0], "attempted": True,
             "observations": {"capabilities": caps, "uids": uids, "mounts": mounts, "devices": devices,
                              "privileged": privileged, "host_namespaces": host_ns, "processes": len(procs)}},
            {"step": steps[1], "attempted": True, "observations": {"sentinel": writes, "exec_write_checks": exec_checks}},
            {"step": steps[2], "attempted": bool(probes), "observations": {"functional": functional_status}},
        ] if len(steps) == 3 else [{"step": s, "attempted": True} for s in steps]
        summary = {
            "container": container, "config": config, "privileged": privileged, "host_namespaces": host_ns,
            "mounts": mounts, "all_mounts": sorted(f"{m.get('Type')}:{_mount_key(m)}" for m in mounts_all),
            "devices": devices, "capabilities_effective": caps, "uids": uids,
            "processes": [{k: p.get(k) for k in ("pid", "name", "uid_effective", "caps")} for p in procs],
            "proc_read_rc": proc_rc, "sentinel_results": sentinel_results,
            "container_fs_writes": container_fs_writes,
            "exec_candidates": candidates, "exec_write_checks": exec_checks,
            "functional_probe_results": functional, "functional_probe_status": functional_status,
            "permission_scan_complete": permission_complete, "collection_errors": sorted(set(errors)),
            "fixture": {"sentinel_paths": list(writes), "proc_cmd": PROC_CMD, "sentinel_cmd": SENTINEL_CMD},
        }
        summary["source_binding"] = {"profile": "docker-engine-source-intervals/1",
            "environment_ref": ctx.env.ref().model_dump(mode="json"),
            "owner_sha256": sha256_hex(canonical_json(anchor)), "intervals_verified": intervals}
        return observations, normal, challenges, summary
