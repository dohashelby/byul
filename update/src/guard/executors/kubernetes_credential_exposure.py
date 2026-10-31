"""PLT04's metadata consumer through the existing common Kubernetes executor."""
from guard.credential_policy import validate_credential_policy
from guard.models import DataOrigin, ORIGIN_STRENGTH
from .credential_exposure import collect_kubernetes_credential_exposure
from .kubernetes_container_config import KubernetesContainerConfigExecutor


class KubernetesCredentialExposureExecutor(KubernetesContainerConfigExecutor):
    name = "credential_exposure"
    version = "0.1.0"

    def validate_policy(self, check, values):
        if (check.id != "PLT-04" or not any(obs.key == "forbidden_mount_violations"
                                           for obs in check.required_observations)):
            raise ValueError("KUBERNETES_CREDENTIAL_PROFILE_UNSUPPORTED")
        validate_credential_policy(check, values)

    def preview_reuse_inputs(self, check, env, values, adapter):
        return None

    def collect(self, ctx):
        return collect_kubernetes_credential_exposure(ctx)

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        draft.data_origin = min(draft.data_origin, getattr(ctx, "_kubernetes_process_data_origin", DataOrigin.EXAMPLE),
                                key=lambda origin: ORIGIN_STRENGTH[origin])
        return draft
