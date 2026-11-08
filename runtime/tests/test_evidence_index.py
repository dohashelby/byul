"""Offline evidence import: hostile archives, provenance, redaction, atomicity."""
from contextlib import redirect_stderr, redirect_stdout
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools import evidence_index as ev


class EvidenceIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.output = self.root / "output"
        self.path = self.root / "capture.tgz"

    def archive(self, rows=None):
        with tarfile.open(self.path, "w:gz") as archive:
            for name, content, kind in rows or [("ev/log.txt", b"measurement\n", tarfile.REGTYPE)]:
                member = tarfile.TarInfo(name)
                member.type = kind
                member.mtime = 1794142800
                if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                    member.linkname = "../../outside"
                member.size = len(content) if kind == tarfile.REGTYPE else 0
                archive.addfile(member, io.BytesIO(content))
        return ev.Input("lab1", self.path)

    def generate(self, item=None, **kwargs):
        return ev.generate([item or self.archive()], self.output, **kwargs)

    def index(self):
        return json.loads((self.output / "evidence-index.json").read_text())

    def test_content_metadata_unknown_origin_and_idempotence(self):
        item = self.archive()
        original = self.path.read_bytes()
        report = self.generate(item)
        entry = self.index()["items"][0]
        self.assertEqual(entry["data_origin"], "DECLARED")
        self.assertEqual(entry["path"], "lab1/ev/log.txt")
        self.assertEqual(entry["bytes"], 12)
        self.assertEqual(entry["sha256"], hashlib.sha256(b"measurement\n").hexdigest())
        self.assertEqual(entry["media_type"], "text/plain")
        self.assertEqual(entry["collector"], {"name": "runtime-evidence-index", "version": "1.1"})
        self.assertEqual(entry["collected_at"], ev.timestamp(1794142800))
        self.assertIsNone(entry["window"])
        self.assertFalse(report["runs"][0]["hash_verified"])
        self.assertEqual(report["runs"][0]["basis"], ["none"])
        self.assertIsNone(report["runs"][0]["origin_assertion"])
        self.assertEqual(report["runs"][0]["files"], 1)
        files = {str(p.relative_to(self.output)): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob("*") if p.is_file()}
        self.assertEqual(self.generate(item), report)
        self.assertEqual(files, {str(p.relative_to(self.output)): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.output.rglob("*") if p.is_file()})
        self.assertEqual(self.path.read_bytes(), original)

    def test_document_hash_measured_window_and_refs(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.root / "result.md"
        doc.write_text(f"# AWS GPU dev 서버 1회차 결과 (2026-11-08)\n- 환경·시간: **23:55:00 KST 기동 요청 → 00:20:00 stopped 확인**\n- 증거: `capture.tgz`(SHA-256 `{digest}`).\n")
        self.generate(ev.Input("lab1", self.path, result_doc=doc))
        entry = self.index()["items"][0]
        self.assertEqual(entry["data_origin"], "MEASURED")
        self.assertEqual(entry["window"], {"from": "2026-11-08T23:55:00+09:00", "until": "2026-11-09T00:20:00+09:00"})
        self.assertEqual(entry["source"], "aws-lab lab1")
        self.assertEqual(entry["refs"], [f"archive-sha256:{digest}", "result-document-sha256:" + hashlib.sha256(doc.read_bytes()).hexdigest()])

    def test_hash_mismatch_publishes_nothing(self):
        self.archive()
        with self.assertRaisesRegex(ev.ImportFailure, "SHA256_MISMATCH"):
            self.generate(ev.Input("lab1", self.path, sha256="0" * 64))
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".evidence-*")), [])

    def test_document_and_explicit_hash_must_agree(self):
        self.archive()
        doc = self.root / "result.md"
        doc.write_text("capture.tgz SHA-256 " + "a" * 64)
        with self.assertRaisesRegex(ev.ImportFailure, "SHA256_MISMATCH"):
            self.generate(ev.Input("lab1", self.path, sha256="b" * 64, result_doc=doc))

    def test_synthetic_overrides_measured_assertion(self):
        self.archive([("log.json", b'{"data_origin":"synthetic"}', tarfile.REGTYPE)])
        self.generate(ev.Input("lab1", self.path, sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(), origin="MEASURED"))
        self.assertEqual(self.index()["items"][0]["data_origin"], "EXAMPLE")

    def result_doc(self, body):
        path = self.root / "result.md"
        path.write_text("# AWS GPU dev 서버 1회차 결과 (2026-11-08)\n" + body)
        return path

    def test_full_hash_on_separate_line_with_other_digest(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"입력 capture.tgz\n기타 SHA-256 {'a' * 64}\n\n반출 SHA-256\n`{digest}`\n")
        report = self.generate(ev.Input("lab1", self.path, result_doc=doc))["runs"][0]
        self.assertIs(report["hash_verified"], True)
        self.assertEqual(report["basis"], ["document_full"])
        self.assertEqual(report["origins"], {"MEASURED": 1})

    def test_run_heading_confirms_document_without_archive_name(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"별도 줄\n{digest}\n")
        report = self.generate(ev.Input("run1", self.path, result_doc=doc))["runs"][0]
        self.assertEqual(report["basis"], ["document_full"])

    def test_unrelated_document_cannot_supply_full_hash(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        for body in (digest, f"capture.tgz\n{digest}"):
            doc = self.result_doc(body)
            with self.subTest(archive_mentioned="capture.tgz" in body), self.assertRaisesRegex(ev.ImportFailure, "DOCUMENT_RUN_MISMATCH"):
                self.generate(ev.Input("run2", self.path, result_doc=doc))
        self.assertFalse(self.output.exists())

    def test_prefix_only_is_weak_and_not_measured(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"묶음 SHA-256 {'a' * 8}\n증거 capture.tgz (SHA-256 `{digest[:8]}`)\n")
        item = ev.Input("lab1", self.path, result_doc=doc)
        report = self.generate(item)["runs"][0]
        self.assertEqual(report["hash_verified"], "prefix_only")
        self.assertEqual(report["basis"], ["document_prefix"])
        self.assertEqual(report["origins"], {"DECLARED": 1})
        self.assertIsNone(report["origin_assertion"])
        self.assertEqual(self.generate(item)["runs"][0], report)

    def test_prefix_on_continuation_line(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"capture.tgz\nSHA-256: `{digest[:8]}…`\n")
        report = self.generate(ev.Input("lab1", self.path, result_doc=doc))["runs"][0]
        self.assertEqual(report["basis"], ["document_prefix"])

    def test_prefix_mismatch_fails_even_with_correct_explicit_hash(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"capture.tgz SHA-256 {'0' * 8}")
        for explicit in (None, digest):
            with self.subTest(explicit=explicit is not None), self.assertRaisesRegex(ev.ImportFailure, "SHA256_MISMATCH"):
                self.generate(ev.Input("lab1", self.path, sha256=explicit, result_doc=doc))
        self.assertFalse(self.output.exists())

    def test_other_full_hash_does_not_fall_back_to_matching_prefix(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"capture.tgz SHA-256 {digest[:8]}\nother {'a' * 64}\n")
        with self.assertRaisesRegex(ev.ImportFailure, "SHA256_MISMATCH"):
            self.generate(ev.Input("lab1", self.path, result_doc=doc))

    def test_explicit_hash_handles_document_without_hash(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc("capture.tgz 해시 미기재\n")
        report = self.generate(ev.Input("lab1", self.path, sha256=digest, result_doc=doc))["runs"][0]
        self.assertIs(report["hash_verified"], True)
        self.assertEqual(report["basis"], ["explicit"])
        self.assertEqual(report["origins"], {"DECLARED": 1})

    def test_explicit_hash_and_origin_record_human_assertion(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"capture.tgz SHA-256 {digest[:8]}\n")
        report = self.generate(ev.Input("lab1", self.path, sha256=digest, result_doc=doc, origin="MEASURED"))["runs"][0]
        self.assertIs(report["hash_verified"], True)
        self.assertEqual(report["basis"], ["document_prefix", "explicit"])
        self.assertEqual(report["origin_assertion"], "MEASURED")
        self.assertEqual(report["origins"], {"MEASURED": 1})

    def test_prefix_cannot_upgrade_origin_without_full_hash(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"capture.tgz SHA-256 {digest[:8]}")
        with self.assertRaisesRegex(ev.ImportFailure, "ORIGIN_REQUIRES_FULL_HASH"):
            self.generate(ev.Input("lab1", self.path, result_doc=doc, origin="MEASURED"))
        self.assertFalse(self.output.exists())

    def test_explicit_and_document_full_are_both_checked(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc(f"capture.tgz\nSHA-256 {digest}\n")
        report = self.generate(ev.Input("lab1", self.path, sha256=digest, result_doc=doc))["runs"][0]
        self.assertEqual(report["basis"], ["document_full", "explicit"])
        for explicit, documented in (("a" * 64, digest), (digest, "a" * 64)):
            doc = self.result_doc(f"capture.tgz\nSHA-256 {documented}\n")
            with self.subTest(explicit_matches=explicit == digest), self.assertRaisesRegex(ev.ImportFailure, "SHA256_MISMATCH"):
                ev.generate([ev.Input("lab1", self.path, sha256=explicit, result_doc=doc)], self.root / "other")

    def test_no_hash_document_still_requires_explicit_evidence(self):
        self.archive()
        doc = self.result_doc("capture.tgz 해시 미기재")
        with self.assertRaisesRegex(ev.ImportFailure, "DOCUMENT_HASH_MISSING"):
            self.generate(ev.Input("lab1", self.path, result_doc=doc))

    def test_cli_explicit_hash_bypasses_missing_document_hash(self):
        self.archive()
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        doc = self.result_doc("capture.tgz 해시 미기재")
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = ev.main(["--input", f"lab1={self.path}", "--output", str(self.output),
                            "--result-doc", f"lab1={doc}", "--sha256", f"lab1={digest}", "--origin", "lab1=MEASURED"])
        self.assertEqual(code, 0)
        report = json.loads(stdout.getvalue())["runs"][0]
        self.assertEqual(report["basis"], ["explicit"])
        self.assertEqual(report["origin_assertion"], "MEASURED")
        self.assertEqual(stderr.getvalue(), "")

    def test_secret_files_excluded_no_values_or_names_reported(self):
        samples = {
            "BEARER": b"Authorization: Bearer test-private-value",
            "PRIVATE_KEY": b"-----BEGIN RSA PRIVATE KEY-----\nprivate\n-----END RSA PRIVATE KEY-----",
            "AWS_ACCOUNT": b"account=123456789012",
            "INSTANCE_ID": b"instance i-0123456789abcdef0",
            "ACCESS_KEY": b"AKIA" + b"A" * 16,
            "TOKEN": b'{"viewer_token": "do-not-include-this"}',
            "TOKEN_ARGUMENT": b'curl --token cli-do-not-include-this',
            "JWT": b"eyJabc.eyJdef.signature",
            "SERVICE_TOKEN": b"ghp_" + b"a" * 24,
        }
        rows = [(f"private{n}.txt", value, tarfile.REGTYPE) for n, value in enumerate(samples.values())]
        rows.append(("clean.txt", b'{"token":null,"other":"redacted"}', tarfile.REGTYPE))
        self.archive(rows)
        report = self.generate(ev.Input("lab1", self.path))
        self.assertEqual(report["runs"][0]["excluded"], len(samples))
        self.assertEqual(report["runs"][0]["files"], 1)
        self.assertEqual(set(report["runs"][0]["reason_codes"]), set(samples))
        report_bytes = (self.output / "import-report.json").read_bytes()
        for value in samples.values():
            self.assertNotIn(value, report_bytes)
        self.assertNotIn(b"private0", report_bytes)
        self.assertEqual(len(list((self.output / "files").rglob("*.txt"))), 1)

    def test_secret_filename_excluded(self):
        self.archive([("i-0123456789abcdef0.txt", b"clean", tarfile.REGTYPE)])
        report = self.generate(ev.Input("lab1", self.path))
        self.assertEqual(report["runs"][0]["reason_codes"], {"INSTANCE_ID": 1})
        self.assertEqual(self.index()["items"], [])

    def test_unsafe_archive_members_rejected(self):
        cases = [(name, tarfile.REGTYPE) for name in ("../escape", "/absolute", "a/../../escape", "a//b", "C:\\escape")]
        cases += [("link", kind) for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.GNUTYPE_SPARSE)]
        for name, kind in cases:
            with self.subTest(kind=kind, name=name):
                self.archive([(name, b"x", kind)])
                with self.assertRaises((ev.ImportFailure, tarfile.TarError)):
                    self.generate(ev.Input("lab1", self.path))
                self.assertFalse(self.output.exists())

    def test_duplicate_member_rejected(self):
        self.archive([("a", b"a", tarfile.REGTYPE)] * 2)
        with self.assertRaisesRegex(ev.ImportFailure, "DUPLICATE_PATH"):
            self.generate(ev.Input("lab1", self.path))

    def test_file_total_member_and_archive_limits(self):
        item = self.archive([("a", b"1234", tarfile.REGTYPE), ("b", b"1234", tarfile.REGTYPE)])
        for limits in (ev.Limits(file_bytes=3), ev.Limits(total_bytes=7), ev.Limits(members=1), ev.Limits(archive_bytes=1)):
            with self.subTest(limits=limits), self.assertRaisesRegex(ev.ImportFailure, "SIZE_LIMIT"):
                self.generate(item, limits=limits)
            self.assertFalse(self.output.exists())

    def test_decompressed_trailing_data_limit(self):
        item = self.archive()
        self.path.write_bytes(self.path.read_bytes() + gzip.compress(b"\0" * 100000))
        with self.assertRaisesRegex(ev.ImportFailure, "SIZE_LIMIT"):
            self.generate(item, limits=ev.Limits(total_bytes=20, members=2))

    def test_folder_hash_repeat_and_links(self):
        folder = self.root / "folder"
        folder.mkdir()
        (folder / "a.txt").write_text("clean")
        item = ev.Input("lab1", folder)
        report = self.generate(item)
        digest = report["runs"][0]["sha256"]
        self.assertEqual(self.generate(item), report)
        second = self.root / "second"
        ev.generate([ev.Input("lab1", folder, sha256=digest)], second)
        self.assertTrue(json.loads((second / "import-report.json").read_text())["runs"][0]["hash_verified"])
        (folder / "link").symlink_to(folder / "a.txt")
        with self.assertRaisesRegex(ev.ImportFailure, "SYMLINK"):
            ev.generate([item], self.root / "third")

    def test_special_folder_file_rejected(self):
        folder = self.root / "folder"
        folder.mkdir()
        os.mkfifo(folder / "fifo")
        with self.assertRaisesRegex(ev.ImportFailure, "SPECIAL_FILE"):
            self.generate(ev.Input("lab1", folder))

    def test_output_overlap_and_symlink_rejected(self):
        folder = self.root / "folder"
        folder.mkdir()
        with self.assertRaisesRegex(ev.ImportFailure, "OUTPUT_OVERLAPS_INPUT"):
            ev.generate([ev.Input("lab1", folder)], folder / "out")
        self.output.symlink_to(folder, target_is_directory=True)
        with self.assertRaisesRegex(ev.ImportFailure, "SYMLINK"):
            self.generate()

    def test_changed_output_never_overwritten(self):
        item = self.archive()
        self.generate(item)
        target = self.output / "files/lab1/ev/log.txt"
        target.write_text("changed")
        with self.assertRaisesRegex(ev.ImportFailure, "OUTPUT_EXISTS_DIFFERENT"):
            self.generate(item)
        self.assertEqual(target.read_text(), "changed")

    def test_schema_failure_does_not_publish(self):
        with patch.object(ev, "validate", side_effect=ev.ImportFailure("SCHEMA_INVALID")):
            with self.assertRaisesRegex(ev.ImportFailure, "SCHEMA_INVALID"):
                self.generate()
        self.assertFalse(self.output.exists())

    def test_schema_rejects_invalid_timestamp_and_origin(self):
        self.generate()
        document = self.index()
        document["items"][0]["collected_at"] = "not-a-time"
        with self.assertRaisesRegex(ev.ImportFailure, "SCHEMA_INVALID"):
            ev.validate(document, ev.ROOT / "contracts/schemas")
        document["items"][0]["collected_at"] = "2026-99-88T25:00:00Z"
        with self.assertRaisesRegex(ev.ImportFailure, "SCHEMA_INVALID"):
            ev.validate(document, ev.ROOT / "contracts/schemas")
        document = self.index()
        document["items"][0]["data_origin"] = "real"
        with self.assertRaisesRegex(ev.ImportFailure, "SCHEMA_INVALID"):
            ev.validate(document, ev.ROOT / "contracts/schemas")

    def test_multiple_runs_order_independent(self):
        item = self.archive()
        second = ev.Input("lab2", self.path)
        a = ev.generate([item, second], self.output)
        b = ev.generate([second, item], self.output)
        self.assertEqual(a, b)
        self.assertEqual(len(self.index()["items"]), 2)

    def test_idempotence_with_small_payload_budget(self):
        item = self.archive()
        limits = ev.Limits(file_bytes=12, total_bytes=12, members=1)
        report = self.generate(item, limits=limits)
        self.assertEqual(self.generate(item, limits=limits), report)

    def test_cli_errors_do_not_echo_input(self):
        for args in (["--private-input=do-not-leak"], ["--input", "lab1=/private/do-not-leak", "--output", str(self.output)]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(ev.main(args), 1)
            self.assertNotIn("do-not-leak", stdout.getvalue() + stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
