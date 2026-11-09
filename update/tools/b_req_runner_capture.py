"""선정 REQ native 연결의 입력 고정과 기존 run/judge/report/export/restore driver.

기본은 읽기 전용 준비다. --collect는 검토된 운영 패킷에서 S2가 실행한다. AWS/기동 기능은 없다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tomllib

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors.req_runner_capture import PREPARER_SHA, SOURCE_COMMIT, SOURCES
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.store import Store
from guard.store.packs import get_pack
from guard.store.bundle import export_bundle, restore_bundle, verify_complete

IDS = {"REQ-08", "REQ-09", "REQ-10"}


def _write(path, value):
    with path.open("xb") as stream:
        stream.write(canonical_json(value))


def _refs(store):
    return sorted([obj.ref().model_dump(mode="json") for kind in ("ENV", "POL", "PLAN", "EVD", "RES", "RPT")
                   for obj in store.iter_kind(kind)], key=lambda row: (row["kind"], row["id"], row["revision"]))


def run(source, plan_id, config, output, *, collect=False):
    source, config, output = Path(source).absolute(), Path(config).absolute(), Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("B_REQ_NEW_OUTPUT_REQUIRED")
    source_store = Store.read_only(source)
    plan = source_store.latest(plan_id)
    selected = {item.check_id for item in plan.items if item.selected}
    if not selected or selected - IDS or plan.mode.value != "audit" or list(source_store.iter_kind("EVD")):
        raise ValueError("B_REQ_FRESH_SELECTED_AUDIT_PLAN_REQUIRED")
    pack = get_pack(source_store, plan.inputs.pack)
    raw = config.read_bytes()
    cfg = tomllib.loads(raw.decode("utf-8")) if config.suffix == ".toml" else strict_json(raw, limit=2*1024**2)
    executors = cfg.get("executors", {})
    if cfg.get("adapter") != "vllm" or any(executors.get(pack.checks[cid].executor) != "kubernetes" for cid in selected):
        raise ValueError("B_REQ_EXISTING_KUBERNETES_EXECUTORS_REQUIRED")
    for cid in selected:
        settings = cfg.get(pack.checks[cid].executor, {})
        if "runner_capture" not in settings or "runner_input" in settings:
            raise ValueError("B_REQ_NATIVE_CAPTURE_CONFIG_REQUIRED")
    source_db = source / "store.sqlite"
    before = sha256_hex(source_db.read_bytes())
    prepared = {"schema": "guard-b-req-native-driver/1", "selected": sorted(selected),
        "plan": plan.ref().model_dump(mode="json"), "pack": pack.ref().model_dump(mode="json"),
        "config": {"path": str(config), "file_sha256": sha256_hex(raw)}, "source_store_sha256": before,
        "vllm_source_commit": SOURCE_COMMIT, "vllm_source_files": SOURCES, "preparer_source_sha256": PREPARER_SHA,
        "collect_invoked": collect, "native_request_mapping_verified": False, "gpu_ready": False,
        "required_runtime": ["owned Ready LAB Pod, fixed 0.31 image/source and readonly callback assets before import",
            "literal VLLM_DISABLE_REQUEST_ID_RANDOMIZATION=1 and VLLM_USE_V2_MODEL_RUNNER=0",
            "single prompt/n=1; unchanged request/body/headers; REQ08 chat response ID unchanged, REQ09/10 completion ID plus exactly -0",
            "original deadlines/budgets/normal controls, S2 fresh resource/cost and cleanup receipts"],
        "limitations": ["Preparation does not query a cluster or establish current runtime readiness.",
            "Rejection without native response ID remains unmapped; no suffix enumeration or candidate matching.",
            "EXAMPLE policy/CPU transport, live component observations and final product scope remain separate."]}
    output.mkdir(parents=True, exist_ok=False)
    _write(output / "inputs.json", prepared)
    if not collect:
        return prepared
    export_bundle(source, output / "source-inputs.zip")
    restore_bundle(output / "source-inputs.zip", output / "store")
    store = Store(output / "store")
    restored_plan = store.resolve(plan.ref())
    evidence = run_plan(store, pack, restored_plan, RunConfig(executors=executors, adapter="vllm", raw=cfg))
    as_of = datetime.now(timezone.utc)
    results = judge_plan(store, pack, restored_plan, as_of=as_of)
    report = build_report(store, pack, restored_plan, as_of=as_of)
    store.put(report)
    verify_complete(store)
    refs = _refs(store)
    export_bundle(store.root, output / "evidence.zip")
    restore_bundle(output / "evidence.zip", output / "restored")
    restored = Store(output / "restored")
    verify_complete(restored)
    saved_pack = get_pack(restored, restored_plan.inputs.pack)
    replay = judge_plan(restored, saved_pack, restored_plan, as_of=as_of)
    if ([item.ref() for item in replay] != [item.ref() for item in results] or _refs(restored) != refs
            or sha256_hex(source_db.read_bytes()) != before or config.read_bytes() != raw):
        raise ValueError("B_REQ_EXACT_RESTORE_OR_INPUT_MISMATCH")
    summary = {**prepared, "refs": refs, "exact_refs_restored": True, "saved_pack_rejudge_equal": True, "original_inputs_unchanged": True,
        "evidence": [{"check_id": item.check_id, "ref": item.ref().model_dump(mode="json"),
                      "status": item.execution.status.value, "data_origin": item.data_origin.value} for item in evidence],
        "results": [{"check_id": item.check_id, "ref": item.ref().model_dump(mode="json"),
                     "security": item.verdicts.security.value, "functional": item.verdicts.functional.value} for item in results],
        "report": report.ref().model_dump(mode="json"), "evidence_bundle_sha256": sha256_hex((output / "evidence.zip").read_bytes()),
        "note": "Driver never starts/stops an engine or operates AWS. S2 owns live execution and resource receipts."}
    _write(output / "receipt.json", summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(args.store, args.plan, args.config, args.output, collect=args.collect)
    except Exception as error:
        print(json.dumps({"error": {"type": type(error).__name__, "code": "B_REQ_NATIVE_DRIVER_FAILED"},
                          "gpu_ready": False}), file=sys.stderr)
        return 2
    print(json.dumps({"selected": result["selected"], "collect_invoked": result["collect_invoked"],
                      "exact_refs_restored": result.get("exact_refs_restored", False), "gpu_ready": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
