"""Bounded AWS GPU lab readiness snapshot; generation probes require opt-in."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Callable, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from tools.tetragon_capability import collect_capabilities
except ModuleNotFoundError:  # direct ``python tools/aws_lab_preflight.py``
    from tetragon_capability import collect_capabilities


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


def lab_management_scope(bundle_dir: str | Path, *, run_id: str, environment: str) -> dict[str,str]:
    """Derive audit references from the same verified immutable bundle as Gateway."""
    from serving_runtime.mvp.policy_bundle import verify_policy_bundle
    bundle=verify_policy_bundle(bundle_dir)
    result={'target_ref':bundle.target_ref,'policy_ref':bundle.policy_ref['sha256'],
        'run_id':run_id,'environment':environment}
    if any(type(v)is not str or re.fullmatch(r'[\w.:-]{1,128}',v) is None for v in result.values()):
        raise ValueError('lab management scope must contain four safe explicit references')
    return result


def _bind_lab_flag(arguments: list, flag: str, value: str, placeholder: str) -> None:
    positions=[i for i,item in enumerate(arguments) if item==flag or type(item)is str and item.startswith(flag+'=')]
    if len(positions)>1:raise ValueError('duplicate lab deployment flag: '+flag)
    if not positions:arguments.extend([flag,value]);return
    index=positions[0]
    if arguments[index]!=flag:
        old=arguments[index].split('=',1)[1]
        if old not in (value,placeholder):raise ValueError('mismatched lab deployment flag: '+flag)
        arguments[index]=flag+'='+value;return
    if index+1>=len(arguments) or arguments[index+1] not in (value,placeholder):
        raise ValueError('mismatched lab deployment flag: '+flag)
    arguments[index+1]=value


def bind_lab_management(engine_deployment: dict, scope: dict[str,str]) -> dict:
    """Prepare only; no cluster operations and no invented actor attribution."""
    if type(scope)is not dict or set(scope)!={'target_ref','policy_ref','run_id','environment'} or any(
            type(v)is not str or re.fullmatch(r'[\w.:-]{1,128}',v) is None for v in scope.values()):
        raise ValueError('four explicit management scope references are required')
    engine=deepcopy(engine_deployment);template=engine['spec']['template']
    labels=template.setdefault('metadata',{}).setdefault('labels',{})
    if labels.get('guard.run') not in (None,'REPLACE_RUN_ID',scope['run_id']):
        raise ValueError('management scope run differs from engine guard.run')
    labels['guard.run']=scope['run_id']
    containers=[c for c in template['spec']['containers'] if c.get('name')=='management-listener']
    if len(containers)!=1:raise ValueError('one existing management listener is required')
    args=containers[0]['args']
    for name,value in scope.items():
        _bind_lab_flag(args,'--'+name.replace('_','-'),value,'REPLACE_'+name.upper())
    return engine


def bind_lab_report(gateway_deployment: dict, *, host_directory: str | Path,
                    report_file: str='report.json') -> tuple[dict,dict]:
    """Mount an actual LabChecker report read-only; other verification schemas fail closed.

    The operator supplies the protected public report directory (root:10000,
    directory0750/file0640) after the existing report writer and secret scan.
    The full source remains immutable; Runtime's existing LabReportSource
    controls the API projection. No report result is synthesized here.
    """
    from serving_runtime.mvp.lab_report import LabReportSource
    directory=Path(host_directory)
    if not directory.is_absolute() or directory.is_symlink() or Path(report_file).name!=report_file \
            or not report_file.endswith('.json') or report_file in ('.','..'):
        raise ValueError('lab report source must be one explicit JSON in an absolute directory')
    source=directory/report_file
    if source.is_symlink():raise ValueError('lab report source cannot be a symlink')
    raw=source.read_bytes();document=json.loads(raw);view=LabReportSource._validate(document)
    if not {'L-05b','L-05c'}<={c['id'] for c in view['cases']}:
        raise ValueError('actual lab report must include L-05b and L-05c outcomes')
    gateway=deepcopy(gateway_deployment);spec=gateway['spec']['template']['spec']
    containers=[c for c in spec['containers'] if c.get('name')=='gateway']
    if len(containers)!=1:raise ValueError('one existing Gateway is required')
    container=containers[0]
    _bind_lab_flag(container['args'],'--lab-report','/runtime-lab/'+report_file,'REPLACE_LAB_REPORT')
    volume={'name':'runtime-lab-report','hostPath':{'path':str(directory),'type':'Directory'}}
    mount={'name':'runtime-lab-report','mountPath':'/runtime-lab','readOnly':True}
    for values,value,key in ((spec.setdefault('volumes',[]),volume,'name'),
            (container.setdefault('volumeMounts',[]),mount,'mountPath')):
        found=[old for old in values if old.get(key)==value[key]]
        if len(found)>1 or found and found[0]!=value:raise ValueError('conflicting lab report mount')
        if not found:values.append(value)
    return gateway,{'schema':'byul-lab-report-deployment-input/1','report_run_id':view['run_id'],
        'source_schema':view['schema'],'context':view['context'],'namespace':view['namespace'],
        'source_sha256':hashlib.sha256(raw).hexdigest(),'source_bytes':len(raw),
        'mounted_path':'/runtime-lab/'+report_file,'read_only':True,'outcomes_preserved':True}


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


BOUNDARY_CODE = r'''
import json, sys, urllib.request, urllib.error
limit, engine, route = int(sys.argv[1]), sys.argv[2], sys.argv[3]
base = 'http://127.0.0.1:8000'
def request(path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=300) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, None
def payload(text, tokens=None):
    value = {'model': 'test-model'}
    if route == 'chat':
        value['messages'] = [{'role': 'user', 'content': text}]
        if tokens is None and engine == 'vllm':
            value['add_generation_prompt'] = True
    else:
        value['prompt'] = text
    if tokens is not None:
        value['ignore_eos'] = True
        value['max_completion_tokens' if route == 'chat' and engine == 'sglang' else 'max_tokens'] = tokens
    return value
low, high, selected = 1, max(4096, limit * 4), None
for _ in range(32):
    if low > high:
        break
    middle = (low + high) // 2
    status, data = request('/tokenize', payload(('hello ' * middle).strip()))
    count = data.get('count') if isinstance(data, dict) else None
    if status != 200 or type(count) is not int or count < 0:
        break
    if limit - 8 <= count < limit:
        selected = middle, count
        break
    if count < limit - 8:
        low = middle + 1
    else:
        high = middle - 1
if selected is None:
    print(json.dumps({'state': 'unknown', 'reason': '토큰 수를 못 찾음'}))
    sys.exit()
repeats, count = selected
output = limit - count
status, data = request('/v1/' + ('chat/completions' if route == 'chat' else 'completions'),
                       payload(('hello ' * repeats).strip(), output))
usage = data.get('usage') if isinstance(data, dict) else None
completion = usage.get('completion_tokens') if isinstance(usage, dict) else None
if 400 <= status < 500:
    state, reason = 'fail', '경계 거부'
elif status == 200 and type(completion) is int and completion == output:
    state, reason = 'ok', '요청 답변 길이 생성'
elif status == 200 and type(completion) is int and completion < output:
    state, reason = 'fail', '답변 잘림'
else:
    state, reason = 'unknown', '엔진 응답 또는 usage 미확인'
print(json.dumps({'state': state, 'reason': reason, 'http_status': status,
                  'input_tokens': count, 'requested_completion_tokens': output,
                  'completion_tokens': completion if type(completion) is int else None}))
'''


def _policy_limit(url: str, env_file: str | None, opener: Callable) -> int | None:
    token = _token(env_file)
    for authorization in (None, token):
        try:
            headers = {"Authorization": f"Bearer {authorization}"} if authorization else {}
            with opener(Request(url.rstrip("/") + "/api/policy", headers=headers), timeout=5) as response:
                document = json.load(response)
                if response.status != 200 or not isinstance(document, dict):
                    return None
                current = document.get("current") or document.get("policy") or {}
                value = current.get("max_context_tokens") if isinstance(current, dict) else None
                return value if isinstance(value, int) and not isinstance(value, bool) and value > 8 else None
        except HTTPError as error:
            if error.code in (401, 403) and authorization is None and token:
                continue
            return None
        except (URLError, OSError, ValueError, TimeoutError):
            return None
    return None


def _boundary_checks(args: argparse.Namespace, runner: Runner, opener: Callable) -> list[Check]:
    limit = _policy_limit(args.gateway_url, args.env_file, opener)
    if limit is None:
        return [_check("boundary_probe:" + route, "unknown", "정책 context 상한 조회 불가") for route in ("chat", "completions")]
    base = ["kubectl", "--context", args.context, "-n", args.namespace,
            "exec", "deployment/serving-engine", "-c", "engine", "--", "python3", "-c", BOUNDARY_CODE]
    checks = []
    for route in ("chat", "completions"):
        result = runner.run(base + [str(limit), args.engine, route], timeout=600)
        try:
            document = json.loads(result.stdout) if result.returncode == 0 else None
        except ValueError:
            document = None
        if not isinstance(document, dict) or document.get("state") not in {"ok", "fail", "unknown"}:
            checks.append(_check("boundary_probe:" + route, "unknown", "엔진 Pod 실측 결과 없음"))
        else:
            checks.append(_check("boundary_probe:" + route, document["state"], document.get("reason", "결과 사유 없음")))
    return checks


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
    try:
        is_link = current.is_symlink()
        target = os.readlink(current) if is_link else ""
        complete = bool(target) and not os.path.isabs(target) and Path(target).name == target and (root / target).is_dir()
    except PermissionError:
        is_link = None
    if is_link is None:
        # The bundle root is root:10000 0750; the operator account may not read it.
        checks.append(_check("bundle", "unknown", "묶음 폴더 읽기 권한 없음; sudo로 bundle verify 별도 확인"))
    elif not is_link:
        checks.append(_check("bundle", "fail", "current가 심볼릭 링크 아님"))
    elif not complete:
        checks.append(_check("bundle", "fail", "상대 링크가 완성된 묶음을 가리키지 않음"))
    else:
        verified = runner.run([sys.executable, "-m", "serving_runtime", "bundle", "verify", "--bundle-dir", str(root / target)], timeout=15)
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
        enabled = "--phase-timeline" in exec_start and (args.engine == "vllm" or "--phase-engine sglang" in exec_start)
        checks.append(_check("phase_timeline", "ok" if enabled else "fail", "단계 시간표 설정 일치" if enabled else "단계 시간표 설정 불일치"))
    obs = Path(args.observation_dir)
    for name in ("hubble.heartbeat.json", "tetragon.heartbeat.json", "target-pods.json", "phase-timeline.json"):
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
    if getattr(args, "override_check", False):
        capability = collect_capabilities(runner, args.context)
        checks.append(_check("kernel", "ok" if capability["kernel"] else "unknown",
                             capability["kernel"] or "커널 버전 조회 실패"))
        for name, key in (("CONFIG_BPF_KPROBE_OVERRIDE", "config_bpf_kprobe_override"),
                          ("CONFIG_BPF_LSM", "config_bpf_lsm"),
                          ("tetra_override_return", "tetragon_override_return"),
                          ("tetra_lsm", "tetragon_lsm")):
            value = capability[key]
            checks.append(_check(name, "ok" if value is True else "fail" if value is False else "unknown",
                                 "true" if value is True else "false" if value is False else "조회 불가"))
        checks.append(_check("kernel_lsm", "ok" if capability["lsm"] is not None else "unknown",
                             ", ".join(capability["lsm"]) if capability["lsm"] is not None else "LSM 목록 조회 불가"))
        checks.append(_check("error_injection_symbols", "ok" if capability["error_injection_symbols"] else "fail" if capability["error_injection_list_readable"] else "unknown",
                             ", ".join(capability["error_injection_symbols"]) or "오류 주입 목록의 대상 심볼 미확인"))
        recommendation = capability["recommended"]
        checks.append(_check("tetragon_enforcement", "ok" if recommendation else "unknown",
                             "권장 방식 " + recommendation if recommendation else "차단 방식 근거 부족; 시험용 Pod 로드 확인 필요"))
        checks.append(_check("tetragon_enforcement_methods", "ok" if capability["available"] else "unknown",
                             "가능: " + ", ".join(capability["available"])
                             + "; 불가: " + ", ".join(capability["unavailable"])
                             + "; 확인 필요: " + ", ".join(capability["unknown"])))
        checks.append(_check("apparmor", "ok" if capability["apparmor"] is True else "unknown",
                             "LSM에 AppArmor 있음" if capability["apparmor"] is True else "LSM AppArmor 활성 확인 필요"))
    if getattr(args, "boundary_probe", False):
        checks.extend(_boundary_checks(args, runner, opener))
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
    parser.add_argument("--boundary-probe", action="store_true", help="엔진 직접 생성 요청으로 정책 경계 실측")
    parser.add_argument("--override-check", action="store_true", help="kprobe Override 수동 확인 명령과 미확인 상태 표시")
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
