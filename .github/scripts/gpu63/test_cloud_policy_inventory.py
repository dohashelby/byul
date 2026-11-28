"""Verify the archive links and rank ordering without a cloud account or a live target."""

from hashlib import sha256
from io import BytesIO
import json
import os
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from guard.models import Evidence, Plan, Report, Result, canonical_json
from tests.builders import T0, environment, policy

import cloud_policy_inventory as inventory


def replace_ref(refs, old, new):
    return [new if item == old else item for item in refs]


def revised_test_bundle(raw, *, report, transform):
    """Mutate only temporary test copies and re-seal the affected object links."""
    with ZipFile(BytesIO(raw)) as bundle:
        file_bytes = {name: bundle.read(name) for name in bundle.namelist() if name != "manifest.json"}
    manifest = {}
    with sqlite3.connect(":memory:") as source, sqlite3.connect(":memory:") as database:
        source.deserialize(file_bytes["store.sqlite"])
        source.execute("PRAGMA query_only = ON")
        database.execute("CREATE TABLE objects (id, revision, kind, body)")
        database.executemany("INSERT INTO objects VALUES (?,?,?,?)",
                             source.execute("SELECT id, revision, kind, body FROM objects"))
        new_report = transform(database, report)
        database.execute("UPDATE objects SET body=? WHERE id=? AND revision=? AND kind='RPT'",
                         (canonical_json(new_report.to_dict()).decode(), report.id, report.revision))
        database.commit()
        file_bytes["store.sqlite"] = database.serialize()
    data = BytesIO()
    with ZipFile(data, "w", compression=ZIP_DEFLATED) as bundle:
        for name, content in file_bytes.items():
            manifest[name] = {"bytes": len(content), "sha256": sha256(content).hexdigest()}
            bundle.writestr(name, content)
        bundle.writestr("manifest.json", canonical_json({"format": "guard-store-bundle/1", "files": manifest}))
    return {"req-02/guard-report.json": canonical_json(new_report.to_dict()),
            "req-02/guard-evidence.zip": data.getvalue()}


def revise_candidate(database, report, *, foreign_attempt=False, foreign_policy=False):
    def get(reference, cls):
        row, = database.execute("SELECT body FROM objects WHERE id=? AND revision=? AND kind=?",
                                (reference.id, reference.revision, reference.kind)).fetchone()
        return cls.model_validate(json.loads(row))

    def save(obj):
        database.execute("UPDATE objects SET body=? WHERE id=? AND revision=? AND kind=?",
                         (canonical_json(obj.to_dict()).decode(), obj.id, obj.revision, obj.kind))

    selected = next(row for row in report.by_check if row.check_id == "REQ-02")
    result = get(selected.candidate.result, Result)
    evidence = get(result.evidence, Evidence)
    old_evidence, old_result = evidence.ref(), result.ref()
    if foreign_attempt:
        execution = evidence.execution.model_copy(update={"attempt_id": "ATTEMPT-FOREIGN"})
        evidence = evidence.model_copy(update={"execution": execution}).seal()
        save(evidence)
    result_refs = replace_ref(result.refs, old_evidence, evidence.ref())
    if foreign_policy:
        policy_ref, = [link for link in result_refs if link.kind == "POL"]
        result_refs = replace_ref(result_refs, policy_ref, policy_ref.model_copy(
            update={"revision": policy_ref.revision + 1}))
    result = result.model_copy(update={"evidence": evidence.ref(), "refs": result_refs}).seal()
    save(result)
    candidate = selected.candidate.model_copy(update={"result": result.ref()})
    changed_row = selected.model_copy(update={"candidate": candidate})
    return report.model_copy(update={
        "refs": replace_ref(replace_ref(report.refs, old_evidence, evidence.ref()), old_result, result.ref()),
        "inputs_manifest": replace_ref(replace_ref(report.inputs_manifest, old_evidence, evidence.ref()),
                                       old_result, result.ref()),
        "by_check": replace_ref(report.by_check, selected, changed_row),
    }).seal()


