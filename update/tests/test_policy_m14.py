"""M14 G01-G20: limited DECLARED REQ-15 contract, not actual cache execution."""
from copy import deepcopy
import csv
import hashlib
import json

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.manual import build_template, import_evidence
from guard.collectors.policy_template import build_policy_template
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.core.select import PolicyValueError, select
from guard.judges import manual_cache_group as cache
from guard.judges.declared import DeclaredJudge
from guard.models.objects import CpuProfile
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack
from tests.builders import PACK_DIR, ROOT, T0, baseline_pack, environment, policy
from tests.prepared_fixtures import manual_setup_seed, put_baseline_snapshot
from tests.req15_builders import AXES, ATTACHMENT, GROUPS, payload, policy_values, unit
from tests.test_policy_m4 import fill, snapshot, write


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def result(check, evd, vals):
    return DeclaredJudge().judge(check, evd, {}, vals)


def test_registration_catalog_empty_template_and_no_new_executor(pack):
    check = pack.checks["REQ-15"]
    assert (pack.manifest.revision, check.revision) == (29, 3) and not pack.reviewed
    assert [(p.key, p.type, p.required) for p in check.policy_params] == [
        (k, t, True) for k, t in cache.POLICIES.items()]
    assert [(o.key, o.type) for o in check.required_observations] == [(cache.OBSERVATION, "dict")]
    assert check.executor is check.judge is None and len(pack.checks) == 77
    assert sum(bool(c.policy_params) for c in pack.active_checks()) == 27
    assert sum(len(c.policy_params) for c in pack.active_checks()) == 74
    assert sum(bool(c.executor) for c in pack.active_checks()) == 16
    assert not pack.checks["REQ-17"].policy_params
    text = build_policy_template(pack)
    doc = yaml.safe_load(text)
    assert doc["values"]["REQ-15"] == {} and doc["scope"] == {"include": [], "exclude": []}
    assert doc["status"] == "DRAFT" and doc["confirmed_by"] is None
    rows = list(csv.DictReader((ROOT / "docs/policy_catalog.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == 347 and sum(len(json.loads(r["check_ids"])) for r in rows) == 639
    for suffix, key in (("PUBLIC-PREFIX", "public_prefix_sharing_allowed"), ("GROUP", "isolated_cache_groups"),
                        ("SALT", "cache_identity_policy_ref")):
        row = next(r for r in rows if r["proposed_policy_id"] == f"P-DRAFT-CACHE-{suffix}")
        assert row["classification"] == "a"
        assert json.loads(row["check_ids"]) == ["REQ-15", "REQ-16", "REQ-17"]
        assert json.loads(row["current_param_paths"]) == [f"values.REQ-15.{key}"]
    assert {k: sum(r["classification"] == k for r in rows) for k in "abcd"} == dict(a=71, b=2, c=161, d=113)


@pytest.mark.parametrize("key,bad", [("public_prefix_sharing_allowed", v) for v in (None, 0, "false", [])]
    + [("isolated_cache_groups", v) for v in (None, False, "EXAMPLE-A", [1])]
    + [("cache_identity_policy_ref", v) for v in (None, False, [])])
def test_g01_select_rejects_wrong_type_even_when_outside_scope(pack, key, bad):
    vals = policy_values(); vals[key] = bad
    pol = policy(pack.ref(), scope={"include": ["SUP-05"]}, values={"REQ-15": vals})
    with pytest.raises(PolicyValueError, match="REQ-15"):
        select(pack, [environment()], pol)


@pytest.mark.parametrize("groups", [GROUPS, tuple(reversed(GROUPS)), (*GROUPS, "EXAMPLE-C")])
def test_complete_ordered_pairs_are_pure_and_policy_order_is_not_rewritten(pack, groups):
    check = pack.checks["REQ-15"]; evd, vals = unit(check, groups=groups)
    before = (check.model_dump(mode="json"), evd.to_dict(), deepcopy(vals))
    got = result(check, evd, vals)
    assert got.verdicts == dict(security="PASS", functional="PASS", performance="UNKNOWN")
    assert result(check, evd, vals) == got
    assert (check.model_dump(mode="json"), evd.to_dict(), vals) == before


SCENARIOS = ("missing_policy", "false_only", "true", "empty_groups", "one_group", "duplicate_group", "blank_group",
    "identity_ref", "foreign_group", "foreign_principal", "principal_reused", "shared_service", "client_salt_only",
    "identity_unknown", "reverse_missing", "forged_missing", "omitted_missing", "derived_source", "consumed_source",
    "key_only", "post_missing", "miss_only", "blocked", "foreign_hash", "blank_locator", "blank_basis", "event_reused",
    "trial_changed", "trial_reused", "consumer_changed", "source_entry_changed", "target_entry_changed", "public",
    "classification_unknown", "uncollected", "missing_observation", "extra_top", "extra_identity", "extra_record",
    "record_duplicate", "unsupported_profile", "unsupported_version", "foreign_case", "unknown_key", "unknown_claim",
    "unknown_effect", "unknown_salt", "unknown_outcome", "unhashable", "empty_identities", "foreign_derived")


def damage(evd, vals, case):
    data = evd.observations[cache.OBSERVATION].value; rows = data["records"]; row = rows[3]
    if case == "missing_policy": vals.pop("public_prefix_sharing_allowed")
    elif case == "false_only": vals.clear(); vals["public_prefix_sharing_allowed"] = False
    elif case == "true": vals["public_prefix_sharing_allowed"] = True
    elif case == "empty_groups": vals["isolated_cache_groups"] = []
    elif case == "one_group": vals["isolated_cache_groups"] = [GROUPS[0]]
    elif case == "duplicate_group": vals["isolated_cache_groups"] = [GROUPS[0], GROUPS[0]]
    elif case == "blank_group": vals["isolated_cache_groups"] = [GROUPS[0], " "]
    elif case == "identity_ref": data["identity_policy_ref"] = "EXAMPLE-other-policy"
    elif case == "foreign_group": row["target_group_id"] = "EXAMPLE-other"
    elif case == "foreign_principal": row["actor_principal_ref"] = "EXAMPLE-other"
    elif case == "principal_reused": data["identities"][1]["principal_ref"] = data["identities"][0]["principal_ref"]
    elif case in ("shared_service", "client_salt_only"):
        data["identities"][0]["credential_kind"] = "shared_service_credential" if case == "shared_service" else case
    elif case == "identity_unknown": data["identities"][0]["verification_status"] = "unknown"
    elif case == "reverse_missing": data["records"] = rows[:6]
    elif case in ("forged_missing", "omitted_missing", "post_missing"):
        rows.remove(next(r for r in rows if r["case"] == {"forged_missing":"forged_salt", "omitted_missing":"omitted_salt", "post_missing":"post_control"}[case]))
    elif case == "derived_source": row["derived_group_id"] = row["source_group_id"]
    elif case == "consumed_source": row["consumed_group_id"] = row["source_group_id"]; row["cache_effect"] = "forbidden_cross_group_reuse"
    elif case == "key_only": row["cache_effect"] = "unknown"
    elif case == "miss_only": rows[1]["cache_effect"] = "unknown"
    elif case == "blocked": row["request_outcome"] = "blocked"
    elif case == "foreign_hash": row["evidence"][0]["sha256"] = "a" * 64
    elif case in ("blank_locator", "blank_basis"): row["evidence"][0][case.removeprefix("blank_")] = " "
    elif case == "event_reused": row["event_id"] = rows[0]["event_id"]
    elif case == "trial_changed": row["trial_id"] = "EXAMPLE-other-trial"
    elif case == "trial_reused":
        for r in rows[6:]: r["trial_id"] = rows[0]["trial_id"]
    elif case == "consumer_changed": row["consumer_ref"] = "EXAMPLE-other-consumer"
    elif case == "source_entry_changed": row["source_entry_ref"] = "EXAMPLE-other-source"
    elif case == "target_entry_changed": row["consumed_entry_ref"] = "EXAMPLE-other-target"
    elif case == "public": data["classification"] = "public"
    elif case == "classification_unknown": data["classification"] = "unknown"
    elif case == "uncollected": evd.observations[cache.OBSERVATION].collected = False
    elif case == "missing_observation": evd.observations.pop(cache.OBSERVATION)
    elif case.startswith("extra_"):
        {"extra_top":data, "extra_identity":data["identities"][0], "extra_record":row}[case]["EXAMPLE-extra"] = True
    elif case == "record_duplicate": rows.append(deepcopy(row))
    elif case == "unsupported_profile": data["profile"] = "EXAMPLE-shared"
    elif case == "unsupported_version": data["version"] = "EXAMPLE-version"
    elif case == "foreign_case": row["case"] = "EXAMPLE-case"
    elif case.startswith("unknown_"):
        row[{"unknown_key":"key_relation", "unknown_claim":"client_group_claim", "unknown_effect":"cache_effect",
             "unknown_salt":"salt_action", "unknown_outcome":"request_outcome"}[case]] = "unknown"
    elif case == "unhashable": row["event_id"] = []
    elif case == "empty_identities": data["identities"] = []
    elif case == "foreign_derived": row["derived_group_id"] = "EXAMPLE-other-group"


@pytest.mark.parametrize("case", SCENARIOS)
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_g02_g16_bad_missing_or_conflicting_contract_never_promotes_or_erases_fail(pack, case, raw):
    check = pack.checks["REQ-15"]; evd, vals = unit(check, raw)
    damage(evd, vals, case); before = (evd.to_dict(), deepcopy(vals))
    got = result(check, evd, vals)
    assert all(v == ("UNKNOWN" if raw == "PASS" else raw) for v in got.verdicts.values())
    assert (evd.to_dict(), vals) == before
    assert any("POLICY_CONTEXT: CACHE_GROUP_" in r for r in got.reasons)


@pytest.mark.parametrize("inference", ["failed", "not_attempted", "unknown"])
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_g17_inference_only_caps_functional_and_performance(pack, inference, raw):
    check = pack.checks["REQ-15"]; evd, vals = unit(check, raw)
    evd.observations[cache.OBSERVATION].value["records"][3]["inference"] = inference
    got = result(check, evd, vals)
    assert got.verdicts["security"] == raw
    assert got.verdicts["functional"] == got.verdicts["performance"] == ("UNKNOWN" if raw == "PASS" else raw)
    assert not any("RECORD_INCOMPLETE" in r or "ENUM_INVALID" in r for r in got.reasons)


@pytest.mark.parametrize("kind", ["legacy", "policy_partial", "observation_partial", "wrong_type", "optional", "other"])
def test_g18_old_definitions_are_legacy_and_partial_markers_are_blocked(pack, kind):
    check = pack.checks["REQ-15"].model_copy(deep=True)
    if kind == "legacy": check.policy_params = []; check.required_observations = []
    elif kind == "policy_partial": check.policy_params.pop()
    elif kind == "observation_partial": check.required_observations = []
    elif kind == "wrong_type": check.required_observations[0].type = "str"
    elif kind == "optional": check.policy_params[0].required = False
    else: check = pack.checks["SUP-05"]
    evd, vals = unit(check)
    got = result(check, evd, vals)
    assert all(v == ("PASS" if kind in ("legacy", "other") else "UNKNOWN") for v in got.verdicts.values())


@pytest.mark.parametrize("axis", AXES)
def test_dependency_errors_no_io_and_sensitive_payload_do_not_erase_independent_fail(pack, monkeypatch, axis):
    check = pack.checks["REQ-15"]; evd, vals = unit(check)
    evd.observations["declared_verdicts"].value[axis] = "FAIL"
    def forbidden(*args, **kwargs): raise RuntimeError("EXAMPLE-credential-token-sensitive")
    monkeypatch.setattr("builtins.open", forbidden); monkeypatch.setattr("socket.socket", forbidden)
    assert result(check, evd, vals).verdicts[axis] == "FAIL"
    monkeypatch.setattr(cache, "evaluate", forbidden)
    got = result(check, evd, vals)
    assert got.verdicts[axis] == "FAIL" and "EXAMPLE-credential" not in " ".join(got.reasons)


@pytest.mark.parametrize("field", list(cache.ENUMS))
@pytest.mark.parametrize("bad", [None, False, [], 0, "", " ", " unknown", "EXAMPLE-invalid-enum"])
def test_enum_shape_and_unsupported_values_are_distinct_without_echo(pack, field, bad):
    check = pack.checks["REQ-15"]; evd, vals = unit(check)
    evd.observations[cache.OBSERVATION].value["records"][3][field] = bad
    got = result(check, evd, vals)
    assert all(v == "UNKNOWN" for v in got.verdicts.values())
    code = "ENUM_INVALID" if bad == "EXAMPLE-invalid-enum" else "RECORD_INCOMPLETE"
    assert any(code in r for r in got.reasons) and "EXAMPLE-invalid-enum" not in " ".join(got.reasons)


@pytest.mark.parametrize("field", ["identity_policy_ref", "entrypoint_ref", "target_scope", "workload_ref",
    "principal_ref", "identity_source", "binding_basis", "trial_id", "event_id", "consumer_ref",
    "source_entry_ref", "consumed_entry_ref", "observation_detail", "locator", "basis"])
def test_unknown_identifiers_and_basis_are_not_complete_evidence(pack, field):
    check = pack.checks["REQ-15"]; evd, vals = unit(check)
    data = evd.observations[cache.OBSERVATION].value
    if field in ("identity_policy_ref", "entrypoint_ref", "target_scope", "workload_ref"):
        data[field] = "unknown"
    elif field in ("principal_ref", "identity_source", "binding_basis"):
        data["identities"][0][field] = "unknown"
    elif field in ("locator", "basis"):
        data["records"][3]["evidence"][0][field] = "unknown"
    else:
        data["records"][3][field] = "unknown"
    assert all(v == "UNKNOWN" for v in result(check, evd, vals).verdicts.values())


@pytest.mark.parametrize("gate", ["normal", "challenge", "attachment", "notes", "cpu"])
def test_g20_existing_core_pass_gates_and_cpu_performance_remain(pack, gate):
    check = pack.checks["REQ-15"]; evd, vals = unit(check)
    if gate == "normal": evd.normal_control.succeeded = False
    elif gate == "challenge": evd.challenges[0].attempted = False
    elif gate == "attachment": evd.artifacts = []
    elif gate == "notes": evd.observations["declared_observation_notes"].value = {}
    else: evd.comparability.cpu = CpuProfile(architecture="EXAMPLE-x86", model="EXAMPLE-cpu", system="EXAMPLE-linux", kernel="EXAMPLE-kernel", threads=1)
    got = judge_evidence(check, evd.seal(), policy(pack.ref(), values={"REQ-15": vals}), T0)
    assert got.verdicts.security == got.verdicts.functional == ("PASS" if gate == "cpu" else "UNKNOWN")
    assert got.verdicts.performance == "UNKNOWN"
    if gate == "cpu":
        evd.observations["declared_verdicts"].value = dict.fromkeys(AXES, "FAIL")
        got = judge_evidence(check, evd.seal(), policy(pack.ref(), values={"REQ-15": vals}), T0)
        assert got.verdicts.security == got.verdicts.functional == "FAIL" and got.verdicts.performance == "UNKNOWN"


def setup(tmp_path, pack, mode="audit", data=ATTACHMENT):
    envs = [environment()]
    if mode == "update": envs.append(environment(id="ENV-CANDIDATE"))
    pol = policy(pack.ref(), scope={"include": ["REQ-15"]}, values={"REQ-15": policy_values()})
    def create(folder):
        st = Store(folder / "store")
        st.put_many([*envs, pol]); put_baseline_snapshot(st)
        plan = select(pack, envs, pol, mode=mode); st.put(plan)
        doc = fill(build_template(st, pack, plan))
        return st, plan, doc

    st, plan, doc = manual_setup_seed("m14", {
        "pack": pack.model_dump(mode="json"), "environments": [e.to_dict() for e in envs],
        "policy": pol.to_dict(), "mode": mode, "attachment": data.hex(),
    }, tmp_path, create)
    assert plan.item("REQ-15").gaps == ["NO_EXECUTOR"]
    digest = hashlib.sha256(data).hexdigest()
    for entry in doc["checks"]:
        assert entry["required_observations"][0]["value"] is None
        entry["required_observations"][0].update(value=payload(digest=digest), collected=True)
    return st, plan, doc


@pytest.mark.parametrize("version,mode", [(1, "audit"), (2, "audit"), (3, "audit"), (3, "update")])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_g19_actual_import_sides_exact_bytes_refs_rejudge_and_report(tmp_path, pack, version, mode, newline):
    data = b"EXAMPLE cache trace one" + newline + b"EXAMPLE trace two" + newline
    st, plan, doc = setup(tmp_path, pack, mode, data)
    doc["template"] = f"guard-manual-evidence/{version}"
    if version == 1: doc.pop("policy"); doc.pop("pack")
    path = write(tmp_path, doc); (tmp_path / "EXAMPLE.txt").write_bytes(data)
    import_evidence(st, pack, path, now=T0)
    original = snapshot(st); first = judge_plan(st, pack, plan, T0)
    assert all(r.verdicts.security == r.verdicts.functional == "PASS" and r.verdicts.performance == "UNKNOWN" for r in first)
    evds = list(st.iter_kind("EVD"))
    assert all(e.artifacts[0].sha256 == hashlib.sha256(data).hexdigest() and e.artifacts[0].bytes == len(data) for e in evds)
    after = snapshot(st); judge_plan(st, pack, plan, T0); assert snapshot(st) == after
    report = build_report(st, pack, plan, T0); text = json.dumps(report.to_dict(), ensure_ascii=False)
    assert not report.official and "CACHE_GROUP_DECLARED_ONLY" in text and "REQ17" in text
    assert next(r for r in report.by_check if r.check_id == "REQ-15").evidence_source == "manual"
    for obj in original.values(): assert st.get(obj["id"], obj["revision"]).to_dict() == obj
    side = "candidate" if mode == "update" else "single"
    entry = next(e for e in doc["checks"] if e["side"] == side)
    entry["required_observations"][0]["value"]["records"][3]["inference"] = "unknown"
    previous = next(e for e in evds if e.side == side)
    path = write(tmp_path, doc); (tmp_path / "EXAMPLE.txt").write_bytes(data)
    import_evidence(st, pack, path, now=T0)
    current = next(e for e in st.find_latest("EVD", lambda e: e.plan == plan.ref()) if e.side == side)
    assert current.ref() != previous.ref() and current.execution.executor.config_sha256 == previous.execution.executor.config_sha256
    got = judge_plan(st, pack, plan, T0)
    assert all(r.verdicts.security == "PASS" and r.verdicts.functional == ("UNKNOWN" if r.side == side else "PASS") for r in got)
    assert not st.verify() and st.resolve(previous.ref()).to_dict() == previous.to_dict()


def test_cli_saved_pack_bundle_restore_judge_report_and_original_preservation(tmp_path, pack):
    st, plan, doc = setup(tmp_path, pack)
    path = write(tmp_path, doc); (tmp_path / "EXAMPLE.txt").write_bytes(ATTACHMENT)
    cli = CliRunner()
    def ok(args):
        got = cli.invoke(app, args)
        assert got.exit_code == 0, got.output + repr(got.exception)
    ok(["evidence", "import", str(path), "--store", str(st.root), "--pack", str(PACK_DIR)])
    ok(["judge", "--plan", plan.id, "--store", str(st.root), "--as-of", T0.isoformat()])
    before = {p.relative_to(st.root): p.read_bytes() for p in st.root.rglob("*") if p.is_file()}
    export_bundle(st.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored"); saved = get_pack(restored, plan.inputs.pack)
    assert saved.ref() == pack.ref()
    ok(["judge", "--plan", plan.id, "--store", str(restored.root), "--as-of", T0.isoformat()])
    ok(["report", "--plan", plan.id, "--store", str(restored.root), "--json", str(tmp_path / "report.json"),
        "--as-of", T0.isoformat()])
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert not report["official"] and "CACHE_GROUP_DECLARED_ONLY" in (tmp_path / "report.json").read_text(encoding="utf-8")
    assert all(r.verdicts.security == r.verdicts.functional == "PASS" for r in restored.iter_kind("RES"))
    assert {p.relative_to(st.root): p.read_bytes() for p in st.root.rglob("*") if p.is_file()} == before
