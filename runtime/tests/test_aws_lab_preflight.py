from __future__ import annotations

import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tools.aws_lab_preflight import CommandResult, build_parser, collect, main


def pod(image: str, image_id: str, container: str) -> dict:
    return {"items": [{"metadata": {}, "spec": {"containers": [{"name": container, "image": image}]}, "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": [{"name": container, "ready": True, "imageID": image_id}]}}]}


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []
        self.verify_code = 0
        self.phase_enabled = True
        self.phase_output = None

    def run(self, argv, *, timeout=8):
        self.calls.append(tuple(argv))
        if argv[-2:] == ["config", "current-context"]:
            return CommandResult(0, "byul-aws-lab\n")
        if list(argv[1:4]) == ["-m", "serving_runtime", "bundle"]:
            return CommandResult(self.verify_code, "secret output")
        if argv[0] == "systemctl":
            if "show" in argv:
                return CommandResult(0, self.phase_output if self.phase_output is not None else "ExecStart=... " + ("--phase-timeline" if self.phase_enabled else ""))
            return CommandResult(0, "active\n")
        if "nodes" in argv:
            return CommandResult(0, json.dumps({"items": [{"status": {"conditions": [{"type": "Ready", "status": "True"}], "allocatable": {"nvidia.com/gpu": "1"}}}]}))
        if "app=serving-engine" in argv:
            return CommandResult(0, json.dumps(pod("engine@sha256:" + "a" * 64, "containerd://sha256:" + "a" * 64, "engine")))
        if "app=serving-gateway" in argv:
            return CommandResult(0, json.dumps(pod("serving-gateway:expected", "containerd://sha256:" + "b" * 64, "gateway")))
        if "tracingpoliciesnamespaced" in argv:
            names = ("runtime-tg-asset-01", "runtime-tg-cache-01", "runtime-tg-secret-01")
        elif "ciliumnetworkpolicies" in argv:
            names = ("runtime-engine-egress", "runtime-engine-ingress", "runtime-gateway-egress", "runtime-observer-0-egress")
        else:
            raise AssertionError(argv)
        return CommandResult(0, json.dumps({"items": [{"metadata": {"name": name}} for name in names]}))


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, *args):
        return json.dumps({"policy_state": {"gateway_active": True, "approval": {"state": "verified"}}}).encode()


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.bundle = root / "bundles"
        self.bundle.mkdir()
        (self.bundle / "good").mkdir()
        (self.bundle / "current").symlink_to("good")
        self.obs = root / "observation"
        self.obs.mkdir()
        for name in ("hubble.heartbeat.json", "tetragon.heartbeat.json", "target-pods.json", "phase-timeline.json"):
            (self.obs / name).write_text("{}")
        self.runner = FakeRunner()
        self.args = build_parser().parse_args(["--engine", "vllm", "--bundle-root", str(self.bundle), "--observation-dir", str(self.obs), "--evidence-dir", str(root), "--expect-gateway-tag", "expected", "--expect-engine-digest", "sha256:" + "a" * 64])

    def check(self):
        with patch("tools.aws_lab_preflight.shutil.disk_usage") as disk:
            disk.side_effect = lambda path: SimpleNamespace(free=(70 if path == self.args.image_store else 10) * 1024**3)
            return {c.name: c for c in collect(self.args, self.runner, opener=lambda request, timeout: FakeResponse())}

    def test_all_ready_and_read_only_commands(self):
        checks = self.check()
        self.assertTrue(all(c.status == "ok" for c in checks.values()), checks)
        self.assertEqual(self.runner.calls[-1][0], "kubectl")
        self.assertIn(("kubectl", "--context", "byul-aws-lab", "-n", "serving-lab", "get", "tracingpoliciesnamespaced", "-o", "json"), self.runner.calls)
        self.assertEqual(checks["tracingpoliciesnamespaced"].status, "ok")
        self.assertFalse(any(word in call for call in self.runner.calls for word in ("apply", "patch", "delete", "restart", "create")))
        self.assertNotIn("boundary_probe:chat", checks)

    def test_boundary_probe_is_opt_in_and_reports_both_routes(self):
        self.args.boundary_probe = True
        original_run = self.runner.run
        def run(argv, *, timeout=8):
            if "exec" in argv:
                self.runner.calls.append(tuple(argv))
                state, reason = ("ok", "요청 답변 길이 생성") if argv[-1] == "chat" else ("fail", "답변 잘림")
                return CommandResult(0, json.dumps({"state": state, "reason": reason, "completion_tokens": 2}))
            return original_run(argv, timeout=timeout)
        self.runner.run = run
        def opener(request, timeout):
            if request.full_url.endswith("/api/policy"):
                class PolicyResponse(FakeResponse):
                    def read(self, *args):
                        return json.dumps({"current": {"max_context_tokens": 2048}}).encode()
                return PolicyResponse()
            return FakeResponse()
        with patch("tools.aws_lab_preflight.shutil.disk_usage") as disk:
            disk.side_effect = lambda path: SimpleNamespace(free=(70 if path == self.args.image_store else 10) * 1024**3)
            checks = {c.name: c for c in collect(self.args, self.runner, opener=opener)}
        self.assertEqual(checks["boundary_probe:chat"].status, "ok")
        self.assertEqual(checks["boundary_probe:completions"].reason, "답변 잘림")
        commands = [call for call in self.runner.calls if "exec" in call]
        self.assertEqual(len(commands), 2)
        self.assertTrue(all(call[-3] == "2048" and "python3" in call and "-c" in call for call in commands))

    def test_boundary_probe_without_policy_limit_is_unknown_without_exec(self):
        self.args.boundary_probe = True
        checks = self.check()
        self.assertEqual(checks["boundary_probe:chat"].status, "unknown")
        self.assertFalse(any("exec" in call for call in self.runner.calls))

    def test_verify_failure_is_fail_and_no_raw_output(self):
        self.runner.verify_code = 1
        checks = self.check()
        self.assertEqual(checks["bundle"].status, "fail")
        self.assertNotIn("secret output", json.dumps([c.reason for c in checks.values()]))

    def test_sglang_timeline_is_not_required(self):
        self.args.engine = "sglang"
        self.runner.phase_enabled = False
        (self.obs / "phase-timeline.json").unlink()
        checks = self.check()
        self.assertEqual(checks["phase_timeline"].status, "ok")
        self.assertNotIn("observation:phase-timeline.json", checks)

    def test_empty_exec_start_is_unknown_for_sglang(self):
        self.args.engine = "sglang"
        self.runner.phase_output = "ExecStart=\n"
        self.assertEqual(self.check()["phase_timeline"].status, "unknown")

    def test_image_store_disk_threshold_is_separate(self):
        self.assertEqual(self.args.image_store, "/var/lib/containerd")
        self.assertEqual(self.args.min_image_free_gib, 60)
        options = build_parser().parse_args(["--engine", "vllm", "--image-store", "/srv/custom-containerd", "--min-image-free-gib", "75"])
        self.args.image_store = options.image_store
        self.args.min_image_free_gib = options.min_image_free_gib
        checks = self.check()
        self.assertEqual(checks["disk"].status, "ok")
        self.assertEqual(checks["image_store_disk"].status, "fail")

    def test_image_store_disk_unavailable_is_unknown(self):
        with patch("tools.aws_lab_preflight.shutil.disk_usage") as disk:
            def usage(path):
                if path == self.args.image_store:
                    raise OSError("unavailable")
                return SimpleNamespace(free=10 * 1024**3)
            disk.side_effect = usage
            checks = {c.name: c for c in collect(self.args, self.runner, opener=lambda request, timeout: FakeResponse())}
        self.assertEqual(checks["disk"].status, "ok")
        self.assertEqual(checks["image_store_disk"].status, "unknown")

    def test_digest_mismatch_and_absolute_link_fail(self):
        self.args.expect_engine_digest = "sha256:" + "c" * 64
        (self.bundle / "current").unlink()
        (self.bundle / "current").symlink_to(self.bundle / "good")
        checks = self.check()
        self.assertEqual(checks["engine_digest"].status, "fail")
        self.assertEqual(checks["bundle"].status, "fail")

    def test_unreadable_bundle_root_is_unknown_not_crash(self):
        with patch("tools.aws_lab_preflight.Path.is_symlink", side_effect=PermissionError(13, "denied")):
            checks = self.check()
        self.assertEqual(checks["bundle"].status, "unknown")

    def test_registry_digest_can_differ_from_pod_config_digest(self):
        self.args.expect_engine_digest = "sha256:" + "a" * 64
        checks = self.check()
        self.assertEqual(checks["engine_digest"].status, "ok")

    def test_stale_observation_fails(self):
        with patch("tools.aws_lab_preflight.shutil.disk_usage") as disk:
            disk.side_effect = lambda path: SimpleNamespace(free=(70 if path == self.args.image_store else 10) * 1024**3)
            from datetime import datetime, timedelta, timezone
            checks = {c.name: c for c in collect(self.args, self.runner, opener=lambda request, timeout: FakeResponse(), now=datetime.now(timezone.utc) + timedelta(minutes=1))}
        self.assertEqual(checks["observation:target-pods.json"].status, "fail")

    def test_authenticated_status_uses_token_without_reporting_it(self):
        env = Path(self.tmp.name) / "acceptance.env"
        env.write_text("RUNTIME_VIEWER_TOKEN=private-value\n")
        self.args.env_file = str(env)
        calls = []

        def opener(request, timeout):
            calls.append(request.get_header("Authorization"))
            if len(calls) == 1:
                raise HTTPError(request.full_url, 401, "auth", {}, None)
            return FakeResponse()

        with patch("tools.aws_lab_preflight.shutil.disk_usage") as disk:
            disk.side_effect = lambda path: SimpleNamespace(free=(70 if path == self.args.image_store else 10) * 1024**3)
            checks = collect(self.args, self.runner, opener=opener)
        self.assertEqual(calls, [None, "Bearer private-value"])
        self.assertNotIn("private-value", json.dumps([c.reason for c in checks]))

    def test_exit_code_on_failure(self):
        with patch("tools.aws_lab_preflight.collect", return_value=[type("Check", (), {"name": "x", "status": "fail", "reason": "bad"})()]):
            with patch("builtins.print"):
                self.assertEqual(main(["--engine", "vllm"]), 1)


if __name__ == "__main__":
    unittest.main()
