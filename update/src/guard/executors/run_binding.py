"""Per-run technical binding computed by guard (`guard-run-binding/1`).

PACK-BASELINE@34 asked the enterprise policy for values that change on every
run: the ENV hash, the hashes of the executor's own target/prepared/manifest
files and the observation window.  From @35 these keys are not policy values.
`guard run` computes them right before the executor starts, from the same ENV
object and the same configured files the executor reads, and records the
binding on the evidence.  Executors keep their existing comparisons, so a file
swapped after binding still fails with the executor's existing mismatch error.

A check uses the run binding only when its CheckDef no longer declares the
keys (the @35 definition).  An @34 definition keeps reading the policy values
unchanged, so old packs, policies and evidence replay as before.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from guard.models import canonical_json, sha256_hex

SCHEMA = 'guard-run-binding/1'

# @34 policy keys moved to the run binding (RB-Q1, 2026-11-14 user approval).
# PLT-10, PLT-11 and LIF-11 keep their validity window in policy: their policy
# also declares event/deadline times that must lie inside that window.
KEYS_BY_CHECK = {
    **{c: ('supply_environment_sha256', 'supply_normal_target_sha256', 'supply_challenge_target_sha256',
           'supply_normal_prepared_sha256', 'supply_challenge_prepared_sha256', 'supply_validity_start', 'supply_validity_end')
       for c in ('LIF-01', 'LIF-02', 'PLT-03', 'PLT-05', 'SUP-05', 'SUP-06', 'SUP-07', 'SUP-09', 'SUP-10', 'SUP-11',
                 'SUP-12', 'SUP-15', 'SUP-16')},
    **{c: ('buffer_environment_sha256', 'buffer_target_sha256', 'buffer_prepared_sha256', 'buffer_validity_start',
           'buffer_validity_end') for c in ('LIF-03', 'LIF-04')},
    **{c: ('init_environment_sha256', 'init_target_sha256', 'init_prepared_sha256', 'init_validity_start',
           'init_validity_end') for c in ('LIF-07', 'LIF-09')},
    'LIF-08': ('rank_environment_sha256', 'rank_target_sha256', 'rank_prepared_sha256', 'rank_validity_start', 'rank_validity_end'),
    'LIF-10': ('termination_environment_sha256', 'termination_target_sha256', 'termination_validity_start', 'termination_validity_end'),
    'LIF-11': ('recovery_environment_sha256', 'recovery_target_sha256', 'recovery_prepared_sha256'),
    'LIF-12': ('retry_environment_sha256', 'retry_target_sha256', 'retry_validity_start', 'retry_validity_end'),
    **{c: ('sensor_environment_sha256', 'sensor_target_sha256', 'sensor_prepared_sha256', 'sensor_manifest_sha256')
       for c in ('LIF-13', 'REQ-14', 'REQ-15', 'REQ-16', 'REQ-17', 'REQ-18', 'REQ-19', 'REQ-20', 'REQ-21')},
    'LIF-14': ('profiler_environment_sha256', 'profiler_target_sha256', 'profiler_prepared_sha256', 'profiler_validity_start',
               'profiler_validity_end'),
    'OBS-01': ('observation_environment_sha256', 'observation_target_sha256', 'observation_validity_start', 'observation_validity_end'),
    'OBS-02': ('observation_state_environment_sha256', 'observation_state_validity_start', 'observation_state_validity_end',
               'identity_prepared_sha256', 'profiler_prepared_sha256', 'identity_target_sha256', 'profiler_target_sha256'),
    'OBS-06': ('record_environment_sha256', 'record_target_sha256', 'record_validity_start', 'record_validity_end'),
    'PLT-07': ('validity_start', 'validity_end'),
    'PLT-08': ('validity_start', 'validity_end'),
    'PLT-10': ('revocation_environment_sha256', 'revocation_target_sha256', 'revocation_prepared_sha256'),
    'PLT-11': ('session_environment_sha256', 'session_target_sha256', 'session_prepared_sha256'),
    'PLT-12': ('copy_environment_sha256', 'copy_target_sha256', 'copy_validity_start', 'copy_validity_end'),
    'REQ-02': ('route_environment_sha256', 'route_target_sha256', 'route_validity_start', 'route_validity_end',
               'route_native_environment_sha256', 'route_native_target_sha256', 'route_native_prepared_sha256',
               'route_native_validity_start', 'route_native_validity_end'),
    'REQ-04': ('forwarded_environment_sha256', 'forwarded_target_sha256', 'forwarded_validity_start', 'forwarded_validity_end'),
    'REQ-05': ('model_environment_sha256', 'model_target_sha256', 'model_validity_start', 'model_validity_end'),
    'REQ-06': ('stored_environment_sha256', 'stored_target_sha256', 'stored_validity_start', 'stored_validity_end'),
    'REQ-12': ('attempt_environment_sha256', 'attempt_target_sha256', 'attempt_validity_start', 'attempt_validity_end'),
}
# Collection profile whose @34 policy carried the keys; another profile of the same check is unaffected.
PROFILE_BY_CHECK = {
    'LIF-01': 'supply-boot-stage-budget-v1',
    'LIF-02': 'supply-boot-fetch-retry-v1',
    'LIF-03': 'kubernetes-stream-buffer-budget-observation-v1',
    'LIF-04': 'kubernetes-stream-buffer-observation-v1',
    'LIF-07': 'kubernetes-mp-readiness-observation-v1',
    'LIF-08': 'kubernetes-mp-rank-failure-observation-v1',
    'LIF-09': 'kubernetes-mp-initialization-observation-v1',
    'LIF-10': 'kubernetes-request-termination-observation-v1',
    'LIF-11': 'kubernetes-selected-resource-recovery-v1',
    'LIF-12': 'kubernetes-automatic-retry-record-observation-v1',
    'LIF-13': 'engine-sensor-weights-lifetime-v1',
    'LIF-14': 'kubernetes-profiler-control-v1',
    'OBS-01': 'kubernetes-required-event-record-observation-v1',
    'OBS-02': 'kubernetes-observation-state-composite-v1',
    'OBS-06': 'kubernetes-record-access-scope-v1',
    'PLT-03': 'supply-boot-fetch-scope-v1',
    'PLT-05': 'supply-boot-credential-fallback-v1',
    'PLT-07': 'explicit-forbidden-egress-v1',
    'PLT-08': 'explicit-owned-api-plaintext-v1',
    'PLT-10': 'kubernetes-credential-revocation-observation-v1',
    'PLT-11': 'kubernetes-existing-http-session-observation-v1',
    'PLT-12': 'kubernetes-credential-copy-record-observation-v1',
    'REQ-02': 'kubernetes-route-record-native-composite-v1',
    'REQ-04': 'kubernetes-forwarded-identity-observation-v1',
    'REQ-05': 'kubernetes-model-authorization-observation-v1',
    'REQ-06': 'kubernetes-stored-response-ownership-v1',
    'REQ-12': 'kubernetes-request-attempt-observation-v1',
    'REQ-14': 'engine-sensor-distributed-attribution-v1',
    'REQ-15': 'engine-sensor-cache-group-v1',
    'REQ-16': 'engine-sensor-cache-context-v1',
    'REQ-17': 'engine-sensor-cache-timing-v1',
    'REQ-18': 'engine-sensor-slot-lifetime-v1',
    'REQ-19': 'engine-sensor-kernel-bounds-v1',
    'REQ-20': 'engine-sensor-graph-lifetime-v1',
    'REQ-21': 'engine-sensor-communication-buffer-v1',
    'SUP-05': 'supply-boot-substitution-v1',
    'SUP-06': 'supply-boot-worker-shard-v1',
    'SUP-07': 'supply-boot-cache-tamper-v1',
    'SUP-09': 'supply-boot-model-code-v1',
    'SUP-10': 'supply-boot-plugin-v1',
    'SUP-11': 'supply-boot-python-mode-v1',
    'SUP-12': 'supply-boot-reader-v1',
    'SUP-15': 'supply-boot-cache-reuse-v1',
    'SUP-16': 'supply-boot-partial-artifact-v1',
}
ALL_KEYS = frozenset(k for keys in KEYS_BY_CHECK.values() for k in keys)

# Where each file hash comes from, relative to the executor's config section.
# Loaders are the executors' own: 'json' = strict JSON file, 'lifecycle' =
# collectors.lifecycle.load_prepared, 'callback' = collectors.engine_callbacks.load_prepared.
_FILE_OVERRIDES = {
    'supply_normal_target_sha256': (('normal', 'target'), 'json'),
    'supply_challenge_target_sha256': (('challenge', 'target'), 'json'),
    'supply_normal_prepared_sha256': (('normal', 'prepared'), 'lifecycle'),
    'supply_challenge_prepared_sha256': (('challenge', 'prepared'), 'lifecycle'),
    'route_native_target_sha256': (('native_route', 'target'), 'json'),
    'route_native_prepared_sha256': (('native_route', 'prepared'), 'lifecycle'),
    'sensor_target_sha256': (('target',), 'json'),
    'sensor_prepared_sha256': (('prepared',), 'callback'),
    'sensor_manifest_sha256': (('manifest',), 'json'),
}
_CHECK_FILE_OVERRIDES = {
    'OBS-02': {'identity_target_sha256': (('identity_target',), 'json'),
               'profiler_target_sha256': (('profiler_target',), 'json'),
               'identity_prepared_sha256': (('identity_prepared',), 'lifecycle'),
               'profiler_prepared_sha256': (('profiler_prepared',), 'lifecycle')},
}
# Longest window each executor's policy contract accepts (end - start).
_WINDOW_SECONDS = {'supply_boot': 3600}
_DEFAULT_WINDOW_SECONDS = 600
_MAX_JSON = 262144


class RunBindingUnavailable(ValueError):
    pass


def keys_for(check) -> tuple[str, ...]:
    """Run-binding keys of this CheckDef: empty for a definition that still declares them (@34)."""
    # Other profiles (source CPU, guard-sensor-run-binding/2 suffix packs) keep their own inputs.
    if getattr(check, 'collection_profile', None) != PROFILE_BY_CHECK.get(check.id):
        return ()
    declared = {p.key for p in check.policy_params}
    keys = KEYS_BY_CHECK.get(check.id, ())
    if declared & set(keys):
        return ()
    return keys


def _source(check_id, key):
    if key in _CHECK_FILE_OVERRIDES.get(check_id, {}):
        return _CHECK_FILE_OVERRIDES[check_id][key]
    if key in _FILE_OVERRIDES:
        return _FILE_OVERRIDES[key]
    if key.endswith('_target_sha256'):
        return ('target',), 'json'
    if key.endswith('_prepared_sha256'):
        return ('prepared',), 'lifecycle'
    raise RunBindingUnavailable('RUN_BINDING_KEY_UNSUPPORTED')


def _load(path, loader):
    if loader == 'json':
        from guard.runtime.bounded import strict_json
        with Path(path).open('rb') as stream:
            return strict_json(stream.read(_MAX_JSON + 1), limit=_MAX_JSON)
    if loader == 'lifecycle':
        from guard.collectors.lifecycle import load_prepared
        return load_prepared(path)
    if loader == 'callback':
        from guard.collectors import engine_callbacks
        return engine_callbacks.load_prepared(path)
    raise RunBindingUnavailable('RUN_BINDING_LOADER_UNSUPPORTED')


def file_hash(check_id, key, executor_cfg) -> str:
    """Canonical JSON hash of the configured file, loaded the way the executor loads it."""
    path, loader = _source(check_id, key)
    node = executor_cfg
    for part in path:
        node = node[part]
    if type(node) is not str or not node:
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
    return sha256_hex(canonical_json(_load(node, loader)))


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def window(executor: str, issued_at: datetime, valid_from=None, valid_until=None):
    start = issued_at.astimezone(timezone.utc).replace(microsecond=0)
    end = start + timedelta(seconds=_WINDOW_SECONDS.get(executor, _DEFAULT_WINDOW_SECONDS))
    basis = 'run_window'
    if valid_from is not None or valid_until is not None:
        basis = 'run_window_and_policy'
        if valid_from is not None and valid_from > start:
            raise RunBindingUnavailable('RUN_BINDING_OUTSIDE_POLICY_VALIDITY')
        if valid_until is not None and valid_until < end:
            end = valid_until.astimezone(timezone.utc).replace(microsecond=0)
    if end <= start:
        raise RunBindingUnavailable('RUN_BINDING_OUTSIDE_POLICY_VALIDITY')
    return start, end, basis


def compute(check, executor_cfg, env, *, side, attempt_id, issued_at, valid_from=None, valid_until=None):
    """Return (binding document, values to give the executor) or raise RunBindingUnavailable."""
    keys = keys_for(check)
    if not keys:
        return None, {}
    values, files = {}, {}
    start = end = basis = None
    try:
        if any(k.endswith(('validity_start', 'validity_end')) for k in keys):
            start, end, basis = window(check.executor, issued_at, valid_from, valid_until)
        for key in keys:
            if key.endswith('environment_sha256'):
                values[key] = env.ref().sha256
            elif key.endswith('validity_start'):
                values[key] = _stamp(start)
            elif key.endswith('validity_end'):
                values[key] = _stamp(end)
            else:
                values[key] = files[key] = file_hash(check.id, key, executor_cfg)
    except RunBindingUnavailable:
        raise
    except Exception:
        raise RunBindingUnavailable('RUN_BINDING_UNAVAILABLE') from None
    document = {
        'schema': SCHEMA, 'check_id': check.id, 'side': side, 'attempt_id': attempt_id,
        'environment_sha256': env.ref().sha256, 'files': files,
        'validity': None if start is None else {'start': _stamp(start), 'end': _stamp(end), 'basis': basis},
        'issued_at': _stamp(issued_at),
    }
    return document, values


def effective_values(check, executor_cfg, env, policy_values, **kwargs):
    """Policy values plus the computed binding values; the policy must not carry them itself."""
    keys = keys_for(check)
    if set(policy_values or {}) & set(keys):
        raise RunBindingUnavailable('RUN_BINDING_BELONGS_TO_EXECUTION')
    if type(executor_cfg) is not dict:
        raise RunBindingUnavailable('RUN_BINDING_UNAVAILABLE')
    document, values = compute(check, executor_cfg, env, **kwargs)
    return document, {**(policy_values or {}), **values}


def verify(document, evidence) -> list[str]:
    """Offline replay check of a recorded binding against its evidence; returns problems."""
    problems = []
    if type(document) is not dict or document.get('schema') != SCHEMA:
        return ['RUN_BINDING_SCHEMA_INVALID']
    if document.get('environment_sha256') != evidence.environment.sha256:
        problems.append('RUN_BINDING_ENVIRONMENT_MISMATCH')
    if document.get('check_id') != evidence.check_id or document.get('side') != evidence.side.value:
        problems.append('RUN_BINDING_SUBJECT_MISMATCH')
    if document.get('attempt_id') != evidence.execution.attempt_id:
        problems.append('RUN_BINDING_ATTEMPT_MISMATCH')
    return problems


def _drop_policy_keys(text: str, keys) -> str:
    """Remove `- key: K` entries of the top-level policy_params list, keeping every other byte."""
    import re
    out, skipping, in_params = [], False, False
    for line in text.splitlines(keepends=True):
        if re.match(r'^[A-Za-z_]', line):
            in_params, skipping = line.startswith('policy_params:'), False
        elif in_params and line.startswith('- '):
            match = re.match(r'^- key: *([A-Za-z0-9_]+) *$', line.rstrip('\n'))
            skipping = match is not None and match.group(1) in keys
        if not skipping:
            out.append(line)
    text = ''.join(out)
    import re as _re
    # A list left with no entries must stay a list, not YAML null.
    return _re.sub(r'(?m)^policy_params:\n(?=[A-Za-z_])', 'policy_params: []\n', text)


def _bump_revision(text: str) -> str:
    import re
    new, count = re.subn(r'(?m)^revision: *(\d+) *$', lambda m: f'revision: {int(m.group(1)) + 1}', text, count=1)
    if count != 1:
        raise ValueError('RUN_BINDING_PACK_REVISION_UNREADABLE')
    return new


def migrate_pack(source, output, *, review=None):
    """Write the next pack revision without the run-binding keys; never overwrite the source.

    Only the removed policy entries, the revisions, file hashes and review block change.
    `review` defaults to DRAFT (reviewer and time empty).
    """
    import re
    import shutil
    from hashlib import sha256
    import yaml
    from guard.models import load_pack
    source, output = Path(source), Path(output)
    original = load_pack(source)
    if output.exists() or output.resolve().is_relative_to(source.resolve()):
        raise ValueError('RUN_BINDING_PACK_MIGRATION_NEEDS_NEW_PATH')
    targets = [cid for cid, check in original.checks.items()
               if cid in KEYS_BY_CHECK and {p.key for p in check.policy_params} & set(KEYS_BY_CHECK[cid])]
    if not targets:
        raise ValueError('RUN_BINDING_PACK_MIGRATION_ALREADY_APPLIED')
    shutil.copytree(source, output)
    for check_id in targets:
        path = output / 'checks' / (check_id + '.yaml')
        text = _bump_revision(_drop_policy_keys(path.read_text(encoding='utf-8'), set(KEYS_BY_CHECK[check_id])))
        left = {p['key'] for p in yaml.safe_load(text).get('policy_params') or []}
        if left & set(KEYS_BY_CHECK[check_id]):
            raise ValueError('RUN_BINDING_PACK_MIGRATION_INCOMPLETE')
        path.write_text(text, encoding='utf-8')
    manifest_path = output / 'pack.yaml'
    text = _bump_revision(manifest_path.read_text(encoding='utf-8'))
    for item in yaml.safe_load(text)['files_sha256']:
        digest = sha256((output / item['path']).read_bytes()).hexdigest()
        text = re.sub(r'(- path: ' + re.escape(item['path']) + r'\n +sha256: )[0-9a-f]{64}', lambda m: m.group(1) + digest, text)
    review = review or {'status': 'DRAFT', 'reviewer': None, 'reviewed_at': None}
    head, sep, _ = text.partition('\nreview:\n')
    if not sep:
        raise ValueError('RUN_BINDING_PACK_REVIEW_UNREADABLE')
    text = head + '\n' + yaml.safe_dump({'review': review}, allow_unicode=True, sort_keys=False)
    manifest_path.write_text(text, encoding='utf-8')
    return load_pack(output)


def migrate_policy_values(values: dict) -> dict:
    """Policy values for the @35 pack: drop only the run-binding keys of migrated checks."""
    return {cid: {k: v for k, v in (row or {}).items() if k not in KEYS_BY_CHECK.get(cid, ())}
            for cid, row in values.items()}
