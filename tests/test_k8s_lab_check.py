"""Cluster-free tests for the Kubernetes lab checker and lab intent."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from serving_runtime.enforcement.cilium import render_manifests as render_cilium
from serving_runtime.enforcement.cilium import write_manifests
from serving_runtime.enforcement.intent import load_intent
from serving_runtime.enforcement.tetragon import render_manifests as render_tetragon
from serving_runtime.mvp.fake_backend import FakeBackend
from tools.k8s_lab_check import (
    ContextSafetyError,
    DNS_SCRIPT,
    EXEC_PREP_SCRIPT,
    EXTERNAL_CONNECT_SCRIPT,
    GATEWAY_POST_SCRIPT,
    HTTP_GET_SCRIPT,
    LabChecker,
    MODEL_WRITE_SCRIPT,
    NORMAL_WRITE_SCRIPT,
    ProcessResult,
    PROBE_WRITE_SCRIPT,
    SECRET_READ_SCRIPT,
    build_parser,
    calculate_overall,
    classify_probe_results,
    evaluate_l04_observations,
    export_denylist_blocks_empty_namespace,
    hubble_observe_command,
    kubectl_command,
    load_observations,
    matches_tetragon_file_observation,
    parse_last_json,
    parse_running_pod,
    parse_tracingpolicy_nenforce,
    write_reports,
)


ROOT = Path(__file__).resolve().parents[1]
LAB_INTENT = ROOT / "deploy/lab/intent.lab.json"


class FakeRunner:
    def __init__(self, *, context: str = "kind-runtime-lab", returncode: int = 0) -> None:
        self.context = context
        self.returncode = returncode
        self.calls: list[tuple[tuple[str, ...], float]] = []

    def run(self, argv: list[str], *, timeout: float) -> ProcessResult:
        command = tuple(argv)
        self.calls.append((command, timeout))
        return ProcessResult(command, self.returncode, self.context + "\n", "")


class StaticOutputRunner:
    def __init__(self, output: str) -> None:
        self.output = output

    def run(self, argv: list[str], *, timeout: float) -> ProcessResult:
        command = tuple(argv)
        return ProcessResult(command, 0, self.output, "")


class TetragonLogRunner:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.calls: list[tuple[str, ...]] = []
        self.log_calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str], *, timeout: float) -> ProcessResult:
        command = tuple(argv)
        self.calls.append(command)
        if "logs" in command and "export-stdout" in command:
            self.log_calls.append(command)
            index = min(len(self.log_calls) - 1, len(self.outputs) - 1)
            return ProcessResult(command, 0, self.outputs[index], "")
        return ProcessResult(command, 0, "", "")


def tetragon_file_event(policy_name: str, path: str, mask: int) -> str:
    return json.dumps(
        {
            "time": "2099-01-01T00:00:00Z",
            "process_kprobe": {
                "process": {},
                "function_name": "security_file_permission",
                "policy_name": policy_name,
                "args": [{"file_arg": {"path": path}}, {"int_arg": mask}],
                "action": "observed",
            },
        }
    )


def tetragon_exec_event(path: str) -> str:
    return json.dumps(
        {
            "time": "2099-01-01T00:00:01Z",
            "process_exec": {
                "process": {
                    "binary": path,
                    "pod": {
                        "namespace": "serving-lab",
                        "pod_name": "serving-engine",
                    },
                }
            },
        }
    )


class CommandConstructionTests(unittest.TestCase):
    def test_all_in_pod_python_snippets_compile(self) -> None:
        for script in (
            HTTP_GET_SCRIPT,
            GATEWAY_POST_SCRIPT,
            EXTERNAL_CONNECT_SCRIPT,
            DNS_SCRIPT,
            MODEL_WRITE_SCRIPT,
            SECRET_READ_SCRIPT,
            EXEC_PREP_SCRIPT,
            PROBE_WRITE_SCRIPT,
            NORMAL_WRITE_SCRIPT,
        ):
            with self.subTest(script=script.splitlines()[0]):
                compile(script, "<lab-script>", "exec")

    def test_kubectl_and_hubble_commands_are_argument_lists(self) -> None:
        self.assertEqual(
            kubectl_command(
                "kind-runtime-lab", "serving-lab", "get", "pods", "-o", "json"
            ),
            [
                "kubectl",
                "--context",
                "kind-runtime-lab",
                "--namespace",
                "serving-lab",
                "get",
                "pods",
                "-o",
                "json",
            ],
        )
        self.assertEqual(
            hubble_observe_command("serving-lab"),
            [
                "hubble",
                "observe",
                "--server",
                "localhost:4245",
                "--namespace",
                "serving-lab",
                "--verdict",
                "DROPPED",
                "-o",
                "json",
                "--last",
                "50",
            ],
        )

    def test_tetragon_wait_option_defaults_to_ninety_seconds(self) -> None:
        default = build_parser().parse_args(["--state-dir", "/tmp/lab-state"])
        custom = build_parser().parse_args(
            ["--state-dir", "/tmp/lab-state", "--tetragon-wait", "15"]
        )
        self.assertEqual(default.tetragon_wait, 90.0)
        self.assertEqual(custom.tetragon_wait, 15.0)

    def test_context_mismatch_stops_before_cluster_commands(self) -> None:
        wrong_requested = FakeRunner()
        checker = LabChecker(
            context="production",
            namespace="serving-lab",
            state_dir=Path("/tmp/not-used"),
            cleanup=False,
            runner=wrong_requested,  # type: ignore[arg-type]
            repo_root=ROOT,
        )
        with self.assertRaises(ContextSafetyError):
            checker.assert_safe_context()
        self.assertEqual(wrong_requested.calls, [])

        wrong_current = FakeRunner(context="kind-something-else")
        checker = LabChecker(
            context="kind-runtime-lab",
            namespace="serving-lab",
            state_dir=Path("/tmp/not-used"),
            cleanup=False,
            runner=wrong_current,  # type: ignore[arg-type]
            repo_root=ROOT,
        )
        with self.assertRaises(ContextSafetyError):
            checker.assert_safe_context()
        self.assertEqual(
            [call[0] for call in wrong_current.calls],
            [("kubectl", "config", "current-context")],
        )


class OutputParsingTests(unittest.TestCase):
    def test_running_pod_and_last_json_are_parsed(self) -> None:
        output = json.dumps(
            {
                "items": [
                    {
                        "metadata": {"name": "terminating", "deletionTimestamp": "now"},
                        "status": {"phase": "Running"},
                    },
                    {
                        "metadata": {"name": "engine-b"},
                        "status": {"phase": "Running"},
                    },
                    {
                        "metadata": {"name": "engine-a"},
                        "status": {"phase": "Pending"},
                    },
                ]
            }
        )
        self.assertEqual(parse_running_pod(output), "engine-b")
        self.assertEqual(parse_last_json("noise\n{\"status\": 200}\n"), {"status": 200})

    def test_observation_loader_ignores_corrupt_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "observations.jsonl"
            path.write_text(
                '{"source":"hubble","verdict":"dropped"}\nnot-json\n',
                encoding="utf-8",
            )
            self.assertEqual(
                load_observations(path),
                [{"source": "hubble", "verdict": "dropped"}],
            )

    def test_export_denylist_detects_empty_namespace_rule(self) -> None:
        blocked = json.dumps({
            "data": {
                "export-denylist": (
                    '{"health_check":true}\n'
                    '{"namespace":["", "cilium", "kube-system"]}'
                )
            }
        })
        lab_override = json.dumps({
            "data": {
                "export-denylist": (
                    '{"health_check":true}\n'
                    '{"namespace":["cilium", "kube-system"]}'
                )
            }
        })
        self.assertTrue(export_denylist_blocks_empty_namespace(blocked))
        self.assertFalse(export_denylist_blocks_empty_namespace(lab_override))

        with tempfile.TemporaryDirectory() as temporary:
            checker = LabChecker(
                context="kind-runtime-lab",
                namespace="serving-lab",
                state_dir=Path(temporary),
                cleanup=False,
                runner=StaticOutputRunner(blocked),  # type: ignore[arg-type]
                repo_root=ROOT,
            )
            self.assertTrue(checker._check_tetragon_export_denylist())
            case = checker.cases["L-04"]
            self.assertEqual(case["status"], "blocked_by_environment")
            self.assertIn("pod_info", case["summary"])
            self.assertIn("helm upgrade tetragon", case["remediation"])

    def test_tracingpolicy_nenforce_is_parsed_by_header_and_policy(self) -> None:
        output = """\
