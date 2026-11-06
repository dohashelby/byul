"""REQ-15 callback traffic through one independently bound owned Gateway Pod."""
from copy import deepcopy

from guard.models import canonical_json, sha256_hex
from .kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP
from .kubernetes_routes import KubernetesRoutesHTTP


class KubernetesCallbackGatewayHTTP(KubernetesHTTP):
    """Keep callback reads on the engine; verify both generations around HTTP."""

    def __init__(self, target, gateway_target, env, *, duration, binary='kubectl', runner=None):
        super().__init__(target, env, duration=duration, binary=binary, runner=runner,
                         profile=ENGINE_CALLBACK_PROFILE)
        routes = KubernetesRoutesHTTP(gateway_target, env, duration=duration, binary=binary, runner=runner)
        if (routes.base.binding != self.binding or routes.api_model != self.api_model
                or routes.model_mapping != self.model_mapping or gateway_target.get('lab') is not True):
            raise KubernetesBindingError()
        self.gateway_routes = routes
        self.gateway_target_sha256 = sha256_hex(canonical_json(gateway_target))
        self.endpoint = routes.gateway

    def _verify(self, *, require_ready=True, capture_snapshot=False):
        pods = super()._verify(require_ready=require_ready, capture_snapshot=capture_snapshot)
        self.gateway_routes._verify()
        return pods

    def report(self):
        return {**super().report(), 'gateway_target_sha256': self.gateway_target_sha256,
                'gateway_binding': deepcopy(self.gateway_routes.binding['gateway']),
                'principal_authenticity_verified': False}
