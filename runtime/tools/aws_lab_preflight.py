"""Read-only, bounded AWS GPU lab readiness snapshot. Output contains no raw command data."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner:
    def run(self, argv: Sequence[str], *, timeout: float = 8) -> CommandResult:
        try:
            result = subprocess.run(list(argv), capture_output=True, text=True, timeout=timeout, check=False)
            return CommandResult(result.returncode, result.stdout, result.stderr)
        except (OSError, subprocess.TimeoutExpired):
            return CommandResult(124)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str


def _check(name: str, status: str, reason: str) -> Check:
    return Check(name, status, reason)


def _json_command(runner: Runner, argv: Sequence[str]) -> tuple[dict | None, str]:
    result = runner.run(argv)
    if result.returncode:
        return None, "조회 명령 실패"
    try:
        value = json.loads(result.stdout)
        return (value, "") if isinstance(value, dict) else (None, "JSON 형식 오류")
    except ValueError:
        return None, "JSON 형식 오류"


def _ready_pod(document: dict, label: str, container: str) -> tuple[Check, str, str]:
    pods = [p for p in document.get("items", []) if p.get("metadata", {}).get("deletionTimestamp") is None and p.get("status", {}).get("phase") in {"Pending", "Running"}]
    if len(pods) != 1:
        return _check(label, "fail", "현재 Pod 수가 1이 아님"), "", ""
    pod = pods[0]
    spec = next((c for c in pod.get("spec", {}).get("containers", []) if c.get("name") == container), {})
    state = next((c for c in pod.get("status", {}).get("containerStatuses", []) if c.get("name") == container), {})
    image = spec.get("image", "")
    image_id = state.get("imageID", "")
    ready = bool(state.get("ready")) and any(c.get("type") == "Ready" and c.get("status") == "True" for c in pod.get("status", {}).get("conditions", []))
    if not image or not image_id:
        return _check(label, "unknown", "이미지 또는 imageID 없음"), image, image_id
    safe_tag = image.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if "@" not in image else "digest 고정"
    safe_id = _digest(image_id)[:12] or "형식 미확인"
    detail = f"이미지 {safe_tag}; imageID sha256:{safe_id}"
    return _check(label, "ok" if ready else "fail", ("Pod Ready; " if ready else "Pod 준비되지 않음; ") + detail), image, image_id


def _digest(value: str) -> str:
    match = re.search(r"sha256:([0-9a-fA-F]{64})(?:$|[^0-9a-fA-F])", value)
    return match.group(1).lower() if match else ""


def _token(path: str | None) -> str | None:
    if not path:
        return None
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.startswith("RUNTIME_VIEWER_TOKEN="):
                return line.partition("=")[2].strip().strip('"').strip("'")
    except OSError:
        return None
    return None


def _gateway(url: str, env_file: str | None, opener: Callable) -> Check:
    token = _token(env_file)
    def request(auth: str | None):
        headers = {"Authorization": f"Bearer {auth}"} if auth else {}
        with opener(Request(url.rstrip("/") + "/api/status", headers=headers), timeout=5) as response:
            return response.status, json.load(response)
    try:
        try:
            status, data = request(None)
        except HTTPError as error:
            if error.code not in (401, 403):
                return _check("gateway_policy", "fail", "상태 조회 HTTP 오류")
            if not token:
                return _check("gateway_policy", "unknown", "조회 인증 필요; viewer token 없음")
            status, data = request(token)
        if status != 200 or not isinstance(data, dict):
            return _check("gateway_policy", "fail", "상태 응답 형식 오류")
        policy = data.get("policy_state") or data.get("policy") or {}
        approval = policy.get("approval") or {}
        if policy.get("gateway_active") is True and approval.get("state") == "verified":
            return _check("gateway_policy", "ok", "정책 활성; 승인 verified")
        return _check("gateway_policy", "fail", "정책 비활성 또는 승인 verified 아님")
    except (HTTPError, URLError, OSError, ValueError, TimeoutError):
        return _check("gateway_policy", "unknown", "상태 조회 불가")


def collect(args: argparse.Namespace, runner: Runner | None = None, *, opener: Callable = urlopen, now: datetime | None = None) -> list[Check]:
    runner = runner or Runner()
    now = now or datetime.now(timezone.utc)
    checks: list[Check] = []
    context = runner.run(["kubectl", "config", "current-context"])
    checks.append(_check("context", "unknown" if context.returncode else ("ok" if context.stdout.strip() == args.context else "fail"), "context 조회 실패" if context.returncode else ("일치" if context.stdout.strip() == args.context else "기대 context와 다름")))
    base = ["kubectl", "--context", args.context]
    nodes, error = _json_command(runner, base + ["get", "nodes", "-o", "json"])
    if nodes is None:
        checks.append(_check("node_gpu", "unknown", error))
    else:
        ready_nodes = [n for n in nodes.get("items", []) if any(c.get("type") == "Ready" and c.get("status") == "True" for c in n.get("status", {}).get("conditions", []))]
        try:
            allocatable = sum(int(n.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu", 0)) for n in ready_nodes)
        except (ValueError, TypeError):
            allocatable = 0
        checks.append(_check("node_gpu", "ok" if ready_nodes and allocatable >= 1 else "fail", f"Ready 노드 {len(ready_nodes)}; 할당 가능 GPU {allocatable}"))
    images: dict[str, tuple[str, str]] = {}
    for name, container in (("serving-engine", "engine"), ("serving-gateway", "gateway")):
        pods, error = _json_command(runner, base + ["-n", args.namespace, "get", "pods", "-l", f"app={name}", "-o", "json"])
        if pods is None:
            checks.append(_check(name, "unknown", error))
        else:
            state, image, image_id = _ready_pod(pods, name, container)
            checks.append(state)
            images[name] = image, image_id
    gateway_image = images.get("serving-gateway", ("", ""))[0]
    if not args.expect_gateway_tag:
        checks.append(_check("gateway_tag", "unknown", "기대 태그 미지정"))
    elif not gateway_image:
        checks.append(_check("gateway_tag", "unknown", "Gateway 이미지 정보 없음"))
    else:
        checks.append(_check("gateway_tag", "ok" if gateway_image.rsplit(":", 1)[-1] == args.expect_gateway_tag else "fail", "기대 태그 일치" if gateway_image.rsplit(":", 1)[-1] == args.expect_gateway_tag else "기대 태그와 다름"))
    engine_image, engine_id = images.get("serving-engine", ("", ""))
    if not args.expect_engine_digest:
        checks.append(_check("engine_digest", "unknown", "기대 digest 미지정"))
    elif not engine_id:
        checks.append(_check("engine_digest", "unknown", "엔진 imageID 없음"))
    else:
        expected = _digest(args.expect_engine_digest)
        matches = bool(expected) and expected in {_digest(engine_id), _digest(engine_image.partition("@")[2])}
        checks.append(_check("engine_digest", "ok" if matches else "fail", "엔진 선언 이미지 또는 Pod imageID digest 일치" if matches else "엔진 이미지 digest 불일치"))
    root = Path(args.bundle_root)
    current = root / "current"
    if not current.is_symlink():
        checks.append(_check("bundle", "fail", "current가 심볼릭 링크 아님"))
    else:
        target = os.readlink(current)
        if os.path.isabs(target) or Path(target).name != target or not (root / target).is_dir():
            checks.append(_check("bundle", "fail", "상대 링크가 완성된 묶음을 가리키지 않음"))
        else:
            verified = runner.run(["serving-runtime", "bundle", "verify", "--bundle-dir", str(root / target)], timeout=15)
            checks.append(_check("bundle", "ok" if verified.returncode == 0 else "fail", "상대 링크와 묶음 검증 성공" if verified.returncode == 0 else "묶음 검증 실패"))
    service = runner.run(["systemctl", "--user", "is-active", "byul-observation-feed"])
    checks.append(_check("collector_service", "ok" if service.returncode == 0 and service.stdout.strip() == "active" else "fail", "active" if service.returncode == 0 and service.stdout.strip() == "active" else "active 아님"))
    service_args = runner.run(["systemctl", "--user", "show", "byul-observation-feed", "-p", "ExecStart"])
    exec_start = service_args.stdout.strip().partition("=")[2].strip() if service_args.stdout.strip().startswith("ExecStart=") else ""
    if service_args.returncode:
        checks.append(_check("phase_timeline", "unknown", "수집기 실행 옵션 조회 실패"))
    elif not exec_start:
        checks.append(_check("phase_timeline", "unknown", "수집기 ExecStart 값 없음"))
    else:
        enabled = "--phase-timeline" in exec_start
        expected_enabled = args.engine == "vllm"
        checks.append(_check("phase_timeline", "ok" if enabled == expected_enabled else "fail", "단계 시간표 설정 일치" if enabled == expected_enabled else "단계 시간표 설정 불일치"))
    obs = Path(args.observation_dir)
    for name in ("hubble.heartbeat.json", "tetragon.heartbeat.json", "target-pods.json") + (("phase-timeline.json",) if args.engine == "vllm" else ()):
        try:
            age = now.timestamp() - (obs / name).stat().st_mtime
            status = "ok" if -5 <= age <= 30 else "fail"
            reason = "30초 이내" if status == "ok" else "신선도 30초 초과 또는 미래 시각"
        except OSError:
            status, reason = "fail", "파일 없음 또는 읽기 불가"
        checks.append(_check("observation:" + name, status, reason))
    for resource, names in (("tracingpoliciesnamespaced", ("runtime-tg-asset-01", "runtime-tg-cache-01", "runtime-tg-secret-01")), ("ciliumnetworkpolicies", ("runtime-engine-egress", "runtime-engine-ingress", "runtime-gateway-egress", "runtime-observer-0-egress"))):
        doc, error = _json_command(runner, base + ["-n", args.namespace, "get", resource, "-o", "json"])
        if doc is None:
            checks.append(_check(resource, "unknown", error))
        else:
            present = {item.get("metadata", {}).get("name") for item in doc.get("items", [])}
            checks.append(_check(resource, "ok" if set(names) <= present else "fail", "필수 정책 존재" if set(names) <= present else "필수 정책 누락"))
    checks.append(_gateway(args.gateway_url, args.env_file, opener))
    try:
        free = shutil.disk_usage(args.evidence_dir).free
        checks.append(_check("disk", "ok" if free >= 5 * 1024**3 else "fail", "여유 5 GiB 이상" if free >= 5 * 1024**3 else "여유 5 GiB 미만"))
    except OSError:
        checks.append(_check("disk", "unknown", "디스크 조회 불가"))
    try:
        image_free = shutil.disk_usage(args.image_store).free
        minimum = args.min_image_free_gib * 1024**3
        checks.append(_check("image_store_disk", "ok" if image_free >= minimum else "fail", f"이미지 저장소 여유 {args.min_image_free_gib:g} GiB 이상" if image_free >= minimum else f"이미지 저장소 여유 {args.min_image_free_gib:g} GiB 미만"))
    except OSError:
        checks.append(_check("image_store_disk", "unknown", "이미지 저장소 디스크 조회 불가"))
    evidence = Path(args.evidence_dir)
    writable = evidence.is_dir() and os.access(evidence, os.W_OK | os.X_OK) and bool(evidence.stat().st_mode & 0o222)
    checks.append(_check("evidence_dir", "ok" if writable else "fail", "기존 폴더 쓰기 가능" if writable else "폴더 없거나 쓰기 불가"))
    return checks


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("vllm", "sglang"), required=True)
    parser.add_argument("--context", default="byul-aws-lab")
    parser.add_argument("--namespace", default="serving-lab")
    parser.add_argument("--bundle-root", default="/srv/serving/bundles")
    parser.add_argument("--observation-dir", default="/srv/serving/observation")
    parser.add_argument("--evidence-dir", default="evidence")
    parser.add_argument("--image-store", default="/var/lib/containerd")
    parser.add_argument("--min-image-free-gib", type=float, default=60)
    parser.add_argument("--gateway-url", default="http://127.0.0.1:18080")
    parser.add_argument("--expect-gateway-tag")
    parser.add_argument("--expect-engine-digest", help="expected declared image or Pod imageID sha256 digest")
    parser.add_argument("--env-file", help="private file with RUNTIME_VIEWER_TOKEN")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    checks = collect(args)
    if args.json:
        print(json.dumps({"engine": args.engine, "checks": [asdict(c) for c in checks]}, ensure_ascii=False))
    else:
        print("항목 | 상태 | 사유")
        print("--- | --- | ---")
        for check in checks:
            print(f"{check.name} | {check.status} | {check.reason}")
    return 1 if any(c.status == "fail" for c in checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
