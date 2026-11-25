"""Owned normal SUP-08 calls only the sealed text/image control and a stock-file reader."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from guard import application
from guard.adapters.vllm_release_profiles import release_profile
from guard.executors import kubernetes_normal_code_selection as stock
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import environment, policy
from tests.test_kubernetes_sampling import World, inputs

PACK = Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE"
VALUES = {"code_selection_fields": ["code_revision", "mm_processor_kwargs.code_revision"],
          "code_selection_allowed": False, "rejection_codes": ["400", "422"],
          "approved_code_revisions": ["a239c75031d0d32a44f2a89135f574c2318244de"]}


def capsule(folder, version):
    repo, revision, inventory = stock.MODEL
    release = release_profile(version)
    image = release["images"][0]
    env = environment(id=f"ENV-TEST-{version}", engine={"name": "vllm", "version": version, "image_digest": image},
                      model={"id": repo, "revision": revision, "inventory_sha256": inventory})
    binding = inputs()
    ref = env.ref().model_dump(mode="json")
    binding.update(schema="guard-kubernetes-request-binding-target/1", lab=False,
                   environment_ref=ref, observed_version=version)
    binding["engine"].update(command=release["launch"]["command"], image_digest=image)
    binding["kubernetes"]["engine"].update(image=image, image_id=image)
    binding["model_mapping"] = {"schema": "guard.model-request-mapping/1", "environment_ref": ref,
        "served_model_name": "fixture-model", "repository_id": repo, "revision": revision,
        "container_mount": "/models", "host_snapshot": "/srv/owned-model", "inventory_sha256": inventory}
    binding["comparison_config"] = {"launch": {"argv": ["/models", "--served-model-name", "fixture-model"],
                                              "offline_env": deepcopy(stock._OFFLINE)}}
    folder.mkdir(exist_ok=True)
    saved = folder / "target.json"
    saved.write_bytes(canonical_json(binding))
    return env, binding, saved


class OwnedWorld(World):
    def __init__(self, binding, fault=None):
        super().__init__(binding)
        self.fault = fault
        self.reads = 0

    def runner(self, args, **kwargs):
        if "exec" in args and "--container=engine" in args:
            self.reads += 1
            packages = {}
            for name, source in stock.PACKAGE_FILES.items():
                packages[name] = {"version": self.target["observed_version"] if name == "vllm" else "4.57.1",
                    "metadata_sha256": "a"*64, "source_file": source, "source_sha256": "b"*64}
            if self.fault == "changed-source" and self.reads == 2:
                packages["vllm"]["source_sha256"] = "c"*64
            result = {"schema": stock.SOURCE_SCHEMA, "model_files": dict(stock.MODEL_FILES), "packages": packages}
            if self.fault == "wrong-model-file":
                result["model_files"]["config.json"] = "d"*64
            return self.response(result)
        if "exec" in args:
            request, = json.loads(kwargs["input_bytes"])["requests"]
            self.execs.append(request)
            raw = json.dumps({"model": request["body"]["model"], "choices": [{"finish_reason": "length",
                "message": {"role": "assistant", "content": "private-output"}}],
                "usage": {"prompt_tokens": 6, "completion_tokens": 1, "total_tokens": 7}})
            response = {"id": request["id"], "mode": "json", "method": "POST", "outcome": 200, "body": raw,
                        "body_bytes": len(raw.encode()), "body_sha256": sha256_hex(raw.encode()),
                        "body_truncated": False, "utf8_valid": True, "capture_complete": True, "eof": True}
            return self.response([response])
        value = json.loads(super().runner(args, **kwargs)["stdout_bytes"])
        if "namespaces" in args:
            for item in value["items"]:
                item["kind"] = "Namespace"
        else:
            value["kind"] = "Pod"
            if value["metadata"]["name"] == "engine-pod":
                if self.fault == "foreign-owner":
                    value["metadata"]["uid"] = "replacement"
                spec = value["spec"]
                command = self.target["engine"]["command"]
                argv = list(self.target["comparison_config"]["launch"]["argv"])
                container = spec["containers"][0]
                container.update(command=command, args=argv, env=[{"name": name, "value": setting}
                                                                 for name, setting in stock._OFFLINE.items()])
                container["volumeMounts"] = [{"name": "model", "mountPath": "/models", "readOnly": True}]
                spec["volumes"] = [{"name": "model", "hostPath": {"path": "/srv/owned-model", "type": "Directory"}}]
                if self.fault == "foreign-mount":
                    container["volumeMounts"].append({"name": "foreign", "mountPath": "/tmp"})
                if self.fault == "online-env":
                    container["env"][0]["value"] = "0"
        return self.response(value)

    @staticmethod
    def response(value):
        return {"returncode": 0, "stdout_bytes": json.dumps(value).encode(), "stderr_bytes": b"",
                "capture": {"complete": True}}


@pytest.mark.parametrize("fault, reads, sends", [
    (None, 2, 1), ("foreign-owner", 0, 0), ("foreign-mount", 0, 0), ("online-env", 0, 0),
    ("wrong-model-file", 1, 0), ("changed-source", 2, 1),
])
def test_stock_read_and_one_ordinary_request_are_closed(tmp_path, fault, reads, sends):
    env, binding, saved = capsule(tmp_path, "0.30.0")
    world = OwnedWorld(binding, fault)
    check = load_pack(PACK).checks["SUP-08"]
    executor = stock.KubernetesNormalCodeSelectionExecutor(
        {"target": str(saved), stock.NORMAL_CODE_SELECTION_OPTION: True}, runner=world.runner,
        expected_target_sha256=sha256_hex(canonical_json(binding)))
    draft = executor.run(check, env, VALUES, None, tmp_path / "work")
    assert draft.status == (ExecStatus.COMPLETED if fault is None else ExecStatus.ABORTED)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert world.reads == reads and len(world.execs) == sends
    assert all(not step.attempted for step in draft.challenges)
    assert not draft.observations["code_selection_trials"].collected
    assert not draft.observations["code_selection_final_code_verified"].collected
    raw = draft.artifacts[0].path.read_text()
    assert "private-output" not in raw and "data:image/png;base64" not in raw
    summary = json.loads(raw)
    assert summary["stock_source_verified"] is (fault is None)
    if sends:
        request, = world.execs
        assert request["body"] == stock.normal_body("fixture-model")
        assert request["headers"] == {}
        assert request["url"].endswith(stock.NORMAL_PATH)


def test_only_the_prebound_paired_scope_is_prepared_and_used(tmp_path, monkeypatch):
    pack = load_pack(PACK)
    pol = policy(pack.ref(), scope={"include": ["SUP-08"]}, values={"SUP-08": VALUES})
    baseline, candidate = [capsule(tmp_path / side, version) for side, version in
                           (("baseline", "0.30.0"), ("candidate", "0.31.0"))]
    cfg = {"adapter": {"name": "vllm"}, "executors": {"input_validation": "kubernetes"},
           "update": {"mode": "external", "kubernetes_candidate_binding": "prebound", "allowed_checks": ["SUP-08"]},
           "sides": {label: {"check_config": {"SUP-08": {"target": str(row[2]),
                      stock.NORMAL_CODE_SELECTION_OPTION: True}}}
                     for label, row in (("baseline", baseline), ("candidate", candidate))}}
    envs = [baseline[0], candidate[0]]

    def no_dispatch(*args, **kwargs):
        raise AssertionError("readiness must not dispatch")

    monkeypatch.setattr(subprocess, "Popen", no_dispatch)
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert ready["ready"], ready
    wrong = deepcopy(cfg)
    wrong["sides"]["candidate"]["check_config"]["SUP-08"]["unapproved_revision"] = "untrusted"
    assert not application.update_readiness(pack, envs, pol, wrong)["ready"]
    changed = []

    def owner_missing(transport, target):
        changed.append((transport, target))
        raise ValueError("SUP08_STOCK_NORMAL_SOURCE_UNAVAILABLE")

    monkeypatch.setattr(stock, "_read_proof", owner_missing)
    store = Store(tmp_path / "store")
    report, _ = application.execute_update(store, pack, envs, pol, cfg, tmp_path / "update", utcnow())
    row = next(item for item in report.by_check if item.check_id == "SUP-08")
    ev = store.resolve(store.resolve(row.baseline.result).evidence)
    assert ev.execution.status == ExecStatus.ABORTED
    assert ev.execution.run_binding["files"]["code_selection_target_sha256"] == sha256_hex(baseline[2].read_bytes())
    assert not any(step.attempted for step in ev.challenges)
    assert not ev.normal_control.attempted and len(changed) == 1


def test_stdlib_reader_fingerprints_stock_source_without_importing(tmp_path, capsys):
    import runpy

    asset = Path(stock.__file__).parents[1] / "runtime/assets/sup08_stock_source.py.txt"
    reader = runpy.run_path(str(asset))
    capsys.readouterr()
    for name, (relative, expected_class) in reader["DISTRIBUTIONS"].items():
        source = tmp_path / relative
        source.parent.mkdir(parents=True)
        # Any accidental import of this test fixture will fail.
        content = f"raise RuntimeError('not an import')\nclass {expected_class}: pass\n"
        source.write_text(content)
        dist = tmp_path / f"{name}-1.2.3.dist-info"
        dist.mkdir()
        (dist / "METADATA").write_text(f"Name: {name}\nVersion: 1.2.3\n")
    packages = reader["selected_files"]([tmp_path])
    assert set(packages) == {"vllm", "transformers"}
    for name, row in packages.items():
        assert row["version"] == "1.2.3"
        assert row["source_sha256"] == sha256_hex((tmp_path / stock.PACKAGE_FILES[name]).read_bytes())
    metadata = tmp_path / "vllm-1.2.3.dist-info/METADATA"
    replacement = tmp_path / "outside"
    metadata.rename(replacement)
    metadata.symlink_to(replacement)
    with pytest.raises(OSError):
        reader["selected_files"]([tmp_path])
