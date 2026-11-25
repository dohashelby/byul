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
    _RANKED_PROCESS_FILES, _read_logs, _ready_window, _source_profile, _verify_launch, _window_stamp,
    normal_startup_load_transport,
)

NORMAL_OPTION = "normal_compile_cache_only"
PROFILE = "kubernetes-owned-normal-compile-cache/1"
_CHECK_PROFILE = "supply-boot-cache-tamper-v1"
_SOURCE_FILES = {"compilation/backends.py", "compilation/monitor.py"}
_AOT_SOURCE = "compilation/decorators.py"
_AOT_SITE = "aot_save_call_linenumber"
_SOURCE_KEYS = {"file_sha256", "directory_call_linenumber", "completion_call_linenumber",
                "cache_env", "cache_root", "relative_directory_pattern", "artifact_name"}
_SHA = re.compile(r"[0-9a-f]{64}")
_LINE = re.compile(rb"(?P<time>[0-9T:.Z-]{20,30}) (?:(?P<process>\([A-Za-z0-9_-]{1,40} pid=[1-9][0-9]{0,8}\)) )?"
                   rb"INFO [0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
                   rb"\[(?P<source>backends|monitor|decorators)\.py:(?P<line>[0-9]{1,5})\] (?P<body>[^\r\n]{1,300})")
