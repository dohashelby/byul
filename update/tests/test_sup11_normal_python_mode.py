"""Verify the bounded SUP-11 product path through an isolated transport fixture."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from guard import application
from guard.adapters import vllm_stock_python_mode as stock_adapter
from guard.adapters.vllm_stock_python_mode import CONFIG_SHA256, FILE_SIZES, LOADER, MODEL, STOCK_MODEL, launch_command, observer_profile
from guard.configuration import NORMAL_PYTHON_MODE_SCOPE_ERROR, validate_normal_python_mode_update
from guard.executors import kubernetes_normal_python_mode as stock
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import T0, assert_unconfirmed_security_failure, environment, policy
from tests.test_kubernetes_sampling import World, inputs
from tests.test_sup11_general_observer import INTERPRETER_CONTEXT, PATH_ENTRIES

READY = "2026-10-26T00:00:01Z"
PACK = Path(__file__).resolve().parents[1] / "packs" / "PACK-BASELINE"
SOURCES = {
    "0.30.0": "5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
    "0.31.0": "a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b",
}


def capsule(folder, side, version, *, general=False):
    image = "docker.io/vllm/vllm-openai@sha256:" + SOURCES[version]
    repo, revision, inventory = STOCK_MODEL
    env = environment(id="ENV-" + side, engine={"name": "vllm", "version": version, "image_digest": image},
        model={"id": repo, "revision": revision, "inventory_sha256": inventory},
        runtime={"tp_size": 2, "pp_size": 1, "dp_size": 1, "gpu_count": 2})
    ref = env.ref().model_dump(mode="json")
    target = inputs()
    target.update(lab=False, schema="guard-kubernetes-request-binding-target/1", environment_ref=ref,
                  observed_version=version, sup11_observer_host_path="/fixture/owner/observer/" + side)
    target["engine"].update(command=launch_command(env), image_digest=image)
    target["kubernetes"]["run_id"] = "owner-" + side
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    target["model_mapping"] = {"schema": "guard.model-request-mapping/1", "environment_ref": ref,
        "served_model_name": "fixture-model", "repository_id": repo, "revision": revision,
        "container_mount": "/models", "host_snapshot": "/fixture/model", "inventory_sha256": inventory}
    target["comparison_config"] = {"launch": {"argv": ["/models", "--served-model-name", "fixture-model",
                      "--tensor-parallel-size", "2"], "offline_env": stock._OFFLINE.copy()}}
    target["startup_ready_window"] = {"container_started_at": target["kubernetes"]["engine"]["started_at"],
                                     "ready_at": READY}
    profile = stock_adapter.general_observer_profile(env) if general else observer_profile(env)
    qualification = profile.get("qualification", {})
    producer = stock.GENERAL_PRODUCER_SHA256 if general else stock.PRODUCER_SHA256
    profile_sha = sha256_hex(canonical_json(profile))
    processes = []
    for role, rank in (("service", None), ("worker", 0), ("worker", 1)):
        anchor = profile["service"]["module"] if rank is None else LOADER
        origin = profile["service"]["sha256"] if rank is None else profile["worker_loader_sha256"]
        processes.append({"role": role, "rank": rank, "pid": 1 if rank is None else rank + 10,
            "start_ticks": 42 if rank is None else rank + 43, "python_optimize": 0, "anchor_module": anchor,
            "anchor_sha256": origin, "module": anchor if rank is None else MODEL,
            "module_sha256": origin if rank is None else profile["model_source_sha256"],
            "producer_sha256": producer})
        if general:
            path = {**INTERPRETER_CONTEXT, "entries": PATH_ENTRIES}
            processes[-1].update(activation_sha256=qualification[f"{role}_activation_sha256"],
                                 sitecustomize_sha256=qualification["sitecustomize_sha256"],
                                 general_plugins=list(stock_adapter.GENERAL_PLUGIN_NAMES),
                                 ordinary_import_path={"activation": deepcopy(path), "anchor": deepcopy(path)})
    readback = {"profile_sha256": profile_sha, "producer_sha256": producer, "processes": processes}
    if general:
        readback["plugin_metadata"] = {name: sha256_hex(raw) for name, raw in stock_adapter.GENERAL_METADATA.items()}
    provenance = {"schema": stock.PROVENANCE_SCHEMA, "environment_ref": ref,
        "binding_sha256": sha256_hex(canonical_json(target["kubernetes"])), "profile": profile,
        "profile_sha256": profile_sha, "readback": readback,
        "oci_source_qualification_sha256": qualification.get("oci_source_qualification_sha256", "a" * 64)}
    folder.mkdir()
    target_path, provenance_path = folder / "target.json", folder / "provenance.json"
    provenance_path.write_bytes(canonical_json(provenance))
    target["sup11_provenance_sha256"] = sha256_hex(provenance_path.read_bytes())
    target_path.write_bytes(canonical_json(target))
    return env, target, provenance, target_path, provenance_path


class OwnedWorld(World):
    def runner(self, argv, **kwargs):
        if "exec" in argv:
            assert "--container=engine" in argv and {"-I", "-S", "-B"} <= set(argv)
            result = {"config_sha256": CONFIG_SHA256, "file_sizes": FILE_SIZES, "read_only": True,
                      "auto_map_present": False, "pid1_start_ticks": 42}
            if argv[-2] == stock._READER:
                result = self.readback
            else:
                assert argv[-2] == stock._INVENTORY
        else:
            result = json.loads(super().runner(argv, **kwargs)["stdout_bytes"])
            if "namespaces" in argv:
                for item in result["items"]:
                    item["kind"] = "Namespace"
            else:
                result["kind"] = "Pod"
                result["metadata"]["labels"]["guard.run"] = self.target["kubernetes"]["run_id"]
                if result["metadata"]["name"] == "engine-pod":
                    self._stock_pod(result)
        return {"returncode": 0, "stdout_bytes": canonical_json(result), "stderr_bytes": b"",
                "capture": {"complete": True}}

    def _stock_pod(self, pod):
        container, = pod["spec"]["containers"]
        selected_mounts = {"models": self.target["model_mapping"]["host_snapshot"],
            "guard-sup11": self.target["sup11_observer_host_path"],
            "cache": "/fixture/owner/cache/" + self.target["kubernetes"]["run_id"]}
        observer_env = stock._GENERAL_OBSERVER_ENV if "plugin_metadata" in self.readback else stock._OBSERVER_ENV
        container.update(command=self.target["engine"]["command"], args=self.target["comparison_config"]["launch"]["argv"],
            env=[{"name": key, "value": value} for key, value in (stock._OFFLINE | observer_env).items()],
            volumeMounts=[{"name": name, "mountPath": "/" + name, "readOnly": name != "cache"} for name in selected_mounts],
            readinessProbe={"httpGet": {"path": "/health", "port": 8000}})
        pod["spec"]["volumes"] = [{"name": name, "hostPath": {"path": path, "type": "Directory"}}
                                  for name, path in selected_mounts.items()]
        if "plugin_metadata" in self.readback:
            container["workingDir"] = stock_adapter.GENERAL_WORKDIR
        pod["status"]["conditions"][0]["lastTransitionTime"] = READY


@pytest.mark.parametrize("missing_rank", [False, True])
@pytest.mark.parametrize("general", [False, True], ids=["legacy", "stock-general-plugin"])
def test_prebound_update_publishes_only_observed_default_mode(tmp_path, monkeypatch, missing_rank, general):
    pack = load_pack(PACK)
    pol = policy(pack.ref(), status="CONFIRMED", confirmed_by="fixture owner", confirmed_at=T0,
                 scope={"include": ["SUP-11"]}, values={})
    if general:
        pins = {
            "oci_source_qualification_sha256": "1" * 64, "plugin_discovery_sha256": "2" * 64,
            "site_roster_sha256": "3" * 64,
            "ordinary_import_path_sha256": {
                role: sha256_hex(canonical_json(PATH_ENTRIES)) for role in stock_adapter.GENERAL_PATH_ROLES},
            "ordinary_interpreter_context_sha256": sha256_hex(canonical_json(INTERPRETER_CONTEXT)),
        }
        monkeypatch.setattr(stock_adapter, "_GENERAL_QUALIFICATIONS", {version: pins for version in SOURCES})
    rows = {side: capsule(tmp_path / side, side, version, general=general) for side, version in
            (("baseline", "0.30.0"), ("candidate", "0.31.0"))}
    if general:
        env = rows["baseline"][0]
        exported = stock_adapter.general_observer_profile(env)
        exported["qualification"]["ordinary_import_path_sha256"]["service"] = "f" * 64
        with pytest.raises(ValueError, match="SUP11_INSTALLED_SOURCE_PROFILE_INVALID"):
            stock_adapter.validate_observer_profile(exported, env)
    worlds = {}
    cfg = {"adapter": {"name": "vllm"}, "executors": {"supply_boot": "kubernetes"},
           "update": {"mode": "external", "kubernetes_candidate_binding": "prebound", "allowed_checks": ["SUP-11"]},
           "sides": {}}
    for side, (_, target, provenance, target_path, provenance_path) in rows.items():
        world = worlds[str(target_path)] = OwnedWorld(target)
        world.readback = deepcopy(provenance["readback"])
        if missing_rank and side == "candidate":
            world.readback["processes"].pop()
        cfg["sides"][side] = {"check_config": {"SUP-11": {stock.NORMAL_PYTHON_MODE_OPTION: True,
            "target": str(target_path), "provenance": str(provenance_path)}}}
    original = stock.KubernetesNormalPythonModeExecutor.__init__

    def inject(self, settings, **kwargs):
        original(self, settings, runner=worlds[settings["target"]].runner, **kwargs)

    def no_subprocess(*args, **kwargs):
        pytest.fail("The fixture must not start a local process")

    monkeypatch.setattr(stock.KubernetesNormalPythonModeExecutor, "__init__", inject)
    monkeypatch.setattr(subprocess, "Popen", no_subprocess)
    envs = [row[0] for row in rows.values()]
    prepared = application.update_readiness(pack, envs, pol, cfg)
    if not prepared["ready"] or not prepared["execution_ready"]:
        pytest.fail(json.dumps(prepared, ensure_ascii=False, sort_keys=True))
    store = Store(tmp_path / "store")
    report, _ = application.execute_update(store, pack, envs, pol, cfg, tmp_path / "output", utcnow())
    results = next(row for row in report.by_check if row.check_id == "SUP-11")
    for side, (_, _, _, target_path, provenance_path) in rows.items():
        result = store.resolve(getattr(results, side).result)
        evd = store.resolve(result.evidence)
        expected = ExecStatus.ABORTED if missing_rank and side == "candidate" else ExecStatus.COMPLETED
        assert evd.execution.status == expected
        assert evd.execution.run_binding["files"] == {
            "python_mode_target_sha256": sha256_hex(target_path.read_bytes()),
            "python_mode_provenance_sha256": sha256_hex(provenance_path.read_bytes())}
        assert evd.data_origin == DataOrigin.EXAMPLE and evd.normal_control.succeeded is None
        assert not any(challenge.attempted for challenge in evd.challenges)
        assert not evd.observations["supply_boot_normal_verified"].collected
        assert_unconfirmed_security_failure(result)
        assert ("ABORTED" in result.security_failure_tags) is (expected == ExecStatus.ABORTED)


def test_general_plugin_requires_qualified_installed_sources(tmp_path, monkeypatch):
    env, target, provenance, _, _ = capsule(tmp_path / "single", "baseline", "0.30.0")
    monkeypatch.setattr(stock_adapter, "_GENERAL_QUALIFICATIONS", {})
    provenance["profile"]["schema"] = stock_adapter.GENERAL_PROFILE_SCHEMA
    provenance["profile_sha256"] = sha256_hex(canonical_json(provenance["profile"]))
    with pytest.raises(ValueError, match="SUP11_GENERAL_OBSERVER_OCI_QUALIFICATION_UNAVAILABLE"):
        stock.prepared_provenance(provenance, env, sha256_hex(canonical_json(target["kubernetes"])))


def test_general_path_qualification_diagnostic_is_not_official(tmp_path):
    env, target, provenance, _, _ = capsule(tmp_path / "single", "baseline", "0.30.0")
    provenance["profile"] = stock_adapter.general_observer_diagnostic_profile(env)
    provenance["profile_sha256"] = sha256_hex(canonical_json(provenance["profile"]))
    with pytest.raises(ValueError, match="SUP11_INSTALLED_SOURCE_PROFILE_INVALID"):
        stock.prepared_provenance(provenance, env, sha256_hex(canonical_json(target["kubernetes"])))


def test_replaced_profile_hash_cannot_qualify(tmp_path):
    env, target, provenance, _, _ = capsule(tmp_path / "single", "baseline", "0.30.0")
    provenance["profile"]["service"]["sha256"] = "b" * 64
    provenance["profile_sha256"] = sha256_hex(canonical_json(provenance["profile"]))
    with pytest.raises(ValueError, match="SUP11_INSTALLED_SOURCE_PROFILE_INVALID"):
        stock.prepared_provenance(provenance, env, sha256_hex(canonical_json(target["kubernetes"])))


def test_attestation_override_cannot_expand_normal_mode_scope(tmp_path):
    cfg = {"executors": {"supply_boot": "kubernetes"}, "sides": {}}
    for side in ("baseline", "candidate"):
        cfg["sides"][side] = {"check_config": {"SUP-11": {stock.NORMAL_PYTHON_MODE_OPTION: True,
            "target": str(tmp_path / side / "target.json"), "provenance": str(tmp_path / side / "provenance.json")}}}
    validate_normal_python_mode_update(cfg)
    cfg["sides"]["candidate"]["record_attestation"] = {"enabled": True}
    with pytest.raises(ValueError, match=NORMAL_PYTHON_MODE_SCOPE_ERROR):
        validate_normal_python_mode_update(cfg)
