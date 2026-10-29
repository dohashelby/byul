"""기존 common 응답/제안 producer 주입→실제 전용 executor/CLI. 실제 cluster 아님."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.container_config import ContainerConfigExecutor
from guard.executors.kubernetes_image_approval import KubernetesImageApprovalExecutor
from guard.models import DataOrigin, canonical_json, sha256_hex, load_pack
from guard.core.select import select
from guard.core.run import run_plan, RunConfig
from guard.runtime.kubernetes_binding import project_owned_snapshot
from guard.runtime.kubernetes_http import KubernetesHTTP
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_kubernetes_running_image_approval import inputs, snapshot, PIN, OTHER, PRIVATE


class World:
    def __init__(self, *, gap=None):
        self.env, self.target = inputs()
        self.env = self.env.model_copy(update={"data_origin": DataOrigin.MEASURED}).seal()
        self.target.update(lab=False, environment_ref=self.env.ref().model_dump(mode="json"),
            model_mapping={"repository_id": self.env.model.id, "revision": self.env.model.revision, "api_model": "alias"})
        self.calls, self.snapshots, self.gap = [], 0, gap

    def runner(self, argv, **kwargs):
        assert "exec" not in argv and "--context=example-context" in argv
        self.calls.append(argv)
        if "namespaces" in argv:
            value = {"kind": "List", "items": [snapshot(self.target, role)["namespace"] for role in ("engine", "probe")]}
        else:
            role = "engine" if "engine-pod" in argv else "probe"
            value = snapshot(self.target, role)["pod"]
            if self.gap == "restart" and self.snapshots == 2 and role == "engine":
                value["status"]["containerStatuses"][0]["restartCount"] += 1
        capture = {"complete": not (self.gap == "incomplete" and self.snapshots == 2)}
        if self.gap == "cleanup" and self.snapshots == 2:
            capture.update(pipes_closed=False, read_errors=["child_reap"])
        return {"returncode": 0, "stdout_bytes": json.dumps(value).encode(), "stderr_bytes": PRIVATE.encode(),
                "capture": capture}

    def producer(self, transport):
        # A1의 공개 owned_snapshot 연결 전 제안 경계만 주입한다. 실제 공통
        # bounded API 파서/소유세대 검증/정제 생산자는 그대로 실행한다.
        self.snapshots += 1
        if self.gap == "before" or self.gap == "after" and self.snapshots == 2:
            raise OSError(PRIVATE)
        pods = transport._verify()
        expected = transport.binding["engine"]
        return project_owned_snapshot(pods["engine"], snapshot(self.target)["namespace"],
                                      expected, transport.binding["run_id"])


def install_producer(monkeypatch, world):
    monkeypatch.setattr(KubernetesHTTP, "owned_snapshot", lambda self: world.producer(self), raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("Kubernetes image path cannot invoke Docker or inference")
    monkeypatch.setattr(ContainerConfigExecutor, "_docker_obj", forbidden)


def proposed_pack(root):
    """A1 반영 제안의 전용 EXAMPLE fixture. 정본 팩/공유 builder를 수정하지 않는다."""
    original = baseline_pack().checks["SUP-04"].model_dump(mode="json")
    check = deepcopy(original)
    check["revision"] += 1
    check["required_observations"].extend([
        {"key": "kubernetes_image_api_identity_stable", "type": "bool"},
        {"key": "kubernetes_image_api_identity_before", "type": "dict"},
        {"key": "kubernetes_image_api_identity_after", "type": "dict"},
    ])
    raw = yaml.safe_dump(check, allow_unicode=True, sort_keys=False).encode("utf-8")
    folder = root / "checks"
    folder.mkdir(parents=True)
    (folder / "SUP-04.yaml").write_bytes(raw)
    manifest = {"pack_id": "PACK-EXAMPLE-KUBE-IMAGE", "revision": 1, "engines": ["vllm"],
        "checks": ["SUP-04"], "files_sha256": [{"path": "checks/SUP-04.yaml", "sha256": sha256_hex(raw)}],
        "review": {"status": "DRAFT", "reviewer": None, "reviewed_at": None}}
    (root / "pack.yaml").write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    assert baseline_pack().checks["SUP-04"].model_dump(mode="json") == original
    return load_pack(root)


@pytest.mark.parametrize("gap", [None, "before", "after", "restart", "incomplete", "cleanup"])
def test_real_executor_preserves_partial_api_metadata_and_never_claims_runtime_approval(tmp_path, monkeypatch, gap):
    world = World(gap=gap)
    install_producer(monkeypatch, world)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(world.target), encoding="utf-8")
    executor = KubernetesImageApprovalExecutor({"target": str(path)}, runner=world.runner, environ={})
    draft = executor.run(baseline_pack().checks["SUP-04"], world.env, {"approved_image_digests": []},
                         VllmAdapter(), tmp_path / "work")
    assert draft.data_origin == DataOrigin.EXAMPLE  # lab=false·ENV MEASURED여도 runner 주입은 EXAMPLE.
    assert draft.status.value == ("COMPLETED" if gap is None else "ABORTED")
    assert draft.observations["kubernetes_image_api_identity_before"].collected is (gap != "before")
    assert draft.observations["kubernetes_image_api_identity_stable"].value is (gap in {None, "cleanup"})
    assert not draft.observations["running_image_identity_verified"].value
    assert not draft.observations["running_image_unapproved"].collected
    assert not draft.normal_control.attempted
    if gap is None:
        assert len(world.calls) == 6
    if gap == "cleanup":
        assert draft.cleanup_errors == ["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]
    for artifact in draft.artifacts:
        assert PRIVATE not in artifact.path.read_text(encoding="utf-8")


def test_missing_public_common_producer_aborts_before_any_api_or_docker(tmp_path, monkeypatch):
    world = World()
    monkeypatch.delattr(KubernetesHTTP, "owned_snapshot", raising=False)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(world.target), encoding="utf-8")
    executor = KubernetesImageApprovalExecutor({"target": str(path)}, runner=world.runner)
    with pytest.raises(ValueError, match="^KUBERNETES_IMAGE_SNAPSHOT_PRODUCER_UNAVAILABLE$"):
        executor.run(baseline_pack().checks["SUP-04"], world.env, {"approved_image_digests": [PIN]}, VllmAdapter(), tmp_path)
    assert not world.calls


@pytest.mark.parametrize("damage", ["hash", "ref", "policy", "wrong-check", "timeout", "unknown-config"])
def test_unverified_configuration_is_rejected_before_metadata_query(tmp_path, monkeypatch, damage):
    world = World()
    install_producer(monkeypatch, world)
    config = {}
    values = {"approved_image_digests": [PIN]}
    selected = baseline_pack().checks["SUP-04"]
    expected_hash = sha256_hex(canonical_json(world.target))
    if damage == "hash":
        expected_hash = "0" * 64
    elif damage == "ref":
        world.target["environment_ref"]["revision"] += 1
        expected_hash = None
    elif damage == "policy":
        values = {}
    elif damage == "wrong-check":
        selected = baseline_pack().checks["PLT-01"]
    elif damage == "timeout":
        config["request_timeout"] = float("nan")
    else:
        config["allow_mutating_routes"] = True
    path = tmp_path / "target.json"
    path.write_text(json.dumps(world.target), encoding="utf-8")
    executor = KubernetesImageApprovalExecutor({"target": str(path), **config}, runner=world.runner,
                                             expected_target_sha256=expected_hash)
    with pytest.raises((ValueError, RuntimeError)):
        executor.run(selected, world.env, values, VllmAdapter(), tmp_path)
    assert not world.calls


@pytest.mark.parametrize("mode", ["approved", "unapproved", "empty", "missing", "later-read", "attachment"])
def test_existing_product_cli_uses_the_executor_and_replays_exact_refs(tmp_path, monkeypatch, mode):
    world = World(gap="after" if mode == "later-read" else None)
    install_producer(monkeypatch, world)
    pack = proposed_pack(tmp_path / "proposed-pack")
    values = {} if mode == "missing" else {"approved_image_digests": [PIN] if mode == "approved" else [] if mode == "empty" else [OTHER]}
    pol = policy(pack.ref(), scope={"include": ["SUP-04"], "exclude": []}, values={"SUP-04": values})
    env_path, policy_path, target_path = [tmp_path / n for n in ("env.json", "policy.json", "target.json")]
    env_path.write_bytes(canonical_json(world.env.to_dict()))
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    target_path.write_text(json.dumps(world.target), encoding="utf-8")
    config = tmp_path / "guard.toml"
    config.write_text('[executors]\ncontainer_config="kubernetes"\n[adapter]\nname="vllm"\n'
                      '[container_config]\ntarget=' + json.dumps(target_path.as_posix()) + '\n', encoding="utf-8")
    # A1의 registry 확정 전 동일 제품 factory 연결만 주입한다. 가짜 실행기 없음.
    monkeypatch.setitem(registry.EXECUTORS, "container_config:kubernetes", lambda raw:
                        KubernetesImageApprovalExecutor(raw["container_config"], runner=world.runner, environ={}))
    original_open = Path.open
    if mode == "attachment":
        def fail_attachment(path, *args, **kwargs):
            if path.name == "probe_log.jsonl" and (args[0] if args else kwargs.get("mode")) == "w":
                raise OSError(PRIVATE)
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", fail_attachment)
    runner, store_path = CliRunner(), tmp_path / "store"
    common = ["--config", str(config), "--store", str(store_path)]
    def cli(args):
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.stdout, repr(result.exception))
        assert PRIVATE not in result.stdout
        return result.stdout
    plan = cli(["select", "--pack", str(pack.path), "--env", str(env_path), "--policy", str(policy_path), *common]).strip().splitlines()[-1]
    cli(["run", "--plan", plan, "--pack", str(pack.path), *common])
    cli(["judge", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(), "--json", str(tmp_path / "report.json"), *common])
    cli(["verify", *common])
    store = Store(store_path)
    evd, = store.find_latest("EVD", lambda r: True)
    res, = store.find_latest("RES", lambda r: True)
    rpt, = store.find_latest("RPT", lambda r: True)
    if mode == "missing":
        assert evd.execution.executor is None and not world.calls
    else:
        assert evd.execution.executor.name == "container_config" and evd.execution.executor.version == "0.1.0"
    assert res.verdicts.security.value == "UNKNOWN" and evd.data_origin == DataOrigin.EXAMPLE
    assert evd.execution.status.value == ("NOT_RUN" if mode == "missing" else "ABORTED" if mode in {"later-read", "attachment"} else "COMPLETED")
    if mode != "missing":
        assert evd.observations["kubernetes_image_api_identity_before"].collected
        assert not evd.observations["running_image_unapproved"].collected
    if mode == "attachment":
        assert "ARTIFACT_CAPTURE_INCOMPLETE" in evd.execution.abort_reason
    assert evd.ref() in rpt.inputs_manifest and res.ref() in rpt.inputs_manifest
    bundle, restored_path = tmp_path / "bundle.zip", tmp_path / "restored"
    cli(["evidence", "export", "--store", str(store_path), "--output", str(bundle)])
    cli(["evidence", "restore", "--bundle", str(bundle), "--destination", str(restored_path)])
    target_path.unlink()
    def forbidden(*args, **kwargs):
        pytest.fail("saved Ref replay must not collect")
    monkeypatch.setattr(KubernetesImageApprovalExecutor, "collect", forbidden)
    cli(["judge", "--plan", plan, "--pack", str(pack.path), "--as-of", T0.isoformat(), "--config", str(config), "--store", str(restored_path)])
    cli(["verify", "--config", str(config), "--store", str(restored_path)])
    restored = Store(restored_path)
    replayed, = restored.find_latest("RES", lambda r: True)
    assert replayed.ref() == res.ref() and restored.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()


def test_current_pack_keeps_its_exact_observation_contract_until_A1_updates_it(tmp_path, monkeypatch):
    world = World()
    install_producer(monkeypatch, world)
    pack = baseline_pack()
    path = tmp_path / "target.json"
    path.write_text(json.dumps(world.target), encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS, "container_config:kubernetes", lambda raw:
                        KubernetesImageApprovalExecutor({"target": str(path)}, runner=world.runner))
    pol = policy(pack.ref(), scope={"include": ["SUP-04"], "exclude": []},
                 values={"SUP-04": {"approved_image_digests": [PIN]}})
    store = Store(tmp_path / "store")
    store.put_many([world.env, pol])
    plan = select(pack, [world.env], pol)
    store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"container_config": "kubernetes"}, adapter="vllm"))
    assert evd.execution.status.value == "COMPLETED"
    assert set(evd.observations) == {row.key for row in pack.checks["SUP-04"].required_observations}
    assert "kubernetes_image_api_identity_before" not in evd.observations
    assert pack.manifest.revision == 31 and not evd.observations["running_image_unapproved"].collected
