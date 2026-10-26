"""Approved asset manifest and on-disk integrity comparison."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from serving_runtime.cli import main
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.assets import check, load_manifest, snapshot
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy.json"


class AssetIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.model = self.base / "model"
        (self.model / "sub").mkdir(parents=True)
        (self.model / "config.json").write_text('{"a": 1}', encoding="utf-8")
        (self.model / "sub" / "weights.bin").write_bytes(b"\x00\x01" * 1000)
        self.manifest_path = self.base / "assets.json"
        self.manifest_path.write_text(json.dumps(snapshot(self.model)), encoding="utf-8")

    def test_snapshot_round_trip_is_intact(self) -> None:
        result = check(load_manifest(self.manifest_path))
        self.assertTrue(result["intact"])
        self.assertEqual(result["counts"]["match"], 2)
        self.assertEqual(result["total"], 2)

    def test_changed_missing_and_extra_files(self) -> None:
        (self.model / "config.json").write_text('{"a": 2}', encoding="utf-8")
        (self.model / "sub" / "weights.bin").unlink()
        (self.model / "new.txt").write_text("x", encoding="utf-8")
        result = check(load_manifest(self.manifest_path))
        self.assertFalse(result["intact"])
        self.assertEqual(result["counts"]["changed"], 1)
        self.assertEqual(result["counts"]["missing"], 1)
        self.assertEqual(result["extra_files"], ["new.txt"])

    def test_unwalked_root_reports_unknown_walk_counts(self) -> None:
        document = load_manifest(self.manifest_path)
        roots = (self.base / "missing", self.base / "not-a-directory")
        roots[1].write_text("x", encoding="utf-8")
        for root in roots:
            with self.subTest(root=root.name):
                result = check({**document, "root": str(root)})
                self.assertEqual(result["extra_files"], [])
                self.assertEqual(result["extra_symlinks"], [])
                self.assertEqual(result["walk_errors"], [])
                self.assertIsNone(result["extra_file_count"])
                self.assertIsNone(result["extra_symlink_count"])
                self.assertIsNone(result["walk_error_count"])
                self.assertFalse(result["intact"])

    def test_symlink_is_not_followed(self) -> None:
        target = self.base / "outside.bin"
        target.write_bytes(b"\x00\x01" * 1000)
        (self.model / "sub" / "weights.bin").unlink()
        os.symlink(target, self.model / "sub" / "weights.bin")
        result = check(load_manifest(self.manifest_path))
        self.assertEqual(result["counts"]["symlink"], 1)
        self.assertFalse(result["intact"])

    def test_root_symlink_is_rejected_everywhere(self) -> None:
        linked_root = self.base / "linked-model"
        os.symlink(self.model, linked_root)
        with self.assertRaises(ValueError):
            snapshot(linked_root)

        document = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        document["root"] = str(linked_root)
        linked_manifest_path = self.base / "linked-assets.json"
        linked_manifest_path.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_manifest(linked_manifest_path)
        with self.assertRaises(ValueError):
            check(document)

    def test_parent_symlink_outside_root_is_not_read(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "weights.bin").write_bytes(b"\x00\x01" * 1000)
        (self.model / "sub" / "weights.bin").unlink()
        (self.model / "sub").rmdir()
        os.symlink(outside, self.model / "sub")

        result = check(load_manifest(self.manifest_path))
        item = next(entry for entry in result["items"] if entry["path"] == "sub/weights.bin")
        self.assertEqual(item["state"], "symlink")
        self.assertEqual(result["counts"]["symlink"], 1)
        self.assertEqual(result["extra_symlinks"], ["sub"])
        self.assertEqual(result["extra_symlink_count"], 1)
        self.assertFalse(result["intact"])

    @unittest.skipIf(os.geteuid() == 0, "root can read files regardless of mode")
    def test_unreadable_file(self) -> None:
        path = self.model / "config.json"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)
        result = check(load_manifest(self.manifest_path))
        self.assertEqual(result["counts"]["unreadable"], 1)

    @unittest.skipIf(os.geteuid() == 0, "root can traverse directories regardless of mode")
    def test_walk_error_is_reported_and_snapshot_refuses_partial_result(self) -> None:
        directory = self.model / "sub"
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o755)

        result = check(load_manifest(self.manifest_path))
        self.assertFalse(result["intact"])
        self.assertEqual(result["walk_error_count"], 1)
        self.assertEqual(
            result["walk_errors"],
            [{"path": "sub", "error": "PermissionError"}],
        )
        with self.assertRaises(ValueError):
            snapshot(self.model)

    def test_manifest_rejects_escaping_and_bad_entries(self) -> None:
        document = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        cases = [
            {**document, "root": "relative/path"},
            {**document, "files": [{**document["files"][0], "path": "../escape"}]},
            {**document, "files": [{**document["files"][0], "path": "/abs"}]},
            {**document, "files": [{**document["files"][0], "sha256": "XYZ"}]},
            {**document, "files": [{**document["files"][0], "size": True}]},
            {**document, "files": [document["files"][0], document["files"][0]]},
            {**document, "extra": 1},
        ]
        for index, case in enumerate(cases):
            with self.subTest(index=index):
                path = self.base / f"bad-{index}.json"
                path.write_text(json.dumps(case), encoding="utf-8")
                with self.assertRaises(ValueError):
                    load_manifest(path)

    def test_snapshot_cli_refuses_overwrite_and_warns(self) -> None:
        out = self.base / "snap.json"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            self.assertEqual(main(["assets", "snapshot", "--root", str(self.model), "--out", str(out)]), 0)
        self.assertIn("사람이 검토", stdout.getvalue())
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["assets", "snapshot", "--root", str(self.model), "--out", str(out)]), 1)

        broken = self.base / "broken-output.json"
        os.symlink(self.base / "missing-output-target.json", broken)
        with redirect_stderr(io.StringIO()):
            self.assertEqual(
                main(
                    [
                        "assets",
                        "snapshot",
                        "--root",
                        str(self.model),
                        "--out",
                        str(broken),
                    ]
                ),
                1,
            )
        self.assertTrue(broken.is_symlink())

    def test_gateway_collector_reports_integrity(self) -> None:
        try:
            engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(engine.stop)
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY),
            data_dir=str(self.base / "data"),
            asset_manifest_path=str(self.manifest_path),
            asset_interval_s=0.1,
        )
        gateway = GatewayApp(config, load_policy(POLICY), engine.address).start()
        self.addCleanup(gateway.stop)
        host, port = gateway.address

        def asset_item() -> dict:
            with urlopen(f"http://{host}:{port}/api/collectors", timeout=3) as response:
                payload = json.loads(response.read())
            return next(item for item in payload["collectors"] if item["source"] == "asset_integrity")

        deadline = time.monotonic() + 5
        item = asset_item()
        while item["state"] != "ok" and time.monotonic() < deadline:
            time.sleep(0.05)
            item = asset_item()
        self.assertEqual(item["state"], "ok")
        self.assertEqual(item["data_origin"], "local_file")
        self.assertTrue(item["last_value"]["intact"])

        (self.model / "config.json").write_text("tampered", encoding="utf-8")
        deadline = time.monotonic() + 5
        while asset_item()["last_value"]["intact"] and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(asset_item()["last_value"]["counts"]["changed"], 1)

    def test_large_asset_check_does_not_delay_status_or_engine_health(self) -> None:
        large = self.model / "large.bin"
        large.write_bytes(b"x" * (4 * 1024 * 1024))
        self.manifest_path.write_text(json.dumps(snapshot(self.model)), encoding="utf-8")
        started = threading.Event()
        release = threading.Event()

        def delayed_check(manifest: dict) -> dict:
            started.set()
            if not release.wait(3):
                raise TimeoutError("test did not release asset check")
            return check(manifest)

        try:
            engine = MockEngine(port=0).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(engine.stop)
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY),
            data_dir=str(self.base / "large-data"),
            asset_manifest_path=str(self.manifest_path),
            asset_interval_s=0.1,
            collector_interval_s=0.05,
            collector_stale_after_s=1,
        )
        with patch("serving_runtime.mvp.app.check_assets", side_effect=delayed_check):
            start_time = time.monotonic()
            gateway = GatewayApp(config, load_policy(POLICY), engine.address).start()
            self.addCleanup(gateway.stop)
            self.addCleanup(release.set)
            self.assertLess(time.monotonic() - start_time, 1)
            self.assertTrue(started.wait(1))
            host, port = gateway.address

            request_started = time.monotonic()
            with urlopen(f"http://{host}:{port}/api/status", timeout=2) as response:
                self.assertEqual(response.status, 200)
            self.assertLess(time.monotonic() - request_started, 1)

            def engine_attempt() -> str | None:
                with urlopen(f"http://{host}:{port}/api/collectors", timeout=2) as response:
                    payload = json.loads(response.read())
                item = next(
                    value
                    for value in payload["collectors"]
                    if value["source"] == "engine_health"
                )
                return item["last_attempt_at"]

            first_attempt = engine_attempt()
            deadline = time.monotonic() + 1
            while engine_attempt() == first_attempt and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertNotEqual(engine_attempt(), first_attempt)

            release.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                with urlopen(f"http://{host}:{port}/api/collectors", timeout=2) as response:
                    payload = json.loads(response.read())
                asset = next(
                    value
                    for value in payload["collectors"]
                    if value["source"] == "asset_integrity"
                )
                if asset["state"] == "ok":
                    break
                time.sleep(0.02)
            self.assertEqual(asset["state"], "ok")
            self.assertGreaterEqual(asset["last_value"]["duration_s"], 0)


if __name__ == "__main__":
    unittest.main()
