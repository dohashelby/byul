"""Profile-resolved callback reads with the existing independently bound gateway."""
from .engine_http import KubernetesHTTP
from .kubernetes_callback_gateway import KubernetesCallbackGatewayHTTP as LegacyGatewayHTTP


class KubernetesCallbackGatewayHTTP(LegacyGatewayHTTP, KubernetesHTTP):
    """The legacy gateway ownership checks wrap the new bounded page transport."""
