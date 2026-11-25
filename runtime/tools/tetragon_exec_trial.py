"""One disposable namespace for an execution-denial trial. Never targets an existing Pod."""

import argparse
from tools.trial_cli import add_options
import hashlib
import json
from pathlib import Path
import re
import time
from uuid import uuid4

from serving_runtime.common.kube_context import validate_kube_context
from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.exec_verification import reassess_execution
from serving_runtime.enforcement.tetragon import comparable_spec, render_manifests
from tools.tetragon_mode import switch_mode, parse_policy_row
from tools.tetragon_trial import atomic_json, now, run
from tools.tetragon_exec_probe import EXEMPT, PROBE, wait_bound_events, probe_passed, safe_exec_events, pod_receipt


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ExecTrial:
    def __init__(self, context, output, document, image, *, runner=run):
        self.context = validate_kube_context(context)
        if not re.fullmatch(r"[^\s@]+@sha256:[a-f0-9]{64}", image):
            raise ValueError("trial image requires a reviewed digest")
        self.output, self.document, self.image = Path(output), document, image
        self.run = runner
        self.namespace = "byul-exec-proof-" + uuid4().hex[:10]
        self.created = False
        self.report = {
            "id": "exec-proof-" + uuid4().hex,
            "schema_version": "runtime-tetragon-verification/1.0",
            "method": "lsm_bprm_check",
            "variant": "combined",
            "data_origin": "real" if runner is run else "synthetic",
            "started_at": now(),
            "facts": {},
        }

    def kube(self, *args, namespace=None):
        return self.run(["kubectl", "--context", self.context, "-n", namespace or self.namespace, *args], timeout=70)

    def checked(self, *args, namespace=None):
        result = self.kube(*args, namespace=namespace)
        if result.returncode:
            raise RuntimeError("trial command failed")
        return result

    def create(self, name, resource):
        path = self.output / (name + ".json")
        atomic_json(path, resource, mode=0o600)
        self.checked("create", "-f", str(path))

    def prepare(self):
        self.create("namespace", {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": self.namespace}})
        self.created = True
        doc = dict(self.document, namespace=self.namespace)
        self.intent = parse_intent(doc)
        self.exempt = next(iter(self.intent.exec_deny_exempt_callers), None)
        if self.exempt is None:
            self.report["empty_exemption_candidate"] = {
                "engine_labels": [{"key": k, "value": v} for k, v in self.intent.engine.labels.items()],
                "deny_paths": [{"path": p} for p in self.intent.exec_deny_binaries],
            }
            self.report["exemption_test"] = {"status": "not_configured"}
        self.policy = render_manifests(self.intent)["tetragon-tg-exec-01.json"]
        self.name = self.policy["metadata"]["name"]
        self.create(
            "pod",
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": "probe", "namespace": self.namespace, "labels": self.intent.engine.labels},
                "spec": {
                    "restartPolicy": "Never",
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": self.intent.engine.container_name or "engine",
                            "image": self.image,
                            "command": ["python3", "-u", "-c", "import time; time.sleep(600)"],
                        }
                    ],
                },
            },
        )
        self.checked("wait", "pod/probe", "--for=condition=Ready", "--timeout=60s")
        self.before = self.pod()
        self.report["pod_before"] = pod_receipt(self.before)
        self.report.update(
            namespace=self.namespace,
            policy=self.name,
            pod_uid=self.before["metadata"]["uid"],
            node=self.before["spec"]["nodeName"],
            context=self.context,
        )
        self.create("policy", self.policy)
        installed = json.loads(self.checked("get", "tracingpolicynamespaced", self.name, "-o", "json").stdout)
        if comparable_spec(installed["spec"]) != comparable_spec(self.policy["spec"]):
            raise ValueError("installed policy differs from candidate")
        self.report["policy_spec_sha256"] = digest(comparable_spec(installed["spec"]))
        self.report["kernel"] = self.node_kernel()

    def node_kernel(self):
        pods = json.loads(
            self.checked(
                "get", "pods", "-l", "app.kubernetes.io/name=tetragon", "-o", "json", namespace="kube-system"
            ).stdout
        )
        nodes = [p for p in pods["items"] if p["spec"].get("nodeName") == self.report["node"]]
        if len(nodes) != 1:
            raise ValueError("trial requires one Tetragon agent on the same node")
        self.agent = nodes[0]["metadata"]["name"]
        return self.checked(
            "exec", self.agent, "-c", "tetragon", "--", "uname", "-r", namespace="kube-system"
        ).stdout.strip()

    def pod(self):
        return json.loads(self.checked("get", "pod", "probe", "-o", "json").stdout)

    def python(self, code, *args):
        result = self.checked(
            "exec", "probe", "-c", self.intent.engine.container_name or "engine", "--", "python3", "-c", code, *args
        )
        return json.loads(result.stdout.splitlines()[-1])

    def wait_loaded(self):
        for _ in range(20):
            result = self.kube(
                "exec",
                "daemonset/tetragon",
                "-c",
                "tetragon",
                "--",
                "tetra",
                "tracingpolicy",
                "list",
                namespace="kube-system",
            )
            row = parse_policy_row(result.stdout, self.name, self.namespace) if not result.returncode else None
            if row and row["state"] == "enabled" and row["mode"] == "monitor":
                return
            time.sleep(1)
        raise ValueError("trial policy did not reach enabled monitor state")

    def exercise(self):
        self.wait_loaded()
        baseline = self.python(PROBE)
        self.report["monitor_probe"] = baseline
        if any(row.get("returncode") != 0 for row in baseline.get("cases", [])):
            raise ValueError("monitor baseline did not allow every probe")
        switched = switch_mode(self.context, self.namespace, self.name, "enforce", run=self.run)
        if not switched["ok"]:
            raise RuntimeError("isolated policy did not enter enforce")
        self.report["policy_id"] = switched["after"]["id"]
        self.report["steps"] = [{"at": now(), "target_mode": "enforce", **switched}]
        result = self.python(PROBE)
        self.report["probe"] = result
        if self.exempt is not None:
            self.report["exempt"] = self.python(EXEMPT, self.exempt)
        after = self.pod()
        self.report["pod_after"] = pod_receipt(after)

        def fetch_events():
            return self.checked(
                "logs",
                self.agent,
                "-c",
                "export-stdout",
                "--since-time=" + result["started_at"],
                namespace="kube-system",
            ).stdout

        events = wait_bound_events(
            fetch_events,
            {
                "policy": self.name,
                "pod_uid": self.report["pod_uid"],
                "start": result["started_at"],
                "end": result["finished_at"],
            },
        )
        stable = (
            after["metadata"]["uid"] == self.before["metadata"]["uid"]
            and all(s.get("state", {}).get("running") for s in after["status"]["containerStatuses"])
            and [s.get("restartCount") for s in after["status"]["containerStatuses"]]
            == [s.get("restartCount") for s in self.before["status"]["containerStatuses"]]
        )
        self.report["events"] = safe_exec_events(events)
        self.report["facts"] = {
            "exec_denied": probe_passed(result),
            "event_bound": bool(events),
            "exemption_check": self.exempt is None or self.report["exempt"].get("returncode") == 0,
            "process_survived": result.get("alive") is True,
            "pod_stable": stable,
        }
        self.report["verification"] = {"status": "passed" if all(self.report["facts"].values()) else "failed"}

    def execute(self, *, allow_isolated_trials=False):
        if not allow_isolated_trials:
            raise ValueError("--allow-isolated-trials is required")
        self.output.mkdir(parents=True, exist_ok=False)
        try:
            self.prepare()
            self.exercise()
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.report.update(verification={"status": "failed"}, error=type(error).__name__)
        finally:
            if self.created:
                restored = switch_mode(self.context, self.namespace, "runtime-tg-exec-01", "monitor", run=self.run)
                deleted = self.kube("delete", "namespace", self.namespace, "--wait=true", "--timeout=60s")
                self.report.setdefault("steps", []).append({"at": now(), "target_mode": "monitor", **restored})
                self.report["cleanup"] = {
                    "monitor_restored": restored["ok"],
                    "namespace_deleted": deleted.returncode == 0,
                }
                if not restored["ok"] or deleted.returncode:
                    self.report["verification"] = {"status": "failed"}
            self.report["finished_at"] = now()
            self.report = reassess_execution(self.report)
            atomic_json(self.output / "source-report.json", self.report, mode=0o600)
            self.report["source_report_sha256"] = hashlib.sha256(
                (self.output / "source-report.json").read_bytes()
            ).hexdigest()
            atomic_json(self.output / "result.verification.json", self.report, mode=0o600)
        return self.report


def main():
    parser = argparse.ArgumentParser(description="일회용 Pod의 셸 실행 거부 시험")
    add_options(parser, [(("--" + flag,), {"required": True}) for flag in ("context", "intent", "image", "output")])
    add_options(
        parser,
        [
            (("--allow-isolated-trials",), {"action": "store_true"}),
        ],
    )
    args = parser.parse_args()
    trial = ExecTrial(args.context, args.output, json.loads(Path(args.intent).read_text()), args.image)
    result = trial.execute(allow_isolated_trials=args.allow_isolated_trials)
    print("시험 결과:", result["verification"]["status"], "· 실제 errno는 결과 파일에서 확인합니다.")
    return 0 if result["verification"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
