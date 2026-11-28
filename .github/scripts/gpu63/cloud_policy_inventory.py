"""Link retained cloud-run metadata, policy inputs and original evidence without external I/O."""

import argparse
from contextlib import closing, contextmanager
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import sqlite3
import tarfile
from zipfile import BadZipFile, ZipFile

from guard.models import Environment, Evidence, Plan, Policy, Report, Result, canonical_json, load_pack
from guard.runtime.bounded import strict_json
from tools import mvp_credential_copy_inputs, mvp_model_grant_inputs
from tools.mvp_a_input_preflight import _read
from tools.mvp_runtime_policy import AMENDMENT, BASE_DECISIONS, ROOT, approved_rules
from tools.mvp_versions import versions


SIDES = ("baseline", "candidate")
# BYUL workbook, '전체 범위와 근거', A31:C62. Do not re-sort by area, ID or status.
ORDER = (
    ("REQ-05", "gateway_authorization", "actual principal-to-model mapping and gateway policy receipt"),
    ("PLT-12", "credential_copy_lifecycle", "owned copy selection, retirement event and retained treatment journal"),
    ("PLT-02", "local_ipc", "owned IPC path, declared subject properties and permission observations"),
    ("PLT-06", "local_ipc", "deployed worker trust model and independent UID/isolation observations"),
    ("OBS-04", "egress", "usage policy, selected target and telemetry state tied to an actual process"),
    ("OBS-05", "egress", "approved usage sinks and fields with matching boundary observations"),
    ("REQ-01", "gateway_routes", "approved service topology and route access audit"),
    ("REQ-03", "gateway_routes", "deployed role-to-route inventory and access audit"),
    ("REQ-02", "gateway_routes", "native route inventory and bound route-record session"),
    ("REQ-04", "gateway_routes", "gateway and engine identity journals with matching run and request bindings"),
    ("REQ-06", "gateway_routes", "deployed stored response service, ownership and retention journal"),
    ("PLT-08", "api_transport", "deployed API segment identity and transport evidence"),
    ("PLT-07", "egress", "deployed enforcement component identity and matching boundary evidence"),
    ("LIF-07", "worker_initialization", "selected scenario, prepared observer and per-worker readiness journal"),
    ("LIF-09", "worker_initialization", "approved recorded initialization failure and prepared observer"),
    ("LIF-05", "generation_transition", "deployment generations, sample times and per-generation prepared evidence"),
    ("LIF-06", "generation_transition", "runtime change generations, sample times and owned transition journal"),
    ("LIF-16", "generation_transition", "recovery generations, restored artifacts and shared timeline"),
    ("PLT-10", "issuer_revocation", "actual issuer event, credential identity and bound access observations"),
    ("PLT-11", "issuer_revocation", "actual issuer event and previously established session timeline"),
    ("REQ-20", "engine_sensor", "release-reviewed native source, prepared sensor and buffer lifetime events"),
    ("REQ-15", "engine_sensor", "native cache group identity and approved sharing observations"),
    ("REQ-17", "engine_sensor", "bound calibration session and owner, time and end records"),
    ("REQ-18", "engine_sensor", "native request and slot lifetimes in the deployed build"),
    ("LIF-13", "engine_sensor", "release/reacquire events and approved generation identity"),
    ("REQ-19", "engine_sensor", "verified source and matched-build instrumentation scope"),
    ("REQ-09", "engine_input_contract", "installed tokenizer facts and full-scope input admission observations"),
    ("REQ-08", "engine_input_contract", "release-specific collection capabilities and retained validation evidence"),
    ("REQ-10", "engine_input_contract", "full sampling policy coverage including max_tokens"),
    ("REQ-07", "template_contract", "approved template identity and pre/post render limit observations"),
    ("PLT-09", "record_transport_profile", "registered collection profile for engine-collector and collector-sink"),
    ("SUP-13", "template_contract", "registered template inspection profile for the original scope"),
)
IDS = {check_id for check_id, _, _ in ORDER}
FILES = ("environment.json", "policy.json", "target.json", "plan.json", "report.json", "summary.json",
         "live-input-preflight.json", "normal-scope-capabilities.json", "startup-owner.json",
         "network-isolation.json", "credential-fixture-scope.json", "workload.json", "evidence.zip")
