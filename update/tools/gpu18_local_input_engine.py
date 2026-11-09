"""별도 설치 CPU vLLM에 새 극소형 GPT2·정상 입력을 연결하는 1회 수집기.

설치 환경/기존 0.31 프로파일은 수정하지 않는다. 모든 자산/캐시는 --output-dir 소유다.
GPU·다운로드·외부 네트워크·공유 프로세스 종료 및 비정상 모델 코드 실행은 하지 않는다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gc
import hashlib
import importlib.metadata as metadata
import inspect
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def reference(path):
    path = Path(path).resolve()
    return {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path.read_bytes())}


def process_identity(pid):
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
        fields = text[text.rfind(")") + 2:].split()
        return {"pid": pid, "ppid": int(fields[1]), "state": fields[0], "start_ticks": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def children_of(pid):
    processes = {int(path.name): process_identity(int(path.name)) for path in Path("/proc").iterdir() if path.name.isdigit()}
    selected, frontier = {}, {pid}
    while frontier:
        newer = {key for key, row in processes.items() if row is not None and row["ppid"] in frontier and key not in selected}
        selected.update({key: processes[key] for key in newer})
        frontier = newer
    return list(selected.values())


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    # ZMQ sockaddr_un has a 107-byte path ceiling. Keep only owned caches/IPC
    # here; durable inputs, logs and receipts remain under --output-dir.
    private_runtime = Path(tempfile.mkdtemp(prefix="s5-g18-", dir="/tmp"))
    for key in ("HF_HOME", "XDG_CACHE_HOME", "TMPDIR", "VLLM_CACHE_ROOT", "TORCHINDUCTOR_CACHE_DIR"):
        folder = private_runtime / key
        folder.mkdir(parents=True)
        os.environ[key] = str(folder)
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        CUDA_VISIBLE_DEVICES="", VLLM_TARGET_DEVICE="cpu", VLLM_PLUGINS="",
        VLLM_HOST_IP="127.0.0.1", OMP_NUM_THREADS="2", MKL_NUM_THREADS="2",
        OPENBLAS_NUM_THREADS="2", VLLM_CPU_OMP_THREADS_BIND="nobind", VLLM_CPU_KVCACHE_SPACE="1",
        VLLM_WORKER_MULTIPROC_METHOD="spawn")
    def audit(event, args):
        if event == "socket.connect" and isinstance(args[1], tuple):
            try:
                allowed = ipaddress.ip_address(args[1][0]).is_loopback
            except ValueError:
                allowed = args[1][0] == "localhost"
            if not allowed:
                raise PermissionError("EXTERNAL_NETWORK_FORBIDDEN")
    sys.addaudithook(audit)
    began = time.perf_counter()
    result = {"schema_version": 1, "execution_kind": "actual_local_component", "input_origin": "synthetic",
        "official": False, "formal_data_origin": "EXAMPLE", "profile": "actual_local_installed_cpu_profile",
        "original_vllm031_profile_reused_or_reclassified": False, "records": [], "stages": [],
        "runtime": {"python": reference(sys.executable), "version": sys.version, "process": process_identity(os.getpid()),
                    "uid": os.getuid(), "gid": os.getgid()}, "source_pins": [], "cleanup": {"errors": []},
        "tool": reference(__file__), "owned_runtime_directory": str(private_runtime),
        "GPU_AWS_Docker_download_shared_kill_actions": 0}
    llm = None
    def save():
        (out / "receipt.json").write_bytes(canonical(result) + b"\n")
    def stage(name, **facts):
        row = {"stage": name, "at": datetime.now(timezone.utc).isoformat(),
               "elapsed_seconds": time.perf_counter() - began, **facts}
        result["stages"].append(row)
        save(); print(json.dumps(row, ensure_ascii=False), flush=True)
    try:
        stage("before_import")
        import torch
        import vllm
        from vllm import LLM, SamplingParams
        from vllm.platforms import current_platform
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        from transformers import GPT2Config, GPT2LMHeadModel, TokenizersBackend
        torch.set_num_threads(2)
        result["runtime"].update(packages={name: metadata.version(name) for name in
            ("vllm", "torch", "transformers", "tokenizers", "safetensors", "numpy")},
            vllm_version=vllm.__version__, vllm_origin=vllm.__file__, torch_cuda_version=torch.version.cuda,
            torch_cuda_initialized=torch.cuda.is_initialized(), current_platform=current_platform.__class__.__module__ + "." + current_platform.__class__.__name__,
            current_platform_is_cpu=current_platform.is_cpu(), torch_threads=torch.get_num_threads())
        if not current_platform.is_cpu() or torch.version.cuda is not None or torch.cuda.is_initialized():
            raise ValueError("ACTUAL_CPU_ONLY_RUNTIME_REQUIRED")
        for value in (LLM, LLM.generate, SamplingParams, GPT2Config, GPT2LMHeadModel, TokenizersBackend):
            ref = reference(inspect.getsourcefile(value)); ref["symbol"] = value.__module__ + "." + value.__qualname__
            result["source_pins"].append(ref)
        folder = out / "tiny-gpt2"
        torch.manual_seed(20261108)
        config = GPT2Config(vocab_size=32, n_embd=32, n_layer=1, n_head=1, n_positions=64, n_ctx=64,
                            bos_token_id=1, eos_token_id=2, resid_pdrop=0.0, embd_pdrop=0.0, attn_pdrop=0.0)
        model = GPT2LMHeadModel(config).eval()
        model.save_pretrained(folder, safe_serialization=True)
        vocab = {"[UNK]": 0, "[BOS]": 1, "[EOS]": 2, "local": 3, "input": 4, "check": 5}
        vocab.update({f"word{i}": i for i in range(6, 32)})
        tok = Tokenizer(WordLevel(vocab, unk_token="[UNK]")); tok.pre_tokenizer = Whitespace()
        backend = TokenizersBackend(tokenizer_object=tok, unk_token="[UNK]", bos_token="[BOS]", eos_token="[EOS]")
        backend.save_pretrained(folder)
        del model; gc.collect()
        result["synthetic_assets_before"] = [reference(path) for path in sorted(folder.iterdir()) if path.is_file()]
        stage("tiny_assets_saved", files=len(result["synthetic_assets_before"]))
        kwargs = {"model": str(folder), "tokenizer": str(folder), "dtype": "float32", "enforce_eager": True,
                  "tensor_parallel_size": 1, "max_model_len": 32, "max_num_seqs": 1, "max_num_batched_tokens": 64,
                  "trust_remote_code": False, "seed": 20261108, "disable_log_stats": True,
                  "kv_cache_memory_bytes": 16 * 1024 * 1024}
        result["engine_arguments"] = kwargs
        stage("before_actual_cpu_engine")
        llm = LLM(**kwargs)
        engine = llm.llm_engine
        config = engine.vllm_config
        result["observed_engine_config"] = {"device_type": str(config.device_config.device_type),
            "dtype": str(config.model_config.dtype), "model": config.model_config.model,
            "max_model_len": config.model_config.max_model_len,
            "tensor_parallel_size": config.parallel_config.tensor_parallel_size,
            "max_num_seqs": config.scheduler_config.max_num_seqs}
        result["owned_descendants_after_engine_start"] = children_of(os.getpid())
        stage("actual_cpu_engine_ready", engine_config=result["observed_engine_config"])
        params = {"temperature": 0.0, "top_p": 1.0, "max_tokens": 2, "min_tokens": 1, "ignore_eos": True}
        sampling = SamplingParams(**params)
        for name, prompt in (("normal_text", "local input check"), ("normal_token_array", {"prompt_token_ids": [7, 8, 9]})):
            started = time.perf_counter()
            outputs = llm.generate([prompt], sampling_params=sampling, use_tqdm=False)
            projected = [{"request_id": row.request_id, "finished": row.finished, "prompt_token_ids": row.prompt_token_ids,
                          "outputs": [{"token_ids": list(item.token_ids), "text": item.text,
                                       "finish_reason": item.finish_reason, "stop_reason": item.stop_reason} for item in row.outputs]} for row in outputs]
            assertions = [{"name": "one_request_finished", "passed": len(projected) == 1 and projected[0]["finished"]},
                          {"name": "bounded_output_tokens", "passed": bool(projected) and all(1 <= len(item["token_ids"]) <= 2 for row in projected for item in row["outputs"])}]
            if isinstance(prompt, dict):
                assertions.append({"name": "prompt_ids_preserved_to_engine_output", "passed": bool(projected) and projected[0]["prompt_token_ids"] == prompt["prompt_token_ids"]})
            record = {"check_id": "REQ-08" if isinstance(prompt, dict) else "REQ-09", "related_check_ids": ["REQ-08", "REQ-09", "REQ-10", "SUP-05"],
                "component": "vllm.LLM.generate", "execution_kind": "actual_local_component", "input_origin": "synthetic",
                "official": False, "formal_data_origin": "EXAMPLE", "command_or_api": "LLM.generate([prompt], SamplingParams(**recorded_parameters))",
                "target_source": reference(inspect.getsourcefile(LLM.generate)), "input_sha256": digest(canonical({"prompt": prompt, "sampling": params})),
                "observations": {"case": name, "input": prompt, "sampling_parameters_submitted": params,
                                 "actual_outputs": projected, "elapsed_seconds": time.perf_counter() - started}, "assertions": assertions,
                "scope_limitations": ["별도 설치 0.29 CPU 프로파일의 실제 정상 추론; 기존 0.31 프로파일/기업 전체 질문 판정 아님",
                                      "worker 내부 sampling 값·파일 FD causal-root·승인 권한은 별도 미관측"],
                "remaining_real_integration": ["원 0.31 고정 환경/지원 계약과 실제 운영 worker/권한·정책의 해당 분기", "SUP05 pre-read loader/model root와 승인 inventory 결합"],
                "requires_gpu_for_remaining": True}
            result["records"].append(record)
            stage(name + "_completed", actual_outputs=projected)
        # 정상 프로세스에서 표준 파라미터/형식 거부만 직접 확인한다.
        rejects = []
        for name, function in (("negative_temperature", lambda: SamplingParams(temperature=-0.125, max_tokens=2)),
                               ("oov_prompt_token", lambda: llm.generate([42], sampling_params=sampling, use_tqdm=False))):
            try:
                value = function(); rejects.append({"name": name, "outcome": "returned", "type": type(value).__name__})
            except Exception as error:
                rejects.append({"name": name, "outcome": "raised", "exception_type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()})
        result["standard_rejections"] = rejects
        result["synthetic_assets_after"] = [reference(path) for path in sorted(folder.iterdir()) if path.is_file()]
        result["assets_unchanged"] = result["synthetic_assets_after"] == result["synthetic_assets_before"]
        result["assertions_passed"] = all(item["passed"] for row in result["records"] for item in row["assertions"]) and all(row["outcome"] == "raised" for row in rejects)
    except Exception as error:
        result["primary_exception"] = {"type": type(error).__name__, "message": str(error), "traceback": traceback.format_exc()}
        stage("primary_exception_recorded", error_type=type(error).__name__, message=str(error))
    finally:
        result["owned_descendants_before_shutdown"] = children_of(os.getpid())
        if llm is not None:
            try:
                llm.llm_engine.engine_core.shutdown()
                result["cleanup"]["engine_core_shutdown_returned"] = True
            except Exception as error:
                result["cleanup"]["errors"].append({"phase": "engine_core_shutdown", "type": type(error).__name__, "message": str(error)})
            renderer = getattr(llm.llm_engine, "renderer", None)
            if renderer is not None and hasattr(renderer, "shutdown"):
                try:
                    renderer.shutdown(); result["cleanup"]["renderer_shutdown_returned"] = True
                except Exception as error:
                    result["cleanup"]["errors"].append({"phase": "renderer_shutdown", "type": type(error).__name__, "message": str(error)})
        llm = None; gc.collect()
        result["owned_descendants_after_shutdown"] = children_of(os.getpid())
        try:
            if (private_runtime.parent != Path("/tmp") or not private_runtime.name.startswith("s5-g18-")
                    or private_runtime.is_symlink() or private_runtime.stat().st_uid != os.getuid()):
                raise ValueError("PRIVATE_RUNTIME_CLEANUP_IDENTITY_MISMATCH")
            shutil.rmtree(private_runtime)
            result["cleanup"]["owned_runtime_directory_removed"] = not private_runtime.exists()
        except Exception as error:
            result["cleanup"]["errors"].append({"phase": "owned_runtime_directory", "type": type(error).__name__, "message": str(error)})
        result["runtime"]["torch_cuda_initialized_after"] = "torch" in sys.modules and sys.modules["torch"].cuda.is_initialized()
        result["elapsed_seconds"] = time.perf_counter() - began
        stage("final_receipt_saved", primary_exception=result.get("primary_exception", {}).get("type"))
    return 0 if result.get("assertions_passed") and not result["cleanup"]["errors"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output_dir.resolve()))
