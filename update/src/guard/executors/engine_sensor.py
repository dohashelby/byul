"""Engine sensor dispatch with explicit implementation and evidence boundaries.

Registration is not a claim that a native capability exists. In particular an
unreviewed source/callback contract is a development dependency, not a collected
UNKNOWN observation. The original PACK31 experiment tools remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import uuid

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from .base import DraftArtifact, EvidenceDraft
from .http_common import incomplete_artifact_capture


PROFILES = {
    'LIF-13': 'engine-sensor-weights-lifetime-v1',
    'REQ-14': 'engine-sensor-distributed-attribution-v1',
    'REQ-15': 'engine-sensor-cache-group-v1',
    'REQ-16': 'engine-sensor-cache-context-v1',
    'REQ-17': 'engine-sensor-cache-timing-v1',
    'REQ-18': 'engine-sensor-slot-lifetime-v1',
    'REQ-19': 'engine-sensor-kernel-bounds-v1',
    'REQ-20': 'engine-sensor-graph-lifetime-v1',
    'REQ-21': 'engine-sensor-communication-buffer-v1',
}
# These checks read the prepared callback session instead of a lifecycle recording.
INFORMATIONAL_GAPS = frozenset({'CACHE_CALLBACK_WEAK_OBJECT_IDENTITY_UNAVAILABLE', 'DISTRIBUTED_NON_EXECUTE_RPC_UNSUPPORTED'})
CALLBACK_CHECKS = frozenset({'LIF-13', 'REQ-14', 'REQ-15', 'REQ-16', 'REQ-17', 'REQ-18', 'REQ-19', 'REQ-20', 'REQ-21'})


def _callback_elapsed_ms(row):
    """Use the probe's request clock, never the enclosing kubectl wall time."""
    if row.get('outcome') != 200 or row.get('capture_complete') is not True:
        return None
    if row.get('timing_basis') == 'batch_monotonic_client_connection_only':
        times = [row.get(key) for key in ('dispatch_ms', 'request_sent_ms', 'headers_ms', 'end_ms')]
        if (any(type(value) is not int for value in times)
                or not 0 <= times[0] <= times[1] <= times[2] <= times[3] <= 120000):
            return None
        return times[3] - times[0]
    if 'timing_basis' in row:
        return None
    value = row.get('elapsed_ms')
    return value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 120000 else None
CPU_PROFILE = 'engine-sensor-slot-reference-cpu-v1'
CPU_PREFIX = 'BYUL_ENGINE_SENSOR_SLOT='
# Reviewed immutable tool/test snapshot. The old product snapshot remains its
# independently selected b15b629 pin; neither value is discovered from HEAD.
CPU_TOOL_SNAPSHOT = 'c8265f135c6bce389daafc6bce9ad11d308a10c9'
CPU_SLOT_TOOL_SHA256 = '649cf7c065dbf085a3b7156a0bbeeb21401c8b02b76a07d13dfa8481c555b544'
CPU_SNAPSHOT_HELPER_SHA256 = '858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
CPU_PROGRAM = r'''
import json,pathlib,sys
root=pathlib.Path(sys.argv[1]).resolve()
sys.path[:0]=[str(root/'update/src'),str(root/'update')]
from tools import b_req18_slot as slot
capture=slot.verify_components(root)
record=capture(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]),source_profile='target031')
try: slot.verify_components(root)
except Exception:
 record['capture_status']='incomplete'
 record['engine_sensor_component_postcheck']='unavailable'
print('BYUL_ENGINE_SENSOR_SLOT='+json.dumps(record,separators=(',',':')))
'''

PACKAGED_CPU_PROGRAM = r'''
import json,pathlib,sys
sys.path.insert(0,sys.argv[1])
from guard.reference import slot_cpu as slot
slot.verify_component()
record=slot.capture_slot_cpu(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3]),source_profile='target031')
try: slot.verify_component()
except Exception:
 record['capture_status']='incomplete'
 record['engine_sensor_component_postcheck']='unavailable'
print('BYUL_ENGINE_SENSOR_SLOT='+json.dumps(record,separators=(',',':')))
'''

