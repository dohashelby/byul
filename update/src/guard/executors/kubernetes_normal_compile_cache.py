"""SUP-07 metadata for an exact file reported by an ordinary owned compile."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath
import re
import stat

from guard.adapters.engine_provider import get_provider
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import (
    ENGINE_CALLBACK_PROFILE, REQUEST_BINDING_PROFILE, KubernetesBindingError, KubernetesHTTP,
)
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import supervised_source
from .kubernetes_normal_startup_load import (
    _read_logs, _ready_window, _source_profile, _verify_launch, _window_stamp, normal_startup_load_transport,
)

NORMAL_OPTION = "normal_compile_cache_only"
PROFILE = "kubernetes-owned-normal-compile-cache/1"
_CHECK_PROFILE = "supply-boot-cache-tamper-v1"
_SOURCE_FILES = {"compilation/backends.py", "compilation/monitor.py"}
_SOURCE_KEYS = {"file_sha256", "directory_call_linenumber", "completion_call_linenumber",
                "cache_env", "cache_root", "relative_directory_pattern", "artifact_name"}
_SHA = re.compile(r"[0-9a-f]{64}")
_LINE = re.compile(rb"(?P<time>[0-9T:.Z-]{20,30}) (?P<process>\([A-Za-z0-9_-]{1,40} pid=[1-9][0-9]{0,8}\) )?"
                   rb"INFO [0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
                   rb"\[(?P<source>backends|monitor)\.py:(?P<line>[0-9]{1,5})\] (?P<body>[^\r\n]{1,300})")
_DIRECTORY = re.compile(rb"Using cache directory: (/[A-Za-z0-9_./-]{1,255}) for vLLM's torch\.compile")
_COMPLETION = re.compile(rb"torch\.compile took ([0-9]{1,6}\.[0-9]{2}) s in total")
_CACHE_ROOT = "/cache/vllm"
_RELATIVE_DIRECTORY = r"torch_compile_cache/[0-9a-f]{10}/rank_0_0/backbone"
_ARTIFACT_NAME = "vllm_compile_cache.py"
_AOT_DIRECTORY = re.compile(r"torch_compile_cache/torch_aot_compile/[a-f0-9]{64}/rank_0_0")
_AOT_KEYS = {"file_sha256", "save_call_linenumber", "load_call_linenumber", "log_pattern"}
_CACHE_FORBIDDEN_LONG = {"--compilation-config", "--optimization-level", "--enforce-eager"}
_CACHE_FORBIDDEN_ENV = {"VLLM_DISABLE_COMPILE_CACHE", "VLLM_USE_AOT_COMPILE", "TORCH_COMPILE_DISABLE", "TORCHDYNAMO_DISABLE"}
_METADATA_KEYS = {"device", "inode", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns"}
_METADATA_COMMON = r'''
import json, os, re, stat, sys

def mark(info):
    return {name: getattr(info, "st_" + field) for name, field in (
        ("device", "dev"), ("inode", "ino"), ("mode", "mode"), ("uid", "uid"),
        ("gid", "gid"), ("size", "size"), ("mtime_ns", "mtime_ns"), ("ctime_ns", "ctime_ns"))}

def snapshot(parts):
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    chain = []
    try:
        for part in parts:
            info = os.stat(part, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ValueError()
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            if mark(os.fstat(fd)) != mark(info):
                raise ValueError()
            chain.append(mark(info))
        try:
            artifact = os.stat(ARTIFACT_NAME, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return chain, None
        if not stat.S_ISREG(artifact.st_mode):
            raise ValueError()
        return chain, mark(artifact)
    finally:
        os.close(fd)

'''
_METADATA_SOURCE = f"CACHE_ROOT = {_CACHE_ROOT!r}\nARTIFACT_NAME = {_ARTIFACT_NAME!r}\n" + _METADATA_COMMON + r'''try:
    cache_id, = sys.argv[1:]
    if re.fullmatch(r"[0-9a-f]{10}", cache_id) is None:
        raise ValueError()
    parent = CACHE_ROOT.lstrip("/").split("/") + ["torch_compile_cache", cache_id, "rank_0_0", "backbone"]
    chain, artifact = snapshot(parent)
    if snapshot(parent) != (chain, artifact):
        raise ValueError()
    print(json.dumps({"reader_uid": os.geteuid(), "directory": chain[-1], "artifact": artifact}))
except (OSError, ValueError):
    print("{}")
'''
_METADATA_PROGRAM = supervised_source(_METADATA_SOURCE)
_METADATA_PROGRAM_SHA256 = sha256_hex(_METADATA_PROGRAM.encode())
_AOT_METADATA_SOURCE = f"CACHE_ROOT = {_CACHE_ROOT!r}\nARTIFACT_NAME = 'model'\n" + _METADATA_COMMON + r'''
try:
    cache_id, = sys.argv[1:]
    if re.fullmatch(r"[0-9a-f]{64}", cache_id) is None:
        raise ValueError()
    parent = CACHE_ROOT.lstrip("/").split("/") + ["torch_compile_cache", "torch_aot_compile", cache_id, "rank_0_0"]
    chain, artifact = snapshot(parent)
    if snapshot(parent) != (chain, artifact):
        raise ValueError()
    print(json.dumps({"reader_uid": os.geteuid(), "directory": chain[-1], "artifact": artifact}))
except (OSError, ValueError):
    print("{}")
'''
_AOT_METADATA_PROGRAM = supervised_source(_AOT_METADATA_SOURCE)
_AOT_METADATA_PROGRAM_SHA256 = sha256_hex(_AOT_METADATA_PROGRAM.encode())
_MISSING = ["inductor_or_triton_artifact_scope", "all_cached_files_and_writers", "compile_cache_initial_emptiness",
            "file_read_and_consumption", "cache_integrity_enforcement", "restart_reuse"]


def normal_compile_cache_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Readiness uses the same non-dispatching boot prefix validation as LIF-01."""
    return normal_startup_load_transport(target, env, duration=duration, binary=binary, runner=runner)


