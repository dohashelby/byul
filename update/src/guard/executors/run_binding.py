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

Stage 3 (RB3, PACK-BASELINE@37, 2026-11-15 user instruction "자동으로 채우게
3단계 진행해") moves the values that are not enterprise decisions either:
the record producers' source SHA-256 (the bytes of the producer file named in
`[run_binding.<CHECK>]`; REQ-06 from the reviewed adapter sources the executor
already requires), the OBS-01 test actors and the PLT-10/11 issuer event with
the run window.  Allow-lists, policy-document and template hashes and the
four E keys stayed in policy until the stage-4 decisions below (RB3-Q).

Stage 4 (RB3-Q3/Q4, PACK-BASELINE@38, 2026-11-15 user decisions) computes the
policy-document references as the canonical JSON SHA-256 of this check's values
written in the POL (the key itself is not there), and the hashes of the four
run files that used to carry enterprise decisions: those decisions are now
policy keys (`retry_limits`, `copy_treatments`, `recovery_require_absent_kinds`)
that the executor compares with the file. OBS-06 uses the current sealed POL
Ref, supplied by the plan outside the POL values, to avoid a self reference.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from guard.configuration import (CILIUM_EGRESS_STATUS_OPTION, INSTALLED_PLUGIN_INVENTORY_OPTION,
                                 KUBERNETES_BOUND_CHECK_OPTIONS,
                                 NORMAL_COMPILE_CACHE_OPTION, NORMAL_FILE_READS_OPTION, NORMAL_REQUEST_LATENCY_OPTION,
                                 NORMAL_GRAPH_CAPTURE_OPTION,
                                 NORMAL_SAFETENSORS_READER_OPTION,
                                 NORMAL_SERVING_READINESS_OPTION, NORMAL_STARTUP_LOAD_OPTION,
                                 NORMAL_STREAM_OPTION)
from guard.models import canonical_json, sha256_hex

SCHEMA = 'guard-run-binding/1'
SCHEMA_2 = 'guard-run-binding/2'
SCHEMAS = (SCHEMA, SCHEMA_2)
INITIALIZATION_CHECKS = frozenset({'LIF-07', 'LIF-09'})
SUPPLY_RECORD_CHECKS = frozenset({'SUP-15', 'SUP-16'})
_TARGET_ONLY_NORMAL_MODES = {
    'LIF-01': ('supply_boot', NORMAL_STARTUP_LOAD_OPTION, 'supply_target_sha256'),
    'LIF-03': ('stream_buffer', NORMAL_REQUEST_LATENCY_OPTION, 'buffer_target_sha256'),
    'OBS-02': ('observation_state', NORMAL_SERVING_READINESS_OPTION, 'observation_state_target_sha256'),
    'PLT-07': ('boundary_egress', CILIUM_EGRESS_STATUS_OPTION, 'egress_target_sha256'),
    'REQ-20': ('engine_sensor', NORMAL_GRAPH_CAPTURE_OPTION, 'sensor_target_sha256'),
    'SUP-07': ('supply_boot', NORMAL_COMPILE_CACHE_OPTION, 'supply_target_sha256'),
    'SUP-10': ('supply_boot', INSTALLED_PLUGIN_INVENTORY_OPTION, 'supply_target_sha256'),
    'SUP-12': ('supply_boot', NORMAL_SAFETENSORS_READER_OPTION, 'supply_target_sha256'),
}

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
_SUPPLY_CHECKS = ('LIF-01', 'LIF-02', 'PLT-03', 'PLT-05', 'SUP-05', 'SUP-06', 'SUP-07', 'SUP-09', 'SUP-10',
                  'SUP-11', 'SUP-12', 'SUP-15', 'SUP-16')
# Stage 3 (RB3): `[run_binding.<CHECK>]` producer entry -> policy key it replaces.
PRODUCERS_3 = {
    **{c: {'record_producer': 'supply_record_source_sha256'} for c in _SUPPLY_CHECKS},
    'LIF-12': {'record_producer': 'retry_record_source_sha256'},
    'PLT-12': {'record_producer': 'copy_record_source_sha256'},
    'OBS-02': {'record_producer': 'identity_record_source_sha256'},
    'OBS-06': {'record_producer': 'record_inventory_source_sha256'},
    'OBS-01': {'record_producer': 'observation_expected_source_sha256'},
    'REQ-04': {'gateway_producer': 'forwarded_gateway_source_sha256', 'engine_producer': 'forwarded_engine_source_sha256'},
}
_STORED_SOURCES = ('api_router', 'serving', 'protocol')
_ACTORS = ('observation_allowed_actor_sha256', 'observation_denied_actor_sha256')
_EVENT_FIELDS = {'PLT-10': ('issuer_id', 'event_id', 'event_at', 'deny_after'),
                 'PLT-11': ('issuer_id', 'event_id', 'event_at', 'open_before', 'deny_after')}
_EVENT_PREFIX = {'PLT-10': 'revocation', 'PLT-11': 'session'}
KEYS_BY_CHECK_3 = {
    **{c: tuple(m.values()) for c, m in PRODUCERS_3.items() if c != 'OBS-01'},
    'OBS-01': ('observation_expected_source_sha256', *_ACTORS),
    'REQ-06': tuple(f'stored_{name}_source_sha256' for name in _STORED_SOURCES),
    **{c: (*(f'{_EVENT_PREFIX[c]}_{f}' for f in fields), f'{_EVENT_PREFIX[c]}_validity_start',
           f'{_EVENT_PREFIX[c]}_validity_end') for c, fields in _EVENT_FIELDS.items()},
}
# Stage 4 (RB3-Q3/Q4): policy-document references from the POL row, run-file hashes.
POLICY_DOCUMENT_KEYS = {'LIF-12': 'retry_policy_sha256', 'PLT-12': 'copy_policy_sha256',
                        'OBS-01': 'observation_event_policy_sha256', 'REQ-04': 'forwarded_identity_policy_sha256',
                        'REQ-06': 'stored_identity_policy_sha256'}
RUN_FILE_KEYS = {'LIF-12': 'retry_scope_sha256', 'PLT-12': 'copy_inventory_sha256',
                 'OBS-06': 'record_subject_plan_sha256', 'LIF-11': 'recovery_selection_sha256'}
KEYS_BY_CHECK_4 = {c: tuple(k for k in (RUN_FILE_KEYS.get(c), POLICY_DOCUMENT_KEYS.get(c)) if k)
                   for c in sorted(set(POLICY_DOCUMENT_KEYS) | set(RUN_FILE_KEYS))}
# Enterprise decisions split out of those run files: policy keys from @38 on.
DECISION_KEYS_4 = {'LIF-12': 'retry_limits', 'PLT-12': 'copy_treatments',
                   'LIF-11': 'recovery_require_absent_kinds'}
DECISION_TYPES_4 = {'retry_limits': 'dict', 'copy_treatments': 'dict', 'recovery_require_absent_kinds': 'list[str]'}
CURRENT_POLICY_REF = 'record_access_policy_ref'
STAGES = (KEYS_BY_CHECK, KEYS_BY_CHECK_2, KEYS_BY_CHECK_3, KEYS_BY_CHECK_4)
# Allowed `[run_binding.<CHECK>]` entries per check and stage.
CONFIG_KEYS_3 = {
    **{c: frozenset(m) for c, m in PRODUCERS_3.items()},
    'OBS-01': frozenset({'record_producer', 'test_actors'}),
    **{c: frozenset({'issuer_event'}) for c in _EVENT_FIELDS},
}
_MAX_PRODUCER_BYTES = 16 * 1024 * 1024
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
CONFIG_KEYS_BY_STAGE = {2: CONFIG_KEYS, 3: CONFIG_KEYS_3}
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
# Stage-4 run files ('bytes' = SHA-256 of the file bytes, as the executor compares them).
_FILE_OVERRIDES.update({
    'retry_scope_sha256': (('retry_scope',), 'bytes'),
    'copy_inventory_sha256': (('copy_inventory',), 'bytes'),
    'record_subject_plan_sha256': (('subjects',), 'json'),
    'recovery_selection_sha256': (('selection',), 'json'),
})
_CHECK_FILE_OVERRIDES = {
    'OBS-02': {'identity_target_sha256': (('identity_target',), 'json'),
               'profiler_target_sha256': (('profiler_target',), 'json'),
               'identity_prepared_sha256': (('identity_prepared',), 'lifecycle'),
               'profiler_prepared_sha256': (('profiler_prepared',), 'lifecycle')},
    'SUP-05': {'supply_normal_reference_manifest_sha256': (('normal', 'reference_manifest'), 'bytes')},
}
# Longest window each executor's policy contract accepts (end - start).
_WINDOW_SECONDS = {'supply_boot': 3600}
_DEFAULT_WINDOW_SECONDS = 600
_MAX_JSON = 262144


