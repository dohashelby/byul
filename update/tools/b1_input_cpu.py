"""Execute the pinned real renderer/InputProcessor with a tiny local config.

No weights, model download, engine process, HTTP server or device execution.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from guard.executors.input_cpu import PROFILE, run_reference, project
from guard.executors.structured_tools import SOURCE_COMMIT


def build_context(folder):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace
    from transformers import GPT2Config, TokenizersBackend
    from vllm.config import ModelConfig, VllmConfig, DeviceConfig
    from vllm.v1.engine.input_processor import InputProcessor
    folder.mkdir()
    GPT2Config(vocab_size=32, n_embd=4, n_layer=1, n_head=1, n_positions=16,
               bos_token_id=1, eos_token_id=2, architectures=['GPT2LMHeadModel']).save_pretrained(folder)
    vocab = {'[UNK]': 0, '[BOS]': 1, '[EOS]': 2, 'hello': 3, 'world': 4, 'user': 5, 'assistant': 6}
    vocab.update({f'word{i}': i for i in range(7, 32)})
    token = Tokenizer(WordLevel(vocab, unk_token='[UNK]'))
    token.pre_tokenizer = Whitespace()
    tokenizer = TokenizersBackend(tokenizer_object=token, unk_token='[UNK]', bos_token='[BOS]', eos_token='[EOS]')
    tokenizer.chat_template = "{% for m in messages %}{{ m['role'] }} {{ m['content'] }} {% endfor %}"
    tokenizer.save_pretrained(folder)
    model_config = ModelConfig(model=str(folder), tokenizer=str(folder), dtype='float32',
                               max_model_len=16, enforce_eager=True, enable_prompt_embeds=True)
    config = VllmConfig(model_config=model_config, device_config=DeviceConfig(device='cpu'))
    return InputProcessor(config)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--suite', choices=('input', 'sampling', 'embeddings', 'online'), default='input')
    args = parser.parse_args()
    root = args.source_root.resolve()
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != SOURCE_COMMIT:
        raise SystemExit('Reference SHA mismatch')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', 'vllm'], text=True):
        raise SystemExit('Reference is modified')
    args.workdir.mkdir(parents=True, exist_ok=False)
    start = time.perf_counter()
    if args.suite == 'sampling':
        for key in ('VLLM_MAX_STOP_STRINGS', 'VLLM_MAX_N_SEQUENCES', 'VLLM_MAX_NUM_BAD_WORDS'):
            os.environ[key] = '4'
    record = {'profile': PROFILE, 'source_commit': SOURCE_COMMIT, 'scope': 'reference_cpu_admission_only',
              'installed_target': None, 'runner_consumption_verified': False, 'suite': args.suite}
    processor = None
    try:
        import torch
        from vllm.renderers import ChatParams
        from vllm.renderers.base import BaseRenderer
        from vllm.sampling_params import SamplingParams
        from vllm.inputs import embeds_input
        from vllm.exceptions import VLLMValidationError
        from vllm.v1.engine.input_processor import InputProcessor
        if torch.version.cuda is not None:
            raise RuntimeError('CPU-only torch required')
        source_file = Path(sys.modules[InputProcessor.__module__].__file__).resolve()
        if not source_file.is_relative_to(root / 'vllm'):
            raise RuntimeError('Unexpected import origin')
        from guard.executors import input_cpu
        own_src = Path(__file__).resolve().parents[1] / 'src'
        if not Path(input_cpu.__file__).resolve().is_relative_to(own_src):
            raise RuntimeError('B-1 module import origin mismatch')
        record['b1_module_file_bytes_sha256'] = hashlib.sha256(Path(input_cpu.__file__).read_bytes()).hexdigest()
        processor = build_context(args.workdir / 'local-config')
        tracked = [('render_cmpl', BaseRenderer.render_cmpl), ('render_chat', BaseRenderer.render_chat),
                   ('validate_token_ids', BaseRenderer.validate_token_ids), ('process_inputs', InputProcessor.process_inputs),
                   ('validate_prompt_len', InputProcessor._validate_prompt_len), ('validate_model_input', InputProcessor._validate_model_input)]
        if args.suite == 'input':
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
            from vllm.entrypoints.openai.completion.protocol import CompletionRequest
            rows = run_reference(processor, SamplingParams, ChatParams, embeds_input, torch.zeros, VLLMValidationError, tracked,
                                 {'chat': ChatCompletionRequest, 'completion': CompletionRequest})
            observations = project(rows)
        elif args.suite == 'sampling':
            from pydantic import ValidationError
            from guard.executors import sampling_cpu
            from vllm import sampling_params
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest, BatchChatCompletionRequest
            from vllm.entrypoints.openai.completion.protocol import CompletionRequest
            from vllm.entrypoints.openai.responses.protocol import ResponsesRequest
            from vllm import envs
            if (envs.VLLM_MAX_STOP_STRINGS, envs.VLLM_MAX_N_SEQUENCES, envs.VLLM_MAX_NUM_BAD_WORDS) != (4, 4, 4):
                raise RuntimeError('Sampling fixture configuration mismatch')
            classes = {'completion': CompletionRequest, 'chat': ChatCompletionRequest,
                       'batch': BatchChatCompletionRequest, 'responses': ResponsesRequest}
            tracked = [('sequence_cap', sampling_params._verify_num_sequences),
                       ('bad_word_tokenization', SamplingParams.update_from_tokenizer),
                       ('beam_conversion', ChatCompletionRequest.to_beam_search_params)]
            rows = sampling_cpu.run_reference(classes, SamplingParams, sampling_params.BeamSearchParams,
                processor.renderer.tokenizer, (VLLMValidationError, ValidationError), tracked)
            observations = {'REQ-10': sampling_cpu.project(rows)}
            record['sampling_fixture_caps'] = {'origin': 'DECLARED', 'n': 4, 'stop_strings': 4, 'tokenized_bad_words': 4}
        elif args.suite == 'embeddings':
            import base64
            import io
            from guard.executors import embedding_cpu
            from vllm.renderers.embed_utils import safe_load_prompt_embeds
            from vllm.config import ModelConfig
            folder = args.workdir / 'local-config'
            disabled = ModelConfig(model=str(folder), tokenizer=str(folder), dtype='float32',
                                   max_model_len=16, enforce_eager=True, enable_prompt_embeds=False)
            def make_payload(shape, dtype):
                buffer = io.BytesIO()
                torch.save(torch.zeros(shape, dtype=getattr(torch, dtype)), buffer)
                return base64.b64encode(buffer.getvalue())
            tracked = [('embedding_loader', safe_load_prompt_embeds)]
            rows = embedding_cpu.run_reference(safe_load_prompt_embeds, processor.model_config, disabled,
                                              make_payload, VLLMValidationError, tracked)
            observations = {'REQ-09': embedding_cpu.project(rows)}
        else:
            from guard.executors import online_input_cpu
            from vllm.renderers.online_renderer import OnlineRenderer, _reused_prompt_token_ids
            from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
            from vllm.entrypoints.serve.engine.protocol import ErrorResponse
            from vllm.entrypoints.generate.base.protocol import validate_request_mm_kwargs
            tracked += [('online_render_chat', OnlineRenderer.render_chat),
                        ('online_preprocess_chat', OnlineRenderer.preprocess_chat),
                        ('reuse_input_gate', _reused_prompt_token_ids),
                        ('template_gate', OnlineRenderer.validate_chat_template),
                        ('mm_kwargs_gate', validate_request_mm_kwargs)]
            rows = online_input_cpu.run_reference(processor, OnlineRenderer, ChatCompletionRequest,
                                                 ErrorResponse, VLLMValidationError, tracked)
            observations = online_input_cpu.project(rows)
        record['observed_source_file_bytes_sha256'] = {
            str(Path(sys.modules[fn.__module__].__file__).resolve().relative_to(root)): hashlib.sha256(Path(sys.modules[fn.__module__].__file__).read_bytes()).hexdigest()
            for _, fn in tracked if Path(sys.modules[fn.__module__].__file__).resolve().is_relative_to(root)}
        case_errors = any(r.get('outcome') == 'execution_error' or r.get('after', {}).get('outcome') == 'execution_error' for r in rows)
        record.update(status='completed_with_case_errors' if case_errors else 'completed', results=rows, observations_by_check=observations,
                      fixture_config={'origin': 'DECLARED', 'vocab_size': 32, 'max_model_len': 16, 'hidden_size': 4},
                      source_file_bytes_sha256=hashlib.sha256(source_file.read_bytes()).hexdigest(),
                      dependencies={k: importlib.metadata.version(k) for k in ('torch', 'transformers', 'tokenizers', 'pydantic')})
    except Exception as error:
        record.update(status='execution_error', error_type=type(error).__name__, missing_module=getattr(error, 'name', None),
                      frames=[{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno} for f in traceback.extract_tb(error.__traceback__)[-8:]])
    finally:
        if processor is not None:
            processor.renderer.shutdown()
    record['parent_wall_seconds'] = time.perf_counter() - start
    record['config_file_bytes_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.workdir / 'local-config').glob('*') if p.is_file()}
    (args.workdir / 'result.json').write_text(json.dumps(record, indent=2))
    print(record['status'])
    return 0 if record['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
