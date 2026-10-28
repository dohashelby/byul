"""Run only the pinned real request class in an already prepared CPU environment.

No installation, engine, network request or model download is performed here.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import time

from guard.executors.structured_tools import SOURCE_COMMIT, validate_request_class


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--model', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.source_root.resolve()
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
    relative = 'vllm/entrypoints/openai/chat_completion/protocol.py'
    changes = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', 'vllm'], text=True)
    if head != SOURCE_COMMIT or changes:
        raise SystemExit('Requires clean pinned vLLM source; no execution performed')
    if args.output.exists():
        raise SystemExit('Output exists; append-only evidence requires a fresh path')
    start = time.perf_counter()
    try:
        protocol = importlib.import_module('vllm.entrypoints.openai.chat_completion.protocol')
        exceptions = importlib.import_module('vllm.exceptions')
        actual = Path(protocol.__file__).resolve()
        if actual != root / relative:
            raise SystemExit('Imported protocol is not the pinned source')
        outcomes = validate_request_class(protocol.ChatCompletionRequest, exceptions.VLLMValidationError, model=args.model)
        status = 'completed'
    except Exception:
        outcomes, status = {}, 'import_or_execution_error'
    record = {'source_commit': head, 'source_file_bytes_sha256': hashlib.sha256((root / relative).read_bytes()).hexdigest(),
              'scope': 'request_class_only', 'status': status, 'outcomes': outcomes,
              'parent_wall_seconds': time.perf_counter() - start,
              'runner_consumption_verified': False, 'backend': None}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
    return 0 if status == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