def _compile_profile(adapter, env):
    method = getattr(adapter, "compile_cache_source", None)
    source = (method(env) if callable(method) else
              get_provider(env.engine.name).evidence_contract("compile_cache_source", env))
    if (type(source) is not dict or source.keys() - {"aot"} != _SOURCE_KEYS
            or type(source["file_sha256"]) is not dict or source["file_sha256"].keys() != _SOURCE_FILES
            or any(type(value) is not str or not _SHA.fullmatch(value) for value in source["file_sha256"].values())
            or any(type(source[key]) is not int or not 1 <= source[key] <= 99999
                   for key in ("directory_call_linenumber", "completion_call_linenumber"))
            or type(source["cache_env"]) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", source["cache_env"])
            or source["cache_root"] != _CACHE_ROOT or source["relative_directory_pattern"] != _RELATIVE_DIRECTORY
            or source["artifact_name"] != _ARTIFACT_NAME):
        raise ValueError("SUP07_COMPILE_SOURCE_UNQUALIFIED")
    if source.get("aot") is not None:
        aot = source["aot"]
        pattern = _aot_pattern(aot)
        return {**source, "aot": {**aot, "log_pattern": pattern}}
    return source


def _aot_pattern(profile):
    if type(profile) is not dict or profile.keys() != _AOT_KEYS:
        raise ValueError("SUP07_COMPILE_AOT_SOURCE_UNQUALIFIED")
    pins = profile["file_sha256"]
    lines = (profile["save_call_linenumber"], profile["load_call_linenumber"])
    pattern = profile["log_pattern"]
    if (type(pins) is not dict or len(pins) != 1
            or any(type(path) is not str or not re.fullmatch(r"compilation/[a-z_][a-z_0-9]{0,63}\.py", path)
                   or type(sha) is not str or not _SHA.fullmatch(sha) for path, sha in pins.items())
            or any(type(line) is not int or not 1 <= line <= 99999 for line in lines) or lines[0] == lines[1]
            or type(pattern) is not str or not 1 <= len(pattern) <= 1024 or not pattern.isascii()):
        raise ValueError("SUP07_COMPILE_AOT_SOURCE_UNQUALIFIED")
    try:
        compiled = re.compile(pattern.encode("ascii"))
    except re.error as exc:
        raise ValueError("SUP07_COMPILE_AOT_SOURCE_UNQUALIFIED") from exc
    if compiled.groupindex.keys() != {"time", "process", "line", "load", "path"}:
        raise ValueError("SUP07_COMPILE_AOT_SOURCE_UNQUALIFIED")
    return compiled


def _cache_mount(container, profile):
    if (any(option.startswith(("-cc", "-O")) or option.partition("=")[0].replace("_", "-") in _CACHE_FORBIDDEN_LONG
            for option in container["args"] if option.startswith("-"))
            or any(row["name"] in _CACHE_FORBIDDEN_ENV for row in container.get("env", []))):
        raise ValueError("SUP07_COMPILE_LAUNCH_UNQUALIFIED")
    root = PurePosixPath(profile["cache_root"])
    mount_path = root.parent.as_posix()
    matching, = [mount for mount in container.get("volumeMounts", [])
                 if mount["mountPath"] == mount_path]
    settings, = [row for row in container.get("env", []) if row["name"] == profile["cache_env"]]
    other_paths = [PurePosixPath(mount["mountPath"]) for mount in container.get("volumeMounts", [])
                   if mount is not matching]
    if (any(path.is_relative_to(root) or root.is_relative_to(path) for path in other_paths)
            or matching.get("mountPropagation", "None") != "None"
            or matching.get("subPath") or matching.get("subPathExpr") or matching.get("readOnly", False) is not False
            or settings.get("value") != root.as_posix() or "valueFrom" in settings):
        raise ValueError("SUP07_COMPILE_MOUNT_UNVERIFIED")
    return {"name": matching["name"], "mount_path": mount_path, "declared_read_only": False}


def _verify_compiler(transport, startup, profile):
    expected = {name: startup["file_sha256"][name] for name in ("logger.py", "logging_utils/formatter.py")}
    expected.update(profile["file_sha256"])
    files = [{"root": 0, "relative": filename} for filename in sorted(expected)]
    rows = transport.engine_supply_hashes([startup["source_root"]], files)
    observed = {row["relative"]: row["sha256"] for row in rows if row["state"] == "hashed"
                and type(row["root"]) is int and row["root"] == 0}
    if len(rows) != len(observed) or observed != expected:
        raise ValueError("SUP07_COMPILE_SOURCE_MISMATCH")
    return observed


def _reported_time(stamp):
    instant, submicro_ns = stamp
    if submicro_ns:
        return instant.isoformat(timespec="microseconds").replace("+00:00", f"{submicro_ns:03d}+00:00")
    return instant.isoformat()


def _compile_observation(raw, window, profile):
    started, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    directories, completions = [], []
    for line in raw.split(b"\n")[:-1]:
        row = _LINE.fullmatch(line)
        if row is None:
            continue
        instant = _window_stamp(row["time"].decode("ascii"))
        if not started <= instant <= ready:
            continue
        location = (row["source"], int(row["line"]))
        if location == (b"backends", profile["directory_call_linenumber"]):
            directory = _DIRECTORY.fullmatch(row["body"])
            if directory:
                directories.append((instant, row["process"], directory[1].decode("ascii")))
        elif location == (b"monitor", profile["completion_call_linenumber"]):
            completion = _COMPLETION.fullmatch(row["body"])
            if completion:
                completions.append((instant, row["process"], int(Decimal(completion[1].decode("ascii")) * 1000)))

    (selected_at, selected_process, directory), = directories
    (completed_at, completed_process, elapsed_ms), = completions
    root = PurePosixPath(profile["cache_root"])
    folder = PurePosixPath(directory)
    relative = folder.relative_to(root)
    if (selected_process != completed_process or selected_at > completed_at
            or folder.as_posix() != directory or ".." in relative.parts
            or not re.fullmatch(profile["relative_directory_pattern"], relative.as_posix())):
        raise ValueError("SUP07_COMPILE_PATH_UNVERIFIED")
    return folder, {"selected_at": _reported_time(selected_at), "completed_at": _reported_time(completed_at),
                    "log_process_labels_matched": selected_process is not None,
                    "reported_compile_elapsed_ms": elapsed_ms, "scope": "native_ordinary_compile_monitor"}


def _aot_observation(raw, window, profile):
    aot = profile.get("aot")
    if aot is None:
        return None
    pattern = aot["log_pattern"]
    started, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    selected = None
    for line in raw.split(b"\n")[:-1]:
        row = pattern.fullmatch(line)
        if row is None:
            continue
        stamp = row["time"].decode("ascii")
        if not started <= _window_stamp(stamp) <= ready:
            continue
        is_load = row["load"] is not None
        line_key = "load_call_linenumber" if is_load else "save_call_linenumber"
        if int(row["line"]) != aot[line_key] or selected is not None:
            raise ValueError("SUP07_AOT_REPORT_UNVERIFIED")
        directory = row["path"].decode("ascii")
        folder = PurePosixPath(directory)
        relative = folder.relative_to(profile["cache_root"])
        if folder.as_posix() != directory or not _AOT_DIRECTORY.fullmatch(relative.as_posix()):
            raise ValueError("SUP07_AOT_REPORT_UNVERIFIED")
        event = {"scope": "native_ordinary_aot_cache", "action": "load" if is_load else "save",
                 "reported_at": stamp, "log_line_sha256": sha256_hex(line),
                 "log_process_label_present": row["process"] is not None}
        selected = folder, event
    return selected


def _ns(timestamp):
    instant, submicro_ns = _window_stamp(timestamp)
    delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000 + submicro_ns


def _cache_metadata(transport, folder, profile, mount, window, *, aot_event=None):
    engine = transport.binding["engine"]
    before, container = _ready_window(transport)
    if before != window or _cache_mount(container, profile) != mount:
        raise ValueError("SUP07_COMPILE_MOUNT_UNVERIFIED")
    relative = folder.relative_to(_CACHE_ROOT)
    if aot_event is None:
        cache_id = relative.parts[1]
        program, program_sha, artifact_name = _METADATA_PROGRAM, _METADATA_PROGRAM_SHA256, _ARTIFACT_NAME
    else:
        cache_id = relative.parts[2]
        program, program_sha, artifact_name = _AOT_METADATA_PROGRAM, _AOT_METADATA_PROGRAM_SHA256, "model"
    remaining = min(5, transport.deadline - transport.clock())
    if remaining <= 0:
        raise ValueError("SUP07_COMPILE_METADATA_UNVERIFIED")
    result = transport._call([f"--namespace={engine['namespace']}", "exec", engine["pod"],
        f"--container={engine['container']}", "--", "python3", "-I", "-S", "-B", "-c", program,
        f"{remaining:.9f}", cache_id], cap=4096, budget=remaining)
    after, container = _ready_window(transport)
    if (after != window or _cache_mount(container, profile) != mount
            or type(result) is not dict or set(result) != {"reader_uid", "directory", "artifact"}
            or type(result["reader_uid"]) is not int or not 0 < result["reader_uid"] <= 2**32 - 1):
        raise ValueError("SUP07_COMPILE_METADATA_UNVERIFIED")
    directory_meta, file_meta = result["directory"], result["artifact"]
    for metadata in ((directory_meta,) if file_meta is None else (directory_meta, file_meta)):
        if (type(metadata) is not dict or set(metadata) != _METADATA_KEYS
                or any(type(value) is not int or not 0 <= value < 2**64 for value in metadata.values())
                or not 0 <= metadata["mode"] <= 65535 or not 0 <= metadata["uid"] <= 2**32 - 1
                or not 0 <= metadata["gid"] <= 2**32 - 1):
            raise ValueError("SUP07_COMPILE_METADATA_UNVERIFIED")
    if not stat.S_ISDIR(directory_meta["mode"]):
        raise ValueError("SUP07_COMPILE_GENERATION_UNVERIFIED")
    source = {"metadata_program_sha256": program_sha,
              "metadata_snapshot_sha256": sha256_hex(canonical_json(result))}
    if file_meta is None:
        return None, {"artifact_absent_at_capture": True, **source}
    started, ready = (_ns(window[key]) for key in ("container_started_at", "ready_at"))
    modified, changed = file_meta["mtime_ns"], file_meta["ctime_ns"]
    time_matches = started <= modified <= changed <= ready
    if aot_event is not None:
        reported = _ns(aot_event["reported_at"])
        if aot_event["action"] == "load":
            time_matches = modified <= changed < started <= reported <= ready
        else:
            time_matches = started <= modified <= changed <= reported <= ready
        source.update({"file_device": file_meta["device"], "file_inode": file_meta["inode"]})
    if (not stat.S_ISREG(file_meta["mode"]) or file_meta["size"] <= 0
            or directory_meta["device"] != file_meta["device"]
            or not time_matches):
        raise ValueError("SUP07_COMPILE_GENERATION_UNVERIFIED")
    return {"file_path_sha256": sha256_hex((folder / artifact_name).as_posix().encode()),
            "directory_path_sha256": sha256_hex(folder.as_posix().encode()),
            "file_permissions": stat.S_IMODE(file_meta["mode"]), "directory_permissions": stat.S_IMODE(directory_meta["mode"]),
            "file_owner_uid": file_meta["uid"], "file_owner_gid": file_meta["gid"],
            "directory_owner_uid": directory_meta["uid"], "directory_owner_gid": directory_meta["gid"],
            "reader_uid": result["reader_uid"], "file_bytes": file_meta["size"],
            "boot_file_mtime_ns": file_meta["mtime_ns"], "boot_file_ctime_ns": file_meta["ctime_ns"],
            **source}, None


