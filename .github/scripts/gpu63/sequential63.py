"""Run the current product on each release sequentially on one owned L4.

Each release gets its own audit PLAN/EVD/RES/RPT. These reports are retained
separately: they are not relabelled as a paired update RPT. Only the six checks
explicitly deferred by the operator are outside the requested scope.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import traceback

from guard.application import run_config
from guard.core.execution_recipe import bind_recipe
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import run_plan
from guard.core.select import select
from guard.models import DataOrigin, Mode, Policy, PolicyStatus, canonical_json, load_pack, new_id, utcnow
from guard.store import Store
from guard.store.bundle import export_bundle
from guard.store.packs import put_pack
from tools.j02_run_config import _row, dumps_toml
from tools.mvp_live_readiness import audit as live_audit, SCHEMA as INDEX_SCHEMA

import stock_capture as stock

EXCLUDED_TP2 = frozenset({"SUP-06", "REQ-14", "REQ-16", "REQ-21", "LIF-08", "LIF-11"})


def scope(pack):
    active = {check.id for check in pack.active_checks()}
    if len(active) != 69 or not EXCLUDED_TP2 <= active:
        raise ValueError("SINGLE_GPU_SCOPE_CHANGED")
    return sorted(active - EXCLUDED_TP2)


def policy(pack, deadline):
    paths = [stock.UPDATE / "docs/dev-1" / filename for filename in
             ("mvp-enterprise-policy-20261116.json", "mvp-enterprise-policy-20261118.json")]
    base, amendment = [json.loads(path.read_bytes()) for path in paths]
    if (base["status"] != amendment["status"] or amendment["status"] != "CONFIRMED"
            or amendment["base_document"]["file_sha256"] != sha256(paths[0].read_bytes()).hexdigest()
            or amendment["pack_ref"] != pack.ref().model_dump(mode="json")):
        raise ValueError("POLICY_DECISION_CHAIN_CHANGED")
    selected = set(scope(pack))
    runtime_keys = {(item["check_id"], item["key"]) for item in base["runtime_materialization_required"]}
    values = {}
    for item in [*base["decisions"], *amendment["decisions"]]:
        if item["check_id"] in selected and (item["check_id"], item["key"]) not in runtime_keys:
            values.setdefault(item["check_id"], {})[item["key"]] = item["value"]
    # Runtime rules are not observations or scalar policy values. Missing
    # current identity mappings stay missing and are reported by the product.
    return Policy(id=new_id("POL"), data_origin=DataOrigin.DECLARED, status=PolicyStatus.DRAFT,
                  pack=pack.ref(), values=values,
                  scope={"include": sorted(selected), "exclude": sorted(EXCLUDED_TP2)},
                  valid_from=utcnow() - timedelta(minutes=1), valid_until=deadline,
                  source_document_sha256=sha256(canonical_json([base, amendment])).hexdigest()).seal()


def configuration(pack, target_path):
    names = sorted({pack.checks[cid].executor for cid in scope(pack) if pack.checks[cid].executor})
    result = {"adapter": {"name": "vllm"}, "executors": {name: "kubernetes" for name in names}}
    for name in names:
        result[name] = {"target": str(target_path)}
    return result


def run_side(root, folder, side, captured, pack, pol):
    env = captured["environment"]
    target_path = captured["target_path"]
    cfg = configuration(pack, target_path)
    cfg_path = stock.save(folder, "guard.toml", dumps_toml(cfg).encode())
    policy_path = stock.save(folder, "policy.json", pol.model_dump(mode="json"))
    index = {"schema": INDEX_SCHEMA, "pack_ref": pack.ref().model_dump(mode="json"), "inputs": []}
    for cid in scope(pack):
        index["inputs"].append({"check_id": cid, "side": side, **{
            role: {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}
            for role, path in (("environment", captured["environment_path"]),
                               ("policy", policy_path), ("configuration", cfg_path))}})
    index_path = stock.save(folder, "current-input-sources.json", index)
    stock.save(folder, "live-input-preflight.json", live_audit(index_path, pack=pack, side=side))
    preparation = [_row(pack, cid, side, cfg, env, pol.values.get(cid, {}), policy_ref=pol.ref())
                   for cid in scope(pack)]
    stock.save(folder, "check-preparation.json", {"side": side, "rows": preparation,
               "requested_checks": scope(pack), "excluded_tp2": sorted(EXCLUDED_TP2)})

    store = Store(root / "stores" / side)
    put_pack(store, pack.path)
    store.put(env)
    store.put(pol)
    plan = bind_recipe(select(pack, [env], pol, mode=Mode.audit), cfg, [env])
    store.put(plan)
    stock.save(folder, "plan.json", plan.model_dump(mode="json"))
    rc = run_config(cfg)
    evidence = run_plan(store, pack, plan, rc)
    attempted = {item.check_id for item in evidence}
    selected = {item.check_id for item in plan.items if item.selected}
    if attempted != selected or attempted & EXCLUDED_TP2 or not attempted <= set(scope(pack)):
        raise ValueError("EXECUTED_SCOPE_MISMATCH")
    as_of = utcnow()
    judged = judge_plan(store, pack, plan, as_of, attempt_id=rc.attempt_id,
                        evidence=evidence, binary_security=True)
    rpt = build_report(store, pack, plan, as_of, attempt_id=rc.attempt_id,
                       evidence=evidence, judged=judged, binary_security=True)
    store.put(rpt)
    stock.save(folder, "report.json", rpt.model_dump(mode="json"))
    rows = {item.check_id: {"check_id": item.check_id, "execution_status": item.execution.status.value,
            "abort_reason": item.execution.abort_reason, "origin": item.data_origin.value,
            "evidence_ref": item.ref().model_dump(mode="json"),
            "cleanup_errors": item.execution.cleanup_errors} for item in evidence}
    for item in plan.items:
        if item.check_id in set(scope(pack)) and not item.selected:
            rows[item.check_id] = {"check_id": item.check_id, "execution_status": "NOT_SELECTED",
                                  "reason": str(item.not_selected_reason)}
    if set(rows) != set(scope(pack)):
        raise ValueError("ALL_63_DISPOSITION_ROWS_REQUIRED")
    summary = {"side": side, "version": env.engine.version, "mode": "audit",
               "source_main": stock.SOURCE_TREE, "requested": len(scope(pack)),
               "excluded_tp2": sorted(EXCLUDED_TP2), "plan_ref": plan.ref().model_dump(mode="json"),
               "report_ref": rpt.ref().model_dump(mode="json"), "official": rpt.official,
               "counts": dict(Counter(row["execution_status"] for row in rows.values())),
               "by_check": rows, "environment_ref": env.ref().model_dump(mode="json")}
    stock.save(folder, "summary.json", summary)
    export_bundle(store.root, folder / "evidence.zip", report_ref=rpt.ref())
    return summary


def execute(args):
    root = args.root.resolve(strict=True)
    if root.parent != Path("/srv/byul-a") or os.geteuid() != 0:
        raise ValueError("EXACT_OWNED_NODE_ROOT_REQUIRED")
    signal.signal(signal.SIGTERM, stock.interrupted)
    os.environ["KUBECONFIG"] = str(args.kubeconfig)
    export = root / "export"
    export.mkdir(mode=0o700)
    captures_dir = export / "capture"
    captures_dir.mkdir(mode=0o700)
    pack = load_pack(stock.UPDATE / "packs/PACK-BASELINE")
    selected = scope(pack)
    deadline = datetime.fromisoformat(os.environ["BYUL_SESSION_DEADLINE"].replace("Z", "+00:00"))
    pol = policy(pack, deadline)
    stock.save(export, "policy.json", pol.model_dump(mode="json"))
    approved, releases = stock.preflight(args.reference)
    model = stock.require_model(root, args.model_root, approved)
    stock.save(export, "model-inventory.json", model)
    summaries = {}
    phases = {}
    for side, release in releases.items():
        namespaces = {}
        phase = "verify-single-idle-gpu"
        try:
            context, expected_devices = stock.verify_host(args.kubeconfig, releases)
            run = root.name + "-" + side
            metadata, deployment, probe, cache = stock.render_side(root, run, release, args.model_root)
            cache.mkdir(mode=0o700, parents=True)
            os.chown(cache, 2000, 0)
            (cache / "home").mkdir(mode=0o700)
            os.chown(cache / "home", 2000, 0)
            namespace = stock.create(args.kubeconfig, {"apiVersion": "v1", "kind": "Namespace", "metadata": {
                "name": run, "labels": {"byul.lab/owned": "true", stock.LABEL: run, "guard.run": run}}})
            uid = namespace["metadata"]["uid"]
            namespaces[run] = uid
            account_uid = stock.default_service_account(args.kubeconfig, run, uid, wait=True)
            stock.add_network_policy(args.kubeconfig, run, "deny-all", stock.DENY_ALL)
            stock.verify_network(args.kubeconfig, run, serving=False)
            stock.create(args.kubeconfig, deployment)
            stock.create(args.kubeconfig, probe)
            phase = "start-" + release["version"]
            print(phase, flush=True)
            stock.kube(args.kubeconfig, "-n", run, "rollout", "status", "deployment/serving-engine", "--timeout=540s", timeout=545)
            stock.kube(args.kubeconfig, "-n", run, "wait", "--for=condition=Ready", f"pod/{probe['metadata']['name']}", "--timeout=60s", timeout=65)
            folder = captures_dir / side
            folder.mkdir(mode=0o700)
            captured, identities, prefix_error = stock.observe(args.kubeconfig, context, run, uid, metadata,
                                                               probe["metadata"]["name"], folder)
            if set(captured["gpu_devices"]) != expected_devices:
                raise ValueError("EXACT_SINGLE_GPU_BINDING_MISMATCH")
            for name, spec in stock.fixed_network_policies(run, serving=True).items():
                if name != "deny-all":
                    stock.add_network_policy(args.kubeconfig, run, name, spec)
            stock.save(folder, "network-isolation.json", stock.verify_network(args.kubeconfig, run, serving=True))
            stock.save(folder, "startup-prefix-status.json", {"error": prefix_error})
            phase = "product-all-63-" + side
            print(phase, flush=True)
            summaries[side] = run_side(root, folder, side, captured, pack, pol)
            stock.verify_generation(args.kubeconfig, run, uid, metadata, probe["metadata"]["name"],
                                    account_uid, captured, identities)
            phases[side] = {"phase": phase, "report_published": True}
        except Exception as error:
            phases[side] = {"phase": phase, "report_published": side in summaries,
                            "error_type": type(error).__name__, "diagnostic": stock.diagnostic(error)}
            print(json.dumps(phases[side]), flush=True)
        finally:
            cleanup = stock.cleanup_namespaces(args.kubeconfig, namespaces) if namespaces else {}
            phases.setdefault(side, {})["namespace_cleanup"] = cleanup
            if namespaces and set(cleanup.values()) != {"owned_namespace_removed"}:
                stock.save(export, "cleanup-unverified.json", phases)
                raise ValueError("SINGLE_GPU_RELEASE_BEFORE_NEXT_SIDE_UNVERIFIED")
            stock.save(export, side + "-phase.json", phases[side])
    result = {"schema": "byul-single-gpu-63-result/1", "source_main": stock.SOURCE_TREE,
              "requested_checks": selected, "excluded_tp2": sorted(EXCLUDED_TP2),
              "sequential_single_gpu": True, "reports_are_separate_audits": True,
              "phases": phases, "sides": summaries}
    stock.save(export, "result.json", result)
    return len(summaries) == 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--kubeconfig", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    raise SystemExit(0 if execute(parser.parse_args()) else 2)
