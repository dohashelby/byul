"""Reviewed warm-start LAB session: exact gates, three probes, monitor restore."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from serving_runtime.enforcement.intent import load_intent
from serving_runtime.operations.safety import evaluate
from tools.tetragon_exec_demo import ExecDemo, normal_request, read_status
from tools.tetragon_monitor_window import human_reviews, read_object, scope_execution_review
from tools.tetragon_trial import atomic_json, now
from tools.trial_cli import add_options


def kube(config, *args, timeout=35):
    result = subprocess.run(
        ["kubectl", "--context", config["context"], "-n", config["namespace"], *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError("session Kubernetes command failed")
    return result.stdout


def preflight(config, intent):
    if config.get("startup_mode") != "monitor_ready_then_enforce":
        raise ValueError("enforce cold startup is disabled; explicit warm-start mode required")
    if "bpf" not in Path("/sys/kernel/security/lsm").read_text().strip().split(","):
        raise ValueError("BPF LSM unavailable")
    if intent.exec_deny_exempt_callers:
        raise ValueError("this session must have no caller exemptions")
    if config["planning_upper_usd"] >= min(config["cost_stop_usd"], config["cost_cap_usd"]):
        raise ValueError("session budget exhausted")
    remaining = (datetime.fromisoformat(config["deadline"]) - datetime.now(timezone.utc)).total_seconds()
    if remaining < 360:
        raise ValueError("insufficient time before unchanged stop timer")
    for option, expected in [("is-active", "inactive"), ("is-enabled", "disabled")]:
        result = subprocess.run(["systemctl", option, "serving-runtime-agent"], capture_output=True, text=True)
        if result.stdout.strip() != expected:
            raise ValueError("agent must remain disabled and inactive")


def scoped_review(config, intent, snapshot):
    source = read_object(config["review"])
    catalog = read_object(config["catalog"])
    _, entries = scope_execution_review(snapshot, source, catalog, intent, {}, None)
    if not config.get("human_review_file"):
        raise ValueError("explicit per-event human review file required; no automatic verdicts")
    review_document = read_object(config["human_review_file"])
    if review_document.get("review_mode") != "per_event_manual":
        raise ValueError("per-event manual review required")
    decisions, reviewer = human_reviews(config["human_review_file"])
    evidence = {r["event_sha256"]: r for r in review_document.get("events", [])}
    if not reviewer or any(
        r["event_sha256"] not in decisions
        or not isinstance(evidence.get(r["event_sha256"], {}).get("basis"), str)
        or not evidence[r["event_sha256"]]["basis"].strip()
        for r in entries
    ):
        raise ValueError("unreviewed shell event or missing per-event basis")
    review, _ = scope_execution_review(snapshot, source, catalog, intent, decisions, reviewer)
    path = Path(config["output"]) / "scoped-review.json"
    atomic_json(path, review, mode=0o600)
    return path


def restore(demo, config, report, baseline_bytes):
    report["restore"] = demo.restore()
    if baseline_bytes is not None:
        Path(config["baseline"]).write_bytes(baseline_bytes)


def execute(config):
    intent = load_intent(config["intent"])
    preflight(config, intent)
    output = Path(config["output"])
    output.mkdir(mode=0o700, exist_ok=False)
    snapshot = read_status(config["status"], config["context"])
    review_path = scoped_review(config, intent, snapshot)
    review = read_object(review_path)
    body = Path(config["request"]).read_bytes()
    infer = lambda: normal_request(config["gateway_url"], body, config["token_file"])
    demo = ExecDemo(
        {
            "context": config["context"],
            "pod": config["pod"],
            "output": str(output),
            "status_path": config["status"],
            "review_path": str(review_path),
        },
        intent,
        snapshot,
        review,
        infer=infer,
    )
    demo.monitor_seconds = config["monitor_seconds"]
    report = {
        "data_origin": "real",
        "started_at": now(),
        "monitor_seconds": config["monitor_seconds"],
        "monitor_setting": "LAB 설정으로 관찰 기간 축소",
        "runs": [],
    }
    baseline_bytes = Path(config["baseline"]).read_bytes()
    try:
        report["checks"] = evaluate(snapshot, demo.name, intent, review, config["monitor_seconds"])["checks"]
        if not all(report["checks"].values()):
            raise ValueError("all six gates must pass")
        if infer() != 200:
            raise ValueError("normal inference failed before enforce")
        demo.activate(report)
        baseline = json.loads(baseline_bytes)
        for p in baseline["policies"]:
            if p["name"] == demo.name and p["namespace"] == config["namespace"]:
                if p["spec_sha256"] != report["transition"]["expected"]["spec_sha256"]:
                    raise ValueError("baseline policy spec changed")
                p["mode"] = "enforce"
        atomic_json(config["baseline"], baseline, mode=0o600)
        report["startup_mode"] = "monitor_ready_then_enforce"
        for index in range(3):
            run = {"index": index + 1, "transition": report["transition"], "started_at": now()}
            demo.exercise(run, already_enforced=True)
            run["finished_at"] = now()
            report["runs"].append(run)
            atomic_json(output / "session.json", report, mode=0o600)
            if not run["denial_confirmed"]:
                raise ValueError("demo proof incomplete")
        report["passed"] = True
    except Exception as error:
        report.update(passed=False, error_class=type(error).__name__)
    finally:
        if demo.activated:
            try:
                restore(demo, config, report, baseline_bytes)
            except Exception as error:
                report.update(passed=False, restore_error_class=type(error).__name__)
        report["finished_at"] = now()
        if not report.get("restore", {}).get("ok"):
            report["passed"] = False
        atomic_json(output / "session.json", report, mode=0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_options(
        parser,
        [
            (("--config",), {"required": True}),
            (("--execute",), {"action": "store_true"}),
            (("--allow-engine-pod",), {"action": "store_true"}),
        ],
    )
    args = parser.parse_args()
    if not args.execute or not args.allow_engine_pod:
        parser.error("explicit --execute and --allow-engine-pod are required")
    report = execute(read_object(args.config))
    print(
        json.dumps(
            {
                "passed": report.get("passed"),
                "checks": report.get("checks"),
                "cold_start": report.get("cold_start"),
                "runs": [
                    {
                        "index": r["index"],
                        "normal_before": r.get("normal_before"),
                        "normal_after": r.get("normal_after"),
                        "denial_confirmed": r.get("denial_confirmed"),
                        "pod_stable": r.get("pod_stable"),
                        "events": len(r.get("events", [])),
                    }
                    for r in report["runs"]
                ],
                "monitor_restored": report.get("restore", {}).get("ok"),
                "original_cache_restored": report.get("original_cache_restored"),
                "error_class": report.get("error_class"),
                "restore_error_class": report.get("restore_error_class"),
            }
        )
    )
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