FILE_BOUND = 8 * 1024**2
ARCHIVE_BOUND = 256 * 1024**2
HEX = re.compile(r"[a-f0-9]{64}")
PAIRED_SCOPES = ("REQ-02", "PLT-12")
PAIRED_FILES = ("guard-report.json", "guard-evidence.zip")
PAIRED_OBSERVATIONS = {
    "REQ-02": (
        ("route_events", "native_route_events", "native_route_associations"),
        ("route_producer_authenticated", "native_route_authorization_verified",
         "route_authorization_scope_complete", "native_route_scope_complete"),
    ),
    "PLT-12": (
        ("copy_inventory_declarations", "copy_event_declarations", "copy_record_snapshots"),
        ("copy_producer_authenticated", "copy_issuer_verified", "copy_membership_verified",
         "copy_treatment_verified", "copy_reuse_enforcer_verified", "copy_scope_complete"),
    ),
}


def checksum(raw):
    return sha256(raw).hexdigest()


def ref(obj):
    return obj.ref().model_dump(mode="json")


def document(raw):
    data = strict_json(raw, limit=FILE_BOUND)
    if type(data) is not dict:
        raise ValueError("EXPECTED_JSON_OBJECT")
    return data


def extract_known(path):
    """Read only fixed archive names. Tar members are streamed and never extracted to disk."""
    names = {f"capture/{side}/{name}" for side in SIDES for name in FILES}
    paired_names = {f"{cid.lower()}/{name}" for cid in PAIRED_SCOPES for name in PAIRED_FILES}
    if path.is_dir():
        result = {}
        for name in paired_names:
            item = path / name
            if item.is_file():
                if item.resolve() != item.absolute():
                    raise ValueError("EXPORT_SYMLINK_DISALLOWED")
                result[name] = _read(item, max_bytes=FILE_BOUND)[0]
        for name in names:
            side, basename = name.split("/")[1:]
            paired_file = basename if basename in {"policy.json", "plan.json", "report.json", "evidence.zip"} else (
                f"{side}-{basename}")
            ordinary = path / name
            paired = path / paired_file
            if ordinary.is_file() and paired.is_file() and (path / f"{side}-environment.json").is_file():
                raise ValueError("AMBIGUOUS_EXPORT_LAYOUT")
            item = paired if (path / f"{side}-environment.json").is_file() else ordinary
            if item.is_file():
                if item.resolve() != item.absolute():
                    raise ValueError("EXPORT_SYMLINK_DISALLOWED")
                result[name] = _read(item, max_bytes=FILE_BOUND)[0]
        return result
    if path.resolve() != path.absolute() or not path.is_file() or path.stat().st_size > ARCHIVE_BOUND:
        raise ValueError("EXPORT_ARCHIVE_DISALLOWED")
    names.update(paired_names)
    result, total = {}, 0
    with tarfile.open(path, mode="r|*") as archive:
        for index, member in enumerate(archive):
            total += member.size
            if index >= 1024 or total > ARCHIVE_BOUND:
                raise ValueError("EXPORT_ARCHIVE_TOO_LARGE")
            name = member.name.removeprefix("./").removeprefix("export/")
            if name not in names:
                continue
            if name in result or not member.isfile() or not 0 < member.size <= FILE_BOUND:
                raise ValueError("EXPORT_MEMBER_INVALID")
            with archive.extractfile(member) as stream:
                result[name] = stream.read(FILE_BOUND + 1)
            if len(result[name]) != member.size:
                raise ValueError("EXPORT_MEMBER_CORRUPT")
    return result


def checked_obj(raw, cls):
    obj = cls.model_validate(document(raw))
    if not obj.hash_ok():
        raise ValueError("OBJECT_CONTENT_HASH_MISMATCH")
    return obj


