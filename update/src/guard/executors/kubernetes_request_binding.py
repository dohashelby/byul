"""REQ-11 Kubernetes transport; existing request cases/analyzer/judge remain shared."""
from pathlib import Path

from guard.adapters.vllm_file_read_sensor import fingerprint
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions, model_request_conditions
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state
from .engine_identity import _READ_TEMPLATE
from .http_common import Ctx, fill_model, request_model
from .kubernetes_observation import _read_snapshot_target
from .request_binding import RequestBindingExecutor, DEFAULTS, SUPPORTED_POLICY
from .response_projection import capture_metadata

NORMAL_OPTION = "normal_request_binding_only"
NORMAL_PROFILE = "kubernetes-owned-normal-request-binding-control/1"
_NORMAL_LABEL = "before"
_NORMAL_CONFIG_ERROR = "KUBERNETES_REQUEST_BINDING_NORMAL_CONFIGURATION_INVALID"


class KubernetesRequestBindingExecutor(RequestBindingExecutor):
    version="0.2.0"
    _uses_docker_identity=False

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None,expected_target_file_sha256=None):
        super().__init__(cfg,environ=environ)
        if expected_target_sha256 is not None and (type(expected_target_sha256) is not str or len(expected_target_sha256)!=64
                or any(c not in "0123456789abcdef" for c in expected_target_sha256)):
            raise ValueError("KUBERNETES_REQUEST_BINDING_CONFIGURATION_INVALID")
        self._kubectl_runner=runner
        self._expected_target_sha256=expected_target_sha256
        self._expected_target_file_sha256=expected_target_file_sha256

    def keys(self):
        if self.cfg.get(NORMAL_OPTION) is True:
            return {}
        return super().keys()

    def validate_policy(self, check, values):
        if NORMAL_OPTION in self.cfg:
            if (self.cfg[NORMAL_OPTION] is not True or check.id != "REQ-11" or check.revision != 8
                    or check.executor != self.name or getattr(check, "collection_profile", None) is not None
                    or set(self.cfg) - {"target", NORMAL_OPTION, "kubectl_binary", "request_timeout", "max_duration_seconds"}
                    or values != SUPPORTED_POLICY):
                raise ValueError(_NORMAL_CONFIG_ERROR)
        super().validate_policy(check, values)

    def _normal_inputs(self, check, env, values, *, expected_target_sha256=None, expected_target_file_sha256=None):
        if self.cfg.get(NORMAL_OPTION) is not True:
            raise ValueError(_NORMAL_CONFIG_ERROR)
        self.validate_policy(check, values)
        try:
            pins = [pin for pin in (self._expected_target_file_sha256, expected_target_file_sha256) if pin is not None]
            if not pins or any(not fingerprint(pin) or pin != pins[0] for pin in pins):
                raise ValueError
            target = _read_snapshot_target(self.cfg["target"], expected_file_sha256=pins[0])
            target_sha = sha256_hex(canonical_json(target))
            canonical_pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
            if (not canonical_pins or any(pin != target_sha for pin in canonical_pins)
                    or target.get("lab") is not False or target.get("synthetic", False) is not False
                    or target.get("observed_version") != env.engine.version or check.functional_probes[0].as_role != "allowed"):
                raise ValueError
            transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", DEFAULTS["max_duration_seconds"]),
                binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=REQUEST_BINDING_PROFILE)
            model = request_model(env, target, transport=transport)
        except (KeyError, TypeError, ValueError, OSError, KubernetesBindingError):
            raise ValueError(_NORMAL_CONFIG_ERROR) from None
        return target, target_sha, transport, model

    def prepared_normal_control(self, check, env, policy_values, *, expected_target_sha256=None, expected_target_file_sha256=None):
        """Validate the selected exact target and fixed CheckDef without collecting."""
        self._normal_inputs(check, env, policy_values, expected_target_sha256=expected_target_sha256,
                            expected_target_file_sha256=expected_target_file_sha256)

    def _run_normal_control(self, check, env, values, adapter, workdir):
        started = utcnow()
        if adapter is None:
            raise ValueError(_NORMAL_CONFIG_ERROR)
        target, target_sha, transport, model = self._normal_inputs(check, env, values)
        probe, = check.functional_probes
        body = fill_model(probe.body, model)
        request = {"id": _NORMAL_LABEL, "mode": "json", "method": probe.method,
                   "url": target["engine"]["trusted_url"] + probe.path, "headers": {}, "body": body}
        response = {}
        try:
            response, = transport("kubernetes-probe", [request], self.cfg["request_timeout"])
        except KubernetesBindingError:
            pass
        report = transport.report()
        rows = report["requests"]
        bound = bool(len(rows) == 1 and rows[0]["id"] == _NORMAL_LABEL
                     and rows[0]["binding_verified_before_after"] is True
                     and all(response.get(key) == request[key] for key in ("id", "mode", "method"))
                     and rows[0]["outcome"] == response.get("outcome"))
        capture = capture_metadata(response) if bound else None
        complete = bool(capture and response.get("capture_complete") is True and response.get("eof") is True
                        and response.get("utf8_valid") is True and response.get("body_truncated") is False
                        and response.get("content_type") == "application/json" and capture["original_json_state"] == "valid"
                        and capture.get("original_body_bytes") == response.get("body_bytes")
                        and capture.get("original_body_sha256") == response.get("body_sha256"))
        outcome = control_state(response, model, probe.path) if complete else "no_response"
        normal = outcome == "pass"
        cleanup = list(report["cleanup_errors"])
        error = None if normal and not report["collection_gap"] and not cleanup else "REQUEST_BINDING_NORMAL_CONTROL_INCOMPLETE"
        fixture = {"profile": NORMAL_PROFILE, "probe_source_sha256": report["probe_source_sha256"],
                   "control_sha256": sha256_hex(canonical_json(probe.model_dump(mode="json")))}
        summary = {"fixture": fixture, "target_sha256": target_sha, "binding_sha256": report["binding_sha256"],
                   "request_body_sha256": sha256_hex(canonical_json(body)), "owner_binding_verified": bound,
                   "response_capture": capture if complete else None, "normal_result": outcome,
                   "output_binding_verified": False}
        artifacts = []
        try:
            path = Path(workdir) / "request_binding_normal_control.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            raw = canonical_json(summary)
            with path.open("xb") as stream:
                if stream.write(raw) != len(raw):
                    raise OSError("incomplete normal control artifact")
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            error = error or "REQUEST_BINDING_NORMAL_CONTROL_ARTIFACT_UNAVAILABLE"
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        config = execution_conditions(target, {NORMAL_OPTION: True, "request_timeout": self.cfg["request_timeout"]}, [])
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": bool(rows), "succeeded": normal if complete else None,
                            "detail": "One fixed JSON functional control. Request and output attribution remain unverified."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations={
                "functional_probes_planned": {"value": [probe.id], "collected": True},
                "functional_probe_results": {"value": {probe.id: outcome}, "collected": True},
                "normal_controls": {"value": False, "collected": True},
                "collection_complete": {"value": False, "collected": True},
                "output_binding_verified": {"value": False, "collected": True},
                "output_binding_gap": {"value": "A single JSON control cannot establish output attribution.", "collected": True},
            },
            artifacts=artifacts, comparability={"fixture_sha256": sha256_hex(canonical_json(fixture)),
                "config_sha256": sha256_hex(canonical_json(config)), "model_inventory_sha256": env.model.inventory_sha256,
                "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, started_at=started, finished_at=utcnow(),
            abort_reason=error, cleanup_errors=cleanup)

    def _api_model(self,ctx):
        return ctx.api_model

    def _template_snapshot(self,ctx,identity,argv,deadline):
        return ctx.cprobe.template_snapshot(identity,argv,ctx.target["engine"].get("command"),_READ_TEMPLATE)

    def run(self,check,env,policy_values,adapter,workdir):
        if NORMAL_OPTION in self.cfg:
            return self._run_normal_control(check, env, policy_values, adapter, workdir)
        try:
            allowed={"target","endpoint","tokenize_endpoint","request_timeout","kubectl_binary","roles",
                     "wrong_key_env","denied_key_env","allow_mutating_routes",*DEFAULTS}
            if set(self.cfg)-allowed or self.cfg.get("allow_mutating_routes",False) is not False or adapter is None:
                raise ValueError
            self.validate_policy(check,policy_values)
            target=strict_json(Path(self.cfg["target"]).read_bytes())
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target))!=self._expected_target_sha256:
                raise ValueError
        except (KeyError,TypeError,ValueError,OSError):
            raise ValueError("KUBERNETES_REQUEST_BINDING_CONFIGURATION_INVALID") from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get("max_duration_seconds",DEFAULTS["max_duration_seconds"]),
            binary=self.cfg.get("kubectl_binary","kubectl"),runner=self._kubectl_runner,profile=REQUEST_BINDING_PROFILE)
        keys=self.keys()
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,
            timeout=float(self.cfg["request_timeout"]),allow_mutating=False,keys=keys,secrets=[v for v in keys.values() if v],
            cprobe=transport,sprobe=transport,workdir=workdir,started=utcnow())
        draft=self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:draft.data_origin=DataOrigin.EXAMPLE
        return draft

    def collect(self,ctx):
        observations,normal,challenges,summary=super().collect(ctx)
        report=ctx.cprobe.report()
        summary["kubernetes"]=report
        if ctx.cprobe.model_mapping.get("schema")=="guard.model-request-mapping/1":
            from .model_mounts import analyze_model_mount
            snapshots=ctx.cprobe.model_snapshots
            target={"schema":"guard-kubernetes-lifecycle-target/1","lab":ctx.target["lab"],
                "run_id":ctx.cprobe.binding["run_id"],"environment_ref":ctx.env.ref().model_dump(mode="json"),
                "kubernetes":{"context":ctx.cprobe.binding["context"],"engine":ctx.cprobe.binding["engine"]}}
            before=snapshots[0] if snapshots else {}
            after=snapshots[-1] if len(snapshots)==2 and not report["collection_gap"] else {}
            summary["model_mount"]=analyze_model_mount(ctx.env,target,ctx.cprobe.model_mapping,before,after)
            summary["model_mount"]["scope"]="first_to_last_verified_ready_snapshot"
            summary["model_mount"]["whole_run_coverage_verified"]=False
        summary["model_mapping_sha256"]=sha256_hex(canonical_json(ctx.cprobe.model_mapping))
        summary["fixture"].update(transport_profile=REQUEST_BINDING_PROFILE,
            probe_source_sha256=report["probe_source_sha256"],model_mapping_sha256=sha256_hex(canonical_json(model_request_conditions(ctx.cprobe.model_mapping))))
        summary["limitations"].append("Kubernetes Pod identity is not actual GPU/tensor/model-consumption or GPU-release proof.")
        return observations,normal,challenges,summary
