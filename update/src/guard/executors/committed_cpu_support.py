"""Optional committed CPU support on existing B executors, never live proof.

The A projection owns source/call semantics. Historical capture scope is an
explicit independent input, not the current target's owner or generation.
No projected candidate is inserted into the original judge observations.
"""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
import re
import stat

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from .base import DraftArtifact

MAX_BYTES = 8 * 1024**2
_SHA = re.compile('[0-9a-f]{64}')
_SESSION = re.compile('[0-9a-f]{32}')


def _require(value, code):
    if not value:
        raise ValueError(code)


def _identity(value):
    return {k: getattr(value, 'st_' + k) for k in
            ('dev', 'ino', 'mode', 'uid', 'gid', 'nlink', 'size', 'mtime_ns', 'ctime_ns')}


def _read(pointer):
    _require(type(pointer['path']) is str and '\x00' not in pointer['path'] and
             type(pointer['file_sha256']) is str and _SHA.fullmatch(pointer['file_sha256']),
             'B_CPU_SUPPORT_POINTER_INVALID')
    path = Path(pointer['path'])
    _require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
             'B_CPU_SUPPORT_ABSOLUTE_ORDINARY_PATH_REQUIRED')
    before_path = path.lstat()
    _require(stat.S_ISREG(before_path.st_mode) and 0 < before_path.st_size <= MAX_BYTES,
             'B_CPU_SUPPORT_FILE_BOUND_OR_IDENTITY')
    # A FIFO selected initially is rejected above; a replacement before open
    # must not block the caller before fstat can reject the new identity.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                 | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        _require(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= MAX_BYTES
                 and _identity(before_path) == _identity(before), 'B_CPU_SUPPORT_FILE_BOUND_OR_IDENTITY')
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    _require(_identity(before) == _identity(after) == _identity(path.lstat()) and
             len(raw) == before.st_size and sha256_hex(raw) == pointer['file_sha256'],
             'B_CPU_SUPPORT_BYTES_CHANGED')
    return raw, _identity(after)


def consume(check_id, descriptor):
    """Exact committed A record -> projection, without current-target claims."""
    allowed = {'path', 'file_sha256', 'capture_scope'}
    if check_id == 'SUP-03':
        allowed.add('allowed_auxiliary_sha256')
    _require(check_id in {'LIF-06', 'SUP-03'} and type(descriptor) is dict
             and set(descriptor) <= allowed
             and {'path', 'file_sha256', 'capture_scope'} <= set(descriptor),
             'B_CPU_SUPPORT_CONFIGURATION_INVALID')
    expected = descriptor['capture_scope']
    _require(type(expected) is dict and set(expected) == {'session', 'pod', 'prepared_sha256'}
             and type(expected['session']) is str and _SESSION.fullmatch(expected['session'])
             and type(expected['prepared_sha256']) is str and _SHA.fullmatch(expected['prepared_sha256']),
             'B_CPU_SUPPORT_CAPTURE_SCOPE_REQUIRED')
    pod = expected['pod']
    _require(type(pod) is dict and set(pod) == {'pod_uid', 'container_id', 'restart_count', 'image'}
             and all(type(pod[k]) is str and bool(pod[k]) for k in ('pod_uid', 'container_id', 'image'))
             and type(pod['restart_count']) is int and pod['restart_count'] >= 0,
             'B_CPU_SUPPORT_CAPTURE_GENERATION_INVALID')
    raw, identity = _read(descriptor)
    record = strict_json(raw, limit=MAX_BYTES)
    _require(type(record) is dict and all(record.get(k) == v for k, v in expected.items()),
             'B_CPU_SUPPORT_CAPTURE_SCOPE_MISMATCH')
    from guard.collectors import consumption_links
    kwargs = ({'allowed_auxiliary_sha256': descriptor['allowed_auxiliary_sha256']}
              if 'allowed_auxiliary_sha256' in descriptor else {})
    projection = consumption_links.project(record, **kwargs)
    return raw, {'accepted': True, 'record_sha256': sha256_hex(raw), 'capture_scope': deepcopy(expected),
        'file_identity': identity, 'projection': projection, 'data_origin': 'EXAMPLE',
        'current_target_binding_verified': False, 'GPU_or_registered_producer_claimed': False,
        'whole_question_complete': False, 'original_judge_observations_changed': False}


def _abort(draft, code):
    draft.status = ExecStatus.ABORTED
    draft.abort_reason = draft.abort_reason or code
    draft.cleanup_errors.append(code)


def attach(draft, check, descriptor, workdir):
    """Existing executor -> exact EVD artifacts; original observations survive."""
    if descriptor is None:
        return draft
    # Selecting EXAMPLE support cannot upgrade origin or assert a live join.
    draft.data_origin = DataOrigin.EXAMPLE
    raw = None
    try:
        raw, result = consume(check.id, descriptor)
    except Exception as error:
        result = {'accepted': False, 'whole_question_complete': False,
            'current_target_binding_verified': False, 'GPU_or_registered_producer_claimed': False,
            'input_error': {'type': type(error).__name__, 'message': str(error)[:1024]}}
        _abort(draft, 'B_CPU_SUPPORT_INPUT_UNAVAILABLE')
    report = {'schema': 'guard-b-committed-cpu-support/1', 'check_id': check.id,
        'data_origin': 'EXAMPLE', 'historical_support_only': True, 'result': result}
    root = Path(workdir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            path = root / 'b_cpu_support_record.json'
            with path.open('xb') as stream:
                stream.write(raw)
            draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector='b-committed-cpu-support/1'))
        path = root / 'b_cpu_support.json'
        with path.open('xb') as stream:
            stream.write(canonical_json(report))
        draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector='b-committed-cpu-support/1'))
    except OSError as error:
        # Preserve already received data and independent FAIL observations.
        _abort(draft, 'B_CPU_SUPPORT_ARTIFACT_UNAVAILABLE')
        result['artifact_error'] = {'type': type(error).__name__, 'message': str(error)[:1024]}
    draft.config['b_cpu_support'] = {'accepted': result['accepted'],
        'record_sha256': result.get('record_sha256'), 'data_origin': 'EXAMPLE',
        'capture_scope': result.get('capture_scope'), 'whole_question_complete': False,
        'current_target_binding_verified': False,
        **({'input_error': result['input_error']} if 'input_error' in result else {}),
        **({'artifact_error': result['artifact_error']} if 'artifact_error' in result else {})}
    return draft
