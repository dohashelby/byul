"""OBS-04·OBS-05 Kubernetes 전송. 원 usage_report의 정책·전후 비교·수신 기록 형식·관측·판정은 그대로 쓴다.

바뀌는 것은 읽기 두 곳뿐이다.
- 엔진 쪽 보고 파일과 PID 1 설정: Docker helper 대신 검증된 엔진 Pod에서 고정 읽기 프로그램을 앞뒤 소유 확인과 함께 실행한다.
- 소유 수신지 기록: 같은 run의 소유 수신 Pod 고정 경로(`/guard-usage/receipts.jsonl`)를 고정 파일 읽기로 읽는다.
수신 기록의 생산자는 미인증이다. timeout이나 수신 실패만으로 비전송을 주장하지 않는 원 규칙도 그대로다.
"""
from __future__ import annotations

from posixpath import normpath
import re
import time

from guard.adapters.engine_provider import get_provider
from guard.collectors.file import load_bounded_json
from guard.models import DataOrigin, ExecStatus, ORIGIN_STRENGTH, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import validate_capture_target
from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP, USAGE_CONTROL_PROFILE
from guard.runtime.kubernetes_process import _mounts
from .content_common import bounded_probe, control_state
from .http_common import Ctx, functional_requests
from .kubernetes_lifecycle_capture import (
    KubernetesCaptureError, KubernetesCaptureFailureKind, KubernetesLifecycleReader, supervised_source,
)
from .kubernetes_normal_startup_load import _source_profile, _verify_launch
from .owned_capture import _FILE_READER, MAX_BYTES, validate_file_result
from .usage_report import PROFILES, SILENCE_PROFILE, UsageReportExecutor, _sink_snapshot, _snapshot_command, new_reports

SINK_PATH = "/guard-usage/receipts.jsonl"
CONFIG_KEYS = {"target", "sink_target", "kubectl_binary", "max_duration_seconds", "usage_wait_seconds", "local_observation_only"}
_SNAPSHOT_BYTES = 262144
# 고정 읽기만 하므로 lifecycle 센서 세션이 없다. reader 초기화에 쓰는 자리 값이며 어떤 기록 경로도 이 값으로 열지 않는다.
_NO_SESSION = "0" * 32
_OPTOUT_SETTINGS = ("VLLM_NO_USAGE_STATS", "DO_NOT_TRACK")
_REPORT_PATHS = frozenset({"/root/usage.jsonl", "/root/.config/vllm/usage_stats.json", "/home/guard/.config/vllm/usage_stats.json"})
_EFFECTIVE_PATHS = {"cache-xdg": "/cache/config/vllm/usage_stats.json",
                    "root-default": "/root/.config/vllm/usage_stats.json",
                    "guard-default": "/home/guard/.config/vllm/usage_stats.json"}