def zip_member(bundle, manifest, name):
    item = manifest["files"][name]
    info = bundle.getinfo(name)
    if (type(item) is not dict or set(item) != {"bytes", "sha256"}
            or not 0 <= info.file_size <= FILE_BOUND or info.file_size != item["bytes"]):
        raise ValueError("EVIDENCE_MEMBER_SIZE_MISMATCH")
    raw = bundle.read(info)
    if checksum(raw) != item["sha256"]:
        raise ValueError("EVIDENCE_MEMBER_HASH_MISMATCH")
    return raw


@contextmanager
def bound_store(raw):
    with ZipFile(BytesIO(raw)) as bundle:
        members = bundle.infolist()
        names = {member.filename for member in members}
        if (len(members) > 1024 or len(names) != len(members)
                or sum(member.file_size for member in members) > ARCHIVE_BOUND
                or bundle.getinfo("manifest.json").file_size > FILE_BOUND):
            raise ValueError("EVIDENCE_ARCHIVE_BOUND")
        manifest = document(bundle.read("manifest.json"))
        if manifest.get("format") != "guard-store-bundle/1":
            raise ValueError("EVIDENCE_ARCHIVE_FORMAT")
        store = zip_member(bundle, manifest, "store.sqlite")
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.deserialize(store)
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA query_only = ON")
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, FILE_BOUND)

            def find(reference, cls):
                matches = connection.execute("SELECT body FROM objects WHERE id=? AND revision=? AND kind=? LIMIT 2",
                                             (reference["id"], reference["revision"], reference["kind"])).fetchall()
                if len(matches) != 1:
                    raise ValueError("EVIDENCE_REF_AMBIGUOUS" if matches else "EVIDENCE_REF_MISSING")
                obj = checked_obj(matches[0][0].encode(), cls)
                if ref(obj) != reference:
                    raise ValueError("EVIDENCE_REF_MISMATCH")
                return obj

            yield bundle, manifest, find


def bound_objects(raw, report, plan, env, side="single"):
    """Check only exact EVD/RES refs named by the original report."""
    with bound_store(raw) as store:
        return report_rows(*store, report, plan, env, side)


def report_rows(bundle, manifest, find, report, plan, env, side, check_id=None):
    outcomes = {}
    report_refs = [entry.model_dump(mode="json") for entry in report.refs]
    manifest_refs = [entry.model_dump(mode="json") for entry in report.inputs_manifest] if check_id else []
    for row in report.to_dict()["by_check"]:
        cid, outcome = row["check_id"], row.get(side)
        if (cid not in IDS or (check_id is not None and cid != check_id)
                or outcome is None or outcome.get("result") is None):
            continue
        try:
            result_ref = outcome["result"]
            res = find(result_ref, Result)
            evidence_ref = outcome.get("evidence_ref") or res.evidence.model_dump(mode="json")
            if evidence_ref not in report_refs or result_ref not in report_refs:
                raise ValueError("EVIDENCE_REF_UNBOUND")
            if check_id and (evidence_ref not in manifest_refs or result_ref not in manifest_refs):
                raise ValueError("PAIRED_ROW_MANIFEST_MISMATCH")
            evd = find(evidence_ref, Evidence)
            if check_id and ([link for link in res.refs if link.kind == "POL"] != [plan.inputs.policy]
                             or evd.ref() not in res.refs or res.evaluation_mode is not None):
                raise ValueError("PAIRED_RESULT_POLICY_MISMATCH")
            if (evd.check_id != cid or res.check_id != cid or evd.plan != plan.ref()
                    or evd.environment != env.ref() or res.evidence != evd.ref()
                    or evd.side.value != side or res.side.value != side
                    or outcome.get("execution_status", evd.execution.status.value) != evd.execution.status.value
                    or res.verdicts.security.value != outcome["security"]):
                raise ValueError("EVIDENCE_RELATION_MISMATCH")
            artifacts = []
            for attachment in evd.artifacts:
                name = f"artifacts/{attachment.sha256}"
                if not HEX.fullmatch(attachment.sha256):
                    raise ValueError("EVIDENCE_ARTIFACT_REF_INVALID")
                content = zip_member(bundle, manifest, name)
                if len(content) != attachment.bytes or checksum(content) != attachment.sha256:
                    raise ValueError("EVIDENCE_ARTIFACT_REF_MISMATCH")
                artifacts.append({"name": attachment.name, "member": name, "sha256": checksum(content),
                                  "bytes": len(content)})
            reason = (evd.execution.abort_reason or "").partition(":")[0]
            outcomes[cid] = {"evidence_ref": evidence_ref, "result_ref": result_ref,
                             "execution_status": evd.execution.status.value, "security": outcome["security"],
                             "security_failure_tags": outcome.get("security_failure_tags", []),
                             "data_origin": evd.data_origin.value, "normal_attempted": evd.normal_control.attempted,
                             "challenge_attempted": any(item.attempted for item in evd.challenges),
                             "collected_observations": sorted(key for key, value in evd.observations.items()
                                                              if value.collected),
                             "abort_code": reason if re.fullmatch(r"[A-Z][A-Z_0-9]{0,100}", reason) else None,
                             "artifacts": artifacts}
            if check_id in PAIRED_OBSERVATIONS:
                lists, flags = PAIRED_OBSERVATIONS[check_id]
                counts, observed = {}, {}
                for key, observation in evd.observations.items():
                    if observation.collected and key in lists and type(observation.value) is list:
                        counts[key] = len(observation.value)
                    if observation.collected and key in flags and type(observation.value) is bool:
                        observed[key] = observation.value
                outcomes[cid]["execution_attempt_id"] = evd.execution.attempt_id
                outcomes[cid]["retained_observation_counts"] = counts
                outcomes[cid]["retained_observation_flags"] = observed
        except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as error:
            outcomes[cid] = {"error": error_code(error)}
    return outcomes


