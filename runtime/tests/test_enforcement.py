"""P2 pre-work tests for intent, manifests, event adapters, CLI, and dashboard."""

from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from serving_runtime.cli import main
from serving_runtime.enforcement.cilium import render_manifests as render_cilium
from serving_runtime.enforcement.cilium import write_manifests
from serving_runtime.enforcement.events import (
    EventError,
    ObservationQuery,
    parse_hubble,
    parse_tetragon,
    transform_lines,
)
from serving_runtime.enforcement.intent import IntentError, load_intent, parse_intent
from serving_runtime.enforcement.tetragon import render_manifests as render_tetragon
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
INTENT_PATH = ROOT / "examples/enforcement/intent.example.json"
FIXTURE_DIR = ROOT / "tests/fixtures/enforcement"
POLICY_PATH = ROOT / "examples/mvp/policy.json"


def intent_document() -> dict:
    return json.loads(INTENT_PATH.read_text(encoding="utf-8"))


class IntentTests(unittest.TestCase):
    def test_example_and_defaults(self) -> None:
        document = intent_document()
        document["engine"].pop("port")
        document.pop("dns")
        document.pop("engine_egress_allow")
        intent = parse_intent(document)
        self.assertEqual(intent.engine.port, 8000)
        self.assertEqual((intent.dns.namespace, intent.dns.port), ("kube-system", 53))
        self.assertEqual(intent.dns.labels, {"k8s-app": "kube-dns"})
        self.assertEqual(intent.engine_egress_allow, ())
        self.assertEqual(len(intent.metrics_collectors), 1)
        self.assertEqual(intent.tetragon_mode, "monitor")

    def test_unknown_empty_label_bad_port_and_relative_path_are_rejected(self) -> None:
        cases = []
        unknown = intent_document()
        unknown["surprise"] = True
        cases.append(unknown)
        empty_labels = intent_document()
        empty_labels["engine"]["labels"] = {}
        cases.append(empty_labels)
        bad_port = intent_document()
        bad_port["engine"]["port"] = 0
        cases.append(bad_port)
        relative = intent_document()
        relative["protected_paths"]["model_dir"] = "models"
        cases.append(relative)
        both_destinations = intent_document()
        both_destinations["engine_egress_allow"][0]["cidr"] = "192.0.2.0/24"
        cases.append(both_destinations)
        missing_collectors = intent_document()
        missing_collectors.pop("metrics_collectors")
        cases.append(missing_collectors)
        for document in cases:
            with self.subTest(document=document):
                with self.assertRaises(IntentError):
                    parse_intent(document)

    def test_enforce_probe_requires_absolute_probe_path(self) -> None:
        document = intent_document()
        document["probe_path"] = "/tmp/unused-monitor-probe"
        self.assertEqual(parse_intent(document).probe_path, document["probe_path"])
        document["tetragon_mode"] = "enforce_probe"
        document.pop("probe_path")
        with self.assertRaises(IntentError):
            parse_intent(document)
        document["probe_path"] = "/var/cache/serving/engine/example/blocked-probe.txt"
        self.assertEqual(parse_intent(document).probe_path, document["probe_path"])

    def test_loader_rejects_duplicate_json_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "intent.json"
            path.write_text(
                '{"schema":"runtime-enforcement-intent/1.0","schema":"duplicate"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IntentError, "duplicate JSON field"):
                load_intent(path)


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.intent = parse_intent(intent_document())

    def test_cilium_policy_shapes_and_fqdn_dns_inspection(self) -> None:
        manifests = render_cilium(self.intent)
        ingress = manifests["cilium-engine-ingress.json"]
        self.assertEqual((ingress["apiVersion"], ingress["kind"]),
                         ("cilium.io/v2", "CiliumNetworkPolicy"))
        sources = ingress["spec"]["ingress"][0]["fromEndpoints"]
        self.assertEqual(len(sources), 2)
        self.assertEqual(
            ingress["spec"]["ingress"][0]["toPorts"][0]["ports"],
            [{"port": "8000", "protocol": "TCP"}],
        )

        egress = manifests["cilium-engine-egress.json"]["spec"]["egress"]
        self.assertEqual(egress[0]["toPorts"][0]["rules"]["dns"],
                         [{"matchPattern": "*"}])
        self.assertEqual(egress[1]["toFQDNs"],
                         [{"matchName": "telemetry.example.invalid"}])
        self.assertEqual(egress[2]["toCIDR"], ["192.0.2.0/24"])
        self.assertNotIn("egressDeny", manifests["cilium-engine-egress.json"]["spec"])

        gateway = manifests["cilium-gateway-egress.json"]
        self.assertEqual(len(gateway["spec"]["egress"]), 2)
        self.assertIn("candidate-cluster-unapplied-unverified",
                      gateway["metadata"]["annotations"].values())

    def test_no_fqdn_keeps_dns_l4_only_and_empty_allowlist_defaults_deny(self) -> None:
        document = intent_document()
        document["engine_egress_allow"] = []
        egress = render_cilium(parse_intent(document))["cilium-engine-egress.json"]
        rules = egress["spec"]["egress"]
        self.assertEqual(len(rules), 1)
        self.assertNotIn("rules", rules[0]["toPorts"][0])

    def test_tetragon_monitor_has_file_rules_without_override_or_exec_policy(self) -> None:
        manifests = render_tetragon(self.intent)
        self.assertEqual(
            set(manifests),
            {
                "tetragon-tg-asset-01.json",
                "tetragon-tg-cache-01.json",
                "tetragon-tg-secret-01.json",
            },
        )
        serialized = json.dumps(manifests)
        self.assertNotIn("Override", serialized)
        self.assertNotIn("tg-exec", serialized.lower())
        for document in manifests.values():
            self.assertEqual(document["kind"], "TracingPolicyNamespaced")
            self.assertEqual(document["spec"]["podSelector"]["matchLabels"],
                             self.intent.engine.labels)
            self.assertIn("TG-", document["metadata"]["annotations"][
                "serving-runtime.local/rule-id"
            ])

    def test_enforce_probe_adds_only_path_limited_override(self) -> None:
        document = intent_document()
        document.update({
            "tetragon_mode": "enforce_probe",
            "probe_path": "/var/cache/serving/engine/example/blocked-probe.txt",
        })
        manifests = render_tetragon(parse_intent(document))
        deny = manifests["tetragon-tg-deny-01.json"]
        selector = deny["spec"]["kprobes"][0]["selectors"][0]
        self.assertEqual(selector["matchArgs"][0], {
            "index": 0,
            "operator": "Equal",
            "values": [document["probe_path"]],
        })
        self.assertEqual(selector["matchActions"], [
            {"action": "Post"},
            {"action": "Override", "argError": -1},
        ])
        self.assertIn("unverified", deny["metadata"]["annotations"][
            "serving-runtime.local/limit"
        ])

    def test_writer_rejects_names_outside_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                write_manifests({"../escape.json": {}}, temporary)
            self.assertFalse((Path(temporary).parent / "escape.json").exists())


class EventAdapterTests(unittest.TestCase):
    def test_hubble_fixture_normalizes_verdict_and_destination(self) -> None:
        lines = (FIXTURE_DIR / "hubble_flows.synthetic.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        accepted, rejected = transform_lines(
            lines, kind="hubble", data_origin="synthetic"
        )
        self.assertEqual(rejected, [])
        self.assertEqual([item["verdict"] for item in accepted],
                         ["allowed", "dropped"])
        self.assertEqual(accepted[0]["dst"]["port"], 8000)
        self.assertEqual(accepted[1]["reason"], "POLICY_DENIED")
        self.assertTrue(all(item["data_origin"] == "synthetic" for item in accepted))

    def test_tetragon_fixture_drops_arguments_and_marks_allowlist(self) -> None:
        lines = (FIXTURE_DIR / "tetragon_events.synthetic.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        accepted, rejected = transform_lines(
            lines,
            kind="tetragon",
            data_origin="synthetic",
            allowed_engine_binaries=["/usr/local/bin/vllm"],
        )
        self.assertEqual(rejected, [])
        self.assertEqual([item["allowed_binary"] for item in accepted[:2]],
                         [True, False])
        self.assertEqual(accepted[2]["access"], "write")
        self.assertEqual(accepted[2]["rule_id"], "TG-ASSET-01")
        read_event = json.loads(lines[2])
        read_event["process_kprobe"]["args"][1]["int_arg"] = 4
        read_event["process_kprobe"]["policy_name"] = "runtime-tg-secret-01"
        read_observation = parse_tetragon(
            read_event,
            data_origin="synthetic",
            allowed_engine_binaries=["/usr/local/bin/vllm"],
        )
        self.assertEqual(
            (read_observation["access"], read_observation["rule_id"]),
            ("read", "TG-SECRET-01"),
        )
        serialized = json.dumps(accepted)
        self.assertNotIn("arguments", serialized)
        self.assertNotIn("synthetic-do-not-store", serialized)

    def test_no_allowlist_means_unknown_and_bad_lines_are_aggregated(self) -> None:
        exec_event = json.loads(
            (FIXTURE_DIR / "tetragon_events.synthetic.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[0]
        )
        self.assertIsNone(
            parse_tetragon(exec_event, data_origin="real")["allowed_binary"]
        )
        accepted, rejected = transform_lines(
            ["not-json", json.dumps({"time": "now", "process_exit": {}})],
            kind="tetragon",
            data_origin="real",
        )
        self.assertEqual(accepted, [])
        self.assertEqual([item["line"] for item in rejected], [1, 2])
        self.assertNotIn("not-json", json.dumps(rejected))

    def test_kprobe_without_binary_or_pod_marks_attribution_unavailable(self) -> None:
        event = json.loads(
            (FIXTURE_DIR / "tetragon_events.synthetic.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[2]
        )
        event["process_kprobe"]["process"].pop("binary")
        event["process_kprobe"]["process"].pop("pod")
        observation = parse_tetragon(event, data_origin="real")
        self.assertIsNone(observation["binary"])
        self.assertEqual(observation["process_attribution"], "unavailable")
        self.assertIsNone(observation["pod"])
        self.assertEqual(observation["pod_attribution"], "unavailable")
        self.assertEqual(observation["policy_name"], "runtime-tg-asset-01")

    def test_kprobe_empty_binary_is_null_but_required_fields_stay_strict(self) -> None:
        event = json.loads(
            (FIXTURE_DIR / "tetragon_events.synthetic.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[2]
        )
        event["process_kprobe"]["process"]["binary"] = ""
        observation = parse_tetragon(event, data_origin="real")
        self.assertIsNone(observation["binary"])
        self.assertEqual(observation["process_attribution"], "unavailable")
        self.assertEqual(observation["pod_attribution"], "available")

        malformed = []
        for key in ("function_name", "policy_name", "action"):
            document = json.loads(json.dumps(event))
            document["process_kprobe"].pop(key)
            malformed.append(document)
        no_path = json.loads(json.dumps(event))
        no_path["process_kprobe"]["args"].pop(0)
        malformed.append(no_path)
        no_mask = json.loads(json.dumps(event))
        no_mask["process_kprobe"]["args"].pop(1)
        malformed.append(no_mask)
        for document in malformed:
            with self.subTest(document=document), self.assertRaises(EventError):
                parse_tetragon(document, data_origin="real")

    def test_process_exec_still_requires_non_empty_binary(self) -> None:
        original = json.loads(
            (FIXTURE_DIR / "tetragon_events.synthetic.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[0]
        )
        for binary in (None, ""):
            event = json.loads(json.dumps(original))
            if binary is None:
                event["process_exec"]["process"].pop("binary")
            else:
                event["process_exec"]["process"]["binary"] = binary
            with self.subTest(binary=binary), self.assertRaisesRegex(
                EventError, "process.binary must be a non-empty string"
            ):
                parse_tetragon(event, data_origin="real")

    def test_origin_is_required_and_validated(self) -> None:
        flow = json.loads(
            (FIXTURE_DIR / "hubble_flows.synthetic.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[0]
        )
        with self.assertRaises(ValueError):
            parse_hubble(flow, data_origin="maybe")

    def test_observation_query_counts_filters_latest_and_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            store = JsonlStore(path)
            for index in range(55):
                store.append({
                    "observed_at": f"2026-10-24T01:00:{index:02d}Z",
                    "source": "hubble" if index % 2 else "tetragon",
                    "kind": "network_flow" if index % 2 else "process_exec",
                    "verdict": "allowed" if index % 2 else None,
                    "rule_id": "TG-EXEC-01" if index % 2 == 0 else None,
                    "data_origin": "synthetic",
                })
            with path.open("ab") as output:
                output.write(b"{broken\n")
            payload = ObservationQuery(path).query()
            self.assertEqual(payload["counts"]["total"], 55)
            self.assertEqual(payload["counts"]["data_origin"], {"synthetic": 55})
            self.assertEqual(
                payload["origins_by_kind"]["process_exec"], {"synthetic": 28}
            )
            self.assertEqual(len(payload["observations"]), 50)
            self.assertEqual(payload["corrupt_lines"], 1)
            filtered = ObservationQuery(path).query({"source": "hubble"})
            self.assertEqual(filtered["counts"]["source"], {"hubble": 27})


class EnforcementCliTests(unittest.TestCase):
    def test_render_and_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = io.StringIO()
            with redirect_stdout(output):
                status = main([
                    "enforce", "render", "--intent", str(INTENT_PATH),
                    "--out", str(root / "rendered"),
                ])
            self.assertEqual(status, 0)
            self.assertIn("클러스터 미적용·미검증", output.getvalue())
            self.assertTrue((root / "rendered/cilium-engine-egress.json").is_file())

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                status = main([
                    "enforce", "ingest", "--kind", "tetragon",
                    "--input", str(FIXTURE_DIR / "tetragon_events.synthetic.jsonl"),
                    "--origin", "synthetic", "--data-dir", str(root / "data"),
                    "--intent", str(INTENT_PATH),
                ])
            self.assertEqual(status, 0)
            records, corrupt = JsonlStore(root / "data/observations.jsonl").read_all()
            self.assertEqual((len(records), corrupt), (3, []))
            self.assertFalse(records[1]["allowed_binary"])


class DashboardStaticTests(unittest.TestCase):
    def test_dashboard_is_text_only_and_has_observation_states(self) -> None:
        static = ROOT / "src/serving_runtime/mvp/static"
        page = (static / "index.html").read_text(encoding="utf-8")
        script = (static / "app.js").read_text(encoding="utf-8")
        self.assertIn("통신·행위 관측", page)
        self.assertIn("network-observation-chip", page)
        self.assertIn("합성 데이터", script)
        self.assertIn("실제 수집", script)
        self.assertIn("exec-outside-allowlist", script)
        self.assertIn("origins_by_kind", script)
        self.assertIn("pod 미확인", script)
        self.assertIn("프로세스 미확인", script)
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval("):
            self.assertNotIn(forbidden, script)


class ObservationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        JsonlStore(self.data_dir / "observations.jsonl").append({
            "schema": "runtime-observation/1.0",
            "observed_at": "2026-10-24T00:00:00Z",
            "source": "tetragon",
            "kind": "process_exec",
            "rule_id": "TG-EXEC-01",
            "data_origin": "synthetic",
            "binary": "/bin/sh",
            "allowed_binary": False,
        })
        with (self.data_dir / "observations.jsonl").open("ab") as output:
            output.write(b"{broken\n")
        try:
            self.engine = MockEngine(
                port=0,
                receipts_path=self.data_dir / "engine_receipts.jsonl",
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        config = MVPConfig(
            gateway_port=0,
            engine_port=0,
            policy_path=str(POLICY_PATH),
            data_dir=str(self.data_dir),
        )
        try:
            self.gateway = GatewayApp(
                config, load_policy(POLICY_PATH), self.engine.address
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.gateway.stop)

    def get(self, path: str) -> tuple[int, bytes]:
        host, port = self.gateway.address
        try:
            response = urlopen(f"http://{host}:{port}{path}", timeout=2)
        except HTTPError as error:
            with closing(error):
                return error.code, error.read()
        with closing(response):
            return response.status, response.read()

    def test_api_counts_filters_and_rejects_unknown_filter(self) -> None:
        status, body = self.get("/api/observations")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["counts"]["rule_id"], {"TG-EXEC-01": 1})
        self.assertEqual(len(payload["observations"]), 1)
        self.assertEqual(payload["corrupt_lines"], 1)
        self.assertEqual(self.get("/api/observations?source=tetragon")[0], 200)
        self.assertEqual(self.get("/api/observations?unknown=x")[0], 400)

if __name__ == "__main__":
    unittest.main()
