"""Safe demonstration: normal request, simulated compromise, event, normal request."""

import argparse
import errno
from tools.trial_cli import add_options
import json
import os
from pathlib import Path
import stat
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from serving_runtime.common.kube_context import validate_kube_context
from serving_runtime.enforcement.intent import load_intent
from serving_runtime.operations.safety import CHECKS, evaluate
from serving_runtime.enforcement.verification import reassess_record
from serving_runtime.enforcement.tetragon import comparable_spec, render_manifests
from tools.tetragon_exec_trial import digest
from tools.tetragon_exec_probe import PROBE, wait_bound_events, probe_passed
from tools.tetragon_trial import atomic_json, now, run
from tools.tetragon_mode import switch_mode, parse_policy_row
from tools.tetragon_monitor_window import read_object
from serving_runtime.observation.tetragon_state import read_tetragon_state

NOTICE = "실제 취약점 공격이 아니라, 침해된 코드가 엔진 컨테이너에서 실행된 상황을 모의한 시험"
FIRST_NOTICE = "침해된 코드의 셸 실행을 모의한 시험이며 실제 취약점 공격이 아님"
POD_SCOPE = "현재 정책 적용 범위: 엔진 Pod 전체"
LIMITS = "/tmp 복사 우회 가능 · 셸 없이 Python 내부에서 수행하는 일은 이 규칙의 대상 밖 · 기동 중에는 monitor"


def read_status(path, context):
    read_object(path)  # Preserve ownership/permission checks before the standard state reader.
    return read_tetragon_state(path, context=context, require_context=True)


def valid_normal_response(payload, model):
    if not isinstance(payload, dict) or payload.get("model") != model or payload.get("object") != "chat.completion":
        return False
    choices = payload.get("choices")
    return (
        isinstance(choices, list)
        and bool(choices)
        and all(
            isinstance(c, dict)
            and isinstance(c.get("message"), dict)
            and c["message"].get("role") == "assistant"
            and isinstance(c["message"].get("content"), str)
            and bool(c["message"]["content"].strip())
            for c in choices
        )
    )