def inspect_paired_scope(files, cid):
    """Read a check-scoped historical bundle without treating it as deployment input."""
    raws = {name: files[f"{cid.lower()}/{name}"] for name in PAIRED_FILES
            if f"{cid.lower()}/{name}" in files}
    result = {"files": {name: {"sha256": checksum(raw), "bytes": len(raw)} for name, raw in raws.items()},
              "deployment_target_verified": False, "report_bundle_verified": False, "sides": {}}
    try:
        if set(raws) != set(PAIRED_FILES):
            raise ValueError("PAIRED_REPORT_OR_EVIDENCE_MISSING")
        report = checked_obj(raws["guard-report.json"], Report)
        result["report_ref"] = ref(report)
        report_rows_by_check = {row.check_id: row for row in report.by_check}
        if (report.mode.value != "update" or len(report_rows_by_check) != len(report.by_check)
                or cid not in report_rows_by_check or not report_rows_by_check[cid].selected
                or report_rows_by_check[cid].carried_over is not None):
            raise ValueError("PAIRED_REPORT_SCOPE_MISMATCH")
        with bound_store(raws["guard-evidence.zip"]) as (bundle, manifest, find):
            stored_report = find(ref(report), Report)
            if stored_report != report:
                raise ValueError("PAIRED_REPORT_STORE_MISMATCH")
            result["report_bundle_verified"] = True
            inputs = report.inputs_manifest
            plans = [item for item in inputs if item.kind == "PLAN"]
            if len(plans) != 1:
                raise ValueError("PAIRED_PLAN_REF_MISSING_OR_AMBIGUOUS")
            plan = find(plans[0].model_dump(mode="json"), Plan)
            plan_items = {item.check_id: item for item in plan.items}
            if (len(plan_items) != len(plan.items) or cid not in plan_items or not plan_items[cid].selected
                    or plan_items[cid].carried_over is not None):
                raise ValueError("PAIRED_PLAN_SELECTION_MISMATCH")
            policy = find(plan.inputs.policy.model_dump(mode="json"), Policy)
            env_refs = plan.inputs.environments
            if (plan.mode != report.mode or len(env_refs) != 2 or env_refs[0] == env_refs[1]
                    or plan.inputs.pack != policy.pack or report.policy_status != policy.status
                    or any(item not in inputs or item not in report.refs
                           for item in (plan.ref(), policy.ref(), policy.pack, *env_refs))):
                raise ValueError("PAIRED_PLAN_INPUTS_MISMATCH")
            known_versions = versions()
            sides = {}
            for side, env_ref in zip(SIDES, env_refs):
                expected = known_versions[side]
                env = find(env_ref.model_dump(mode="json"), Environment)
                if (env.engine.name, env.engine.version, env.engine.image_digest) != (
                        expected["engine"], expected["version"], expected["image"]):
                    raise ValueError("PAIRED_RELEASE_MISMATCH")
                row = report_rows(bundle, manifest, find, report, plan, env, side, cid).get(cid, {})
                if "error" in row:
                    raise ValueError(row["error"])
                if not row:
                    raise ValueError("PAIRED_ROW_MISSING")
                sides[side] = {"environment_ref": ref(env), "version": env.engine.version, "row": row}
            attempts = {side["row"]["execution_attempt_id"] for side in sides.values()}
            if (len(attempts) != 1 or not all(attempts)
                    or (report.execution_attempt_id is not None and report.execution_attempt_id not in attempts)):
                raise ValueError("PAIRED_EXECUTION_ATTEMPT_MISMATCH")
            result.update(sides=sides, plan_ref=ref(plan), policy_ref=ref(policy), official=report.official,
                          report_data_origin=report.data_origin.value, policy_status=policy.status.value,
                          shared_execution_attempt_id=next(iter(attempts)),
                          execution_attempt_anchored_in_report=report.execution_attempt_id is not None)
    except (KeyError, TypeError, ValueError, OSError, BadZipFile, sqlite3.Error) as error:
        result["error"] = error_code(error)
    return result


def error_code(error):
    value = str(error)
    return value if re.fullmatch(r"[A-Z][A-Z_0-9]{0,100}", value) else type(error).__name__


def inspect_side(files, side):
    raws = {name: files[f"capture/{side}/{name}"] for name in FILES if f"capture/{side}/{name}" in files}
    result = {"files": {name: {"sha256": checksum(raw), "bytes": len(raw)} for name, raw in raws.items()},
              "bindings": {}, "rows": {}, "missing_files": sorted(set(FILES[:6]) - raws.keys())}
    parsed = {}
    for name, cls in (("environment", Environment), ("policy", Policy), ("plan", Plan), ("report", Report)):
        filename = name + ".json"
        if filename in raws:
            try:
                parsed[name] = checked_obj(raws[filename], cls)
            except (TypeError, ValueError) as error:
                result["bindings"][f"{name}_error"] = error_code(error)
    env, policy, plan, report = (parsed.get(name) for name in ("environment", "policy", "plan", "report"))
    extra = {}
    for name in ("target", "summary", "startup-owner", "network-isolation", "credential-fixture-scope",
                 "live-input-preflight", "normal-scope-capabilities"):
        filename = name + ".json"
        if filename in raws:
            try:
                extra[name] = document(raws[filename])
            except (TypeError, ValueError) as error:
                result["bindings"][f"{name}_error"] = error_code(error)
    target = extra.get("target")
    expected = versions()[side]
    target_linked = bool(target and env and target.get("environment_ref") == ref(env)
                         and target.get("observed_version") == env.engine.version == expected["version"]
                         and target.get("engine", {}).get("image_digest") == env.engine.image_digest == expected["image"])
    plan_linked = bool(env and policy and plan and env.ref() in plan.inputs.environments
                       and plan.inputs.policy == policy.ref() and plan.inputs.pack == policy.pack)
    report_linked = bool(plan_linked and report and all(item in report.refs for item in
                                                       (env.ref(), policy.ref(), plan.ref())))
    result["bindings"].update(target_env_exact=target_linked, plan_env_policy_exact=plan_linked,
                              report_plan_env_policy_exact=report_linked)
    result["environment_ref"] = ref(env) if env else None
    result["policy_ref"] = ref(policy) if policy else None
    result["report_ref"] = ref(report) if report_linked else None
    if target_linked:
        owner = (target.get("kubernetes") or {}).get("engine")
        result["recorded_generation"] = {"version": env.engine.version, "tp_size": env.runtime.tp_size,
                                          "model_inventory_sha256": env.model.inventory_sha256,
                                          "target_file_sha256": checksum(raws["target.json"]),
                                          "engine_owner_sha256": checksum(canonical_json(owner))
                                          if type(owner) is dict else None}
    result["policy_status"] = policy.status.value if policy else None
    result["gateway_fact_bound"] = bool(env and target_linked and env.facts.get("gateway")
                                        and env.facts["gateway"].value is True
                                        and env.facts["gateway"].source and not env.facts["gateway"].conflict
                                        and env.facts["gateway"].origin.value in {"DECLARED", "MEASURED"})
    result["runtime_values_present"] = (
        {"REQ-05": "selected_model_grants" in policy.values.get("REQ-05", {}),
         "PLT-12": "copy_treatments" in policy.values.get("PLT-12", {})} if policy else None)
    if "summary" in extra and report_linked:
        summary = extra["summary"]
        if (summary.get("environment_ref") == ref(env) and summary.get("report_ref") == ref(report)
                and summary.get("plan_ref") == ref(plan) and summary.get("side") == side):
            result["bindings"]["summary_report_exact"] = True
            source = summary.get("source_main")
            if type(source) is str and re.fullmatch(r"[a-f0-9]{40}", source):
                result["recorded_source_main"] = source
    if "startup-owner" in extra and target_linked:
        owner = extra["startup-owner"]
        if (owner.get("environment_ref") == ref(env) and owner.get("version") == env.engine.version
                and owner.get("target_file_sha256") == checksum(raws["target.json"])):
            result["recorded_startup_owner"] = {"same_owner_before_after": owner.get("same_owner_before_after"),
                                                "startup_log_prefix_captured": owner.get("startup_log_prefix_captured")}
    if "network-isolation" in extra:
        network = extra["network-isolation"]
        result["recorded_network_metadata"] = {key: network.get(key) for key in
                                               ("deny_all_verified", "public_service_or_ingress_present")}
    if "credential-fixture-scope" in extra:
        fixture = extra["credential-fixture-scope"]
        result["recorded_credential_fixture"] = {key: fixture.get(key) for key in
                                                 ("enterprise_identity_authenticated", "memory_only_canary", "policy_status")}
    if report_linked and "evidence.zip" in raws:
        try:
            evidence_side = side if report.mode.value == "update" else "single"
            result["rows"] = bound_objects(raws["evidence.zip"], report, plan, env, evidence_side)
        except (KeyError, TypeError, ValueError, OSError, BadZipFile, sqlite3.Error) as error:
            result["bindings"]["evidence_error"] = error_code(error)
    if "live-input-preflight" in extra:
        result["preflight"] = {row["check_id"]: {"blocking_codes": [code for code in row["blocking_codes"]
                              if type(code) is str and re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", code)],
                              "input_preflight_passed": row.get("input_preflight_passed"),
                              "live_target_verified": row.get("live_target_verified")}
                               for row in extra["live-input-preflight"].get("rows", [])
                               if row["check_id"] in IDS and row.get("side") == side}
    if "normal-scope-capabilities" in extra:
        normal = extra["normal-scope-capabilities"]
        if env and target_linked and normal.get("environment_ref") == ref(env) and (
                normal.get("target_sha256") == checksum(canonical_json(target))):
            result["normal_scope"] = {cid: {"mode": row.get("mode"), "input_scope_ready": row.get("input_scope_ready")}
                                      for cid, row in normal.get("checks", {}).items() if cid in IDS}
    return result, env, target


def input_audit(policy_path, supplied, environments, targets):
    specs = {"REQ-05": ("selected_model_grants", mvp_model_grant_inputs.materialize),
             "PLT-12": ("copy_treatments", mvp_credential_copy_inputs.materialize)}
    pack = load_pack(ROOT / "packs/PACK-BASELINE") if policy_path else None
    policy = checked_obj(_read(policy_path)[0], Policy) if policy_path else None
    outcomes = {}
    for cid, (key, validator) in specs.items():
        status = outcomes[cid] = {"policy_value": key, "authority_checked": False, "input_bound": False,
                                  "runtime_status": "UNVERIFIED", "missing": []}
        if policy is None:
            status["missing"].append("exact_confirmed_policy_not_supplied")
        else:
            try:
                approved_rules(policy, pack, cid)
                status["authority_checked"] = True
            except (ValueError, KeyError) as error:
                status["missing"].append(error_code(error))
        if cid not in supplied:
            status["missing"].append("hash_pinned_owned_input_not_supplied")
            continue
        source, digest = supplied[cid]
        try:
            raw = _read(source, max_bytes=FILE_BOUND)[0]
            if checksum(raw) != digest:
                raise ValueError("INPUT_FILE_DIGEST_MISMATCH")
            status["supplied_source"] = {"path": str(source), "sha256": digest}
            if status["missing"]:
                continue
            if not all(environments.values()) or cid == "REQ-05" and not all(targets.values()):
                raise ValueError("EXACT_ARCHIVED_PAIR_UNAVAILABLE")
            if cid == "REQ-05":
                validator(policy, pack, environments, targets, document(raw))
            else:
                validator(policy, pack, environments, document(raw))
            status["input_bound"] = True
        except (KeyError, TypeError, OSError, ValueError) as error:
            status["missing"].append(error_code(error))
    return outcomes


def inventory(exports, *, policy_path=None, supplied=None):
    authority = {path.name: {"sha256": checksum(raw), "status": document(raw)["status"],
                             "scope": document(raw)["scope"]}
                 for path in (BASE_DECISIONS, AMENDMENT) for raw in [_read(path)[0]]}
    groups = []
    for export in exports:
        files = extract_known(export)
        inspected = {side: inspect_side(files, side) for side in SIDES}
        sides = {side: info for side, (info, _, _) in inspected.items()}
        runtime_inputs = input_audit(policy_path, supplied or {},
                                     {side: env for side, (_, env, _) in inspected.items()},
                                     {side: target for side, (_, _, target) in inspected.items()})
        group = {"source": str(export), "sides": sides, "runtime_policy_inputs": runtime_inputs}
        scopes = {cid: inspect_paired_scope(files, cid) for cid in PAIRED_SCOPES
                  if any(f"{cid.lower()}/{name}" in files for name in PAIRED_FILES)}
        if scopes:
            group["retained_paired_scopes"] = scopes
        groups.append(group)
    return {"schema": "byul-retained-cloud-evidence-inventory/1",
            "rank_source": {"sheet": "전체 범위와 근거", "range": "A31:C62"},
            "ordered_scope": [{"rank": rank, "check_id": cid, "evidence_family": family, "requires": needed}
                              for rank, (cid, family, needed) in enumerate(ORDER, 23)],
            "policy_authority_documents": authority, "archives": groups,
            "live_cloud_access_performed": False, "paired_deployment_approval_verified": False,
            "new_policy_created": False, "new_verdict_created": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, action="append", required=True,
                        help="Existing export directory or tar.gz, read without extraction")
    parser.add_argument("--confirmed-policy", type=Path,
                        help="Exact previously sealed confirmed policy, used only for input validation")
    parser.add_argument("--gateway-inventory", nargs=2, metavar=("PATH", "SHA256"))
    parser.add_argument("--copy-selection", nargs=2, metavar=("PATH", "SHA256"))
    args = parser.parse_args()
    supplied = {cid: (Path(value[0]), value[1]) for cid, value in
                (("REQ-05", args.gateway_inventory), ("PLT-12", args.copy_selection)) if value is not None}
    print(json.dumps(inventory(args.archive, policy_path=args.confirmed_policy, supplied=supplied),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
