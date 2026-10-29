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
TARGET031_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
TARGET031_PROFILE = 'structured-tools-vllm-031-cpu-v1'


def _verify_source(root, *, expected_commit=None):
    """Pin the complete reference package, including files Git status can hide."""
    import io
    import tarfile

    root = Path(root)
    expected_commit = SOURCE_COMMIT if expected_commit is None else expected_commit
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)
    def mismatch():
        raise ValueError('STRUCTURED_TOOLS_REFERENCE_MISMATCH')

    if git('rev-parse', 'HEAD').decode().strip() != expected_commit:
        mismatch()
    if git('diff', '--cached', expected_commit, '--', 'vllm') or git('diff', expected_commit, '--', 'vllm'):
        mismatch()
    tracked = {raw.decode() for raw in git('ls-tree', '-r', '--name-only', '-z', expected_commit, '--', 'vllm').split(b'\0') if raw}
    seen = set()
    with tarfile.open(fileobj=io.BytesIO(git('archive', expected_commit, '--', 'vllm'))) as archive:
        for entry in archive:
            target = root / entry.name
            if entry.isdir():
                if target.is_symlink() or not target.is_dir():
                    mismatch()
            elif entry.issym():
                if not target.is_symlink() or target.readlink().as_posix() != entry.linkname:
                    mismatch()
                seen.add(entry.name)
            elif entry.isfile():
                if target.is_symlink() or not target.is_file() or target.read_bytes() != archive.extractfile(entry).read():
                    mismatch()
                seen.add(entry.name)
            else:
                mismatch()
    # export-ignore must not silently remove tracked files from byte verification.
    if not tracked or seen != tracked:
        mismatch()
    for options in (('--exclude-standard',), ('--ignored', '--exclude-standard')):
        for raw in git('ls-files', '--others', '-z', *options, '--', 'vllm').split(b'\0'):
            if not raw:
                continue
            path = Path(raw.decode())
            target = root / path
            if path.parent.name == '__pycache__' and path.suffix == '.pyc' and target.is_file() and not target.is_symlink():
                continue
            mismatch()


def _run(request_class, backend_validator, rejection_type, tracked, model, *, followups=False):
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
        if followups:
            def after():
                request = request_class.model_validate(cases(model)['auto'])
                backend_validator(request.to_sampling_params(max_tokens=2, default_sampling_params={}))
            row['after_normal_conversion'] = observe_call(after, rejection_type, tracked)
        rows.append(row)
    return rows


def project(rows, *, require_followups=False):
    outcomes = {r['case_id']: 'accepted' if r.get('request_accepted') is True else
                'rejected' if r['outcome'] == 'rejected' else 'execution_error' for r in rows}
    controls = {r['case_id']: 'pass' if r['outcome'] == 'accepted' and
                {'request_validator', 'sampling_conversion', 'backend_validator'}.issubset(r['events']) and
                (r['case_id'] == 'named-only' or 'json_conversion' in r['events']) else 'unknown'
                for r in rows if r['case_id'] != 'named-json'}
    planned = [k for k in CASE_IDS if k != 'named-json']
    if require_followups:
        by_id = {r['case_id']: r for r in rows}
        required = {'request_validator', 'sampling_conversion', 'backend_validator', 'json_conversion'}
        for key in CASE_IDS:
            followup = by_id.get(key, {}).get('after_normal_conversion', {})
            controls['after-' + key] = 'pass' if (followup.get('outcome') == 'accepted'
                and required.issubset(followup.get('events', []))) else 'unknown'
        planned += ['after-' + key for key in CASE_IDS]
    return {**project_validator(outcomes),
        'structured_tools_controls': {'value': controls or None, 'collected': bool(controls)},
        'structured_tools_controls_planned': {'value': planned, 'collected': True},
        'structured_tools_cpu_rows': {'value': rows or None, 'collected': bool(rows)},
        'runner_consumption_verified': {'value': False, 'collected': True}}


def _capture(source_root, *, model, expected_commit, profile, followups=False):
    """Shared live implementation; commit/profile pairs are explicitly selected."""
    if (expected_commit, profile, followups) not in (
            (SOURCE_COMMIT, PROFILE, False), (TARGET031_COMMIT, TARGET031_PROFILE, True)):
        raise ValueError('STRUCTURED_TOOLS_SOURCE_PROFILE_MISMATCH')
    def verify(root):
        if expected_commit == SOURCE_COMMIT:
            _verify_source(root)
        else:
            _verify_source(root, expected_commit=expected_commit)
    root = Path(source_root).resolve()
    if sys.version_info[:2] != (3, 12):
        raise ValueError('STRUCTURED_TOOLS_PYTHON_MISMATCH')
    verify(root)
    if expected_commit == TARGET031_COMMIT:
        _validate_target031_dependencies()
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
    # Collect auxiliary metadata before execution so later lookup failures cannot lose rows.
    record = {'profile': profile, 'reference_commit': expected_commit, 'source_clean_verified': True,
            'reference_describe': subprocess.check_output(['git', '-C', str(root), 'describe', '--always', '--tags'], text=True).strip(),
            'producer_file_bytes_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': 'target031_source_cpu_request_sampling_xgrammar_conversion' if followups else 'reference_cpu_request_sampling_xgrammar_conversion', 'installed_target': None,
            'backend': 'xgrammar', 'server_preprocessing_verified': False,
            'runner_consumption_verified': False,
            'source_file_bytes_sha256': {Path(m.__file__).resolve().relative_to(root).as_posix():
                hashlib.sha256(Path(m.__file__).read_bytes()).hexdigest() for m in modules},
            'dependencies': {k: importlib.metadata.version(k) for k in ('torch', 'xgrammar', 'pydantic')},
            'python_version': sys.version.split()[0]}
    if followups:
        record['dependencies']['transformers'] = importlib.metadata.version('transformers')
        rows = _run(request, validator, exceptions.VLLMValidationError, tracked, model, followups=True)
    else:
        rows = _run(request, validator, exceptions.VLLMValidationError, tracked, model)
    verify(root)
    return {**record, 'rows': rows}


