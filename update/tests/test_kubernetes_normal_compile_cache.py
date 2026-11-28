"""Check the current-boot compiler projection and fixed no-content reader."""

import json
import stat
import sys

import pytest

from guard.adapters.engine_provider import EngineProvider, get_provider
from guard.core.run import _normalize
from guard.executors import kubernetes_normal_compile_cache as compile_cache
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import capture_pipes
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_normal_startup_load import Adapter, ROOT, STARTED, StartupWorld, _inputs as startup_inputs

CACHE_ID = "abc012def3"
CACHE_PATH = f"/cache/vllm/torch_compile_cache/{CACHE_ID}/rank_0_0/backbone"
ARTIFACT = "vllm_compile_cache.py"
PRIVATE = "EXAMPLE_unrelated_console_text"
FILES = ("compilation/backends.py", "compilation/monitor.py", "logger.py", "logging_utils/formatter.py")
AOT_ID = "09" * 32
AOT_PATH = f"/cache/vllm/torch_compile_cache/torch_aot_compile/{AOT_ID}/rank_0_0/model"
AOT_SOURCE = "compilation/decorators.py"
AOT_PINS = {
    "0.30.0": ("5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
               "cab6962e8a1fead59c75f5ed8cb4732f9e5441ff972030c2f4b1c4ba0c58de3c", 719, 313),
    "0.31.0": ("a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b",
               "ebbb909015f31c9d71cddef6718dc77459e05e3c70f49383f6b639595272c8a7", 717, 312),
}


class CompileAdapter(Adapter):
    def compile_cache_source(self, env):
        return {"file_sha256": {name: sha256_hex(name.encode()) for name in FILES[:2]},
                "directory_call_linenumber": 1090 if self.line == 5354 else 1094, "completion_call_linenumber": 53,
                "cache_env": "VLLM_CACHE_ROOT", "cache_root": "/cache/vllm",
                "relative_directory_pattern": r"torch_compile_cache/[0-9a-f]{10}/rank_0_0/backbone",
                "artifact_name": ARTIFACT}


