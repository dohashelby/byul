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

from .http_common import Ctx, HttpCheckExecutor, functional_requests, functional_results

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
        if line.startswith("PID "):
            cur = {"pid": int(line[4:].strip())}
            procs.append(cur)
        elif cur is not None and ":" in line:
            key, _, val = line.partition(":")
            val = val.strip()
            if key == "Name":
                cur["name"] = val
            elif key == "Uid":
                try:
                    cur["uid_effective"] = int(val.split()[1])
                except (ValueError, IndexError):
                    cur["read_error"] = True
            elif key == "CapEff":
                try:
                    cur["caps"] = decode_caps(val)
                except ValueError:
                    cur["read_error"] = True
        elif cur is not None and line == "GUARD_READ_ERROR":
            cur["read_error"] = True
    return procs


def exec_candidates(mounts_text: str) -> list[dict]:
    """/proc/<pid>/mounts → 쓰기 가능(rw)·실행 가능(noexec 아님)한 실제 파일시스템 마운트 후보."""
    out = []
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 4:
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
    version = "0.3.0"
    requires_roles = False
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg: dict, container_probe=None, host_probe=None, environ=None, docker=None):
        kw = {} if host_probe is None else {"host_probe": host_probe}
        super().__init__(cfg, container_probe=container_probe, environ=environ, **kw)
        self._docker = docker  # inspect_json(container, timeout=), exec(container, argv, timeout=)

    def _docker_obj(self):
        if self._docker is None:
            from guard.runtime.docker import Docker

            self._docker = Docker(f"c{secrets.token_hex(4)}")  # inspect·exec만 쓴다(컨테이너를 만들지 않음)
        return self._docker

    def collect(self, ctx: Ctx):
        container = self.cfg.get("container") or ctx.target.get("engine", {}).get("container")
        if not container:
            raise RuntimeError("container_config: no target container (set [container_config].container)")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", container):
            raise RuntimeError(f"container_config: invalid container name {container!r}")
        docker = self._docker_obj()

        # 1. 실행 구성
        ins = docker.inspect_json(container, timeout=30)
        hc = ins.get("HostConfig") or {}
        mounts_all = ins.get("Mounts") or []
        host_mounts = [m for m in mounts_all if m.get("Type") in ("bind", "volume")]
        mounts = sorted(_mount_key(m) for m in host_mounts)
        devices = sorted(f"{d.get('PathOnHost')}:{d.get('PathInContainer')}:{d.get('CgroupPermissions')}"
                         for d in hc.get("Devices") or [])
        privileged = bool(hc.get("Privileged"))
        host_ns = sorted(ns for ns, key in HOST_NAMESPACE_KEYS if hc.get(key) == "host")
        config = {
            "user": (ins.get("Config") or {}).get("User") or "",
            "readonly_rootfs": bool(hc.get("ReadonlyRootfs")),
            "cap_add": hc.get("CapAdd") or [], "cap_drop": hc.get("CapDrop") or [],
            "security_opt": hc.get("SecurityOpt") or [], "tmpfs": sorted((hc.get("Tmpfs") or {}).keys()),
            "image": (ins.get("Config") or {}).get("Image"),
            # Config.Env는 기록하지 않는다(비밀이 들어 있을 수 있음)
        }

        # 2. 모든 프로세스의 유효 capability·UID
        proc = docker.exec(container, ["sh", "-c", PROC_CMD], timeout=30)
        procs = parse_proc_status(proc.stdout) if proc.returncode == 0 else []
        caps = sorted({cap for p in procs for cap in p.get("caps", [])})
        uids = sorted({p["uid_effective"] for p in procs if "uid_effective" in p})
        complete = bool(procs) and all("caps" in p and "uid_effective" in p and not p.get("read_error") for p in procs)

        # 3. sentinel 쓰기 시도
        mount_dests = {m.get("Destination") for m in host_mounts}
        writes: dict[str, str] = {}
        for path in self.cfg.get("sentinel_paths", DEFAULT_SENTINELS):
            r = docker.exec(container, ["sh", "-c", SENTINEL_CMD, "_", path, secrets.token_hex(4)], timeout=30)
            writes[path] = r.stdout.strip() if r.returncode == 0 and r.stdout.strip() in ("absent", "succeeded", "denied") \
                else "error"
        sentinel_results = {p: o for p, o in writes.items() if p in mount_dests or o == "absent"}
        container_fs_writes = {p: o for p, o in writes.items() if p not in sentinel_results}

        # 3-b. 쓰기 가능 + 실행 가능 마운트(그 자체가 권한): 마운트 표의 후보마다 실제 쓰기 시도
        mt = docker.exec(container, ["sh", "-c", MOUNTS_CMD], timeout=30)
        candidates = exec_candidates(mt.stdout) if mt.returncode == 0 else []
        exec_checks: dict[str, str] = {}
        for c in candidates:
            r = docker.exec(container, ["sh", "-c", SENTINEL_CMD, "_", c["target"], secrets.token_hex(4)], timeout=30)
            value = r.stdout.strip()
            exec_checks[c["target"]] = value if r.returncode == 0 and value in {"succeeded", "denied", "absent"} else "error"
        exec_writable = sorted(t for t, o in exec_checks.items() if o == "succeeded")

        # 4. 정상 대조(신뢰 출발점 → 엔진)
        reqs, probes = functional_requests(ctx, via="engine")
        res = ctx.cprobe(ctx.trusted_net, reqs, max(ctx.timeout, 30.0)) if reqs else []
        func_raw = {}
        for (pid, fp), r in zip(probes.items(), res):
            func_raw[pid] = r
            ctx.record("trusted", "functional-engine", pid, fp.as_role, r)
        functional, functional_status, normal = functional_results(probes, func_raw)

        mount_lines = [line.split() for line in mt.stdout.splitlines() if line.strip()]
        mounts_complete = mt.returncode == 0 and bool(mount_lines) and all(len(line) >= 6 for line in mount_lines)
        observations = {
            "process_scan_complete": {"value": complete, "collected": True},
            "capabilities_effective": {"value": caps, "collected": any("caps" in p for p in procs)},
            "mounts": {"value": mounts, "collected": True},
            "devices": {"value": devices, "collected": True},
            "privileged": {"value": privileged, "collected": True},
            "host_namespaces": {"value": host_ns, "collected": True},
            "root_uid_present": {"value": 0 in uids, "collected": 0 in uids or (bool(procs) and all("uid_effective" in p for p in procs))},
            "sentinel_results": {"value": sentinel_results, "collected": all(v != "error" for v in sentinel_results.values())},
            "exec_writable_mounts": {"value": exec_writable,
                                     "collected": mounts_complete and all(v != "error" for v in exec_checks.values())},
            "functional_probes_planned": {"value": list(probes), "collected": True},
            "functional_probe_results": {"value": functional, "collected": bool(probes)},
        }
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
            "proc_read_rc": proc.returncode, "sentinel_results": sentinel_results,
            "container_fs_writes": container_fs_writes,
            "exec_candidates": candidates, "exec_write_checks": exec_checks,
            "functional_probe_results": functional, "functional_probe_status": functional_status,
            "fixture": {"sentinel_paths": list(writes), "proc_cmd": PROC_CMD, "sentinel_cmd": SENTINEL_CMD},
        }
        return observations, normal, challenges, summary
