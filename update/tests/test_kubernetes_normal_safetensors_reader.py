"""SUP-12 requires observed normal iterator, strategy and loader-return reports."""

import json

import pytest

from guard.core.run import _normalize
from guard.executors import kubernetes_normal_safetensors_reader as safetensors
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, load_pack, sha256_hex
from tests.builders import evidence
from tests.test_gpu_a_run_inputs import UPDATE
from tests.test_kubernetes_normal_startup_load import Adapter, ARGS, ROOT, StartupWorld, _inputs as startup_inputs

VALUES = {"supply_allowed_readers": ["safetensors"]}
FILES = ("model_executor/model_loader/weight_utils.py", "model_executor/model_loader/default_loader.py",
         "logger.py", "logging_utils/formatter.py")
PRIVATE = "EXAMPLE unretained console data"


class ReaderAdapter(Adapter):
    def safetensors_reader_source(self, env):
        filesystem, strategy, complete = (1063, 1086, 484) if self.line == 5354 else (895, 918, 430)
        return {"file_sha256": {name: sha256_hex(name.encode()) for name in FILES[:2]},
                "filesystem_call_linenumber": filesystem, "default_strategy_call_linenumber": strategy,
                "loader_completion_call_linenumber": complete}


class ReaderWorld(StartupWorld):
    def __init__(self, target, adapter, modern, fault):
        super().__init__(target, adapter, modern, fault)
        source = adapter.safetensors_reader_source(None)
        proc = "(EngineCore pid=96) " if modern else ""
        fs = source["filesystem_call_linenumber"]
        strategy = source["default_strategy_call_linenumber"]
        returned = source["loader_completion_call_linenumber"]
        completion_proc = "(EngineCore pid=98) " if fault == "other-process" else proc
        checkpoint_size = "0.00" if fault == "empty-checkpoint" else "0.47"
        fs_code = "XFS" if fault == "strategy-filesystem-mismatch" else "EXT4"
        rows = [
            f"2026-10-26T00:00:04.517Z {proc}INFO 10-26 00:00:04 [weight_utils.py:{fs}] "
            f"Filesystem type for checkpoints: EXT4. Checkpoint size: {checkpoint_size} GiB. Available RAM: 50.11 GiB.",
            f"2026-10-26T00:00:04.616Z {proc}INFO 10-26 00:00:04 [weight_utils.py:{strategy}] "
            f"Auto-prefetch is disabled because the filesystem ({fs_code}) is not a recognized network FS "
            "(NFS/Lustre). If you want to force prefetching, start vLLM with --safetensors-load-strategy=prefetch.",
            f"2026-10-26T00:00:06.233Z {completion_proc}INFO 10-26 00:00:06 [default_loader.py:{returned}] "
            "Loading weights took 0.29 seconds",
        ]
        if fault == "wrong-order":
            rows[1], rows[2] = rows[2], rows[1]
        if fault == "duplicate":
            rows.append(rows[-1])
        if fault == "no-branch":
            rows.pop(1)
        if fault == "other-reader":
            rows.insert(2, "2026-10-26T00:00:04.988Z Loading pt checkpoint shards: 100%")
        rows.insert(0, f"2026-10-26T00:00:03.324Z INFO {PRIVATE}\rprogress ended")
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
        files = [{**row, "sha256": sha256_hex(row["relative"].encode()), "state": "hashed"} for row in request["files"]]
        if self.fault == "wrong-source" or self.fault == "source-changed" and len(self.hash_calls) == 2:
            files[0]["sha256"] = "0" * 64
        return {"returncode": 0, "stdout_bytes": canonical_json({"files": files}), "capture": {"complete": True}}


def _run(tmp_path, version, fault=None):
    env, target, path, initial = startup_inputs(tmp_path, version)
    adapter = ReaderAdapter(initial.adapter.line)
    world = ReaderWorld(target, adapter, version == "0.31.0", fault)
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


@pytest.mark.parametrize("version", ["0.30.0", "0.31.0"])
def test_sup12_reports_scoped_actual_default_reader_branch_without_a_full_verdict(tmp_path, version):
    draft, artifact, check, world = _run(tmp_path, version)
    assert draft.status == ExecStatus.COMPLETED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["gap"] is None and len(world.hash_calls) == 2 and world.log_calls == [len(world.raw)]
    assert not world.execs and not draft.normal_control.attempted
    assert [step.attempted for step in draft.challenges] == [True, False, False]
    reader = artifact["observation"]["reader"]
    assert reader["observed_reader_family"] == "safetensors"
    assert reader["load_strategy_branch"] == "automatic_local_no_prefetch"
    assert reader["source_indicated_iterator_call"] == "safe_open"
    assert reader["direct_safe_open_call_observed"] is False
    assert reader["reported_checkpoint_size_centi_gib"] == 47 and reader["filesystem_type"] == "EXT4"
    assert reader["same_process_label_observed"] is (version == "0.31.0")
    assert "direct_safe_open_call_and_file_identity" in artifact["missing"]
    observations = _normalize(check, draft)["observations"]
    assert observations["supply_boot_complete"] == {"collected": True, "value": False}
    assert observations["supply_boot_violations"] == {"collected": False, "value": None}
    assessed = RuleTableJudge().judge(check, evidence(check_id=check.id, observations=observations), check.judge.params, VALUES)
    assert assessed.verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("fault", ["prefix-changed", "wrong-source", "source-changed", "override", "no-branch",
                                   "other-reader", "other-process", "wrong-order", "duplicate", "empty-checkpoint",
                                   "strategy-filesystem-mismatch", "owner-changed"])
def test_sup12_requires_exact_source_scope_and_ordered_unambiguous_report(tmp_path, fault):
    draft, artifact, _, world = _run(tmp_path, "0.31.0", fault)
    assert draft.status == ExecStatus.ABORTED and draft.data_origin == DataOrigin.EXAMPLE
    assert artifact["observation"] is None and artifact["gap"] == draft.abort_reason
    assert not draft.observations["supply_boot_sessions"].collected
    assert bool(world.log_calls) is (fault not in {"wrong-source", "override"})
    if fault == "override":
        assert not world.hash_calls
