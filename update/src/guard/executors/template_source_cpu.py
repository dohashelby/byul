"""SUP13 fixed source-CPU diagnostic for the original template_validation check.

The common CLI remains dependency-light. Only the approved, bounded collector
child imports the offline source runtime. No arbitrary command or JSON input API.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import stat
import sys

from guard.models import DataOrigin, ExecStatus, canonical_json
from guard.runtime.bounded import capture_pipes, strict_json
from .base import DraftArtifact, EvidenceDraft
from .http_common import incomplete_artifact_capture
from .template_sandbox import validate_policy

SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
PROFILE = 'sup13-fixed-template-source-cpu/1'
MAX_RESULT_BYTES = 1024 * 1024
_CONFIG = {'source_root', 'python_binary', 'producer_checkout', 'collector_sha256', 'callback_sha256'}


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _file_hash(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_RESULT_BYTES:
        raise ValueError('SUP13_APPROVED_FILE_UNAVAILABLE')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _date(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _result(path):
    # This exact fresh child path is the only result channel. Reject symlinks,
    # oversized/truncated documents and duplicate JSON keys before projection.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_RESULT_BYTES:
            raise ValueError('SUP13_RESULT_FILE_UNAVAILABLE')
        return strict_json(stream.read(MAX_RESULT_BYTES + 1), limit=MAX_RESULT_BYTES)


def _normal_control(raw_result, record):
    """Only actual qualified normal render outcomes determine control success."""
    if not isinstance(raw_result, dict) or not isinstance(record, dict):
        return None
    from guard.adapters.template_boundary_evidence import NORMAL_TEXT
    raw_rows = {row.get('case_id'): row for row in raw_result.get('results', []) if isinstance(row, dict)}
    analyzed = {row.get('case_id'): row for row in record.get('rows', []) if isinstance(row, dict)}
    expected_sha = hashlib.sha256(NORMAL_TEXT.encode()).hexdigest()
    states = []
    for case in ('before', 'after'):
        row, reduced = raw_rows.get(case, {}), analyzed.get(case, {})
        if reduced.get('qualified') is not True:
            states.append(None)
        elif reduced.get('normal') is True:
            states.append(True)
        elif row.get('outcome') == 'raised':
            states.append(False)
        elif row.get('outcome') == 'returned':
            summary = row.get('result_summary')
            if (isinstance(summary, dict) and summary.get('status') == 'collected'
                    and summary.get('kind') in {'text', 'token_ids'} and _sha(summary.get('sha256'))
                    and type(summary.get('count')) is int and summary['count'] >= 0
                    and (summary['kind'] != 'text' or summary['sha256'] != expected_sha or summary['count'] != len(NORMAL_TEXT))):
                states.append(False)
            else:
                states.append(None)
        else:
            states.append(None)
    if any(state is False for state in states):
        return False
    return True if all(state is True for state in states) else None


class TemplateSourceCPUExecutor:
    name = 'template_validation'
    version = '0.1.0-source-cpu'

    def __init__(self, cfg):
        if type(cfg) is not dict or set(cfg) != _CONFIG or any(type(cfg[k]) is not str or not cfg[k] for k in _CONFIG):
            raise ValueError('SUP13_SOURCE_CPU_CONFIGURATION_INVALID')
        if not _sha(cfg['collector_sha256']) or not _sha(cfg['callback_sha256']):
            raise ValueError('SUP13_INDEPENDENT_APPROVAL_REQUIRED')
        self.cfg = dict(cfg)

    def _paths(self):
        repo = Path(self.cfg['producer_checkout']).resolve(strict=True)
        source = Path(self.cfg['source_root']).resolve(strict=True)
        python = Path(self.cfg['python_binary']).absolute()
        if (not repo.is_dir() or not source.is_dir() or not (source / 'vllm').is_dir()
                or not python.is_file() or not os.access(python, os.X_OK)):
            raise ValueError('SUP13_SOURCE_CPU_PATHS_INVALID')
        return repo, source, python

    def _verify_files(self, repo):
        collector = repo / 'update/tools/b_sup13_template_cpu.py'
        callback = repo / 'update/src/guard/adapters/vllm_template_boundary.py'
        if (_file_hash(collector) != self.cfg['collector_sha256']
                or _file_hash(callback) != self.cfg['callback_sha256']):
            raise ValueError('SUP13_APPROVED_PRODUCER_BYTES_MISMATCH')

    def run(self, check, env, policy_values, adapter, workdir):
        if (sys.flags.optimize or check.id != 'SUP-13' or check.executor != self.name
                or check.collection_profile != 'supplied-template-type-boundary-v1'
                or not env.hash_ok() or env.engine.name != 'vllm' or env.engine.version != 'source-' + SOURCE_COMMIT
                or env.engine.image_digest is not None
                or any(value is not None for value in (env.model.id, env.model.revision, env.model.inventory_sha256))):
            raise ValueError('SUP13_SOURCE_CPU_BINDING_INVALID')
        # Preserve the original [] / ['__class__'] contract and original
        # inference functional fixture. Core select owns missing/None handling.
        validate_policy(check, policy_values, {})
        workdir = Path(workdir).resolve(strict=True)
        raw_result, record, gaps = None, None, []
        began = datetime.now(timezone.utc)
        child = None
        try:
            repo, source, python = self._paths()
            self._verify_files(repo)
            runtime = workdir / 'source-cpu-runtime'
            runtime.mkdir(exist_ok=False)
            for name in ('tmp', 'cache', 'hf', 'xdg'):
                (runtime / name).mkdir()
            owned = workdir / 'source-cpu-capture'
            if owned.exists() or owned.is_symlink():
                raise ValueError('SUP13_FRESH_CAPTURE_REQUIRED')
            env_extra = {'PYTHONPATH': os.pathsep.join(map(str, (repo / 'update', repo / 'update/src', source))),
                'PYTHONOPTIMIZE': '0', 'PYTHONINSPECT': '0', 'PYTHONHOME': '', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                'CUDA_VISIBLE_DEVICES': '', 'VLLM_PLUGINS': '', 'VLLM_TARGET_DEVICE': 'cpu',
                'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
                'VLLM_CACHE_ROOT': str(runtime / 'cache'), 'HF_HOME': str(runtime / 'hf'),
                'XDG_CACHE_HOME': str(runtime / 'xdg'), 'TMPDIR': str(runtime / 'tmp'),
                'TEMP': str(runtime / 'tmp'), 'TMP': str(runtime / 'tmp')}
            child = capture_pipes([str(python), '-B', '-P', '-m', 'tools.b_sup13_template_cpu',
                '--source-root', str(source), '--workdir', str(owned)], timeout=180,
                env_extra=env_extra, max_stdout_bytes=4096, max_stderr_bytes=16384)
            # An unapproved child file is never promoted through its JSON claims.
            self._verify_files(repo)
            raw_result = _result(owned / 'result.json')
            from guard.adapters.template_boundary_evidence import analyze_cpu_capture
            record = analyze_cpu_capture(raw_result, source_commit=SOURCE_COMMIT,
                collector_sha256=self.cfg['collector_sha256'], callback_sha256=self.cfg['callback_sha256'])
            if record is None:
                gaps.append('SUP13_SOURCE_OBSERVATION_UNAVAILABLE')
            if child.get('returncode') != 0 or child.get('capture', {}).get('complete') is not True:
                gaps.append('SUP13_CHILD_CAPTURE_INCOMPLETE')
                if record is not None:
                    record['complete'] = False
        except Exception:
            gaps.append('SUP13_SOURCE_CPU_CAPTURE_UNAVAILABLE')
        finished = datetime.now(timezone.utc)
        collected = record is not None and any(row.get('qualified') for row in record.get('rows', []))
        reference_collected = collected and record.get('witness') is True and any(
            row.get('case_id') == 'challenge' and row.get('qualified') is True for row in record.get('rows', []))
        complete = collected and record.get('complete') is True and not gaps
        observations = {key.key: {'value': None, 'collected': False} for key in check.required_observations}
        observations.update({
            'template_identity_verified': {'value': record.get('identity') if collected else None, 'collected': collected},
            'template_render_witness_verified': {'value': record.get('witness') if collected else None, 'collected': collected},
            'forbidden_template_type_reference_verified': {
                'value': bool('__class__' in policy_values['forbidden_template_attributes'] and record.get('reference_consumed')) if reference_collected else None,
                'collected': reference_collected},
            'template_sandbox_scope_complete': {'value': False, 'collected': True},
            'functional_probes_planned': {'value': ['POST /v1/chat/completions'], 'collected': True},
            'functional_probe_results': {'value': {'POST /v1/chat/completions': 'unknown'}, 'collected': True}})
        draft = EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
            data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={'attempted': child is not None, 'succeeded': _normal_control(raw_result, record)},
            challenges=[{'step': step, 'attempted': collected} for step in check.challenge_steps],
            observations=observations, config={'profile': PROFILE, 'source_commit': SOURCE_COMMIT,
                'collector_sha256': self.cfg['collector_sha256'], 'callback_sha256': self.cfg['callback_sha256'],
                'collection_id': record.get('collection_id') if record else None, 'installed_target': None,
                'gpu_execution_verified': False, 'full_scope_coverage': False,
                'policy_scope': 'explicit source CPU DRAFT fixture policy; live policy requires separate approval',
                'diagnostic_codes': gaps},
            started_at=_date(record.get('started_at')) if record else began,
            finished_at=_date(record.get('finished_at')) if record else finished,
            abort_reason=None if complete else 'SUP13_SOURCE_CPU_DIAGNOSTIC_INCOMPLETE')
        if record is not None:
            try:
                artifact = workdir / 'template-source-cpu.json'
                with artifact.open('xb') as stream:
                    stream.write(canonical_json({'profile': PROFILE, **record, 'diagnostic_codes': gaps}))
                draft.artifacts = [DraftArtifact(name=artifact.name, path=artifact, collector=PROFILE)]
            except Exception:
                draft = incomplete_artifact_capture(draft)
        return draft
