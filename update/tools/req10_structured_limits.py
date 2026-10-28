"""Execute actual reference request/backend code in the prepared CPU environment."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys
import time

from guard.executors.structured_limits import (SOURCE_COMMIT, PROFILE, DEPTH_LIMIT,
    TAG_WRAPPER_ALLOWANCE, run_reference, project)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    if subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() != SOURCE_COMMIT:
        raise SystemExit('Reference SHA mismatch')
    if subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', 'vllm'], text=True):
        raise SystemExit('Reference source is modified')
    if args.output.exists():
        raise SystemExit('Output exists; choose a fresh evidence path')
    started = time.perf_counter()
    record = {'profile': PROFILE, 'source_commit': SOURCE_COMMIT, 'scope': 'reference_cpu_request_and_xgrammar_validation',
              'backend': 'xgrammar', 'installed_target': None, 'runner_consumption_verified': False}
    try:
        import torch
        import xgrammar
        from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
        from vllm import sampling_params as sampling
        from vllm.exceptions import VLLMValidationError
        from vllm.v1.structured_output.backend_xgrammar import validate_xgrammar_grammar
        modules = [sys.modules[ChatCompletionRequest.__module__], sampling, sys.modules[validate_xgrammar_grammar.__module__]]
        if any(not Path(m.__file__).resolve().is_relative_to(root / 'vllm') for m in modules):
            raise RuntimeError('Unexpected reference import origin')
        if torch.version.cuda is not None or sampling.MAX_STRUCTURED_OUTPUT_JSON_NESTING != DEPTH_LIMIT or sampling.STRUCTURAL_TAG_WRAPPER_NESTING != TAG_WRAPPER_ALLOWANCE:
            raise RuntimeError('Reference CPU/depth contract mismatch')
        tracked = [('nesting_check', sampling.check_json_nesting), ('backend_validator', validate_xgrammar_grammar),
                   ('json_conversion', xgrammar.Grammar.from_json_schema), ('tag_conversion', xgrammar.Grammar.from_structural_tag)]
        rows = run_reference(ChatCompletionRequest, sampling.SamplingParams, sampling.StructuredOutputsParams,
                             validate_xgrammar_grammar, VLLMValidationError, tracked)
        record.update(status='completed', results=rows, observations=project(rows),
                      source_file_bytes_sha256={str(Path(m.__file__).resolve().relative_to(root)): hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules},
                      dependencies={k: importlib.metadata.version(k) for k in ('torch', 'pydantic', 'xgrammar')})
    except Exception as error:
        record.update(status='execution_error', error_type=type(error).__name__)
        if isinstance(error, ModuleNotFoundError):
            record['missing_module'] = error.name
    record['parent_wall_seconds'] = time.perf_counter() - started
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(record, stream, indent=2)
    print(record['status'])
    return 0 if record['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