_USAGE_SOURCE_ROOT = "/usr/local/lib/python3.12/dist-packages/vllm"
_USAGE_SOURCE_FILES = ("envs.py", "usage/usage_lib.py")
_LOCAL_READER = (f"REPORT_PATHS = {sorted(_REPORT_PATHS)!r}\nEFFECTIVE_PATHS = {_EFFECTIVE_PATHS!r}\n"
                 f"OPTOUTS = {_OPTOUT_SETTINGS!r}\n" + r'''
import hashlib,json,os,stat,sys

def identity(info):
    return (info.st_dev, info.st_ino)

def checked_row(row, opened, missing=None):
    chain = []
    for parent, (name, fd) in enumerate(opened):
        selected = os.fstat(fd)
        if parent and identity(os.stat(name, dir_fd=opened[parent-1][1], follow_symlinks=False)) != identity(selected):
            return dict(path=row['path'], exists=None, readable=False)
        chain.append(hashlib.sha256(('%d:%d' % identity(selected)).encode('ascii')).hexdigest())
    if missing is not None:
        try:
            os.stat(missing, dir_fd=opened[-1][1], follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return dict(path=row['path'], exists=None, readable=False)
    return dict(row, directory_chain=chain)

def file_metadata(path):
    row = dict(path=path, exists=None, readable=False)
    parts = path.lstrip('/').split('/')
    opened = [('/', os.open('/', os.O_DIRECTORY | os.O_RDONLY))]
    try:
        for name in parts[:-1]:
            try:
                child = os.open(name, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW, dir_fd=opened[-1][1])
            except FileNotFoundError:
                return checked_row(dict(path=path, exists=False, readable=True), opened, missing=name)
            opened.append((name, child))
        directory = opened[-1][1]
        try:
            metadata = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return checked_row(dict(path=path, exists=False, readable=True), opened, missing=parts[-1])
        if not stat.S_ISREG(metadata.st_mode):
            return row
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino):
                return row
            limit = 4 * 1024 * 1024
            raw = stream.read(limit + 1)
            end = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (end.st_size, end.st_mtime_ns, end.st_ctime_ns):
                return row
            if len(raw) > limit or len(raw) != end.st_size:
                return row
        current = os.stat(parts[-1], dir_fd=directory, follow_symlinks=False)
        if (current.st_dev, current.st_ino, current.st_ctime_ns) != (end.st_dev, end.st_ino, end.st_ctime_ns):
            return row
        return checked_row(dict(path=path, exists=True, readable=True, bytes=len(raw), lines=raw.count(b'\n'),
                                sha256=hashlib.sha256(raw).hexdigest()), opened)
    except OSError:
        return row
    finally:
        for _, fd in reversed(opened):
            os.close(fd)

def start_ticks():
    with open('/proc/1/stat', 'rb') as stream:
        return int(stream.read(4096).rsplit(b')', 1)[1].split()[19])

def selected_environment():
    with open('/proc/1/environ', 'rb') as stream:
        environment = stream.read(1048577)
    if len(environment) > 1048576:
        raise ValueError('usage process profile unavailable')
    names = (b'HOME', b'XDG_CONFIG_HOME', b'VLLM_CONFIG_ROOT', *(name.encode() for name in OPTOUTS))
    selected = {}
    for item in environment.split(b'\0'):
        name, sep, value = item.partition(b'=')
        if name not in names:
            continue
        if not sep or name in selected or name == b'VLLM_CONFIG_ROOT':
            raise ValueError('usage process profile unavailable')
        selected[name] = value
    return selected

def report_root(environment):
    home, xdg = environment.get(b'HOME'), environment.get(b'XDG_CONFIG_HOME')
    if home == b'/cache/home' and xdg == b'/cache/config':
        return 'cache-xdg'
    if home == b'/root' and xdg in (None, b'/root/.config'):
        return 'root-default'
    if home == b'/home/guard' and xdg in (None, b'/home/guard/.config'):
        return 'guard-default'
    raise ValueError('usage process profile unavailable')

allow_cache, *paths = sys.argv[2:]
if (sys.argv[1] != ','.join(OPTOUTS) or allow_cache not in ('0', '1')
        or not paths or not set(paths) <= set(REPORT_PATHS)):
    raise ValueError('usage report paths unavailable')
started = start_ticks()
environment = selected_environment()
root = report_root(environment)
settings = {name: environment.get(name.encode()) == b'1' for name in OPTOUTS}
effective = EFFECTIVE_PATHS[root]
files = [file_metadata(path) if path != effective or root != 'cache-xdg' or allow_cache == '1'
         else dict(path=path, exists=None, readable=False) for path in dict.fromkeys([*paths, effective])]
if selected_environment() != environment or start_ticks() != started:
    raise ValueError('usage process changed')
print(json.dumps(dict(files=files, settings=settings, process_start_ticks=started, report_root=root)))
''')


