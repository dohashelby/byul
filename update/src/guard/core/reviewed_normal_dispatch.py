"""The final dispatch guard and call receipt for the owned normal campaign."""

from contextlib import contextmanager
import inspect
from pathlib import Path

from guard.configuration import REVIEWED_NORMAL_CASES, reviewed_check_settings
from guard.models import canonical_json, sha256_hex, utcnow


_TYPES = {
    "LIF-01": ("kubernetes_normal_startup_load", "KubernetesNormalStartupLoadExecutor"),
    "LIF-03": ("kubernetes_normal_request_latency", "KubernetesNormalRequestLatencyExecutor"),
    "LIF-04": ("kubernetes_stream_buffer", "KubernetesStreamBufferExecutor"),
    "OBS-02": ("kubernetes_normal_serving_readiness", "KubernetesNormalServingReadinessExecutor"),
    "OBS-03": ("kubernetes_log_analysis", "KubernetesLogAnalysisExecutor"),
    "OBS-04": ("kubernetes_usage_report", "kubernetes_boundary_egress.<locals>.Dispatch"),
    "PLT-01": ("kubernetes_container_config", "KubernetesContainerConfigExecutor"),
    "PLT-02": ("kubernetes_ipc_product", "KubernetesIPCProductExecutor"),
    "PLT-04": ("kubernetes_credential_exposure", "KubernetesCredentialExposureExecutor"),
    "PLT-06": ("kubernetes_ipc_product", "KubernetesIPCProductExecutor"),
    "PLT-07": ("kubernetes_passive_egress_policy", "KubernetesPassiveEgressPolicyExecutor"),
    "REQ-07": ("kubernetes_template_supply", "KubernetesTemplateSupplyExecutor"),
    "REQ-09": ("kubernetes_normal_token_input", "KubernetesNormalTokenInputExecutor"),
    "REQ-10": ("kubernetes_sampling", "KubernetesSamplingExecutor"),
    "REQ-11": ("kubernetes_request_binding", "KubernetesRequestBindingExecutor"),
    "REQ-20": ("kubernetes_normal_graph_capture", "KubernetesNormalGraphCaptureExecutor"),
    "SUP-03": ("kubernetes_template_supply", "KubernetesTemplateSupplyExecutor"),
    "SUP-04": ("kubernetes_container_config", "KubernetesContainerConfigExecutor"),
    "SUP-05": ("kubernetes_normal_config_parser", "KubernetesNormalConfigParserExecutor"),
    "SUP-07": ("kubernetes_normal_compile_cache", "KubernetesNormalCompileCacheExecutor"),
    "SUP-09": ("kubernetes_normal_boot_identity", "KubernetesNormalBootIdentityExecutor"),
    "SUP-10": ("kubernetes_installed_plugin_inventory", "KubernetesInstalledPluginInventoryExecutor"),
    "SUP-11": ("kubernetes_normal_boot_identity", "KubernetesNormalBootIdentityExecutor"),
    "SUP-12": ("kubernetes_normal_safetensors_reader", "KubernetesNormalSafetensorsReaderExecutor"),
}


def eligible(check, settings, executor):
    if check.id not in REVIEWED_NORMAL_CASES or check.id not in _TYPES or type(settings) is not dict:
        return False
    name, _ = REVIEWED_NORMAL_CASES[check.id]
    module, typename = _TYPES[check.id]
    cls = type(executor)
    expected = reviewed_check_settings(check.id)
    return (name == check.executor and type(settings.get("target")) is str
            and settings == {"target": settings["target"], **expected}
            and all(type(settings[key]) is type(value) for key, value in expected.items())
            and cls.__module__ == "guard.executors." + module and cls.__qualname__ == typename)


def required_input_gap(check, env, values, executor, target_sha256):
    """Validate required declarations using the existing read-only preparation."""
    if check.id in {"REQ-07", "SUP-03"}:
        from guard.collectors.file import load_bounded_json
        from guard.executors.template_supply_approval import validate_policy
        try:
            validate_policy(check, values, executor.cfg)
            target = load_bounded_json(executor.cfg["target"])
            launch = target["comparison_config"]["launch"]
            template = launch["chat_template"]
            argv = launch["argv"]
            if (sha256_hex(canonical_json(target)) != target_sha256 or argv.count("--chat-template") != 1
                    or argv[argv.index("--chat-template") + 1] != template["container_path"]
                    or template["sha256"] not in values["allowed_template_sha256"]):
                raise ValueError
        except (ValueError, KeyError, OSError, TypeError, IndexError):
            return "REVIEWED_TEMPLATE_APPROVED_TARGET_REQUIRED"
        return None
    if check.id != "PLT-07":
        return None
    try:
        executor.prepared_passive_egress_policy(check, env, values, expected_target_sha256=target_sha256)
    except ValueError as exc:
        diagnostic = str(exc)
        if diagnostic in {"PLT07_CILIUM_TARGET_UNVERIFIED", "PLT07_CILIUM_SCOPE_INVALID", "PLT07_CILIUM_POLICY_INVALID"}:
            return diagnostic
        return "REVIEWED_NORMAL_REQUIRED_INPUT_UNVERIFIED"
    return None


@contextmanager
def invocation(store, check, env, executor, plan, attempt, side, binding, target_sha256, *,
               projection=None, source_evidence=None):
    root = Path(store.root) / "reviewed-normal-dispatch" / attempt / check.id / side
    root.mkdir(parents=True, exist_ok=False)
    method = type(executor).run if projection is None else projection
    source = Path(inspect.getsourcefile(method))
    receipt = {"check_id": check.id, "execution_attempt_id": attempt, "side": side,
               "environment_ref": env.ref().model_dump(mode="json"), "plan_ref": plan.ref().model_dump(mode="json"),
               "executor_type": type(executor).__qualname__ if projection is None else projection.__module__,
               "executor_version": executor.version if projection is None else None,
               "dispatch_kind": "executor" if projection is None else "passive_evidence_projection",
               "source_evidence": source_evidence.model_dump(mode="json") if source_evidence is not None else None,
               "actual_method": method.__qualname__, "actual_source_sha256": sha256_hex(source.read_bytes()),
               "target_sha256": target_sha256, "execution_binding": binding, "entered_utc": utcnow().isoformat()}
    with (root / "entered.json").open("xb") as stream:
        stream.write(canonical_json(receipt))
    result = "returned"
    try:
        yield
    except BaseException:
        result = "raised"
        raise
    finally:
        with (root / "terminal.json").open("xb") as stream:
            stream.write(canonical_json({"outcome": result, "terminal_utc": utcnow().isoformat()}))
