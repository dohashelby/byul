"""SUP-04의 기존 container_config 논리 실행기를 위한 Kubernetes API 소비 진입."""
import math
from pathlib import Path

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from .container_config import ContainerConfigExecutor
from .http_common import Ctx
from .running_image_approval import PROFILE, collect_kubernetes_running_image_approval


class KubernetesImageApprovalExecutor(ContainerConfigExecutor):
    version = "0.1.0"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def run(self, check, env, policy_values, adapter, workdir):
        try:
            if (check.id != "SUP-04" or check.collection_profile != PROFILE or adapter is None
                    or set(self.cfg) - {"target", "kubectl_binary", "request_timeout", "max_duration_seconds"}):
                raise ValueError
            self.validate_policy(check, policy_values)
            timeout = self.cfg.get("request_timeout", 3.0)
            if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
                raise ValueError
            target = strict_json(Path(self.cfg["target"]).read_bytes())
            if self._expected_target_sha256 is not None and (
                    type(self._expected_target_sha256) is not str
                    or sha256_hex(canonical_json(target)) != self._expected_target_sha256):
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_IMAGE_CONFIGURATION_INVALID") from None
        # Target/model/ENV validation and I/O bounds remain in the shared path.
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 30),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner,
            profile=REQUEST_BINDING_PROFILE)
        if not callable(getattr(transport, "owned_snapshot", None)):
            # A1 supplies this public producer. Never substitute a private API,
            # raw target declaration, Docker wrapper or empty successful capture.
            raise ValueError("KUBERNETES_IMAGE_SNAPSHOT_PRODUCER_UNAVAILABLE")
        keys = self.keys()
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
            timeout=float(timeout), allow_mutating=False, keys=keys, secrets=[v for v in keys.values() if v],
            cprobe=transport, workdir=workdir, started=utcnow())
        draft = self._finish(ctx)
        if not getattr(ctx, "_image_snapshot_capture_complete", False):
            if draft.data_origin == DataOrigin.MEASURED:
                draft.data_origin = DataOrigin.DECLARED
            draft.status = ExecStatus.ABORTED
            reason = "KUBERNETES_IMAGE_CAPTURE_INCOMPLETE"
            draft.abort_reason = f"{draft.abort_reason}; {reason}" if draft.abort_reason else reason
        draft.cleanup_errors.extend(transport.report().get("cleanup_errors", []))
        if transport.injected or env.data_origin == DataOrigin.EXAMPLE:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft

    def collect(self, ctx):
        snapshots, errors = [], []
        for _ in range(2):
            try:
                snapshots.append(ctx.cprobe.owned_snapshot())
            except Exception:
                errors.append("KUBERNETES_IMAGE_SNAPSHOT_UNAVAILABLE")
                break
        before = snapshots[0] if snapshots else {}
        after = snapshots[1] if len(snapshots) == 2 else {}
        observations, normal, challenges, summary = collect_kubernetes_running_image_approval(ctx, before, after)
        analysis = summary["kubernetes_image"]
        # Preserve already obtained API metadata even if a later read or the
        # summary attachment fails. None of these facts is runtime approval.
        observations["kubernetes_image_api_identity_stable"] = {
            "value": analysis["api_identity_stable"], "collected": True}
        for stage in ("before", "after"):
            value = analysis["identity_" + stage]
            observations["kubernetes_image_api_identity_" + stage] = {"value": value, "collected": value is not None}
        report = ctx.cprobe.report()
        ctx._image_snapshot_capture_complete = (analysis["api_identity_stable"] and not errors
                                                and report["collection_gap"] is False
                                                and not report.get("cleanup_errors"))
        summary["snapshot_capture_complete"] = ctx._image_snapshot_capture_complete
        summary["kubernetes"] = report
        summary["collection_errors"] = sorted(set(summary["collection_errors"] + errors))
        summary["fixture"].update(executor_version=self.version, transport_profile=REQUEST_BINDING_PROFILE,
                                   scope="owned-kubernetes-api-snapshots-only")
        return observations, normal, challenges, summary
