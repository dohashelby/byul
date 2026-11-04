"""REQ-13 Kubernetes 전송. 기존 응답 내용 사례·관측·판정은 그대로 쓰고 전송만 소유 Pod probe로 바꾼다.

엔진 대상만 지원한다. Gateway 대상은 Kubernetes 바인딩이 없어 수집 공백으로 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.kubernetes_http import RESPONSE_CONTENT_PROFILE, KubernetesHTTP
from .http_common import Ctx
from .response_content import ResponseContentExecutor

_SETTINGS = {"target", "endpoint", "kubectl_binary", "request_timeout", "max_duration_seconds"}


class KubernetesResponseContentExecutor(ResponseContentExecutor):
    version = "0.2.1+k8s.1"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def validate_configuration(self, check):
        if type(self.cfg) is not dict or set(self.cfg) - _SETTINGS:
            raise ValueError("KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID")
        settings = {k: v for k, v in self.cfg.items() if k not in {"target", "kubectl_binary", "max_duration_seconds"}}
        saved, self.cfg = self.cfg, settings
        try:
            super().validate_configuration(check)
        finally:
            self.cfg = saved

    def run(self, check, env, policy_values, adapter, workdir):
        try:
            if adapter is None:
                raise ValueError
            self.validate_policy(check, policy_values)
            target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target)) != self._expected_target_sha256:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID") from None
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 120),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=RESPONSE_CONTENT_PROFILE)
        # 엔진 API 키를 쓰지 않는 소유 lab 경로다. 키가 있으면 전송이 Authorization 헤더를 거부한다.
        # 원 실행기가 쓰는 망 이름만 전송 망으로 바꾼 사본. 전송에는 원 target을 그대로 넘긴다.
        # Gateway 대상은 Kubernetes 바인딩이 없으므로 원 실행기가 "target URL unavailable" 공백으로 남긴다.
        view = {k: v for k, v in target.items() if k != "gateway"}
        view["origins"] = {"trusted": {"network": "kubernetes-probe"}, "external": {"network": "kubernetes-probe"}}
        ctx = Ctx(check=check, env=env, adapter=adapter, target=view, policy_values=policy_values,
                  timeout=float(self.cfg.get("request_timeout", 10)), allow_mutating=False, keys={}, secrets=[],
                  cprobe=transport, workdir=workdir, started=utcnow())
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft
