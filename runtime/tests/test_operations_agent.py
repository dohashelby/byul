"""Fail-closed validation, dry-run, locking and sanitized terminal reporting."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import nullcontext
from urllib.error import URLError

from serving_runtime.operations.agent import Agent, Config, PlatformClient, Rejected, locked, now
from serving_runtime.operations.safety import evaluate
from serving_runtime.bundle_tools import StatusUnavailable
from serving_runtime.enforcement.intent import IntentError, parse_intent
from serving_runtime.enforcement.tetragon import render_manifests
from tests.test_tetragon_rule_modes import document


def intent_document():
    value = document()
    value["tetragon_allowed_modes"] = {"TG-SECRET-01": ["monitor", "enforce"]}
    value["tetragon_enforce_method"] = "override_lsm"
    value["protected_paths"]["secret_paths"] = ["/run/test-token"]
    value["protected_paths"]["enforce_exact_files"] = ["/run/test-token"]
    return value


def operation(kind="bundle.switch"):
    return {"id": "b" * 32, "server": "server-01", "kind": kind, "payload": {"bundle_id": "previous"},
            "state": "claimed", "requested_role": "runtime_operator", "requested_by": "operator-01",
            "expected": {"bundle_id": "old", "approval": "verified", "policy_id": None, "mode": None, "spec_sha256": None},
            "approval_required": kind == "bundle.apply", "approvals": [], "created_at": now(),
            "lease_until": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), "lease_id": "c" * 32}


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.agent = Agent(self.client, Config("server-01", "/unused", "test", "test"))
        self.before = {"bundle_id": "old", "approval": "verified", "policy_id": None, "mode": None, "spec_sha256": None}
        self.intent = parse_intent(intent_document())

    def test_state_changed_and_target_and_lease(self):
        op = operation()
        self.assertEqual(self.agent.validate(op, self.before, self.intent), self.before)
        for change, code in [({"expected": {"bundle_id": "changed"}}, "STATE_CHANGED"),
                             ({"server": "server-02"}, "TARGET_MISMATCH"),
                             ({"lease_until": now()}, "LEASE_EXPIRED"),
                             ({"requested_role": "viewer"}, "APPROVAL_REQUIRED")]:
            with self.assertRaisesRegex(Rejected, code):
                self.agent.validate({**op, **change}, self.before, self.intent)

    def test_approval_rechecked_and_lab_opt_in(self):
        op = operation("bundle.apply")
        with self.assertRaisesRegex(Rejected, "APPROVAL_REQUIRED"):
            self.agent.validate(op, self.before, self.intent)
        op["approvals"] = [{"token_id": "approver-01", "role": "approver"}]
        self.agent.validate(op, self.before, self.intent)
        op.update(lab_self_approval=True, approvals=[{"token_id": "operator-01", "role": "runtime_operator", "self_approval": True}])
        with self.assertRaisesRegex(Rejected, "APPROVAL_REQUIRED"):
            self.agent.validate(op, self.before, self.intent)
        agent = Agent(self.client, replace(self.agent.config, lab_self_approval=True))
        agent.validate(op, self.before, self.intent)

    def test_dry_run_no_execution_and_result_sanitized(self):
        op = operation("tetragon.mode")
        op["payload"] = {"policy_name": "runtime-tg-secret-01", "mode": "monitor"}
        state = {**self.before, "policy_id": "7", "mode": "enforce", "spec_sha256": "a" * 64}
        op["expected"] = state
        with patch.object(self.agent, "current", return_value=(self.before, self.intent)), patch.object(self.agent, "mode_state", return_value=state), patch.object(self.agent, "tool") as tool:
            result = self.agent.process(op)
        self.assertEqual(result["code"], "DRY_RUN")
        self.assertEqual(result["state"], "checked")
        self.assertTrue(result["dry_run"])
        self.assertEqual(result["before"], result["after"])
        self.assertIn({"stage": "validation", "state": "passed"}, result["steps"])
        tool.assert_not_called()
        self.assertEqual(self.client.request.call_count, 1)
        self.assertEqual(self.client.request.call_args.args[1], result)

    def test_dry_run_validation_failure_remains_failed(self):
        op = operation("tetragon.mode")
        op["payload"] = {"policy_name": "runtime-tg-secret-01", "mode": "monitor"}
        op["expected"]["bundle_id"] = "changed"
        with patch.object(self.agent, "current", return_value=(self.before, self.intent)), patch.object(self.agent, "mode_state", return_value=self.before), patch.object(self.agent, "tool") as tool:
            result = self.agent.process(op)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["code"], "STATE_CHANGED")
        self.assertTrue(result["dry_run"])
        self.assertIn({"stage": "validation", "state": "failed"}, result["steps"])
        tool.assert_not_called()

    def test_mode_rollback_and_unverified(self):
        self.agent = Agent(self.client, replace(self.agent.config, execute=True))
        for rollback, expected in [(True, "rolled_back"), (False, "unknown")]:
            op = operation("tetragon.mode")
            op["payload"] = {"policy_name": "runtime-tg-secret-01", "mode": "monitor"}
            state = {**self.before, "policy_id": "7", "mode": "enforce", "spec_sha256": "a" * 64}
            op["expected"] = state
            helper = Mock()
            helper.switch_mode.return_value = {"ok": False, "rollback_ok": rollback, "reason": "never transmitted"}
            with patch.object(self.agent, "current", return_value=(self.before, self.intent)), patch.object(self.agent, "mode_state", return_value=state), patch.object(self.agent, "tool", return_value=helper):
                result = self.agent.process(op)
            self.assertEqual(result["state"], expected)
            self.assertNotIn("never transmitted", json.dumps(result))

    def test_enforce_without_local_proof_rejected(self):
        op = operation("tetragon.mode")
        op["payload"] = {"policy_name": "runtime-tg-secret-01", "mode": "enforce", "confirmation": "ENGINE_RESTART"}
        op.update(approval_required=True, approvals=[{"token_id": "approver-01", "role": "approver"}])
        state = {**self.before, "policy_id": "7", "mode": "monitor", "spec_sha256": "a" * 64}
        op["expected"] = state
        with patch.object(self.agent, "mode_state", return_value=state), patch.object(self.agent, "live_safety", return_value={}):
            with self.assertRaisesRegex(Rejected, "SAFETY_REJECTED"):
                self.agent.validate(op, self.before, self.intent)

    def test_private_token_and_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "agent-token"
            path.write_text("test-only-token")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                PlatformClient("http://127.0.0.1:1234", str(path))
            path.chmod(0o600)
            client = PlatformClient("https://platform.invalid", str(path))
            self.assertNotIn("test-only-token", repr(client))
            with self.assertRaises(ValueError):
                PlatformClient("http://platform.invalid", str(path))

    def test_lock_excludes_second_agent(self):
        with tempfile.TemporaryDirectory() as tmp, locked(Path(tmp) / "lock"):
            with self.assertRaises(BlockingIOError):
                with locked(Path(tmp) / "lock"):
                    pass

    def test_archive_hash_and_traversal_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(Rejected, "HASH_MISMATCH"):
                self.agent.unpack(b"bad", {"bundle_id": "bundle", "archive_sha256": "a" * 64}, tmp)
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as output:
                member = tarfile.TarInfo("bundle/../../escape")
                member.size = 1
                output.addfile(member, io.BytesIO(b"x"))
            raw = archive.getvalue()
            with self.assertRaisesRegex(Rejected, "VALIDATION_FAILED"):
                self.agent.unpack(raw, {"bundle_id": "bundle", "archive_sha256": hashlib.sha256(raw).hexdigest()}, tmp)

    def test_linked_result_failure_not_replayed(self):
        self.client.request.side_effect = [
            {"items": [{"id": "a", "state": "unknown"}], "monitor_seconds": 86400}, {},
        ]
        with patch.object(self.agent, "current", side_effect=ValueError("private path")), patch.object(self.agent, "process") as process:
            self.agent.once()
        process.assert_not_called()


class CurrentGatewayTest(unittest.TestCase):
    def setUp(self):
        # Build real four-role inputs; only Kubernetes/HTTP transport is doubled.
        from tests.test_bundle_tools import BundleToolsTest
        fixture = BundleToolsTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.bundle, _ = fixture._build("vllm")
        self.root = fixture.out
        (self.root / "current").symlink_to("vllm")
        target = json.loads(Path(self.bundle.role_paths()["target"]).read_text())["kubernetes"]
        self.agent = Agent(Mock(), Config("server-01", str(self.root), target["context"], target["namespace"], kubeconfig="/trusted/config"))
        self.status = {"policy_bundle": {"bundle_id": "vllm", "sha256": self.bundle.sha256},
                       "policy": {"approval": {"state": "verified"}, "gateway_active": True}}

    def test_default_ignores_unrelated_fixed_gateway_and_reopens_forward(self):
        def status(url, token):
            if url == "http://127.0.0.1:8080":
                return {"policy_bundle": {"bundle_id": "other-gateway"}}
            if url.endswith(":19001"):
                raise URLError("old Pod; private endpoint")
            return self.status
        with patch("serving_runtime.bundle_tools._port_forward", side_effect=[
                nullcontext("http://127.0.0.1:19001"), nullcontext("http://127.0.0.1:19002"),
                nullcontext("http://127.0.0.1:19003")]) as forward, \
             patch("serving_runtime.bundle_tools._status", side_effect=status) as lookup, \
             patch("serving_runtime.bundle_tools.time.sleep"):
            self.assertEqual(self.agent.current()[0]["bundle_id"], "vllm")
            self.assertEqual(self.agent.current()[0]["bundle_id"], "vllm")
        self.assertEqual(forward.call_count, 3)
        forward.assert_called_with(self.agent.config.context, self.agent.config.namespace, "/trusted/config", 8080)
        self.assertNotIn("http://127.0.0.1:8080", [c.args[0] for c in lookup.call_args_list])

    def test_explicit_local_url_is_opt_in(self):
        agent = Agent(Mock(), replace(self.agent.config, status_url="http://127.0.0.1:19099"))
        with patch("serving_runtime.bundle_tools._port_forward") as forward, \
             patch("serving_runtime.bundle_tools._status", return_value=self.status) as lookup:
            agent.current()
        forward.assert_not_called()
        lookup.assert_called_once_with("http://127.0.0.1:19099", None)

    def test_reason_codes_and_sanitized_report(self):
        with patch("serving_runtime.bundle_tools._gateway_status", side_effect=StatusUnavailable("private token /secret/path")):
            report = self.agent.process(operation())
        self.assertEqual(report["code"], "VALIDATION_FAILED")
        self.assertEqual(report["reason_code"], "STATUS_UNREACHABLE")
        self.assertNotIn("/secret/path", json.dumps(report))
        with patch("serving_runtime.bundle_tools._gateway_status", return_value={**self.status, "policy_bundle": {"bundle_id": "other"}}):
            report = self.agent.process(operation())
        self.assertEqual((report["code"], report["reason_code"]), ("STATE_CHANGED", "STATUS_MISMATCH"))
        manifest_path = self.root / "vllm/bundle.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"] = [f for f in manifest["files"] if f["role"] in {"gateway_policy", "policy_approvals"}]
        manifest_path.write_text(json.dumps(manifest))
        report = self.agent.process(operation())
        self.assertEqual(report["reason_code"], "BUNDLE_ROLES_MISSING")
        self.assertIsNone(report["before"])
        (self.root / "current").unlink()
        (self.root / "current").symlink_to("../private-path")
        report = self.agent.process(operation())
        self.assertEqual(report["reason_code"], "CURRENT_LINK_INVALID")
        self.assertNotIn("private-path", json.dumps(report))

    def test_inventory_omits_links_invalid_and_wrong_target(self):
        self.agent.current = Mock(return_value=({"bundle_id": "vllm"}, None))
        (self.root / "linked-copy").symlink_to("vllm")
        (self.root / "broken").mkdir()
        self.assertEqual(self.agent.available_bundles(), [{"bundle_id": "vllm", "sha256": self.bundle.sha256}])
        wrong = Agent(Mock(), replace(self.agent.config, namespace="another-namespace"))
        self.assertEqual(wrong.available_bundles(), [])

    def test_invalid_bundle_is_diagnosed_without_paths(self):
        (self.root / "vllm/runtime-policy.json").write_text("invalid")
        report = self.agent.process(operation())
        self.assertEqual(report["reason_code"], "BUNDLE_INVALID")
        self.assertNotIn(str(self.root), json.dumps(report))

    def test_after_execution_status_failure_preserves_unknown_and_reason(self):
        agent = Agent(Mock(), replace(self.agent.config, execute=True))
        before = {"bundle_id": "old", "approval": "verified", "policy_id": None, "mode": None, "spec_sha256": None}
        with patch.object(agent, "current", side_effect=[(before, None)] * 3 + [Rejected("VALIDATION_FAILED", reason_code="STATUS_UNREACHABLE")]), \
             patch.object(agent, "verified_bundle", return_value=self.bundle), \
             patch.object(agent, "check_target"), patch.object(agent, "execute_bundle"), \
             patch("serving_runtime.bundle_tools.apply_bundle"):
            report = agent.process(operation())
        self.assertEqual((report["state"], report["code"], report["reason_code"]), ("unknown", "STATUS_UNKNOWN", "STATUS_UNREACHABLE"))

    def test_unreachable_retries_have_finite_timeout(self):
        from serving_runtime.bundle_tools import _gateway_status, StatusUnavailable
        with patch("serving_runtime.bundle_tools._port_forward", side_effect=OSError("private")) as forward, \
             patch("serving_runtime.bundle_tools.time.monotonic", side_effect=[0, 0, 0, 1]), \
             patch("serving_runtime.bundle_tools.time.sleep"):
            with self.assertRaises(StatusUnavailable):
                _gateway_status("ctx", "ns", None, 8080, None, timeout=1)
        self.assertEqual(forward.call_count, 2)


class AllowedModesTest(unittest.TestCase):
    def test_legacy_bytes_and_new_monitor_capable_spec(self):
        old = document()
        baseline = json.dumps(render_manifests(parse_intent(old)), sort_keys=True).encode()
        old["tetragon_allowed_modes"] = {"TG-SECRET-01": ["monitor"]}
        self.assertEqual(baseline, json.dumps(render_manifests(parse_intent(old)), sort_keys=True).encode())
        intent = parse_intent(intent_document())
        spec = render_manifests(intent)["tetragon-tg-secret-01.json"]["spec"]
        self.assertEqual(spec["options"], [{"name": "policy-mode", "value": "monitor"}])
        self.assertIn({"action": "Sigkill"}, spec["kprobes"][0]["selectors"][0]["matchActions"])
        self.assertEqual(intent.allowed_modes("TG-SECRET-01"), ("monitor", "enforce"))

    def test_conflicting_or_v10_or_invalid_modes(self):
        for update in [{"tetragon_rule_modes": {"TG-SECRET-01": "monitor"}}, {"schema": "runtime-enforcement-intent/1.0"},
                       {"tetragon_allowed_modes": {"TG-SECRET-01": ["enforce"]}},
                       {"tetragon_allowed_modes": {"TG-ASSET-01": ["monitor", "enforce"]}}]:
            value = {**intent_document(), **update}
            with self.assertRaises(IntentError):
                parse_intent(value)


class SafetyTest(unittest.TestCase):
    def setUp(self):
        self.intent = parse_intent(intent_document())
        self.time = datetime.now(timezone.utc)
        self.name = "runtime-tg-secret-01"
        self.snapshot = {"fresh": True, "state": "ok", "generated_at": self.time.isoformat(),
            "policies": [{"name": self.name, "namespace": self.intent.namespace, "node": "node-01", "state": "enabled", "mode": "monitor", "id": "7", "method": "override_lsm", "spec_sha256": "a" * 64}],
            "capabilities": [{"node": "node-01", "kernel": "test-kernel", "available": ["override_lsm"]}],
            "verifications": [{"data_origin": "real", "node": "node-01", "method": "override_lsm", "policy_spec_sha256": "a" * 64, "kernel": "test-kernel", "verification": {"status": "passed"}, "source_report_sha256": "c" * 64}],
            "monitoring": {"state": "healthy", **dict.fromkeys(("policies_verified", "writer_verified", "identities_verified", "events_verified"), True)}}
        self.window = {"policy": self.name, "node": "node-01", "method": "override_lsm", "spec_sha256": "a" * 64, "kernel": "test-kernel", "since": (self.time - timedelta(days=1)).isoformat(), "until": self.time.isoformat(), "continuous": True, "event_count": 0, "false_positive_count": 0, "events_sha256": "b" * 64}

    def evaluate(self):
        return evaluate(self.snapshot, self.name, self.intent, {"windows": [self.window]}, 86400, now=self.time)

    def test_all_checks_and_evidence_hashes(self):
        result = self.evaluate()
        self.assertTrue(all(result["checks"].values()), result)
        self.assertEqual(len(result["evidence_sha256"]), 2)
        self.assertEqual(result["capability_basis"], "static")
        self.assertIsNone(result["capability_reason"])

    def test_capability_static_or_live_trial_decision_table(self):
        cap = self.snapshot["capabilities"][0]
        proof = self.snapshot["verifications"][0]
        for state in ("available", "unknown", "unavailable"):
            for has_evidence in (False, True):
                with self.subTest(state=state, evidence=has_evidence):
                    cap.update({key: ["override_lsm"] if key == state else []
                                for key in ("available", "unknown", "unavailable")})
                    self.snapshot["verifications"] = [proof] if has_evidence else []
                    result = self.evaluate()
                    passed = state == "available" or (state == "unknown" and has_evidence)
                    self.assertEqual(result["checks"]["capability"], passed)
                    self.assertEqual(result["checks"]["evidence"], has_evidence)
                    self.assertEqual(result["capability_basis"],
                                     "static" if state == "available" else "live_trial" if passed else None)
                    self.assertEqual(result["capability_reason"], None if passed else
                                     "static_unavailable" if state == "unavailable" else "evidence_required")

    def test_unknown_requires_the_same_real_combined_evidence(self):
        self.snapshot["capabilities"][0].update(available=[], unknown=["override_lsm"])
        original = {**self.snapshot["verifications"][0], "variant": "combined"}
        for change in ({"node": "other"}, {"kernel": "other"}, {"policy_spec_sha256": "d" * 64},
                       {"method": "sigkill_fd_install"}, {"data_origin": "synthetic"},
                       {"variant": "override_only"}, {"verification": {"status": "failed"}},
                       {"source_report_sha256": "invalid"}):
            with self.subTest(change=change):
                self.snapshot["verifications"] = [{**original, **change}]
                result = self.evaluate()
                self.assertFalse(result["checks"]["evidence"])
                self.assertFalse(result["checks"]["capability"])
                self.assertEqual(result["capability_reason"], "evidence_required")

    def test_unavailable_wins_over_conflicting_available_and_unknown(self):
        self.snapshot["capabilities"][0].update(unknown=["override_lsm"], unavailable=["override_lsm"])
        result = self.evaluate()
        self.assertTrue(result["checks"]["evidence"])
        self.assertFalse(result["checks"]["capability"])
        self.assertEqual(result["capability_reason"], "static_unavailable")

    def test_kind_state_shape_with_unreadable_injection_list_and_sigkill_trial(self):
        from tools.tetragon_capability import classify_capabilities
        # 실제 kind state의 분류·배열 구조를 재현하며 개인 로컬 파일에는 의존하지 않는다.
        node, kernel = "runtime-lab-control-plane", "6.18.33.2-microsoft-standard-WSL2"
        cap = classify_capabilities(kernel=kernel, config="CONFIG_BPF_LSM=y\n", lsm=None,
                                    injection_list=None, probe="override_return: false\nfmodret: true\nlsm: false")
        cap.update(node=node, checked_at=self.time.isoformat())
        self.assertFalse(cap["error_injection_list_readable"])
        self.assertEqual(cap["unknown"], ["override_lsm", "sigkill_fd_install"])
        self.snapshot.update(schema_version="runtime-tetragon-state/1.0", context="kind-runtime-lab",
                             error=None, agents=[], operation_checks=[], transitions=[], capabilities=[cap])
        policy = self.snapshot["policies"][0]
        proof = self.snapshot["verifications"][0]
        for method in cap["unknown"]:
            with self.subTest(method=method):
                policy.update(node=node, method=method)
                proof.update(node=node, kernel=kernel, method=method, variant="combined",
                             schema_version="runtime-tetragon-verification/1.0")
                self.window.update(node=node, kernel=kernel, method=method)
                # 같은 state에 별도 monitor_only 정책과 무관한 시험 기록도 함께 있다.
                self.snapshot["policies"] = [policy, {**policy, "name": "another-policy", "mode": "monitor_only"}]
                self.snapshot["verifications"] = [{**proof, "kernel": "old-kernel"}, proof]
                self.snapshot = json.loads(json.dumps(self.snapshot))
                result = self.evaluate()
                self.assertTrue(all(result["checks"].values()), result)
                self.assertEqual(result["capability_basis"], "live_trial")

    def test_d7_kernel_spec_method_mismatch_and_collection(self):
        for field in ("kernel", "policy_spec_sha256", "method"):
            old = self.snapshot["verifications"][0][field]
            self.snapshot["verifications"][0][field] = "changed"
            self.assertFalse(self.evaluate()["checks"]["evidence"])
            self.snapshot["verifications"][0][field] = old
        for key in ("policies_verified", "writer_verified", "identities_verified", "events_verified"):
            self.snapshot["monitoring"][key] = False
            self.assertFalse(self.evaluate()["checks"]["collection"])
            self.snapshot["monitoring"][key] = True

    def test_individual_review_and_duration(self):
        self.window["event_count"] = 2
        self.assertFalse(self.evaluate()["checks"]["monitor_period"])
        self.window.update(reviewed_event_count=2, reviewer_token_id="reviewer-01")
        self.assertTrue(self.evaluate()["checks"]["monitor_period"])
        self.window["false_positive_count"] = 1
        self.assertFalse(self.evaluate()["checks"]["monitor_period"])
        self.window.update(event_count=0, false_positive_count=0, since=self.time.isoformat())
        self.assertFalse(self.evaluate()["checks"]["monitor_period"])

    def test_stale_disabled_monitor_only_and_capability_unknown(self):
        self.snapshot["fresh"] = False
        self.assertFalse(self.evaluate()["checks"]["collection"])
        self.snapshot["policies"][0]["mode"] = "monitor_only"
        self.assertFalse(self.evaluate()["checks"]["policy"])
        self.snapshot["capabilities"][0]["available"] = []
        self.assertFalse(self.evaluate()["checks"]["capability"])


class TransportFailureTest(unittest.TestCase):
    def test_disconnected_result_does_not_repeat_execution(self):
        client = Mock()
        agent = Agent(client, Config("server-01", "/unused", "test", "test", execute=True))
        before = {"bundle_id": "old", "approval": "verified", "policy_id": None, "mode": None, "spec_sha256": None}
        state = {**before, "policy_id": "7", "mode": "enforce", "spec_sha256": "a" * 64}
        intent = parse_intent(intent_document())
        op = operation("tetragon.mode")
        op["expected"] = state
        op["payload"] = {"policy_name": "runtime-tg-secret-01", "mode": "monitor"}
        helper = Mock()
        helper.switch_mode.return_value = {"ok": True}
        client.request.side_effect = [{}, OSError("private endpoint")]
        with patch.object(agent, "current", return_value=(before, intent)), patch.object(agent, "mode_state", return_value=state), patch.object(agent, "tool", return_value=helper):
            with self.assertRaises(OSError):
                agent.process(op)
            self.assertEqual(helper.switch_mode.call_count, 1)
            client.request.side_effect = [{"items": [{**op, "state": "running"}], "monitor_seconds": 86400}, {}]
            self.assertIsNone(agent.once())
            self.assertEqual(helper.switch_mode.call_count, 1)


class PrivilegedApplyTest(unittest.TestCase):
    def test_privileged_restore_is_reverified_and_output_bounded(self):
        from serving_runtime.operations.agent import RestoreReported
        from serving_runtime.cli import main
        from contextlib import redirect_stdout
        agent = Agent(Mock(), Config("server-01", "/fixed-root", "test", "test", execute=True, sudo_bundle_apply=True))
        process = Mock(returncode=1, stdout='{"state":"rolled_back","code":"ROLLED_BACK"}')
        with patch("serving_runtime.operations.agent.subprocess.run", return_value=process) as run:
            with self.assertRaises(RestoreReported):
                agent.execute_bundle(None, "previous")
        command = run.call_args.args[0]
        self.assertIn("--operation-result", command)
        self.assertEqual(command[:2], ["sudo", "-n"])
        output = io.StringIO()
        with patch("serving_runtime.bundle_tools.apply_bundle", side_effect=RuntimeError("이전 링크 복원 및 재시작 완료: private diagnostic")), redirect_stdout(output):
            code = main(["bundle", "apply", "--bundle-root", "/fixed-root", "--context", "test", "--namespace", "test", "--status-url", "http://127.0.0.1:8080", "--existing", "previous", "--operation-result"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue()), {"state": "rolled_back", "code": "ROLLED_BACK"})
        self.assertNotIn("private", output.getvalue())
