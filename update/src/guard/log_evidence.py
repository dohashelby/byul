"""Read-only replay of pinned OBS-03/09 artifacts, never a new engine run.

An accepted audit means the saved redacted capture, observations and fixed
rule-table verdicts agree. It does not mean security PASS, authenticate a host,
recover redacted source bytes or amend any original result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import stat
import sqlite3

from guard.core.judge import pass_gate
from guard.executors.log_analysis_contract import analyze_log_captures
from guard.executors.log_integrity_contract import PLANNED_CASES, analyze_integrity_capture
from guard.executors.log_integrity_parser import PROFILE, TRUSTED_FIELDS
from guard.judges.rule_table import RuleTableJudge
from guard.models import canonical_json, sha256_hex, weakest_origin
from guard.models.objects import object_from_dict
from guard.report.log_integrity import render_log_integrity, verify_log_display
from guard.validation_evidence import inventory_tree, read_objects, verify_store_files

SCHEMA = "guard-log-evidence-audit/1"
CHECKS = {"OBS-03": "log_analysis", "OBS-09": "log_integrity"}
MAX_JSON_BYTES = 16 * 1024**2


def _key(ref):
    return ref.kind, ref.id, ref.revision, ref.sha256


def _artifact(store, evd, name, *, json_value=True):
    matches = [item for item in evd.artifacts if item.name == name]
    if len(matches) != 1:
        raise ValueError("one pinned named log artifact is required")
    item = matches[0]
    if item.bytes > MAX_JSON_BYTES:
        raise ValueError("log replay artifact exceeds its read budget")
    data = (store / item.path).read_bytes()
    return json.loads(data) if json_value else data.decode("utf-8")


def _persisted_text(capture):
    text = capture.get("text")
    if not isinstance(text, str):
        raise ValueError("saved log capture text is unavailable")
    raw = text.encode("utf-8")
    # Raw pipe caps are enforced at collection, before decoding/redaction.
    # Replacement characters and redaction can expand retained text. The exact
    # persisted artifact has already passed MAX_JSON_BYTES, so do not confuse
    # the raw source limit with the saved decoded representation budget.
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("saved decoded capture exceeds the artifact read budget")
    if (capture.get("persisted_decoded_sha256") != hashlib.sha256(raw).hexdigest()
            or capture.get("persisted_decoded_bytes") != len(raw)):
        raise ValueError("persisted decoded capture hash/byte count differs")
    if "stderr_text" in capture:
        stderr = capture["stderr_text"].encode("utf-8")
        if (len(stderr) > MAX_JSON_BYTES or capture.get("persisted_stderr_sha256") != hashlib.sha256(stderr).hexdigest()
                or capture.get("persisted_stderr_bytes") != len(stderr)):
            raise ValueError("persisted stderr capture hash/byte count differs")


def _obs09(store, evd, sample, values):
    capture, trials = sample["capture"], sample["trials"]
    _persisted_text(capture)
    if (not isinstance(trials, list) or len(trials) > 12
            or len({trial["case"] for trial in trials}) != len(trials)):
        raise ValueError("invalid bounded native trial inventory")
    for trial in trials:
        if (trial["case"] not in PLANNED_CASES or not isinstance(trial["request_id"], str)
                or len(trial["request_id"].encode("utf-8")) > 512):
            raise ValueError("invalid native trial identity")
        response_match = (trial.get("normal_generation") == "pass"
                          and trial.get("http_outcome") == 200
                          and trial.get("response_id") == "cmpl-" + trial["request_id"])
        if trial.get("response_id_matches_body") is not response_match:
            raise ValueError("native response and exact body request ID differ")
    analysis = analyze_integrity_capture(capture, trials, sample.get("source_identity_before"),
                                         sample.get("source_identity_after"),
                                         initial_gaps=sample["initial_gaps"], max_events=sample["max_events"])
    collection = analysis["collection"]
    collection["capture_window"] = capture.get("window")
    collection["redaction"] = "configured authorization secrets and labelled credential values; source digests precede redaction"
    saved_collection = _artifact(store, evd, "log_integrity_events.json")["capture"]
    if saved_collection != collection:
        raise ValueError("saved native events differ from capture replay (redaction may limit reconstruction)")
    document = _artifact(store, evd, "log_integrity_display.html", json_value=False)
    displayed = verify_log_display(document, collection)
    if (hashlib.sha256(document.encode()).hexdigest() != sample.get("display_sha256")
            or document != render_log_integrity(collection) or not displayed):
        raise ValueError("saved reference HTML does not map exactly to native events")
    if (hashlib.sha256(json.dumps(collection, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            != sample.get("collection_sha256")):
        raise ValueError("native collection digest differs")
    gaps = analysis["gaps"]
    obs = {"log_collector_profile": PROFILE, "log_client_request_id_trust": "untrusted_correlation",
           "log_trusted_fields": list(TRUSTED_FIELDS), "log_identity_verified": analysis["stable"],
           "log_required_trusted_fields": [field for field in values["required_trusted_fields"] if field in TRUSTED_FIELDS],
           "log_collection_complete": not gaps, "log_boundaries_preserved": analysis["boundaries"],
           "log_trusted_fields_preserved": analysis["fields_preserved"], "log_display_verified": displayed,
           "log_trials_planned": list(PLANNED_CASES), "log_trials_reached": analysis["reached"],
           "log_security_violations": analysis["violations"], "log_coverage_gaps": gaps,
           "normal_controls": analysis["normal_inference"] and analysis["normal_logs"],
           "functional_probes_planned": ["before", "after"], "functional_probe_results": analysis["controls"]}
    return obs, not gaps


def _obs03(evd, sample, values):
    captures = sample["captures"]
    if not isinstance(captures, list) or len(captures) > 16:
        raise ValueError("invalid bounded source inventory")
    for capture in captures:
        _persisted_text(capture)
        if capture["window"] != sample["window"]:
            raise ValueError("source capture window differs from trial window")
        source = capture["source"]
        if (source.get("role", "engine") == "engine"
                and source.get("container") != sample.get("source_target")):
            raise ValueError("captured engine differs from the recorded trial target")
    correlation = sample["correlation"]
    if (not isinstance(correlation, dict) or set(correlation) != {"normal", "error"}
            or any(not isinstance(value, str) or not value for value in correlation.values())
            or correlation["normal"] == correlation["error"]):
        raise ValueError("exact normal/error trial correlation is required")
    for request in sample["requests"]:
        branch = "error" if request["case"] == "safe_error" else "normal"
        if request["request_id"] != correlation[branch]:
            raise ValueError("saved request differs from its capture trial identity")
    obs = analyze_log_captures(captures, values, sample["markers"], correlation,
                               initial_gaps=sample["initial_gaps"])
    if obs != sample.get("analysis"):
        raise ValueError("recorded log analysis differs from persisted capture replay (redaction may limit reconstruction)")
    obs.update({"functional_probes_planned": ["before", "after"],
                "functional_probe_results": sample["controls"]})
    return obs, obs["log_coverage_complete"]


def analyze_saved_logs(store: Path, report_id: str | None = None) -> dict:
    """Public read-only boundary: unreadable saved inputs are validation errors.

    Never copy OS messages (which can contain paths or supplied material) into
    the public failure reason. No repair or source write is attempted.
    """
    try:
        return _analyze_saved_logs(store, report_id)
    except (OSError, sqlite3.Error) as exc:
        raise ValueError("saved evidence input could not be read (" + type(exc).__name__ + ")") from exc


def _analyze_saved_logs(store: Path, report_id: str | None = None) -> dict:
    """Audit an exact RPT manifest. Ambiguous reports require id@revision.

    Source SQLite is immutable/read-only. No Store is constructed, no judgment
    is written, and no executable or payload path/URL is followed.
    """
    store = Path(store).absolute()
    for path in [store, *store.parents]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("saved source path must not contain symlinks or reparse points")
    before = inventory_tree(store)
    parsed = [object_from_dict(value) for value in read_objects(store)]
    if any(not value.hash_ok() for value in parsed):
        raise ValueError("stored object hash differs")
    index = {_key(value.ref()): value for value in parsed}
    if len(index) != len(parsed):
        raise ValueError("duplicate saved object identity")
    reports = [value for value in parsed if value.kind == "RPT"]
    if report_id:
        ident, sep, revision = report_id.rpartition("@")
        if sep:
            if not revision.isdigit() or int(revision) < 1:
                raise ValueError("report revision must be a positive integer")
            reports = [value for value in reports if value.id == ident and value.revision == int(revision)]
        else:
            reports = [value for value in reports if value.id == report_id]
    if len(reports) != 1:
        raise ValueError("select one exact saved report using --report RPT-ID@REVISION")
    report = reports[0]
    manifest_keys = [_key(ref) for ref in report.inputs_manifest]
    if len(set(manifest_keys)) != len(manifest_keys) or set(manifest_keys) != {_key(ref) for ref in report.refs}:
        raise ValueError("report manifest and refs differ or contain duplicates")
    plans = [index.get(_key(ref)) for ref in report.inputs_manifest if ref.kind == "PLAN"]
    if len(plans) != 1 or plans[0] is None:
        raise ValueError("report must pin one original PLAN")
    plan = plans[0]
    # The helper verifies all artifact bytes, but loads only the report's exact
    # original PLAN pack. Other plans/history in the store are not selected.
    pack = verify_store_files(store, [value.to_dict() for value in parsed
                                     if value.kind != "PLAN" or value.ref() == plan.ref()])

    def resolve(ref):
        if ref.kind == "PACK":
            if ref != pack.ref():
                raise ValueError("pinned original pack differs")
            return pack
        value = index.get(_key(ref))
        if value is None:
            raise ValueError("broken exact saved reference")
        return value

    if [ref for ref in report.inputs_manifest if ref.kind == "PACK"] != [pack.ref()]:
        raise ValueError("report must pin its one original pack")
    fixed = [resolve(ref) for ref in report.inputs_manifest if ref.kind != "PACK"]
    if plan.inputs.pack != pack.ref():
        raise ValueError("PLAN and saved pack differ")
    policy = resolve(plan.inputs.policy)
    if policy not in fixed or policy.pack != pack.ref():
        raise ValueError("report must pin the original policy and pack")
    for value in fixed:
        for ref in getattr(value, "refs", []):
            resolve(ref)
    environments = [resolve(ref) for ref in plan.inputs.environments]
    evidence = [value for value in fixed if value.kind == "EVD"]
    results = [value for value in fixed if value.kind == "RES"]
    required_refs = [pack.ref(), *plan.inputs.environments, policy.ref(), plan.ref(),
                     *(value.ref() for value in [*evidence, *results])]
    plan_refs = [pack.ref(), *plan.inputs.environments, policy.ref()]
    if plan.inputs.change:
        resolve(plan.inputs.change)
        required_refs.append(plan.inputs.change)
        plan_refs.append(plan.inputs.change)
    if set(manifest_keys) != {_key(ref) for ref in required_refs}:
        raise ValueError("report manifest differs from its original PLAN graph")
    if (len(plan.refs) != len(plan_refs) or {_key(ref) for ref in plan.refs} != {_key(ref) for ref in plan_refs}):
        raise ValueError("PLAN refs differ from its exact original inputs")
    if (report.policy_status != policy.status
            or report.pack_review_status.value != ("REVIEWED" if pack.reviewed else "DRAFT")):
        raise ValueError("report policy/pack review status differs from saved sources")
    origins = [value.data_origin for value in environments] + [policy.data_origin, plan.data_origin]
    origins += [value.data_origin for value in [*evidence, *results]]
    official = policy.status.value == "CONFIRMED" and pack.reviewed and all(value.value != "EXAMPLE" for value in origins)
    if report.official != official or report.data_origin != weakest_origin(origins):
        raise ValueError("report official/origin differs from its pinned original sources")
    for evd in evidence:
        if (evd.plan != plan.ref() or evd.environment not in plan.inputs.environments
                or len(evd.refs) != 2 or {_key(ref) for ref in evd.refs} != {_key(plan.ref()), _key(evd.environment)}):
            raise ValueError("saved evidence refs differ from exact PLAN/environment")
    for res in results:
        evd = resolve(res.evidence)
        if (evd not in evidence or res.check_id != evd.check_id or res.side != evd.side
                or len(res.refs) != 2 or {_key(ref) for ref in res.refs} != {_key(evd.ref()), _key(policy.ref())}):
            raise ValueError("saved result refs differ from exact evidence/policy")
        item = plan.item(evd.check_id)
        level = weakest_origin([evd.data_origin if evd.execution.status.value == "COMPLETED" else None,
                                *(condition.origin for condition in item.conditions)])
        origin = weakest_origin([evd.data_origin, policy.data_origin, plan.data_origin])
        if res.verification_level != level or res.data_origin != origin:
            raise ValueError("saved result origin/verification level differs from original evidence")
    problems, limitations, rows = [], [], []
    evds = [value for value in fixed if value.kind == "EVD" and value.check_id in CHECKS]
    if not evds:
        problems.append("the selected report contains no pinned automated OBS-03/OBS-09 capture")
    if len({(value.check_id, value.side) for value in evds}) != len(evds):
        raise ValueError("report pins multiple evidence revisions for one log check side")
    for evd in evds:
        row = {"check_id": evd.check_id, "side": evd.side.value, "evidence": evd.ref().model_dump(mode="json"),
               "data_origin": evd.data_origin.value, "replayed": False, "coverage_complete": False}
        rows.append(row)
        try:
            if (evd.plan != plan.ref() or evd.environment not in plan.inputs.environments
                    or not plan.item(evd.check_id).selected):
                raise ValueError("log evidence PLAN, environment or selection differs")
            expected_env = plan.inputs.environments[0 if evd.side.value in {"single", "baseline"} else 1]
            if evd.environment != expected_env or (plan.mode.value == "audit") != (evd.side.value == "single"):
                raise ValueError("log evidence side and PLAN environment differ")
            executor = evd.execution.executor
            if (evd.execution.operator is not None or executor is None or executor.name != CHECKS[evd.check_id]
                    or evd.execution.status.value != "COMPLETED"):
                raise ValueError("completed automated log evidence is required")
            results = [value for value in fixed if value.kind == "RES" and value.evidence == evd.ref()]
            if len(results) != 1:
                raise ValueError("report must pin one exact result for each log evidence")
            res = results[0]
            check = pack.checks[evd.check_id]
            if res.check_id != check.id or res.side != evd.side:
                raise ValueError("result differs from log evidence check/side")
            report_rows = [value for value in report.by_check if value.check_id == check.id and value.selected]
            side_row = getattr(report_rows[0], evd.side.value) if len(report_rows) == 1 else None
            if side_row is None or side_row.result != res.ref():
                raise ValueError("report row does not pin the saved log result")
            if side_row.verification_level != res.verification_level:
                raise ValueError("report verification level differs from pinned result")
            if any(getattr(side_row, axis) != getattr(res.verdicts, axis)
                   for axis in ("security", "functional", "performance")):
                raise ValueError("report verdicts differ from the pinned result")
            name = "log_samples.json" if check.id == "OBS-03" else "log_integrity_samples.json"
            envelope = _artifact(store, evd, name)
            sample = envelope.get("capture", {})
            expected_schema = "guard-log-analysis-capture/2" if check.id == "OBS-03" else "guard-log-integrity-capture/2"
            if sample.get("schema") != expected_schema:
                row["status"] = "LEGACY_UNVERIFIABLE"
                limitations.append(f"{check.id}/{evd.side.value}: legacy capture retained; new pipe/nofollow/correlation guarantees cannot be reconstructed")
                continue
            values = policy.values.get(check.id, {})
            if sample.get("policy_values") != values:
                raise ValueError("saved capture policy differs from pinned policy")
            obs, complete = (_obs03(evd, sample, values) if check.id == "OBS-03"
                             else _obs09(store, evd, sample, values))
            for key, value in obs.items():
                observation = evd.observations.get(key)
                if observation is None or not observation.collected or observation.value != value:
                    raise ValueError("saved observation differs from capture replay: " + key)
            plugin = RuleTableJudge()
            if (check.judge is None or check.judge.plugin != "rule_table" or res.judge.name != plugin.name
                    or res.judge.version != plugin.version
                    or res.judge.params_sha256 != sha256_hex(canonical_json(check.judge.params))):
                row["status"] = "JUDGE_VERSION_UNAVAILABLE"
                limitations.append(f"{check.id}/{evd.side.value}: exact historical judge version is unavailable; no substituted re-judgment")
                continue
            judged = plugin.judge(check, evd, check.judge.params, values)
            expected = {axis: value.value for axis, value in judged.verdicts.items()}
            if pass_gate(check, evd):
                expected = {axis: "UNKNOWN" if value == "PASS" else value for axis, value in expected.items()}
            if evd.comparability.cpu is not None:
                expected["performance"] = "UNKNOWN"
            if expected != res.verdicts.model_dump(mode="json"):
                raise ValueError("pinned result differs from fixed-rule replay")
            row.update(status="REPLAY_MATCH", replayed=True, coverage_complete=complete,
                       result=res.ref().model_dump(mode="json"), verdicts=res.verdicts.model_dump(mode="json"))
            if not complete:
                limitations.append(f"{check.id}/{evd.side.value}: capture remains incomplete; saved UNKNOWN/independent FAIL is preserved")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            row["status"] = "REPLAY_MISMATCH"
            # Only our fixed ValueErrors are shown; parser/payload exception
            # messages can contain input material and are not copied.
            message = str(exc) if type(exc) is ValueError and str(exc).startswith((
                "saved ", "persisted ", "source ", "captured ", "exact ", "completed ", "log ",
                "result ", "report ", "pinned ", "one ", "invalid ", "native ", "recorded ")) else type(exc).__name__
            problems.append(f"{evd.check_id}/{evd.side.value}: {message}")
    preserved = inventory_tree(store) == before
    if not preserved:
        raise ValueError("source inventory changed during read-only log analysis")
    return {"schema": SCHEMA, "accepted": bool(rows) and not problems and all(row["replayed"] for row in rows),
            "evidence_contract_valid": not problems, "checks": rows, "problems": problems,
            "limitations": limitations + ["Replay agreement is not security PASS or a new MEASURED execution.",
                "Source/collector authenticity and redacted original bytes are not authenticated.",
                "Normal inference remains the original saved observation; no request was sent."],
            "report": report.ref().model_dump(mode="json"), "plan": plan.ref().model_dump(mode="json"),
            "pack": pack.ref().model_dump(mode="json"), "official": report.official,
            "policy_status": report.policy_status.value, "pack_review_status": report.pack_review_status.value,
            "original_inventory_preserved": preserved}


def main(argv=None):
    parser = argparse.ArgumentParser(description="저장 OBS-03/09 로그 증거의 읽기 전용 재검증; 엔진을 기동하지 않습니다.")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report", help="정확한 RPT-ID@revision; 생략은 RPT가 하나일 때만 허용")
    parser.add_argument("--output", type=Path, required=True, help="원본과 분리된 새 결과 폴더")
    args = parser.parse_args(argv)
    try:
        from guard.update_validation import reserve_output
        output = reserve_output(args.output, protected=[args.store])
        result = analyze_saved_logs(args.store, args.report)
        text = ("저장 로그 재검증 일치" if result["accepted"] else "저장 로그 재검증 미확인 또는 불일치")
        text += "\n보안 PASS·새 실측·출처 인증을 뜻하지 않습니다. 원래 판정과 원본은 변경하지 않습니다.\n"
        text += "\n".join(f"{row['check_id']}/{row['side']}: {row['status']}" for row in result["checks"]) + "\n"
        for name, content in (("summary.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
                              ("summary.txt", text)):
            with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        print(text, end="")
        return 0 if result["accepted"] else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("저장 로그 재검증 실패: " + type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