def _local_snapshot_value(value, paths, allow_cache):
    if (type(value) is not dict or set(value) != {"files", "settings", "process_start_ticks", "report_root"}
            or type(value["process_start_ticks"]) is not int or value["process_start_ticks"] <= 0
            or type(value["settings"]) is not dict or set(value["settings"]) != set(_OPTOUT_SETTINGS)
            or any(type(setting) is not bool for setting in value["settings"].values())
            or type(value["report_root"]) is not str or value["report_root"] not in _EFFECTIVE_PATHS
            or type(value["files"]) is not list):
        raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
    paths = list(dict.fromkeys([*paths, _EFFECTIVE_PATHS[value["report_root"]]]))
    if len(value["files"]) != len(paths):
        raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
    for row, path in zip(value["files"], paths):
        if type(row) is not dict or row.get("path") != path:
            raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
        common = {"path", "exists", "readable"}
        exists, readable = row.get("exists"), row.get("readable")
        if not allow_cache and path == _EFFECTIVE_PATHS["cache-xdg"] and (exists is not None or readable is not False):
            raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
        if readable is True:
            common.add("directory_chain")
            chain = row.get("directory_chain")
            directory_count = path.count("/")
            if (type(chain) is not list or not 1 <= len(chain) <= directory_count
                    or exists is True and len(chain) != directory_count
                    or any(type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None for digest in chain)):
                raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
        if exists is True and readable is True:
            if (set(row) != common | {"bytes", "lines", "sha256"}
                    or type(row["bytes"]) is not int or type(row["lines"]) is not int
                    or not 0 <= row["lines"] <= row["bytes"] <= 4 * 1024 * 1024
                    or type(row["sha256"]) is not str or re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is None):
                raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
        elif set(row) != common or not (exists is False and readable is True or exists is None and readable is False):
            raise ValueError("USAGE_LOCAL_SNAPSHOT_INVALID")
    return value


def _stable_local_reports(before, after):
    """Treat lost observations as gaps while allowing genuine creation."""
    old = {row["path"]: row for row in before["files"]}
    stable_files = []
    for row in after["files"]:
        earlier = old[row["path"]]
        prior_chain = earlier.get("directory_chain", [])
        chain = row.get("directory_chain", [])
        if (chain[:len(prior_chain)] != prior_chain
                or earlier["exists"] is True and row["exists"] is False):
            row = {**row, "readable": False}
        stable_files.append(row)
    return new_reports(before, {"files": stable_files})


def _usage_source(ctx):
    """Only separately reviewed release profiles may add the fixed XDG path."""
    try:
        source = get_provider(ctx.env.engine.name).evidence_contract("usage_report_source", ctx.env)
    except (AttributeError, ValueError):
        return None
    if (type(source) is not dict or source.keys() != {"source_root", "file_sha256", "filename"}
            or source["source_root"] != _USAGE_SOURCE_ROOT or source["filename"] != "usage_stats.json"
            or type(source["file_sha256"]) is not dict or source["file_sha256"].keys() != set(_USAGE_SOURCE_FILES)
            or any(type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None
                   for digest in source["file_sha256"].values())):
        return None
    return source


def _verify_usage_launch(transport, target, startup):
    pod = transport._verify(capture_snapshot=True)["engine"]
    container_name = transport.binding["engine"]["container"]
    container, = [row for row in pod["spec"]["containers"] if row["name"] == container_name]
    _verify_launch(target, container, startup)


def _verify_usage_source(transport, source, target, startup):
    if transport is None:
        return False
    try:
        if startup["source_root"] != source["source_root"]:
            return False
        _verify_usage_launch(transport, target, startup)
        files = [{"root": 0, "relative": name} for name in _USAGE_SOURCE_FILES]
        rows = transport.engine_supply_hashes([source["source_root"]], files)
        _verify_usage_launch(transport, target, startup)
        observed = {row["relative"]: row["sha256"] for row in rows if row["state"] == "hashed"
                    and type(row["root"]) is int and row["root"] == 0}
        report = transport.report()
        return (len(rows) == len(observed) and observed == source["file_sha256"]
                and not report["collection_gap"] and not report["cleanup_errors"])
    except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
        return False


def _complete_control(result):
    body = result.get("body")
    return (result.get("id") == _ControlAlias.ORIGINAL and result.get("method") == "POST" and result.get("mode") == "json"
            and result.get("capture_complete") is True and result.get("eof") is True
            and result.get("utf8_valid") is True and result.get("body_truncated") is False and type(body) is str
            and type(result.get("body_bytes")) is int and result["body_bytes"] == len(body.encode("utf-8"))
            and result.get("body_sha256") == sha256_hex(body.encode("utf-8")))


