"""Observe a stock model becoming Ready in an owned, unchanged generation."""

from pathlib import Path

from guard.adapters.vllm_file_read_sensor import dependency
from guard.configuration import NORMAL_MODEL_CODE_OPTION, NORMAL_MODEL_CODE_SCOPE_ERROR
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import KubernetesBindingError
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import supervised_source
from .kubernetes_normal_code_selection import MODEL_FILES, _launch_matches, _read_proof, normal_code_selection_transport
from .kubernetes_normal_serving_readiness import _owned_ready
from .kubernetes_normal_startup_load import STARTUP_READY_WINDOW, _window_stamp, startup_ready_window

PROFILE = "kubernetes-owned-normal-model-code/1"
ARTIFACT = "normal_model_code.json"
_CHECK_PROFILE = "supply-boot-model-code-v1"
_SETTINGS = {"target", NORMAL_MODEL_CODE_OPTION, "kubectl_binary", "max_duration_seconds"}
_UNCOLLECTED = ["custom_code_import_trace", "trust_branch_comparison"]
_TARGET_ERROR = "SUP09_STOCK_NORMAL_TARGET_UNVERIFIED"
_PROOF_ERROR = "SUP09_STOCK_NORMAL_PROOF_UNAVAILABLE"
_FILE_SIZES = {
    "chat_template.json": 1050,
    "config.json": 1196,
    "generation_config.json": 272,
    "merges.txt": 1671839,
    "model-00001-of-00002.safetensors": 3988609112,
    "model-00002-of-00002.safetensors": 429441656,
    "model.safetensors.index.json": 56411,
    "preprocessor_config.json": 347,
    "tokenizer.json": 7029741,
    "tokenizer_config.json": 4190,
    "vocab.json": 2776833,
}
_CONFIG_SHA256 = {**MODEL_FILES,
                  "tokenizer_config.json": "ff5c4fd898fe8c39591eb70e5d39d2782802d4204d6ae9ba1223252f354842a0"}
_SOURCE = supervised_source((Path(__file__).parents[1] / "runtime/assets/sup09_stock_inventory.py.txt").read_text(
    encoding="utf-8"))
_SOURCE_SHA256 = sha256_hex(_SOURCE.encode())


def normal_model_code_transport(target, env, *, duration, binary="kubectl", runner=None):
    transport = normal_code_selection_transport(target, env, duration=duration, binary=binary, runner=runner)
    window = target.get(STARTUP_READY_WINDOW)
    if (type(window) is not dict or set(window) != {"container_started_at", "ready_at"}
            or window["container_started_at"] != transport.binding["engine"]["started_at"]
            or _window_stamp(window["ready_at"]) < _window_stamp(window["container_started_at"])):
        raise ValueError(_TARGET_ERROR)
    return transport


def _ready(transport, target):
    pod = transport._verify(capture_snapshot=True)["engine"]
    _launch_matches(pod, transport, target)
    if (pod["spec"].get("shareProcessNamespace", False) is not False
            or pod["spec"].get("hostPID", False) is not False
            or startup_ready_window(pod, transport.binding["engine"]) != target[STARTUP_READY_WINDOW]):
        raise ValueError(_PROOF_ERROR)
    ready = _owned_ready(transport, pod=pod)
    if ready["pod_ready_last_transition_at"] is None:
        raise ValueError(_PROOF_ERROR)
    return ready


def _inventory(transport, target):
    engine = transport.binding["engine"]
    _launch_matches(transport._verify()["engine"], transport, target)
    budget = min(5, transport.deadline - transport.clock())
    if budget <= 0:
        raise ValueError(_PROOF_ERROR)
    result = transport._call([f"--namespace={engine['namespace']}", "exec", engine["pod"],
        f"--container={engine['container']}", "--", "python3", "-I", "-S", "-B", "-c", _SOURCE, str(budget)],
        cap=4096, budget=budget)
    _launch_matches(transport._verify()["engine"], transport, target)
    if (type(result) is not dict or set(result) != {"file_sizes", "config_sha256", "read_only", "auto_map_present",
                                                    "pid1_start_ticks"}
            or result["file_sizes"] != _FILE_SIZES or result["config_sha256"] != _CONFIG_SHA256
            or result["read_only"] is not True or result["auto_map_present"] is not False
            or type(result["pid1_start_ticks"]) is not int or result["pid1_start_ticks"] <= 0):
        raise ValueError(_PROOF_ERROR)
    return {**result, "reader_sha256": _SOURCE_SHA256,
            "binding_sha256": sha256_hex(canonical_json(transport.binding))}


class KubernetesNormalModelCodeExecutor:
    name = "supply_boot"
    version = "0.1.0+k8s-sup09-stock-normal"

    def __init__(self, cfg, *, runner=None, expected_target_sha256=None):
        self.cfg = cfg
        self._runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_MODEL_CODE_OPTION) is not True
                or check.id != "SUP-09" or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - _SETTINGS or type(self.cfg.get("target")) is not str or not self.cfg["target"]
                or type(values) is not dict or values):
            raise ValueError(NORMAL_MODEL_CODE_SCOPE_ERROR)

    def _target(self, env, expected_target_sha256=None):
        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            digest = sha256_hex(canonical_json(target))
            pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
            if not pins or any(pin != digest for pin in pins):
                raise ValueError(_TARGET_ERROR)
            return target, digest, normal_model_code_transport(target, env,
                duration=self.cfg.get("max_duration_seconds", 60), binary=self.cfg.get("kubectl_binary", "kubectl"),
                runner=self._runner)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError(_TARGET_ERROR) from None

    def prepared_normal_model_code(self, check, env, values, *, expected_target_sha256=None):
        """Validate the sealed input without contacting the cluster."""
        self.validate_policy(check, values)
        self._target(env, expected_target_sha256)

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        target, target_sha, transport = self._target(env)
        ready_before = ready_after = source_before = source_after = inventory_before = inventory_after = None
        error = None
        try:
            ready_before = _ready(transport, target)
            inventory_before = _inventory(transport, target)
            source_before = _read_proof(transport, target)
            source_after = _read_proof(transport, target)
            inventory_after = _inventory(transport, target)
            ready_after = _ready(transport, target)
            ready_keys = ready_before.keys() - {"observed_at"}
            if (any(ready_before[key] != ready_after[key] for key in ready_keys)
                    or source_before != source_after or inventory_before != inventory_after
                    or source_before["binding_sha256"] != inventory_before["binding_sha256"]):
                raise ValueError(_PROOF_ERROR)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            error = _PROOF_ERROR
        cleanup = transport.report()["cleanup_errors"]
        if cleanup:
            error = error or _PROOF_ERROR
        verified = error is None
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_ref": transport.environment_ref,
                   "ready_before": ready_before, "ready_after": ready_after, "source_before": source_before,
                   "source_after": source_after, "model_inventory": {"before": inventory_before, "after": inventory_after},
                   "stock_load_verified": verified, "uncollected": _UNCOLLECTED}
        artifacts = []
        try:
            path = Path(workdir) / ARTIFACT
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            error = error or "SUP09_STOCK_NORMAL_ARTIFACT_UNAVAILABLE"
        verified = error is None
        observations = {obs.key: {"value": None, "collected": False} for obs in check.required_observations}
        observations.update({
            "supply_boot_missing": {"value": _UNCOLLECTED if verified else [*_UNCOLLECTED, "stock_load"], "collected": True},
            "supply_boot_complete": {"value": False, "collected": True},
            "supply_boot_change_applied": {"value": False, "collected": True},
            "supply_boot_record_authenticated": {"value": False, "collected": True},
        })
        if verified:
            observations.update({
                "supply_boot_normal_verified": {"value": True, "collected": True},
                "supply_boot_sessions": {"value": [{"scope": "ordinary_stock_model_load", "stock_load_verified": True,
                    "binding_sha256": source_before["binding_sha256"]}], "collected": True},
            })
        comparability = {} if error else {
            "fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "scope": "ordinary_stock_model_load"})),
            "config_sha256": sha256_hex(canonical_json(execution_conditions(target, {"profile": PROFILE}, []))),
            "model_inventory_sha256": env.model.inventory_sha256,
            "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu,
        }
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if transport.process_observation_origin == "EXAMPLE" else DataOrigin.DECLARED,
            normal_control={"attempted": ready_before is not None, "succeeded": True if verified else None,
                            "detail": "Approved stock model Ready with a read-only inventory and installed source provenance."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations=observations, artifacts=artifacts, comparability=comparability,
            config={NORMAL_MODEL_CODE_OPTION: True, "target_sha256": target_sha},
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