# These are required observations, not promises that current sensors emit them.
# Keeping them per check prevents a successful HTTP control from standing in for
# the actual question, or another check's evidence from supplying missing facts.
REQUIRED_BRANCHES = {
    'LIF-13': ('release_actor_scope', 'two_consumer_acquisition', 'post_release_acquisition',
               'fallback_approval_and_consumption', 'existing_native_reference_effect'),
    'REQ-14': ('request_model_generation_rank_binding', 'intermediate_buffer_transfer',
               'buffer_completion_and_release', 'selected_output_rank', 'final_output_attribution'),
    'REQ-15': ('authenticated_principal_binding', 'forged_and_omitted_group',
               'actual_cache_key_entry_consumer', 'all_ordered_group_pairs'),
    'REQ-16': ('required_cache_contexts', 'same_context_reuse', 'changed_context_key',
               'actual_entry_consumption', 'post_control'),
    'REQ-17': ('independent_timing_cohorts', 'private_cache_hit_binding',
               'normal_performance_control', 'enterprise_statistical_policy'),
    'REQ-18': ('slot_replacement_lineage', 'actual_sampler_argument',
               'same_logits_standalone_control', 'state_and_output_comparison', 'actual_backend_consumption'),
    'REQ-19': ('actual_kernel_and_shape', 'buffer_capacity_and_access_range',
               'initialization_consumption', 'gpu_completion', 'independent_reference_output'),
    'REQ-20': ('input_refresh', 'actual_graph_key', 'output_consumption',
               'gpu_completion', 'buffer_generation_binding', 'independent_output_control'),
    'REQ-21': ('registration_generation', 'completion_fence', 'restart_and_reregistration',
               'release_generation', 'buffer_consumption', 'normal_recovered_control'),
}

LONG_LAB_CHECKS = frozenset({'LIF-13', 'REQ-21'})


class EngineSensorNotImplemented(NotImplementedError):
    """A missing implementation must not be reported as a completed collection."""


def _verify_frozen_component(root):
    """Verify foreign code using trusted parent code before importing any of it.

    Tools import tests.builders and the product. Both transitive namespaces must
    therefore be checked before the child gets their import path, even though
    the original gate checks its own files again after import.
    """
    try:
        from tools import b_req18_slot as slot, b1_integration_cpu as helper
    except ImportError:
        raise ValueError('ENGINE_SENSOR_LEGACY_ARTIFACT_REQUIRED') from None
    own_repo = Path(__file__).resolve().parents[4]
    try:
        for module, function, relative, digest in (
            (slot, slot.verify_components, 'update/tools/b_req18_slot.py', CPU_SLOT_TOOL_SHA256),
            (helper, helper.verify_snapshot, 'update/tools/b1_integration_cpu.py', CPU_SNAPSHOT_HELPER_SHA256)):
            slot._identity(module, function, own_repo / relative, digest)
        # This is the existing strict verifier, not the foreign module's method.
        helper.verify_snapshot(Path(root), CPU_TOOL_SNAPSHOT, ('update/tools', 'update/tests'))
        helper.verify_snapshot(Path(root), slot.COMPONENT_COMMIT, helper.PINNED_PATHS)
    except Exception:
        raise ValueError('ENGINE_SENSOR_FROZEN_COMPONENT_PARENT_VERIFICATION_FAILED') from None

def _component_argv(cfg, cache):
    # -B alone disables cache writes, not reads. The private fresh prefix makes
    # every component import compile its verified source instead of old pyc.
    return [cfg.get('python_binary', sys.executable), '-I', '-B', '-X', 'pycache_prefix='+str(cache),
            '-c', CPU_PROGRAM if 'component_root' in cfg else PACKAGED_CPU_PROGRAM,
            str(Path(cfg['component_root']).resolve()) if 'component_root' in cfg else str(Path(__file__).resolve().parents[2]),
            str(Path(cfg['source_root']).resolve()), str(Path(cfg['owned_parent']).resolve())]

def _answer(body):
    """비교용 답변 해시. 원문은 증거에 남기지 않는다."""
    try:
        return sha256_hex(json.loads(body)['choices'][0]['message']['content'].encode())
    except (TypeError, ValueError, KeyError, IndexError, AttributeError):
        return None


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def native_capability(check_id, env=None, *, implementation="kubernetes"):
    """Code availability is separate from profile and runtime qualification."""
    if env is not None:
        from guard.adapters.engine_profiles import resolve
        profile = resolve(env.engine.name, env.engine.version, "native_lifecycle")
        if implementation != "kubernetes" or not profile.available:
            return {"implemented": False, "reason": "ENGINE_SENSOR_PROFILE_UNREVIEWED",
                    "code_available": check_id in PROFILES, "profile_available": False, "runtime_qualified": False}
    if check_id not in PROFILES:
        raise ValueError('ENGINE_SENSOR_CHECK_UNSUPPORTED')
    if check_id in CALLBACK_CHECKS:
        return {'implemented': True, 'reason': None}
    if check_id == 'REQ-18':
        return {'implemented': False, 'reason': 'REVIEWED_SLOT_NATIVE_CONTRACT_UNAVAILABLE'}
    return {'implemented': False, 'reason': 'ENGINE_SENSOR_NATIVE_PRODUCER_UNIMPLEMENTED'}


