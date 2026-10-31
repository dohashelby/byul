"""Execute the existing 15 structured-limit inputs in an explicitly pinned CPU source."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import sys
import time

from guard.executors.structured_limits import (SOURCE_COMMIT, PROFILE, DEPTH_LIMIT,
    TAG_WRAPPER_ALLOWANCE, fixtures, describe, run_reference, project)
from guard.executors.structured_tools_cpu import (TARGET031_COMMIT, _verify_source,
    _validate_target031_dependencies)
from tools.b1_integration_cpu import verify_tool_checkout

TARGET031_PROFILE = 'structured-limits-vllm-031-cpu-v1'


def target031_spec(case):
    """Source-specific expectation for the unchanged input; no inferred depth policy."""
    spec = describe(case)
    spec.update(source_commit=TARGET031_COMMIT, expected=None,
                expectation_status='source_specific_expectation_unavailable')
    if case['id'] == 'unsupported-unique-items':
        spec.update(expected='rejected', rule='target031_xgrammar_unsupported_uniqueItems',
            expectation_status='explicit_source_rule',
            rule_source={'path': 'vllm/v1/structured_output/backend_xgrammar.py',
                         'symbol': 'has_xgrammar_unsupported_json_features', 'keyword': 'uniqueItems'})
    elif case['rule'] in {'reference_depth', 'reference_tag_depth'}:
        spec['rule'] = 'target031_depth_observation'
    return spec


def target031_rows(rows):
    specs = {case['id']: target031_spec(case) for case in fixtures()}
    seen, result = set(), []
    for row in rows:
        key = row.get('case_id')
        if key not in specs or key in seen or row.get('input_canonical_sha256') != specs[key]['input_canonical_sha256']:
            raise ValueError('STRUCTURED_LIMITS_TARGET031_FIXTURE_MISMATCH')
        seen.add(key)
        result.append({**row, **specs[key]})
    return result


def project_target031(rows):
    # Re-derive source expectations rather than trusting the old reference fields.
    selected = target031_rows(rows)
    violations = [row['case_id'] for row in selected if row['expected'] == 'rejected' and row['outcome'] == 'accepted']
    return {'structured_limits_confirmed_violations': {'value': violations, 'collected': bool(selected)},
            'structured_limits_cases_complete': {'value': len(selected) == len(fixtures()), 'collected': True}}


def capture(root, *, source_profile='reference'):
    if sys.flags.optimize:
        raise ValueError('STRUCTURED_LIMITS_CPU_REQUIRES_ASSERTIONS')
    if source_profile not in {'reference', 'vllm-031'}:
        raise ValueError('STRUCTURED_LIMITS_SOURCE_PROFILE_INVALID')
    verify_tool_checkout(Path(__file__).resolve().parents[2])
    target = source_profile == 'vllm-031'
    commit = TARGET031_COMMIT if target else SOURCE_COMMIT
    profile = TARGET031_PROFILE if target else PROFILE
    root = Path(root).resolve()
    if sys.version_info[:2] != (3, 12):
        raise ValueError('STRUCTURED_LIMITS_PYTHON_MISMATCH')
    _verify_source(root, expected_commit=commit)
    if target:
        _validate_target031_dependencies()
    started = time.perf_counter()
    record = {'profile': profile, 'source_commit': commit,
        'scope': 'target031_source_cpu_request_and_xgrammar_validation' if target else 'reference_cpu_request_and_xgrammar_validation',
        'backend': 'xgrammar', 'installed_target': None, 'server_preprocessing_verified': False,
        'runner_consumption_verified': False, 'enterprise_size_depth_complexity_policy': 'UNSET',
        'corpus': 'existing_structured_limits_15', 'new_input_case_count': 0}
    try:
        import torch
        import xgrammar
        from vllm.entrypoints.openai.chat_completion.protocol import ChatCompletionRequest
        from vllm import sampling_params as sampling
        from vllm.exceptions import VLLMValidationError
        from vllm.v1.structured_output.backend_xgrammar import validate_xgrammar_grammar
        from vllm.entrypoints.generate.base import protocol as tag_protocol
        modules = [sys.modules[ChatCompletionRequest.__module__], sampling,
            sys.modules[validate_xgrammar_grammar.__module__], tag_protocol]
        functions = [ChatCompletionRequest.check_structured_outputs_count, validate_xgrammar_grammar,
            tag_protocol.validate_structural_tag_payload]
        if any(not Path(m.__file__).resolve().is_relative_to(root / 'vllm') for m in modules) or any(
                not Path(inspect.getsourcefile(fn)).resolve().is_relative_to(root / 'vllm') for fn in functions):
            raise ValueError('STRUCTURED_LIMITS_IMPORT_ORIGIN_MISMATCH')
        if torch.version.cuda is not None:
            raise ValueError('STRUCTURED_LIMITS_CPU_REQUIRED')
        symbols = ('check_json_nesting', 'MAX_STRUCTURED_OUTPUT_JSON_NESTING', 'STRUCTURAL_TAG_WRAPPER_NESTING')
        features = {name: {'module': 'vllm.sampling_params', 'present': hasattr(sampling, name),
                          'collection_status': 'inspected_selected_source'} for name in symbols}
        if not target and (sampling.MAX_STRUCTURED_OUTPUT_JSON_NESTING != DEPTH_LIMIT or
                           sampling.STRUCTURAL_TAG_WRAPPER_NESTING != TAG_WRAPPER_ALLOWANCE):
            raise ValueError('STRUCTURED_LIMITS_REFERENCE_DEPTH_MISMATCH')
        tracked = [('backend_validator', validate_xgrammar_grammar),
            ('json_conversion', xgrammar.Grammar.from_json_schema), ('tag_conversion', xgrammar.Grammar.from_structural_tag)]
        if target:
            tracked += [('request_validator', ChatCompletionRequest.check_structured_outputs_count),
                        ('tag_payload_validator', tag_protocol.validate_structural_tag_payload)]
        if features['check_json_nesting']['present']:
            tracked.append(('nesting_check', sampling.check_json_nesting))
        # Resolve metadata before any of the 15 calls; no later metadata lookup loses rows.
        record.update(source_features=features, own_tool_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            source_file_bytes_sha256={Path(m.__file__).resolve().relative_to(root).as_posix():
                hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules},
            dependencies={k: importlib.metadata.version(k) for k in ('torch', 'pydantic', 'xgrammar')})
        rows = run_reference(ChatCompletionRequest, sampling.SamplingParams, sampling.StructuredOutputsParams,
                             validate_xgrammar_grammar, VLLMValidationError, tracked)
        record['results'] = target031_rows(rows) if target else rows
        _verify_source(root, expected_commit=commit)
        record.update(status='completed', observations=project_target031(record['results']) if target else project(rows))
    except Exception as error:
        record.update(status='execution_error', error_type=type(error).__name__)
        if isinstance(error, ModuleNotFoundError):
            record['missing_module'] = error.name
    record['parent_wall_seconds'] = time.perf_counter() - started
    return record


def main():
    if sys.flags.optimize:
        raise ValueError('STRUCTURED_LIMITS_CPU_REQUIRES_ASSERTIONS')
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-profile', choices=('reference', 'vllm-031'), default='reference')
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('Output exists; choose a fresh evidence path')
    record = capture(args.source_root, source_profile=args.source_profile)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(record, stream, indent=2)
    print(record['status'])
    return 0 if record['status'] == 'completed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