class _UsageReader(KubernetesLifecycleReader):
    def __init__(self, target, env, *, paths=None, sink=False, binary, duration, runner, local_transport=None,
                 prefixes=None):
        self._local = local_transport is not None
        if self._local:
            if (sink or local_transport.profile != USAGE_CONTROL_PROFILE or target.get("lab") is not False
                    or local_transport.target_sha256 != sha256_hex(canonical_json(target))):
                raise KubernetesCaptureError()
            binding = local_transport.binding
        else:
            binding = validate_capture_target(target, env)
        self._initialize(binding, target, {"session": _NO_SESSION}, binary=binary, duration=duration, runner=runner)
        self._paths, self._sink = list(paths or []), sink
        program, args = _snapshot_command(self._paths, prefixes)
        self.snapshot_argv = ["python3", "-c", program, *args]
        if self._local:
            program = _LOCAL_READER
        self.programs["usage"] = program
        self.wrapped["usage"] = supervised_source(program)

    def _request(self, args, *, deadline, cap):
        raw = super()._request(args, deadline=deadline, cap=cap)
        if self._local and args[1:3] == ["get", "pod"]:
            pod = strict_json(raw, limit=cap)
            spec = pod.get("spec", {})
            mounts = _mounts(pod, self.binding["engine"])
            proc_view = (normpath("/" + row["mount_path"].lstrip("/")) for row in mounts)
            if (spec.get("hostPID", False) is not False
                    or any(path in {"/", "/proc"} or path.startswith("/proc/") for path in proc_view)):
                self.gap = True
                raise KubernetesCaptureError()
        return raw

    def _program(self, container, argv, max_bytes):
        if container != self.binding["engine"]["container"] or type(argv) is not list:
            raise KubernetesCaptureError()
        if self._local:
            if (not self._sink and self._paths and set(self._paths) <= _REPORT_PATHS and max_bytes == _SNAPSHOT_BYTES
                    and len(argv) == 5 + len(self._paths) and argv[4] in ("0", "1")
                    and argv == ["python3", "-c", _LOCAL_READER, ",".join(_OPTOUT_SETTINGS), argv[4], *self._paths]):
                return "usage"
            raise KubernetesCaptureError()
        if (not self._sink and self._paths and max_bytes == _SNAPSHOT_BYTES
                and argv == self.snapshot_argv):
            return "usage"
        if (self._sink and max_bytes == 2 * MAX_BYTES
                and argv == ["python3", "-c", _FILE_READER, SINK_PATH, str(MAX_BYTES)]):
            return "file"
        raise KubernetesCaptureError()


class _ControlAlias:
    """원 기능 대조 식별자(`func|POST /v1/completions`)를 고정 probe가 받는 `usage-control`로 바꿔 보내고 결과에서 되돌린다."""
    ORIGINAL, ALIAS = "func|POST /v1/completions", "usage-control"

    def __init__(self, transport):
        self._transport = transport

    def __getattr__(self, name):
        return getattr(self._transport, name)

    def __call__(self, network, requests, timeout):
        sent = [{**r, "id": self.ALIAS} if type(r) is dict and r.get("id") == self.ORIGINAL else r for r in requests]
        rows = self._transport(network, sent, timeout)
        return [{**row, "id": self.ORIGINAL} if type(row) is dict and row.get("id") == self.ALIAS else row for row in rows]


