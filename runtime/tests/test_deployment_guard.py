import copy
from datetime import datetime, timezone, timedelta
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import replace
from serving_runtime.bundle_tools import StatusMismatch, StatusUnavailable, _checked_status, _normal_canary
from contextlib import nullcontext
try:
    from guard.models.enums import Verdict
    from guard.models.objects import Environment, Evidence, Result, Plan
except ImportError:
    Verdict = None

from serving_runtime.mvp.deployment_guard import verify_deployment_guard
from serving_runtime.mvp.policy_bundle import VerifiedPolicyBundle, VerifiedBundleFile
from serving_runtime.bundle_tools import apply_bundle

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
SHA = "a" * 64


@unittest.skipIf(Verdict is None, "UPDATE package is required for the deployment guard profile")
class DeploymentGuardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = {"schema_version": "runtime-target/1.0", "target_id": "engine", "engine": {"kind": "vllm", "version": "0.31.0", "image_digest": "sha256:" + SHA}, "model": {"id": "m", "revision": "revision1"}, "kubernetes": {"context": "context", "namespace": "namespace", "container": "engine", "selector": {"app": "engine"}}}
        self.policy = {"schema_version": "runtime-mvp/1.1", "policy_id": "p", "policy_version": "1", "target_id": "engine", "approval_ref": "approval1", "allowed_models": ["m"], "output_default_tokens": 8, "output_max_tokens": 16, "max_body_bytes": 4096}
        files = []
        for role, doc in (("target", self.target), ("gateway_policy", self.policy)):
            raw = json.dumps(doc).encode()
            files.append(VerifiedBundleFile(role, role + ".json", str(self.root / role), doc["schema_version"], hashlib.sha256(raw).hexdigest(), len(raw), raw))
        self.bundle = VerifiedPolicyBundle("test", "b" * 64, {"policy_id": "p", "policy_version": "1", "sha256": "c" * 64}, "engine", tuple(files), NOW.isoformat())
        self.approvals = self.root / "approvals.json"
        self.approvals.write_text(json.dumps({"schema_version": "runtime-policy-approvals/1.0", "entries": [{"policy_id": "p", "policy_version": "1", "sha256": files[1].sha256, "approval_ref": "approval1", "approved_at": (NOW - timedelta(days=1)).isoformat(), "valid_until": (NOW + timedelta(days=1)).isoformat()}]}))
        common = {"created_at": NOW, "data_origin": "MEASURED"}
        self.env = Environment(id="env1", engine={"name": "vllm", "version": "0.31.0", "image_digest": "sha256:" + SHA}, model={"id": "m", "revision": "revision1", "inventory_sha256": "d" * 64}, **common).seal()
        self.pack_ref = {"kind": "PACK", "id": "PACK-BASELINE", "revision": 33, "sha256": "e" * 64}
        self.plan = Plan(id="plan1", mode="audit", inputs={"pack": self.pack_ref, "environments": [self.env.ref()], "policy": {"kind": "POL", "id": "p", "revision": 1, "sha256": "f" * 64}}, items=[{"check_id": "SUP-03", "check_revision": 1, "applicability": "APPLICABLE", "reason": "required", "selected": True, "priority": "P0", "executor": "existing"}], inventory_complete={}, **common).seal()
        self.evd = Evidence(id="evd1", plan=self.plan.ref(), check_id="SUP-03", side="candidate", environment=self.env.ref(), execution={"status": "COMPLETED", "executor": {"name": "existing", "version": "1", "config_sha256": SHA}, "started_at": NOW - timedelta(minutes=5), "finished_at": NOW - timedelta(minutes=1)}, normal_control={"attempted": True, "succeeded": True}, **common).seal()
        self.res = Result(id="res1", evidence=self.evd.ref(), check_id="SUP-03", side="candidate", judge={"name": "existing", "version": "1", "params_sha256": SHA}, verdicts={"security": "PASS", "functional": "PASS", "performance": "UNKNOWN"}, verification_level="MEASURED", **common).seal()
        self.check = {"result_path": str(self.root / "res.json"), "result_ref": self.res.ref().model_dump(mode="json"), "evidence_path": str(self.root / "evd.json"), "environment_path": str(self.root / "env.json"), "plan_path": str(self.root / "plan.json")}
        self.guard = {"schema_version": "runtime-deployment-guard/1.0", "approvals_path": str(self.approvals), "pack_ref": self.pack_ref, "required_checks": {"CHK-SUP-003": "SUP-03"}, "max_evidence_age_s": 3600,
                      "records": [{"bundle_sha256": self.bundle.sha256, "target_sha256": files[0].sha256, "model_inventory_sha256": "d" * 64, "valid_from": (NOW - timedelta(days=1)).isoformat(), "valid_until": (NOW + timedelta(days=1)).isoformat(), "revoked": False, "checks": {"CHK-SUP-003": self.check}, "artifact_root": str(self.root)}]}
        self.path = self.root / "guard.json"
        self.save()

    def save(self):
        for name, obj in (("res", self.res), ("evd", self.evd), ("env", self.env), ("plan", self.plan)):
            (self.root / (name + ".json")).write_text(json.dumps(obj.to_dict()))
        self.path.write_text(json.dumps(self.guard))

    def verify(self):
        return verify_deployment_guard(self.bundle, str(self.path), now=NOW)

    def test_existing_sealed_refs_and_current_approval_pass(self):
        self.assertEqual(self.verify()["state"], "verified")
        self.assertEqual(self.verify()["results"]["CHK-SUP-003"], self.check["result_ref"])

    def test_missing_revoked_expired_target_and_pack_mismatch(self):
        original = copy.deepcopy(self.guard)
        changes = [lambda g: g["records"][0].update(revoked=True),
                   lambda g: g["records"][0].update(valid_from=None),
                   lambda g: g["records"][0].update(artifact_root=None),
                   lambda g: g["records"][0].update(model_inventory_sha256="invalid"),
                   lambda g: g["records"][0].update(valid_until=NOW.isoformat()),
                   lambda g: g["records"][0].update(target_sha256="0" * 64),
                   lambda g: g.update(required_checks={"CHK-SUP-014": "SUP-14"}),
                   lambda g: g["pack_ref"].update(revision=32),
                   lambda g: g["records"][0]["checks"]["CHK-SUP-003"]["result_ref"].update(revision=2)]
        for mutate in changes:
            self.guard = copy.deepcopy(original)
            mutate(self.guard)
            self.save()
            with self.assertRaises(ValueError):
                self.verify()

    def test_fail_unknown_stale_wrong_engine_and_tampered_evidence_rejected(self):
        for verdict in ("FAIL", "UNKNOWN"):
            self.res = self.res.model_copy(update={"verdicts": self.res.verdicts.model_copy(update={"security": Verdict(verdict)})}).seal()
            self.check["result_ref"] = self.res.ref().model_dump(mode="json")
            self.save()
            with self.assertRaisesRegex(ValueError, "PASS unavailable"):
                self.verify()
        # Restore full setup to isolate the stale interval from verdict checks.
        self.setUp()
        self.guard["max_evidence_age_s"] = 1
        self.save()
        with self.assertRaisesRegex(ValueError, "stale"):
            self.verify()
        self.guard["max_evidence_age_s"] = 3600
        self.save()
        raw = json.loads((self.root / "evd.json").read_text())
        raw["execution"]["status"] = "ABORTED"
        (self.root / "evd.json").write_text(json.dumps(raw))
        with self.assertRaises(ValueError):
            self.verify()

    def test_current_approval_removal_and_unreadable_source_never_fallback(self):
        self.verify()
        self.approvals.write_text('{"schema_version":"runtime-policy-approvals/1.0","entries":[]}')
        with self.assertRaisesRegex(ValueError, "current external"):
            self.verify()
        self.approvals.unlink()
        with self.assertRaisesRegex(ValueError, "current external"):
            self.verify()

    def apply_fixture(self):
        for name in ("test", "previous"):
            (self.root / name).mkdir()
        (self.root / "current").symlink_to("previous")
        previous = replace(self.bundle, bundle_id="previous", sha256="9" * 64)
        def verified(path):
            return previous if Path(path).name == "previous" else self.bundle
        return previous, verified

    def apply(self):
        return apply_bundle(None, str(self.root), "context", "namespace", "http://127.0.0.1:9000", None,
                            existing="test", deployment_guard_path=str(self.path))

    def test_apply_fails_before_switch_when_current_evidence_unavailable(self):
        previous, verified = self.apply_fixture()
        with patch("serving_runtime.bundle_tools.verify_policy_bundle", side_effect=verified), \
             patch("serving_runtime.bundle_tools._check_deployed_permissions"), \
             patch("serving_runtime.mvp.deployment_guard.verify_deployment_guard", side_effect=ValueError("revoked")), \
             patch("serving_runtime.bundle_tools._run") as run:
            with self.assertRaisesRegex(ValueError, "revoked"):
                self.apply()
            run.assert_not_called()
        self.assertEqual((self.root / "current").readlink(), Path("previous"))

    def test_rollback_rechecks_current_authority_and_does_not_revive_revoked_previous(self):
        previous, verified = self.apply_fixture()
        guarded = []
        def authority(bundle, path):
            guarded.append(bundle.bundle_id)
            if bundle.bundle_id == "previous":
                raise ValueError("previous now revoked")
            return {"state": "verified"}
        with patch("serving_runtime.bundle_tools.verify_policy_bundle", side_effect=verified), \
             patch("serving_runtime.bundle_tools._check_deployed_permissions"), \
             patch("serving_runtime.mvp.deployment_guard.verify_deployment_guard", side_effect=authority), \
             patch("serving_runtime.bundle_tools._run") as run, \
             patch("serving_runtime.bundle_tools._checked_status", side_effect=StatusMismatch("wrong")):
            with self.assertRaisesRegex(RuntimeError, "previous now revoked"):
                self.apply()
            self.assertEqual(run.call_count, 2)
        self.assertEqual(guarded, ["test", "test", "previous"])
        self.assertEqual((self.root / "current").readlink(), Path("test"))

    def test_approved_rollback_checks_final_bundle_status(self):
        previous, verified = self.apply_fixture()
        with patch("serving_runtime.bundle_tools.verify_policy_bundle", side_effect=verified), \
             patch("serving_runtime.bundle_tools._check_deployed_permissions"), \
             patch("serving_runtime.mvp.deployment_guard.verify_deployment_guard", return_value={"state": "verified"}), \
             patch("serving_runtime.bundle_tools._run") as run, \
             patch("serving_runtime.bundle_tools._checked_status", side_effect=[StatusMismatch("wrong"), None]) as status:
            with self.assertRaisesRegex(RuntimeError, "이전 링크 복원 및 재시작 완료"):
                self.apply()
            self.assertEqual(run.call_count, 4)
            self.assertEqual(status.call_args.args[-1], "previous")
        self.assertEqual((self.root / "current").readlink(), Path("previous"))

    def test_evidence_artifact_bytes_and_engine_version_are_checked(self):
        artifact = self.root / "raw.txt"
        artifact.write_bytes(b"evidence")
        raw = self.evd.to_dict()
        raw["artifacts"] = [{"name": "raw", "path": "raw.txt", "sha256": hashlib.sha256(b"evidence").hexdigest(), "bytes": 8, "collected_at": NOW.isoformat(), "collector": "existing"}]
        self.evd = Evidence.model_validate(raw).seal()
        self.res = self.res.model_copy(update={"evidence": self.evd.ref()}).seal()
        self.check["result_ref"] = self.res.ref().model_dump(mode="json")
        self.save()
        self.assertEqual(self.verify()["state"], "verified")
        artifact.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "artifact missing or changed"):
            self.verify()
        artifact.write_bytes(b"evidence")
        target = copy.deepcopy(self.target)
        target["engine"]["version"] = "other"
        raw_target = json.dumps(target).encode()
        target_file = replace(self.bundle.files[0], content=raw_target, sha256=hashlib.sha256(raw_target).hexdigest(), bytes=len(raw_target))
        self.bundle = replace(self.bundle, files=(target_file, self.bundle.files[1]))
        self.guard["records"][0]["target_sha256"] = target_file.sha256
        self.save()
        with self.assertRaisesRegex(ValueError, "tested engine/model/version mismatch"):
            self.verify()

    def test_required_plan_must_be_applicable_and_have_the_measured_executor(self):
        original_plan = self.plan
        original_evd = self.evd
        for change in ({"executor": None}, {"applicability": "NOT_APPLICABLE"}, {"executor": "different-executor"}):
            raw = original_plan.to_dict()
            raw["items"][0].update(change)
            self.plan = Plan.model_validate(raw).seal()
            self.evd = original_evd.model_copy(update={"plan": self.plan.ref()}).seal()
            self.res = self.res.model_copy(update={"evidence": self.evd.ref()}).seal()
            self.check["result_ref"] = self.res.ref().model_dump(mode="json")
            self.save()
            with self.assertRaisesRegex(ValueError, "selected executable plan item missing"):
                self.verify()

    def test_apply_context_and_namespace_match_approved_target_before_mutation(self):
        previous, verified = self.apply_fixture()
        for context, namespace in (("other-context", "namespace"), ("context", "other-namespace")):
            with patch("serving_runtime.bundle_tools.verify_policy_bundle", side_effect=verified), \
                 patch("serving_runtime.bundle_tools._check_deployed_permissions"), \
                 patch("serving_runtime.bundle_tools._run") as run:
                with self.assertRaisesRegex(ValueError, "context/namespace"):
                    apply_bundle(None, str(self.root), context, namespace, "http://127.0.0.1:9000", None,
                                 existing="test", deployment_guard_path=str(self.path))
                run.assert_not_called()
            self.assertEqual((self.root / "current").readlink(), Path("previous"))

    def test_checked_status_requires_current_target_verification_for_guarded_apply(self):
        status = {"policy_bundle": {"bundle_id": "test"}, "policy": {"approval": {"state": "verified"}, "gateway_active": True},
                  "target": {"target_id": "engine", "registration": "ok", "gateway_hold": None, "verification": {"state": "ok"}}, "backend": {"healthy": True}}
        def checked(document):
            with patch("serving_runtime.bundle_tools._port_forward", return_value=nullcontext("http://127.0.0.1:9000")), \
                 patch("serving_runtime.bundle_tools._status", return_value=document):
                _checked_status("context", "namespace", None, 9000, None, "test", timeout=0, expected_target_id="engine")
        checked(status)
        wrong = copy.deepcopy(status)
        wrong["target"]["target_id"] = "another"
        with self.assertRaises(StatusMismatch):
            checked(wrong)
        unknown = copy.deepcopy(status)
        unknown["target"]["verification"]["state"] = "unavailable"
        with self.assertRaises(StatusUnavailable):
            checked(unknown)

    def test_canary_uses_inference_auth_and_checks_bounded_chat_response(self):
        class Response(io.BytesIO):
            status = 200
            headers = {"X-Request-ID": "request-123"}
        normal = json.dumps({"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}).encode()
        def serve(request, timeout):
            self.assertEqual(request.full_url, "http://127.0.0.1:9000/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer inference-secret")
            self.assertEqual(json.loads(request.data)["model"], "m")
            self.assertEqual(json.loads(request.data)["max_tokens"], 8)
            return Response(normal)
        with patch("serving_runtime.bundle_tools.urlopen", side_effect=serve):
            result = _normal_canary("http://127.0.0.1:9000", "inference-secret", "m", 8)
        self.assertEqual(result["request_id"], "request-123")
        self.assertNotIn("inference-secret", json.dumps(result))
        for bad in (b"{}", b"x" * (1024 * 1024 + 1), json.dumps({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}).encode()):
            with patch("serving_runtime.bundle_tools.urlopen", return_value=Response(bad)):
                with self.assertRaises(StatusMismatch):
                    _normal_canary("http://127.0.0.1:9000", None, "m", 8)

    def test_checked_status_matches_exact_bundle_before_sending_canary(self):
        document = {"policy_bundle": {"bundle_id": "test", "sha256": "b" * 64},
                    "policy": {"approval": {"state": "verified"}, "gateway_active": True},
                    "target": {"target_id": "engine", "registration": "ok", "gateway_hold": None, "verification": {"state": "ok"}},
                    "backend": {"healthy": True}}
        with patch("serving_runtime.bundle_tools._port_forward", return_value=nullcontext("http://127.0.0.1:9000")), \
             patch("serving_runtime.bundle_tools._status", return_value=document), \
             patch("serving_runtime.bundle_tools._normal_canary", return_value={"state": "verified"}) as canary:
            result = _checked_status("context", "namespace", None, 9000, None, "test", timeout=0,
                                     expected_target_id="engine", expected_bundle_sha256="b" * 64, canary_model="m")
            self.assertEqual(result["canary"]["state"], "verified")
            self.assertEqual(canary.call_count, 1)
            document["policy_bundle"]["sha256"] = "wrong"
            with self.assertRaises(StatusMismatch):
                _checked_status("context", "namespace", None, 9000, None, "test", timeout=0,
                                expected_target_id="engine", expected_bundle_sha256="b" * 64, canary_model="m")
            self.assertEqual(canary.call_count, 1)


class NormalCanaryGatewayTest(unittest.TestCase):
    def test_canary_runs_through_real_gateway_and_mock_engine_http(self):
        from serving_runtime.mvp.app import GatewayApp
        from serving_runtime.mvp.config import MVPConfig
        from serving_runtime.mvp.mock_engine import MockEngine
        from serving_runtime.mvp.policy import load_policy
        root = Path(__file__).resolve().parents[1]
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        data = Path(temporary.name)
        try:
            engine = MockEngine(port=0, receipts_path=data / "engine_receipts.jsonl").start()
        except PermissionError as error:
            self.skipTest(f"local sockets unavailable: {error}")
        self.addCleanup(engine.stop)
        policy = root / "examples/mvp/policy.json"
        config = MVPConfig(gateway_port=0, engine_port=0, policy_path=str(policy), data_dir=str(data), relay_timeout_s=2)
        gateway = GatewayApp(config, load_policy(policy), engine.address).start()
        self.addCleanup(gateway.stop)
        host, port = gateway.address
        result = _normal_canary(f"http://{host}:{port}", None, "test-model", 8)
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["model"], "test-model")
        self.assertTrue(result["request_id"])
        self.assertEqual(len(result["response_sha256"]), 64)
