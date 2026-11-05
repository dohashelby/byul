"""OBS-04·OBS-05 Kubernetes 전송. 원 usage_report의 정책·전후 비교·수신 기록 형식·관측·판정은 그대로 쓴다.

바뀌는 것은 읽기 두 곳뿐이다.
- 엔진 쪽 보고 파일과 PID 1 설정: Docker helper 대신 검증된 엔진 Pod에서 고정 읽기 프로그램을 앞뒤 소유 확인과 함께 실행한다.
- 소유 수신지 기록: 같은 run의 소유 수신 Pod 고정 경로(`/guard-usage/receipts.jsonl`)를 고정 파일 읽기로 읽는다.
수신 기록의 생산자는 미인증이다. timeout이나 수신 실패만으로 비전송을 주장하지 않는 원 규칙도 그대로다.
"""
from __future__ import annotations

from pathlib import Path

from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_binding import validate_capture_target
from guard.runtime.kubernetes_http import KubernetesHTTP, USAGE_CONTROL_PROFILE
from .http_common import Ctx
from .kubernetes_lifecycle_capture import KubernetesCaptureError, KubernetesLifecycleReader, supervised_source
from .owned_capture import _FILE_READER, MAX_BYTES, validate_file_result
from .usage_report import PROFILES, SETTINGS, START_SCHEMA, UsageReportExecutor, _READER
from guard.models import utcnow

SINK_PATH = "/guard-usage/receipts.jsonl"
CONFIG_KEYS = {"target", "sink_target", "kubectl_binary", "max_duration_seconds", "usage_wait_seconds"}
_SNAPSHOT_BYTES = 262144
# 고정 읽기만 하므로 lifecycle 센서 세션이 없다. reader 초기화에 쓰는 자리 값이며 어떤 기록 경로도 이 값으로 열지 않는다.
_NO_SESSION = "0" * 32


class _UsageReader(KubernetesLifecycleReader):
    def __init__(self, target, env, *, paths=None, sink=False, binary, duration, runner):
        self._initialize(validate_capture_target(target, env), target, {"session": _NO_SESSION},
                         binary=binary, duration=duration, runner=runner)
        self._paths, self._sink = list(paths or []), sink
        self.programs["usage"] = _READER
        self.wrapped["usage"] = supervised_source(_READER)

    def _program(self, container, argv, max_bytes):
        if container != self.binding["engine"]["container"] or type(argv) is not list:
            raise KubernetesCaptureError()
        if (not self._sink and self._paths and max_bytes == _SNAPSHOT_BYTES
                and argv == ["python3", "-c", _READER, ",".join(SETTINGS), *self._paths]):
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

    def run(self, check, env, policy_values, adapter, workdir):
        try:
            if getattr(check, "collection_profile", None) not in PROFILES or adapter is None:
                raise ValueError
            self.validate_policy(check, policy_values)
            def load(name):
                with Path(self.cfg[name]).open("rb") as stream:
                    return strict_json(stream.read(65537), limit=65536)
            target = load("target")
            sink = load("sink_target") if "sink_target" in self.cfg else None
            if (target.get("lab") is not True or sink is not None and sink.get("run_id") != target["kubernetes"]["run_id"]
                    or self.expected_target is not None and sha256_hex(canonical_json(target)) != self.expected_target):
                raise ValueError
            if sink is not None:
                validate_capture_target(sink, env)
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_USAGE_CONFIGURATION_INVALID") from None
        duration = self.cfg.get("max_duration_seconds", 120)
        transport = KubernetesHTTP(target, env, duration=duration, binary=self.cfg.get("kubectl_binary", "kubectl"),
                                   runner=self.runner, profile=USAGE_CONTROL_PROFILE)
        engine_target = {"schema": "guard-kubernetes-lifecycle-target/1", "run_id": target["kubernetes"]["run_id"],
                         "environment_ref": env.ref().model_dump(mode="json"), "lab": True,
                         "kubernetes": {"context": target["kubernetes"]["context"], "engine": target["kubernetes"]["engine"]}}
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
                  timeout=10.0, allow_mutating=False, keys={}, secrets=[], cprobe=_ControlAlias(transport), workdir=workdir, started=utcnow())
        ctx._usage_engine_target, ctx._usage_sink_target, ctx._usage_duration = engine_target, sink, duration
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        return draft

    def _reader(self, ctx, target, **kwargs):
        return _UsageReader(target, ctx.env, binary=self.cfg.get("kubectl_binary", "kubectl"),
                            duration=ctx._usage_duration, runner=self.runner, **kwargs)

    def _snapshot(self, ctx):
        reader = self._reader(ctx, ctx._usage_engine_target, paths=ctx.policy_values["usage_report_paths"])
        result = reader.exec_bounded(reader.binding["engine"]["container"],
                                     ["python3", "-c", _READER, ",".join(SETTINGS), *ctx.policy_values["usage_report_paths"]],
                                     timeout=5, max_bytes=_SNAPSHOT_BYTES)
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
        raw, _ = validate_file_result(result, max_bytes=MAX_BYTES)
        if result.owner_binding_verified is not True:
            raise KubernetesCaptureError()
        # 원 read_owned_jsonl과 같은 경계: 1..64행, 행마다 16 KiB 이하 JSON 객체.
        lines = raw.split(b"\n")
        if lines[-1] == b"":
            lines.pop()
        if not 1 <= len(lines) <= 64 or any(not line.strip() or len(line) > 16384 for line in lines):
            raise ValueError("CAPTURE_JSONL_BOUND")
        rows = [strict_json(line, limit=16384) for line in lines]
        if any(type(row) is not dict for row in rows) or rows[0] != {"schema": START_SCHEMA}:
            raise ValueError("USAGE_SINK_RECORD_INVALID")
        return rows


def kubernetes_boundary_egress(cfg, *, environ=None, expected_target_sha256=None):
    """boundary_egress:kubernetes 진입점. usage 프로필(OBS-04·05)만 이 실행기로, 나머지(PLT-07)는 기존 소비자로 보낸다."""
    from .kubernetes_egress_product import KubernetesEgressProductExecutor

    class Dispatch(KubernetesEgressProductExecutor):
        def validate_policy(self, check, values):
            if getattr(check, "collection_profile", None) in PROFILES:
                return KubernetesUsageReportExecutor(self.cfg, environ=environ).validate_policy(check, values)
            return super().validate_policy(check, values)

        def run(self, check, env, policy_values, adapter, workdir):
            if getattr(check, "collection_profile", None) not in PROFILES:
                return super().run(check, env, policy_values, adapter, workdir)
            executor = KubernetesUsageReportExecutor(self.cfg, runner=self.runner, environ=environ,
                                                     expected_target_sha256=expected_target_sha256)
            draft = executor.run(check, env, policy_values, adapter, workdir)
            self.version = executor.version
            return draft

    return Dispatch(cfg, environ=environ, expected_target_sha256=expected_target_sha256)
