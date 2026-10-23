"""Synthetic update graphs for acceptance tests; never engine-run evidence.

The MEASURED EVD labels below are constructed test metadata for the verifier's
source checks. They are not saved in repository evidence or reported as a real
engine measurement. Probe attachments explicitly identify synthetic test input.
"""
from copy import deepcopy
from datetime import timedelta

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.select import select
from guard.executors.fake import SCENARIOS_BY_CHECK
from guard.models import Evidence, sha256_hex
from guard.store import Store
from guard.store.packs import put_pack
from guard.update_acceptance import CHECKS
from tests.builders import PACK_DIR, T0, baseline_pack, environment, policy

CPU = {"architecture": "amd64", "model": "SYNTHETIC TEST CPU", "system": "Linux",
       "kernel": "SYNTHETIC TEST KERNEL", "threads": 2}
VALUES = {
    "REQ-01": {"allowed_callers": ["allowed"]},
    "REQ-03": {"general_allowed_kinds": ["inference"]},
    "PLT-01": {"allowed_capabilities": [], "allowed_mounts": ["/models:ro"], "allowed_devices": [],
               "require_non_root": True, "allowed_exec_writable_paths": ["/guard-cache"]},
    "PLT-04": {"forbidden_env_names": ["GUARD_TEST_CREDENTIAL"], "forbidden_mount_paths": []},
    "REQ-09": {"max_input_tokens": 127, "rejection_codes": ["400", "413", "422"],
               "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]},
}


def _observations(cid, fail):
    if cid in SCENARIOS_BY_CHECK:
        scenario = ({"REQ-01": "direct_200", "REQ-03": "role_open", "PLT-01": "root_user"}[cid]
                    if fail else "pass")
        return deepcopy(SCENARIOS_BY_CHECK[cid][scenario]["observations"])
    if cid == "PLT-04":
        probe = {"functional_probes_planned": ["POST /v1/completions"],
                 "functional_probe_results": {"POST /v1/completions": "pass"}}
        values = {**probe, "process_scan_complete": True,
                  "worker_env_names": ["GUARD_TEST_CREDENTIAL"] if fail else [],
                  "exposed_credential_paths": []}
    else:
        probe = {"functional_probes_planned": ["before", "after_token_id", "after_length"],
                 "functional_probe_results": {key: "pass" for key in ("before", "after_token_id", "after_length")}}
        values = {**probe, "challenges_planned": ["token_id", "length"],
                  "challenge_outcomes": {"token_id": "400", "length": "200" if fail else "400"},
                  "accepted_challenges": ["length"] if fail else [],
                  "input_endpoints_tested": ["POST /v1/completions"], "input_formats_tested": ["token_ids"]}
    return {key: {"collected": True, "value": value} for key, value in values.items()}


def build_update_case(tmp_path, *, candidate_fail=False, baseline_fail=False, different_config=False,
                      same_version=False, same_image=False, mutate_evidence=None):
    """Build only synthetic objects with the real pack/rules and product report.

    mutate_evidence(cid, side, raw) may modify test input before sealing and
    judging, so a negative scenario cannot pass only because of a stale hash.
    Returns (sealed_report_dict, all_stored_object_dicts, verified_pack).
    """
    pack = baseline_pack()
    store = Store(tmp_path / "store")
    put_pack(store, PACK_DIR)
    envs = [
        environment(id=f"ENV-SYNTHETIC-{side.upper()}", origin="DECLARED",
                    engine={"name": "vllm", "version": "test-1" if side == "baseline" or same_version else "test-2",
                            "image_digest": "example/synthetic-cpu@sha256:" + (
                                "a" if side == "baseline" or same_image else "b") * 64},
                    adapter={"name": "vllm", "version": "SYNTHETIC"},
                    runtime={"gpu_count": 0, "cpu": CPU},
                    model={"id": "SYNTHETIC", "revision": "SYNTHETIC", "inventory_sha256": "c" * 64},
                    facts={"gateway": {"value": True, "origin": "DECLARED", "source": "synthetic test input"}})
        for side in ("baseline", "candidate")
    ]
    pol = policy(pack.ref(), values=deepcopy(VALUES), scope={"include": list(CHECKS)})
    for obj in [*envs, pol]:
        store.put(obj)
    plan = select(pack, envs, pol, mode="update")
    store.put(plan)
    for side, env, fail in zip(("baseline", "candidate"), envs, (baseline_fail, candidate_fail)):
        for cid in CHECKS:
            check = pack.checks[cid]
            artifact = store.put_artifact(
                f'{{"synthetic_test_only":true,"check":"{cid}","side":"{side}"}}\n'.encode())
            raw = {
                "id": f"EVD-SYNTHETIC-{cid}-{side}", "created_at": T0, "data_origin": "MEASURED",
                "refs": [plan.ref().model_dump(mode="json"), env.ref().model_dump(mode="json")],
                "plan": plan.ref(), "check_id": cid, "side": side, "environment": env.ref(),
                "execution": {"status": "COMPLETED",
                              "executor": {"name": check.executor, "version": "SYNTHETIC",
                                           "config_sha256": sha256_hex(b"synthetic test config")},
                              "adapter": {"name": "vllm", "version": "SYNTHETIC"},
                              "started_at": T0, "finished_at": T0 + timedelta(seconds=1)},
                "normal_control": {"attempted": True, "succeeded": True, "detail": "SYNTHETIC test input"},
                "challenges": [{"step": step, "attempted": True} for step in check.challenge_steps],
                "observations": _observations(cid, fail),
                "artifacts": [{"name": "probe_log.jsonl", "path": artifact.path, "sha256": artifact.sha256,
                               "bytes": artifact.bytes, "collected_at": T0,
                               "collector": f"{check.executor}/SYNTHETIC"}],
                "comparability": {"model_inventory_sha256": env.model.inventory_sha256,
                                  "fixture_sha256": sha256_hex(b"synthetic fixture"),
                                  "config_sha256": sha256_hex(b"changed synthetic config"
                                      if different_config and side == "candidate" else b"synthetic config"),
                                  "cpu": CPU},
            }
            if mutate_evidence:
                mutate_evidence(cid, side, raw)
            store.put(Evidence(**raw).seal())
    judge_plan(store, pack, plan, T0)
    report = build_report(store, pack, plan, T0)
    store.put(report)
    objects = [obj.to_dict() for kind in ("ENV", "POL", "CHG", "PLAN", "EVD", "RES", "RPT")
               for obj in store.iter_kind(kind)]
    return report.to_dict(), objects, pack
