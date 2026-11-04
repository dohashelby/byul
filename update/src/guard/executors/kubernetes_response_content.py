"""REQ-13 Kubernetes 전송. 기존 응답 내용 사례·관측·판정은 그대로 쓰고 전송만 소유 Pod probe로 바꾼다.

엔진 대상은 소유 probe Pod에서 엔진으로 직접 보낸다.
Gateway 대상은 `route_target`(4-Pod 경로 target)과 `allowed_key_env`를 주면 바깥 probe Pod에서 Gateway로 보낸다.
그 전송은 기존 4-Pod 경로 전송(KubernetesRoutesHTTP)이고 요청 전후로 네 Pod 세대를 다시 확인한다.
설정하지 않으면 원 실행기가 Gateway 대상을 "target URL unavailable" 공백으로 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.kubernetes_http import RESPONSE_CONTENT_PROFILE, KubernetesHTTP
from .http_common import Ctx
from .response_content import ResponseContentExecutor

_SETTINGS = {"target", "endpoint", "kubectl_binary", "request_timeout", "max_duration_seconds", "route_target", "allowed_key_env"}


class _Legs:
    """엔진 다리와 Gateway 다리를 망 이름으로 나눈다.

    원 실행기는 같은 호출자 키를 두 대상에 모두 붙인다. 이 lab 엔진은 API 키를 받지 않고 호출자 키는 Gateway 자격이므로,
    엔진 다리에서는 Authorization만 뺀다(본문·경로는 그대로). Gateway 다리는 그대로 보낸다.
    """
    def __init__(self, engine, gateway):
        self._engine, self._gateway = engine, gateway

    def __getattr__(self, name):
        return getattr(self._engine, name)

    def __call__(self, network, requests, timeout):
        from guard.runtime.kubernetes_routes import EXTERNAL
        if network == EXTERNAL:
            return self._gateway(network, requests, timeout)
        stripped = [{**r, "headers": {k: v for k, v in (r.get("headers") or {}).items() if k.lower() != "authorization"}}
                    if type(r) is dict else r for r in requests]
        return self._engine(network, stripped, timeout)


class KubernetesResponseContentExecutor(ResponseContentExecutor):
    version = "0.2.1+k8s.1"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def validate_configuration(self, check):
        if type(self.cfg) is not dict or set(self.cfg) - _SETTINGS:
            raise ValueError("KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID")
        if ("route_target" in self.cfg) != ("allowed_key_env" in self.cfg):
            raise ValueError("KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID")
        settings = {k: v for k, v in self.cfg.items() if k not in {"target", "kubectl_binary", "max_duration_seconds", "route_target", "allowed_key_env"}}
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
        gateway, keys = None, {}
        if "route_target" in self.cfg:
            from guard.runtime.kubernetes_routes import EXTERNAL, KubernetesRoutesHTTP
            try:
                routes = json.loads(Path(self.cfg["route_target"]).read_text(encoding="utf-8"))
                key = self._environ.get(self.cfg["allowed_key_env"]) if type(self.cfg["allowed_key_env"]) is str else None
                if (type(key) is not str or not key or {k: v for k, v in routes.items() if k not in
                        {"kubernetes_routes", "gateway", "origins", "direct_targets"}} != {k: v for k, v in target.items() if k != "origins"}):
                    raise ValueError
            except (KeyError, TypeError, ValueError, OSError, AttributeError):
                raise ValueError("KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID") from None
            gateway = KubernetesRoutesHTTP(routes, env, duration=self.cfg.get("max_duration_seconds", 120),
                                           binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner)
            keys = {"allowed": key}
        # 엔진 API 키를 쓰지 않는 소유 lab 경로다. 키가 있으면 전송이 Authorization 헤더를 거부한다.
        # 원 실행기가 쓰는 망 이름만 전송 망으로 바꾼 사본. 전송에는 원 target을 그대로 넘긴다.
        # Gateway 대상은 Kubernetes 바인딩이 없으므로 원 실행기가 "target URL unavailable" 공백으로 남긴다.
        view = {k: v for k, v in target.items() if k != "gateway"}
        view["origins"] = {"trusted": {"network": "kubernetes-probe"}, "external": {"network": "kubernetes-probe"}}
        cprobe = transport
        if gateway is not None:
            view["gateway"] = {"url": routes["gateway"]["url"]}
            view["origins"]["external"] = {"network": EXTERNAL}
            cprobe = _Legs(transport, gateway)
        ctx = Ctx(check=check, env=env, adapter=adapter, target=view, policy_values=policy_values,
                  timeout=float(self.cfg.get("request_timeout", 10)), allow_mutating=False, keys=keys,
                  secrets=[v for v in keys.values() if v], cprobe=cprobe, workdir=workdir, started=utcnow())
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if gateway is not None:
            draft.cleanup_errors.extend(gateway.report()["cleanup_errors"])
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft
