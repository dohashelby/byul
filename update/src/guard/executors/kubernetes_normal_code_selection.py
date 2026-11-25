"""Observe ordinary SUP-08 stock code provenance without sending a selection field."""

from pathlib import Path
import re

from guard.adapters.vllm_release_profiles import release_profile
from guard.configuration import NORMAL_CODE_SELECTION_OPTION
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state, validate_content_settings
from .kubernetes_lifecycle_capture import supervised_source
from .kubernetes_normal_token_input import _normal_exchange
from .request_field_probe import CODE_SELECTION_PROFILE, validate_policy

PROFILE = "kubernetes-owned-normal-code-selection/1"
SOURCE_SCHEMA = "guard-sup08-stock-source/1"
NORMAL_PATH = "/v1/chat/completions"
NORMAL_CASE = "approved-stock-multimodal-control"
NORMAL_LABEL = "func|POST_" + NORMAL_PATH
ARTIFACT = "normal_code_selection.json"
MODEL = ("Qwen/Qwen2-VL-2B-Instruct", "895c3a49bc3fa70a340399125c650a463535e71c",
         "ad73950ffd6022ad452aef105d66caf2c623a3eeb7dc9eafe983ee416168d432")
MODEL_FILES = {
    "config.json": "422adefa19e62dd175961cec85bc0400344fe5bf9b22bd1182e05aaae78556e0",
    "preprocessor_config.json": "b5eaad0c2815f07631535dcc58f3c462b0d73693638ad21d19f3c50820eae1cc",
}
PACKAGE_FILES = {
    "vllm": "vllm/model_executor/models/qwen2_vl.py",
    "transformers": "transformers/models/qwen2_vl/processing_qwen2_vl.py",
}
_POLICY_KEYS = {"code_selection_fields", "code_selection_allowed", "rejection_codes", "approved_code_revisions"}
_SETTINGS = {"target", NORMAL_CODE_SELECTION_OPTION, "kubectl_binary", "request_timeout", "max_duration_seconds"}
_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAADgAAAA4CAAAAACN7WTCAAAAGklEQVR4nO3BMQEAAADCoPVPbQo/oAAAAL4GDHgAAaRRt7cAAAAASUVORK5CYII="
_SOURCE = supervised_source((Path(__file__).parents[1] / "runtime/assets/sup08_stock_source.py.txt").read_text(
    encoding="utf-8"))
_SOURCE_SHA256 = sha256_hex(_SOURCE.encode())
_SHA = re.compile(r"[a-f0-9]{64}")
_VERSION = re.compile(r"[0-9][A-Za-z0-9._+!-]{0,127}")
_UNCOLLECTED = ["request_selectable_fields", "override_rejection", "override_removal",
                "request_final_processor_consumption", "unapproved_revision_execution"]
_LAUNCH_OPTIONS = {"--served-model-name", "--host", "--port", "--max-model-len", "--max-num-seqs",
                   "--gpu-memory-utilization", "--tensor-parallel-size", "--pipeline-parallel-size",
                   "--data-parallel-size", "--distributed-executor-backend"}
_RELEASES = frozenset({"0.30.0", "0.31.0"})
_OFFLINE = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
_ALLOWED_MOUNTS = frozenset({"/models", "/cache", "/dev/shm"})
_SCOPE_ERROR = "SUP08_STOCK_NORMAL_SCOPE_INVALID"
_TARGET_ERROR = "SUP08_STOCK_NORMAL_TARGET_UNVERIFIED"
_SOURCE_ERROR = "SUP08_STOCK_NORMAL_SOURCE_UNAVAILABLE"
_CONTROL_ERROR = "SUP08_STOCK_NORMAL_CONTROL_UNVERIFIED"


def normal_body(api_model):
    return {"model": api_model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": _IMAGE}},
    ]}], "max_tokens": 1, "temperature": 0}


def _approved_arguments(args, api_model):
    if type(args) is not list or not args or args[0] != "/models" or len(args) % 2 != 1:
        raise ValueError(_TARGET_ERROR)
    keys, values = args[1::2], args[2::2]
    if (len(set(keys)) != len(keys) or set(keys) - _LAUNCH_OPTIONS
            or any(type(value) is not str or not value or value.startswith("-") for value in values)
            or dict(zip(keys, values)).get("--served-model-name") != api_model):
        raise ValueError(_TARGET_ERROR)


