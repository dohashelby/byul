"""Read selected Pod logs as two declared identities; retain identity-plan hashes and byte counts only.

Roles describe the operator's subject plan. No RBAC, IAM or cluster resource is
changed, and a client error cannot establish that access was denied by policy.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
import re
import tempfile
import time
import uuid

from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader, validate_target
from guard.models import DataOrigin, Ref, canonical_json
from guard.runtime.bounded import capture_pipes, strict_json
from guard.runtime.kubernetes_binding import verify_owned_pod

SCHEMA = 'guard-record-access-capture/1'
PROFILE = 'kubernetes-owned-pod-log-access/1'
_CASES = (('allowed-before', 'authorized_reader'), ('unauthorized', 'unauthorized_reader'),
          ('allowed-after', 'authorized_reader'))
_IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}')


def _digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _policy_ref(value):
    raw = value.model_dump(mode='json') if isinstance(value, Ref) else value
    if (type(raw) is not dict or set(raw) != {'kind', 'id', 'revision', 'sha256'}
            or raw['kind'] != 'POL' or type(raw['id']) is not str or not raw['id'].strip()
            or raw['id'] != raw['id'].strip() or any(ord(c) < 32 for c in raw['id'])
            or type(raw['revision']) is not int or raw['revision'] < 1
            or type(raw['sha256']) is not str or re.fullmatch('[0-9a-f]{64}', raw['sha256']) is None):
        raise ValueError('RECORD_ACCESS_POLICY_REF_INVALID')
    return deepcopy(raw)


def _inputs(env, target, subjects, expected_policy_ref, binary, duration, timeout, injected):
    if injected and env.data_origin != DataOrigin.EXAMPLE:
        raise ValueError('RECORD_ACCESS_INJECTION_REQUIRES_EXAMPLE')
    if type(binary) is not str or not binary or binary.startswith('-') or any(c.isspace() or ord(c) < 32 for c in binary):
        raise ValueError('RECORD_ACCESS_BINARY_INVALID')
    for value, maximum in ((duration, 300), (timeout, 5)):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError('RECORD_ACCESS_BUDGET_INVALID')
    binding = validate_target(target, env)
    ref = _policy_ref(expected_policy_ref)
    if (type(subjects) is not dict or set(subjects) != {'schema', 'environment_ref', 'target_sha256', 'policy_ref', 'subjects'}
            or subjects['schema'] != 'guard-record-access-subjects/1'
            or subjects['environment_ref'] != env.ref().model_dump(mode='json')
            or subjects['target_sha256'] != _digest(target) or _policy_ref(subjects['policy_ref']) != ref
            or type(subjects['subjects']) is not list or len(subjects['subjects']) != 2):
        raise ValueError('RECORD_ACCESS_SUBJECT_PLAN_INVALID')
    selected = {}
    usernames = set()
    for subject in subjects['subjects']:
        if (type(subject) is not dict or set(subject) != {'role', 'username', 'groups'}
                or type(subject['role']) is not str or subject['role'] not in {'authorized_reader', 'unauthorized_reader'}
                or subject['role'] in selected or type(subject['username']) is not str
                or _IDENTITY.fullmatch(subject['username']) is None or subject['username'] in usernames
                or type(subject['groups']) is not list or not 1 <= len(subject['groups']) <= 32
                or any(type(g) is not str or _IDENTITY.fullmatch(g) is None for g in subject['groups'])
                or len(set(subject['groups'])) != len(subject['groups'])):
            raise ValueError('RECORD_ACCESS_SUBJECT_INVALID')
        selected[subject['role']] = deepcopy(subject)
        usernames.add(subject['username'])
    return binding, ref, selected


def _complete(result, limit):
    pipe = result.get('capture', {})
    return (type(result.get('returncode')) is int and type(result.get('stdout_bytes')) is bytes
            and type(result.get('stderr_bytes')) is bytes and len(result['stdout_bytes']) <= limit
            and len(result['stderr_bytes']) <= 4096 and pipe.get('complete') is True
            and pipe.get('bounded_at_source') is True and pipe.get('timed_out') is False
            and pipe.get('truncated') is False and pipe.get('pipes_closed') is True
            and pipe.get('read_errors') == [])


def _identity(result, subject):
    if not _complete(result, 16384) or result['returncode'] != 0:
        return False, 'ACCESS_IDENTITY_UNAVAILABLE'
    try:
        value = strict_json(result['stdout_bytes'], limit=16384)
        info = value['status']['userInfo']
        if (value.get('apiVersion') != 'authentication.k8s.io/v1' or value.get('kind') != 'SelfSubjectReview'
                or type(info.get('username')) is not str or type(info.get('groups')) is not list
                or any(type(g) is not str for g in info['groups'])
                or len(set(info['groups'])) != len(info['groups'])
                or info['username'] != subject['username'] or set(info['groups']) != set(subject['groups'])):
            return False, 'ACCESS_IDENTITY_MISMATCH'
        return True, None
    except (ValueError, KeyError, TypeError):
        return False, 'ACCESS_IDENTITY_UNAVAILABLE'


def capture_record_access(env, target, subjects, *, expected_policy_ref, workdir,
                          binary='kubectl', duration=60, timeout=5, _runner=None, _reader_factory=None):
    """Return partial safe facts after collection errors; reject invalid inputs first."""
    target, subjects = deepcopy(target), deepcopy(subjects)
    binding, policy_ref, selected = _inputs(env, target, subjects, expected_policy_ref, binary, duration, timeout,
                                          _runner is not None or _reader_factory is not None)
    workdir = Path(workdir)
    started = datetime.now(timezone.utc)
    deadline = time.monotonic() + duration
    result = {'schema': SCHEMA, 'profile': PROFILE, 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': _digest(target), 'subject_plan_sha256': _digest(subjects), 'policy_ref': policy_ref,
        'data_origin': 'EXAMPLE' if env.data_origin == DataOrigin.EXAMPLE else 'DECLARED',
        'started_at': started.isoformat(), 'finished_at': None, 'collection_id': 'record-access-' + uuid.uuid4().hex,
        'rows': [], 'normal_control': {'attempted': False, 'succeeded': None},
        'unauthorized_read_verified': None, 'cleanup': {'complete': False, 'collection_gap': True}, 'gaps': []}
    for case, role in _CASES:
        result['rows'].append({'case_id': case, 'role': role, 'attempted': False, 'identity_verified': False,
            'binding_verified': False, 'read_verified': None, 'byte_count': None, 'content_sha256': None,
            'returncode': None, 'gaps': []})
    scratch = None
    child_cleanup = True
    runner = _runner or capture_pipes

    def remaining():
        value = min(timeout, deadline - time.monotonic())
        if value <= 0:
            raise TimeoutError('ACCESS_BUDGET_EXHAUSTED')
        return value

    def dispatch(argv, *, log_row=None, **kwargs):
        nonlocal child_cleanup
        budget = min(kwargs['timeout'], remaining())
        argv = [argv[0], '--cache-dir=' + str(Path(scratch.name) / 'cache'), *argv[1:]]
        kwargs.update(timeout=budget, env_extra={'TMPDIR': scratch.name, 'TMP': scratch.name, 'TEMP': scratch.name})
        try:
            if log_row is not None:
                log_row['attempted'] = True
            value = runner(argv, **kwargs)
            pipe = value.get('capture', {})
            if pipe.get('pipes_closed') is not True or pipe.get('read_errors') != []:
                child_cleanup = False
            return value
        except Exception:
            child_cleanup = False
            raise

    def command(subject, args, limit, *, log_row=None):
        budget = remaining()
        argv = [binary, '--context=' + binding['context'], '--request-timeout=' + f'{budget:.3f}s',
                '--as=' + subject['username'], *['--as-group=' + group for group in subject['groups']], *args]
        return dispatch(argv, log_row=log_row, timeout=budget, env_extra={}, max_stdout_bytes=limit, max_stderr_bytes=4096)

    def snapshot(reader):
        value = reader.snapshot(timeout=remaining())
        owner = verify_owned_pod(value['pod'], value['namespace']['metadata'], binding['engine'], binding['run_id'],
                                 require_ready=False)
        return _digest(owner)

    try:
        if workdir.is_symlink():
            raise ValueError('scratch parent unavailable')
        workdir.mkdir(parents=True, exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix='record-access-', dir=workdir)
        reader = (_reader_factory or KubernetesLifecycleReader)(target, env, {'session': uuid.uuid4().hex},
            duration=max(0, deadline - time.monotonic()), binary=binary, runner=dispatch)
        for row in result['rows']:
            subject = selected[row['role']]
            before = None
            try:
                before = snapshot(reader)
            except Exception:
                row['gaps'].append('ACCESS_BUDGET_EXHAUSTED' if deadline <= time.monotonic() else 'ACCESS_BINDING_UNAVAILABLE')
                continue
            try:
                verified, gap = _identity(command(subject, ['auth', 'whoami', '-o=json'], 16384), subject)
                if not verified:
                    row['gaps'].append(gap)
                else:
                    engine = binding['engine']
                    response = command(subject, ['--namespace=' + engine['namespace'], 'logs', engine['pod'],
                        '--container=' + engine['container'], '--tail=1', '--limit-bytes=4096',
                        '--pod-running-timeout=' + f'{remaining():.3f}s'], 4096, log_row=row)
                    row['returncode'] = response.get('returncode') if type(response.get('returncode')) is int else None
                    complete = _complete(response, 4096)
                    if not complete:
                        row['gaps'].append('ACCESS_CAPTURE_INCOMPLETE')
                    elif response['returncode'] != 0:
                        row['gaps'].append('ACCESS_LOGS_UNAVAILABLE')
                    elif not response['stdout_bytes']:
                        row['gaps'].append('ACCESS_LOGS_EMPTY')
                    else:
                        row['byte_count'] = len(response['stdout_bytes'])
                    verified, gap = _identity(command(subject, ['auth', 'whoami', '-o=json'], 16384), subject)
                    row['identity_verified'] = verified
                    if not verified:
                        row['gaps'].append(gap)
            except Exception:
                row['gaps'].append('ACCESS_BUDGET_EXHAUSTED' if deadline <= time.monotonic() else 'ACCESS_LOGS_UNAVAILABLE')
            finally:
                try:
                    row['binding_verified'] = snapshot(reader) == before
                    if not row['binding_verified']:
                        row['gaps'].append('ACCESS_BINDING_CHANGED')
                except Exception:
                    row['gaps'].append('ACCESS_BUDGET_EXHAUSTED' if deadline <= time.monotonic() else 'ACCESS_BINDING_UNAVAILABLE')
                if row['attempted'] and row['identity_verified'] and row['binding_verified'] and not row['gaps'] and row['byte_count']:
                    row['read_verified'] = True
    except Exception:
        result['gaps'].append('ACCESS_BUDGET_EXHAUSTED' if deadline <= time.monotonic() else 'ACCESS_LOGS_UNAVAILABLE')
    finally:
        cache_clean = False
        if scratch is not None:
            try:
                scratch.cleanup()
                cache_clean = True
            except Exception:
                result['gaps'].append('ACCESS_CLEANUP_UNVERIFIED')
        else:
            cache_clean = True
        for row in result['rows']:
            row['gaps'] = sorted(set(row['gaps']))
            result['gaps'].extend(row['gaps'])
        if not child_cleanup:
            result['gaps'].append('ACCESS_CLEANUP_UNVERIFIED')
        controls = [result['rows'][0], result['rows'][2]]
        result['normal_control'] = {'attempted': any(row['attempted'] for row in controls),
            'succeeded': True if all(row['read_verified'] is True for row in controls) else None}
        result['unauthorized_read_verified'] = result['rows'][1]['read_verified']
        result['gaps'] = sorted(set(result['gaps']))
        result['cleanup'] = {'complete': cache_clean and child_cleanup, 'collection_gap': bool(result['gaps'])}
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
    return result
