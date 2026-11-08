"""신규 synthetic 입력을 실제 로컬 API에 연결한다. 설치·모델·네트워크 실행 없음.

guard의 검증/파일 소비와 선택한 기존 WSL CPU 환경의 직접 API를 구분한다.
vLLM import 실패를 대역으로 수복하지 않으며 기존 gate/판정/팩을 바꾸지 않는다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata as metadata
import inspect
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import traceback


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def file_ref(path, version=None):
    path = Path(path).resolve()
    return {"path": str(path), "sha256": sha(path.read_bytes()), "version": version}


def source_ref(obj, version=None):
    return file_ref(inspect.getsourcefile(obj) or obj.__file__, version)


def runtime():
    packages = {}
    for name in ("vllm", "torch", "transformers", "tokenizers", "safetensors", "pydantic", "numpy", "Pillow"):
        try:
            dist = metadata.distribution(name)
            packages[name] = {"version": dist.version, "root": str(dist.locate_file(""))}
        except metadata.PackageNotFoundError:
            packages[name] = {"installed": False, "exception": "PackageNotFoundError"}
    return {"python": sys.executable, "python_sha256": sha(Path(sys.executable).read_bytes()),
            "version": sys.version, "platform": platform.platform(), "pid": os.getpid(),
            "uid": os.getuid() if hasattr(os, "getuid") else None,
            "gid": os.getgid() if hasattr(os, "getgid") else None,
            "flags": {k: getattr(sys.flags, k) for k in ("isolated", "no_site", "dont_write_bytecode")},
            "packages": packages}


def invoke(name, function, value):
    began = time.perf_counter()
    try:
        result = function(deepcopy(value))
        return {"name": name, "input": value, "input_sha256": sha(encoded(value)),
                "outcome": "returned", "return_value": result, "elapsed_seconds": time.perf_counter() - began}
    except Exception as error:
        return {"name": name, "input": value, "input_sha256": sha(encoded(value)), "outcome": "raised",
                "exception": {"type": type(error).__name__, "module": type(error).__module__,
                              "message": str(error), "traceback": traceback.format_exc()},
                "elapsed_seconds": time.perf_counter() - began}


def record(check_id, component, target, calls, limitations, remaining, *, assertion_names=None):
    assertions = []
    for name, outcome in (assertion_names or []):
        assertions.append({"name": name, "passed": outcome})
    return {"check_id": check_id, "component": component, "execution_kind": "actual_local_component",
            "input_origin": "synthetic", "official": False, "formal_data_origin": "EXAMPLE",
            "command_or_api": component, "target_source": target,
            "input_sha256": sha(encoded([call["input"] for call in calls])),
            "observations": {"calls": calls}, "assertions": assertions,
            "scope_limitations": limitations, "remaining_real_integration": remaining,
            "requires_gpu_for_remaining": True}


def blocked(check_id, component, target, error):
    return {"check_id": check_id, "component": component, "execution_kind": "not_executed_dependency_missing",
            "input_origin": "synthetic", "official": False, "formal_data_origin": "EXAMPLE",
            "command_or_api": component, "target_source": target, "input_sha256": None,
            "observations": {"import_or_setup_exception": {"type": type(error).__name__,
                "module": type(error).__module__, "message": str(error), "traceback": traceback.format_exc()}},
            "assertions": [], "scope_limitations": ["대역/AST 추출/임의 설치로 import를 복구하지 않음; API 실행 미완료"],
            "remaining_real_integration": ["원 CPU 의존성과 source/extension 호환 환경 반환", "실제 renderer/InputProcessor 및 worker 최종 소비"],
            "requires_gpu_for_remaining": True}


def guard_checks(root, out):
    sys.path.insert(0, str(root / "src"))
    import yaml
    from guard.models import CheckDef
    from guard.executors.input_validation import InputValidationExecutor
    from guard.executors.sampling_validation import SamplingValidationExecutor
    from guard.collectors.model_manifest import build_manifest, _load_manifest
    from guard.adapters import vllm_file_read_sensor as sensor
    if not Path(inspect.getsourcefile(InputValidationExecutor)).resolve().is_relative_to(root / "src"):
        raise ValueError("GUARD_IMPORT_ORIGIN_MISMATCH")
    checks = {cid: CheckDef.model_validate(yaml.safe_load((root / "packs/PACK-BASELINE/checks" / (cid + ".yaml")).read_bytes()))
              for cid in ("REQ-08", "REQ-09", "REQ-10")}
    configs = {
        "REQ-08": ({"reused_token_ids": [9, 12, 19], "vocab_size": 64, "vocab_source": "EXAMPLE local temporary config"},
                   {"reused_token_field": "prompt_token_ids", "reused_token_allowed": False, "rejection_codes": ["400", "422"]}),
        "REQ-09": ({"vocab_size": 64, "vocab_source": "EXAMPLE local temporary config", "max_probe_tokens": 24},
                   {"max_input_tokens": 12, "rejection_codes": ["400", "422"],
                    "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]}),
        "REQ-10": ({"constraint_source": "EXAMPLE local validation config", "constraint_engine_version": "EXAMPLE",
                    "max_output_tokens": 3, "max_total_requests": 16, "max_probe_tokens": 64,
                    "max_duration_seconds": 12, "request_timeout": 2},
                   {"allowed_sampling_options": ["temperature", "top_p", "min_tokens"], "rejection_codes": ["400", "422"]}),
    }
    records = []
    for cid, (cfg, policy) in configs.items():
        cls = SamplingValidationExecutor if cid == "REQ-10" else InputValidationExecutor
        def validate(value):
            executor = cls(value["config"])
            executor.validate_policy(checks[cid], value["policy"])
            return {"configuration_and_policy_accepted": True, "executor_version": executor.version,
                    "check_revision": checks[cid].revision, "engine_request_sent": False}
        valid = {"config": cfg, "policy": policy}
        cases = [("normal", valid)]
        invalid = deepcopy(valid)
        if cid == "REQ-08":
            invalid["config"]["reused_token_ids"] = [1.125]
        elif cid == "REQ-09":
            invalid["config"]["vocab_size"] = True
        else:
            invalid["config"]["max_output_tokens"] = True
        cases.append(("invalid_type", invalid))
        invalid = deepcopy(valid)
        if cid == "REQ-08":
            invalid["config"]["reused_token_ids"] = [64]
        elif cid == "REQ-09":
            invalid["policy"]["max_input_tokens"] = 25
        else:
            invalid["config"]["request_timeout"] = 0
        cases.append(("invalid_range", invalid))
        calls = [invoke(name, validate, value) for name, value in cases]
        records.append(record(cid, cls.__module__ + "." + cls.__name__ + ".validate_policy",
            source_ref(cls, cls.version), calls,
            ["실제 guard 설정·정책 검증 함수만 실행; HTTP/Docker/renderer/model 미호출", "임시 vocab·정책 값은 실제 모델 기준 또는 기업 승인이 아님"],
            ["동일 고정 vLLM CPU 설치에서 renderer/InputProcessor/SamplingParams 직접 소비", "선정 실제 endpoint/worker/model 최종 소비와 권한 있는 정책"],
            assertion_names=[("normal_configuration_returned", calls[0]["outcome"] == "returned"),
                             ("standard_type_rejected", calls[1]["outcome"] == "raised"),
                             ("standard_range_rejected", calls[2]["outcome"] == "raised")]))
    snapshot = out / "guard-snapshot"
    snapshot.mkdir()
    config_bytes = encoded({"model_type": "EXAMPLE-fixture", "vocab_size": 64, "architectures": ["EXAMPLE-no-model-code"]})
    (snapshot / "config.json").write_bytes(config_bytes)
    (snapshot / "payload.bin").write_bytes(b"EXAMPLE supply input 20261108\x00\x01\x02")
    def manifest_call(value):
        manifest = build_manifest(snapshot, model_id=value["model_id"])
        accepted = _load_manifest(manifest, None)
        return {"manifest": accepted, "source_files": [file_ref(p) for p in sorted(snapshot.iterdir())]}
    calls = [invoke("actual_file_inventory_roundtrip", manifest_call, {"model_id": "EXAMPLE-local-supply-v1"})]
    produced = calls[0].get("return_value", {}).get("manifest")
    if produced is not None:
        malformed = deepcopy(produced); malformed["inventory_sha256"] = "0" * 64
        calls.append(invoke("manifest_hash_mismatch", lambda value: _load_manifest(value, None), malformed))
    else:
        calls.append(invoke("manifest_shape_rejected", lambda value: _load_manifest(value, None), {"files": []}))
    path = snapshot / "payload.bin"
    calls.append(invoke("actual_fd_dependency_read", lambda value: {
        "returned_bytes": len(raw := sensor.dependency(path, value["expected_sha256"])), "returned_sha256": sha(raw)},
        {"expected_sha256": sha(path.read_bytes())}))
    calls.append(invoke("fd_hash_mismatch_rejected", lambda value: sensor.dependency(path, value["expected_sha256"]).hex(),
                        {"expected_sha256": "0" * 64}))
    records.append(record("SUP-05", "guard.collectors.model_manifest.build_manifest/_load_manifest + vllm_file_read_sensor.dependency",
        source_ref(build_manifest, "guard.model-manifest/1"), calls,
        ["실제 임시 파일 inventory·manifest consumer·FD hash 검증; vLLM 파일 읽기/loader/tensor 적용 관측은 아님", "파일 race·symlink·mount 교체 및 권한 우회 실험 없음"],
        ["실제 eager read 이전 loader/model causal-root 지원; 기존 seq29→후속 seq125 소급 결합 금지",
         "정식 scope/worker/rank·FD·승인 inventory·권한 및 decoder/tensor 최종 소비 원자료"],
        assertion_names=[("real_file_manifest_consumed", calls[0]["outcome"] == "returned"),
                         ("bad_manifest_rejected", calls[1]["outcome"] == "raised"),
                         ("real_fd_bytes_returned", calls[2]["outcome"] == "returned"),
                         ("bad_fd_reference_rejected", calls[3]["outcome"] == "raised")]))
    records[-1]["additional_target_sources"] = [source_ref(sensor.dependency, sensor.PROFILE)]
    return records


def child_checks(args):
    out = args.output_dir.resolve()
    out.mkdir(exist_ok=False)
    for key in ("HF_HOME", "XDG_CACHE_HOME", "VLLM_CACHE_ROOT", "TORCHINDUCTOR_CACHE_DIR"):
        os.environ[key] = str(out / "isolated-cache" / key)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", VLLM_TARGET_DEVICE="cpu",
                      CUDA_VISIBLE_DEVICES="", VLLM_PLUGINS="", VLLM_LOGGING_LEVEL="ERROR")
    def audit(event, arguments):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.fork", "os.posix_spawn"}:
            raise PermissionError("LOCAL_COMPONENT_NETWORK_OR_CHILD_PROCESS_FORBIDDEN:" + event)
    sys.addaudithook(audit)
    records = []
    context = runtime()
    root = args.vllm_source.resolve()
    sys.path.insert(0, str(root))
    # 직접 package import를 시도하며 AST/stub/sys.modules 대역은 사용하지 않는다.
    for cid, module_name, attr, relative in (
        ("REQ-08", "vllm.renderers.base", "BaseRenderer", "vllm/renderers/base.py"),
        ("REQ-09", "vllm.v1.engine.input_processor", "InputProcessor", "vllm/v1/engine/input_processor.py"),
        ("REQ-10", "vllm.sampling_params", "SamplingParams", "vllm/sampling_params.py"),
    ):
        target = file_ref(root / relative, "checkout:" + args.source_commit)
        try:
            cls = getattr(importlib.import_module(module_name), attr)
            if not Path(inspect.getsourcefile(cls)).resolve().is_relative_to(root):
                raise ValueError("VLLM_API_ORIGIN_MISMATCH")
            if cid != "REQ-10":
                # 실제 구성이 없는 클래스를 __new__/toy ModelConfig로 대체하지 않는다.
                records.append({"check_id": cid, "component": module_name + "." + attr,
                    "execution_kind": "api_import_only", "input_origin": "synthetic", "official": False,
                    "formal_data_origin": "EXAMPLE", "command_or_api": "import " + module_name,
                    "target_source": target, "input_sha256": None,
                    "observations": {"api_imported": True, "context_creation_attempted": False},
                    "assertions": [], "scope_limitations": ["import만 수행; context 구성/입력 소비 가능 여부를 판정하지 않음"],
                    "remaining_real_integration": ["기존 실제 build_context를 별도 구성해 직접 renderer/InputProcessor 호출"],
                    "requires_gpu_for_remaining": True})
                continue
            def sampling(value):
                result = cls(**value)
                return {key: getattr(result, key) for key in ("temperature", "top_p", "min_tokens", "max_tokens")}
            normal = {"temperature": 0.375, "top_p": 0.875, "min_tokens": 0, "max_tokens": 3}
            calls = [invoke("sampling_normal", sampling, normal),
                     invoke("sampling_negative_temperature", sampling, {**normal, "temperature": -0.125}),
                     invoke("sampling_zero_top_p", sampling, {**normal, "top_p": 0.0})]
            records.append(record(cid, module_name + "." + attr, source_ref(cls, "checkout:" + args.source_commit), calls,
                ["실제 source checkout의 SamplingParams 직접 구성; engine/sampler 최종 소비 아님"],
                ["정상 설치 renderer/InputProcessor 및 scheduler/SamplingMetadata/Sampler 최종 소비", "구조화 출력/실제 V2 count 경로는 별도 지원/환경 필요"],
                assertion_names=[("sampling_normal_returned", calls[0]["outcome"] == "returned"),
                                 ("sampling_negative_rejected", calls[1]["outcome"] == "raised"),
                                 ("sampling_zero_top_p_rejected", calls[2]["outcome"] == "raised")]))
        except Exception as error:
            records.append(blocked(cid, module_name + "." + attr, target, error))
    # 기존 설치 tokenizer와 safetensors도 실제 dependency API로 별도 구분한다.
    try:
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        import tokenizers
        tokenizer = Tokenizer(WordLevel({"[UNK]": 0, "local": 1, "input": 2, "check": 3}, unk_token="[UNK]"))
        tokenizer.pre_tokenizer = Whitespace()
        def tokenize(value):
            result = tokenizer.encode(value)
            return {"ids": result.ids, "tokens": result.tokens, "offsets": result.offsets}
        calls = [invoke("temporary_text", tokenize, "local input check"),
                 invoke("invalid_text_container", tokenize, {"invalid_text": 1})]
        records.append(record("REQ-08", "tokenizers.Tokenizer.encode", file_ref(tokenizers.__file__, metadata.version("tokenizers")), calls,
            ["실제 설치 tokenizer 의존성 API; vLLM renderer/reused-token 경로 또는 worker 소비 아님"],
            ["실제 vLLM renderer/InputProcessor 입력과 최종 worker 소비 연결"],
            assertion_names=[("local_text_encoded", calls[0]["outcome"] == "returned" and calls[0].get("return_value", {}).get("ids") == [1, 2, 3]),
                             ("invalid_text_type_rejected", calls[1]["outcome"] == "raised")]))
        extension = importlib.import_module("tokenizers.tokenizers")
        records[-1]["additional_target_sources"] = [file_ref(extension.__file__, metadata.version("tokenizers"))]
    except Exception as error:
        records.append(blocked("REQ-08", "tokenizers.Tokenizer.encode", {"path": None, "sha256": None, "version": None}, error))
    try:
        import torch
        import safetensors.torch as safe_torch
        import safetensors
        if torch.version.cuda is not None:
            raise ValueError("CPU_ONLY_TORCH_REQUIRED")
        tensor = torch.tensor([[0.25, 1.25, 2.25], [3.25, 4.25, 5.25]], dtype=torch.float32)
        tensor_path = out / "temporary.safetensors"
        safe_torch.save_file({"temporary_input": tensor}, str(tensor_path))
        bad_path = out / "invalid.safetensors"; bad_path.write_bytes(b"EXAMPLE-invalid-header")
        def load(value):
            result = safe_torch.load_file(value, device="cpu")["temporary_input"]
            return {"shape": list(result.shape), "dtype": str(result.dtype), "values": result.tolist(), "sum": float(result.sum())}
        calls = [invoke("actual_native_tensor_decode", load, str(tensor_path)),
                 invoke("standard_invalid_format", load, str(bad_path))]
        records.append(record("SUP-05", "safetensors.torch.save_file/load_file", source_ref(safe_torch.load_file, metadata.version("safetensors")), calls,
            ["실제 설치 safetensors CPU decoder가 새 임시 tensor를 읽음; vLLM loader/root/승인 공급물 사용 차단은 미확인"],
            ["실제 vLLM loader/model causal-root와 worker·승인 권한·파일 선택부터 최종 tensor 적용 연결"],
            assertion_names=[("native_tensor_values_preserved", calls[0]["outcome"] == "returned" and calls[0].get("return_value", {}).get("values") == tensor.tolist()),
                             ("invalid_format_rejected", calls[1]["outcome"] == "raised")]))
        records[-1]["input_file_refs"] = [file_ref(tensor_path), file_ref(bad_path)]
        records[-1]["additional_target_sources"] = [file_ref(safetensors._safetensors_rust.__file__, metadata.version("safetensors"))]
    except Exception as error:
        records.append(blocked("SUP-05", "safetensors.torch.load_file", {"path": None, "sha256": None, "version": None}, error))
    payload = {"schema_version": 1, "execution_kind": "actual_local_component", "input_origin": "synthetic",
               "official": False, "formal_data_origin": "EXAMPLE", "runtime": context, "records": records,
               "isolation": {"network_and_spawn_audit_denied": True, "engine_monkeypatches": False, "AST_extraction": False,
                             "source_checkout_not_installed_vllm": True, "whole_checkout_gate_not_replaced_or_bypassed": True}}
    (out / "receipt.json").write_bytes(encoded(payload) + b"\n")
    print(json.dumps({"receipt": str(out / "receipt.json"), "records": len(records)}))
    return 0


def linux_path(path):
    path = Path(path).resolve()
    return "/mnt/" + path.drive[0].lower() + "/" + path.as_posix()[3:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--guard-root", type=Path)
    parser.add_argument("--wsl-python")
    parser.add_argument("--vllm-source", type=Path, required=True)
    parser.add_argument("--source-commit", default="db9527a46873454610df6dbedf79a36d6bf1a7f6")
    parser.add_argument("--api-child", action="store_true")
    args = parser.parse_args()
    if args.api_child:
        return child_checks(args)
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    root = args.guard_root.resolve() if args.guard_root else Path(__file__).resolve().parents[1]
    records = guard_checks(root, out)
    child = None
    if args.wsl_python:
        command = [str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/wsl.exe"), "-d", "Ubuntu", "--exec",
                   args.wsl_python, "-I", "-B", linux_path(__file__), "--api-child", "--output-dir", linux_path(out / "wsl-api"),
                   "--vllm-source", linux_path(args.vllm_source), "--source-commit", args.source_commit]
        before = datetime.now(timezone.utc).isoformat()
        proc = subprocess.run(command, capture_output=True)
        (out / "child.stdout").write_bytes(proc.stdout); (out / "child.stderr").write_bytes(proc.stderr)
        child = {"command": command, "started_at": before, "finished_at": datetime.now(timezone.utc).isoformat(),
                 "exit_code": proc.returncode, "stdout": file_ref(out / "child.stdout"), "stderr": file_ref(out / "child.stderr")}
        received = out / "wsl-api/receipt.json"
        if received.is_file():
            data = json.loads(received.read_bytes()); records.extend(data["records"])
            child.update(receipt=file_ref(received), runtime=data["runtime"], isolation=data["isolation"])
    assertions_ok = all(a["passed"] for row in records for a in row["assertions"])
    payload = {"schema_version": 1, "execution_kind": "actual_local_component", "input_origin": "synthetic",
               "official": False, "formal_data_origin": "EXAMPLE", "at": datetime.now(timezone.utc).isoformat(),
               "command": [sys.executable, *sys.orig_argv[1:]], "tool": file_ref(__file__), "runtime": runtime(),
               "records": records, "child_process": child, "executed_assertions_passed": assertions_ok,
               "old_actual_evidence_modified": False, "prototype_files_modified": False,
               "AWS_GPU_Docker_install_download_or_engine_operations": 0,
               "scope": "직접 호출한 로컬 component·의존성만; 실제 배포/worker 소비·전체 ID 판정 아님"}
    destination = out / "receipt.json"; destination.write_bytes(encoded(payload) + b"\n")
    print(json.dumps({"receipt": str(destination), "sha256": sha(destination.read_bytes()),
                      "records": len(records), "assertions_passed": assertions_ok}, ensure_ascii=False))
    return 0 if assertions_ok and (child is None or child["exit_code"] == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