class RunBindingUnavailable(ValueError):
    pass


def stages_for(check) -> tuple[int, ...]:
    """Stages (1, 2, 3, 4) this CheckDef no longer declares; a stage it still declares keeps its policy values."""
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


def is_supply_record_binding(check) -> bool:
    return (check.id in SUPPLY_RECORD_CHECKS and check.executor == 'supply_boot'
            and {1, 3}.issubset(stages_for(check)))


def is_paired_supply_role_preflight(check, settings, update: dict) -> bool:
    """Existing canonical role files are separate from explicit normal-only modes."""
    return (check.id in _SUPPLY_CHECKS and check.executor == 'supply_boot'
            and type(settings) is dict and ('normal' in settings or 'challenge' in settings)
            and KUBERNETES_BOUND_CHECK_OPTIONS.get(check.id) not in settings
            and update.get('mode', 'external') == 'external'
            and update.get('kubernetes_candidate_binding', 'prebound') == 'prebound'
            and {1, 3}.issubset(stages_for(check)))


def owned_keys(check):
    """Keys the policy must not carry for this definition (computed or dropped)."""
    stages = stages_for(check)
    return (set(keys_for(check)) | (set(DROPPED_BY_CHECK_2.get(check.id, ())) if 2 in stages else set())
            | ({CURRENT_POLICY_REF} if check.id == 'OBS-06' and 4 in stages else set()))


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
    if (check_id == 'LIF-08' and key == 'rank_prepared_sha256'
            and executor_cfg.get('input_mode') == 'common-records'):
        # 파일 소비 경로의 준비물은 선언 JSON이다. 기존 native 실행기는 이 모드를 받지 않는다.
        loader = 'json'
    node = executor_cfg
    for part in path:
        node = node[part]
    if type(node) is not str or not node:
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
    if loader == 'bytes':
        return producer_sha256(node)
    return sha256_hex(canonical_json(_load(node, loader)))


def native_route_preparation_missing(executor_cfg) -> bool:
    """Check only whether the declared manifest itself is absent."""
    from .kubernetes_route_record import has_record_only_native_role

    native_cfg = executor_cfg.get('native_route')
    if not has_record_only_native_role(native_cfg):
        return False
    try:
        Path(native_cfg['prepared']).lstat()
    except FileNotFoundError:
        return True
    return False


def policy_document_sha256(check_id, key, policy_row) -> str:
    """RB3-Q3: the policy document is this check's values as written in the POL (canonical JSON)."""
    if type(policy_row) is not dict or key in policy_row:
        raise RunBindingUnavailable('RUN_BINDING_POLICY_UNAVAILABLE')
    return sha256_hex(canonical_json(policy_row))


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


def config_keys(check) -> frozenset:
    """`[run_binding.<CHECK>]` entries this definition needs (every stage it no longer declares)."""
    stages = stages_for(check)
    return frozenset().union(*(CONFIG_KEYS_BY_STAGE[n].get(check.id, frozenset()) for n in stages if n in CONFIG_KEYS_BY_STAGE))


def producer_sha256(path) -> str:
    """SHA-256 of the producer file's bytes (the `source_sha256` its records must carry)."""
    import os
    import stat
    if type(path) is not str or not path:
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except OSError:
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE') from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_PRODUCER_BYTES:
        os.close(fd)
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(_MAX_PRODUCER_BYTES + 1)
    if len(raw) > _MAX_PRODUCER_BYTES:
        raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
    return sha256_hex(raw)


def _hex(value):
    import re
    if type(value) is not str or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    return value


def _issuer_event(check_id, path):
    """PLT-10/11 issuer event declared by whoever revoked (closed JSON); returns (file hash, values)."""
    digest, event = _binding_file(path, _EVENT_FIELDS[check_id])
    if any(type(v) is not str or not v or len(v) > 64 for v in event.values()):
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    return digest, {f'{_EVENT_PREFIX[check_id]}_{k}': v for k, v in event.items()}


