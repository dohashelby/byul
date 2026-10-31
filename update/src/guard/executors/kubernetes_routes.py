"""REQ01/03 reuse existing safe probes, collectors and judges on owned Pods."""
from pathlib import Path
import math
import re

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_routes import KubernetesRoutesHTTP, PROFILE
from .base import DraftArtifact
from .direct_route import DirectRouteExecutor
from .role_route import RoleRouteExecutor
from .http_common import Ctx, incomplete_artifact_capture


class _KubernetesRoutes:
    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._target_pin = expected_target_sha256
        if expected_target_sha256 is not None and (type(expected_target_sha256) is not str or re.fullmatch('[0-9a-f]{64}', expected_target_sha256) is None):
            raise ValueError('KUBERNETES_ROUTE_CONFIGURATION_INVALID')

    def preview_reuse_inputs(self, check, env, values, adapter):
        return None

    def validate_policy(self, check, values):
        if check.id != self.check_id or check.route_scope is None:
            raise ValueError('KUBERNETES_ROUTE_PROFILE_UNSUPPORTED')
        super().validate_policy(check, values)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        try:
            allowed = {'target','roles','allowed_key_env','denied_key_env','wrong_key_env','admin_role',
                       'principals_policy','comparison_routes','request_timeout','kubectl_binary','max_duration_seconds'}
            if type(self.cfg) is not dict or set(self.cfg)-allowed or adapter is None:
                raise ValueError
            if any(re.fullmatch('[A-Za-z0-9_.-]{1,64}', role) is None for role in self.roles()):
                raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:
                target = strict_json(stream.read(65537), limit=65536)
            if self._target_pin is not None and sha256_hex(canonical_json(target)) != self._target_pin:
                raise ValueError
            if self.check_id == 'REQ-01' and not target['direct_targets']:
                raise ValueError
            timeout = self.cfg.get('request_timeout',3)
            if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
                raise ValueError
            keys = self.keys()
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError('KUBERNETES_ROUTE_CONFIGURATION_INVALID') from None
        transport = KubernetesRoutesHTTP(target, env, duration=self.cfg.get('max_duration_seconds', 300),
            binary=self.cfg.get('kubectl_binary','kubectl'), runner=self._kubectl_runner)
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
                  timeout=float(timeout), allow_mutating=False,
                  keys=keys, secrets=[x for x in keys.values() if x], cprobe=transport,
                  workdir=workdir, started=utcnow(), http_probe_profile=PROFILE)
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()['cleanup_errors'])
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        path = Path(workdir)/'kubernetes_route_binding.json'
        try:
            path.write_bytes(canonical_json(transport.report()))
            draft.artifacts.append(DraftArtifact(name=path.name,path=path,collector=self.name+'/'+self.version))
        except Exception:
            draft = incomplete_artifact_capture(draft)
        return draft


class KubernetesDirectRouteExecutor(_KubernetesRoutes, DirectRouteExecutor):
    check_id = 'REQ-01'


class KubernetesRoleRouteExecutor(_KubernetesRoutes, RoleRouteExecutor):
    check_id = 'REQ-03'
