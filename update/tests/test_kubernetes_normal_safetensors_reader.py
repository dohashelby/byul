"""SUP-12 requires observed normal iterator, strategy and loader-return reports."""

import json

import pytest

from guard.adapters.vllm_startup_load import safetensors_reader_profile
from guard.core.run import _normalize
from guard.executors import kubernetes_normal_safetensors_reader as safetensors
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_normal_startup_load import (
    ARGS, READY, ROOT, STARTED, StartupWorld, _inputs as startup_inputs,
)
from tools.mvp_versions import versions

VALUES = {"supply_allowed_readers": ["safetensors"]}
FILES = ("model_executor/model_loader/weight_utils.py", "model_executor/model_loader/default_loader.py",
         "logger.py", "logging_utils/formatter.py")
PRIVATE = "EXAMPLE unretained console data"


class ReaderWorld(StartupWorld):
    def __init__(self, target, env, adapter, modern, fault):
        super().__init__(target, adapter, modern, fault)
        source = safetensors_reader_profile(env)
        self.source_hashes = {**adapter.startup_load_source(env)["file_sha256"], **source["file_sha256"]}
        proc = "(EngineCore pid=96) " if modern else ""
        fs = source["filesystem_call_linenumber"]
        strategy = source["default_strategy_call_linenumber"]
        returned = source["loader_completion_call_linenumber"]
        completion_proc = "(EngineCore pid=98) " if fault == "other-process" else proc
        checkpoint_size = "0.00" if fault == "empty-checkpoint" else "0.47"
        filesystem = "unknown" if fault in {"filesystem-unreported", "strategy-unreported-mismatch"} else "EXT4"
        strategy_filesystem = {"strategy-filesystem-mismatch": "XFS",
                               "strategy-unreported-mismatch": "EXT4"}.get(fault, filesystem)
        available_ram = "50.11"
        reason = ("(NFS/Lustre). If you want to force prefetching, "
                  "start vLLM with --safetensors-load-strategy=prefetch.")
        if fault in {"low-ram", "low-ram-inconsistent"}:
            strategy = 1093 if modern else 925  # Exact installed logger call site.
            available_ram = "0.40"
            strategy_ram = "0.41" if fault == "low-ram-inconsistent" else available_ram
            reason = ("(NFS/Lustre) and the checkpoint size "
                      f"({checkpoint_size} GiB) exceeds 90% of available RAM ({strategy_ram} GiB).")
        rows = [
            f"2026-10-26T00:00:04.517Z {proc}INFO 10-26 00:00:04 [weight_utils.py:{fs}] "
            f"Filesystem type for checkpoints: {filesystem}. Checkpoint size: {checkpoint_size} GiB. Available RAM: {available_ram} GiB.",
            f"2026-10-26T00:00:04.616Z {proc}INFO 10-26 00:00:04 [weight_utils.py:{strategy}] "
            f"Auto-prefetch is disabled because the filesystem ({strategy_filesystem}) is not a recognized network FS {reason}",
            f"2026-10-26T00:00:06.233Z {completion_proc}INFO 10-26 00:00:06 [default_loader.py:{returned}] "
            "Loading weights took 0.29 seconds",
        ]
        if fault == "exact-window-boundaries":
            rows[0] = rows[0].replace("2026-10-26T00:00:04.517Z", STARTED)
            rows[2] = rows[2].replace("2026-10-26T00:00:06.233Z", READY)
        elif fault == "return-after-ready-one-ns":
            rows[2] = rows[2].replace("2026-10-26T00:00:06.233Z", READY.removesuffix("Z") + ".000000001Z")
        elif fault == "reader-reversed-one-ns":
            rows[0] = rows[0].replace("00:00:04.517Z", "00:00:04.517000002Z")
            rows[1] = rows[1].replace("00:00:04.616Z", "00:00:04.517000001Z")
        elif fault == "alternate-after-ready-one-ns":
            later = READY.removesuffix("Z") + ".000000001Z"
            rows.append(f"{later} Loading pt checkpoint shards: 100%")
        if fault == "wrong-order":
            rows[1], rows[2] = rows[2], rows[1]
        if fault == "duplicate":
            rows.append(rows[-1])
        if fault == "no-branch":
            rows.pop(1)
        if fault == "other-reader":
            rows.insert(2, "2026-10-26T00:00:04.988Z Loading pt checkpoint shards: 100%")
        rows.insert(1 if fault == "exact-window-boundaries" else 0,
                    f"2026-10-26T00:00:03.324Z INFO {PRIVATE}\rprogress ended")
        self.raw = ("\n".join(rows) + "\n").encode()

    def pod_readback(self, role, document, after):
        super().pod_readback(role, document, after)
        if role == "engine" and self.fault == "override":
            document["spec"]["containers"][0]["args"] = [*ARGS, "--safetensors_load_strategy=lazy"]

    def runner(self, argv, **kwargs):
        if "exec" not in argv:
            answer = super().runner(argv, **kwargs)
            if "logs" in argv and self.fault == "prefix-changed":
                answer["stdout_bytes"] = answer["stdout_bytes"].replace(b"0.29 seconds", b"0.30 seconds")
            return answer
        engine = self.target["kubernetes"]["engine"]
        assert argv[:2] == ["kubectl", "--context=fixture-context"]
        assert argv[3:13] == [f"--namespace={engine['namespace']}", "exec", "-i", engine["pod"],
                              "--container=engine", "--", "python3", "-I", "-S", "-c"]
        request = json.loads(kwargs["input_bytes"])
        assert request["roots"] == [ROOT] and {row["relative"] for row in request["files"]} == set(FILES)
        self.hash_calls.append(request)
        files = [{**row, "sha256": self.source_hashes[row["relative"]], "state": "hashed"} for row in request["files"]]
        if self.fault == "wrong-source" or self.fault == "source-changed" and len(self.hash_calls) == 2:
            files[0]["sha256"] = "0" * 64
        return {"returncode": 0, "stdout_bytes": canonical_json({"files": files}), "capture": {"complete": True}}


