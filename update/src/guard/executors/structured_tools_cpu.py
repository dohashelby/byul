"""Live reference CPU producer; no JSON ingestion or target/runner inference."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib
import importlib.metadata
import inspect
import json
from pathlib import Path
import sys
import subprocess

from guard.models import DataOrigin, ExecStatus
from .base import DraftArtifact, EvidenceDraft
from .http_common import incomplete_artifact_capture
from .structured_limits import observe_call
from .structured_tools import CASE_IDS, SOURCE_COMMIT, cases, project_validator

PROFILE = 'structured-tools-reference-cpu-v1'
def _verify_source(root):
    head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True, stderr=subprocess.DEVNULL).strip()
    changes = subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain', '--', 'vllm'], text=True, stderr=subprocess.DEVNULL)
    if head != SOURCE_COMMIT or changes:
        raise ValueError('STRUCTURED_TOOLS_REFERENCE_MISMATCH')


def _run(request_class, backend_validator, rejection_type, tracked, model):
    """Private algorithm also exercised by EXAMPLE contracts; live entry pins imports."""
    rows = []
    for key, body in cases(model).items():
        row = {'case_id': key, 'input_canonical_sha256': hashlib.sha256(
            json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        def invoke():
            request = request_class.model_validate(body)
            row['request_accepted'] = True
            sampling = request.to_sampling_params(max_tokens=2, default_sampling_params={})
            row['sampling_created'] = True
            row['schema_present'] = sampling.structured_outputs is not None and sampling.structured_outputs.json is not None
            backend_validator(sampling)
        row.update(observe_call(invoke, rejection_type, tracked))
        rows.append(row)
    return rows


def project(rows):
    outcomes = {r['case_id']: 'accepted' if r.get('request_accepted') is True else
                'rejected' if r['outcome'] == 'rejected' else 'execution_error' for r in rows}
    controls = {r['case_id']: 'pass' if r['outcome'] == 'accepted' and
                {'request_validator', 'sampling_conversion', 'backend_validator'}.issubset(r['events']) and
                (r['case_id'] == 'named-only' or 'json_conversion' in r['events']) else 'unknown'
                for r in rows if r['case_id'] != 'named-json'}
    return {**project_validator(outcomes),
        'structured_tools_controls': {'value': controls or None, 'collected': bool(controls)},
        'structured_tools_controls_planned': {'value': [k for k in CASE_IDS if k != 'named-json'], 'collected': True},
        'structured_tools_cpu_rows': {'value': rows or None, 'collected': bool(rows)},
        'runner_consumption_verified': {'value': False, 'collected': True}}


def capture_reference(source_root, *, model):
    """Import and invoke pinned code now. A supplied observation file is never read."""
    root = Path(source_root).resolve()
    if sys.version_info[:2] != (3, 12):
        raise ValueError('STRUCTURED_TOOLS_PYTHON_MISMATCH')
    _verify_source(root)
    protocol = importlib.import_module('vllm.entrypoints.openai.chat_completion.protocol')
    backend = importlib.import_module('vllm.v1.structured_output.backend_xgrammar')
    sampling = importlib.import_module('vllm.sampling_params')
    exceptions = importlib.import_module('vllm.exceptions')
    xgrammar = importlib.import_module('xgrammar')
    torch = importlib.import_module('torch')
    modules = [protocol, backend, sampling, exceptions]
    if torch.version.cuda is not None or any(not Path(m.__file__).resolve().is_relative_to(root / 'vllm') for m in modules):
        raise ValueError('STRUCTURED_TOOLS_CPU_IMPORT_MISMATCH')
    request = protocol.ChatCompletionRequest
    validator = backend.validate_xgrammar_grammar
    # Reject imported replacements even if their owning module reports a pinned path.
    functions = [request.check_structured_outputs_count, request.to_sampling_params, validator]
    if any(not Path(inspect.getsourcefile(fn)).resolve().is_relative_to(root / 'vllm') for fn in functions):
        raise ValueError('STRUCTURED_TOOLS_FUNCTION_ORIGIN_MISMATCH')
    tracked = [('request_validator', request.check_structured_outputs_count),
               ('sampling_conversion', request.to_sampling_params), ('backend_validator', validator),
               ('json_conversion', xgrammar.Grammar.from_json_schema)]
    rows = _run(request, validator, exceptions.VLLMValidationError, tracked, model)
    _verify_source(root)
    return {'profile': PROFILE, 'reference_commit': SOURCE_COMMIT, 'source_clean_verified': True,
            'reference_describe': subprocess.check_output(['git', '-C', str(root), 'describe', '--always', '--tags'], text=True).strip(),
            'producer_file_bytes_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': 'reference_cpu_request_sampling_xgrammar_conversion', 'installed_target': None,
            'backend': 'xgrammar', 'server_preprocessing_verified': False,
            'runner_consumption_verified': False, 'rows': rows,
            'source_file_bytes_sha256': {Path(m.__file__).resolve().relative_to(root).as_posix():
                hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules},
            'dependencies': {k: importlib.metadata.version(k) for k in ('torch', 'xgrammar', 'pydantic')},
            'python_version': sys.version.split()[0]}


def produce_reference_draft(check, env, workdir, *, source_root, model):
    """Existing draft boundary for A wiring; source and imports are checked per run.

    DECLARED ceiling preserves the distinction between a reference CPU process and
    the ENV's installed deployment. A selects the dedicated ENV/PLAN/CheckDef.
    """
    if check.id != 'REQ-10':
        raise ValueError('STRUCTURED_TOOLS_CHECK_MISMATCH')
    started = datetime.now(timezone.utc)
    config = {'profile': PROFILE, 'reference_commit': SOURCE_COMMIT, 'backend': 'xgrammar'}
    try:
        record = capture_reference(source_root, model=model)
    except Exception:
        return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={'attempted': False, 'succeeded': None}, config=config,
            abort_reason='STRUCTURED_TOOLS_CPU_CAPTURE_UNAVAILABLE', started_at=started, finished_at=datetime.now(timezone.utc))
    observations = project(record['rows'])
    controls = observations['structured_tools_controls']['value'] or {}
    draft = EvidenceDraft(status=ExecStatus.COMPLETED,
        data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
        normal_control={'attempted': bool(controls), 'succeeded': len(controls) == 4 and all(v == 'pass' for v in controls.values())},
        challenges=[{'step': step, 'attempted': any(r['case_id'] == 'named-json' for r in record['rows'])} for step in check.challenge_steps],
        observations=observations,
        config=config, started_at=started, finished_at=datetime.now(timezone.utc))
    path = Path(workdir) / 'structured-tools-reference-cpu.json'
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
        draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=PROFILE)]
    except Exception:
        return incomplete_artifact_capture(draft)
    return draft


class StructuredToolsCPUExecutor:
    """Existing executor protocol; A owns registration and dedicated pack/PLAN."""
    name, version = 'structured_tools_cpu', '0.1.0'

    def __init__(self, cfg):
        if type(cfg) is not dict or set(cfg) != {'source_root', 'model'}:
            raise ValueError('STRUCTURED_TOOLS_CPU_CONFIGURATION_INVALID')
        if not isinstance(cfg['model'], str) or not cfg['model']:
            raise ValueError('STRUCTURED_TOOLS_CPU_MODEL_INVALID')
        self.cfg = dict(cfg)

    def run(self, check, env, policy_values, adapter, workdir):
        if policy_values:
            raise ValueError('No enterprise policy is inferred by this reference CPU profile')
        return produce_reference_draft(check, env, workdir, **self.cfg)