def _stage_3(check, run_cfg, issued_at, valid_from=None, valid_until=None):
    """Return (file hashes, values, validity) for the stage-3 keys of this check."""
    cid, files, values, validity = check.id, {}, {}, None
    for entry, key in PRODUCERS_3.get(cid, {}).items():
        files[entry + '_sha256'] = values[key] = producer_sha256(run_cfg[entry])
    if cid == 'OBS-01':
        actors = run_cfg['test_actors']
        if type(actors) is not dict or set(actors) != {'allowed', 'denied'} or actors['allowed'] == actors['denied']:
            raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
        values.update({f'observation_{role}_actor_sha256': _hex(actors[role]) for role in ('allowed', 'denied')})
    elif cid == 'REQ-06':
        from guard.executors.stored_response_contract import REVIEWED_SOURCES
        values.update({f'stored_{name}_source_sha256': REVIEWED_SOURCES[name] for name in _STORED_SOURCES})
    elif cid in _EVENT_FIELDS:
        files['issuer_event_sha256'], event = _issuer_event(cid, run_cfg['issuer_event'])
        values.update(event)
        start, end, basis = window(check.executor, issued_at, valid_from, valid_until)
        prefix = _EVENT_PREFIX[cid]
        values[prefix + '_validity_start'], values[prefix + '_validity_end'] = _stamp(start), _stamp(end)
        validity = {'start': _stamp(start), 'end': _stamp(end), 'basis': basis}
    return files, values, validity


def initialization_recipe_digests(executor_cfg, run_cfg):
    """Commit bounded raw input bytes separately from the published JSON digests."""
    from guard.adapters.vllm_file_read_sensor import dependency

    return {'prepared': sha256_hex(dependency(executor_cfg['prepared'], None)),
            'scenario': sha256_hex(dependency(run_cfg['scenario'], None))}


def supply_record_recipe_digests(executor_cfg, run_cfg):
    """Commit the two prepared manifests and the record producer's identity bytes."""
    from guard.adapters.vllm_file_read_sensor import dependency

    return {'normal': sha256_hex(dependency(executor_cfg['normal']['prepared'], None)),
            'challenge': sha256_hex(dependency(executor_cfg['challenge']['prepared'], None)),
            'record_producer': producer_sha256(run_cfg['record_producer'])}