class CompileWorld(StartupWorld):
    def __init__(self, target, adapter, modern, fault, *, aot=None):
        super().__init__(target, adapter, modern, fault)
        self.metadata_reads = []
        host = "(EngineCore pid=96) " if modern else ""
        src = adapter.compile_cache_source(None)["directory_call_linenumber"]
        selected = "/models/fixture" if fault == "wrong-directory" else CACHE_PATH
        completion = (f"2026-10-26T00:00:08.534Z {host}INFO 10-26 00:00:08 [monitor.py:53] "
                      "torch.compile took 3.27 s in total\n")
        if fault == "different-process":
            completion = completion.replace(f"{host}INFO", "(EngineCore pid=97) INFO")
        self.raw = (f"2026-10-26T00:00:03.324Z INFO {PRIVATE}\rprogress ended\n"
                    f"2026-10-26T00:00:04.517Z {host}INFO 10-26 00:00:04 [backends.py:{src}] "
                    f"Using cache directory: {selected} for vLLM's torch.compile\n").encode()
        if fault != "no-completed-compile":
            self.raw += completion.encode()
        if fault == "duplicate-compile":
            self.raw += completion.encode()
        self.aot = aot
        self.aot_pin = None
        if aot:
            version = "0.31.0" if modern else "0.30.0"
            _, self.aot_pin, save_line, load_line = AOT_PINS[version]
            line, action = ((save_line, "saved AOT compiled function to") if aot == "save"
                            else (load_line, "Directly load AOT compilation from path"))
            if fault == "wrong-aot-line":
                line += 1
            event = f"2026-10-26T00:00:08.534000712Z {host}INFO 10-26 00:00:08 [decorators.py:{line}] {action} {AOT_PATH}\n".encode()
            self.raw += event * (2 if fault == "duplicate-aot" else 1)

    def pod_readback(self, role, document, after):
        super().pod_readback(role, document, after)
        if role == "engine":
            container, = document["spec"]["containers"]
            container["env"].append({"name": "VLLM_CACHE_ROOT", "value": "/cache/vllm"})
            container["volumeMounts"].append({"name": "cache", "mountPath": "/cache"})
            if self.fault == "overlay":
                container["volumeMounts"].append({"name": "nested", "mountPath": "/cache/vllm/torch_compile_cache"})
            if self.fault == "compile-override":
                container["env"].append({"name": "VLLM_USE_AOT_COMPILE", "value": "1"})
            if self.fault == "changed-after-stat" and self.metadata_reads:
                document["metadata"]["uid"] = "new-generation"

    def runner(self, argv, **kwargs):
        if "exec" not in argv:
            answer = super().runner(argv, **kwargs)
            if "logs" in argv and self.fault == "prefix-changed":
                answer["stdout_bytes"] = answer["stdout_bytes"].replace(b"3.27 s", b"3.28 s")
            return answer
        engine = self.target["kubernetes"]["engine"]
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        args = argv[3:]
        if "input_bytes" in kwargs:
            assert args[:10] == [f"--namespace={engine['namespace']}", "exec", "-i", engine["pod"],
                                 "--container=engine", "--", "python3", "-I", "-S", "-c"]
            request = json.loads(kwargs["input_bytes"])
            expected_paths = ({AOT_SOURCE, *FILES[2:]} if self.aot else set(FILES))
            assert request["roots"] == [ROOT] and {row["relative"] for row in request["files"]} == expected_paths
            self.hash_calls.append(request)
            files = [{**row, "sha256": sha256_hex(row["relative"].encode()), "state": "hashed"}
                     for row in request["files"]]
            if self.aot:
                selected, = [row for row in files if row["relative"] == AOT_SOURCE]
                selected["sha256"] = "0" * 64 if self.fault == "wrong-aot-source" else self.aot_pin
            if self.fault == "wrong-installed-source" or (
                    self.fault in {"metadata-unavailable-source-changed", "metadata-absent-source-changed"}
                    and len(self.hash_calls) == 2):
                files[0]["sha256"] = "0" * 64
            source = {"files": files}
        else:
            assert args[:10] == [f"--namespace={engine['namespace']}", "exec", engine["pod"],
                                "--container=engine", "--", "python3", "-I", "-S", "-B", "-c"]
            program = compile_cache._AOT_METADATA_PROGRAM if self.aot else compile_cache._METADATA_PROGRAM
            assert args[10] == program and len(args) == 13
            assert 0 < float(args[11]) <= 5 and args[12] == (AOT_ID if self.aot else CACHE_ID)
            assert kwargs["max_stdout_bytes"] == 4096
            self.metadata_reads.append(args)
            boot_ns = compile_cache._ns(STARTED)
            artifact = {"device": 2, "inode": 100, "uid": 2000, "gid": 0, "size": 543,
                        "mode": stat.S_IFREG | 0o640, "mtime_ns": boot_ns + 7000000000, "ctime_ns": boot_ns + 7000000000}
            directory = {**artifact, "inode": 99, "size": 4096, "mode": stat.S_IFDIR | 0o700}
            if self.aot == "load" and self.fault != "after-boot-aot-file":
                artifact.update(mtime_ns=boot_ns-2000, ctime_ns=boot_ns-1000)
            if self.fault == "stale":
                artifact["mtime_ns"] = boot_ns - 1000
            elif self.fault == "non-file":
                artifact["mode"] = stat.S_IFLNK | 0o777
            elif self.fault == "empty":
                artifact["size"] = 0
            source = {"reader_uid": 2000, "directory": directory, "artifact": artifact}
            if self.fault in {"metadata-unavailable", "metadata-unavailable-source-changed"}:
                source = {}
            elif self.fault in {"metadata-absent", "metadata-absent-source-changed", "metadata-absent-invalid-parent"}:
                source["artifact"] = None
                if self.fault == "metadata-absent-invalid-parent":
                    directory["mode"] = stat.S_IFREG | 0o600
        return {"returncode": 0, "stdout_bytes": canonical_json(source), "capture": {"complete": True}}


