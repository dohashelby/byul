"""REQ-13 Kubernetes 전송. 기존 응답 내용 사례·관측·판정은 그대로 쓰고 전송만 소유 Pod probe로 바꾼다.

엔진 대상은 소유 probe Pod에서 엔진으로 직접 보낸다.
Gateway 대상은 `route_target`(4-Pod 경로 target)과 `allowed_key_env`를 주면 바깥 probe Pod에서 Gateway로 보낸다.
그 전송은 기존 4-Pod 경로 전송(KubernetesRoutesHTTP)이고 요청 전후로 네 Pod 세대를 다시 확인한다.
설정하지 않으면 원 실행기가 Gateway 대상을 "target URL unavailable" 공백으로 남긴다.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import stat

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import RESPONSE_CONTENT_PROFILE, KubernetesBindingError, KubernetesHTTP
from guard.runtime.kubernetes_routes import KubernetesRoutesHTTP
from .base import DraftArtifact, EvidenceDraft
from .http_common import Ctx, incomplete_artifact_capture
from . import kubernetes_response_snapshot as snapshot
from .response_content import ResponseContentExecutor

_SETTINGS = {"target", "endpoint", "kubectl_binary", "request_timeout", "max_duration_seconds", "route_target", "allowed_key_env"}
_TARGET_BYTE_LIMIT = 262144
CONFIGURATION_INVALID = "KUBERNETES_RESPONSE_CONTENT_CONFIGURATION_INVALID"
GATEWAY_TARGET_REQUIRED = "KUBERNETES_RESPONSE_CONTENT_GATEWAY_TARGET_REQUIRED"
ROUTE_TARGET_CHANGED = "KUBERNETES_RESPONSE_CONTENT_ROUTE_TARGET_CHANGED"


def route_target_bytes(path):
    """Read one locally declared target without following links or blocking on pipes."""
    if not isinstance(path, (str, Path)) or not path:
        raise ValueError(CONFIGURATION_INVALID)
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= _TARGET_BYTE_LIMIT:
            raise ValueError(CONFIGURATION_INVALID)
        raw = stream.read(_TARGET_BYTE_LIMIT + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) != before.st_size or
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
            (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError(ROUTE_TARGET_CHANGED)
    return raw


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

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None, expected_route_target_sha256=None,
                 expected_manifest_sha256=None, expected_policy_sha256=None, expected_target_file_sha256=None,
                 expected_policy_validity=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256
        self._expected_route_target_sha256 = expected_route_target_sha256
        self._expected_manifest_sha256 = expected_manifest_sha256
        self._expected_policy_sha256 = expected_policy_sha256
        self._expected_target_file_sha256 = expected_target_file_sha256
        self._expected_policy_validity = expected_policy_validity

    def validate_configuration(self, check):
        if snapshot.OPTION in self.cfg or snapshot.INPUT in self.cfg:
            if (check.id != "REQ-13" or check.executor != self.name or self.cfg.get(snapshot.OPTION) is not True
                    or set(self.cfg) != {"target", snapshot.OPTION, snapshot.INPUT}
                    or self._expected_route_target_sha256 is not None):
                raise ValueError(snapshot.INVALID)
            return
        if type(self.cfg) is not dict or set(self.cfg) - _SETTINGS:
            raise ValueError(CONFIGURATION_INVALID)
        if ("route_target" in self.cfg) != ("allowed_key_env" in self.cfg):
            raise ValueError(CONFIGURATION_INVALID)
        settings = {k: v for k, v in self.cfg.items() if k not in {"target", "kubectl_binary", "max_duration_seconds", "route_target", "allowed_key_env"}}
        saved, self.cfg = self.cfg, settings
        try:
            super().validate_configuration(check)
        finally:
            self.cfg = saved

    def validate_policy(self, check, values):
        super().validate_policy(check, values)
        if (self.cfg.get(snapshot.OPTION) is True
                and (set(values) != {"allowed_response_fields", "allowed_error_fields", "forbidden_return_options", "required_targets"}
                     or values["required_targets"] != ["gateway"])):
            raise ValueError(snapshot.INVALID)

    def prepared_response_snapshot(self, check, env, values, *, expected_target_sha256, expected_manifest_sha256,
                                   expected_policy_sha256, policy_validity=None):
        """Qualify the independently pinned ordinary response without dispatch or key lookup."""
        self.validate_policy(check, values)
        if self.cfg.get(snapshot.OPTION) is not True:
            raise ValueError(snapshot.INVALID)
        for stored, expected in ((self._expected_target_sha256, expected_target_sha256),
                                 (self._expected_manifest_sha256, expected_manifest_sha256),
                                 (self._expected_policy_sha256, expected_policy_sha256)):
            if stored is not None and stored != expected:
                raise ValueError(snapshot.INVALID)
        if policy_validity is None:
            policy_validity = self._expected_policy_validity
        elif self._expected_policy_validity is not None and policy_validity != self._expected_policy_validity:
            raise ValueError(snapshot.INVALID)
        return snapshot.verified_snapshot(self.cfg, env, target_sha256=expected_target_sha256,
            manifest_sha256=expected_manifest_sha256, guard_policy_sha256=expected_policy_sha256,
            target_file_sha256=self._expected_target_file_sha256, policy_validity=policy_validity, policy_values=values)

    def _run_response_snapshot(self, check, env, values, workdir):
        if self._expected_target_file_sha256 is None:
            raise ValueError(snapshot.INVALID)
        started = utcnow()
        proof = self.prepared_response_snapshot(check, env, values,
            expected_target_sha256=self._expected_target_sha256, expected_manifest_sha256=self._expected_manifest_sha256,
            expected_policy_sha256=self._expected_policy_sha256)
        gaps = ["gateway: error responses, private context and return options were not exercised"]
        response_only = proof["profile"] == snapshot.RESPONSE_ONLY_PROFILE
        if response_only:
            gaps.append("gateway: independent access audit and denial were not observed")
        observations = {obs.key: {"collected": False, "value": None} for obs in check.required_observations}
        for key, value in {
            "response_coverage_complete": False, "response_coverage_gaps": gaps,
            "response_cases_tested": ["gateway:normal"], "response_fields_seen": proof["response_fields_seen"],
            "response_targets_tested": ["gateway"],
        }.items():
            observations[key] = {"collected": True, "value": value}
        self.version = "0.2.1+k8s-req13-response-only.1" if response_only else "0.2.1+k8s-req13-snapshot.1"
        conditions = {"profile": proof["profile"], "target": proof["target_sha256"],
                      "manifest_sha256": proof["manifest_sha256"], "policy_sha256": proof["guard_policy_sha256"]}
        comparison = {"profile": proof["profile"], "policy": values}
        if response_only and "transport_origin" in proof:
            comparison["transport_origin"] = proof["transport_origin"]
        draft = EvidenceDraft(
            status=ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if (self._kubectl_runner is not None or env.data_origin == DataOrigin.EXAMPLE
                or proof.get("guard_policy_data_origin") == DataOrigin.EXAMPLE.value) else DataOrigin.MEASURED,
            normal_control={"attempted": True, "succeeded": True, "detail": "Verified one retained ordinary Gateway response."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations=observations, config=conditions,
            started_at=started, finished_at=utcnow(),
            comparability={
                "fixture_sha256": proof["request_fixture_sha256"],
                "config_sha256": sha256_hex(canonical_json(comparison)),
                "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                "driver": env.runtime.driver, "cpu": env.runtime.cpu,
            })
        path = workdir / "response_samples.json"
        try:
            workdir.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json({"snapshot": proof, "coverage_gaps": gaps}))
        except OSError:
            return incomplete_artifact_capture(draft)
        draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        return draft

    def _gateway_transport(self, target, env, *, bounded=False):
        pinned = self._expected_route_target_sha256
        bounded = bounded or pinned is not None
        try:
            path = self.cfg['route_target']
            raw = route_target_bytes(path) if bounded else Path(path).read_bytes()
        except (KeyError, TypeError, OSError):
            raise ValueError(CONFIGURATION_INVALID) from None
        if pinned is not None and sha256_hex(raw) != pinned:
            raise ValueError(ROUTE_TARGET_CHANGED)
        try:
            routes = strict_json(raw, limit=_TARGET_BYTE_LIMIT) if bounded else json.loads(raw.decode('utf-8'))
        except ValueError:
            raise ValueError(CONFIGURATION_INVALID) from None
        if (type(routes) is not dict or {k: v for k, v in routes.items()
                if k not in {'kubernetes_routes', 'gateway', 'origins', 'direct_targets'}}
                != {k: v for k, v in target.items() if k != 'origins'}):
            raise ValueError(CONFIGURATION_INVALID)
        return routes, KubernetesRoutesHTTP(routes, env, duration=self.cfg.get('max_duration_seconds', 120),
            binary=self.cfg.get('kubectl_binary', 'kubectl'), runner=self._kubectl_runner)

    def prepared_gateway(self, env, *, expected_target_sha256=None):
        """Verify the saved base and Gateway declarations without opening a caller key."""
        if 'route_target' not in self.cfg:
            raise ValueError(GATEWAY_TARGET_REQUIRED)
        key_name = self.cfg.get('allowed_key_env')
        if type(key_name) is not str or not key_name.strip():
            raise ValueError(CONFIGURATION_INVALID)
        try:
            target = strict_json(route_target_bytes(self.cfg['target']), limit=_TARGET_BYTE_LIMIT)
            target_sha256 = sha256_hex(canonical_json(target))
            expected_pins = (self._expected_target_sha256, expected_target_sha256)
            if any(pin is not None and pin != target_sha256 for pin in expected_pins):
                raise ValueError(ROUTE_TARGET_CHANGED)
            KubernetesHTTP(target, env, duration=self.cfg.get('max_duration_seconds', 120),
                binary=self.cfg.get('kubectl_binary', 'kubectl'), runner=self._kubectl_runner,
                profile=RESPONSE_CONTENT_PROFILE)
            self._gateway_transport(target, env, bounded=True)
        except KubernetesBindingError as exc:
            raise ValueError(str(exc)) from None
        except (KeyError, TypeError, OSError):
            raise ValueError(CONFIGURATION_INVALID) from None

    def run(self, check, env, policy_values, adapter, workdir):
        if snapshot.OPTION in self.cfg or snapshot.INPUT in self.cfg:
            return self._run_response_snapshot(check, env, policy_values, workdir)
        self.version = "0.2.1+k8s.1"
        try:
            if adapter is None:
                raise ValueError
            self.validate_policy(check, policy_values)
            target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target)) != self._expected_target_sha256:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError(CONFIGURATION_INVALID) from None
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 120),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=RESPONSE_CONTENT_PROFILE)
        gateway, keys = None, {}
        if "route_target" in self.cfg:
            from guard.runtime.kubernetes_routes import EXTERNAL
            routes, gateway = self._gateway_transport(target, env)
            try:
                key = self._environ.get(self.cfg["allowed_key_env"]) if type(self.cfg["allowed_key_env"]) is str else None
                if type(key) is not str or not key:
                    raise ValueError
            except (KeyError, TypeError, ValueError, OSError, AttributeError):
                raise ValueError(CONFIGURATION_INVALID) from None
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