class KubernetesNormalCompileCacheExecutor:
    name = "supply_boot"
    version = "0.1.2+k8s-normal-compile-cache"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "SUP-07"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"] or type(values) is not dict or values):
            raise ValueError("SUP07_COMPILE_SCOPE_INVALID")

    def _target_inputs(self, target, env, adapter, *, duration, expected_target_sha256):
        target_sha = sha256_hex(canonical_json(target))
        if target_sha != expected_target_sha256 or self._expected_target_sha256 not in (None, target_sha):
            raise ValueError
        startup = _source_profile(adapter, env)
        compile_profile = _compile_profile(adapter, env)
        binary = self.cfg.get("kubectl_binary", "kubectl")
        logs = normal_compile_cache_transport(target, env, duration=duration, binary=binary, runner=self.runner)
        source_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                                       profile=ENGINE_CALLBACK_PROFILE, clock=logs.clock)
        metadata_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                                         profile=REQUEST_BINDING_PROFILE, clock=logs.clock)
        source_reader.deadline = metadata_reader.deadline = logs.deadline
        return target_sha, startup, compile_profile, logs, source_reader, metadata_reader

    def prepared_normal_compile_cache(self, check, env, values, *, expected_target_sha256):
        """Validate the saved boot selector and configured transport without collecting."""
        from guard.adapters.vllm_file_read_sensor import dependency

        self.validate_policy(check, values)
        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            adapter = get_provider(env.engine.name).adapter()
            self._target_inputs(target, env, adapter, duration=self.cfg.get("max_duration_seconds", 30),
                                expected_target_sha256=expected_target_sha256)
        except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError, re.error, KubernetesBindingError):
            raise ValueError("SUP07_COMPILE_TARGET_OR_SOURCE_UNVERIFIED") from None

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        duration = self.cfg.get("max_duration_seconds", 30)
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha, startup, compile_profile, logs, source_reader, metadata_reader = self._target_inputs(
                target, env, adapter, duration=duration, expected_target_sha256=self._expected_target_sha256)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, re.error, KubernetesBindingError):
            raise ValueError("SUP07_COMPILE_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        observed = source_hashes = retained_compile_report = None
        aot_event = None
        failure = "SUP07_COMPILE_OWNER_BEFORE_UNVERIFIED"
        try:
            window, container = _ready_window(logs)
            if any(prefix[key] != value for key, value in window.items()):
                raise ValueError
            _verify_launch(target, container, startup)
            mount = _cache_mount(container, compile_profile)
            failure = "SUP07_COMPILE_PREFIX_UNVERIFIED"
            raw = _read_logs(logs, window, prefix["byte_count"])
            if len(raw) != prefix["byte_count"] or sha256_hex(raw) != prefix["sha256"]:
                raise ValueError
            failure = "SUP07_COMPILE_REPORT_UNVERIFIED"
            aot = _aot_observation(raw, window, compile_profile)
            source_profile = compile_profile
            if aot is None:
                folder, compilation = _compile_observation(raw, window, compile_profile)
            else:
                folder, aot_event = aot
                source_profile = compile_profile["aot"]
                compilation = aot_event
            failure = "SUP07_COMPILE_INSTALLED_SOURCE_UNVERIFIED"
            source_hashes = _verify_compiler(source_reader, startup, source_profile)
            try:
                metadata, absence = _cache_metadata(metadata_reader, folder, compile_profile, mount, window,
                                                    aot_event=aot_event)
            except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
                metadata = absence = None
            failure = ("SUP07_COMPILE_FILE_METADATA_UNVERIFIED" if metadata is None
                       else "SUP07_COMPILE_OWNER_AFTER_UNVERIFIED")
            final_window, container = _ready_window(logs)
            _verify_launch(target, container, startup)
            if (final_window == window and _cache_mount(container, compile_profile) == mount
                    and _verify_compiler(source_reader, startup, source_profile) == source_hashes
                    and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"]
                                for t in (logs, source_reader, metadata_reader))):
                if metadata is None:
                    retained_compile_report = {
                        **compilation,
                        "reported_directory_sha256": sha256_hex(folder.as_posix().encode()),
                    }
                    if absence is not None:
                        retained_compile_report["cache_file_absence"] = absence
                else:
                    observed = {"role": "normal", "profile": PROFILE, "compilation": compilation,
                                "cache_file_metadata": metadata}
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            pass

        cleanup = sorted({item for t in (logs, source_reader, metadata_reader) for item in t.report()["cleanup_errors"]})
        missing = _MISSING + ([] if observed else ["ordinary_compile_cache_file_metadata"])
        settings = {NORMAL_OPTION: True, "max_duration_seconds": duration, "cache_scope": "one_native_rank_zero_artifact"}
        native = None
        if observed and aot_event is not None:
            settings["cache_scope"] = "one_native_rank_zero_aot_artifact"
            if aot_event["action"] == "load":
                source, = compile_profile["aot"]["file_sha256"]
                native = [{"role": "normal", "source": source, "installed_source_sha256": source_hashes[source],
                           "target_sha256": target_sha, "native_load_report": aot_event,
                           "cache_file_metadata": observed["cache_file_metadata"]}]
                missing = ["other_cache_file_read_and_consumption" if gap == "file_read_and_consumption" else gap
                           for gap in missing]
        conditions_sha = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                          if observed and target.get("comparison_config") else None)
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": logs.report()["binding_sha256"], "fixed_boot_prefix": prefix,
                   "installed_source_sha256": source_hashes if observed or retained_compile_report else None,
                   "retained_compile_report": retained_compile_report, "observation": observed,
                   "conditions_sha256": conditions_sha, "missing": missing,
                   "gap": None if observed else failure}
        error = None if observed else failure
        artifacts = []
        try:
            path = Path(workdir) / "normal_compile_cache.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("SUP07_COMPILE_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(logs.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        normal_detail = ("Owned AOT startup evidence. Paired modification and consumer enforcement remain unverified."
                         if aot_event is not None else
                         "Selected cache metadata and ordinary compile evidence; restart or consumption not verified.")
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None,
                            "detail": normal_detail},
            challenges=[{"step": step, "attempted": index == 0 and observed is not None and aot_event is None}
                        for index, step in enumerate(check.challenge_steps)],
            observations={"supply_boot_sessions": {"value": [observed] if observed else None, "collected": observed is not None},
                          "supply_boot_violations": {"value": None, "collected": False},
                          "supply_boot_missing": {"value": missing, "collected": True},
                          "supply_boot_complete": {"value": False, "collected": True},
                          "supply_boot_native_consumption": {"value": native, "collected": native is not None},
                          "supply_boot_record_authenticated": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "source_scope": "normal_compile_cache"})),
                           "config_sha256": conditions_sha, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
