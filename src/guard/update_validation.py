"""Explicit CPU update acceptance harness; product CLI owns engine lifecycle.

Preparation and existing-run analysis never start an engine. Execution needs
--execute and uses only local prepared resources; this module never pulls an
image, edits a policy, or cleans resources outside the product's owned run.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import tomllib

from guard.application import merge_config, update_readiness
from guard.collectors.file import load_environment, load_policy
from guard.core.select import select
from guard.models import Mode, load_pack
from guard.report.html import render_html
from guard.update_acceptance import CHECKS, assess_update
from guard.validation_evidence import (inventory_tree, read_objects,
                                       verify_saved_bundle, verify_store_files)


SCHEMA = "guard-update-validation/1"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _new_json(path: Path, data: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _verification_failure(exc: Exception) -> str:
    # These exact ValueErrors originate in the bounded local validators, not
    # subprocess stderr. Parser/library errors retain their type only.
    return f"ValueError: {exc}" if type(exc) is ValueError else type(exc).__name__


def _plain_path(path: Path) -> Path:
    """Reject links before resolve; resolving first would hide a linked parent."""
    path = path.expanduser().absolute()
    for part in [path, *path.parents]:
        reparse = part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & 0x400
        if part.is_symlink() or reparse:
            raise ValueError("input/output paths must not contain symlinks or junctions")
    return path.resolve()


def reserve_output(output: Path, *, protected: list[Path]) -> Path:
    output = _plain_path(output)
    if output.exists():
        raise ValueError("--output must be a new directory; existing evidence is never overwritten")
    for item in protected:
        item = _plain_path(item)
        if output.is_relative_to(item) or item.is_relative_to(output):
            raise ValueError("--output must be outside source inputs, pack, models and existing run")
    output.mkdir(parents=True, exist_ok=False)
    return output


def _conditions(cfg: dict, side: str) -> dict:
    value = merge_config(cfg, cfg.get("sides", {}).get(side, {}))
    value.pop("sides", None)
    value.pop("store", None)
    launch = value.get("vllm", {})
    # ENV pins image/version independently; model bytes and CPU are checked
    # separately. Other launch/probe/security options remain meaningful.
    for key in ("image", "expected_version", "models_dir", "source_commit_observed",
                "source_commit_status", "expected_source_commit", "files_read_by_engine_sha256",
                "files_read_by_engine_count"):
        launch.pop(key, None)
    return value


def validate_inputs(pack, envs, policy, cfg: dict) -> dict:
    """Pure declared-input checks, never an observed engine-identity claim."""
    if cfg.get("update", {}).get("mode") != "managed":
        raise ValueError("the harness requires update.mode=managed")
    for side in ("baseline", "candidate"):
        settings = merge_config(cfg, cfg.get("sides", {}).get(side, {}))
        if settings.get("vllm", {}).get("launch_profile") != "cpu-bounded":
            raise ValueError("both sides require the explicit cpu-bounded launch profile")
    selected = {item.check_id for item in select(pack, envs, policy, mode=Mode.update).items if item.selected}
    if selected != set(CHECKS):
        raise ValueError("scope must select exactly the five minimum checks; run optional checks separately")
    digests = []
    for env in envs:
        if env.data_origin.value == "EXAMPLE":
            raise ValueError("real CPU ENV inputs are required; EXAMPLE identities cannot be executed")
        image = env.engine.image_digest or ""
        if env.engine.name != "vllm" or not env.engine.version or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image):
            raise ValueError("each ENV needs its actual engine version and repository@sha256 image")
        digests.append(image.rsplit("@", 1)[1])
        if env.runtime.gpu_count != 0 or env.runtime.cpu is None or env.runtime.cpu.system != "Linux":
            raise ValueError("both ENVs need an explicit Linux CPU profile and gpu_count=0")
        if (not env.model.id or not env.model.revision
                or not re.fullmatch(r"[0-9a-f]{64}", env.model.inventory_sha256 or "")):
            raise ValueError("actual model ID, revision and full inventory are required")
    if digests[0] == digests[1]:
        raise ValueError("two distinct image digests are required; aliases of one image do not qualify")
    if envs[0].model.model_dump() != envs[1].model.model_dump():
        raise ValueError("model identity and inventory must match")
    if envs[0].runtime.model_dump() != envs[1].runtime.model_dump():
        raise ValueError("declared CPU/runtime conditions must match; performance remains UNKNOWN")
    if _conditions(cfg, "baseline") != _conditions(cfg, "candidate"):
        raise ValueError("meaningful side execution conditions differ; profile diagnostics are not image-update acceptance")
    return {"comparison_kind": "image-comparison" if envs[0].engine.version == envs[1].engine.version else "version-upgrade",
            "declared_images": [env.engine.image_digest for env in envs],
            "declared_versions": [env.engine.version for env in envs],
            "limitations": ["Declared inputs are not observed identities or engine results.",
                            "CPU security/functional comparison only; performance remains UNKNOWN."]}


def _support_ready(readiness: dict) -> bool:
    rows = {(row.get("side"), row.get("check_id")): row for row in readiness.get("check_support", [])}
    return readiness.get("ready") is True and all(
        rows.get((side, check), {}).get("status") == "SUPPORTED"
        for side in ("baseline", "candidate") for check in CHECKS)


def _command(args: list[str], label: str, timeout: int) -> tuple[dict, str]:
    """Keep subprocess output in memory: daemon output can contain credentials."""
    started = time.monotonic()
    try:
        result = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
        output = result.stdout.decode("utf-8", errors="replace")
        record = {"command": label, "returncode": result.returncode, "timed_out": False}
    except subprocess.TimeoutExpired:
        # Do not attempt an unowned cleanup or report success after a timeout.
        output = ""
        record = {"command": label, "returncode": None, "timed_out": True,
                  "detail": "command timeout; inspect run-labelled resources and diagnostics; no automatic cleanup attempted"}
    except OSError as exc:
        output = ""
        record = {"command": label, "returncode": None, "timed_out": False,
                  "detail": f"command could not start ({type(exc).__name__})"}
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return record, output


def _report_path(source: Path) -> Path:
    files = sorted((source / "updates").glob("*/report.json"))
    if len(files) != 1:
        raise ValueError("the run must contain exactly one updates/*/report.json")
    return files[0]


def _assess_store(store: Path, report_path: Path, *, diagnostic: bool = False) -> dict:
    objects = read_objects(store)
    pack = verify_store_files(store, objects)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    return assess_update(report, objects, pack, diagnostic=diagnostic)


def _report_pair(report_path: Path) -> dict:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report_path.with_suffix(".html").read_text(encoding="utf-8") != render_html(report):
        raise ValueError("saved HTML differs from its JSON report")
    return report


def _preserved_graph(source: Path) -> dict:
    """Tie bundle, immutable restore and replay to one original object graph."""
    original = read_objects(source / "store")
    restored = read_objects(source / "restored")
    if original != restored:
        raise ValueError("bundle/restore objects do not match the original run")
    replay = read_objects(source / "replay-store")
    key = lambda obj: (obj["kind"], obj["id"], obj["revision"])
    replay_by_key = {key(obj): obj for obj in replay}
    if any(replay_by_key.get(key(obj)) != obj for obj in original):
        raise ValueError("replay removed or changed an original object")
    original_keys = {key(obj) for obj in original}
    if any(obj["kind"] not in {"RES", "RPT"} for obj in replay if key(obj) not in original_keys):
        raise ValueError("replay added execution inputs or evidence instead of rejudging stored evidence")
    report = _report_pair(_report_path(source))
    replay_report = _report_pair(source / "replay-report.json")
    original_plan = [ref for ref in report["inputs_manifest"] if ref["kind"] == "PLAN"]
    replay_plan = [ref for ref in replay_report["inputs_manifest"] if ref["kind"] == "PLAN"]
    if original_plan != replay_plan:
        raise ValueError("replay report uses a different original PLAN")
    def outcomes(data):
        return {row["check_id"]: {field: row.get(field) for field in (
            "selected", "single", "baseline", "candidate", "comparison", "functional_comparison")}
            for row in data["by_check"]}
    if outcomes(report) != outcomes(replay_report):
        raise ValueError("saved-pack replay changed verdicts or comparisons")
    return {"original_objects": len(original), "restored_objects": len(restored), "replay_objects": len(replay),
            "all_original_objects_preserved": True, "same_verdicts_and_comparisons": True}


def _target_proof(source: Path, report_path: Path) -> dict:
    """Compare preserved product launch metadata with the stored PLAN inputs."""
    report = json.loads(report_path.read_text(encoding="utf-8"))
    objects = read_objects(source / "store")
    lookup = {(obj["kind"], obj["id"], obj["revision"], obj["content_sha256"]): obj for obj in objects}
    def deref(ref):
        return lookup[(ref["kind"], ref["id"], ref["revision"], ref["sha256"])]
    plans = [ref for ref in report["inputs_manifest"] if ref["kind"] == "PLAN"]
    if len(plans) != 1:
        raise ValueError("report must identify exactly one source PLAN")
    plan = deref(plans[0])
    env_refs = plan["inputs"]["environments"]
    if len(env_refs) != 2:
        raise ValueError("update PLAN needs two ENV references")
    proofs = {}
    for side, ref in zip(("baseline", "candidate"), env_refs):
        env = deref(ref)
        path = report_path.parent / side / "target.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        actual = data.get("engine", {}).get("image_digest")
        expected = env["engine"]["image_digest"]
        image_ok = (isinstance(actual, str) and actual.rsplit("@", 1)[-1] == expected.rsplit("@", 1)[-1]
                    and data.get("images", {}).get(expected) == actual)
        if (data.get("lab") is not False or data.get("engine_kind") != env["engine"]["name"]
                or data.get("observed_version") != env["engine"]["version"] or not image_ok):
            raise ValueError("preserved target identity differs from its PLAN ENV")
        proofs[side] = {"path": str(path), "sha256": _sha(path), "image_digest": actual,
                        "observed_version": data["observed_version"], "environment": ref}
    return proofs


def analyze_run(source: Path, output: Path, *, diagnostic: bool = False) -> dict:
    """Read existing run files only; even Store initialization is avoided."""
    source = _plain_path(source)
    if not source.is_dir():
        raise ValueError("--analyze-existing must identify an existing run directory")
    output = reserve_output(output, protected=[source])
    before = inventory_tree(source)
    summary = {"schema": SCHEMA, "mode": "analysis", "accepted": False,
               "source": str(source), "problems": []}
    try:
        report = _report_path(source)
        summary["acceptance"] = _assess_store(source / "store", report, diagnostic=diagnostic)
        summary["accepted"] = summary["acceptance"]["accepted"]
        summary["target_identity"] = _target_proof(source, report)
        summary["references"] = {"report": {"path": str(report), "sha256": _sha(report)}}
        if (source / "evidence.zip").is_file() and (source / "restored").is_dir():
            verify_saved_bundle(source / "evidence.zip", source / "restored")
        else:
            summary["accepted"] = False
            summary["problems"].append("verified export/restore proof is absent")
        replay = source / "replay-report.json"
        if replay.is_file() and (source / "replay-store").is_dir():
            summary["replay_acceptance"] = _assess_store(source / "replay-store", replay, diagnostic=diagnostic)
            summary["accepted"] = summary["accepted"] and summary["replay_acceptance"]["accepted"]
            summary["preserved_graph"] = _preserved_graph(source)
        else:
            summary["accepted"] = False
            summary["problems"].append("saved-pack replay proof is absent")
    except Exception as exc:
        summary["accepted"] = False
        summary["problems"].append(f"read-only evidence verification failed ({_verification_failure(exc)})")
    summary["source_unchanged"] = inventory_tree(source) == before
    if not summary["source_unchanged"]:
        summary["accepted"] = False
        summary["problems"].append("source inventory changed during analysis")
    _new_json(output / "summary.json", summary)
    return summary


def execute_run(inputs: dict[str, Path], output: Path, readiness: dict, declared: dict,
                *, timeout: int) -> dict:
    """Delegate lifecycle and evidence operations to the existing product CLI."""
    summary = {"schema": SCHEMA, "mode": "execute", "accepted": False, "commands": [],
               "readiness": readiness, **declared, "problems": []}
    common = [arg for name in ("pack", "baseline", "candidate", "policy", "config")
              for arg in (f"--{name}", str(inputs[name]))]
    cli = [sys.executable, "-m", "guard.cli"]

    def invoke(label, args):
        command = [*cli, *args]
        record, stdout = _command(command, label, timeout)
        record["argv"] = command  # Paths/flags/Ref/time only; never configuration bodies or credentials.
        summary["commands"].append(record)
        return record["returncode"] == 0 and not record.get("timed_out", False), stdout

    ok, prepare_output = invoke("prepare-update", ["prepare-update", *common])
    try:
        latest = json.loads(prepare_output)
        support = _support_ready(latest)
        summary["readiness"] = latest
    except (ValueError, TypeError):
        support = False
    if not ok or not support:
        summary["problems"].append("product preparation or per-check support is insufficient; no update started")
        summary["stage"] = "preparation-insufficient"
        return summary
    store = output / "store"
    summary["as_of"] = datetime.now(timezone.utc).isoformat()
    updated, _ = invoke("update", ["update", *common, "--store", str(store), "--output", str(output / "updates"),
                                 "--as-of", summary["as_of"]])
    summary["stage"] = "executed"
    if not updated:
        summary["problems"].append("update command failed or timed out; preserve all generated evidence and inspect owned resources")
        if summary["commands"][-1]["timed_out"]:
            return summary
    try:
        report_path = _report_path(output)
        report = _report_pair(report_path)
        summary["acceptance"] = _assess_store(store, report_path)
        summary["target_identity"] = _target_proof(output, report_path)
        before = inventory_tree(store)
        verified, _ = invoke("verify-original", ["verify", "--store", str(store), "--config", str(inputs["config"])])
        exported, _ = invoke("export", ["evidence", "export", "--store", str(store), "--output", str(output / "evidence.zip")])
        restored = False
        if exported:
            restored, _ = invoke("restore", ["evidence", "restore", "--bundle", str(output / "evidence.zip"),
                                             "--destination", str(output / "restored")])
        replayed = False
        if restored:
            verify_saved_bundle(output / "evidence.zip", output / "restored")
            restored_again, _ = invoke("restore-replay", ["evidence", "restore", "--bundle", str(output / "evidence.zip"),
                "--destination", str(output / "replay-store")])
            if not restored_again:
                raise ValueError("replay restore failed")
            restored_objects = read_objects(output / "replay-store")
            verify_store_files(output / "replay-store", restored_objects)
            plan = next(ref for ref in report["inputs_manifest"] if ref["kind"] == "PLAN")
            plan_id = f"{plan['id']}@{plan['revision']}"
            # Deliberately absent: allow_snapshot must resolve the PLAN's saved
            # pack rather than a same-revision folder in the current checkout.
            absent_pack = output / "no-current-pack-use-saved-snapshot"
            judged, _ = invoke("judge-restored", ["judge", "--plan", plan_id, "--pack", str(absent_pack),
                "--store", str(output / "replay-store"), "--config", str(inputs["config"]), "--as-of", summary["as_of"]])
            reported, _ = invoke("report-restored", ["report", "--plan", plan_id, "--pack", str(absent_pack),
                "--store", str(output / "replay-store"), "--config", str(inputs["config"]), "--as-of", summary["as_of"],
                "--json", str(output / "replay-report.json"), "--html", str(output / "replay-report.html")])
            replayed = judged and reported
            if replayed:
                summary["replay_acceptance"] = _assess_store(output / "replay-store", output / "replay-report.json")
                replayed = summary["replay_acceptance"]["accepted"]
                summary["preserved_graph"] = _preserved_graph(output)
        summary["original_store_unchanged"] = inventory_tree(store) == before
        summary["accepted"] = all((updated, verified, exported, restored, replayed,
                                    summary["original_store_unchanged"], summary["acceptance"]["accepted"]))
        summary["references"] = {name: {"path": str(path), "sha256": _sha(path)} for name, path in {
            "report": report_path, "html": report_path.with_suffix(".html"),
            "bundle": output / "evidence.zip", "replay_report": output / "replay-report.json",
            "replay_html": output / "replay-report.html"}.items() if path.is_file()}
        if not summary["accepted"]:
            summary["problems"].append("one or more execution, evidence, replay or acceptance contracts were not met")
    except Exception as exc:
        summary["accepted"] = False
        summary["problems"].append(f"post-run evidence verification failed ({_verification_failure(exc)}); original files retained")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pack", "baseline", "candidate", "policy", "config"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new directory outside all source inputs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true", help="opt in to product CLI sequential local CPU engines")
    mode.add_argument("--analyze-existing", type=Path, help="read existing run only; never start an engine or subprocess")
    parser.add_argument("--diagnostic", action="store_true", help="classify profile differences without accepting them as image-update proof")
    parser.add_argument("--command-timeout", type=int, default=3600)
    args = parser.parse_args(argv)
    if not 1 <= args.command_timeout <= 7200:
        parser.error("--command-timeout must be in [1,7200]")
    try:
        if args.analyze_existing:
            if any(getattr(args, name) for name in ("pack", "baseline", "candidate", "policy", "config")):
                raise ValueError("existing-run analysis uses stored inputs; do not supply fresh input files")
            summary = analyze_run(args.analyze_existing, args.output, diagnostic=args.diagnostic)
            code = 0 if summary["accepted"] else 1
        else:
            if args.diagnostic:
                raise ValueError("--diagnostic is available only with --analyze-existing")
            if not all(getattr(args, name) for name in ("pack", "baseline", "candidate", "policy", "config")):
                raise ValueError("preparation/execution needs --pack --baseline --candidate --policy --config")
            inputs = {name: _plain_path(getattr(args, name)) for name in ("pack", "baseline", "candidate", "policy", "config")}
            pack = load_pack(inputs["pack"])
            envs = [load_environment(inputs[name]) for name in ("baseline", "candidate")]
            policy = load_policy(inputs["policy"], pack)
            cfg = tomllib.loads(inputs["config"].read_text(encoding="utf-8"))
            declared = validate_inputs(pack, envs, policy, cfg)
            protected = [inputs["pack"], *(inputs[name].parent for name in ("baseline", "candidate", "policy", "config"))]
            for side in ("baseline", "candidate"):
                launch = merge_config(cfg, cfg.get("sides", {}).get(side, {})).get("vllm", {})
                if launch.get("models_dir"):
                    protected.append(Path(launch["models_dir"]))
            output = reserve_output(args.output, protected=protected)
            input_hashes = {name: {"path": str(path), "sha256": _sha(path)} for name, path in inputs.items() if name != "pack"}
            pack_files = inventory_tree(inputs["pack"])
            _new_json(output / "inputs.json", {"schema": SCHEMA, "inputs": input_hashes,
                                               "pack": pack.ref().model_dump(mode="json"), "pack_files": pack_files})
            readiness = update_readiness(pack, envs, policy, cfg)
            ready = _support_ready(readiness)
            if args.execute and ready:
                summary = execute_run(inputs, output, readiness, declared, timeout=args.command_timeout)
                code = 0 if summary["accepted"] else (2 if summary.get("stage") == "preparation-insufficient" else 1)
            else:
                summary = {"schema": SCHEMA, "mode": "preparation", "stage": "ready" if ready else "preparation-insufficient",
                           "accepted": False, "ready": ready, "readiness": readiness, **declared,
                           "limitations": [*declared["limitations"], "No engine was started and no store was created."]}
                code = 0 if ready else 2
            unchanged = all(_sha(Path(item["path"])) == item["sha256"] for item in input_hashes.values())
            unchanged = unchanged and inventory_tree(inputs["pack"]) == pack_files
            if not unchanged:
                _new_json(output / "preservation-failure.json", {"inputs_unchanged": False})
                summary["accepted"] = False
                code = 1
            summary["source_inputs_unchanged"] = unchanged
            _new_json(output / "commands.json", {"schema": SCHEMA, "commands": summary.get("commands", [])})
            _new_json(output / "summary.json", summary)
        print(json.dumps({"status": "ready" if code == 0 and summary["mode"] == "preparation" else
                          "accepted" if code == 0 else "preparation-insufficient" if code == 2 else "not-accepted",
                          "accepted": summary["accepted"] if code == 0 else False,
                          "summary": str(args.output / "summary.json")}, ensure_ascii=False))
        return code
    except Exception as exc:
        # Do not echo raw config, environment, daemon output or exception text.
        detail = str(exc) if isinstance(exc, ValueError) and type(exc) is ValueError else type(exc).__name__
        print(json.dumps({"status": "error", "accepted": False, "detail": detail}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