def normal_request(url, body, token_file):
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username or parsed.query or parsed.fragment:
        raise ValueError("Gateway must use an explicit local loopback HTTP URL")
    fd = os.open(token_file, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd) as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in {0, os.getuid()} or stat.S_IMODE(info.st_mode) != 0o600:
            raise ValueError("token requires a trusted 0600 regular file")
        token = source.read(8193).rstrip("\r\n")
    if not token or len(token) > 8192 or any(not 0x21 <= ord(c) <= 0x7E for c in token):
        raise ValueError("invalid private authentication input")
    request = Request(url, data=body, headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    with urlopen(request, timeout=60) as response:
        payload = response.read(1024 * 1024 + 1)
        if len(payload) > 1024 * 1024 or not valid_normal_response(json.loads(payload), json.loads(body).get("model")):
            raise ValueError("normal inference body/model/generated content not confirmed")
        return response.status


class ExecDemo:
    def __init__(self, config, intent, snapshot, review, *, runner=run, infer=None):
        self.context = validate_kube_context(config["context"])
        self.intent, self.snapshot, self.review = intent, snapshot, review
        self.pod_name, self.output, self.run, self.infer = config["pod"], Path(config["output"]), runner, infer
        self.name = "runtime-tg-exec-01"
        self.activated = False
        self.config = config
        self.emit = config.get("emit", lambda _: None)

    def kube(self, *args, namespace=None):
        return self.run(
            ["kubectl", "--context", self.context, "-n", namespace or self.intent.namespace, *args], timeout=70
        )

    def pod(self):
        result = self.kube("get", "pod", self.pod_name, "-o", "json")
        if result.returncode:
            raise ValueError("target Pod unavailable")
        pod = json.loads(result.stdout)
        labels = pod["metadata"].get("labels", {})
        if not all(labels.get(k) == v for k, v in self.intent.engine.labels.items()):
            raise ValueError("target Pod does not match reviewed engine selector")
        return pod

    def activate(self, report):
        self.emit("[1/5 규칙 생성] enforcement.tetragon.render_manifests")
        expected = render_manifests(self.intent)["tetragon-tg-exec-01.json"]["spec"]
        self.emit("[2/5 승인 대조] operations.safety.evaluate — 기존 행별 승인 자료")
        snapshot = (
            read_status(self.config["status_path"], self.context) if self.config.get("status_path") else self.snapshot
        )
        review = read_object(self.config["review_path"]) if self.config.get("review_path") else self.review
        snapshot = dict(snapshot, verifications=[reassess_record(r) for r in snapshot.get("verifications", [])])
        self.emit("[3/5 전환 검사] operations.safety.evaluate — 6개 조건 모두 확인")
        ready = evaluate(snapshot, self.name, self.intent, review, self.monitor_seconds)
        if snapshot.get("context") != self.context or not all(ready["checks"].get(k) is True for k in CHECKS):
            raise ValueError("enforce checks changed before switching")
        if digest(comparable_spec(expected)) != ready["expected"]["spec_sha256"]:
            raise ValueError("generated policy differs from reviewed snapshot")
        report["transition"] = ready
        installed = self.kube("get", "tracingpolicynamespaced", self.name, "-o", "json")
        if (
            installed.returncode
            or digest(comparable_spec(json.loads(installed.stdout)["spec"]))
            != report["transition"]["expected"]["spec_sha256"]
        ):
            raise ValueError("live policy spec differs from approved snapshot")
        current = self.kube(
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
        row = parse_policy_row(current.stdout, self.name, self.intent.namespace) if current.returncode == 0 else None
        if not row or row["id"] != ready["expected"]["policy_id"] or row["mode"] != "monitor":
            raise ValueError("live policy identity or mode changed")
        self.activated = True
        self.emit("[4/5 반영] tools.tetragon_mode.switch_mode — enforce")
        switched = switch_mode(self.context, self.intent.namespace, self.name, "enforce", run=self.run)
        if not switched["ok"]:
            raise ValueError("enforce transition was not verified")

    def verify_enforced(self, report):
        if not self.activated:
            raise ValueError("this session has not passed activation checks")
        expected = report["transition"]["expected"]
        installed = self.kube("get", "tracingpolicynamespaced", self.name, "-o", "json")
        if (
            installed.returncode
            or digest(comparable_spec(json.loads(installed.stdout)["spec"])) != expected["spec_sha256"]
        ):
            raise ValueError("enforced policy spec changed")
        current = self.kube(
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
        row = parse_policy_row(current.stdout, self.name, self.intent.namespace) if current.returncode == 0 else None
        if not row or row["mode"] != "enforce" or row["id"] != expected["policy_id"]:
            raise ValueError("enforced policy identity or mode changed")

    def exercise(self, report, *, already_enforced=False):
        self.emit("[5/5 검증] 정상 추론 → 셸 실행 시도 → 같은 Pod·시도 구간의 이벤트 → 정상 추론")
        before = self.pod()
        report["normal_before"] = self.infer()
        if report["normal_before"] != 200:
            raise ValueError("normal inference failed before the probe")
        if already_enforced:
            self.verify_enforced(report)
        else:
            self.activate(report)
        result = self.kube(
            "exec", self.pod_name, "-c", self.intent.engine.container_name or "engine", "--", "python3", "-c", PROBE
        )
        report["command"] = {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        probe = json.loads(result.stdout.splitlines()[-1]) if result.returncode == 0 else {}
        pods_result = self.kube(
            "get", "pods", "-l", "app.kubernetes.io/name=tetragon", "-o", "json", namespace="kube-system"
        )
        agents = [
            p
            for p in json.loads(pods_result.stdout)["items"]
            if p["spec"].get("nodeName") == before["spec"]["nodeName"]
        ]
        if len(agents) != 1:
            raise ValueError("same-node event source unavailable")

        def fetch_events():
            result = self.kube(
                "logs",
                agents[0]["metadata"]["name"],
                "-c",
                "export-stdout",
                "--since-time=" + probe["started_at"],
                namespace="kube-system",
            )
            if result.returncode:
                raise ValueError("event source unavailable")
            return result.stdout

        report["events"] = wait_bound_events(
            fetch_events,
            {
                "policy": self.name,
                "pod_uid": before["metadata"]["uid"],
                "start": probe["started_at"],
                "end": probe["finished_at"],
            },
        )
        report["normal_after"] = self.infer()
        after = self.pod()
        report["pod_stable"] = (
            before["metadata"]["uid"] == after["metadata"]["uid"]
            and before["status"]["containerStatuses"] == after["status"]["containerStatuses"]
        )
        report["actual_errno"] = sorted(
            {row["errno"] for row in probe.get("cases", []) if type(row.get("errno")) is int}
        )
        report["denial_confirmed"] = bool(
            probe_passed(probe)
            and (self.config.get("required_errno") is None or report["actual_errno"] == [self.config["required_errno"]])
            and report["events"]
            and report["normal_before"] == report["normal_after"] == 200
            and report["pod_stable"]
        )

        report["actual_errno"] = sorted(
            {row["errno"] for row in probe.get("cases", []) if type(row.get("errno")) is int}
        )
        errors = ", ".join(
            f"{errno.errorcode.get(value, 'UNKNOWN')} (errno {value})" for value in report["actual_errno"]
        )
        report["event_summary"] = "실행 거부 확인: " + errors if report["denial_confirmed"] else "실제 거부 확인 안 됨"
        self.emit("거부 확인 (EPERM·이벤트 연결·이후 추론·재시작 0)" if report["denial_confirmed"] else "시도 기록만")

    def run_rounds(self, report):
        rounds = self.config.get("rounds", 1)
        if type(rounds) is not int or not 1 <= rounds <= 3:
            raise ValueError("demo rounds must be between one and three")
        history = []
        for index in range(rounds):
            self.emit(f"검증 회차 {index + 1}/{rounds}")
            report["denial_confirmed"] = False
            try:
                self.exercise(report, already_enforced=self.activated)
            finally:
                history.append(
                    {
                        k: report.get(k)
                        for k in ("normal_before", "normal_after", "denial_confirmed", "pod_stable", "actual_errno")
                    }
                )
                report["runs"] = history
            if not report["denial_confirmed"]:
                break
        report["denial_confirmed"] = len(history) == rounds and all(r["denial_confirmed"] for r in history)

    def execute(self, *, allow_isolated_trials=False, allow_engine_pod=False, monitor_seconds=86400):
        if not allow_isolated_trials or not allow_engine_pod:
            raise ValueError("both --allow-isolated-trials and --allow-engine-pod are required")
        if type(monitor_seconds) is not int or monitor_seconds < 0:
            raise ValueError("monitor seconds must match the approved agent configuration")
        self.monitor_seconds = monitor_seconds
        if self.infer is None or self.snapshot.get("context") != self.context:
            raise ValueError("explicit inference input and matching context are required")
        report = {
            "notice": NOTICE,
            "started_at": now(),
            "data_origin": "real" if self.run is run else "synthetic",
            "denial_confirmed": False,
            "monitor_seconds": monitor_seconds,
            "monitor_setting": "LAB 설정으로 관찰 기간 축소" if monitor_seconds < 86400 else "운영 기본 관찰 기간",
        }
        snapshot = dict(
            self.snapshot, verifications=[reassess_record(r) for r in self.snapshot.get("verifications", [])]
        )
        report["transition"] = evaluate(snapshot, self.name, self.intent, self.review, monitor_seconds)
        if not all(report["transition"]["checks"].get(k) is True for k in CHECKS):
            raise ValueError("all six enforce checks must pass")
        self.output.mkdir(parents=True, exist_ok=False)
        try:
            self.run_rounds(report)
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            report["error"] = type(error).__name__
        finally:
            report["restore"] = (
                switch_mode(self.context, self.intent.namespace, self.name, "monitor", run=self.run)
                if self.activated
                else {"ok": True, "reason": "no mode change attempted"}
            )
            if not report["restore"]["ok"]:
                report["denial_confirmed"] = False
            report["finished_at"] = now()
            report["result"] = "거부 확인" if report["denial_confirmed"] else "시도 기록만"
            atomic_json(self.output / "result.json", report, mode=0o600)
            table = NOTICE + "\n\n항목 | 결과\n--- | ---\n"
            rows = [
                ("정책", self.name),
                ("관찰 기간", report["monitor_setting"]),
                ("시작 모드", report["transition"]["expected"]["mode"]),
                ("전환 검사", "6/6 통과"),
                ("시험 전 정상 추론", report.get("normal_before", "확인 안 됨")),
                ("시험 뒤 정상 추론", report.get("normal_after", "확인 안 됨")),
                ("Pod 재시작", "없음" if report.get("pod_stable") else "확인 필요"),
                ("판정", report["result"]),
                ("실제 오류", report.get("event_summary", "확인 안 됨")),
            ]
            table += "".join(f"{label} | {value}\n" for label, value in rows)
            table += "\n" + POD_SCOPE + "\n한계: " + LIMITS + "\n"
            (self.output / "result.md").write_text(table)

        return report


def main():
    parser = argparse.ArgumentParser(description="정상 추론과 셸 실행 거부를 한 번에 확인합니다")
    add_options(
        parser,
        [
            (("--" + name,), {"required": True})
            for name in (
                "context",
                "intent",
                "status",
                "review",
                "pod",
                "output",
                "request",
                "token-file",
                "gateway-url",
            )
        ],
    )
    add_options(
        parser,
        [
            (("--allow-isolated-trials",), {"action": "store_true"}),
            (("--allow-engine-pod",), {"action": "store_true"}),
            (("--monitor-seconds",), {"type": int, "default": 86400}),
        ],
    )
    args = parser.parse_args()
    print(FIRST_NOTICE)
    print("관찰 기간은 LAB 설정" if args.monitor_seconds < 86400 else "관찰 기간은 운영 기본값")
    print(POD_SCOPE)
    body = Path(args.request).read_bytes()
    demo = ExecDemo(
        {
            "context": args.context,
            "pod": args.pod,
            "output": args.output,
            "status_path": args.status,
            "review_path": args.review,
            "required_errno": 1,
            "rounds": 3,
            "emit": print,
        },
        load_intent(args.intent),
        read_status(args.status, args.context),
        read_object(args.review),
        infer=lambda: normal_request(args.gateway_url, body, args.token_file),
    )
    report = demo.execute(
        allow_isolated_trials=args.allow_isolated_trials,
        allow_engine_pod=args.allow_engine_pod,
        monitor_seconds=args.monitor_seconds,
    )
    print((Path(args.output) / "result.md").read_text())
    return 0 if report["denial_confirmed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
