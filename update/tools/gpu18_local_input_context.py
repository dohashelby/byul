"""기존 b1_input_cpu.build_context에 새 입력을 연결하는 실제 CPU API 수집.

원래 ModelConfig/renderer/InputProcessor와 의존성을 사용한다. AST/stub/함수 대체 없음.
설치·다운로드·엔진 기동 없이 소스 checkout component만 관측한다.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
import traceback


def run(args):
    out = args.output_dir.resolve(); out.mkdir(parents=True, exist_ok=False)
    for key in ("HF_HOME", "XDG_CACHE_HOME", "TMPDIR", "VLLM_CACHE_ROOT"):
        path = out / "private-cache" / key; path.mkdir(parents=True); os.environ[key] = str(path)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", VLLM_TARGET_DEVICE="cpu",
                      CUDA_VISIBLE_DEVICES="", VLLM_PLUGINS="", PYTHONDONTWRITEBYTECODE="1",
                      OMP_NUM_THREADS="2", MKL_NUM_THREADS="2", PYTHONPATH=str(args.vllm_source.resolve()))
    sys.path[:0] = [str(args.guard_root.resolve()), str(args.guard_root.resolve() / "src"), str(args.vllm_source.resolve())]
    result = {"schema_version": 1, "execution_kind": "actual_local_component", "input_origin": "synthetic",
              "official": False, "formal_data_origin": "EXAMPLE", "profile": "actual_source_checkout_cpu_component",
              "records": [], "subprocess_launches": [], "cleanup_errors": [], "stages": [],
              "runtime": {"python": sys.executable, "version": sys.version, "pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()},
              "source_checkout": str(args.vllm_source.resolve()), "source_commit_expected": "db9527a46873454610df6dbedf79a36d6bf1a7f6",
              "existing_gated_profile_not_claimed": True, "source_gate_modification": False,
              "network_download_GPU_engine_AST_stub_function_patch": False}
    def sha(raw): return hashlib.sha256(raw).hexdigest()
    def file_ref(path):
        path = Path(path).resolve(); return {"path": str(path), "sha256": sha(path.read_bytes()), "version": None}
    def save(): (out / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    def stage(name):
        result["stages"].append({"name": name, "at": datetime.now(timezone.utc).isoformat()}); save(); print(name, flush=True)
    def audit(event, values):
        if event in {"socket.connect", "socket.getaddrinfo"}: raise PermissionError("NETWORK_NOT_ALLOWED")
        if event == "subprocess.Popen":
            argv = values[1]
            result["subprocess_launches"].append({"executable": values[0], "argv": argv})
            if not isinstance(argv, (list, tuple)) or "vllm.model_executor.models.registry" not in argv:
                raise PermissionError("ONLY_ORIGINAL_VLLM_REGISTRY_CHILD_ALLOWED")
    sys.addaudithook(audit)
    processor = None
    try:
        stage("before_real_context_import")
        import torch
        from tools.b1_input_cpu import build_context
        from vllm.sampling_params import SamplingParams
        from vllm.inputs import embeds_input
        if torch.version.cuda is not None: raise ValueError("CPU_ONLY_TORCH_REQUIRED")
        torch.set_num_threads(2)
        result["helper_source"] = file_ref(inspect.getsourcefile(build_context))
        result["tool_source"] = file_ref(__file__)
        stage("before_original_build_context")
        processor = build_context(out / "local-config")
        result["effective_config"] = {"vocab_size": processor.model_config.get_vocab_size(),
            "max_model_len": processor.model_config.max_model_len, "hidden_size": processor.model_config.get_hidden_size()}
        result["config_files"] = [file_ref(p) for p in sorted((out / "local-config").iterdir()) if p.is_file()]
        tracked = {processor.renderer.render_cmpl.__func__.__code__: "renderer.render_cmpl",
                   processor.process_inputs.__func__.__code__: "InputProcessor.process_inputs"}
        for function in (processor.renderer.render_cmpl, processor.process_inputs):
            path = Path(inspect.getsourcefile(function)).resolve()
            if not path.is_relative_to(args.vllm_source.resolve()): raise ValueError("VLLM_CONTEXT_ORIGIN_MISMATCH")
        stage("actual_context_ready")
        cases = [("REQ-08", "new_text", {"prompt": "word11 word12"}),
                 ("REQ-08", "new_reused_tokens", {"prompt_token_ids": [8, 9, 10]}),
                 ("REQ-09", "negative_token", {"prompt_token_ids": [-2]}),
                 ("REQ-09", "over_model_length", {"prompt_token_ids": [8] * 17}),
                 ("REQ-09", "embedding_mask_valid", {"embedding_shape": [2, 4], "mask_length": 2}),
                 ("REQ-09", "embedding_mask_short", {"embedding_shape": [2, 4], "mask_length": 1})]
        for cid, name, value in cases:
            events = []
            def profile(frame, event, arg):
                point = tracked.get(frame.f_code)
                if point and event in {"call", "return"}: events.append({"point": point, "event": event})
            original_profile = sys.getprofile(); started = time.perf_counter()
            observations = {"input": value, "events": events}
            try:
                sys.setprofile(profile)
                if "embedding_shape" in value:
                    prompt = embeds_input(torch.zeros(value["embedding_shape"]), prompt_token_ids=[8, 9],
                                          is_token_ids=[False] * value["mask_length"])
                else:
                    prompt = processor.renderer.render_cmpl([value])[0]
                request = processor.process_inputs("s5-local-" + name, prompt, SamplingParams(max_tokens=2), ("generate",))
                observations.update(outcome="returned", admitted_token_ids=request.prompt_token_ids,
                    admitted_embedding_shape=None if request.prompt_embeds is None else list(request.prompt_embeds.shape),
                    admitted_mask=None if request.prompt_is_token_ids is None else request.prompt_is_token_ids)
            except Exception as error:
                observations.update(outcome="raised", exception={"type": type(error).__name__, "module": type(error).__module__,
                    "message": str(error), "traceback": traceback.format_exc()})
            finally:
                sys.setprofile(original_profile)
            observations["elapsed_seconds"] = time.perf_counter() - started
            expected_normal = name in {"new_text", "new_reused_tokens", "embedding_mask_valid"}
            assertions = [{"name": "standard_normal_or_rejection_observed",
                           "passed": observations["outcome"] == ("returned" if expected_normal else "raised")}]
            result["records"].append({"check_id": cid, "component": "original build_context -> renderer -> InputProcessor",
                "execution_kind": "actual_local_component", "command_or_api": "processor.process_inputs(new_synthetic_input)",
                "target_source": file_ref(inspect.getsourcefile(processor.process_inputs)),
                "additional_target_sources": [file_ref(inspect.getsourcefile(processor.renderer.render_cmpl))],
                "input_sha256": sha(json.dumps(value, sort_keys=True).encode()), "observations": observations, "assertions": assertions,
                "scope_limitations": ["실제 원 소스 checkout의 renderer/admission component; engine/worker 최종 추론 아님", "synthetic 설정·입력, 기업 정책/전체 질문 PASS 아님"],
                "remaining_real_integration": ["원 0.31 정상 설치 worker/최종 소비", "모든 지원 입력·endpoint/권한 있는 정책 및 전체 분기"],
                "requires_gpu_for_remaining": True})
            stage(name + "_collected")
    except Exception as error:
        result["setup_exception"] = {"type": type(error).__name__, "module": type(error).__module__,
                                     "message": str(error), "traceback": traceback.format_exc()}
        result["setup_execution_complete"] = False
        stage("actual_setup_failed")
    finally:
        if processor is not None:
            try: processor.renderer.shutdown(); result["renderer_shutdown_returned"] = True
            except Exception as error: result["cleanup_errors"].append({"type": type(error).__name__, "message": str(error)})
        result["component_assertions_passed"] = bool(result["records"]) and all(a["passed"] for r in result["records"] for a in r["assertions"])
        stage("final_receipt_saved")
    return 0 if result["component_assertions_passed"] and not result["cleanup_errors"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--guard-root", type=Path, required=True)
    parser.add_argument("--vllm-source", type=Path, required=True)
    raise SystemExit(run(parser.parse_args()))