def _run(tmp_path, version, fault=None, *, aot=None, legacy_aot_line=False):
    env, target, path, startup = startup_inputs(tmp_path, version)
    if aot:
        image = "docker.io/vllm/vllm-openai@sha256:" + AOT_PINS[version][0]
        env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
        target["environment_ref"] = env.ref().model_dump(mode="json")
        target["engine"]["image_digest"] = image
        target["kubernetes"]["engine"].update(image=image, image_id=image)
    adapter = CompileAdapter(startup.adapter.line)
    world = CompileWorld(target, adapter, version == "0.31.0", fault, aot=aot)
    if legacy_aot_line:
        world.raw += (f"2026-10-26T00:00:08.534Z INFO 10-26 00:00:08 [decorators.py:312] "
                      f"Directly load AOT compilation from path {AOT_PATH}\n").encode()
    world.fault = None
    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.log_calls == [65536] and not world.hash_calls and not world.metadata_reads
    world.log_calls.clear()
    world.fault = fault
    path.write_bytes(canonical_json(target))
    executor = compile_cache.KubernetesNormalCompileCacheExecutor(
        {"normal_compile_cache_only": True, "target": str(path)},
        runner=world.runner, expected_target_sha256=sha256_hex(path.read_bytes()))
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-07"]
    # For AOT use the registered provider to supply compile_cache_source and
    # the existing mock adapter only for the unrelated startup source reader.
    draft = executor.run(check, env, {}, startup.adapter if aot else adapter, tmp_path / "work")
    text = draft.artifacts[0].path.read_text()
    assert all(raw not in text for raw in (CACHE_ID, CACHE_PATH, AOT_ID, AOT_PATH, PRIVATE, ROOT, "pid=96", ARTIFACT))
    return draft, json.loads(text), check, world


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_report_uses_exact_boot_window(tmp_path, version):
    env, target, _, world = startup_inputs(tmp_path, version)
    adapter = CompileAdapter(world.adapter.line)
    normal = CompileWorld(target, adapter, version == "0.31.0", None).raw
    profile = adapter.compile_cache_source(env)
    selected = b"2026-10-26T00:00:04.000000711Z"
    finished = b"2026-10-26T00:00:04.000000712Z"
    narrow = normal.replace(b"2026-10-26T00:00:04.517Z", selected).replace(b"2026-10-26T00:00:08.534Z", finished)
    window = {"container_started_at": selected.decode(), "ready_at": finished.decode()}
    _, report = compile_cache._compile_observation(narrow, window, profile)
    assert report["selected_at"] == "2026-10-26T00:00:04.000000711+00:00"
    assert report["completed_at"] == "2026-10-26T00:00:04.000000712+00:00"
    assert report["log_process_labels_matched"] is (version == "0.31.0")
    assert compile_cache._ns(window["ready_at"]) - compile_cache._ns(window["container_started_at"]) == 1
    for raw in (narrow.replace(selected, selected.replace(b"711Z", b"710Z")),
                narrow.replace(finished, finished.replace(b"712Z", b"713Z")),
                normal.replace(b"04.517Z", b"04.000000712Z").replace(b"08.534Z", b"04.000000711Z")):
        with pytest.raises(ValueError):
            compile_cache._compile_observation(raw, window, profile)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_different_compile_log_processes_are_not_joined(tmp_path, version):
    draft, artifact, _, world = _run(tmp_path, version, "different-process")
    assert draft.status == ExecStatus.ABORTED and draft.abort_reason == "SUP07_COMPILE_REPORT_UNVERIFIED"
    assert not world.metadata_reads and artifact["retained_compile_report"] is None
    assert artifact["observation"] is None


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("settings,accepted", [
    ({"max_duration_seconds": 30.5, "kubectl_binary": "example-kubectl"}, True),
    ({"max_duration_seconds": 0}, False),
    ({"kubectl_binary": " "}, False),
])
def test_prepares_saved_compile_cache_target_without_collecting(tmp_path, version, settings, accepted):
    from tools.mvp_versions import versions

    env, target, path, startup = startup_inputs(tmp_path, version)
    image = next(item["image"] for item in versions().values() if item["version"] == version)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["engine"]["image_digest"] = image
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=startup.runner)
    raw = canonical_json(target)
    path.write_bytes(raw)
    pin = sha256_hex(raw)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-07"]

    def no_collection(*args, **kwargs):
        pytest.fail("SUP-07 preparation must not collect from the target")

    executor = compile_cache.KubernetesNormalCompileCacheExecutor(
        {"normal_compile_cache_only": True, "target": str(path), **settings}, runner=no_collection)
    if accepted:
        executor.prepared_normal_compile_cache(check, env, {}, expected_target_sha256=pin)
        pin = sha256_hex(b"different-owned-target")
    with pytest.raises(ValueError, match="^SUP07_COMPILE_TARGET_OR_SOURCE_UNVERIFIED$"):
        executor.prepared_normal_compile_cache(check, env, {}, expected_target_sha256=pin)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_normal_compile_with_owned_boot_cache_metadata_is_partial(tmp_path, version):
    draft, artifact, check, world = _run(tmp_path, version)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert len(world.hash_calls) == 2 and len(world.log_calls) == len(world.metadata_reads) == 1 and not world.execs
    assert not draft.normal_control.attempted
    assert [row.attempted for row in draft.challenges] == [True, False, False]
    assert artifact["observation"]["compilation"]["reported_compile_elapsed_ms"] == 3270
    cache = artifact["observation"]["cache_file_metadata"]
    assert cache["file_permissions"] == 0o640 and cache["directory_permissions"] == 0o700
    assert cache["file_owner_uid"] == cache["directory_owner_uid"] == cache["reader_uid"] == 2000
    assert cache["file_path_sha256"] == sha256_hex(f"{CACHE_PATH}/{ARTIFACT}".encode())
    assert cache["file_bytes"] == 543
    assert artifact["gap"] is None and "file_read_and_consumption" in artifact["missing"]
    assert artifact["retained_compile_report"] is None
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_complete"] == {"collected": True, "value": False}
    assert observations["supply_boot_violations"] == {"collected": False, "value": None}
    assessed = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize("action", ["save", "load"])
