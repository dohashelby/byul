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

Stage 2 (RB2, PACK-BASELINE@36, 2026-11-14 user decisions RB2-Q1~Q7) moves the
run-preparation values: producer record sessions, transition generations and
sample times, scenario hashes and the PLT-07/08 run identities.  They come from
the per-check `[run_binding.<CHECK>]` config table (never an executor section,
which several checks share), so executors and their config hashes are
unchanged.  Each stage is decided separately: an @35 definition still declares
the stage-2 keys and keeps them as policy values while stage 1 stays computed.
A binding with stage-2 values is written as `guard-run-binding/2`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from guard.models import canonical_json, sha256_hex

SCHEMA = 'guard-run-binding/1'
SCHEMA_2 = 'guard-run-binding/2'
SCHEMAS = (SCHEMA, SCHEMA_2)

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
_PHASES = ('before', 'during', 'after', 'recovered')
_TRANSITION = ('transition_inputs_sha256', *(f'transition_{p}_at' for p in _PHASES),
               *(f'transition_{p}_generation' for p in _PHASES))
_EGRESS = ('subject_container_id', 'subject_uid', 'subject_net_namespace_inode',
           'forbidden_receiver_container_id', 'forbidden_receiver_ip', 'forbidden_receiver_port')
_API = ('api_container_id', 'api_network_id', 'api_endpoint_id', 'api_ip', 'api_port')
_SESSION_KEY = {'LIF-12': 'retry_session', 'OBS-01': 'observation_session', 'OBS-02': 'identity_record_session',
                'OBS-06': 'record_inventory_session', 'OBS-11': 'collection_session', 'PLT-12': 'copy_record_session',
                'REQ-02': 'route_session', 'REQ-04': 'forwarded_session', 'REQ-06': 'stored_session'}
# @35 policy keys moved by stage 2 (RB2-Q1/Q2/Q3/Q6).  E keys (retry_scope, copy_inventory,
# record_subject_plan, recovery_selection) stay in policy (RB2-Q4).
KEYS_BY_CHECK_2 = {
    **{c: _TRANSITION for c in ('LIF-05', 'LIF-06', 'LIF-16')},
    'LIF-07': ('init_scenario_sha256',), 'LIF-09': ('init_scenario_sha256',),
    'LIF-11': ('recovery_scenario_sha256',),
    **{c: (k,) for c, k in _SESSION_KEY.items()},
    'PLT-07': ('subject_scope', *_EGRESS),
    'PLT-08': ('api_hop_scope', *_API),
}
# Stage-2 keys that leave the policy without a computed value: REQ-02 records the
# read file's hash instead of comparing a declared one (RB2-Q5, accepted narrowing).
DROPPED_BY_CHECK_2 = {'REQ-02': ('route_file_sha256',)}
STAGES = (KEYS_BY_CHECK, KEYS_BY_CHECK_2)
# Allowed `[run_binding.<CHECK>]` entries per check.
CONFIG_KEYS = {
    **{c: frozenset({'phase_generation', 'sample_offsets_ms'}) for c in ('LIF-05', 'LIF-06', 'LIF-16')},
    **{c: frozenset({'scenario'}) for c in ('LIF-07', 'LIF-09', 'LIF-11')},
    **{c: frozenset({'record_session'}) for c in _SESSION_KEY},
    'PLT-07': frozenset({'egress_binding'}), 'PLT-08': frozenset({'api_binding'}),
}
# Executor contract constants (only one value is valid).
_CONSTANTS = {'subject_scope': 'all_processes_in_container_uid_netns', 'api_hop_scope': 'guard_probe_to_owned_direct_api'}
_MAX_TRANSITION_SPAN_MS = 240000

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
    'LIF-05': 'lif05-stopped-redeploy-v1',
    'LIF-06': 'lif06-live-weights-v1',
    'LIF-16': 'lif16-restore-redeploy-v1',
    'OBS-11': 'collection-gap-visibility-v1',
}
ALL_KEYS = frozenset(k for stage in STAGES for keys in stage.values() for k in keys)

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


def stages_for(check) -> tuple[int, ...]:
    """Stages (1, 2) this CheckDef no longer declares; a stage it still declares keeps its policy values."""
    # Other profiles (source CPU, guard-sensor-run-binding/2 suffix packs) keep their own inputs.
    if getattr(check, 'collection_profile', None) != PROFILE_BY_CHECK.get(check.id):
        return ()
    declared = {p.key for p in check.policy_params}
    out = []
    for number, stage in enumerate(STAGES, 1):
        keys = set(stage.get(check.id, ())) | (set(DROPPED_BY_CHECK_2.get(check.id, ())) if number == 2 else set())
        if keys and not declared & keys:
            out.append(number)
    return tuple(out)