ID   NAME                   STATE     NAMESPACE     SENSORS          KERNELMEMORY   MODE      NPOST   NENFORCE   NMONITOR
1    runtime-tg-asset-01    enabled   serving-lab   generic_kprobe   1.88 MB        monitor   2       0          0
2    runtime-tg-deny-01     enabled   serving-lab   generic_kprobe   1.88 MB        enforce   4       16         0
"""
        self.assertEqual(
            parse_tracingpolicy_nenforce(output, "runtime-tg-deny-01"), 16
        )

    def test_podless_tetragon_event_matches_policy_path_access_and_time(self) -> None:
        item = {
            "observed_at": "2026-10-24T01:00:01.123456789Z",
            "source": "tetragon",
            "kind": "file_access",
            "data_origin": "real",
            "policy_name": "runtime-tg-asset-01",
            "path": "/opt/serving/assets/model/lab-write-run.txt",
            "access": "write",
            "pod": None,
            "pod_attribution": "unavailable",
        }
        self.assertTrue(
            matches_tetragon_file_observation(
                item,
                policy_name="runtime-tg-asset-01",
                path="/opt/serving/assets/model/lab-write-run.txt",
                access="write",
                since="2026-10-24T01:00:00Z",
            )
        )
        item["observed_at"] = "2026-10-24T00:59:59Z"
        self.assertFalse(
            matches_tetragon_file_observation(
                item,
                policy_name="runtime-tg-asset-01",
                path="/opt/serving/assets/model/lab-write-run.txt",
                access="write",
                since="2026-10-24T01:00:00Z",
            )
        )

    def test_l04_accepts_unattributed_kprobes_and_requires_namespaced_exec(self) -> None:
        since = "2026-10-24T01:00:00Z"
        asset_path = "/opt/serving/assets/model/lab-write-run.txt"
        secret_path = "/opt/serving/secret/read-probe.txt"
        exec_path = "/opt/serving/cache/lab-exec-run"
        observations = [
            {
                "observed_at": "2026-10-24T01:00:01Z",
                "source": "tetragon",
                "kind": "file_access",
                "data_origin": "real",
                "policy_name": "runtime-tg-asset-01",
                "path": asset_path,
                "access": "write",
                "binary": None,
                "process_attribution": "unavailable",
                "pod": None,
                "pod_attribution": "unavailable",
            },
            {
                "observed_at": "2026-10-24T01:00:02Z",
                "source": "tetragon",
                "kind": "file_access",
                "data_origin": "real",
                "policy_name": "runtime-tg-secret-01",
                "path": secret_path,
                "access": "read",
                "binary": None,
                "process_attribution": "unavailable",
                "pod": None,
                "pod_attribution": "unavailable",
            },
            {
                "observed_at": "2026-10-24T01:00:03Z",
                "source": "tetragon",
                "kind": "process_exec",
                "data_origin": "real",
                "binary": exec_path,
                "pod": {"namespace": "serving-lab", "pod": "serving-engine"},
            },
        ]
        result = evaluate_l04_observations(
            observations,
            asset_path=asset_path,
            secret_path=secret_path,
            exec_path=exec_path,
            namespace="serving-lab",
            since=since,
        )
        self.assertTrue(result["asset"])
        self.assertTrue(result["secret"])
        self.assertTrue(result["exec"])
        self.assertTrue(result["pod_attribution_unavailable"])
        self.assertTrue(result["process_attribution_unavailable"])

        observations[-1]["pod"]["namespace"] = "other"
        result = evaluate_l04_observations(
            observations,
            asset_path=asset_path,
            secret_path=secret_path,
            exec_path=exec_path,
            namespace="serving-lab",
            since=since,
        )
        self.assertTrue(result["asset"])
        self.assertTrue(result["secret"])
        self.assertFalse(result["exec"])


class TetragonCollectionTests(unittest.TestCase):
    @staticmethod
    def prepare_checker(
        temporary: str,
        outputs: list[str],
        *,
        tetragon_wait: float,
        sleeps: list[float],
    ) -> tuple[LabChecker, TetragonLogRunner]:
        runner = TetragonLogRunner(outputs)
        checker = LabChecker(
            context="kind-runtime-lab",
            namespace="serving-lab",
            state_dir=Path(temporary),
            cleanup=False,
            tetragon_wait=tetragon_wait,
            runner=runner,  # type: ignore[arg-type]
            repo_root=ROOT,
            sleeper=sleeps.append,
        )
        checker.rendered_dir.mkdir(parents=True)
        for name in (
            "tetragon-tg-asset-01.json",
            "tetragon-tg-secret-01.json",
            "tetragon-tg-cache-01.json",
        ):
            (checker.rendered_dir / name).write_text("{}\n", encoding="utf-8")
        checker.pods["engine"] = "serving-engine"
        return checker, runner

    def test_l04_passes_when_exec_appears_in_second_collection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sleeps: list[float] = []
            placeholder = LabChecker(
                context="kind-runtime-lab",
                namespace="serving-lab",
                state_dir=Path(temporary),
                cleanup=False,
                repo_root=ROOT,
            )
            asset_path = (
                f"/opt/serving/assets/model/lab-write-{placeholder.run_id}.txt"
            )
            exec_path = f"/opt/serving/cache/lab-exec-{placeholder.run_id}"
            asset = tetragon_file_event("runtime-tg-asset-01", asset_path, 2)
            secret = tetragon_file_event(
                "runtime-tg-secret-01", "/opt/serving/secret/read-probe.txt", 4
            )
            process_exec = tetragon_exec_event(exec_path)
            checker, runner = self.prepare_checker(
                temporary,
                [f"{asset}\n{secret}\n", f"{asset}\n{secret}\n{process_exec}\n"],
                tetragon_wait=10,
                sleeps=sleeps,
            )
            checker.run_id = placeholder.run_id
            checker.raw_dir = placeholder.raw_dir
            checker.data_dir = placeholder.data_dir
            checker.observations_path = placeholder.observations_path

            checker._l04(checker.cases["L-04"])

            case = checker.cases["L-04"]
            self.assertEqual(case["status"], "passed")
            self.assertEqual(len(runner.log_calls), 2)
            self.assertEqual(sleeps, [3, 5.0])
            self.assertEqual(case["tetragon_wait"]["elapsed_seconds"], 5.0)
            self.assertEqual(
                case["tetragon_wait"]["first_seen_seconds"],
                {
                    "asset_write": 0.0,
                    "secret_read": 0.0,
                    "process_exec": 5.0,
                },
            )
            since_values = [
                command[command.index("--since-time") + 1]
                for command in runner.log_calls
            ]
            self.assertEqual(len(set(since_values)), 1)
            ingest_command = next(
                command for command in runner.calls if "ingest" in command
            )
            ingested_path = Path(
                ingest_command[ingest_command.index("--input") + 1]
            )
            self.assertEqual(
                ingested_path.read_text(encoding="utf-8").splitlines(),
                [asset, secret, process_exec],
            )

    def test_l04_timeout_reports_only_missing_event(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sleeps: list[float] = []
            placeholder = LabChecker(
                context="kind-runtime-lab",
                namespace="serving-lab",
                state_dir=Path(temporary),
                cleanup=False,
                repo_root=ROOT,
            )
            asset_path = (
                f"/opt/serving/assets/model/lab-write-{placeholder.run_id}.txt"
            )
            asset = tetragon_file_event("runtime-tg-asset-01", asset_path, 2)
            secret = tetragon_file_event(
                "runtime-tg-secret-01", "/opt/serving/secret/read-probe.txt", 4
            )
            checker, runner = self.prepare_checker(
                temporary,
                [f"{asset}\n{secret}\n"],
                tetragon_wait=5,
                sleeps=sleeps,
            )
            checker.run_id = placeholder.run_id
            checker.raw_dir = placeholder.raw_dir
            checker.data_dir = placeholder.data_dir
            checker.observations_path = placeholder.observations_path

            checker._l04(checker.cases["L-04"])

            case = checker.cases["L-04"]
            self.assertEqual(case["status"], "failed")
            self.assertIn(
                "serving-lab process_exec(고유 실행 파일 경로)", case["summary"]
            )
            self.assertNotIn("TG-ASSET-01 write", case["summary"])
            self.assertNotIn("TG-SECRET-01 read", case["summary"])
            self.assertEqual(len(runner.log_calls), 2)
            self.assertEqual(sleeps, [3, 5.0])
            self.assertIsNone(
                case["tetragon_wait"]["first_seen_seconds"]["process_exec"]
            )

    def test_collection_removes_duplicate_lines_before_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sleeps: list[float] = []
            asset = tetragon_file_event(
                "runtime-tg-asset-01", "/opt/serving/assets/model/duplicate.txt", 2
            )
            checker, _runner = self.prepare_checker(
                temporary,
                [f"{asset}\n{asset}\n"],
                tetragon_wait=10,
                sleeps=sleeps,
            )
            path = checker.raw_dir / "deduplicated.jsonl"

            observations, wait = checker._collect_tetragon_logs(
                checker.cases["L-04"],
                path=path,
                since="2026-10-24T00:00:00Z",
                expected_events=lambda items: {"asset_write": bool(items)},
            )

            self.assertEqual(len(observations), 1)
            self.assertEqual(path.read_text(encoding="utf-8").splitlines(), [asset])
            self.assertEqual(wait["elapsed_seconds"], 0.0)
            self.assertEqual(sleeps, [])


class StatusClassificationTests(unittest.TestCase):
    @staticmethod
    def probe(returncode: int, stdout: str) -> ProcessResult:
        return ProcessResult(("probe",), returncode, stdout, "")

    def test_repeated_probe_all_blocked_passes(self) -> None:
        results = [self.probe(13, "EPERM\n") for _ in range(5)]
        self.assertEqual(classify_probe_results(results), "passed")

    def test_l05_has_fixed_measurement_note(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checker = LabChecker(
                context="kind-runtime-lab",
                namespace="serving-lab",
                state_dir=Path(temporary),
                cleanup=False,
                repo_root=ROOT,
            )
        self.assertEqual(
            checker.cases["L-05"]["note"],
            "적용 직후 측정; 이전 관측에서 시간 경과 후 불일치 사례 있음",
        )

    def test_repeated_probe_all_success_is_unsupported(self) -> None:
        results = [self.probe(0, "WRITE_SUCCEEDED\n") for _ in range(5)]
        self.assertEqual(classify_probe_results(results), "unsupported")

    def test_repeated_probe_mixed_is_inconsistent(self) -> None:
        results = [self.probe(13, "EPERM\n") for _ in range(2)]
        results.extend(self.probe(0, "WRITE_SUCCEEDED\n") for _ in range(3))
        self.assertEqual(classify_probe_results(results), "inconsistent")

    def test_overall_failed_wins_and_other_non_passed_states_are_partial(self) -> None:
        self.assertEqual(calculate_overall(["passed", "passed"]), "passed")
        for status in (
            "unsupported",
            "inconsistent",
            "blocked_by_environment",
            "not_run",
        ):
            with self.subTest(status=status):
                self.assertEqual(calculate_overall(["passed", status]), "partial")
        self.assertEqual(calculate_overall(["not_run", "failed"]), "failed")


class ReportTests(unittest.TestCase):
    def test_json_and_markdown_reports_redact_secret_values(self) -> None:
        report = {
            "schema": "serving-runtime-k8s-lab-report/1.0",
            "run_id": "unit-report",
            "context": "kind-runtime-lab",
            "namespace": "serving-lab",
            "overall": "passed",
            "cases": [
                {
                    "id": "L-01",
                    "title": "baseline",
                    "status": "passed",
                    "summary": "Authorization: Bearer should-not-leak",
                    "evidence": [],
                    "commands": [
                        {
                            "argv": ["example"],
                            "returncode": 0,
                            "stdout": 'password=hunter2 {"api_key":"json-key"}',
                            "stderr": "",
                        }
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            json_path, markdown_path = write_reports(report, Path(temporary))
            for path in (json_path, markdown_path):
                content = path.read_text(encoding="utf-8")
                self.assertNotIn("should-not-leak", content)
                self.assertNotIn("hunter2", content)
                self.assertNotIn("json-key", content)
                self.assertIn("[REDACTED]", content)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)


class LabIntentTests(unittest.TestCase):
    def test_dockerfile_and_lab_workloads_keep_requested_runtime_shape(self) -> None:
        dockerfile = (ROOT / "deploy/lab/Dockerfile").read_text(encoding="utf-8")
        manifest = (ROOT / "deploy/lab/manifests/lab.yaml").read_text(
            encoding="utf-8"
        )
        runner = (ROOT / "deploy/lab/run_fake_engine.py").read_text(encoding="utf-8")
        self.assertIn("FROM python:3.12-slim", dockerfile)
        self.assertIn("pip install --no-cache-dir --no-deps .", dockerfile)
        self.assertIn("USER 10001:10001", dockerfile)
        self.assertIn("/opt/serving/assets/model/model.txt", dockerfile)
        self.assertIn("/opt/serving/probe/blocked-probe.txt", dockerfile)
        self.assertEqual(manifest.count("imagePullPolicy: IfNotPresent"), 4)
        for label in (
            "app: serving-engine",
            "app: serving-gateway",
            "app: other-client",
            "app: serving-observer",
        ):
            self.assertIn(label, manifest)
        self.assertIn("http://serving-engine.serving-lab.svc.cluster.local:8000", manifest)
        self.assertIn('command: ["serving-runtime"]', manifest)
        self.assertIn("FakeBackend(host=\"0.0.0.0\", port=8000)", runner)
        self.assertIn("TEST ONLY", runner)
        tetragon_values = (ROOT / "deploy/lab/tetragon-values.yaml").read_text(
            encoding="utf-8"
        )
        def json_lines(key: str) -> list[dict[str, object]]:
            marker = f"  {key}: |-"
            lines = tetragon_values.splitlines()
            start = lines.index(marker) + 1
            block = []
            for line in lines[start:]:
                if line.startswith("  ") and not line.startswith("    "):
                    break
                if line.strip().startswith("{"):
                    block.append(json.loads(line.strip()))
            return block

        self.assertEqual(
            json_lines("exportAllowList"),
            [
                {
                    "event_set": ["PROCESS_EXEC", "PROCESS_EXIT"],
                    "namespace": ["serving-lab"],
                },
                {
                    "event_set": ["PROCESS_KPROBE"],
                    "policy_names": [
                        "runtime-tg-asset-01",
                        "runtime-tg-secret-01",
                        "runtime-tg-cache-01",
                        "runtime-tg-deny-01",
                    ],
                },
            ],
        )
        self.assertEqual(
            json_lines("exportDenyList"),
            [
                {"health_check": True},
                {"namespace": ["cilium", "kube-system"]},
            ],
        )
        self.assertIn('exportDenyList: |-', tetragon_values)
        self.assertIn('{"namespace":["cilium","kube-system"]}', tetragon_values)
        self.assertNotIn('{"namespace":["",', tetragon_values)

    def test_lab_intent_validates_and_all_manifests_render(self) -> None:
        intent = load_intent(LAB_INTENT)
        self.assertEqual(intent.namespace, "serving-lab")
        self.assertEqual(intent.engine.port, 8000)
        self.assertEqual(intent.engine_egress_allow, ())
        self.assertEqual(intent.allowed_engine_binaries, ("/usr/local/bin/vllm",))
        self.assertNotIn("python", " ".join(intent.allowed_engine_binaries))
        self.assertNotIn("/bin/sh", intent.allowed_engine_binaries)
        manifests = render_cilium(intent)
        manifests.update(render_tetragon(intent))
        self.assertIn("cilium-engine-ingress.json", manifests)
        self.assertIn("tetragon-tg-deny-01.json", manifests)
        with tempfile.TemporaryDirectory() as temporary:
            written = write_manifests(manifests, temporary)
            self.assertEqual(len(written), 7)
            self.assertTrue(all(path.is_file() for path in written))

    def test_fake_backend_host_default_is_unchanged_and_lab_host_is_selectable(self) -> None:
        self.assertEqual(FakeBackend().host, "127.0.0.1")
        self.assertEqual(FakeBackend(host="0.0.0.0").host, "0.0.0.0")
        with self.assertRaises(ValueError):
            FakeBackend(host="")


if __name__ == "__main__":
    unittest.main()