def test_pinned_aot_uses_the_native_path_and_remains_partial(tmp_path, version, action):
    draft, artifact, check, world = _run(tmp_path, version, aot=action)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert len(world.hash_calls) == 2 and len(world.metadata_reads) == 1 and not world.execs
    observed = artifact["observation"]
    assert observed["compilation"]["action"] == action
    assert observed["compilation"]["reported_at"].endswith("534000712Z")
    assert observed["cache_file_metadata"]["file_path_sha256"] == sha256_hex(AOT_PATH.encode())
    assert observed["cache_file_metadata"]["file_inode"] == 100
    assert artifact["installed_source_sha256"][AOT_SOURCE] == world.aot_pin
    assert not any(row.attempted for row in draft.challenges)
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_native_consumption"]["collected"] is (action == "load")
    assert observations["supply_boot_complete"]["value"] is False
    assessed = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, {})
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_vllm_compile_contract_preserves_exact_aot_source_and_call_sites(tmp_path, version):
    env, _, _, _ = startup_inputs(tmp_path, version)
    digest, source, save, load = AOT_PINS[version]
    engine = env.engine.model_copy(update={"image_digest": "docker.io/vllm/vllm-openai@sha256:" + digest})
    provider = get_provider("vllm")
    aot = provider.evidence_contract("compile_cache_source", env.model_copy(update={"engine": engine}))["aot"]
    assert aot["file_sha256"] == {AOT_SOURCE: source}
    assert (aot["save_call_linenumber"], aot["load_call_linenumber"]) == (save, load)
    for update in ({"image_digest": "docker.io/vllm/vllm-openai@sha256:" + "0" * 64},
                   {"version": "0.99.0"}, {"source_commit": "0" * 40}, {"name": "another-engine"}):
        candidate = env.model_copy(update={"engine": engine.model_copy(update=update)})
        with pytest.raises(ValueError, match="SUP07_COMPILE_CACHE_SOURCE_UNQUALIFIED"):
            provider.evidence_contract("compile_cache_source", candidate)


