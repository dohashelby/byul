"""Monitor code opens in download/writable directories, independent of request fields."""
from __future__ import annotations

import posixpath
import unicodedata

from .intent import EnforcementIntent

RULE_ID = "TG-CODE-01"
CODE_SUFFIXES = (".py", ".pyc", ".pyo", ".so")
PATH_LIMIT = 4096
DEFAULT_WATCH_PATHS = ("/cache", "/tmp", "/var/tmp", "/dev/shm", "/root/.cache", "/home")


def watch_prefixes(intent: EnforcementIntent) -> list[str]:
    paths = [*DEFAULT_WATCH_PATHS, intent.protected_paths.model_dir,
             intent.protected_paths.cache_dir, *intent.code_load_watch_paths]
    if intent.assets is not None:
        paths.append(intent.assets.engine_cache_watch_root)
        if intent.assets.tokenizer_dir:
            paths.append(intent.assets.tokenizer_dir)
    prefixes = sorted({posixpath.normpath(path).rstrip("/") + "/" for path in paths})
    # Prefix matching has no glob semantics. Keep directory boundaries and
    # remove redundant descendants, including HF caches below /cache or /home.
    prefixes = [p for p in prefixes if not any(p != q and p.startswith(q) for q in prefixes)]
    if any(len(p.encode("utf-8")) > 256 or p == "/" for p in prefixes):
        raise ValueError("TG-CODE-01 directory prefixes must fit Tetragon's 256-byte limit and cannot be /")
    return prefixes


def clean_path(raw: str | None) -> tuple[str | None, bool]:
    """Bound display text; never use a truncated/escaped path as approval evidence."""
    if not isinstance(raw, str) or not raw:
        return None, False
    safe = "".join(c if not unicodedata.category(c).startswith("C") else "?" for c in raw[:PATH_LIMIT])
    valid = (len(raw) <= PATH_LIMIT and safe == raw and raw.startswith("/")
             and not raw.startswith("//") and ".." not in raw.split("/"))
    return safe, valid


def classify_code_open(record: dict, event: dict, policy, identity: dict | None) -> None:
    """Use the existing phase evidence; execution allowlists do not approve imports."""
    path, valid = clean_path(record.get("path"))
    record.update(path=path, phase=["unknown"], code_load_verdict="unknown", reason="phase_unavailable")
    if policy is None:
        return
    process = event.get("process") if isinstance(event.get("process"), dict) else {}
    pod = process.get("pod") if isinstance(process.get("pod"), dict) else {}
    container = pod.get("container") if isinstance(pod.get("container"), dict) else {}
    intent = policy.intent
    from .events import _matches_engine
    attributed = (
        _matches_engine(event, intent) and bool(record.get("binary"))
        and type(process.get("pid")) is int and process["pid"] > 0
        and bool(pod.get("uid")) and bool(record.get("pod"))
        and bool(intent.engine.container_name)
        and container.get("name") == intent.engine.container_name
        and (identity is None or identity.get("state") == "confirmed")
    )
    record["container_attribution"] = "available" if attributed else "unavailable"
    if not attributed:
        record["reason"] = "engine_attribution_unavailable"
        return
    record["pod_uid"] = pod["uid"]
    record["container_name"] = container["name"]
    # Reuse heartbeat, Pod generation, interval and engine-signal checks. Only
    # phase evidence is copied; binary approval/exception verdicts are discarded.
    phase = policy.classify_exec(binary=record["binary"], parent=None, image=None,
                                 observed_at=record["observed_at"], pod_uid=pod["uid"])
    record.update({key: value for key, value in phase.items() if key.startswith("phase")})
    file_args = [arg["file_arg"] for arg in event.get("args", [])
                 if isinstance(arg, dict) and isinstance(arg.get("file_arg"), dict)]
    if (not valid or any(arg.get("flags") or arg.get("path") != path for arg in file_args)):
        record["reason"] = "path_evidence_invalid"
    elif phase["phase_source_error"] is not None or phase["phase"] == ["unknown"]:
        record["reason"] = "phase_unavailable"
    elif phase["phase"] != ["inferring"]:
        # Overlapping phases are ambiguous, never silently treated as startup.
        record["reason"] = "phase_ambiguous" if len(phase["phase"]) != 1 else "outside_inferring"
        if len(phase["phase"]) == 1:
            record["code_load_verdict"] = "observed"
    elif not path.endswith(CODE_SUFFIXES) or not any(path.startswith(p) for p in watch_prefixes(intent)):
        record.update(code_load_verdict="observed", reason="outside_code_watch_scope")
    elif record.get("access") != "open" or event.get("function_name") != "security_file_open":
        record["reason"] = "open_evidence_unavailable"
    elif record.get("action_mode") != "monitor":
        record["reason"] = "monitor_evidence_unavailable"
    else:
        record.update(code_load_verdict="detected", reason="code_open_during_inferring")


def apply_collection_evidence(record: dict, heartbeat: dict) -> None:
    """A gap cannot become a claim of no detection; keep the existing feed status."""
    if record.get("rule_id") != RULE_ID:
        return
    if (not heartbeat.get("configured") or heartbeat.get("state") != "running"
            or heartbeat.get("open_gap")):
        record.update(code_load_verdict="unknown", reason="collector_unavailable")
