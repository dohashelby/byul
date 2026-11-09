"""원 0.31 validate_request_mm_kwargs 직접 호출: SUP-08 사전 정책 검사만.

멀티모달/media I/O·engine·plugin·Hub·code_revision·최종 모듈 소비는 실행하지 않는다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import inspect
import json
import os
from pathlib import Path
import sys
import time
import traceback

SOURCE_COMMIT = "db9527a46873454610df6dbedf79a36d6bf1a7f6"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    root = args.source_root.resolve()
    target = root / "vllm/entrypoints/generate/base/protocol.py"
    if digest(target.read_bytes()) != args.source_sha256:
        raise ValueError("MM_KWARGS_SOURCE_PIN_MISMATCH")
    for key in ("HF_HOME", "XDG_CACHE_HOME", "TMPDIR", "VLLM_CACHE_ROOT"):
        path = out / "private-cache" / key; path.mkdir(parents=True); os.environ[key] = str(path)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", CUDA_VISIBLE_DEVICES="",
                      VLLM_TARGET_DEVICE="cpu", VLLM_PLUGINS="", PYTHONDONTWRITEBYTECODE="1",
                      OMP_NUM_THREADS="2", MKL_NUM_THREADS="2")
    def audit(event, values):
        if event in {"socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.fork", "os.posix_spawn"}:
            raise PermissionError("MM_KWARGS_COMPONENT_NO_NETWORK_OR_CHILD:" + event)
    sys.addaudithook(audit)
    stat = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
    receipt = {"schema_version": 1, "execution_kind": "actual_local_component", "input_origin": "synthetic",
               "official": False, "formal_data_origin": "EXAMPLE", "check_id": "SUP-08", "records": [],
               "command": [sys.executable, *sys.orig_argv[1:]],
               "runtime": {"python": sys.executable, "version": sys.version, "uid": os.getuid(), "gid": os.getgid(),
                           "pid": os.getpid(), "start_ticks": int(stat[19]),
                           "flags": {k: getattr(sys.flags, k) for k in ("isolated", "no_site", "dont_write_bytecode")}},
               "tool": {"path": str(Path(__file__).resolve()), "sha256": digest(Path(__file__).read_bytes())},
               "source_commit": SOURCE_COMMIT, "original_gates_or_functions_modified": False}
    def save(): (out / "receipt.json").write_bytes(canonical(receipt) + b"\n")
    save(); print("before_original_api_import", flush=True)
    try:
        sys.path.insert(0, str(root))
        from vllm.entrypoints.generate.base.protocol import validate_request_mm_kwargs
        from vllm.exceptions import VLLMValidationError
        if Path(inspect.getsourcefile(validate_request_mm_kwargs)).resolve() != target:
            raise ValueError("MM_KWARGS_FUNCTION_ORIGIN_MISMATCH")
        source = {"path": str(target), "sha256": digest(target.read_bytes()), "version": "checkout:" + SOURCE_COMMIT}
        receipt["runtime"]["packages"] = {key: metadata.version(key) for key in ("torch", "transformers", "tokenizers", "pydantic")}
        receipt["exception_source"] = {"path": inspect.getsourcefile(VLLMValidationError),
            "sha256": digest(Path(inspect.getsourcefile(VLLMValidationError)).read_bytes())}
        cases = [("none_untrusted", None, None, False, None),
                 ("empty_untrusted", {}, {}, False, None),
                 ("processor_untrusted", {"example_scale": 1}, None, False, "mm_processor_kwargs"),
                 ("media_untrusted", None, {"image": {"example": 1}}, False, "media_io_kwargs"),
                 ("processor_trusted", {"example_scale": 1}, None, True, None),
                 ("media_trusted", None, {"image": {"example": 1}}, True, None)]
        for name, processor, media, trusted, rejected_parameter in cases:
            value = {"mm_processor_kwargs": processor, "media_io_kwargs": media, "trust_request_mm_kwargs": trusted}
            before = canonical(value); began = time.perf_counter(); observation = {"case": name, "input": value}
            caught = None
            try:
                returned = validate_request_mm_kwargs(**value)
                observation.update(outcome="returned", return_value=returned)
            except Exception as error:
                caught = error
                observation.update(outcome="raised", exception={"type": type(error).__name__, "module": type(error).__module__,
                    "parameter": getattr(error, "parameter", None), "message": str(error), "traceback": traceback.format_exc()})
            observation["elapsed_seconds"] = time.perf_counter() - began
            correct = ((caught is None and observation.get("return_value") is None) if rejected_parameter is None
                       else isinstance(caught, VLLMValidationError) and getattr(caught, "parameter", None) == rejected_parameter)
            receipt["records"].append({"check_id": "SUP-08", "component": "vllm.entrypoints.generate.base.protocol.validate_request_mm_kwargs",
                "execution_kind": "actual_local_component", "input_origin": "synthetic", "official": False,
                "formal_data_origin": "EXAMPLE", "command_or_api": "validate_request_mm_kwargs(**recorded_input)",
                "target_source": source, "input_sha256": digest(before), "observations": observation,
                "assertions": [{"name": "actual_return_or_original_typed_parameter_rejection", "passed": correct},
                               {"name": "input_not_mutated", "passed": canonical(value) == before}],
                "scope_limitations": ["원 0.31 사전 신뢰 플래그 검사만 직접 실행; request endpoint/멀티모달 처리·media I/O 미실행",
                                      "trust=True 수락은 실제 권한·기업 승인·안전한 최종 소비를 증명하지 않음"],
                "remaining_real_integration": ["실제 Hub/code_revision 선택·해당 worker/import/module 최종 소비 및 권한 있는 정책",
                                               "기존 SUP-08 원자료/Ref 복구 및 전체 질문 판정은 별도"],
                "requires_gpu_for_remaining": True})
            save(); print(name + ":" + observation["outcome"], flush=True)
        receipt["source_unchanged_after"] = digest(target.read_bytes()) == source["sha256"] == args.source_sha256
        receipt["assertions_passed"] = receipt["source_unchanged_after"] and all(a["passed"] for r in receipt["records"] for a in r["assertions"])
    except Exception as error:
        receipt["setup_exception"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
    receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
    receipt["GPU_AWS_Docker_network_engine_model_or_media_IO_actions"] = 0
    save(); print("final_receipt_saved", flush=True)
    return 0 if receipt.get("assertions_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