class KubernetesUsageReportExecutor(UsageReportExecutor):
    version = UsageReportExecutor.version + "+k8s.1"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ, docker=object())
        self.runner, self.expected_target = runner, expected_target_sha256

    def validate_policy(self, check, values):
        if type(self.cfg) is not dict or set(self.cfg) - CONFIG_KEYS:
            raise ValueError("KUBERNETES_USAGE_CONFIGURATION_INVALID")
        super().validate_policy(check, values)
        if "local_observation_only" in self.cfg and (
                self.cfg["local_observation_only"] is not True or check.id != "OBS-04"
                or getattr(check, "collection_profile", None) != SILENCE_PROFILE or "sink_target" in self.cfg
                or set(values) != {"usage_allowed", "usage_report_paths", "usage_settings_fresh_process"}
                or values["usage_allowed"] is not False
                or not set(values["usage_report_paths"]) <= _REPORT_PATHS):
            raise ValueError("KUBERNETES_USAGE_LOCAL_SCOPE_UNSUPPORTED")

    def _validate_targets(self, target, sink, env, *, expected_target_sha256=None):
        local = self.cfg.get("local_observation_only") is True
        pins = [pin for pin in (self.expected_target, expected_target_sha256) if pin is not None]
        if (target.get("lab") is not (not local) or local and not pins
                or sink is not None and sink.get("run_id") != target["kubernetes"]["run_id"]):
            raise ValueError
        if pins:
            digest = sha256_hex(canonical_json(target))
            if any(pin != digest for pin in pins):
                raise ValueError
        if sink is not None:
            validate_capture_target(sink, env)
        return local

    def _transport(self, target, env, duration):
        return KubernetesHTTP(target, env, duration=duration, binary=self.cfg.get("kubectl_binary", "kubectl"),
                              runner=self.runner, profile=USAGE_CONTROL_PROFILE)

    def _read_local_target(self):
        from guard.adapters.vllm_file_read_sensor import dependency

        target = strict_json(dependency(self.cfg["target"], None), limit=65536)
        if type(target) is not dict:
            raise ValueError
        return target

    def prepared_local_observation(self, check, env, values, *, expected_target_sha256=None):
        """Qualify only the saved OBS-04 local target declaration, without a collection."""
        try:
            self.validate_policy(check, values)
            if self.cfg.get("local_observation_only") is not True:
                raise ValueError
            target = self._read_local_target()
            self._validate_targets(target, None, env, expected_target_sha256=expected_target_sha256)
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_USAGE_CONFIGURATION_INVALID") from None
        try:
            self._transport(target, env, self.cfg.get("max_duration_seconds", 120))
        except KubernetesBindingError as error:
            raise ValueError(str(error)) from None
        except OverflowError:
            raise ValueError("KUBERNETES_USAGE_CONFIGURATION_INVALID") from None

    def run(self, check, env, policy_values, adapter, workdir):
        try:
            if getattr(check, "collection_profile", None) not in PROFILES or adapter is None:
                raise ValueError
            self.validate_policy(check, policy_values)
            target = (self._read_local_target() if self.cfg.get("local_observation_only") is True
                      else load_bounded_json(self.cfg["target"]))
            sink = load_bounded_json(self.cfg["sink_target"]) if "sink_target" in self.cfg else None
            local = self._validate_targets(target, sink, env)
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_USAGE_CONFIGURATION_INVALID") from None
        duration = self.cfg.get("max_duration_seconds", 120)
        transport = self._transport(target, env, duration)
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
                  timeout=10.0, allow_mutating=False, keys={}, secrets=[], cprobe=_ControlAlias(transport), workdir=workdir, started=utcnow())
        ctx._usage_duration = duration
        if local:
            ctx.http_probe_profile = USAGE_CONTROL_PROFILE
        else:
            ctx._usage_engine_target = {"schema": "guard-kubernetes-lifecycle-target/1", "run_id": target["kubernetes"]["run_id"],
                "environment_ref": env.ref().model_dump(mode="json"), "lab": True,
                "kubernetes": {"context": target["kubernetes"]["context"], "engine": target["kubernetes"]["engine"]}}
            ctx._usage_sink_target = sink
        draft = self._finish(ctx)
        transport_report = transport.report()
        draft.cleanup_errors.extend(transport_report["cleanup_errors"])
        if local:
            draft.cleanup_errors.extend(ctx._usage_local_cleanup)
            ceiling = min((draft.data_origin, DataOrigin(transport.process_observation_origin)),
                          key=ORIGIN_STRENGTH.get)
            if draft.status != ExecStatus.COMPLETED or transport_report["collection_gap"] or draft.cleanup_errors:
                draft.status = ExecStatus.ABORTED
                draft.abort_reason = draft.abort_reason or "KUBERNETES_USAGE_LOCAL_COLLECTION_INCOMPLETE"
                ceiling = min(ceiling, DataOrigin.DECLARED, key=ORIGIN_STRENGTH.get)
            draft.data_origin = ceiling
        return draft

    def collect(self, ctx):
        if self.cfg.get("local_observation_only") is not True:
            return super().collect(ctx)

        requests, _ = functional_requests(ctx, via="engine")
        if len(requests) != 1:
            raise ValueError("KUBERNETES_USAGE_LOCAL_CONTROL_UNAVAILABLE")
        transport = ctx.cprobe._transport
        policy_paths = ctx.policy_values["usage_report_paths"]
        reader = self._reader(ctx, ctx.target, paths=policy_paths, local_transport=transport)
        source = _usage_source(ctx)
        source_reader = startup = None
        if source:
            try:
                startup = _source_profile(ctx.adapter, ctx.env)
                source_reader = KubernetesHTTP(ctx.target, ctx.env, duration=ctx._usage_duration,
                    binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self.runner,
                    profile=ENGINE_CALLBACK_PROFILE, clock=transport.clock)
                source_reader.deadline = transport.deadline
            except (AttributeError, TypeError, ValueError, KubernetesBindingError):
                pass
        qualified = _verify_usage_source(source_reader, source, ctx.target, startup)
        source_gaps = []
        before, before_status, before_failure = self._local_snapshot(reader, policy_paths, qualified, source_gaps)
        snapshot_status = {"before": before_status}
        snapshot_failures = {"before": before_failure} if before_failure is not None else {}
        after = None
        effective = _EFFECTIVE_PATHS[before["report_root"]] if before else None
        paths = list(dict.fromkeys([*policy_paths, *([effective] if effective else [])]))
        before_effective = next((row for row in before["files"] if row["path"] == effective), None) if before else None
        attempted = bool(before_effective and before_effective["readable"] and all(before["settings"].values()))
        outcome = "no_response"
        if attempted:
            result = bounded_probe(ctx, ctx.trusted_net, requests[0])
            if _complete_control(result):
                outcome = control_state(result, ctx.api_model, "/v1/completions")
            wait = self.cfg.get("usage_wait_seconds", 5)
            if wait < transport.deadline - transport.clock():
                time.sleep(wait)
                if effective == _EFFECTIVE_PATHS["cache-xdg"]:
                    qualified = _verify_usage_source(source_reader, source, ctx.target, startup)
                after, snapshot_status["after"], after_failure = self._local_snapshot(
                    reader, policy_paths, qualified, source_gaps)
                if after_failure is not None:
                    snapshot_failures["after"] = after_failure

        diagnostics = sorted(set(source_gaps))
        if before is None:
            diagnostics.append("USAGE_REPORT_BASELINE_UNAVAILABLE")
        elif not all(before["settings"].values()):
            diagnostics.append("USAGE_OPTOUT_UNVERIFIED")
        elif not before_effective["readable"]:
            diagnostics.append("USAGE_EFFECTIVE_REPORT_PATH_UNAVAILABLE")
        if attempted and after is None:
            diagnostics.append("USAGE_REPORT_AFTER_UNAVAILABLE")
        if attempted and outcome != "pass":
            diagnostics.append("USAGE_NORMAL_CONTROL_UNVERIFIED")
        owner_stable = bool(before and after and before["process_start_ticks"] == after["process_start_ticks"])
        settings_stable = bool(owner_stable and before["settings"] == after["settings"]
                               and before["report_root"] == after["report_root"])
        if before and after and not settings_stable:
            diagnostics.append("USAGE_PROCESS_OR_SETTINGS_CHANGED")
        fresh, gaps = _stable_local_reports(before, after) if settings_stable and outcome == "pass" else ([], paths)
        if gaps:
            diagnostics.append("USAGE_LOCAL_PATHS_UNAVAILABLE")
        reader_report = reader.report()
        ctx._usage_local_cleanup = (["USAGE_READER_PARENT_CLEANUP_UNVERIFIED"]
                                    if reader_report.get("parent_cleanup_unverified") else [])
        if source_reader is not None:
            ctx._usage_local_cleanup.extend(source_reader.report()["cleanup_errors"])
        observed_paths = set(paths) - set(gaps)
        effective_verified = effective in observed_paths
        if attempted and settings_stable and outcome == "pass" and not effective_verified:
            diagnostics.append("USAGE_EFFECTIVE_REPORT_PATH_UNAVAILABLE")
        ctx._usage_gap = bool(not effective_verified or reader.gap or ctx._usage_local_cleanup)
        reports = [{"path_sha256": sha256_hex(row["path"].encode()), "bytes": row["bytes"],
                    "lines": row["lines"], "sha256": row["sha256"]} for row in fresh]
        settings = {name: "1" for name, enabled in before["settings"].items() if enabled} if before else None
        observation_values = {
            "usage_policy_forbidden": (True, True),
            "usage_effective_settings": (settings, settings is not None),
            "usage_settings_fresh_process": (None, False),
            "usage_new_local_reports": (reports if reports or not gaps else None, bool(reports or not gaps)),
            "usage_sink_receipts": (None, False),
            "usage_send_sensor_available": (False, True),
            "functional_probes_planned": (["inference"], True),
            "functional_probe_results": ({"inference": outcome}, True),
        }
        observations = {key: {"value": value, "collected": collected}
                        for key, (value, collected) in observation_values.items()}
        normal = {"attempted": attempted, "succeeded": outcome == "pass" if attempted else None,
                  "detail": "One fixed inference between local report snapshots."}
        challenges = [{"step": step, "attempted": False} for step in ctx.check.challenge_steps]
        summary = {"profile": SILENCE_PROFILE, "local_observation_only": True, "target_sha256": transport.target_sha256,
                   "diagnostic_codes": diagnostics, "owner_and_optouts_stable": settings_stable,
                   "effective_report_path_observed": effective_verified,
                   "effective_report_path_sha256": sha256_hex(effective.encode()) if effective_verified else None,
                   "effective_source_contract_sha256": (sha256_hex(canonical_json(source))
                       if effective_verified and effective == _EFFECTIVE_PATHS["cache-xdg"] else None),
                   "local_paths_complete": not gaps, "unverified_paths": [sha256_hex(path.encode()) for path in gaps],
                   "observed_paths": sorted(sha256_hex(path.encode()) for path in observed_paths),
                   "local_snapshot_status": snapshot_status,
                   **({"local_snapshot_failure_kind": snapshot_failures} if snapshot_failures else {}),
                   "local_snapshot_presence": {
                       stage: {sha256_hex(row["path"].encode()): row["exists"] for row in snapshot["files"]}
                       for stage, snapshot in (("before", before), ("after", after)) if snapshot is not None},
                   "send_observation_available": False, "fresh_process_verified": False, "settings_subject": "engine-container-pid-1",
                   "fixture": {"profile": SILENCE_PROFILE, "scope": "owned-local-report-files-and-one-normal-inference",
                               "reader_sha256": sha256_hex(_LOCAL_READER.encode()),
                               "probe_source_sha256": transport.source_sha256}}
        return observations, normal, challenges, summary

    def _local_snapshot(self, reader, paths, qualified, source_gaps):
        prior_reads = len(reader.rows)
        try:
            result = reader.exec_bounded(reader.binding["engine"]["container"],
                                         ["python3", "-c", _LOCAL_READER, ",".join(_OPTOUT_SETTINGS),
                                          "1" if qualified else "0", *paths],
                                         timeout=5, max_bytes=_SNAPSHOT_BYTES)
        except (KubernetesCaptureError, AttributeError, ValueError, TypeError, OSError) as error:
            if (len(reader.rows) > prior_reads and reader.rows[-1]["program"] == "usage"
                    and "KUBERNETES_READ_TRANSPORT_UNAVAILABLE" in reader.rows[-1]["diagnostic_codes"]):
                failure = error.failure_kind if isinstance(error, KubernetesCaptureError) else None
                kind = failure.value if type(failure) is KubernetesCaptureFailureKind else None
                return None, "USAGE_LOCAL_TRANSPORT_UNAVAILABLE", kind
            return None, "USAGE_LOCAL_PRE_READ_UNVERIFIED", None
        if getattr(result, "owner_binding_verified", None) is not True:
            return None, "USAGE_LOCAL_OWNER_AFTER_UNVERIFIED", None
        try:
            value = _local_snapshot_value(strict_json(result.stdout.encode(), limit=_SNAPSHOT_BYTES), paths, qualified)
            if value["report_root"] == "cache-xdg" and not qualified:
                source_gaps.append("USAGE_EFFECTIVE_REPORT_SOURCE_UNVERIFIED")
            return value, "USAGE_LOCAL_SNAPSHOT_VERIFIED", None
        except (AttributeError, ValueError, TypeError, OSError):
            return None, "USAGE_LOCAL_PROJECTION_INVALID", None

    def _reader(self, ctx, target, **kwargs):
        return _UsageReader(target, ctx.env, binary=self.cfg.get("kubectl_binary", "kubectl"),
                            duration=ctx._usage_duration, runner=self.runner, **kwargs)

    def _snapshot(self, ctx, *, prefixes=None):
        reader = self._reader(ctx, ctx._usage_engine_target, paths=ctx.policy_values["usage_report_paths"], prefixes=prefixes)
        result = reader.exec_bounded(reader.binding["engine"]["container"], reader.snapshot_argv, timeout=5, max_bytes=_SNAPSHOT_BYTES)
        if result.owner_binding_verified is not True:
            raise KubernetesCaptureError()
        value = strict_json(result.stdout.encode(), limit=_SNAPSHOT_BYTES)
        if type(value) is not dict or set(value) != {"files", "settings", "process_start_ticks"}:
            raise ValueError("USAGE_SNAPSHOT_INVALID")
        return value

    def _sink_rows(self, ctx):
        if ctx._usage_sink_target is None:
            return None
        reader = self._reader(ctx, ctx._usage_sink_target, sink=True)
        result = reader.exec_bounded(reader.binding["engine"]["container"], ["python3", "-c", _FILE_READER, SINK_PATH, str(MAX_BYTES)],
                                     timeout=5, max_bytes=2 * MAX_BYTES)
        raw, identity = validate_file_result(result, max_bytes=MAX_BYTES)
        if result.owner_binding_verified is not True:
            raise KubernetesCaptureError()
        return _sink_snapshot(raw, result.owner_before, identity)


