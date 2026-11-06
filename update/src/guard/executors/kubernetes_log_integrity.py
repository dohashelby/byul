"""OBS-09·OBS-11 Kubernetes 전송. 원 log_integrity의 사례·분석·판정은 그대로 쓴다.

바뀌는 것은 세 가지뿐이다.
- 엔진 신원과 로깅 설정: Docker inspect 대신 검증된 엔진 Pod 사양(인자·리터럴 환경변수·이미지·시작 시각).
- 엔진 로그: `kubectl logs --timestamps --since-time`. 끝 시각은 줄의 시각으로 자른다.
  CRI 로그 계층(containerd)은 LF 앞의 CR 하나를 지운다(k3d 실측 2026-11-06). native formatter의 연속 표식이
  그 CR이므로, 시험 동안 `kubectl attach --quiet`로 받은 원 stdout 바이트를 CRI 줄과 한 줄씩 맞춰 쓴다.
  attach 줄에서 끝 CR 하나를 뗀 값이 CRI 줄과 같고 그 위치가 한 곳뿐일 때만 CRI 시각 + attach 바이트로 잇는다.
  맞추지 못하면 CRI 줄만 두고 수집 불완전으로 남긴다(경계를 추정하지 않는다).
- OBS-11 상태 기록: 소유 probe Pod의 고정 위치 파일.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.kubernetes_http import LOG_INTEGRITY_PROFILE, KubernetesBindingError, KubernetesHTTP
from . import log_collection_gap
from .http_common import Ctx
from .log_integrity import _ENV, LogIntegrityExecutor, _pinned_image
from .log_integrity_parser import MAX_CAPTURE_BYTES

_SETTINGS = {"target", "kubectl_binary", "request_timeout", "max_duration_seconds", "max_total_requests",
             "max_id_bytes", "max_events", "endpoint"}
_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})) ")
ATTACH_SETTLE_SECONDS = 1.0
SOURCE_PROFILE = "kubernetes-cri-time-attach-bytes-v1"


def align_attach(lines, attach_raw):
    """CRI 줄(시각 포함, LF 끝)과 attach stdout 바이트를 맞춘다. (잇은 텍스트 또는 None, 진단)."""
    diag = {"profile": SOURCE_PROFILE, "cri_lines": len(lines), "attach_lines": None, "offsets": 0, "aligned": False,
            "cr_restored_lines": 0}
    if type(attach_raw) is not bytes or not lines:
        diag["reason"] = "attach stdout or CRI window lines unavailable"
        return None, diag
    decoded = attach_raw.decode("utf-8", "replace")
    physical = decoded.split("\n")[:-1]  # 끝나지 않은 마지막 조각은 쓰지 않는다
    diag["attach_lines"] = len(physical)
    stamped = []
    for line in lines:
        found = _STAMP.match(line)
        if found is None or not line.endswith("\n"):
            diag["reason"] = "CRI window line without time or LF"
            return None, diag
        stamped.append((line[:found.end()], line[found.end():-1]))
    normal = [item[:-1] if item.endswith("\r") else item for item in physical]
    width = len(stamped)
    offsets = [k for k in range(len(normal) - width + 1)
               if all(normal[k + i] == stamped[i][1] for i in range(width))]
    diag["offsets"] = len(offsets)
    if len(offsets) != 1:
        diag["reason"] = "CRI window lines are not one contiguous run of attach stdout lines"
        return None, diag
    start = offsets[0]
    merged = [stamp + physical[start + i] + "\n" for i, (stamp, _) in enumerate(stamped)]
    diag["aligned"] = True
    diag["cr_restored_lines"] = sum(1 for i in range(width) if physical[start + i] != stamped[i][1])
    return "".join(merged), diag


def _instant(value):
    text = value.replace("Z", "+00:00")
    head, _, rest = text.partition(".")
    if rest:  # kubectl은 나노초까지 준다. datetime은 마이크로초까지만 받는다.
        digits = re.match(r"\d+", rest).group()
        text = head + "." + digits[:6] + rest[len(digits):]
    return datetime.fromisoformat(text)


class KubernetesLogIntegrityExecutor(LogIntegrityExecutor):
    version = "0.3.1+k8s.2"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ, docker=object(), capture=None)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def validate_policy(self, check, values):
        if type(self.cfg) is not dict or set(self.cfg) - _SETTINGS:
            raise ValueError("KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID")
        if getattr(check, "collection_profile", None) == log_collection_gap.PROFILE:
            log_collection_gap.validate_policy(values)  # 상태 기록 위치는 probe Pod 고정 경로라 컨테이너 설정이 없다
            return
        super().validate_policy(check, values)

    def run(self, check, env, policy_values, adapter, workdir):
        try:
            if adapter is None:
                raise ValueError
            self.validate_policy(check, policy_values)
            target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target)) != self._expected_target_sha256:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID") from None
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 300),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=LOG_INTEGRITY_PROFILE)
        view = {k: v for k, v in target.items() if k != "gateway"}
        view["origins"] = {"trusted": {"network": "kubernetes-probe"}, "external": {"network": "kubernetes-probe"}}
        view["run_id"] = target["kubernetes"]["run_id"]
        ctx = Ctx(check=check, env=env, adapter=adapter, target=view, policy_values=policy_values,
                  timeout=float(self.cfg.get("request_timeout", 30)), allow_mutating=False, keys={}, secrets=[],
                  cprobe=transport, workdir=workdir, started=utcnow())
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft

    def _snapshot(self, ctx, launch, deadline):
        """원 Docker 조건과 같은 항목을 Pod 사양으로 확인한다. Pod 소유·run 표식은 전송 바인딩이 이미 검증한다."""
        try:
            if deadline <= time.monotonic():
                return None
            actual = ctx.cprobe.engine_log_identity()
            environ, argv, expected = actual["env"], actual["argv"], ctx.env.engine.image_digest
            digest = expected.rsplit("@", 1)[-1] if _pinned_image(expected) else None
            image_id = actual["image_id"]
            launch_argv = launch.get("argv")
            if (digest is None or ctx.target["engine"].get("image_digest") != expected or actual["image"] != expected
                    or type(image_id) is not str or not image_id.endswith("@" + digest)
                    or not actual["container_id"] or not actual["started_at"]
                    or type(launch_argv) is not list or argv[-len(launch_argv):] != launch_argv
                    or argv.count("--enable-log-requests") != 1 or argv.count("--max-log-len") != 1
                    or argv.index("--max-log-len") + 1 >= len(argv) or argv[argv.index("--max-log-len") + 1] != "512"
                    or "--log-config-file" in argv or "--disable-log-requests" in argv or "--logging-config" in argv
                    or ("--log-level" in argv and (argv.count("--log-level") != 1 or argv.index("--log-level") + 1 >= len(argv)
                        or argv[argv.index("--log-level") + 1] != "INFO"))
                    or any(environ.get(key) != value for key, value in _ENV.items())
                    or environ.get("VLLM_LOGGING_CONFIG_PATH") not in (None, "")):
                return None
            return {"container_id": actual["container_id"], "started_at": actual["started_at"],
                    "image": expected, "image_id": image_id, "image_repo_digest_verified": True,
                    "logging_profile": "request-info-v1", "logging_controls_verified": True}
        except (KubernetesBindingError, KeyError, TypeError, ValueError):
            return None

    def _read(self, ctx, since, until, deadline):
        """kubectl 출력의 줄 시각으로 [since, until] 밖을 버린다. 시각 없는 줄이 있으면 불완전으로 둔다."""
        if deadline <= time.monotonic():
            return {"text": "", "returncode": None, "truncated": False, "error": "capture deadline exhausted"}
        try:
            result = ctx.cprobe.engine_logs(since, MAX_CAPTURE_BYTES)
            raw, pipe = result["stdout_bytes"], result.get("capture") or {}
            decoded = raw.decode("utf-8", "replace")
            kept, unstamped, end = [], False, _instant(until)
            # 런타임은 \n 단위로만 시각을 붙인다. \r은 메시지 안의 내용이므로 나누지 않는다(OBS-09가 보는 경계다).
            segments = decoded.split("\n")
            lines = [segment + "\n" for segment in segments[:-1]] + ([segments[-1]] if segments[-1] else [])
            for line in lines:
                found = _STAMP.match(line)
                if found is None:
                    unstamped = unstamped or bool(line.strip()); continue
                if _instant(found.group(1)) <= end:
                    kept.append(line)
            attached = self._stop_attach()
            source = {"cri_raw_bytes": len(raw), "cri_raw_sha256": hashlib.sha256(raw).hexdigest(),
                      "attach_capture": (attached or {}).get("capture")}
            merged, diag = (align_attach(kept, attached["stdout_bytes"]) if attached is not None
                            else (None, {"profile": SOURCE_PROFILE, "aligned": False, "reason": "attach stream unavailable"}))
            attach_ok = bool(attached is not None and attached.get("returncode") == 0
                             and (attached.get("capture") or {}).get("complete") is True)
            if merged is not None and not attach_ok:
                merged, diag = None, {**diag, "aligned": False, "reason": "attach stream incomplete"}
            source.update(alignment=diag)
            self._source = source
            text = merged if merged is not None else "".join(kept)
            data = text.encode("utf-8")
            retained = attached["stdout_bytes"] if merged is not None else raw
            return {"text": text, "stderr_text": "", "returncode": result["returncode"],
                    "truncated": len(raw) >= MAX_CAPTURE_BYTES or bool(pipe.get("truncated")),
                    "timed_out": bool(pipe.get("timed_out")),
                    "source_complete": pipe.get("complete") is True and not unstamped and merged is not None,
                    "pipe_capture": pipe, "decoded_stdout_bytes": len(data), "decoded_stdout_sha256": hashlib.sha256(data).hexdigest(),
                    "raw_retained_bytes": len(retained), "raw_retained_sha256": hashlib.sha256(retained).hexdigest(),
                    "stderr_present": bool((result.get("stderr_bytes") or b"").strip()) or bool((attached or {}).get("stderr_bytes", b"").strip()),
                    "window": {"since": since, "until": until},
                    "error": "kubectl logs read failed" if result["returncode"] else None,
                    "encoding": ("kubectl logs --timestamps line times joined to kubectl attach stdout bytes by exact"
                                 " one-trailing-CR line alignment, decoded as UTF-8 and clipped to the window by line time"
                                 if merged is not None else
                                 "kubectl logs --timestamps bytes decoded as UTF-8 and clipped to the window by line time;"
                                 " CRI removed one CR before each LF, so native boundaries are incomplete")}
        except Exception:
            self._stop_attach()
            return {"text": "", "returncode": None, "truncated": False, "error": "NATIVE_CAPTURE_UNAVAILABLE"}

    def _stop_attach(self):
        stream, self._attach = getattr(self, "_attach", None), None
        if stream is None:
            return None
        try:
            time.sleep(ATTACH_SETTLE_SECONDS)  # 마지막 시험 줄이 attach 스트림에 닿을 시간
            return stream.stop()
        except Exception:
            return None

    def collect(self, ctx):
        if getattr(ctx.check, "collection_profile", None) != log_collection_gap.PROFILE:
            self._source = {"alignment": {"profile": SOURCE_PROFILE, "aligned": False, "reason": "capture not reached"}}
            try:
                self._attach = ctx.cprobe.engine_attach(MAX_CAPTURE_BYTES)
            except Exception:
                self._attach = None
            try:
                obs, normal, steps, summary = super().collect(ctx)
            finally:
                if self._stop_attach() is not None:  # _read에 닿지 못한 경로의 정리
                    self._source["alignment"]["reason"] = "attach stopped without a CRI window read"
            summary["kubernetes_source"] = self._source
            return obs, normal, steps, summary
        # OBS-11: 원 log_collection_gap의 기록 검증·판정을 그대로 쓰고 읽기만 probe Pod로 바꾼다.
        self.version = log_collection_gap.VERSION + "+k8s.1"
        values = ctx.policy_values
        packet, file_sha256, diagnostics, result = None, None, [], None
        try:
            read = ctx.cprobe.read_collection_status(values["collection_session"])
            file_sha256 = read["sha256"]
            packet = log_collection_gap.validate_record(read["document"], environment_ref=ctx.env.ref().model_dump(mode="json"),
                                                        session=values["collection_session"])
            result = log_collection_gap.evaluate(packet, values)
        except Exception:
            diagnostics.append("COLLECTION_STATUS_UNAVAILABLE")
        collected = result is not None
        obs = {"collection_gap_violations": {"value": result and result["violations"], "collected": collected},
               "collection_records_unverified": {"value": result and result["unverified"], "collected": collected},
               "collection_records_missing": {"value": result and result["missing"], "collected": collected},
               "collection_normal_arrivals": {"value": result and result["normal"], "collected": collected},
               "collection_producer_authenticated": {"value": False, "collected": True},
               "collection_independent_event_verified": {"value": False, "collected": True}}
        summary = {"profile": log_collection_gap.PROFILE, "diagnostic_codes": diagnostics,
                   "record_sha256": sha256_hex(canonical_json(packet)) if packet is not None else None,
                   "file_sha256": file_sha256, "source": "owned probe Pod /guard-collection/data/<session>/collection-status.json",
                   "fixture": {"profile": log_collection_gap.PROFILE, "schema": log_collection_gap.SCHEMA}}
        normal = {"attempted": collected, "succeeded": None, "detail": "상태 기록의 정상 도착은 생산자 미인증 주장이다."}
        return obs, normal, [{"step": step, "attempted": False} for step in ctx.check.challenge_steps], summary