def _stage_2(check, executor_cfg, run_cfg, issued_at, *, expected_initialization_scenario_sha256=None):
    """Return (file hashes, values) for the stage-2 keys of this check."""
    cid, files, values = check.id, {}, {}
    if cid in ('LIF-05', 'LIF-06', 'LIF-16'):
        values = _transition(executor_cfg, run_cfg, issued_at)
    elif cid in ('LIF-07', 'LIF-09', 'LIF-11'):
        key = KEYS_BY_CHECK_2[cid][0]
        path = run_cfg['scenario']
        if type(path) is not str or not path:
            raise RunBindingUnavailable('RUN_BINDING_FILE_UNAVAILABLE')
        if cid in INITIALIZATION_CHECKS and expected_initialization_scenario_sha256 is not None:
            from guard.adapters.vllm_file_read_sensor import dependency
            from guard.runtime.bounded import strict_json
            try:
                raw = dependency(path, expected_initialization_scenario_sha256)
            except (OSError, ValueError):
                raise RunBindingUnavailable('RUN_BINDING_INITIALIZATION_SCENARIO_CHANGED') from None
            scenario = strict_json(raw, limit=_MAX_JSON)
        else:
            scenario = _load(path, 'json')
        values[key] = files[key] = sha256_hex(canonical_json(scenario))
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
            run_cfg=None, policy_row=None, policy_ref=None, expected_target_sha256=None, expected_normal_file_sha256=None,
            expected_transition_manifest_sha256=None, expected_initialization_digests=None,
            expected_supply_record_digests=None, native_route_prepared_absent: bool = False):
    """Return (binding document, values to give the executor) or raise RunBindingUnavailable.

    `run_cfg` is this check's `[run_binding.<CHECK>]` table (stages 2 and 3);
    `policy_row` is this check's values as written in the POL (stage 4).
    """
    stages = stages_for(check)
    supply_digests = expected_supply_record_digests
    if supply_digests is not None and (
            not is_supply_record_binding(check) or type(supply_digests) is not dict
            or set(supply_digests) != {'normal', 'challenge', 'record_producer'}):
        raise RunBindingUnavailable('RUN_BINDING_SUPPLY_RECORD_FILE_SCOPE_INVALID')
    if supply_digests is not None:
        for digest in supply_digests.values():
            _hex(digest)
    normal = _TARGET_ONLY_NORMAL_MODES.get(check.id)
    if normal is not None and type(executor_cfg) is dict and normal[1] in executor_cfg:
        executor, marker, key = normal
        error_prefix = f"RUN_BINDING_{check.id.replace('-', '')}_NORMAL"
        if (check.executor != executor or getattr(check, 'collection_profile', None) != PROFILE_BY_CHECK[check.id]
                or executor_cfg[marker] is not True or run_cfg is not None
                or (check.id == 'LIF-03' and NORMAL_STREAM_OPTION in executor_cfg)):
            raise RunBindingUnavailable(f'{error_prefix}_SCOPE_INVALID')
        try:
            target = file_hash(check.id, key, executor_cfg)
        except (KeyError, OSError, TypeError, ValueError):
            raise RunBindingUnavailable(f'{error_prefix}_TARGET_UNAVAILABLE') from None
        if expected_target_sha256 is not None and target != expected_target_sha256:
            raise RunBindingUnavailable(f'{error_prefix}_TARGET_CHANGED')
        document = {'schema': SCHEMA, 'check_id': check.id, 'side': side, 'attempt_id': attempt_id,
                    'environment_sha256': env.ref().sha256, 'files': {key: target}, 'validity': None,
                    'issued_at': _stamp(issued_at)}
        return document, {}
    keys = tuple(k for k in KEYS_BY_CHECK.get(check.id, ()) if 1 in stages)
    normal_file_reads = check.id == 'SUP-05' and NORMAL_FILE_READS_OPTION in executor_cfg
    if normal_file_reads:
        if (check.executor != 'supply_boot' or getattr(check, 'collection_profile', None) != PROFILE_BY_CHECK[check.id]
                or executor_cfg[NORMAL_FILE_READS_OPTION] is not True
                or stages not in {(1,), (1, 3)} or run_cfg is not None):
            raise RunBindingUnavailable('RUN_BINDING_SUP05_NORMAL_SCOPE_INVALID')
        # This mode consumes a sealed native recording and has no supply-record producer.
        stages = (1,)
        keys = (*(key for key in keys if not key.startswith('supply_challenge_')),
                'supply_normal_reference_manifest_sha256')
    if check.id == 'LIF-04' and check.executor == 'stream_buffer' and executor_cfg.get(NORMAL_STREAM_OPTION) is True:
        from .stream_buffer_contract import PROFILE
        if getattr(check, 'collection_profile', None) == PROFILE:
            keys = tuple(key for key in keys if key != 'buffer_prepared_sha256')
    if not stages:
        return None, {}
    wanted = frozenset() if normal_file_reads else config_keys(check)
    if run_cfg is not None and not wanted:
        # These values still come from this definition's policy; never mix both sources.
        raise RunBindingUnavailable('RUN_BINDING_BELONGS_TO_POLICY')
    if wanted and (type(run_cfg) is not dict or set(run_cfg) != wanted):
        raise RunBindingUnavailable('RUN_BINDING_CONFIG_INVALID')
    values, files, extra = {}, {}, {}
    start = end = basis = None
    validity = None
    try:
        expected_prepared = expected_scenario = None
        if expected_initialization_digests is not None:
            expected_prepared = expected_initialization_digests['prepared']
            expected_scenario = expected_initialization_digests['scenario']
        if 2 in stages:
            stage_files, extra = _stage_2(check, executor_cfg, run_cfg, issued_at,
                                         expected_initialization_scenario_sha256=expected_scenario)
            if expected_transition_manifest_sha256 is not None:
                if extra.get('transition_inputs_sha256') != expected_transition_manifest_sha256:
                    raise RunBindingUnavailable('RUN_BINDING_TRANSITION_GENERATIONS_CHANGED')
                from guard.executors.lifecycle_transition import LifecycleTransitionExecutor
                try:
                    LifecycleTransitionExecutor(executor_cfg).prepared_generations(check, env, extra)
                except ValueError:
                    raise RunBindingUnavailable('RUN_BINDING_TRANSITION_GENERATIONS_INVALID') from None
            files.update(stage_files)
        if 3 in stages:
            stage_files, stage_values, validity = _stage_3(check, run_cfg or {}, issued_at, valid_from, valid_until)
            if (supply_digests is not None
                    and stage_values['supply_record_source_sha256'] != supply_digests['record_producer']):
                raise RunBindingUnavailable('RUN_BINDING_SUPPLY_RECORD_PRODUCER_CHANGED')
            files.update(stage_files)
            extra = {**extra, **stage_values}
        if 4 in stages:
            stage_values = {}
            if check.id in RUN_FILE_KEYS:
                key = RUN_FILE_KEYS[check.id]
                stage_values[key] = files[key] = file_hash(check.id, key, executor_cfg)
            if check.id in POLICY_DOCUMENT_KEYS:
                key = POLICY_DOCUMENT_KEYS[check.id]
                stage_values[key] = policy_document_sha256(check.id, key, policy_row)
            if check.id == 'OBS-06':
                from .kubernetes_record_access import _policy_ref
                try:
                    stage_values[CURRENT_POLICY_REF] = _policy_ref(policy_ref)
                except ValueError:
                    raise RunBindingUnavailable('RUN_BINDING_POLICY_REF_UNAVAILABLE') from None
            extra = {**extra, **stage_values}
        if any(k.endswith(('validity_start', 'validity_end')) for k in keys):
            start, end, basis = window(check.executor, issued_at, valid_from, valid_until)
        if (native_route_prepared_absent is True and check.id == 'REQ-02' and check.executor == 'route_record'
                and stages == (1, 2)):
            from guard.runtime.kubernetes_http import _validate as validate_target
            from .kubernetes_route_record import has_record_only_native_role
            from .native_route_contract import POLICY_TYPES

            # The sealed recipe chose record-only. A later-created native file
            # cannot add a subcollection, and the declared owner must still match.
            native_cfg = executor_cfg.get('native_route')
            if not has_record_only_native_role(native_cfg):
                raise RunBindingUnavailable('RUN_BINDING_UNAVAILABLE')
            target = _load(native_cfg['target'], 'json')
            if (type(target) is not dict or target.get('schema') != 'guard-kubernetes-request-binding-target/1'
                    or target.get('environment_ref') != env.ref().model_dump(mode='json')):
                raise RunBindingUnavailable('RUN_BINDING_UNAVAILABLE')
            validate_target(target, env)
            keys = tuple(key for key in keys if key not in POLICY_TYPES)
        for key in keys:
            if key.endswith('environment_sha256'):
                values[key] = env.ref().sha256
            elif key.endswith('validity_start'):
                values[key] = _stamp(start)
            elif key.endswith('validity_end'):
                values[key] = _stamp(end)
            elif key == 'init_prepared_sha256' and (expected_prepared is not None
                    or (type(executor_cfg.get('prepared')) is str and executor_cfg['prepared'])):
                from guard.adapters.vllm_release_profiles import InitializationPreparationMismatch
                from guard.collectors.lifecycle import load_initialization_prepared
                try:
                    prepared = load_initialization_prepared(executor_cfg['prepared'], env, expected_sha256=expected_prepared)
                except InitializationPreparationMismatch as exc:
                    raise RunBindingUnavailable(str(exc)) from None
                except (OSError, ValueError):
                    if expected_prepared is None:
                        raise
                    raise RunBindingUnavailable('RUN_BINDING_INITIALIZATION_PREPARED_CHANGED') from None
                values[key] = files[key] = sha256_hex(canonical_json(prepared))
            elif normal_file_reads and expected_normal_file_sha256 is not None and key == 'supply_normal_prepared_sha256':
                from guard.collectors.lifecycle import load_prepared
                try:
                    manifest = load_prepared(executor_cfg['normal']['prepared'],
                                             expected_sha256=expected_normal_file_sha256['prepared'])
                except (OSError, ValueError):
                    raise RunBindingUnavailable('RUN_BINDING_SUP05_NORMAL_PREPARED_CHANGED') from None
                values[key] = files[key] = sha256_hex(canonical_json(manifest))
            elif supply_digests is not None and key in {'supply_normal_prepared_sha256', 'supply_challenge_prepared_sha256'}:
                from guard.collectors.lifecycle import load_prepared
                role = key.split('_')[1]
                try:
                    manifest = load_prepared(executor_cfg[role]['prepared'], expected_sha256=supply_digests[role])
                except (OSError, ValueError):
                    raise RunBindingUnavailable(f'RUN_BINDING_SUPPLY_{role.upper()}_PREPARED_CHANGED') from None
                values[key] = files[key] = sha256_hex(canonical_json(manifest))
            else:
                values[key] = files[key] = file_hash(check.id, key, executor_cfg)
        if (normal_file_reads and expected_target_sha256 is not None
                and values['supply_normal_target_sha256'] != expected_target_sha256):
            raise RunBindingUnavailable('RUN_BINDING_SUP05_NORMAL_TARGET_CHANGED')
        if (normal_file_reads and expected_normal_file_sha256 is not None
                and values['supply_normal_reference_manifest_sha256'] != expected_normal_file_sha256['reference_manifest']):
            raise RunBindingUnavailable('RUN_BINDING_SUP05_NORMAL_REFERENCE_CHANGED')
    except RunBindingUnavailable:
        raise
    except Exception:
        raise RunBindingUnavailable('RUN_BINDING_UNAVAILABLE') from None
    with_values = bool({2, 3, 4} & set(stages))
    document = {
        'schema': SCHEMA_2 if with_values else SCHEMA, 'check_id': check.id, 'side': side, 'attempt_id': attempt_id,
        'environment_sha256': env.ref().sha256, 'files': files,
        'validity': validity if start is None else {'start': _stamp(start), 'end': _stamp(end), 'basis': basis},
        'issued_at': _stamp(issued_at),
    }
    if with_values:
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
    if (check.id == 'SUP-05' and NORMAL_FILE_READS_OPTION in executor_cfg
            and 'supply_normal_reference_manifest_sha256' in (policy_values or {})):
        raise RunBindingUnavailable('RUN_BINDING_BELONGS_TO_EXECUTION')
    kwargs.setdefault('policy_row', dict(policy_values or {}))
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
    if stage == 3:
        return KEYS_BY_CHECK_3
    if stage == 4:
        return KEYS_BY_CHECK_4
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
        if stage == 4 and check_id in DECISION_KEYS_4:
            key = DECISION_KEYS_4[check_id]
            parameter = f'- key: {key}\n  type: {DECISION_TYPES_4[key]}\n  required: true\n'
            if 'policy_params: []\n' in text:
                text = text.replace('policy_params: []\n', 'policy_params:\n' + parameter, 1)
            else:
                text = text.replace('policy_params:\n', 'policy_params:\n' + parameter, 1)
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


