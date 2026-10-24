"""Tests for enforcement intent 1.1: SP4 task roles, SP1 assets/cache, SP3 phases."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from serving_runtime.enforcement import cilium, tetragon
from serving_runtime.enforcement.cache import check_cache
from serving_runtime.enforcement.events import parse_tetragon, transform_lines
from serving_runtime.enforcement.execpolicy import ExecPolicy, parse_exceptions, parse_timeline
from serving_runtime.enforcement.intent import IntentError, load_intent, parse_intent
from serving_runtime.enforcement.security import render_pod_security


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples/enforcement"
INTENT_V11 = EXAMPLES / "intent-v1.1.example.json"
INTENT_V10 = EXAMPLES / "intent.example.json"
EXCEPTIONS = EXAMPLES / "exec-exceptions.example.json"
TIMELINE = EXAMPLES / "phase-timeline.example.json"


def v11_document() -> dict:
    return json.loads(INTENT_V11.read_text(encoding="utf-8"))


def exec_event(binary: str, at: str, *, image: str | None = None, parent: str | None = None) -> dict:
    process: dict = {
        "binary": binary,
        "arguments": "--token super-secret-argument",
        "pod": {"namespace": "serving-lab", "name": "serving-engine-1"},
    }
    if image is not None:
        process["pod"]["container"] = {"image": {"id": f"docker.io/x@{image}"}}
    event: dict = {"process": process}
    if parent is not None:
        event["parent"] = {"binary": parent}
    return {"process_exec": event, "time": at}


class IntentV11ParsingTests(unittest.TestCase):
    def test_example_loads_and_v10_is_unchanged(self) -> None:
        intent = load_intent(INTENT_V11)
        self.assertEqual(intent.schema, "runtime-enforcement-intent/1.1")
        self.assertEqual(intent.engine.management_port, 8001)
        self.assertEqual(intent.acquire.labels, {"app": "artifact-acquire"})
        self.assertEqual(
            intent.assets.generation_path,
            "/var/cache/serving/vllm/image-example/revision-example/default",
        )
        self.assertEqual(intent.binaries_for("compiling"), ("/usr/bin/python3", "/usr/bin/gcc", "/usr/bin/ld"))
        legacy = load_intent(INTENT_V10)
        self.assertEqual(legacy.schema, "runtime-enforcement-intent/1.0")
        self.assertIsNone(legacy.acquire)
        self.assertIsNone(legacy.assets)

    def test_v11_fields_are_unknown_in_v10(self) -> None:
        base = json.loads(INTENT_V10.read_text(encoding="utf-8"))
        for field, value in (
            ("acquire", {"labels": {"app": "a"}}),
            ("assets", {}),
            ("phase_binaries", {}),
            ("suspect_binaries", []),
            ("test_receivers", []),
            ("observer_kube_api", True),
        ):
            with self.subTest(field=field):
                document = dict(base, **{field: value})
                with self.assertRaisesRegex(IntentError, "unknown fields"):
                    parse_intent(document)
        with self.assertRaisesRegex(IntentError, "unknown fields"):
            parse_intent(dict(base, engine={"labels": {"app": "e"}, "management_port": 9000}))

    def test_invalid_v11_documents(self) -> None:
        def mutate(change) -> dict:
            document = v11_document()
            change(document)
            return document

        cases = {
            "acquire-same-labels": lambda d: d["acquire"].update(labels={"app": "serving-engine"}),
            "engine-shares-acquire-destination": lambda d: d["engine_egress_allow"].append(
                {"fqdn": "artifacts.example.invalid", "port": 443, "protocol": "TCP"}
            ),
            "engine-shares-acquire-destination-case": lambda d: d["engine_egress_allow"].append(
                {"fqdn": "ARTIFACTS.EXAMPLE.INVALID.", "port": 443, "protocol": "TCP"}
            ),
            "engine-endpoint-egress": lambda d: d["engine_egress_allow"].append(
                {"endpoint": {"app": "x"}, "port": 1, "protocol": "TCP"}
            ),
            "acquire-two-targets": lambda d: d["acquire"]["egress_allow"].append(
                {"fqdn": "a.example.invalid", "cidr": "192.0.2.0/24", "port": 1, "protocol": "TCP"}
            ),
            "receiver-without-acquire": lambda d: d.pop("acquire"),
            "receiver-bad-role": lambda d: d["test_receivers"][0].update(allowed_from=["engine"]),
            "receiver-dup-name": lambda d: d["test_receivers"].append(dict(d["test_receivers"][0])),
            "receiver-invalid-name": lambda d: d["test_receivers"][0].update(name="Bad_Name"),
            "acquire-invalid-label": lambda d: d["acquire"].update(labels={"bad key": "value"}),
            "invalid-namespace": lambda d: d.update(namespace="Serving_Lab"),
            "mgmt-equals-port": lambda d: d["engine"].update(management_port=8000),
            "cache-dir-mismatch": lambda d: d["protected_paths"].update(cache_dir="/var/cache/serving/other"),
            "cache-under-model": lambda d: (
                d["assets"].update(cache_root="/opt/serving/assets/model/cache"),
                d["protected_paths"].update(cache_dir="/opt/serving/assets/model/cache/vllm/image-example/revision-example/default"),
            ),
            "manifest-mount-contains-cache": lambda d: (
                d["assets"].update(manifest_path="/var/cache/manifest.json"),
                d["assets"].update(cache_root="/var/cache/serving"),
            ),
            "overlapping-role-selectors": lambda d: d["acquire"].update(
                labels={"app": "serving-engine", "task": "acquire"}
            ),
            "unsafe-segment": lambda d: d["assets"]["cache_generation"].update(image_id="../escape"),
            "root-uid": lambda d: d["assets"].update(engine_uid=0),
            "shared-uid": lambda d: d["assets"].update(acquire_uid=10001),
            "bool-uid": lambda d: d["assets"].update(probe_uid=True),
            "unknown-phase": lambda d: d["phase_binaries"].update(serving=["/bin/x"]),
            "relative-binary": lambda d: d["phase_binaries"].update(loading=["python3"]),
            "dup-binary": lambda d: d["phase_binaries"].update(loading=["/a", "/a"]),
            "suspect-overlaps-allowed": lambda d: d["suspect_binaries"].append("/usr/bin/python3"),
            "kube-api-not-bool": lambda d: d.update(observer_kube_api="yes"),
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(IntentError):
                    parse_intent(mutate(change))


class RenderV11Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.intent = load_intent(INTENT_V11)
        self.cilium = cilium.render_manifests(self.intent)
        self.tetragon = tetragon.render_manifests(self.intent)

    def test_observers_use_only_the_management_listener(self) -> None:
        ingress = self.cilium["cilium-engine-ingress.json"]["spec"]["ingress"]
        self.assertEqual(len(ingress), 2)
        self.assertEqual(ingress[0]["fromEndpoints"], [{"matchLabels": {"app": "serving-gateway"}}])
        self.assertEqual(ingress[0]["toPorts"][0]["ports"][0]["port"], "8000")
        self.assertEqual(ingress[1]["fromEndpoints"], [{"matchLabels": {"app": "serving-observer"}}])
        self.assertEqual(ingress[1]["toPorts"][0]["ports"][0]["port"], "8001")
        observer = self.cilium["cilium-observer-0-egress.json"]["spec"]
        self.assertEqual(observer["endpointSelector"], {"matchLabels": {"app": "serving-observer"}})
        self.assertEqual(observer["egress"][0]["toPorts"][0]["ports"][0]["port"], "8001")
        self.assertNotIn("toEntities", json.dumps(observer))

    def test_acquire_reaches_artifacts_and_fixture_but_engine_does_not(self) -> None:
        acquire = self.cilium["cilium-acquire-egress.json"]["spec"]
        self.assertEqual(acquire["endpointSelector"], {"matchLabels": {"app": "artifact-acquire"}})
        text = json.dumps(acquire)
        self.assertIn("artifacts.example.invalid", text)
        self.assertIn('"test-receiver"', text)
        self.assertIn('"matchPattern": "*"', text)
        engine_text = json.dumps(self.cilium["cilium-engine-egress.json"])
        self.assertNotIn("artifacts.example.invalid", engine_text)
        self.assertNotIn("test-receiver", engine_text)
        receiver = self.cilium["cilium-receiver-fixture-ingress.json"]["spec"]
        self.assertEqual(receiver["ingress"][0]["fromEndpoints"], [{"matchLabels": {"app": "artifact-acquire"}}])

    def test_kube_api_entity_only_when_requested(self) -> None:
        document = v11_document()
        document["observer_kube_api"] = True
        manifests = cilium.render_manifests(parse_intent(document))
        self.assertIn({"toEntities": ["kube-apiserver"]}, manifests["cilium-observer-0-egress.json"]["spec"]["egress"])

    def test_tetragon_watches_tokenizer_and_cache_root(self) -> None:
        self.assertIn("tetragon-tg-asset-02.json", self.tetragon)
        def prefix(name: str) -> list[str]:
            return self.tetragon[name]["spec"]["kprobes"][0]["selectors"][0]["matchArgs"][0]["values"]
        self.assertEqual(prefix("tetragon-tg-asset-02.json"), ["/opt/serving/assets/tokenizer"])
        self.assertEqual(prefix("tetragon-tg-cache-01.json"), ["/var/cache/serving"])
        legacy = tetragon.render_manifests(load_intent(INTENT_V10))
        self.assertNotIn("tetragon-tg-asset-02.json", legacy)

    def test_pod_security_candidate(self) -> None:
        document = render_pod_security(self.intent)
        engine = document["engine"]
        self.assertEqual(engine["securityContext"]["runAsUser"], 10001)
        self.assertTrue(engine["securityContext"]["runAsNonRoot"])
        mounts = {item["name"]: item for item in engine["volumeMounts"]}
        self.assertTrue(mounts["serving-model"]["readOnly"])
        self.assertTrue(mounts["serving-tokenizer"]["readOnly"])
        self.assertTrue(mounts["serving-approved-manifest"]["readOnly"])
        self.assertFalse(mounts["serving-cache"]["readOnly"])
        self.assertEqual(mounts["serving-cache"]["subPath"], "vllm/image-example/revision-example/default")
        acquire_mounts = {item["name"]: item for item in document["acquire"]["volumeMounts"]}
        self.assertFalse(acquire_mounts["serving-model"]["readOnly"])
        self.assertEqual(document["acquire"]["securityContext"]["runAsUser"], 10002)
        self.assertIsNone(render_pod_security(load_intent(INTENT_V10)))


class CacheCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "cache"
        document = v11_document()
        document["assets"]["engine_uid"] = os.getuid() or 1
        self.intent = parse_intent(document)
        self.current = self.root / "vllm/image-example/revision-example/default"

    def check(self):
        return check_cache(self.intent, root_override=str(self.root))

    def test_states(self) -> None:
        self.assertEqual(self.check()["state"], "cache_root_missing")
        self.root.mkdir()
        self.assertEqual(self.check()["state"], "current_generation_missing")
        self.current.mkdir(parents=True, mode=0o755)
        os.chmod(self.current, 0o755)
        if os.getuid() == 0:
            self.skipTest("ownership check needs a non-root test user")
        result = self.check()
        self.assertEqual(result["state"], "ok", result)
        other = self.root / "vllm/image-old/revision-example/default"
        other.mkdir(parents=True)
        result = self.check()
        self.assertEqual(result["state"], "other_generations_present")
        self.assertEqual(result["other_generation_count"], 1)
        os.chmod(other, 0o775)
        self.assertEqual(self.check()["state"], "problems")
        os.chmod(other, 0o755)
        stray_generation_file = self.root / "vllm/image-old/revision-example/not-a-directory"
        stray_generation_file.write_text("unexpected", encoding="utf-8")
        self.assertIn(
            str(stray_generation_file),
            [item["path"] for item in self.check()["unexpected_entries"]],
        )
        stray_generation_file.unlink()
        os.chmod(self.current, 0o775)
        self.assertIn("group_or_other_writable", [p for g in self.check()["generations"] if g["is_current"] for p in g["problems"]])
        self.assertEqual(self.check()["state"], "problems")
        os.chmod(self.current, 0o755)
        (self.root / "vllm" / "stray-link").symlink_to(other)
        self.assertEqual(self.check()["unexpected_entries"][0]["kind"], "symlink")


class ExecPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.intent = load_intent(INTENT_V11)
        self.policy = ExecPolicy(self.intent, exceptions_path=EXCEPTIONS, timeline_path=TIMELINE)

    def classify(self, binary: str, at: str, **kwargs):
        return self.policy.classify_exec(binary=binary, observed_at=at, parent=kwargs.get("parent"), image=kwargs.get("image"))

    def test_phase_verdicts(self) -> None:
        cases = [
            ("/usr/bin/python3", "2026-10-24T09:20:00Z", "allowed_in_phase", ["inferring"]),
            ("/usr/local/bin/vllm", "2026-10-24T09:20:00Z", "allowed_in_phase", ["inferring"]),
            ("/usr/bin/gcc", "2026-10-24T09:04:00Z", "allowed_in_phase", ["compiling", "loading"]),
            ("/usr/bin/gcc", "2026-10-24T09:20:00Z", "outside_allowlist", ["inferring"]),
            ("/usr/bin/python3", "2026-10-24T08:00:00Z", "phase_unknown", ["unknown"]),
            ("/bin/sh", "2026-10-24T09:20:00Z", "suspect_binary", ["inferring"]),
            ("/var/cache/serving/vllm/x/y/z/kernel.so", "2026-10-24T09:20:00Z", "exec_from_cache", ["inferring"]),
        ]
        for binary, at, verdict, phases in cases:
            with self.subTest(binary=binary, at=at):
                result = self.classify(binary, at)
                self.assertEqual(result["exec_verdict"], verdict)
                self.assertEqual(result["phase"], phases)
        cache = self.classify("/var/cache/serving/vllm/x/y/z/kernel.so", "2026-10-24T09:20:00Z")
        self.assertIn("CACHE-03", cache["rule_refs"])

    def test_exceptions_are_bounded(self) -> None:
        maintenance = self.classify("/bin/sh", "2026-10-24T10:10:00Z")
        self.assertEqual((maintenance["exec_verdict"], maintenance["exception_id"]), ("exception", "EXC-LAB-MAINT-001"))
        after = self.classify("/bin/sh", "2026-10-24T10:31:00Z")
        self.assertEqual(after["exec_verdict"], "suspect_binary")
        # Python is allowed for inference, and maintenance overlaps inference here.
        self.assertEqual(self.classify("/usr/bin/python3", "2026-10-24T10:10:00Z")["exec_verdict"], "allowed_in_phase")
        digest = "sha256:" + "0" * 64
        no_image = self.classify("/usr/bin/ptxas", "2026-10-24T09:05:00Z")
        self.assertEqual(no_image["exec_verdict"], "outside_allowlist")
        with_image = self.classify("/usr/bin/ptxas", "2026-10-24T09:05:00Z", image=digest)
        self.assertEqual(with_image["exec_verdict"], "exception")
        wrong_phase = self.classify("/usr/bin/ptxas", "2026-10-24T09:20:00Z", image=digest)
        self.assertEqual(wrong_phase["exec_verdict"], "outside_allowlist")

    def test_maintenance_only_window_needs_an_exception(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            timeline = Path(directory) / "t.json"
            timeline.write_text(json.dumps({
                "schema_version": "runtime-phase-timeline/1.0",
                "entries": [{"phase": "maintenance", "from": "2026-10-24T10:00:00+00:00", "until": "2026-10-24T10:30:00+00:00", "basis": "declared"}],
            }), encoding="utf-8")
            policy = ExecPolicy(self.intent, timeline_path=timeline)
            result = policy.classify_exec(binary="/usr/bin/python3", parent=None, image=None, observed_at="2026-10-24T10:10:00Z")
            self.assertEqual(result["exec_verdict"], "outside_allowlist")

    def test_naive_event_time_alerts_instead_of_aborting_ingest(self) -> None:
        result = self.classify("/usr/bin/python3", "2026-10-24T09:20:00")
        self.assertEqual(result["exec_verdict"], "phase_unknown")
        self.assertEqual(result["phase"], ["unknown"])

    def test_parent_condition_requires_parent_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "e.json"
            path.write_text(json.dumps({
                "schema_version": "runtime-exec-exceptions/1.0",
                "exceptions": [{
                    "exception_id": "E1", "rule_ref": "SP3-EXEC", "kind": "maintenance",
                    "executable": "/bin/sh", "parent": "/usr/bin/python3", "phase": "inferring",
                    "reason": "r", "approved_by": "a", "approval_ref": "x",
                    "valid_from": "2026-10-24T09:10:00+00:00", "valid_until": "2026-10-24T09:30:00+00:00",
                }],
            }), encoding="utf-8")
            policy = ExecPolicy(self.intent, exceptions_path=path, timeline_path=TIMELINE)
            classify = lambda parent: policy.classify_exec(binary="/bin/sh", parent=parent, image=None, observed_at="2026-10-24T09:20:00Z")["exec_verdict"]
            self.assertEqual(classify(None), "suspect_binary")
            self.assertEqual(classify("/usr/bin/bash"), "suspect_binary")
            self.assertEqual(classify("/usr/bin/python3"), "exception")

    def test_invalid_exception_and_timeline_documents(self) -> None:
        base = json.loads(EXCEPTIONS.read_text(encoding="utf-8"))
        def exc(**updates):
            document = copy.deepcopy(base)
            document["exceptions"][0].update(updates)
            return document
        bad = {
            "long-maintenance": exc(valid_until="2026-10-24T10:31:00+00:00"),
            "end-before-start": exc(valid_until="2026-10-24T09:00:00+00:00"),
            "naive-time": exc(valid_from="2026-10-24T10:00:00"),
            "bad-phase": exc(phase="serving"),
            "relative-exe": exc(executable="sh"),
            "unknown-field": exc(comment="x"),
            "empty-reason": exc(reason=" "),
            "standing-without-image": exc(kind="standing"),
        }
        for label, document in bad.items():
            with self.subTest(case=label):
                with self.assertRaises(IntentError):
                    parse_exceptions(document)
        duplicate = copy.deepcopy(base)
        duplicate["exceptions"][1]["exception_id"] = duplicate["exceptions"][0]["exception_id"]
        with self.assertRaises(IntentError):
            parse_exceptions(duplicate)
        with self.assertRaises(IntentError):
            parse_timeline({"schema_version": "runtime-phase-timeline/1.0", "entries": [{"phase": "loading", "from": "2026-10-24T09:00:00+00:00", "until": None, "basis": "guess"}]})

    def test_broken_or_changed_files_are_reloaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "t.json"
            path.write_text("{not json", encoding="utf-8")
            policy = ExecPolicy(self.intent, timeline_path=path)
            result = policy.classify_exec(binary="/usr/bin/python3", parent=None, image=None, observed_at="2026-10-24T09:20:00Z")
            self.assertEqual(result["exec_verdict"], "phase_unknown")
            self.assertIsNotNone(result["phase_source_error"])
            time.sleep(0.01)
            path.write_text(TIMELINE.read_text(encoding="utf-8"), encoding="utf-8")
            os.utime(path, None)
            result = policy.classify_exec(binary="/usr/bin/python3", parent=None, image=None, observed_at="2026-10-24T09:20:00Z")
            self.assertEqual(result["exec_verdict"], "allowed_in_phase")
            self.assertIsNone(result["phase_source_error"])


class AdapterIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = ExecPolicy(load_intent(INTENT_V11), exceptions_path=EXCEPTIONS, timeline_path=TIMELINE)

    def test_exec_event_is_classified_without_arguments(self) -> None:
        digest = "sha256:" + "0" * 64
        item = parse_tetragon(
            exec_event("/usr/bin/ptxas", "2026-10-24T09:05:00.123456789Z", image=digest),
            data_origin="synthetic",
            exec_policy=self.policy,
        )
        self.assertEqual(item["exec_verdict"], "exception")
        self.assertEqual(item["image_digest"], digest)
        self.assertEqual(item["rule_id"], "TG-EXEC-01")
        self.assertNotIn("super-secret-argument", json.dumps(item))
        plain = parse_tetragon(exec_event("/usr/bin/ptxas", "2026-10-24T09:05:00Z"), data_origin="synthetic")
        self.assertNotIn("exec_verdict", plain)

    def test_cache_write_generation_tag(self) -> None:
        def kprobe(path: str) -> str:
            return json.dumps({
                "time": "2026-10-24T09:20:00Z",
                "process_kprobe": {
                    "process": {"binary": "/usr/bin/python3"},
                    "function_name": "security_file_permission",
                    "policy_name": "runtime-tg-cache-01",
                    "args": [{"file_arg": {"path": path}}, {"int_arg": 2}],
                    "action": "KPROBE_ACTION_POST",
                },
            })
        accepted, rejected = transform_lines(
            [
                kprobe("/var/cache/serving/vllm/image-example/revision-example/default/a.bin"),
                kprobe("/var/cache/serving/vllm/image-old/revision-example/default/a.bin"),
            ],
            kind="tetragon",
            data_origin="synthetic",
            exec_policy=self.policy,
        )
        self.assertEqual(rejected, [])
        self.assertEqual([item["cache_generation"] for item in accepted], ["current", "other"])
        self.assertIn("CACHE-02", accepted[1]["rule_refs"])


if __name__ == "__main__":
    unittest.main()