def keys_for(check) -> tuple[str, ...]:
    """Run-binding keys of this CheckDef: empty for a definition that still declares them (@34)."""
    return tuple(k for number in stages_for(check) for k in STAGES[number - 1].get(check.id, ()))


def owned_keys(check):
    """Keys the policy must not carry for this definition (computed or dropped)."""
    stages = stages_for(check)
    return set(keys_for(check)) | (set(DROPPED_BY_CHECK_2.get(check.id, ())) if 2 in stages else set())


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


def _session(value):
    import re
    if type(value) is not str or re.fullmatch('[0-9a-f]{32}', value) is None:
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    return value


def _binding_file(path, keys):
    """Strict JSON object with exactly `keys`; returns (file hash, values)."""
    if type(path) is not str or not path:
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
    value = _load(path, 'json')
    if type(value) is not dict or set(value) != set(keys):
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    return sha256_hex(canonical_json(value)), value


def _transition(executor_cfg, run_cfg, issued_at):
    """LIF-05/06/16 values: generation-input manifest hash, phase generations, sample times."""
    import re
    from guard.executors.lifecycle_transition import inputs_manifest
    generations, offsets = run_cfg.get('phase_generation'), run_cfg.get('sample_offsets_ms')
    if (type(generations) is not dict or set(generations) != set(_PHASES)
            or any(type(v) is not str or re.fullmatch('[a-z][a-z0-9-]{0,31}', v) is None for v in generations.values())
            or type(offsets) is not dict or set(offsets) != set(_PHASES)
            or any(type(v) is not int or not 0 <= v <= _MAX_TRANSITION_SPAN_MS for v in offsets.values())):
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    start = issued_at.astimezone(timezone.utc)
    values = {'transition_inputs_sha256': sha256_hex(canonical_json(inputs_manifest(executor_cfg)))}
    for phase in _PHASES:
        moment = start + timedelta(milliseconds=offsets[phase])
        values[f'transition_{phase}_at'] = moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        values[f'transition_{phase}_generation'] = generations[phase]
    return values


def _stage_2(check, executor_cfg, run_cfg, issued_at):
    """Return (file hashes, values) for the stage-2 keys of this check."""
    if type(run_cfg) is not dict or set(run_cfg) != CONFIG_KEYS.get(check.id, frozenset()):
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    cid, files, values = check.id, {}, {}
    if cid in ('LIF-05', 'LIF-06', 'LIF-16'):
        values = _transition(executor_cfg, run_cfg, issued_at)
    elif cid in ('LIF-07', 'LIF-09', 'LIF-11'):
        key = KEYS_BY_CHECK_2[cid][0]
        path = run_cfg['scenario']
        if type(path) is not str or not path:
            raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
        values[key] = files[key] = sha256_hex(canonical_json(_load(path, 'json')))
    elif cid in _SESSION_KEY:
        values[_SESSION_KEY[cid]] = _session(run_cfg['record_session'])
    elif cid in ('PLT-07', 'PLT-08'):
        name, keys = ('egress_binding', _EGRESS) if cid == 'PLT-07' else ('api_binding', _API)
        files[name + '_sha256'], declared = _binding_file(run_cfg[name], keys)
        values.update(declared)
        constant = 'subject_scope' if cid == 'PLT-07' else 'api_hop_scope'
        values[constant] = _CONSTANTS[constant]
    return files, values


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


def compute(check, executor_cfg, env, *, side, attempt_id, issued_at, valid_from=None, valid_until=None,
            run_cfg=None):
    """Return (binding document, values to give the executor) or raise RunBindingUnavailable.

    `run_cfg` is this check's `[run_binding.<CHECK>]` table (stage 2 only).
    """
    stages = stages_for(check)
    keys = tuple(k for k in KEYS_BY_CHECK.get(check.id, ()) if 1 in stages)
    if not stages:
        return None, {}
    if run_cfg is not None and 2 not in stages:
        # Stage-2 values still come from this definition's policy; never mix both sources.
        raise RunBindingUnavailable('RUN_BINDING_BELONGS_TO_POLICY')
    values, files, extra = {}, {}, {}
    start = end = basis = None
    try:
        if 2 in stages:
            stage_files, extra = _stage_2(check, executor_cfg, run_cfg, issued_at)
            files.update(stage_files)
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
        'schema': SCHEMA_2 if 2 in stages else SCHEMA, 'check_id': check.id, 'side': side, 'attempt_id': attempt_id,
        'environment_sha256': env.ref().sha256, 'files': files,
        'validity': None if start is None else {'start': _stamp(start), 'end': _stamp(end), 'basis': basis},
        'issued_at': _stamp(issued_at),
    }
    if 2 in stages:
        document['values'] = extra
    return document, {**values, **extra}