def test_classic_normal_path_ignores_aot_log_without_optional_contract(tmp_path):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", legacy_aot_line=True)
    assert draft.status == ExecStatus.COMPLETED
    assert artifact["observation"]["compilation"]["scope"] == "native_ordinary_compile_monitor"
    assert not draft.observations["supply_boot_native_consumption"].collected
    assert len(world.metadata_reads) == 1 and world.metadata_reads[0][10] == compile_cache._METADATA_PROGRAM


def test_malformed_aot_contract_is_rejected_at_source_validation():
    class InvalidAdapter(CompileAdapter):
        def compile_cache_source(self, env):
            return {**super().compile_cache_source(env),
                    "aot": {"file_sha256": {AOT_SOURCE: "0" * 64},
                            "save_call_linenumber": 717, "load_call_linenumber": 312, "log_pattern": "("}}

    with pytest.raises(ValueError, match="SUP07_COMPILE_AOT_SOURCE_UNQUALIFIED"):
        compile_cache._compile_profile(InvalidAdapter(5354), None)


def test_other_provider_can_supply_classic_contract_without_aot(tmp_path, monkeypatch):
    env, target, _, world = startup_inputs(tmp_path, "0.31.0")
    classic = CompileAdapter(world.adapter.line).compile_cache_source(env)
    provider = EngineProvider("another-engine", lambda: world.adapter)
    monkeypatch.setattr(provider, "evidence_contract",
                        lambda name, _: classic if name == "compile_cache_source" else None)
    monkeypatch.setattr(compile_cache, "get_provider", lambda name: provider if name == provider.name else None)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"name": provider.name})})
    source = compile_cache._compile_profile(world.adapter, env)
    raw = CompileWorld(target, CompileAdapter(world.adapter.line), True, None).raw
    window = {"container_started_at": STARTED, "ready_at": "2026-10-26T00:00:12Z"}
    assert compile_cache._aot_observation(raw, window, source) is None
    assert compile_cache._compile_observation(raw, window, source)[1]["reported_compile_elapsed_ms"] == 3270


@pytest.mark.parametrize(("fault", "stat_called"), [
    ("wrong-aot-line", False), ("duplicate-aot", False), ("wrong-aot-source", False),
    ("after-boot-aot-file", True), ("changed-after-stat", True),
])
def test_aot_rejects_unbound_or_inconsistent_live_facts(tmp_path, fault, stat_called):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault, aot="load")
    assert draft.status == ExecStatus.ABORTED and artifact["observation"] is None
    assert bool(world.metadata_reads) is stat_called
    assert not draft.observations["supply_boot_native_consumption"].collected