def run_config_candidates_3(values: dict) -> dict:
    """Stage-3 `[run_binding.<CHECK>]` candidates from @36 policy values (an operator DRAFT, never applied).

    OBS-01 test actors move as they are.  A producer source SHA cannot be turned
    back into its file and an issuer event must be written as a file by whoever
    revoked: they are listed under `needs` with the value they must reproduce.
    REQ-06 needs nothing (the reviewed adapter sources).
    """
    out = {}
    for cid, row in values.items():
        row, entry, needs = row or {}, {}, []
        for name, key in PRODUCERS_3.get(cid, {}).items():
            needs.append(f'{name}: producer file whose bytes SHA-256 is {row.get(key)}')
        if cid == 'OBS-01' and all(k in row for k in _ACTORS):
            entry['test_actors'] = {role: row[f'observation_{role}_actor_sha256'] for role in ('allowed', 'denied')}
        if cid in _EVENT_FIELDS:
            prefix = _EVENT_PREFIX[cid]
            entry['issuer_event_content'] = {f: row.get(f'{prefix}_{f}') for f in _EVENT_FIELDS[cid]}
            needs.append('issuer_event file with exactly this content; validity is the run window')
        if entry or needs:
            out[cid] = {**entry, **({'needs': needs} if needs else {})}
    return out


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
