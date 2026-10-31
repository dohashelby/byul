"""PLT08's versioned one-hop consumer over the existing common executor."""
from guard.models import DataOrigin, ORIGIN_STRENGTH
from guard.runtime.kubernetes_api_hop import PROFILE, normalize_policy
from .api_transport import collect_kubernetes_api_transport
from .kubernetes_container_config import KubernetesContainerConfigExecutor


class KubernetesAPITransportExecutor(KubernetesContainerConfigExecutor):
    name = "api_transport"
    version = "0.1.0"

    def validate_policy(self, check, values):
        if check.id != "PLT-08" or getattr(check, "collection_profile", None) != PROFILE:
            raise ValueError("KUBERNETES_API_TRANSPORT_PROFILE_UNSUPPORTED")
        normalize_policy(values)

    def preview_reuse_inputs(self, check, env, values, adapter):
        return None

    def collect(self, ctx):
        return collect_kubernetes_api_transport(ctx)

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        draft.data_origin = min(draft.data_origin, getattr(ctx, "_kubernetes_api_hop_data_origin", DataOrigin.EXAMPLE),
                                key=lambda origin: ORIGIN_STRENGTH[origin])
        return draft