_DIRECTORY = re.compile(rb"Using cache directory: (/[A-Za-z0-9_./-]{1,255}) for vLLM's torch\.compile")
_COMPLETION = re.compile(rb"torch\.compile took ([0-9]{1,6}\.[0-9]{2}) s in total")
_AOT_SAVED = re.compile(rb"saved AOT compiled function to (/[A-Za-z0-9_./-]{1,255})")
_CACHE_ROOT = "/cache/vllm"
_RELATIVE_DIRECTORY = r"torch_compile_cache/[0-9a-f]{10}/rank_0_0/backbone"
_ARTIFACT_NAME = "vllm_compile_cache.py"
_AOT_ARTIFACT = "model"
_AOT_KIND = "native_aot_model"
_AOT_RELATIVE_PATH = r"torch_compile_cache/torch_aot_compile/[0-9a-f]{64}/rank_0_0/model"
_CACHE_FORBIDDEN_LONG = {"--compilation-config", "--optimization-level", "--enforce-eager"}
_CACHE_FORBIDDEN_ENV = {"VLLM_DISABLE_COMPILE_CACHE", "VLLM_USE_AOT_COMPILE", "TORCH_COMPILE_DISABLE", "TORCHDYNAMO_DISABLE"}
_METADATA_KEYS = {"device", "inode", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns"}
_METADATA_BODY = r'''
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

try:
    cache_id, = sys.argv[1:]
    if re.fullmatch(CACHE_ID_PATTERN, cache_id) is None:
        raise ValueError()
    parent = CACHE_ROOT.lstrip("/").split("/") + ["torch_compile_cache", *NAMESPACE, cache_id, *SUFFIX]
    chain, artifact = snapshot(parent)
    if snapshot(parent) != (chain, artifact):
        raise ValueError()
    print(json.dumps({"reader_uid": os.geteuid(), "directory": chain[-1], "artifact": artifact}))
except (OSError, ValueError):
    print("{}")
'''
def _metadata_source(artifact_name, namespace, suffix, key_pattern):
    return (f"CACHE_ROOT = {_CACHE_ROOT!r}\nARTIFACT_NAME = {artifact_name!r}\n"
            f"NAMESPACE = {namespace!r}\nSUFFIX = {suffix!r}\nCACHE_ID_PATTERN = {key_pattern!r}\n"
            + _METADATA_BODY)


_METADATA_SOURCE = _metadata_source(_ARTIFACT_NAME, [], ["rank_0_0", "backbone"], r"[0-9a-f]{10}")
_METADATA_PROGRAM = supervised_source(_METADATA_SOURCE)
_METADATA_PROGRAM_SHA256 = sha256_hex(_METADATA_PROGRAM.encode())
_AOT_METADATA_PROGRAM = supervised_source(_metadata_source(_AOT_ARTIFACT, ["torch_aot_compile"],
                                                           ["rank_0_0"], r"[0-9a-f]{64}"))
_MISSING = ["inductor_or_triton_artifact_scope", "all_cached_files_and_writers", "compile_cache_initial_emptiness",
            "file_read_and_consumption", "cache_integrity_enforcement", "restart_reuse"]


def normal_compile_cache_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Readiness uses the same non-dispatching boot prefix validation as LIF-01."""
    return normal_startup_load_transport(target, env, duration=duration, binary=binary,
                                         runner=runner, allow_ranked=True)


def _compile_profile(adapter, env):
    method = getattr(adapter, "compile_cache_source", None)
    source = (method(env) if callable(method) else
              get_provider(env.engine.name).evidence_contract("compile_cache_source", env))
    ranked = env.runtime.tp_size == 2
    keys = _SOURCE_KEYS | {_AOT_SITE} if ranked else _SOURCE_KEYS
    files = _SOURCE_FILES | {_AOT_SOURCE} if ranked else _SOURCE_FILES
    sites = {"directory_call_linenumber", "completion_call_linenumber"}
    if ranked:
        sites.add(_AOT_SITE)
    if (type(source) is not dict or set(source) != keys
            or type(source["file_sha256"]) is not dict or source["file_sha256"].keys() != files
            or any(type(value) is not str or not _SHA.fullmatch(value) for value in source["file_sha256"].values())
            or any(type(source[key]) is not int or not 1 <= source[key] <= 99999
                   for key in sites)
            or type(source["cache_env"]) is not str or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", source["cache_env"])
            or source["cache_root"] != _CACHE_ROOT or source["relative_directory_pattern"] != _RELATIVE_DIRECTORY
            or source["artifact_name"] != _ARTIFACT_NAME):
        raise ValueError("SUP07_COMPILE_SOURCE_UNQUALIFIED")
    return source


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
    if "ranked_load" in startup:
        expected.update({name: startup["file_sha256"][name] for name in _RANKED_PROCESS_FILES})
    expected.update(profile["file_sha256"])
    files = [{"root": 0, "relative": filename} for filename in sorted(expected)]
    rows = transport.engine_supply_hashes([startup["source_root"]], files)
    observed = {row["relative"]: row["sha256"] for row in rows if row["state"] == "hashed"
                and type(row["root"]) is int and row["root"] == 0}
    if len(rows) != len(observed) or observed != expected:
        raise ValueError("SUP07_COMPILE_SOURCE_MISMATCH")
    return observed


def _compile_observation(raw, window, profile, *, worker=None):
    label = b"(" + worker.encode("ascii") + b" pid=" if worker is not None else None
    started, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    directories, completions, publications = [], [], []
    for line in raw.split(b"\n")[:-1]:
        row = _LINE.fullmatch(line)
        if row is None or label is not None and not (row["process"] or b"").startswith(label):
            continue
        instant = _window_stamp(row["time"].decode("ascii"))
        if not started <= instant <= ready:
            continue
        location = (row["source"], int(row["line"]))
        if location == (b"backends", profile["directory_call_linenumber"]):
            directory = _DIRECTORY.fullmatch(row["body"])
            if directory:
                directories.append((instant, directory[1].decode("ascii"), row["process"], sha256_hex(line)))
        elif location == (b"monitor", profile["completion_call_linenumber"]):
            completion = _COMPLETION.fullmatch(row["body"])
            if completion:
                completions.append((instant, int(Decimal(completion[1].decode("ascii")) * 1000),
                                    row["process"], sha256_hex(line)))
        elif location == (b"decorators", profile.get(_AOT_SITE)):
            published = _AOT_SAVED.fullmatch(row["body"])
            if published is None:
                raise ValueError("SUP07_COMPILE_AOT_REPORT_UNVERIFIED")
            publications.append((instant, published[1].decode("ascii"), row["process"], sha256_hex(line)))

    (selected_at, directory, directory_process, directory_line_sha), = directories
    (completed_at, elapsed_ms, completion_process, completion_line_sha), = completions
    root = PurePosixPath(profile["cache_root"])
    folder = PurePosixPath(directory)
    relative = folder.relative_to(root)
    if (selected_at > completed_at or folder.as_posix() != directory or ".." in relative.parts
            or label is not None and directory_process != completion_process
            or not re.fullmatch(profile["relative_directory_pattern"], relative.as_posix())):
        raise ValueError("SUP07_COMPILE_PATH_UNVERIFIED")
    observation = {"selected_at": selected_at[0].isoformat(), "completed_at": completed_at[0].isoformat(),
                   "reported_compile_elapsed_ms": elapsed_ms, "scope": "native_ordinary_compile_monitor"}
    if label is not None:
        observation.update(reported_tp_rank=0, directory_line_sha256=directory_line_sha,
                           completion_line_sha256=completion_line_sha)
    if publications:
        (published_at, published_path, published_process, publication_sha), = publications
        artifact = PurePosixPath(published_path)
        if (artifact.as_posix() != published_path or published_process != directory_process
                or not selected_at <= published_at <= completed_at
                or not re.fullmatch(_AOT_RELATIVE_PATH, artifact.relative_to(root).as_posix())):
            raise ValueError("SUP07_COMPILE_AOT_REPORT_UNVERIFIED")
        folder = artifact.parent
        observation.update(artifact_kind=_AOT_KIND, publication_line_sha256=publication_sha)
    return folder, observation


def _ns(value):
    instant, remainder_ns = _window_stamp(value)
    delta = instant - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return ((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds) * 1000 + remainder_ns


def _cache_metadata(transport, folder, profile, mount, window, *, aot=False):
    engine = transport.binding["engine"]
    before, container = _ready_window(transport)
    if before != window or _cache_mount(container, profile) != mount:
        raise ValueError("SUP07_COMPILE_MOUNT_UNVERIFIED")
    cache_id = folder.relative_to(_CACHE_ROOT).parts[2 if aot else 1]
    program = _AOT_METADATA_PROGRAM if aot else _METADATA_PROGRAM
    artifact_name = _AOT_ARTIFACT if aot else _ARTIFACT_NAME
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
    source = {"metadata_program_sha256": sha256_hex(program.encode()) if aot else _METADATA_PROGRAM_SHA256,
              "metadata_snapshot_sha256": sha256_hex(canonical_json(result))}
    if file_meta is None:
        return None, {"artifact_absent_at_capture": True, **source}
    started, ready = (_ns(window[key]) for key in ("container_started_at", "ready_at"))
    if (not stat.S_ISREG(file_meta["mode"]) or file_meta["size"] <= 0
            or directory_meta["device"] != file_meta["device"]
            or not started <= file_meta["mtime_ns"] <= file_meta["ctime_ns"] <= ready):
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
    version = "0.1.0+k8s-normal-compile-cache"

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

    def _saved_target(self):
        from guard.adapters.vllm_file_read_sensor import dependency

        return strict_json(dependency(self.cfg["target"], None), limit=65536)

    def prepared_normal_compile_cache(self, check, env, values, *, expected_target_sha256):
        """Validate the saved boot selector and configured transport without collecting."""
        self.validate_policy(check, values)
        try:
            target = self._saved_target()
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
            target = self._saved_target()
            target_sha, startup, compile_profile, logs, source_reader, metadata_reader = self._target_inputs(
                target, env, adapter, duration=duration, expected_target_sha256=self._expected_target_sha256)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, re.error, KubernetesBindingError):
            raise ValueError("SUP07_COMPILE_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        observed = source_hashes = retained_compile_report = None
        failure = "SUP07_COMPILE_OWNER_BEFORE_UNVERIFIED"
        try:
            window, container = _ready_window(logs)
            if any(prefix[key] != value for key, value in window.items()):
                raise ValueError
            _verify_launch(target, container, startup)
            mount = _cache_mount(container, compile_profile)
            failure = "SUP07_COMPILE_INSTALLED_SOURCE_UNVERIFIED"
            source_hashes = _verify_compiler(source_reader, startup, compile_profile)
            failure = "SUP07_COMPILE_PREFIX_UNVERIFIED"
            raw = _read_logs(logs, window, prefix["byte_count"])
            if len(raw) != prefix["byte_count"] or sha256_hex(raw) != prefix["sha256"]:
                raise ValueError
            failure = "SUP07_COMPILE_REPORT_UNVERIFIED"
            ranked = startup.get("ranked_load")
            worker = ranked["process_names"][0] if ranked else None
            folder, compilation = _compile_observation(raw, window, compile_profile, worker=worker)
            try:
                metadata, absence = _cache_metadata(metadata_reader, folder, compile_profile, mount, window,
                                                   aot=compilation.get("artifact_kind") == _AOT_KIND)
            except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
                metadata = absence = None
            failure = "SUP07_COMPILE_OWNER_AFTER_UNVERIFIED"
            final_window, container = _ready_window(logs)
            _verify_launch(target, container, startup)
            if (final_window == window and _cache_mount(container, compile_profile) == mount
                    and _verify_compiler(source_reader, startup, compile_profile) == source_hashes
                    and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"]
                                for t in (logs, source_reader, metadata_reader))):
                if metadata is None:
                    failure = "SUP07_COMPILE_FILE_METADATA_UNVERIFIED"
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
        if "ranked_load" in startup:
            missing.append("other_tp_rank_compile_artifacts")
        settings = {NORMAL_OPTION: True, "max_duration_seconds": duration, "cache_scope": "one_native_rank_zero_artifact"}
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
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None,
                            "detail": "Selected cache metadata and ordinary compile evidence; restart or consumption not verified."},
            challenges=[{"step": step, "attempted": index == 0 and observed is not None}
                        for index, step in enumerate(check.challenge_steps)],
            observations={"supply_boot_sessions": {"value": [observed] if observed else None, "collected": observed is not None},
                          "supply_boot_violations": {"value": None, "collected": False},
                          "supply_boot_missing": {"value": missing, "collected": True},
                          "supply_boot_complete": {"value": False, "collected": True},
                          "supply_boot_record_authenticated": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "source_scope": "normal_compile_cache"})),
                           "config_sha256": conditions_sha, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