def kubernetes_boundary_egress(cfg, *, environ=None, expected_target_sha256=None):
    """boundary_egress:kubernetes 진입점. usage 프로필(OBS-04·05)만 이 실행기로, 나머지(PLT-07)는 기존 소비자로 보낸다."""
    from .kubernetes_egress_product import KubernetesEgressProductExecutor

    class Dispatch(KubernetesEgressProductExecutor):
        def validate_policy(self, check, values):
            if getattr(check, "collection_profile", None) in PROFILES:
                return KubernetesUsageReportExecutor(self.cfg, environ=environ).validate_policy(check, values)
            return super().validate_policy(check, values)

        def prepared_local_observation(self, check, env, values, *, expected_target_sha256=None):
            executor = KubernetesUsageReportExecutor(self.cfg, runner=self.runner, environ=environ,
                                                     expected_target_sha256=self.expected_target)
            executor.prepared_local_observation(check, env, values, expected_target_sha256=expected_target_sha256)

        def run(self, check, env, policy_values, adapter, workdir):
            if getattr(check, "collection_profile", None) not in PROFILES:
                return super().run(check, env, policy_values, adapter, workdir)
            executor = KubernetesUsageReportExecutor(self.cfg, runner=self.runner, environ=environ,
                                                     expected_target_sha256=expected_target_sha256)
            draft = executor.run(check, env, policy_values, adapter, workdir)
            self.version = executor.version
            return draft

    return Dispatch(cfg, environ=environ, expected_target_sha256=expected_target_sha256)
