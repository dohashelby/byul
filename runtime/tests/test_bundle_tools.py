import hashlib
from contextlib import nullcontext, redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from serving_runtime.bundle_tools import (build_bundle, apply_bundle, _canonical,
                                          _checked_status, _check_deployed_permissions,
                                          _status, StatusMismatch,
                                          StatusUnavailable)
from serving_runtime.cli import main
from serving_runtime.enforcement.intent import load_intent
from serving_runtime.enforcement.tetragon import comparable_spec, render_monitor_manifests
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_bundle import verify_policy_bundle

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "deploy/gpu-lab"


class BundleToolsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        summary = json.loads((LAB / "platform-approval.aws-lab.json").read_text())
        self.summary = summary
        self.policy = {
            "schema_version": "byul-policy/0.1", "policy_id": summary["policy_id"],
            "policy_version": summary["policy_version"], "created_at": "2026-11-03T05:18:02Z",
            "data_origin": "EXAMPLE",
            "status": "CONFIRMED", "target_ref": summary["target_ref"],
            "environment_ref": summary["environment_ref"], "validity": summary["validity"],
            "approval": summary["approval"],
            "verification": {"pack": {"id": "PACK-TEST", "revision": 1}, "values": {},
                             "scope": {"include": [], "exclude": []}, "exceptions": []},
            "runtime": summary["runtime"],
        }
        self.policy_path = self.root / "policy.json"
        self.policy_path.write_text(json.dumps(self.policy))
        self.digest = hashlib.sha256(_canonical(self.policy)).hexdigest()
        self.server_path = self.root / "server.json"
        self.out = self.root / "out"

    def _build(self, engine):
        lab = ROOT / ("deploy/gpu-lab-sglang" if engine == "sglang" else "deploy/gpu-lab")
        self.server_path.write_bytes((ROOT / "tests/fixtures" / f"bundle-server-{engine}.json").read_bytes())
        intent_path = self.root / f"{engine}-intent.json"
        intent_path.write_text((lab / "intent.aws-lab.json").read_text().replace(
            "REPLACE_IMAGE_DIGEST_HEX", "a" * 64).replace("REPLACE_PROFILE", "test"))
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["bundle", "build", "--policy", str(self.policy_path),
                         "--policy-sha256", self.digest, "--server-values", str(self.server_path),
                         "--intent", str(intent_path), "--output-parent", str(self.out),
                         "--bundle-id", engine])
        self.assertEqual(code, 0)
        self.assertIn("자기 신고", output.getvalue())
        bundle = verify_policy_bundle(self.out / engine)
        return bundle, lab

    def test_built_bundle_binds_external_enforcement_to_gateway_file_hash(self):
        bundle, _ = self._build("vllm")
        feeds = (("tetragon", str(self.root / "feed.jsonl")),)
        options, bundle_status = bundle.mvp_options(feeds)
        policy_state = load_policy(options["policy_path"])
        self.assertTrue(policy_state.ok)
        self.assertNotEqual(bundle_status["policy_ref"]["sha256"], policy_state.sha256)
        gateway_file = next(f for f in bundle_status["files"] if f["role"] == "gateway_policy")
        self.assertEqual(gateway_file["sha256"], policy_state.sha256)
        public = self.root / "tetragon-state.json"
        config = MVPConfig(**options, data_dir=str(self.root / "data"), policy_bundle=bundle_status,
                           tetragon_state_path=str(public), observation_feeds=feeds)
        app = GatewayApp(config, policy_state, ("127.0.0.1", 1))
        context = json.loads(Path(options["target_path"]).read_text())["kubernetes"]["context"]
        sample = {"schema_version": "runtime-tetragon-state/1.0", "context": context,
                  "generated_at": datetime.now(timezone.utc).isoformat(), "state": "ok",
                  "agents": [{"node": "test-node"}], "policies": [],
                  "capabilities": [], "transitions": [], "verifications": []}
        for manifest in render_monitor_manifests(load_intent(options["observation_intent_path"])).values():
            digest = hashlib.sha256(json.dumps(comparable_spec(manifest["spec"]), sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
            sample["policies"].append({"node": "test-node", "namespace": manifest["metadata"]["namespace"],
                                       "name": manifest["metadata"]["name"], "id": "1", "state": "enabled",
                                       "mode": "monitor", "spec_sha256": digest})
        public.write_text(json.dumps(sample))
        self.assertEqual(app.policy_status()["external_enforcement"], "monitor")
        sample["policies"][0]["mode"] = "enforce"
        public.write_text(json.dumps(sample))
        self.assertEqual(app.policy_status()["external_enforcement"], "mixed")

        # Even the same ID/version cannot bind a different Gateway policy file.
        gateway_file["sha256"] = "0" * 64
        mismatched_app = GatewayApp(config, policy_state, ("127.0.0.1", 1))
        self.assertEqual(mismatched_app.policy_status()["external_enforcement"], "unknown")

    def test_build_bundle_with_enforced_secret_intent(self):
        self._build("vllm")
        intent_path = self.root / "vllm-intent.json"
        intent = json.loads(intent_path.read_text())
        intent["protected_paths"]["secret_paths"] = ["/var/run/secrets/serving/probe-secret.txt"]
        intent["tetragon_rule_modes"] = {"TG-SECRET-01": "enforce"}
        intent_path.write_text(json.dumps(intent))
        bundle = build_bundle(str(self.policy_path), self.digest, str(self.server_path),
                              str(intent_path), str(self.out), "enforce-secret")
        self.assertEqual(bundle.bundle_id, "enforce-secret")
        self.assertEqual(verify_policy_bundle(self.out / "enforce-secret").bundle_id,
                         "enforce-secret")

    def test_golden_both_engines_and_rejects_draft_or_hash(self):
        for engine in ("vllm", "sglang"):
            bundle, lab = self._build(engine)
            self.assertEqual(verify_policy_bundle(self.out / engine).bundle_id, engine)
            self.assertEqual((self.out / engine / "runtime-policy.json").read_bytes(),
                             (lab / "policy.aws-lab.json").read_bytes())
            self.assertEqual(bundle.policy_ref["sha256"], self.digest)
            self.assertNotEqual(self.digest, self.summary["source"]["document_sha256"])
        with self.assertRaisesRegex(ValueError, "필수/허용"):
            build_bundle(str(LAB / "platform-approval.aws-lab.json"),
                         self.summary["source"]["document_sha256"], str(self.server_path),
                         str(LAB / "intent.aws-lab.json"), str(self.out), "summary")
        self.server_path.write_bytes((ROOT / "tests/fixtures/bundle-server-vllm.json").read_bytes())
        with self.assertRaises(FileExistsError):
            build_bundle(str(self.policy_path), self.digest, str(self.server_path),
                         str(self.root / "vllm-intent.json"), str(self.out), "vllm")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            build_bundle(str(self.policy_path), "0" * 64, str(self.server_path),
                         str(LAB / "intent.aws-lab.json"), str(self.out), "bad")
        self.policy["status"] = "DRAFT"
        self.policy_path.write_text(json.dumps(self.policy))
        with self.assertRaisesRegex(ValueError, "CONFIRMED"):
            build_bundle(str(self.policy_path), hashlib.sha256(_canonical(self.policy)).hexdigest(),
                         str(self.server_path), str(LAB / "intent.aws-lab.json"), str(self.out), "draft")

    def test_apply_dry_run_and_rollback_on_status_failure(self):
        self._build("vllm")
        root = self.root / "server"
        root.mkdir()
        old = root / "previous"
        old.mkdir()
        for item in (self.out / "vllm").iterdir():
            (old / item.name).write_bytes(item.read_bytes())
        manifest = json.loads((old / "bundle.json").read_text())
        manifest["bundle_id"] = "previous"
        (old / "bundle.json").write_text(json.dumps(manifest))
        (root / "current").symlink_to("previous")
        token_file = self.root / "secret.env"
        token_file.write_text("RUNTIME_INFERENCE_TOKEN=inference-secret\nRUNTIME_VIEWER_TOKEN=secret-token\n")
        args = (str(self.out / "vllm"), str(root), "test-context", "test-ns",
                "http://127.0.0.1:9999", str(token_file))
        result = apply_bundle(*args, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertFalse((root / "vllm").exists())
        calls = []
        def fake_run(command):
            calls.append(command)
        with patch("serving_runtime.bundle_tools.os.chown"), \
             patch("serving_runtime.bundle_tools._run", side_effect=fake_run), \
             patch("serving_runtime.bundle_tools._checked_status", side_effect=StatusMismatch("bundle_id=wrong")):
            with self.assertRaisesRegex(RuntimeError, "이전 링크 복원"):
                apply_bundle(*args)
        self.assertEqual(os.readlink(root / "current"), "previous")
        self.assertEqual(sum("restart" in call for call in calls), 2)
        self.assertTrue(all(call[1:5] == ["--context", "test-context", "-n", "test-ns"] for call in calls))
        self.assertEqual(verify_policy_bundle(root / "vllm").bundle_id, "vllm")
        for item in (root / "vllm").iterdir():
            item.unlink()
        (root / "vllm").rmdir()
        with patch("serving_runtime.bundle_tools.os.chown", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(PermissionError, "sudo"):
                apply_bundle(*args)
        self.assertEqual(os.readlink(root / "current"), "previous")
        kubectl = self.root / "kubectl"
        kubectl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_KUBECTL_LOG"\n'
                           'case "$*" in *port-forward*) printf "Forwarding from 127.0.0.1:9999 -> 9999\\n"; exec sleep 60;; esac\n')
        kubectl.chmod(0o755)
        log = self.root / "kubectl.log"
        kubeconfig = self.root / "config"
        kubeconfig.write_text("fake config")
        def fake_http(request, timeout):
            self.assertEqual(request.full_url, "http://127.0.0.1:9999/api/status")
            if request.get_header("Authorization") is None:
                raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)
            self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
            return io.BytesIO(json.dumps({"policy_bundle": {"bundle_id": "vllm"},
                                          "policy": {"approval": {"state": "verified"}}}).encode())
        with patch("serving_runtime.bundle_tools.os.chown"), \
             patch("serving_runtime.bundle_tools.urlopen", side_effect=fake_http), \
             patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                                      "FAKE_KUBECTL_LOG": str(log)}):
            result = apply_bundle(*args, kubeconfig=str(kubeconfig))
        self.assertFalse(result["dry_run"])
        self.assertEqual(os.readlink(root / "current"), "vllm")
        self.assertEqual(stat.S_IMODE((root / "vllm").stat().st_mode), 0o750)
        self.assertEqual(stat.S_IMODE((root / "vllm" / "bundle.json").stat().st_mode), 0o640)
        self.assertEqual(len(log.read_text().splitlines()), 3)
        self.assertIn("port-forward deployment/serving-gateway :9999", log.read_text())
        self.assertTrue(all(f"--kubeconfig {kubeconfig}" in line for line in log.read_text().splitlines()))
        self.assertNotIn("secret-token", log.read_text())

    def test_status_unavailable_keeps_new_link(self):
        self._build("vllm")
        root = self.root / "server"
        root.mkdir()
        old = root / "previous"
        old.mkdir()
        for item in (self.out / "vllm").iterdir():
            (old / item.name).write_bytes(item.read_bytes())
        manifest = json.loads((old / "bundle.json").read_text())
        manifest["bundle_id"] = "previous"
        (old / "bundle.json").write_text(json.dumps(manifest))
        (root / "current").symlink_to("previous")
        with patch("serving_runtime.bundle_tools.os.chown"), \
             patch("serving_runtime.bundle_tools._run") as kubectl, \
             patch("serving_runtime.bundle_tools._checked_status",
                   side_effect=StatusUnavailable("연결 실패")):
            with self.assertRaisesRegex(StatusUnavailable, "연결 실패"):
                apply_bundle(str(self.out / "vllm"), str(root), "ctx", "ns",
                             "http://127.0.0.1:8080", None)
        self.assertEqual(os.readlink(root / "current"), "vllm")
        self.assertEqual(kubectl.call_count, 2)

    def test_apply_subprocess_keeps_kubectl_output_off_result_stdout(self):
        self._build("vllm")
        kubectl = self.root / "kubectl"
        kubectl.write_text(
            '#!/bin/sh\n'
            'case "$*" in\n'
            '  *restart*) printf "deployment.apps/serving-gateway restarted\\n";;\n'
            '  *status*) printf "Waiting for deployment serving-gateway...\\n"\n'
            '            printf "deployment serving-gateway successfully rolled out\\n";;\n'
            '  *) exit 2;;\n'
            'esac\n'
            'printf "kubectl diagnostic\\n" >&2\n'
        )
        kubectl.chmod(0o755)
        # Run the real CLI/apply/_run in a child so inherited file descriptors are
        # captured too; redirect_stdout alone cannot detect this regression.
        script = textwrap.dedent("""\
            import sys
            from unittest.mock import patch
            from serving_runtime.bundle_tools import StatusMismatch, StatusUnavailable
            from serving_runtime.cli import main

            error = {"rollback": StatusMismatch("wrong bundle"),
                     "unknown": StatusUnavailable("status unavailable")}.get(sys.argv[1])
            with patch("serving_runtime.bundle_tools.os.chown"), \\
                 patch("serving_runtime.bundle_tools._checked_status", side_effect=error):
                sys.exit(main(sys.argv[2:]))
            """)
        cases = [
            ("success", True, 0, {"state": "succeeded", "code": "OK"}, 1),
            ("rollback", True, 1, {"state": "rolled_back", "code": "ROLLED_BACK"}, 2),
            ("unknown", True, 1, {"state": "unknown", "code": "STATUS_UNKNOWN"}, 1),
            ("success", False, 0, None, 1),
        ]
        for index, (scenario, machine, returncode, outcome, rollouts) in enumerate(cases):
            with self.subTest(scenario=scenario, operation_result=machine):
                root = self.root / f"server-{index}"
                old = root / "previous"
                shutil.copytree(self.out / "vllm", old)
                manifest = json.loads((old / "bundle.json").read_text())
                manifest["bundle_id"] = "previous"
                (old / "bundle.json").write_text(json.dumps(manifest))
                (root / "current").symlink_to("previous")
                command = [sys.executable, "-B", "-c", script, scenario,
                           "bundle", "apply", "--bundle-dir", str(self.out / "vllm"),
                           "--bundle-root", str(root), "--context", "test", "--namespace", "test",
                           "--status-url", "http://127.0.0.1:9999"]
                if machine:
                    command.append("--operation-result")
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=10, check=False,
                    env={**os.environ, "PATH": str(self.root) + os.pathsep + os.environ["PATH"]},
                )
                self.assertEqual(result.returncode, returncode, result.stderr)
                if machine:
                    self.assertEqual(json.loads(result.stdout), outcome)
                    self.assertEqual(len(result.stdout.splitlines()), 1)
                else:
                    self.assertEqual(result.stdout, "묶음 적용·상태 확인 성공: previous -> vllm\n")
                for message in ("deployment.apps/serving-gateway restarted",
                                "Waiting for deployment serving-gateway...",
                                "deployment serving-gateway successfully rolled out"):
                    self.assertEqual(result.stderr.count(message), rollouts)
                self.assertEqual(result.stderr.count("kubectl diagnostic"), rollouts * 2)
                self.assertEqual(os.readlink(root / "current"),
                                 "previous" if scenario == "rollback" else "vllm")

    def test_status_retry_and_unprotected_lookup(self):
        status = {"policy_bundle": {"bundle_id": "new"},
                  "policy": {"approval": {"state": "verified"}}}
        with patch("serving_runtime.bundle_tools._port_forward", return_value=nullcontext("http://127.0.0.1:9090")), \
             patch("serving_runtime.bundle_tools._status", side_effect=[URLError("old Pod"), status]) as lookup, \
             patch("serving_runtime.bundle_tools.time.sleep"):
            _checked_status("ctx", "ns", None, 8080, None, "new", timeout=1)
        self.assertEqual(lookup.call_count, 2)
        def no_auth_http(request, timeout):
            self.assertIsNone(request.get_header("Authorization"))
            return io.BytesIO(json.dumps(status).encode())
        with patch("serving_runtime.bundle_tools.urlopen", side_effect=no_auth_http):
            self.assertEqual(_status("http://127.0.0.1:9090", None), status)
        with patch("serving_runtime.bundle_tools.urlopen",
                   side_effect=HTTPError("http://127.0.0.1:9090/api/status", 401, "Unauthorized", {}, None)):
            with self.assertRaisesRegex(StatusUnavailable, "RUNTIME_VIEWER_TOKEN 없음"):
                _status("http://127.0.0.1:9090", None)
        with patch("serving_runtime.bundle_tools._port_forward", return_value=nullcontext("http://127.0.0.1:9090")), \
             patch("serving_runtime.bundle_tools._status", side_effect=URLError("disconnected")):
            with self.assertRaisesRegex(StatusUnavailable, "조회 불가.*URLError"):
                _checked_status("ctx", "ns", None, 8080, None, "new", timeout=0)

    def test_status_reopens_failed_fake_kubectl_port_forward(self):
        kubectl = self.root / "kubectl"
        kubectl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$FAKE_KUBECTL_LOG"\n'
                           'if [ "$(wc -l < "$FAKE_KUBECTL_LOG")" -eq 1 ]; then\n'
                           '  printf "old Pod gone\\n"\n  exit 1\nfi\n'
                           'printf "Forwarding from 127.0.0.1:9090 -> 8080\\n"\n'
                           'exec sleep 60\n')
        kubectl.chmod(0o755)
        log = self.root / "kubectl.log"
        status = {"policy_bundle": {"bundle_id": "new"},
                  "policy": {"approval": {"state": "verified"}}}
        with patch.dict(os.environ, {"PATH": str(self.root) + os.pathsep + os.environ["PATH"],
                                     "FAKE_KUBECTL_LOG": str(log)}), \
             patch("serving_runtime.bundle_tools._status", return_value=status) as lookup, \
             patch("serving_runtime.bundle_tools.time.sleep"):
            _checked_status("ctx", "ns", None, 8080, None, "new", timeout=5)
        self.assertEqual(len(log.read_text().splitlines()), 2)
        lookup.assert_called_once()
        self.assertTrue(all("port-forward deployment/serving-gateway" in line
                            for line in log.read_text().splitlines()))

    def test_existing_switch_dry_run_permissions_and_rollback(self):
        self._build("vllm")
        root = self.root / "server"
        root.mkdir()
        for name in ("previous", "vllm"):
            target = root / name
            target.mkdir()
            for item in (self.out / "vllm").iterdir():
                (target / item.name).write_bytes(item.read_bytes())
            if name == "previous":
                manifest = json.loads((target / "bundle.json").read_text())
                manifest["bundle_id"] = name
                (target / "bundle.json").write_text(json.dumps(manifest))
        (root / "current").symlink_to("previous")
        args = (None, str(root), "ctx", "ns", "http://127.0.0.1:8080", None)
        with self.assertRaisesRegex(ValueError, "소유권·권한"):
            apply_bundle(*args, existing="vllm", dry_run=True)
        with patch("serving_runtime.bundle_tools._check_deployed_permissions") as permissions:
            result = apply_bundle(*args, existing="vllm", dry_run=True)
            self.assertEqual(result["bundle_id"], "vllm")
            with redirect_stdout(io.StringIO()):
                code = main(["bundle", "apply", "--existing", "vllm", "--bundle-root", str(root),
                             "--context", "ctx", "--namespace", "ns",
                             "--status-url", "http://127.0.0.1:8080", "--dry-run"])
            self.assertEqual(code, 0)
            with self.assertRaises(FileExistsError):
                apply_bundle(str(self.out / "vllm"), *args[1:], dry_run=True)
            with patch("serving_runtime.bundle_tools.tempfile.TemporaryDirectory",
                       side_effect=AssertionError("복사 경로에 들어감")), \
                 patch("serving_runtime.bundle_tools._run") as rollout, \
                 patch("serving_runtime.bundle_tools._checked_status",
                       side_effect=StatusMismatch("wrong")):
                with self.assertRaisesRegex(RuntimeError, "이전 링크 복원"):
                    apply_bundle(*args, existing="vllm")
            self.assertEqual(os.readlink(root / "current"), "previous")
            self.assertEqual(rollout.call_count, 4)
            with patch("serving_runtime.bundle_tools._run") as rollout, \
                 patch("serving_runtime.bundle_tools._checked_status") as checked:
                result = apply_bundle(*args, existing="vllm")
            self.assertEqual(os.readlink(root / "current"), "vllm")
            self.assertEqual(rollout.call_count, 2)
            checked.assert_called_once()
            self.assertFalse(result["dry_run"])
        self.assertGreaterEqual(permissions.call_count, 3)

    def test_dry_run_root_permission_error_mentions_sudo(self):
        self._build("vllm")
        root = self.root / "server"
        root.mkdir()
        root.chmod(0o000)
        try:
            with self.assertRaisesRegex(PermissionError, "--dry-run도 sudo"):
                apply_bundle(str(self.out / "vllm"), str(root), "ctx", "ns",
                             "http://127.0.0.1:8080", None, dry_run=True)
        finally:
            root.chmod(0o700)

    def test_existing_permissions_require_exact_owner_and_modes(self):
        directory = self.root / "deployed"
        directory.mkdir(mode=0o750)
        file = directory / "bundle.json"
        file.write_text("{}")
        file.chmod(0o640)
        original_lstat = Path.lstat

        def root_owned(path):
            values = list(original_lstat(path))
            values[4:6] = [0, 10000]
            return os.stat_result(values)

        with patch("serving_runtime.bundle_tools.Path.lstat", root_owned):
            _check_deployed_permissions(directory)
            file.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "소유권·권한"):
                _check_deployed_permissions(directory)

    def test_gateway_12_requires_explicit_server_cache_value(self):
        self._build("vllm")
        self.policy["runtime"]["gateway"]["profile"] = "default"
        self.policy_path.write_text(json.dumps(self.policy))
        digest = hashlib.sha256(_canonical(self.policy)).hexdigest()
        server = json.loads(self.server_path.read_text())
        server["gateway_schema_version"] = "runtime-mvp/1.2"
        self.server_path.write_text(json.dumps(server))
        intent_path = self.root / "vllm-intent.json"
        with self.assertRaisesRegex(ValueError, "gateway_cache_isolation"):
            build_bundle(str(self.policy_path), digest, str(self.server_path),
                         str(intent_path), str(self.out), "v12")
        server["gateway_cache_isolation"] = "off"
        self.server_path.write_text(json.dumps(server))
        bundle = build_bundle(str(self.policy_path), digest, str(self.server_path),
                              str(intent_path), str(self.out), "v12")
        self.assertEqual(bundle.files[0].schema_version, "runtime-mvp/1.2")


if __name__ == "__main__":
    unittest.main()
