"""Read-only live Kubernetes observations for an UNAPPROVED environment draft.

No raw kubectl output, image repository, Pod/node identity, env var, or Secret is
persisted. Unknowns live in an accompanying observation map, never as invented
contract values. The partial environment needs human completion before import.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

from serving_runtime.mvp.assets import snapshot, _walk
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.target import load_target, summarize_pods
try:
    from tools.aws_lab_preflight import Runner
    from tools.verification_material import atomic_write, encode, now, read_json
    from tools.candidate_errors import CandidateError, CandidateArgumentParser, operation
except ModuleNotFoundError:
    from aws_lab_preflight import Runner
    from verification_material import atomic_write, encode, now, read_json
    from candidate_errors import CandidateError, CandidateArgumentParser, operation

SCHEMA = "runtime-environment-candidate/1.0"
VERSION = re.compile(r"(?<![\w.])v?(\d+\.\d+\.\d+[A-Za-z0-9.+_-]*)")
ENGINE_PROBE = '''import importlib.metadata,json,sys,urllib.request
out={}
try: out['version']=importlib.metadata.version(sys.argv[1])
except Exception: pass
try:
 with urllib.request.urlopen('http://127.0.0.1:8000/v1/models',timeout=3) as r:
  data=json.load(r).get('data',[])
 if len(data)==1 and isinstance(data[0].get('id'),str): out['model_id']=data[0]['id']
except Exception: pass
print(json.dumps(out))
'''


def _json(runner, command):
    result = runner.run(command, timeout=8)
    if result.returncode:
        return {}, "KUBECTL_FAILED:kubectl (조회 실패 또는 시간 초과)"
    try:
        value = json.loads(result.stdout)
        return (value, "") if isinstance(value, dict) else ({}, "INVALID_JSON:kubectl (JSON 객체 아님)")
    except (ValueError, TypeError, RecursionError):
        return {}, "INVALID_JSON:kubectl (JSON 해석 실패)"


def _single_running(document, container, node=None):
    pods = []
    for pod in document.get("items", []):
        if (pod.get("metadata", {}).get("deletionTimestamp")
                or pod.get("status", {}).get("phase") != "Running"
                or (node and pod.get("spec", {}).get("nodeName") != node)):
            continue
        statuses = pod.get("status", {}).get("containerStatuses", [])
        if any(c.get("name") == container and c.get("ready") is True
               and "running" in c.get("state", {}) for c in statuses):
            pods.append(pod)
    return pods[0] if len(pods) == 1 else {}


def _safe_version(value):
    match = VERSION.search(value) if isinstance(value, str) else None
    return match.group(1) if match else ""


def _arg(args, name):
    for index, value in enumerate(args):
        if value == name and index + 1 < len(args):
            return args[index + 1]
        if isinstance(value, str) and value.startswith(name + "="):
            return value.split("=", 1)[1]
    return ""


def collect(args, runner=None):
    runner = runner or Runner()
    read_json(args.target_registration, "target_registration")
    target = load_target(args.target_registration)
    if target.state != "ok" or target.kubernetes is None or target.engine_kind not in {"vllm", "sglang"}:
        raise CandidateError("INVALID_INPUT", "target_registration")
    policy_raw = None
    if args.gateway_policy:
        policy_raw = read_json(args.gateway_policy, "gateway_policy")
        policy = load_policy(args.gateway_policy)
        if not policy.ok:
            raise CandidateError("INVALID_INPUT", "gateway_policy")
    kube = target.kubernetes
    observations, unconfirmed = {}, {}
    environment = {"schema_version": "byul-environment/0.1",
                   "environment_id": args.environment_id, "revision": args.revision,
                   "created_at": now(), "data_origin": "MEASURED",
                   "target": {"target_ref": target.target_id, "engine": {}, "model": {}, "runtime": {}},
                   "deployment": {"kind": "kubernetes", "kubernetes_context": kube.context,
                                  "namespace": kube.namespace, "engine_labels": dict(kube.selector),
                                  "engine_container": kube.container}, "facts": {}}
    fields = {}

    def record(path, value, source, reason="값 없음 또는 형식 미확인", *, destination=None, key=None):
        if value is None or value == "":
            item = {"state": "unconfirmed", "display": "미확인", "reason": reason, "source": source}
            unconfirmed[path] = item
        else:
            item = {"state": "observed", "value": value, "source": source}
            if destination is not None:
                destination[key or path.rsplit("/", 1)[-1]] = value
        observations[path] = item

    base = ["kubectl", "--context", kube.context]
    pods, pod_error = _json(runner, base + ["-n", kube.namespace, "get", "pods", "-l", kube.label_selector(), "-o", "json"])
    # Target module distinguishes multiple live Pods and missing/running states.
    try:
        summary = summarize_pods(pods, kube.container)
    except (ValueError, TypeError, KeyError):
        summary = {}
    instance = summary.get("instance") or {}
    pod = _single_running(pods, kube.container) if summary.get("state") == "ok" else {}
    if not pod:
        instance = {}
    engine = environment["target"]["engine"]
    model = environment["target"]["model"]
    runtime = environment["target"]["runtime"]
    record("/target/engine/image_digest", instance.get("image_digest"), "running Pod containerStatuses.imageID",
           pod_error or "단일 실행·Ready 엔진의 imageID digest를 확인할 수 없음", destination=engine)
    record("/target/engine/name", target.engine_kind if pod else "", "대상 등록의 수집기 종류(선언)", destination=engine)
    if pod:
        engine["runtime_kind"] = target.engine_kind
        fields["engine.kind"] = target.engine_kind
    spec = next((c for c in pod.get("spec", {}).get("containers", []) if c.get("name") == kube.container), {})
    node_name = pod.get("spec", {}).get("nodeName", "")
    pod_name = pod.get("metadata", {}).get("name", "")
    exec_base = base + ["-n", kube.namespace, "exec", pod_name, "-c", kube.container, "--"]
    probe, probe_error = _json(runner, exec_base + ["python3", "-B", "-c", ENGINE_PROBE, target.engine_kind]) if pod_name else ({}, "엔진 Pod 미확인")
    record("/target/engine/version", _safe_version(probe.get("version")), "engine importlib.metadata",
           probe_error or "설치 패키지 판본 조회 실패", destination=engine)
    record("/target/engine/source_commit", "", "engine package metadata", "이미지 digest에서 소스 commit을 추정하지 않음")
    model_id = probe.get("model_id")
    record("/target/model/id", model_id if isinstance(model_id, str) and model_id.strip() else "", "engine loopback /v1/models",
           probe_error or "단일 모델 ID 조회 실패", destination=model)

    # Resolve exactly the live engine's /models hostPath, never a guessed cache path.
    mount = next((m for m in spec.get("volumeMounts", []) if m.get("mountPath") == "/models"), {})
    volume = next((v for v in pod.get("spec", {}).get("volumes", []) if v.get("name") == mount.get("name")), {})
    root = volume.get("hostPath", {}).get("path", "")
    inventory = ""
    model_error = ""
    if root and not (mount.get("subPath") or mount.get("subPathExpr")):
        try:
            with operation("model_root"):
                snap = snapshot(root)
                _, links, _, errors = _walk(Path(root))
                if links or errors:
                    raise ValueError("INCOMPLETE_TREE")
            # Same all-scope file-list contract as update's model_inventory.py.
            files = [{"path": f["path"], "bytes": f["size"], "sha256": f["sha256"]} for f in snap["files"]]
            inventory = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        except CandidateError as error:
            model_error = str(error) + " "
    record("/target/model/inventory_sha256", inventory, "live /models hostPath; all-scope files hashed on host",
           model_error + "호스트 snapshot 해시를 완전하게 읽지 못함(권한·링크·배치 확인)", destination=model)
    # A directory named like a revision is only a mount generation, not provenance.
    record("/target/model/revision", "", "live /models hostPath", "폴더명을 모델 출처 revision으로 승인하지 않음")
    record("/target/model/tokenizer_revision", "", "live /models hostPath", "토크나이저 출처 revision은 사람의 근거 확인 필요")
    record("model_mount", "/models" if root else "", "running engine volumeMounts")
    record("model_mount_read_only", mount.get("readOnly") if root else "", "running engine volumeMounts")
    cache = next((m for m in spec.get("volumeMounts", []) if m.get("mountPath") == "/cache"), {})
    record("cache_mount", "/cache" if cache else "", "running engine volumeMounts", "캐시 마운트 미확인; 모델 snapshot과 별개")

    nodes, node_error = _json(runner, base + ["get", "nodes", "-o", "json"])
    node = next((n for n in nodes.get("items", []) if n.get("metadata", {}).get("name") == node_name), {}) if node_name else {}
    record("kernel", node.get("status", {}).get("nodeInfo", {}).get("kernelVersion"), "engine node status.nodeInfo.kernelVersion",
           node_error or "엔진 노드 커널 미확인")
    if node:
        runtime["nodes"] = 1
    gpu_result = runner.run(exec_base + ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], timeout=8) if pod_name else None
    gpu_rows = []
    if gpu_result and gpu_result.returncode == 0:
        for line in gpu_result.stdout.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and re.fullmatch(r"[A-Za-z0-9 ._()+-]{1,100}", parts[0]) and re.fullmatch(r"[0-9.]{1,32}", parts[1]):
                gpu_rows.append(tuple(parts))
            else:
                # A partial response cannot establish the number of visible GPUs.
                gpu_rows.clear()
                break
    gpu_error = "KUBECTL_FAILED:kubectl (조회 실패 또는 시간 초과)" if gpu_result and gpu_result.returncode else "가시 GPU/드라이버 미확인"
    unique = set(gpu_rows)
    record("/target/runtime/gpu_model", gpu_rows[0][0] if len(unique) == 1 else "", "engine nvidia-smi", gpu_error, destination=runtime)
    record("/target/runtime/driver", gpu_rows[0][1] if len(unique) == 1 else "", "engine nvidia-smi", gpu_error, destination=runtime)
    record("/target/runtime/gpu_count", len(gpu_rows) if gpu_rows else "", "engine nvidia-smi visible devices", gpu_error, destination=runtime)

    for name, selector, container, binary in (("tetragon", "app.kubernetes.io/name=tetragon", "tetragon", "tetra"),
                                               ("cilium", "k8s-app=cilium", "cilium-agent", "cilium-dbg")):
        agents, error = _json(runner, base + ["-n", "kube-system", "get", "pods", "-l", selector, "-o", "json"])
        agent = _single_running(agents, container, node_name) if node_name else {}
        agent_name = agent.get("metadata", {}).get("name")
        version = ""
        if agent_name:
            result = runner.run(base + ["-n", "kube-system", "exec", agent_name, "-c", container, "--", binary, "version"], timeout=8)
            if result.returncode == 0:
                version = _safe_version(result.stdout)
            else:
                error = "KUBECTL_FAILED:kubectl (조회 실패 또는 시간 초과)"
        record(name + "_version", version, "engine node agent executable version",
               error or "같은 노드의 단일 Ready 에이전트 판본 조회 실패")

    max_len = _arg(spec.get("args", []), "--max-model-len")
    if max_len.isdigit() and int(max_len) > 0:
        fields["option.max_model_len"] = int(max_len)
    record("option.max_model_len", fields.get("option.max_model_len"), "running engine args --max-model-len")
    for source, dest in ((engine, "engine"), (model, "model")):
        for name in ("version", "image_digest") if dest == "engine" else ("id", "inventory_sha256"):
            if name in source:
                fields[dest + "." + name] = source[name]

    if args.gateway_policy:
        if policy.ok:
            raw = policy_raw
            environment["deployment"]["gateway_schema_version"] = raw["schema_version"]
            # v1.2 requires the explicit cache-isolation mode for export.
            if raw["schema_version"] == "runtime-mvp/1.2":
                mode = (raw.get("cache_isolation") or {}).get("mode")
                record("/deployment/gateway_cache_isolation", mode, "local Gateway policy (declared)", destination=environment["deployment"])
            fields["gateway.schema_version"] = raw["schema_version"]
        else:
            record("/deployment/gateway_schema_version", "", "local Gateway policy", "정책 읽기/검증 실패")
    else:
        record("/deployment/gateway_schema_version", "", "local Gateway policy", "--gateway-policy 미지정")
    record("/deployment/enforcement_intent", "", "approved policy/environment", "기존 승인 intent를 사람이 확인·연결해야 함; 임의 생성 안 함")
    record("/facts", "", "environment review", "기능 사용/비사용 사실은 이 수집기만으로 판정하지 않음")
    return {"schema_version": SCHEMA, "state": "candidate_incomplete", "approval": "not_approved",
            "collected_at": now(), "environment": environment,
            "observations": observations, "unconfirmed": unconfirmed, "declaration_fields": fields,
            "import_ready": False,
            "reason": "환경 계약 필수 미확인 필드는 생략됨. 사람이 근거를 채우고 계약 검증 후 환경 판본으로 등록해야 합니다."}


def build_parser():
    parser = CandidateArgumentParser(description=__doc__)
    parser.add_argument("--target-registration", required=True, help="installed bundle target.json, with real context/selector")
    parser.add_argument("--environment-id", required=True)
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--gateway-policy", help="installed bundle Gateway policy; only version/cache mode is retained")
    parser.add_argument("--out", required=True)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        if args.revision < 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", args.environment_id):
            raise CandidateError("INVALID_INPUT", "environment_id_or_revision")
        value = collect(args)
        raw = encode(value)
        with operation("out"):
            atomic_write(args.out, raw)
        print("ENVIRONMENT_CANDIDATE_ONLY sha256=" + hashlib.sha256(raw).hexdigest())
        for code in sorted({item["reason"].split(" ", 1)[0] for item in value["unconfirmed"].values()
                            if item["reason"].startswith(("KUBECTL_FAILED:", "INVALID_JSON:", "PERMISSION_DENIED:", "NOT_FOUND:", "IO_ERROR:", "INVALID_INPUT:"))}):
            print("ENVIRONMENT_CANDIDATE_UNCONFIRMED: " + code, file=sys.stderr)
        print("미승인·미확인=" + str(len(value["unconfirmed"])) + "; 플랫폼 등록 전 계약 검증 필요")
        return 0
    except CandidateError as error:
        print(f"ENVIRONMENT_CANDIDATE_FAILED: {error}", file=sys.stderr)
        return 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print("ENVIRONMENT_CANDIDATE_FAILED: INVALID_INPUT:arguments", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
