"""Read-only host and Tetragon capability classification for enforcement candidates."""

from __future__ import annotations

import re
from typing import Any


def _flag(config: str | None, name: str) -> bool | None:
    if config is None:
        return None
    match = re.search(rf"^{re.escape(name)}=(y|n)$", config, re.M)
    if match:
        return match.group(1) == "y"
    return False if re.search(rf"^# {re.escape(name)} is not set$", config, re.M) else None


def _probe_flag(probe: str | None, name: str) -> bool | None:
    if probe is None:
        return None
    match = re.search(rf'(?i)["\']?{re.escape(name)}["\']?\s*[:=]\s*(true|false)', probe)
    return match.group(1).lower() == "true" if match else None


def classify_capabilities(*, kernel: str | None, config: str | None, lsm: str | None,
                          injection_list: str | None, probe: str | None) -> dict[str, Any]:
    override_config = _flag(config, "CONFIG_BPF_KPROBE_OVERRIDE")
    bpf_lsm_config = _flag(config, "CONFIG_BPF_LSM")
    lsm_names = [name.strip() for name in lsm.split(",") if name.strip()] if lsm is not None else None
    override_probe = _probe_flag(probe, "override_return")
    lsm_probe = _probe_flag(probe, "lsm")
    fmodret_probe = _probe_flag(probe, "fmodret")
    symbols = set(re.findall(r"\b(?:security_file_permission|(?:__x64_|__arm64_)?sys_openat)\b", injection_list)) if injection_list is not None else None
    supported: list[str] = []
    unavailable: list[str] = []
    unknown: list[str] = []
    if (lsm_names is not None and "bpf" in lsm_names) or lsm_probe is True:
        supported.append("lsm_file_open")
    elif lsm_probe is False or (lsm_names is not None and "bpf" not in lsm_names):
        unavailable.append("lsm_file_open")
    else:
        unknown.append("lsm_file_open")
    if fmodret_probe is True and (override_probe is False or (symbols is not None and "security_file_permission" not in symbols)):
        # security_* hooks can use the fmodret path. A missing kprobe Override
        # feature alone is not proof that this policy cannot load.
        unknown.append("override_lsm")
    elif override_config is False or override_probe is False or (symbols is not None and "security_file_permission" not in symbols):
        unavailable.append("override_lsm")
    elif override_config is True and override_probe is True and symbols is not None and "security_file_permission" in symbols:
        supported.append("override_lsm")
    else:
        unknown.append("override_lsm")
    openat = sorted(symbol for symbol in (symbols or set()) if symbol.endswith("sys_openat"))
    if override_config is False or override_probe is False or (symbols is not None and not openat):
        unavailable.append("override_syscall")
    elif override_config is True and override_probe is True and openat:
        supported.append("override_syscall")
    else:
        unknown.append("override_syscall")
    # fd_install requires a live load trial. The host inspection alone cannot prove it.
    unknown.append("sigkill_fd_install")
    recommended = next((method for method in ("lsm_file_open", "override_syscall", "sigkill_fd_install") if method in supported), None)
    return {"kernel": kernel, "config_bpf_kprobe_override": override_config, "config_bpf_lsm": bpf_lsm_config,
            "lsm": lsm_names, "apparmor": None if lsm_names is None else "apparmor" in lsm_names,
            "bpf_lsm": None if lsm_names is None else "bpf" in lsm_names,
            "tetragon_override_return": override_probe, "tetragon_lsm": lsm_probe,
            "tetragon_fmodret": fmodret_probe,
            "error_injection_list_readable": injection_list is not None,
            "error_injection_symbols": openat + (["security_file_permission"] if symbols and "security_file_permission" in symbols else []),
            "available": supported, "unavailable": unavailable, "unknown": unknown,
            "recommended": recommended}


def collect_capabilities(runner: Any, context: str, tetragon_namespace: str = "kube-system") -> dict[str, Any]:
    def read(command: list[str]) -> str | None:
        result = runner.run(command, timeout=10)
        return result.stdout if result.returncode == 0 else None
    kernel = read(["uname", "-r"])
    kernel = kernel.strip() if kernel else None
    config = read(["cat", f"/boot/config-{kernel}"]) if kernel else None
    if config is None:
        config = read(["zcat", "/proc/config.gz"])
    lsm = read(["cat", "/sys/kernel/security/lsm"])
    injection = read(["cat", "/sys/kernel/debug/error_injection/list"])
    probe = read(["kubectl", "--context", context, "-n", tetragon_namespace,
                  "exec", "daemonset/tetragon", "-c", "tetragon", "--", "tetra", "probe"])
    return classify_capabilities(kernel=kernel, config=config, lsm=lsm, injection_list=injection, probe=probe)