def normal_code_selection_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Resolve only an exact, non-lab profile before any process or HTTP dispatch."""
    release = release_profile(env.engine.version) if env.engine.version in _RELEASES else None
    if (env.engine.name != "vllm" or release is None or env.engine.image_digest not in release["images"]
            or (env.model.id, env.model.revision, env.model.inventory_sha256) != MODEL
            or target.get("lab") is not False or target.get("synthetic", False) is not False
            or target["engine"]["command"] != release["launch"]["command"]
            or target.get("observed_version") != env.engine.version
            or target["model_mapping"]["schema"] != "guard.model-request-mapping/1"
            or target["model_mapping"]["container_mount"] != "/models"
            or target["comparison_config"]["launch"]["offline_env"] != _OFFLINE
            or target["kubernetes"]["engine"]["restart_count"] != 0):
        raise ValueError(_TARGET_ERROR)
    transport = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner,
                               profile=INPUT_VALIDATION_PROFILE)
    _approved_arguments(target["comparison_config"]["launch"]["argv"], transport.api_model)
    return transport


def _launch_matches(pod, transport, target):
    engine = transport.binding["engine"]
    container, = (row for row in pod["spec"]["containers"] if row["name"] == engine["container"])
    args = target["comparison_config"]["launch"]["argv"]
    if (container.get("command") != target["engine"]["command"] or container.get("args") != args
            or container.get("envFrom")):
        raise ValueError(_TARGET_ERROR)
    env_rows = container.get("env", [])
    environment = {row["name"]: row for row in env_rows}
    if (len(environment) != len(env_rows) or {"PYTHONPATH", "PYTHONHOME", "LD_PRELOAD"} & environment.keys()
            or any(environment.get(name) != {"name": name, "value": value} for name, value in _OFFLINE.items())):
        raise ValueError(_TARGET_ERROR)
    _approved_arguments(args, transport.api_model)
    mounts = container["volumeMounts"]
    paths = {row["mountPath"] for row in mounts}
    if len(paths) != len(mounts) or not paths <= _ALLOWED_MOUNTS:
        raise ValueError(_TARGET_ERROR)
    mount, = (row for row in mounts if row["mountPath"] == "/models")
    volume, = (row for row in pod["spec"]["volumes"] if row["name"] == mount["name"])
    if (mount.get("readOnly") is not True or mount.get("subPath") or mount.get("subPathExpr")
            or volume.get("hostPath", {}).get("path") != target["model_mapping"]["host_snapshot"]):
        raise ValueError(_TARGET_ERROR)


def _read_proof(transport, target):
    engine = transport.binding["engine"]
    _launch_matches(transport._verify()["engine"], transport, target)
    remaining = min(5, transport.deadline - transport.clock())
    if remaining <= 0:
        raise ValueError(_SOURCE_ERROR)
    result = transport._call([f"--namespace={engine['namespace']}", "exec", engine["pod"],
        f"--container={engine['container']}", "--", "python3", "-I", "-S", "-B", "-c",
        _SOURCE, str(remaining)], cap=8192, budget=remaining)
    _launch_matches(transport._verify()["engine"], transport, target)
    if (type(result) is not dict or set(result) != {"schema", "model_files", "packages"}
            or result["schema"] != SOURCE_SCHEMA or result["model_files"] != MODEL_FILES
            or type(result["packages"]) is not dict or set(result["packages"]) != set(PACKAGE_FILES)):
        raise ValueError(_SOURCE_ERROR)
    for name, row in result["packages"].items():
        if (type(row) is not dict or set(row) != {"version", "metadata_sha256", "source_file", "source_sha256"}
                or row["source_file"] != PACKAGE_FILES[name]
                or type(row["version"]) is not str or not _VERSION.fullmatch(row["version"])
                or (name == "vllm" and row["version"] != transport.engine_version)
                or any(type(row[key]) is not str or not _SHA.fullmatch(row[key])
                       for key in ("metadata_sha256", "source_sha256"))):
            raise ValueError(_SOURCE_ERROR)
    return {**result, "reader_sha256": _SOURCE_SHA256,
            "binding_sha256": sha256_hex(canonical_json(transport.binding))}


class KubernetesNormalCodeSelectionExecutor:
    name = "input_validation"
    version = "0.7.1+k8s-sup08-stock-normal"

    def __init__(self, cfg, *, runner=None, expected_target_sha256=None):
        self.cfg = cfg
        self._runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_configuration(self, check):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_CODE_SELECTION_OPTION) is not True
                or check.id != "SUP-08" or check.executor != self.name
                or check.collection_profile != CODE_SELECTION_PROFILE or set(self.cfg) - _SETTINGS
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError(_SCOPE_ERROR)
        validate_content_settings(self.cfg)

    def validate_policy(self, check, values):
        self.validate_configuration(check)
        validate_policy(check, values)
        if (set(values) != _POLICY_KEYS or values["code_selection_allowed"] is not False
                or not values["approved_code_revisions"]):
            raise ValueError("SUP08_STOCK_NORMAL_POLICY_INVALID")

    def _target(self, env, expected_target_sha256=None):
        from guard.adapters.vllm_file_read_sensor import dependency

        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            target_sha = sha256_hex(canonical_json(target))
            pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
            if not pins or any(pin != target_sha for pin in pins):
                raise ValueError(_TARGET_ERROR)
            transport = normal_code_selection_transport(target, env, duration=self.cfg.get("max_duration_seconds", 60),
                       binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._runner)
        except (AttributeError, KeyError, TypeError, ValueError, OSError, KubernetesBindingError):
            raise ValueError(_TARGET_ERROR) from None
        return target, target_sha, transport

    def prepared_normal_code_selection(self, check, env, values, *, expected_target_sha256=None):
        """Validate the target and policy without contacting the engine."""
        self.validate_policy(check, values)
        self._target(env, expected_target_sha256)

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        target, target_sha, transport = self._target(env)
        before = request = after = None
        normal = False
        error = None
        try:
            before = _read_proof(transport, target)
            body = normal_body(transport.api_model)
            transport.declare_input_contract({"path": NORMAL_PATH, "control": body, "prompt_tokens": None, "fields": {}})
            timeout = min(self.cfg.get("request_timeout", 12), transport.deadline - transport.clock())
            response, document, request, _ = _normal_exchange(transport, NORMAL_PATH, NORMAL_LABEL, body, timeout)
            if (document is None or request["binding_sha256"] != before["binding_sha256"]
                    or control_state(response, transport.api_model, NORMAL_PATH) != "pass"
                    or len(document["choices"]) != 1 or document["usage"]["completion_tokens"] != 1):
                raise ValueError(_CONTROL_ERROR)
            normal = True
            after = _read_proof(transport, target)
            if after != before:
                raise ValueError(_SOURCE_ERROR)
        except (AttributeError, KeyError, TypeError, ValueError, OSError, KubernetesBindingError) as exc:
            error = str(exc) if str(exc) in {_CONTROL_ERROR, _TARGET_ERROR} else _SOURCE_ERROR
        cleanup = transport.report()["cleanup_errors"]
        if cleanup:
            error = error or _SOURCE_ERROR
        observed_source = not error and after is not None and before == after
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_ref": transport.environment_ref,
                   "request": request, "source_before": before, "source_after": after,
                   "normal_control_verified": normal, "stock_source_verified": observed_source,
                   "uncollected": _UNCOLLECTED}
        observations = {obs.key: {"value": None, "collected": False} for obs in check.required_observations}
        observations.update({
            "code_selection_forbidden": {"value": not values["code_selection_allowed"], "collected": True},
            "code_selection_fields_planned": {"value": values["code_selection_fields"], "collected": True},
            "functional_probes_planned": {"value": [NORMAL_CASE], "collected": True},
        })
        if normal:
            observations["functional_probe_results"] = {"value": {NORMAL_CASE: "pass"}, "collected": True}
        artifacts = []
        try:
            path = Path(workdir) / ARTIFACT
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            error = error or "SUP08_STOCK_NORMAL_ARTIFACT_UNAVAILABLE"
        comparability = {} if error else {
            "fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "request": normal_body("{model}")})),
            "config_sha256": sha256_hex(canonical_json(execution_conditions(target, {"profile": PROFILE}, []))),
            "model_inventory_sha256": env.model.inventory_sha256,
            "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu,
        }
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if transport.process_observation_origin == "EXAMPLE" else DataOrigin.DECLARED,
            normal_control={"attempted": request is not None and request["ownership_verified_before_after"],
                            "succeeded": True if normal else None,
                            "detail": "Ordinary Qwen response and installed source provenance. Request-level code selection is unobserved."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations=observations, artifacts=artifacts, comparability=comparability,
            config={NORMAL_CODE_SELECTION_OPTION: True, "target_sha256": target_sha},
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