def _run(tmp_path, version, fault=None):
    env, target, path, initial = startup_inputs(tmp_path, version)
    image = next(item["image"] for item in versions().values() if item["version"] == version)
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": image})}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target["engine"]["image_digest"] = image
    target["kubernetes"]["engine"].update(image=image, image_id=image)
    adapter = initial.adapter
    world = ReaderWorld(target, env, adapter, version == "0.31.0", fault)
    world.fault = None
    target["startup_log_prefix"] = capture_startup_log_prefix(target, env, runner=world.runner)
    assert world.log_calls == [65536] and not world.hash_calls
    world.log_calls.clear()
    world.fault = fault
    if fault == "override":
        target["comparison_config"]["launch"]["argv"] = [*ARGS, "--safetensors_load_strategy=lazy"]
    path.write_bytes(canonical_json(target))
    executor = safetensors.KubernetesNormalSafetensorsReaderExecutor(
        {"normal_safetensors_reader_only": True, "target": str(path)},
        expected_target_sha256=sha256_hex(path.read_bytes()), runner=world.runner)
    check = load_pack(UPDATE / "packs/PACK-BASELINE").checks["SUP-12"]
    draft = executor.run(check, env, VALUES, adapter, tmp_path / "work")
    text = draft.artifacts[0].path.read_text()
    assert all(value not in text for value in (PRIVATE, "pid=96", "pid=98", ROOT, "/models/fixture", "0.29 seconds"))
    return draft, json.loads(text), check, world