def run_config(raw, check_id):
    """This check's `[run_binding.<CHECK>]` table from a (side) run config, or None."""
    table = (raw or {}).get('run_binding')
    if table is None:
        return None
    if type(table) is not dict:
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    return table.get(check_id)


def effective_values(check, executor_cfg, env, policy_values, **kwargs):
    """Policy values plus the computed binding values; the policy must not carry them itself."""
    if set(policy_values or {}) & owned_keys(check):
        raise RunBindingUnavailable('RUN_BINDING_BELONGS_TO_EXECUTION')
    if type(executor_cfg) is not dict:
        raise RunBindingUnavailable('RUN_BINDING_UNAVAILABLE')
    document, values = compute(check, executor_cfg, env, **kwargs)
    return document, {**(policy_values or {}), **values}


def verify(document, evidence) -> list[str]:
    """Offline replay check of a recorded binding against its evidence; returns problems."""
    problems = []
    if type(document) is not dict or document.get('schema') not in SCHEMAS:
        return ['RUN_BINDING_SCHEMA_INVALID']
    if (document['schema'] == SCHEMA_2) != ('values' in document):
        problems.append('RUN_BINDING_SCHEMA_INVALID')
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


def _stage_keys(stage):
    if stage == 1:
        return KEYS_BY_CHECK
    if stage == 2:
        return {c: tuple(KEYS_BY_CHECK_2.get(c, ())) + tuple(DROPPED_BY_CHECK_2.get(c, ()))
                for c in set(KEYS_BY_CHECK_2) | set(DROPPED_BY_CHECK_2)}
    raise ValueError('RUN_BINDING_STAGE_UNKNOWN')


def migrate_pack(source, output, *, review=None, stage=1):
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
    by_check = _stage_keys(stage)
    targets = [cid for cid, check in original.checks.items()
               if cid in by_check and {p.key for p in check.policy_params} & set(by_check[cid])]
    if not targets:
        raise ValueError('RUN_BINDING_PACK_MIGRATION_ALREADY_APPLIED')
    shutil.copytree(source, output)
    for check_id in targets:
        path = output / 'checks' / (check_id + '.yaml')
        text = _bump_revision(_drop_policy_keys(path.read_text(encoding='utf-8'), set(by_check[check_id])))
        left = {p['key'] for p in yaml.safe_load(text).get('policy_params') or []}
        if left & set(by_check[check_id]):
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


def migrate_policy_values(values: dict, *, stage=1) -> dict:
    """Policy values for the next pack: drop only the run-binding keys of that stage."""
    by_check = _stage_keys(stage)
    return {cid: {k: v for k, v in (row or {}).items() if k not in by_check.get(cid, ())}
            for cid, row in values.items()}


def run_config_candidates(values: dict, *, issued_at=None) -> dict:
    """Stage-2 `[run_binding.<CHECK>]` candidates from @35 policy values (an operator DRAFT, never applied).

    Sessions, phase generations and the PLT-07/08 identities move as they are.
    Absolute sample times become offsets from the first one; a run start must
    still be chosen before `before`.  Scenario hashes cannot be turned back into
    files and identity files must be written separately: they are listed under
    `needs`.
    """
    out = {}
    for cid, row in values.items():
        row = row or {}
        if cid in _SESSION_KEY and _SESSION_KEY[cid] in row:
            out[cid] = {'record_session': row[_SESSION_KEY[cid]]}
        elif cid in ('LIF-05', 'LIF-06', 'LIF-16') and all(f'transition_{p}_at' in row for p in _PHASES):
            times = [datetime.fromisoformat(row[f'transition_{p}_at'].replace('Z', '+00:00')) for p in _PHASES]
            out[cid] = {'phase_generation': {p: row.get(f'transition_{p}_generation') for p in _PHASES},
                        'sample_offsets_ms': {p: int((t - times[0]).total_seconds() * 1000) for p, t in zip(_PHASES, times)},
                        'needs': ['run start before the first sample; offsets are relative to guard run start']}
        elif cid in ('LIF-07', 'LIF-09', 'LIF-11'):
            out[cid] = {'needs': ['scenario file whose canonical JSON SHA-256 is '
                                  + str(row.get(KEYS_BY_CHECK_2[cid][0]))]}
        elif cid in ('PLT-07', 'PLT-08'):
            keys = _EGRESS if cid == 'PLT-07' else _API
            name = 'egress_binding' if cid == 'PLT-07' else 'api_binding'
            out[cid] = {name + '_content': {k: row.get(k) for k in keys},
                        'needs': [name + ' file with exactly this content']}
    return out