def slot_observations(record):
    """Reuse the unchanged source-invariant qualifier, including positive facts."""
    from guard.reference import slot_result as slot
    if isinstance(record, dict) and record.get('sensor_artifact') == 'guard-source-cpu/2':
        from guard.reference.integrity import verify_packaged
        pins = verify_packaged('slot')
        result = slot._cpu_result(record, collector_sha256=pins['reference/slot_cpu.py'],
                                  callback_sha256=pins['adapters/vllm_slot_mask.py'])
    else:
        result = slot._cpu_result(record)
    collected = result is not None and result.get('collected') is True
    return {
        'engine_sensor_violation': {'value': result['mismatch'] if collected else None, 'collected': collected},
        'engine_sensor_full_scope': {'value': False, 'collected': True},
        'engine_sensor_gpu_execution': {'value': False, 'collected': True},
        'engine_sensor_rows': {'value': result['rows'] if result is not None else None, 'collected': result is not None},
    }, result


class EngineSensorExecutor:
    """A single executor name; source CPU is never substituted for Kubernetes."""
    name = 'engine_sensor'
    version = '0.3.0'

    def __init__(self, cfg, *, implementation='kubernetes', expected_target_sha256=None):
        self.cfg = deepcopy(cfg)
        self.implementation = implementation
        self.expected_target = expected_target_sha256
        self.transport = None

    def support(self, check_id, collection_profile, env=None):
        if self.implementation == 'source-cpu':
            good = check_id == 'REQ-18' and collection_profile == CPU_PROFILE
            return {'implemented': good, 'scope': 'source_component',
                    'reason': None if good else 'ENGINE_SENSOR_CPU_PROFILE_UNSUPPORTED'}
        from guard.runtime.sensor_binding import SUFFIX
        if self.implementation != 'kubernetes' or collection_profile not in (PROFILES.get(check_id), (PROFILES.get(check_id) or '') + SUFFIX):
            return {'implemented': False, 'scope': 'native', 'reason': 'ENGINE_SENSOR_PROFILE_UNSUPPORTED'}
        state = native_capability(check_id, env, implementation=self.implementation)
        return {**state, 'scope': 'native',
                'code_available': check_id in CALLBACK_CHECKS or check_id == 'REQ-18',
                'profile_available': state.get('profile_available', state['implemented'] if env is not None else None),
                'runtime_qualified': False}

    def validate_policy(self, check, values):
        if check.executor != self.name:
            raise ValueError('ENGINE_SENSOR_EXECUTOR_MISMATCH')
        state = self.support(check.id, check.collection_profile)
        if not state['implemented']:
            raise EngineSensorNotImplemented(state['reason'])
        if self.implementation == 'source-cpu':
            if values:
                raise ValueError('ENGINE_SENSOR_SOURCE_CPU_POLICY_UNSUPPORTED')
        else:
            from guard.runtime.sensor_binding import KEYS, SUFFIX, values as binding_values
            required = KEYS
            if check.collection_profile.endswith(SUFFIX):
                binding_values(self.cfg.get('binding'))
                if set(values) & KEYS:
                    raise ValueError('SENSOR_RUN_BINDING_BELONGS_TO_EXECUTION')
                return
            if type(values) is not dict or not required <= set(values) or any(not _sha(values[k]) for k in required):
                raise ValueError('ENGINE_SENSOR_EXACT_POLICY_BINDING_REQUIRED')

    def run(self, check, env, policy_values, adapter, workdir):
        state = self.support(check.id, check.collection_profile, env)
        if not state['implemented']:
            raise EngineSensorNotImplemented(state['reason'])
        self.validate_policy(check, policy_values)
        if self.implementation == 'source-cpu':
            return self._source_cpu(check, env, workdir)
        from guard.runtime.sensor_binding import SUFFIX, values as binding_values
        bound = binding_values(self.cfg.get('binding')) if check.collection_profile.endswith(SUFFIX) else policy_values
        return self._native(check, env, bound, workdir)

    def _verify_cpu_component(self):
        if 'component_root' in self.cfg:
            _verify_frozen_component(self.cfg['component_root'])
        else:
            from guard.reference.integrity import verify_packaged
            verify_packaged('slot')

    def _source_cpu(self, check, env, workdir):
        from guard.reference import slot_result as slot
        if (set(self.cfg) - {'source_root', 'owned_parent', 'component_root', 'python_binary'}
                or not {'source_root', 'owned_parent'} <= set(self.cfg) or not env.hash_ok()
                or env.engine.name != 'vllm' or env.engine.version != 'source-' + slot.SOURCE_COMMIT
                or env.engine.image_digest is not None
                or any(v is not None for v in (env.model.id, env.model.revision, env.model.inventory_sha256))):
            raise ValueError('ENGINE_SENSOR_SOURCE_CPU_REQUIRES_SEPARATE_ENV')
        # The historical component gate covers the entire original src/pack tree.
        # Execute it in its frozen checkout, never relax it to admit this new file.
        from guard.runtime.bounded import capture_pipes, strict_json
        self._verify_cpu_component()
        began = utcnow()
        cache = tempfile.TemporaryDirectory(prefix='guard-engine-sensor-import-')
        cache_cleanup_failed = False
        try:
            response = capture_pipes(_component_argv(self.cfg, cache.name), timeout=3600, env_extra={},
                max_stdout_bytes=8*1024**2, max_stderr_bytes=4096)
        finally:
            try:
                cache.cleanup()
            except Exception:
                cache_cleanup_failed = True
        pipe = response['capture']
        if (response['returncode'] != 0 or pipe.get('complete') is not True
                or pipe.get('bounded_at_source') is not True or pipe.get('truncated') is not False
                or pipe.get('timed_out') is not False):
            raise ValueError('ENGINE_SENSOR_FROZEN_COMPONENT_CAPTURE_UNAVAILABLE')
        lines = [line[len(CPU_PREFIX):] for line in response['stdout_bytes'].splitlines() if line.startswith(CPU_PREFIX.encode())]
        if len(lines) != 1:
            raise ValueError('ENGINE_SENSOR_FROZEN_COMPONENT_RESULT_UNAVAILABLE')
        raw = strict_json(lines[0], limit=8*1024**2)
        observations, result = slot_observations(raw)
        gap = ['ENGINE_SENSOR_COMPONENT_POSTCHECK_UNAVAILABLE'] if raw.get('engine_sensor_component_postcheck') else []
        if cache_cleanup_failed:
            gap.append('ENGINE_SENSOR_IMPORT_CACHE_CLEANUP_UNVERIFIED')
        try:
            self._verify_cpu_component()
        except Exception:
            gap.append('ENGINE_SENSOR_PARENT_COMPONENT_POSTCHECK_UNAVAILABLE')
        if pipe.get('pipes_closed') is False or 'child_reap' in pipe.get('read_errors', []):
            gap.append('ENGINE_SENSOR_COMPONENT_CLEANUP_UNVERIFIED')
        complete = result is not None and result.get('complete') is True and not gap
        normal = next((r for r in (result or {}).get('rows', []) if r['case_id'] == 'standalone-b'), None)
        draft = EvidenceDraft(
            status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
            data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE or raw.get('data_origin') == 'EXAMPLE' else DataOrigin.DECLARED,
            normal_control={'attempted': normal is not None, 'succeeded': normal.get('qualified') if normal else None,
                            'detail': 'Pinned source component CPU sampler; no API, deployed engine or GPU claim.'},
            observations=observations,
            challenges=[{'step': step, 'attempted': bool(result and result.get('collected'))} for step in check.challenge_steps],
            started_at=began, finished_at=utcnow(),
            abort_reason=None if complete else 'ENGINE_SENSOR_SOURCE_CPU_INCOMPLETE',
            cleanup_errors=list(raw.get('cleanup_errors', [])),
            config={'profile': CPU_PROFILE if 'component_root' in self.cfg else CPU_PROFILE + '-packaged-v2', 'source_commit': slot.SOURCE_COMMIT,
                    'scope': 'source_component', 'installed_target': None, 'gpu_execution_verified': False})
        return self._artifact(draft, workdir, {'safe_facts': result, 'diagnostic_codes': gap})

    def _native(self, check, env, values, workdir):
        from guard.adapters.engine_profiles import require
        from guard.collectors.lifecycle import load_prepared
        from guard.collectors import engine_callbacks
        from guard.runtime.bounded import strict_json
        from guard.executors.kubernetes_lifecycle_capture import validate_target
        if (set(self.cfg) - {'target', 'prepared', 'manifest', 'max_duration_seconds', 'kubectl_binary', 'principal_token_env', 'gateway_target', 'binding', 'comparison_settings'}
                or not {'target', 'prepared', 'manifest'} <= set(self.cfg)
                or not env.hash_ok()):
            raise ValueError('ENGINE_SENSOR_NATIVE_CONFIGURATION_INVALID')
        try:
            require(env.engine.name, env.engine.version, 'native_lifecycle')
        except ValueError:
            raise ValueError('ENGINE_SENSOR_NATIVE_CONFIGURATION_INVALID') from None
        def read(path):
            with Path(path).open('rb') as stream:
                return strict_json(stream.read(262145), limit=262144)
        load = engine_callbacks.load_prepared if check.id in CALLBACK_CHECKS else load_prepared
        target, prepared, manifest = read(self.cfg['target']), load(self.cfg['prepared']), read(self.cfg['manifest'])
        if (values['sensor_environment_sha256'] != env.ref().sha256
                or any(values[key] != sha256_hex(canonical_json(value)) for key, value in (
                    ('sensor_target_sha256', target), ('sensor_prepared_sha256', prepared), ('sensor_manifest_sha256', manifest)))
                or self.expected_target is not None and values['sensor_target_sha256'] != self.expected_target):
            raise ValueError('ENGINE_SENSOR_NATIVE_BINDING_MISMATCH')
        duration = self.cfg.get('max_duration_seconds', 180)
        # 실행 안에서 엔진을 기동하는 lab 점검만 900초까지 받는다. LIF-13은 소비자 엔진을 두 번 기동하고,
        # REQ-21은 TP2 엔진 컨테이너를 재기동한다(재기동 대기만 300초). 다른 점검은 300초 그대로다.
        if type(duration) not in (int, float) or not 0 < duration <= (900 if check.id in LONG_LAB_CHECKS else 300):
            raise ValueError('ENGINE_SENSOR_DURATION_INVALID')
        began = utcnow()
        if check.id in CALLBACK_CHECKS:
            return self._callback(check, env, values, target, prepared, manifest, duration, began, workdir)
        raise EngineSensorNotImplemented(native_capability(check.id)['reason'])

    def _callback(self, check, env, values, target, prepared, manifest, duration, began, workdir):
        """Send the fixed cohort once, then project the session's native rows."""
        from guard.collectors import engine_callbacks
        from guard.runtime.engine_http import KubernetesBindingError, KubernetesHTTP, ENGINE_CALLBACK_PROFILE
        from . import engine_callback_projection as projection
        if (type(manifest) is not dict or manifest.get('schema') != projection.SCHEMA or manifest.get('session') != prepared['session']
                or manifest.get('target_sha256') != values['sensor_target_sha256']
                or manifest.get('environment_ref') != env.ref().model_dump(mode='json')):
            raise ValueError('ENGINE_SENSOR_CALLBACK_MANIFEST_BINDING_MISMATCH')
        cases = manifest.get('cases'); projection.validate_cases(check.id, cases)
        # Independent graph-on/off controls must send identical input bytes.
        # The optional comparison nonce is part of the policy-bound manifest;
        # other checks keep fresh cache-isolating nonces for every run.
        nonce = manifest.get('comparison_nonce')
        if 'comparison_nonce' in manifest and (check.id != 'REQ-20' or target.get('lab') is not True
                or type(nonce) is not str or re.fullmatch('[0-9a-f]{32}', nonce) is None):
            raise ValueError('ENGINE_SENSOR_COMPARISON_NONCE_INVALID')
        if nonce is None:
            nonce = uuid.uuid4().hex
        # REQ-19 대조는 엔진 안에서 GPU 동기화와 복사를 한다. 소유 lab 엔진과 명시한 준비 자산에서만 돈다.
        if check.id in ('REQ-19', 'REQ-20') and (target.get('lab') is not True or prepared.get('lab_kernel_checks') is not True):
            raise ValueError('ENGINE_SENSOR_KERNEL_CHECKS_REQUIRE_LAB')
        references = projection.validate_reference_outputs(cases, manifest.get('reference_outputs')) if check.id == 'REQ-20' else None
        lab = manifest.get('lab_contexts')
        # 엔진 상태를 바꾸는 사례(어댑터 교체·가중치 교체)는 소유한 lab 엔진에서만 돈다.
        if any(c['role'] == 'switch' or 'adapter' in c for c in cases) or lab is not None:
            if target.get('lab') is not True:
                raise ValueError('ENGINE_SENSOR_LAB_CONTEXT_REQUIRES_LAB')
            projection.validate_lab_contexts(lab, cases)
        transport = self.transport
        if transport is None and any('principal' in case for case in cases):
            # A Bearer header on a direct unauthenticated engine is not a
            # principal binding. Join the independently pinned Gateway target.
            from guard.runtime.bounded import strict_json
            from guard.runtime.engine_callback_gateway import KubernetesCallbackGatewayHTTP
            try:
                if check.id != 'REQ-15':
                    raise ValueError
                gateway_target = strict_json(Path(self.cfg['gateway_target']).read_bytes(), limit=65536)
                if manifest.get('gateway_target_sha256') != sha256_hex(canonical_json(gateway_target)):
                    raise ValueError
            except (KeyError, OSError, ValueError, TypeError):
                raise ValueError('ENGINE_SENSOR_GATEWAY_TARGET_BINDING_REQUIRED') from None
            transport = KubernetesCallbackGatewayHTTP(target, gateway_target, env, duration=duration,
                binary=self.cfg.get('kubectl_binary', 'kubectl'))
        elif 'gateway_target' in self.cfg and check.id != 'REQ-15':
            raise ValueError('ENGINE_SENSOR_GATEWAY_TARGET_UNSUPPORTED')
        if transport is None:
            transport = KubernetesHTTP(target, env, duration=duration,
                binary=self.cfg.get('kubectl_binary', 'kubectl'), profile=ENGINE_CALLBACK_PROFILE)
        if isinstance(transport, KubernetesHTTP):
            transport.page_archive = Path(workdir) / 'callback-pages'
        endpoint = transport.endpoint.scheme + '://' + transport.endpoint.netloc + '/v1/chat/completions'
        tags = {}; elapsed = {}; outputs = {}; trials = []; cleanup = []; switches = []; gateway_ids = {}
        if lab is not None:
            try:
                transport.declare_callback_lab(lab)
            except (KubernetesBindingError, ValueError):
                cleanup.append('ENGINE_SENSOR_LAB_OWNERSHIP_UNVERIFIED')
        # 같은 엔진을 다시 시험해도 이전 실행의 캐시가 교차 적중으로 보이지 않게 한다.
        # 경계 요청: 이 요청 앞의 세션 기록은 이번 시험 근거에서 뺀다.
        if hasattr(transport, 'begin_callback_trial'):
            transport.begin_callback_trial(prepared['session'])
        barrier = 'byul-cb-' + uuid.uuid4().hex
        try:
            transport('kubernetes-probe', [{'id': 'cb-9999', 'mode': 'json', 'method': 'POST', 'url': endpoint,
                'headers': {'X-Request-Id': barrier, **self._auth(cases[0])},
                'body': {'model': transport.api_model, 'messages': [{'role': 'user', 'content': nonce + ' barrier'}],
                    'max_tokens': 1, 'stream': False, **({} if 'principal' in cases[0] else {'temperature': 0})}}], 30)
        except (KubernetesBindingError, ValueError):
            cleanup.append('ENGINE_SENSOR_CALLBACK_BARRIER_UNAVAILABLE')
        for number, case in enumerate(() if 'ENGINE_SENSOR_LAB_OWNERSHIP_UNVERIFIED' in cleanup else cases):
            if case['role'] == 'switch':
                fact = {'case_id': case['case_id'], 'action': case['action'], 'variant': case['variant']}
                try:
                    switches.append({**fact, 'completed': True, **transport.engine_callback_switch(case['action'], case['variant'])})
                except (KubernetesBindingError, ValueError):
                    # 바꾸지 못한 문맥의 뒤 사례는 보내지 않는다. 앞선 사례의 행은 그대로 둔다.
                    switches.append({**fact, 'completed': False})
                    cleanup.append('ENGINE_SENSOR_LAB_SWITCH_UNAVAILABLE'); break
                continue
            tag = tags[case['case_id']] = 'byul-cb-' + uuid.uuid4().hex
            body = {'model': case.get('adapter', transport.api_model), 'messages': [{'role': 'user',
                    'content': projection.message_content(nonce + ' ' + case['prompt'], case.get('media'))}],
                'max_tokens': case.get('max_tokens', 1), 'temperature': 0, 'stream': False}
            if 'allowed_token_ids' in case:
                body['allowed_token_ids'] = case['allowed_token_ids']
            if 'principal' in case:
                del body['temperature']  # Gateway가 표본 설정을 정한다
            salt = case.get('forged_salt', case.get('cache_salt'))
            if salt is not None:
                body['cache_salt'] = salt
            try:
                headers = {'X-Request-Id': tag, **self._auth(case)}
            except ValueError:
                cleanup.append('ENGINE_SENSOR_PRINCIPAL_TOKEN_UNAVAILABLE'); break
            try:
                row, = transport('kubernetes-probe', [{'id': f'cb-{number}', 'mode': 'json', 'method': 'POST',
                    'url': endpoint, 'headers': headers, 'body': body}], 30)
            except KubernetesBindingError:
                cleanup.append('ENGINE_SENSOR_CALLBACK_REQUEST_UNAVAILABLE')
                break
            good = row.get('outcome') == 200 and row.get('capture_complete') is True
            trials.append({'case_id': case['case_id'], 'role': case['role'], 'outcome': row.get('outcome'),
                'capture_complete': row.get('capture_complete') is True, 'normal_shape': good})
            if good:
                outputs[case['case_id']] = _answer(row.get('body'))
                if 'principal' in case:
                    gateway_ids[case['case_id']] = row.get('gateway_request_id')
            latency = _callback_elapsed_ms(row)
            if good and latency is not None:
                elapsed[case['case_id']] = latency
        weights_scope = None
        try:
            read = transport.engine_callback_events(prepared['session'])
            if check.id == 'LIF-13':
                # daemon·소비자 엔진 행은 경계 요청이 아니라 현재 컨테이너 세대로 고른다.
                events, weights_scope = projection.weights_window(read['events'], engine_callbacks.load_secret(self.cfg['prepared']),
                                                                  barrier, read.get('container_start_monotonic_ns'))
            else:
                events = read['events'] if any('principal' in case for case in cases) else projection.window(
                    read['events'], engine_callbacks.load_secret(self.cfg['prepared']), barrier)
            if read['truncated']:
                cleanup.append('ENGINE_SENSOR_CALLBACK_EVENTS_TRUNCATED')
        except KubernetesBindingError:
            events = []; cleanup.append('ENGINE_SENSOR_CALLBACK_EVENTS_UNAVAILABLE')
        if check.id == 'LIF-13' and lab is not None:
            try:
                transport.stop_lab_consumers()
            except (KubernetesBindingError, ValueError):
                cleanup.append('ENGINE_SENSOR_LAB_CONSUMER_CLEANUP_UNVERIFIED')
        secret = engine_callbacks.load_secret(self.cfg['prepared'])
        gateway = any('principal' in case for case in cases)
        served = [c for c in cases if outputs.get(c['case_id']) is not None or c['case_id'] in elapsed]
        # Gateway가 4xx로 막은 위조 요청은 엔진에 닿지 않는다.
        rejected = {t['case_id'] for t in trials if t['capture_complete'] and type(t['outcome']) is int and 400 <= t['outcome'] < 500}
        rows = projection.gateway_rows(events, secret, served, gateway_ids) if gateway else projection.computed_rows(events, secret, tags)
        if gateway and any(case['case_id'] not in rows for case in served):
            cleanup.append('ENGINE_SENSOR_GATEWAY_REQUEST_BINDING_UNAVAILABLE')
        if check.id == 'REQ-14':
            result = projection.project_distributed(events, secret, tags)
        elif check.id == 'REQ-15':
            identity = projection.project_identity(cases, rows, rejected) if gateway else None
            result = projection.project_cache_group(cases, rows, values, identity)
        elif check.id == 'REQ-16':
            result = projection.project_cache_context(cases, rows, values)
        elif check.id == 'REQ-18':
            result = projection.project_slot(cases, outputs, events, secret, tags)
        elif check.id == 'REQ-19':
            result = projection.project_kernel_bounds(cases, events, secret, tags)
        elif check.id == 'REQ-20':
            result = projection.project_graph_lifetime(cases, outputs, events, secret, tags, references)
        elif check.id == 'LIF-13':
            result = projection.project_weights_lifetime(cases, outputs, events, switches, weights_scope)
        elif check.id == 'REQ-21':
            result = projection.project_comm_buffer(cases, outputs, events)
        else:
            result = projection.project_cache_timing(cases, rows, elapsed, values)
        # 약한 참조가 안 되는 출력 객체, 관측 대상이 아닌 다른 RPC는 근거 공백이 아니다.
        gaps = sorted({e['code'] for e in events if e.get('phase') == 'GAP' and type(e.get('code')) is str} - INFORMATIONAL_GAPS)
        collected = result['violation'] is not None
        finished = collected and not cleanup
        origin = DataOrigin(transport.process_observation_origin)
        # Gateway가 위조 salt를 4xx로 거부한 것은 도전의 기대 결과다. 정상 대조에서는 뺀다.
        forged = {c['case_id'] for c in cases if 'forged_salt' in c}
        normal_trials = [t for t in trials if not (gateway and t['case_id'] in forged and t['case_id'] in rejected)]
        from guard.runtime.comparison import execution_conditions
        conditions = execution_conditions(target, {'sensor_profile': check.collection_profile,
            'duration': duration, 'options': self.cfg.get('comparison_settings', {})},
            sorted({c['principal'] for c in cases if 'principal' in c}))
        fixture = {'schema': 'guard-native-trials/1', 'check': check.id, 'cases': cases,
            'reference_outputs': manifest.get('reference_outputs'), 'lab_contexts': manifest.get('lab_contexts')}
        comparison = {'model_inventory_sha256': env.model.inventory_sha256,
            'fixture_sha256': sha256_hex(canonical_json(fixture)),
            'config_sha256': sha256_hex(canonical_json(conditions)) if target.get('comparison_config') or self.cfg.get('comparison_settings') else None,
            'gpu': env.runtime.gpu_model, 'driver': env.runtime.driver, 'cpu': env.runtime.cpu}
        # INV-02: 끝나지 않은 실행은 MEASURED가 될 수 없다.
        draft = EvidenceDraft(status=ExecStatus.COMPLETED if finished else ExecStatus.ABORTED,
            data_origin=origin if finished or origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED,
            normal_control={'attempted': bool(normal_trials), 'succeeded': bool(normal_trials) and all(t['normal_shape'] for t in normal_trials)},
            observations={'engine_sensor_violation': {'value': result['violation'], 'collected': collected},
                          'engine_sensor_full_scope': {'value': result['full_scope'] and not gaps and not cleanup, 'collected': True}},
            challenges=[{'step': step, 'attempted': True} for step in check.challenge_steps],
            cleanup_errors=cleanup, started_at=began, finished_at=utcnow(),
            abort_reason=None if collected and not cleanup else 'ENGINE_SENSOR_CALLBACK_SCOPE_INCOMPLETE',
            comparability=comparison,
            config={'profile': check.collection_profile, 'conditions': conditions,
                    'implementation': 'kubernetes', 'engine_started': False})
        return self._artifact(draft, workdir, {'transport': transport.report() if hasattr(transport, 'report') else None, 'safe_facts': {**result, 'trials': trials, 'sensor_gaps': gaps,
                **({'runtime_request_bindings': [
                    {'case_id': case_id, 'runtime_request_sha256': sha256_hex(rid.encode()),
                     'upstream_request_sha256': sha256_hex(('byul-cb-' + rid).encode()),
                     'native_rows_observed': case_id in rows}
                    for case_id, rid in gateway_ids.items() if type(rid) is str and re.fullmatch('[0-9a-f]{32}', rid)]}
                   if gateway else {}),
                'run_binding': {key: values[key] for key in ('sensor_environment_sha256', 'sensor_target_sha256', 'sensor_prepared_sha256', 'sensor_manifest_sha256')},
                **({'comparison_nonce_sha256': sha256_hex(nonce.encode())} if 'comparison_nonce' in manifest else {}),
                **({'lab_switches': switches} if lab is not None else {})},
            'required_branches': REQUIRED_BRANCHES[check.id]})

    def _auth(self, case):
        if 'principal' not in case:
            return {}
        token = os.environ.get((self.cfg.get('principal_token_env') or {}).get(case['principal'], ''))
        if not token:
            raise ValueError('ENGINE_SENSOR_PRINCIPAL_TOKEN_UNAVAILABLE')
        return {'Authorization': 'Bearer ' + token}

    @staticmethod
    def _artifact(draft, workdir, payload):
        path = Path(workdir) / 'engine-sensor.json'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(canonical_json(payload))
            draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector='engine_sensor'))
            for page in sorted((Path(workdir) / 'callback-pages').glob('capture-*/page-*.json')):
                draft.artifacts.append(DraftArtifact(name=page.parent.name + '-' + page.name, path=page,
                                                     collector='engine_sensor/callback-page-2'))
        except Exception:
            return incomplete_artifact_capture(draft)
        return draft