@pytest.mark.parametrize(('version', 'damage'), [
    ('0.30.0', None), ('0.31.0', None), ('0.30.0', 'reader-option'),
    ('0.31.0', 'startup-option'), ('0.31.0', 'argv'), ('0.30.0', 'source'),
    ('0.30.0', 'duration'), ('0.31.0', 'binary'), ('0.31.0', 'missing-prefix'),
    ('0.30.0', 'caller-pin'), ('0.31.0', 'executor-pin'),
])
def test_sup12_prepares_the_saved_default_reader_without_collecting(tmp_path, monkeypatch, version, damage):
    from tests.test_kubernetes_normal_startup_load import READY, STARTED

    env, target, path, world = startup_inputs(tmp_path, version)
    image = next(item['image'] for item in versions().values() if item['version'] == version)
    engine = {'image_digest': image}
    if damage == 'source':
        engine['source_commit'] = '0' * 40
    env = env.model_copy(update={'engine': env.engine.model_copy(update=engine)}).seal()
    target['environment_ref'] = env.ref().model_dump(mode='json')
    target['engine']['image_digest'] = image
    target['kubernetes']['engine'].update(image=image, image_id=image)
    if damage != 'missing-prefix':
        target['startup_log_prefix'] = {'byte_count': len(world.raw), 'sha256': sha256_hex(world.raw),
                                        'container_started_at': STARTED, 'ready_at': READY}
    launch = target['comparison_config']['launch']
    if damage == 'reader-option':
        launch['argv'] = [*launch['argv'], '--safetensors_load_strategy=lazy']
    elif damage == 'startup-option':
        launch['argv'] = [*launch['argv'], '--logging_config=/example']
    elif damage == 'argv':
        launch['argv'] = None
    path.write_bytes(canonical_json(target))
    digest = sha256_hex(path.read_bytes())
    config = {'normal_safetensors_reader_only': True, 'target': str(path)}
    if damage == 'duration':
        config['max_duration_seconds'] = 301
    elif damage == 'binary':
        config['kubectl_binary'] = ' '

    def forbidden(*args, **kwargs):
        pytest.fail('SUP-12 declaration validation must not verify live owners or read logs or installed code')

    for method in ('_verify', 'engine_logs', 'engine_supply_hashes'):
        monkeypatch.setattr(safetensors.KubernetesHTTP, method, forbidden)
    executor = safetensors.KubernetesNormalSafetensorsReaderExecutor(config, runner=forbidden,
        expected_target_sha256='e' * 64 if damage == 'executor-pin' else None)
    check = load_pack(UPDATE / 'packs/PACK-BASELINE').checks['SUP-12']
    caller_pin = 'f' * 64 if damage == 'caller-pin' else digest
    if damage is None:
        executor.prepared_normal_reader(check, env, VALUES, expected_target_sha256=caller_pin)
        original = path.with_name("saved-target.json")
        path.rename(original)
        path.symlink_to(original.name)
        runtime = safetensors.KubernetesNormalSafetensorsReaderExecutor(
            config, runner=forbidden, expected_target_sha256=digest)
        adapter = safetensors.get_provider(env.engine.name).adapter()
        with pytest.raises(ValueError, match='^SUP12_READER_TARGET_OR_SOURCE_UNVERIFIED$'):
            runtime.run(check, env, VALUES, adapter, tmp_path / "work")
    else:
        with pytest.raises(ValueError, match='^SUP12_READER_TARGET_OR_SOURCE_UNVERIFIED$'):
            executor.prepared_normal_reader(check, env, VALUES, expected_target_sha256=caller_pin)


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
@pytest.mark.parametrize(("fixture", "filesystem", "branch"), [
    (None, "EXT4", "automatic_local_no_prefetch"),
    ("low-ram", "EXT4", "automatic_local_no_prefetch"),
    ("filesystem-unreported", None, "automatic_no_prefetch_filesystem_unreported"),
])
def test_sup12_reports_scoped_actual_default_reader_branch_without_a_full_verdict(
        tmp_path, version, fixture, filesystem, branch):
    draft, artifact, check, world = _run(tmp_path, version, fixture)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["gap"] is None and len(world.hash_calls) == 2 and world.log_calls == [len(world.raw)]
    assert not world.execs and not draft.normal_control.attempted
    assert [step.attempted for step in draft.challenges] == [True, False, False]
    reader = artifact["observation"]["reader"]
    assert reader["observed_reader_family"] == "safetensors"
    assert reader["load_strategy_branch"] == branch
    assert reader["source_indicated_iterator_call"] == "safe_open"
    assert reader["direct_safe_open_call_observed"] is False
    assert reader["reported_checkpoint_size_centi_gib"] == 47 and reader["filesystem_type"] == filesystem
    assert reader["same_process_label_observed"] is (version == "0.31.0")
    assert "direct_safe_open_call_and_file_identity" in artifact["missing"]
    assert ("checkpoint_filesystem_type" in artifact["missing"]) is (filesystem is None)
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_complete"] == {"collected": True, "value": False}
    assert observations["supply_boot_violations"] == {"collected": False, "value": None}
    assessed = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, VALUES)
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize(("fault", "complete"), [
    ("exact-window-boundaries", True),
    ("return-after-ready-one-ns", False),
    ("reader-reversed-one-ns", False),
    ("alternate-after-ready-one-ns", True),
])
def test_sup12_uses_nanoseconds_for_closed_boot_window_and_order(tmp_path, fault, complete):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.status == (ExecStatus.COMPLETED if complete else ExecStatus.ABORTED)
    assert artifact["gap"] == (None if complete else "SUP12_READER_BOOT_REPORT_UNVERIFIED")
    assert (artifact["observation"] is not None) is complete
    assert draft.observations["supply_boot_sessions"].collected is complete
    assert draft.observations["supply_boot_complete"].value is False
    assert len(world.log_calls) == 1 and len(world.hash_calls) == (2 if complete else 1)


@pytest.mark.parametrize("fault", ["prefix-changed", "wrong-source", "source-changed", "override", "no-branch",
                                   "other-reader", "other-process", "wrong-order", "duplicate", "empty-checkpoint",
                                   "strategy-filesystem-mismatch", "strategy-unreported-mismatch", "owner-changed",
                                   "low-ram-inconsistent"])
def test_sup12_requires_exact_source_scope_and_ordered_unambiguous_report(tmp_path, fault):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert not draft.observations["supply_boot_sessions"].collected
    assert bool(world.log_calls) is (fault not in {"wrong-source", "override"})
    if fault == "override":
        assert not world.hash_calls