def _produce_draft(check, env, workdir, *, source_root, model, capture, profile, commit, followups=False):
    """Existing draft boundary for A wiring; source and imports are checked per run.

    DECLARED ceiling preserves the distinction between a reference CPU process and
    the ENV's installed deployment. A selects the dedicated ENV/PLAN/CheckDef.
    """
    if check.id != 'REQ-10':
        raise ValueError('STRUCTURED_TOOLS_CHECK_MISMATCH')
    started = datetime.now(timezone.utc)
    config = {'profile': profile, 'reference_commit': commit, 'backend': 'xgrammar'}
    try:
        record = capture(source_root, model=model)
    except Exception:
        return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={'attempted': False, 'succeeded': None}, config=config,
            abort_reason='STRUCTURED_TOOLS_CPU_CAPTURE_UNAVAILABLE', started_at=started, finished_at=datetime.now(timezone.utc))
    observations = project(record['rows'], require_followups=followups)
    controls = observations['structured_tools_controls']['value'] or {}
    draft = EvidenceDraft(status=ExecStatus.COMPLETED,
        data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
        normal_control={'attempted': bool(controls), 'succeeded': set(controls) == set(observations['structured_tools_controls_planned']['value']) and all(v == 'pass' for v in controls.values())},
        challenges=[{'step': step, 'attempted': any(r['case_id'] == 'named-json' for r in record['rows'])} for step in check.challenge_steps],
        observations=observations,
        config=config, started_at=started, finished_at=datetime.now(timezone.utc))
    path = Path(workdir) / ('structured-tools-vllm-031-cpu.json' if followups else 'structured-tools-reference-cpu.json')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(record, stream, sort_keys=True, indent=2)
        draft.artifacts = [DraftArtifact(name=path.name, path=path, collector=profile)]
    except Exception:
        return incomplete_artifact_capture(draft)
    return draft


def _validate_target031_dependencies():
    # packaging is installed by the target transformers CPU dependency. Import
    # only in this profile; the guard product dependency set stays unchanged.
    from packaging.specifiers import SpecifierSet
    required = {'torch': '==2.13.0', 'xgrammar': '==0.2.7', 'transformers': '>=5.10.4,<5.18.0'}
    for name, constraint in required.items():
        if importlib.metadata.version(name) not in SpecifierSet(constraint):
            raise ValueError('STRUCTURED_TOOLS_TARGET031_DEPENDENCY_MISMATCH')


def capture_reference(source_root, *, model):
    """Preserved strict 5d35 reference entry; supplied observation files are not read."""
    return _capture(source_root, model=model, expected_commit=SOURCE_COMMIT, profile=PROFILE)


def capture_target031(source_root, *, model):
    """Live 0.31 source CPU observation, with declared backend dependency checks."""
    return _capture(source_root, model=model, expected_commit=TARGET031_COMMIT,
                    profile=TARGET031_PROFILE, followups=True)


def produce_reference_draft(check, env, workdir, *, source_root, model):
    return _produce_draft(check, env, workdir, source_root=source_root, model=model,
        capture=capture_reference, profile=PROFILE, commit=SOURCE_COMMIT)


def produce_target031_draft(check, env, workdir, *, source_root, model):
    if (not env.hash_ok() or env.engine.name != 'vllm' or type(model) is not str or not model or env.model.id != model
            or env.engine.version != 'reference-' + TARGET031_COMMIT):
        raise ValueError('STRUCTURED_TOOLS_TARGET031_ENVIRONMENT_MISMATCH')
    return _produce_draft(check, env, workdir, source_root=source_root, model=model,
        capture=capture_target031, profile=TARGET031_PROFILE, commit=TARGET031_COMMIT, followups=True)


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


class StructuredToolsTarget031CPUExecutor(StructuredToolsCPUExecutor):
    """0.31 source CPU executor; observations are bound to a dedicated reference ENV."""
    name = 'structured_tools_target031_cpu'

    def run(self, check, env, policy_values, adapter, workdir):
        if policy_values:
            raise ValueError('No enterprise policy is inferred by this target source CPU profile')
        return produce_target031_draft(check, env, workdir, **self.cfg)