@pytest.mark.parametrize(("fault", "stat_called"), [
    ("wrong-installed-source", False), ("prefix-changed", False), ("wrong-directory", False), ("overlay", False),
    ("compile-override", False),
    ("no-completed-compile", False), ("duplicate-compile", False), ("non-file", True), ("empty", True),
    ("stale", True), ("changed-after-stat", True), ("metadata-unavailable-source-changed", True),
])
def test_current_path_and_stable_metadata_are_required(tmp_path, fault, stat_called):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert bool(world.metadata_reads) is stat_called
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert (artifact["retained_compile_report"] is not None) is (fault in {"non-file", "empty", "stale"})


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_retains_verified_native_compile_report_when_file_metadata_is_unavailable(tmp_path, version):
    draft, artifact, _, world = _run(tmp_path, version, "metadata-unavailable")
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert draft.abort_reason == artifact["gap"] == "SUP07_COMPILE_FILE_METADATA_UNVERIFIED"
    assert len(world.hash_calls) == 2 and len(world.log_calls) == len(world.metadata_reads) == 1
    assert artifact["retained_compile_report"] == {
        "scope": "native_ordinary_compile_monitor",
        "selected_at": "2026-10-26T00:00:04.517000+00:00",
        "completed_at": "2026-10-26T00:00:08.534000+00:00",
        "log_process_labels_matched": version == "0.31.0",
        "reported_compile_elapsed_ms": 3270,
        "reported_directory_sha256": sha256_hex(CACHE_PATH.encode()),
    }
    assert set(artifact["installed_source_sha256"]) == set(FILES)
    assert artifact["observation"] is None and not draft.observations["supply_boot_sessions"].collected
    assert "ordinary_compile_cache_file_metadata" in artifact["missing"]


@pytest.mark.parametrize("fault", ["metadata-absent", "metadata-absent-source-changed", "metadata-absent-invalid-parent"])
def test_retains_only_same_owner_and_source_bound_cache_file_absence(tmp_path, fault):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert len(world.metadata_reads) == 1
    assert draft.status == ExecStatus.ABORTED and draft.abort_reason == "SUP07_COMPILE_FILE_METADATA_UNVERIFIED"
    assert artifact["observation"] is None and not draft.observations["supply_boot_sessions"].collected
    absence = (artifact["retained_compile_report"] or {}).get("cache_file_absence")
    if fault == "metadata-absent":
        assert absence["artifact_absent_at_capture"] is True
        assert absence["metadata_program_sha256"] == compile_cache._METADATA_PROGRAM_SHA256
        assert len(absence["metadata_snapshot_sha256"]) == 64
    else:
        assert absence is None


@pytest.mark.parametrize("leaf_type", ["unreadable", "symbolic-link", "absent", "missing-parent"])
def test_native_reader_lstats_only_fixed_leaf(tmp_path, leaf_type):
    # Relocate the shell's fixture root in a test copy. The production child
    # receives only the checked compile ID and cannot choose a root.
    source = compile_cache._METADATA_SOURCE.replace("CACHE_ROOT = '/cache/vllm'", f"CACHE_ROOT = {str(tmp_path)!r}", 1)
    folder = tmp_path / "torch_compile_cache" / CACHE_ID / "rank_0_0" / "backbone"
    if leaf_type != "missing-parent":
        folder.mkdir(parents=True)
    leaf = folder / ARTIFACT
    if leaf_type == "unreadable":
        leaf.write_bytes(PRIVATE.encode())
        leaf.chmod(0)
    elif leaf_type == "symbolic-link":
        leaf.symlink_to(tmp_path / "unreadable-elsewhere")
    actual = capture_pipes([sys.executable, "-I", "-S", "-B", "-c", supervised_source(source), "3", CACHE_ID],
                           timeout=5, env_extra={}, max_stdout_bytes=4096, max_stderr_bytes=1024)
    assert actual["capture"]["complete"] and actual["returncode"] == 0 and not actual["stderr_bytes"]
    assert PRIVATE.encode() not in actual["stdout_bytes"] and str(tmp_path).encode() not in actual["stdout_bytes"]
    projected = json.loads(actual["stdout_bytes"])
    if leaf_type == "unreadable":
        assert projected["artifact"]["size"] == len(PRIVATE) and stat.S_IMODE(projected["artifact"]["mode"]) == 0
    elif leaf_type == "absent":
        assert projected["artifact"] is None and stat.S_ISDIR(projected["directory"]["mode"])
    else:
        assert projected == {}