class InventoryTests(unittest.TestCase):
    def test_scope_order_and_absent_runtime_inputs_stay_unverified(self):
        with tempfile.TemporaryDirectory() as temp:
            result = inventory.inventory([Path(temp)])
        self.assertEqual([row["rank"] for row in result["ordered_scope"]], list(range(23, 55)))
        self.assertEqual([row["check_id"] for row in result["ordered_scope"][:4]],
                         ["REQ-05", "PLT-12", "PLT-02", "PLT-06"])
        self.assertEqual([row["check_id"] for row in result["ordered_scope"][-2:]], ["PLT-09", "SUP-13"])
        audited = result["archives"][0]["runtime_policy_inputs"]
        self.assertTrue(all(not row["input_bound"] and row["runtime_status"] == "UNVERIFIED"
                            for row in audited.values()))
        self.assertFalse(result["new_policy_created"])

    def test_tar_reads_fixed_records_and_rejects_linked_record(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / "export.tgz"
            known = "export/capture/baseline/environment.json"
            with tarfile.open(path, "w:gz") as archive:
                meta = tarfile.TarInfo(known)
                meta.size = 2
                archive.addfile(meta, BytesIO(b"{}"))
                meta = tarfile.TarInfo("export/arbitrary/token.txt")
                meta.size = 1
                archive.addfile(meta, BytesIO(b"x"))
            self.assertEqual(inventory.extract_known(path), {"capture/baseline/environment.json": b"{}"})
            self.assertEqual(list(directory.iterdir()), [path])
            with tarfile.open(path, "w:gz") as archive:
                meta = tarfile.TarInfo(known)
                meta.type, meta.linkname = tarfile.SYMTYPE, "arbitrary/token.txt"
                archive.addfile(meta)
            with self.assertRaisesRegex(ValueError, "EXPORT_MEMBER_INVALID"):
                inventory.extract_known(path)

    def test_nested_pair_is_scoped_and_incomplete_pair_preserves_missing_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "export.tgz"
            with tarfile.open(path, "w:gz") as archive:
                for name in ("export/req-02/guard-report.json", "export/req-05/guard-report.json"):
                    meta = tarfile.TarInfo(name)
                    meta.size = 2
                    archive.addfile(meta, BytesIO(b"{}"))
            archived = inventory.inventory([path])["archives"][0]
        self.assertEqual(set(archived["retained_paired_scopes"]), {"REQ-02"})
        self.assertEqual(archived["retained_paired_scopes"]["REQ-02"]["error"],
                         "PAIRED_REPORT_OR_EVIDENCE_MISSING")
        self.assertFalse(archived["runtime_policy_inputs"]["REQ-05"]["input_bound"])

    def test_zip_uncompressed_budget_counts_all_members_before_reading_manifest(self):
        data = BytesIO()
        with ZipFile(data, "w", compression=ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", "{}")
            bundle.writestr("one", b"a" * 80)
            bundle.writestr("two", b"b" * 80)
        with patch.object(inventory, "ARCHIVE_BOUND", 100):
            with self.assertRaisesRegex(ValueError, "EVIDENCE_ARCHIVE_BOUND"):
                with inventory.bound_store(data.getvalue()):
                    self.fail("archive exceeds the expanded byte limit")

    @unittest.skipUnless(os.environ.get("BYUL_RETAINED_PAIRED_ARCHIVE"), "original archive not supplied")
    def test_retained_pair_resolves_original_rows_and_rejects_cross_session_bundle(self):
        source = Path(os.environ["BYUL_RETAINED_PAIRED_ARCHIVE"])
        self.assertEqual(sha256(source.read_bytes()).hexdigest(),
                         "4fafa923a55adf7f1f91b56b1d80986546693cb2dbab5a57a31a1c5cc89dadc8")
        result = inventory.inventory([source])["archives"][0]
        scopes = result["retained_paired_scopes"]
        self.assertEqual(set(scopes), {"REQ-02", "PLT-12"})
        for scope in scopes.values():
            self.assertTrue(scope["report_bundle_verified"])
            self.assertFalse(scope["execution_attempt_anchored_in_report"])
            self.assertTrue(scope["shared_execution_attempt_id"])
            self.assertFalse(scope["official"])
            self.assertFalse(scope["deployment_target_verified"])
            self.assertEqual(scope["policy_status"], "DRAFT")
            self.assertEqual(scope["sides"]["baseline"]["version"], "0.30.0")
            self.assertEqual(scope["sides"]["candidate"]["version"], "0.31.0")
            for side in scope["sides"].values():
                self.assertEqual(side["row"]["execution_status"], "COMPLETED")
                self.assertEqual(side["row"]["security"], "UNKNOWN")
        for route in scopes["REQ-02"]["sides"].values():
            self.assertEqual(route["row"]["data_origin"], "EXAMPLE")
            self.assertEqual(route["row"]["retained_observation_counts"],
                             {"route_events": 3, "native_route_events": 14, "native_route_associations": 3})
            self.assertFalse(route["row"]["retained_observation_flags"]["route_producer_authenticated"])
            self.assertFalse(route["row"]["retained_observation_flags"]["native_route_authorization_verified"])
        for copy in scopes["PLT-12"]["sides"].values():
            self.assertEqual(copy["row"]["data_origin"], "DECLARED")
            self.assertEqual(copy["row"]["retained_observation_counts"]["copy_inventory_declarations"], 3)
            self.assertFalse(copy["row"]["retained_observation_flags"]["copy_treatment_verified"])
            self.assertFalse(copy["row"]["retained_observation_flags"]["copy_reuse_enforcer_verified"])
        self.assertNotEqual(scopes["REQ-02"]["policy_ref"], scopes["PLT-12"]["policy_ref"])
        self.assertNotEqual(scopes["REQ-02"]["shared_execution_attempt_id"],
                            scopes["PLT-12"]["shared_execution_attempt_id"])
        self.assertTrue(all(not row["input_bound"] for row in result["runtime_policy_inputs"].values()))
        files = inventory.extract_known(source)
        files["req-02/guard-evidence.zip"] = files["plt-12/guard-evidence.zip"]
        unbound = inventory.inspect_paired_scope(files, "REQ-02")
        self.assertEqual(unbound["error"], "EVIDENCE_REF_MISSING")
        self.assertEqual(unbound["sides"], {})
        self.assertFalse(unbound["report_bundle_verified"])

    @unittest.skipUnless(os.environ.get("BYUL_RETAINED_PAIRED_ARCHIVE"), "original archive not supplied")
    def test_nested_pair_rejects_duplicate_row_attempt_and_judging_policy(self):
        files = inventory.extract_known(Path(os.environ["BYUL_RETAINED_PAIRED_ARCHIVE"]))
        report = inventory.checked_obj(files["req-02/guard-report.json"], Report)
        raw = files["req-02/guard-evidence.zip"]
        selected = next(row for row in report.by_check if row.check_id == "REQ-02")
        duplicate = selected.model_copy(update={"selected": False})
        mutations = {
            "PAIRED_REPORT_SCOPE_MISMATCH": lambda _database, original: original.model_copy(
                update={"by_check": [*original.by_check, duplicate]}).seal(),
            "PAIRED_EXECUTION_ATTEMPT_MISMATCH": lambda database, original: revise_candidate(
                database, original, foreign_attempt=True),
            "PAIRED_RESULT_POLICY_MISMATCH": lambda database, original: revise_candidate(
                database, original, foreign_policy=True),
        }
        for error, transform in mutations.items():
            with self.subTest(error=error):
                modified = revised_test_bundle(raw, report=report, transform=transform)
                result = inventory.inspect_paired_scope(modified, "REQ-02")
                self.assertEqual(result["error"], error)
                self.assertEqual(result["sides"], {})

    def test_paired_export_reuses_the_bundle_but_keeps_side_metadata_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for side, contents in (("baseline", b"old"), ("candidate", b"new")):
                (directory / f"{side}-environment.json").write_bytes(contents)
            (directory / "evidence.zip").write_bytes(b"shared")
            exported = inventory.extract_known(directory)
            self.assertEqual(exported["capture/baseline/environment.json"], b"old")
            self.assertEqual(exported["capture/candidate/environment.json"], b"new")
            self.assertEqual({exported[f"capture/{side}/evidence.zip"] for side in inventory.SIDES}, {b"shared"})

    def test_original_evidence_and_result_refs_and_artifact_bytes_are_verified(self):
        env = environment()
        pol = policy()
        plan = Plan(id="PLAN-EXAMPLE", created_at=T0, data_origin="EXAMPLE", mode="audit",
                    inputs={"pack": pol.pack, "environments": [env.ref()], "policy": pol.ref()},
                    inventory_complete={}, items=[]).seal()
        payload = b"fixture artifact bytes"
        digest = sha256(payload).hexdigest()
        evidence = Evidence(id="EVD-EXAMPLE", created_at=T0, data_origin="EXAMPLE", environment=env.ref(),
                            plan=plan.ref(), check_id="REQ-05", side="single",
                            execution={"status": "NOT_RUN", "abort_reason": "NO_POLICY_VALUE: private detail"},
                            normal_control={"attempted": False},
                            artifacts=[{"name": "metadata", "path": "path", "sha256": digest,
                                        "bytes": len(payload), "collected_at": T0, "collector": "fixture"}]).seal()
        result = Result(id="RES-EXAMPLE", created_at=T0, data_origin="EXAMPLE", evidence=evidence.ref(),
                        check_id="REQ-05", side="single",
                        judge={"name": "fixture", "version": "1", "params_sha256": "0" * 64},
                        verdicts={"security": "FAIL", "functional": "UNKNOWN", "performance": "UNKNOWN"}).seal()
        report = Report(id="RPT-EXAMPLE", created_at=T0, data_origin="EXAMPLE", mode="audit", official=False,
                        inputs_manifest=[], pack_review_status="DRAFT", policy_status="DRAFT", by_purpose={},
                        by_area={}, refs=[result.ref(), evidence.ref(), env.ref(), pol.ref(), plan.ref()],
                        by_check=[{"check_id": "REQ-05", "purpose": "fixture", "area": "REQ", "question": "fixture",
                                   "review_order": "reference", "applicability": "APPLICABLE", "selected": True,
                                   "single": {"security": "FAIL", "evidence_ref": evidence.ref(), "result": result.ref(),
                                              "execution_status": "NOT_RUN"}}]).seal()

        def evidence_zip(artifact):
            with sqlite3.connect(":memory:") as database:
                database.execute("CREATE TABLE objects (id, revision, kind, body)")
                for obj in (evidence, result):
                    database.execute("INSERT INTO objects VALUES (?,?,?,?)",
                                     (obj.id, obj.revision, obj.kind, canonical_json(obj.to_dict()).decode()))
                file_bytes = {"store.sqlite": database.serialize(), f"artifacts/{digest}": artifact}
            manifest = {"format": "guard-store-bundle/1",
                        "files": {key: {"bytes": len(value), "sha256": sha256(value).hexdigest()}
                                  for key, value in file_bytes.items()}}
            data = BytesIO()
            with ZipFile(data, "w") as archive:
                archive.writestr("manifest.json", canonical_json(manifest))
                for name, content in file_bytes.items():
                    archive.writestr(name, content)
            return data.getvalue()

        verified = inventory.bound_objects(evidence_zip(payload), report, plan, env)["REQ-05"]
        self.assertEqual(verified["abort_code"], "NO_POLICY_VALUE")
        self.assertEqual(verified["artifacts"][0]["sha256"], digest)
        self.assertNotIn("private detail", str(verified))
        corrupted = inventory.bound_objects(evidence_zip(b"changed but catalogued"), report, plan, env)["REQ-05"]
        self.assertIn("error", corrupted)
