"""Engine sensor dispatch with explicit implementation and evidence boundaries.

Registration is not a claim that a native capability exists. In particular an
unreviewed source/callback contract is a development dependency, not a collected
UNKNOWN observation. The original PACK31 experiment tools remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import hashlib
import re
import sys
import tempfile

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


class EngineSensorNotImplemented(NotImplementedError):
    """A missing implementation must not be reported as a completed collection."""


def _verify_frozen_component(root):
    """Verify foreign code using trusted parent code before importing any of it.

    Tools import tests.builders and the product. Both transitive namespaces must
    therefore be checked before the child gets their import path, even though
    the original gate checks its own files again after import.
    """
    from tools import b_req18_slot as slot, b1_integration_cpu as helper
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
            '-c', CPU_PROGRAM, str(Path(cfg['component_root']).resolve()),
            str(Path(cfg['source_root']).resolve()), str(Path(cfg['owned_parent']).resolve())]


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def native_capability(check_id):
    """Read only reviewed program constants; never approve pins from input/HEAD."""
    if check_id not in PROFILES:
        raise ValueError('ENGINE_SENSOR_CHECK_UNSUPPORTED')
    if check_id == 'REQ-18':
        from tools import b_req18_slot as slot
        from guard.runtime import kubernetes_http
        ready = (type(slot.A_SOURCE_PINS) is dict and bool(slot.A_SOURCE_PINS)
                 and all(_sha(v) for v in slot.A_SOURCE_PINS.values())
                 and type(slot.POINT_CONTRACTS) is dict and bool(slot.POINT_CONTRACTS)
                 and _sha(slot.CALLBACK_CODE_SHA256)
                 and type(getattr(kubernetes_http, 'SLOT_MASK_PROFILE', None)) is str)
        return {'implemented': bool(ready), 'reason': None if ready else 'REVIEWED_SLOT_NATIVE_CONTRACT_UNAVAILABLE'}
    if check_id == 'REQ-20':
        from tools import b_req20_graph as graph
        # _capability performs additional exact module/code/profile checks at run.
        ready = type(graph.NATIVE_CONTRACT) is dict and bool(graph.NATIVE_CONTRACT)
        return {'implemented': bool(ready), 'reason': None if ready else 'REVIEWED_GRAPH_NATIVE_CONTRACT_UNAVAILABLE'}
    reasons = {
        'LIF-13': 'WEIGHTS_TOOL_HAS_NO_NATIVE_AUTHORIZATION_OR_REFERENCE_ATTESTATION',
        'REQ-14': 'CROSS_RANK_INTERMEDIATE_BUFFER_AND_OUTPUT_ATTRIBUTION_PRODUCER_UNIMPLEMENTED',
        'REQ-15': 'AUTHENTICATED_CACHE_KEY_ENTRY_CONSUMER_PRODUCER_UNIMPLEMENTED',
        'REQ-16': 'CONTEXT_CHANGE_TO_CACHE_CONSUMPTION_PRODUCER_UNIMPLEMENTED',
        'REQ-17': 'INDEPENDENT_CACHE_TIMING_COHORT_PRODUCER_UNIMPLEMENTED',
        'REQ-19': 'ACTUAL_KERNEL_BOUNDS_AND_REFERENCE_OUTPUT_PRODUCER_UNIMPLEMENTED',
        'REQ-21': 'GPU_COMPLETION_AND_REGISTRATION_GENERATION_PRODUCER_UNIMPLEMENTED',
    }
    return {'implemented': False, 'reason': reasons[check_id]}


def slot_observations(record):
    """Reuse the unchanged source-invariant qualifier, including positive facts."""
    from tools import b_req18_slot as slot
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
    version = '0.1.0'

    def __init__(self, cfg, *, implementation='kubernetes', expected_target_sha256=None):
        self.cfg = deepcopy(cfg)
        self.implementation = implementation
        self.expected_target = expected_target_sha256

    def support(self, check_id, collection_profile):
        if self.implementation == 'source-cpu':
            good = check_id == 'REQ-18' and collection_profile == CPU_PROFILE
            return {'implemented': good, 'scope': 'source_component',
                    'reason': None if good else 'ENGINE_SENSOR_CPU_PROFILE_UNSUPPORTED'}
        if self.implementation != 'kubernetes' or PROFILES.get(check_id) != collection_profile:
            return {'implemented': False, 'scope': 'native', 'reason': 'ENGINE_SENSOR_PROFILE_UNSUPPORTED'}
        return {**native_capability(check_id), 'scope': 'native'}

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
            required = {'sensor_environment_sha256', 'sensor_target_sha256',
                        'sensor_prepared_sha256', 'sensor_manifest_sha256'}
            if type(values) is not dict or set(values) != required or any(not _sha(v) for v in values.values()):
                raise ValueError('ENGINE_SENSOR_EXACT_POLICY_BINDING_REQUIRED')

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        if self.implementation == 'source-cpu':
            return self._source_cpu(check, env, workdir)
        return self._native(check, env, policy_values, workdir)

    def _source_cpu(self, check, env, workdir):
        from tools import b_req18_slot as slot
        if (set(self.cfg) - {'source_root', 'owned_parent', 'component_root', 'python_binary'}
                or not {'source_root', 'owned_parent', 'component_root'} <= set(self.cfg) or not env.hash_ok()
                or env.engine.name != 'vllm' or env.engine.version != 'source-' + slot.SOURCE_COMMIT
                or env.engine.image_digest is not None
                or any(v is not None for v in (env.model.id, env.model.revision, env.model.inventory_sha256))):
            raise ValueError('ENGINE_SENSOR_SOURCE_CPU_REQUIRES_SEPARATE_ENV')
        # The historical component gate covers the entire original src/pack tree.
        # Execute it in its frozen checkout, never relax it to admit this new file.
        from guard.runtime.bounded import capture_pipes, strict_json
        _verify_frozen_component(self.cfg['component_root'])
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
            _verify_frozen_component(self.cfg['component_root'])
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
            config={'profile': CPU_PROFILE, 'source_commit': slot.SOURCE_COMMIT,
                    'scope': 'source_component', 'installed_target': None, 'gpu_execution_verified': False})
        return self._artifact(draft, workdir, {'safe_facts': result, 'diagnostic_codes': gap})

    def _native(self, check, env, values, workdir):
        from guard.collectors.lifecycle import load_prepared
        from guard.runtime.bounded import strict_json
        from guard.executors.kubernetes_lifecycle_capture import validate_target
        if (set(self.cfg) - {'target', 'prepared', 'manifest', 'max_duration_seconds', 'kubectl_binary'}
                or not {'target', 'prepared', 'manifest'} <= set(self.cfg)
                or not env.hash_ok() or env.engine.name != 'vllm' or env.engine.version != '0.31.0'):
            raise ValueError('ENGINE_SENSOR_NATIVE_CONFIGURATION_INVALID')
        def read(path):
            with Path(path).open('rb') as stream:
                return strict_json(stream.read(262145), limit=262144)
        target, prepared, manifest = read(self.cfg['target']), load_prepared(self.cfg['prepared']), read(self.cfg['manifest'])
        if (values['sensor_environment_sha256'] != env.ref().sha256
                or any(values[key] != sha256_hex(canonical_json(value)) for key, value in (
                    ('sensor_target_sha256', target), ('sensor_prepared_sha256', prepared), ('sensor_manifest_sha256', manifest)))
                or self.expected_target is not None and values['sensor_target_sha256'] != self.expected_target):
            raise ValueError('ENGINE_SENSOR_NATIVE_BINDING_MISMATCH')
        duration = self.cfg.get('max_duration_seconds', 180)
        if type(duration) not in (int, float) or not 0 < duration <= 300:
            raise ValueError('ENGINE_SENSOR_DURATION_INVALID')
        began = utcnow()
        if check.id == 'REQ-18':
            from tools import b_req18_slot as slot
            from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
            validate_target(slot._capture_target(env, target), env)
            transport = KubernetesHTTP(target, env, duration=duration, binary=self.cfg.get('kubectl_binary', 'kubectl'), profile=REQUEST_BINDING_PROFILE)
            slot._validate_manifest(env, target, prepared, manifest, transport)
            result = slot._capture_kubernetes(env, target, prepared, manifest, duration=duration, binary=self.cfg.get('kubectl_binary', 'kubectl'))
            observed = result is not None and result.get('collected') is True
            violation = result.get('mismatch') if observed else None
            # This collector proves a scoped host-mask argument invariant only.
            scope = False
            cleanup = (result or {}).get('cleanup_errors', [])
            normal = (result or {}).get('standalone_unrestricted_source_path_observed')
        elif check.id == 'REQ-20':
            from tools import b_req20_graph as graph
            from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
            validate_target(graph._capture_target(env, target), env)
            transport = KubernetesHTTP(target, env, duration=duration, binary=self.cfg.get('kubectl_binary', 'kubectl'), profile=REQUEST_BINDING_PROFILE)
            graph._bodies(manifest, transport)
            if (manifest.get('schema') != 'byul-req20-cohort/1' or manifest.get('environment_ref') != env.ref().model_dump(mode='json')
                    or manifest.get('target_sha256') != values['sensor_target_sha256'] or manifest.get('session') != prepared['session']
                    or manifest.get('run_id') != target['kubernetes']['run_id']
                    or manifest.get('model_inventory_sha256') != env.model.inventory_sha256
                    or manifest.get('declared_graph_mode') not in graph.MODES - {'NONE'}):
                raise ValueError('ENGINE_SENSOR_GRAPH_MANIFEST_BINDING_MISMATCH')
            if not graph._capability(prepared):
                raise EngineSensorNotImplemented('REVIEWED_GRAPH_NATIVE_CONTRACT_UNAVAILABLE')
            raw = graph._capture_kubernetes(env, target, prepared, manifest, duration=duration, binary=self.cfg.get('kubectl_binary', 'kubectl'))
            result = graph.project(raw, env, target, prepared, manifest)
            # Version changes and incomplete GPU query facts remain candidates.
            observed, violation, scope, normal = False, None, False, None
            cleanup = [] if result['cleanup']['complete'] else ['ENGINE_SENSOR_GRAPH_CLEANUP_UNVERIFIED']
        else:
            raise EngineSensorNotImplemented(native_capability(check.id)['reason'])
        draft = EvidenceDraft(status=ExecStatus.ABORTED,
            data_origin=DataOrigin.EXAMPLE if env.data_origin == DataOrigin.EXAMPLE or target.get('lab') else DataOrigin.DECLARED,
            normal_control={'attempted': result is not None, 'succeeded': normal},
            observations={'engine_sensor_violation': {'value': violation, 'collected': observed},
                          'engine_sensor_full_scope': {'value': scope, 'collected': True}},
            challenges=[{'step': step, 'attempted': False} for step in check.challenge_steps],
            cleanup_errors=cleanup, started_at=began, finished_at=utcnow(),
            abort_reason='ENGINE_SENSOR_NATIVE_SCOPE_INCOMPLETE',
            config={'profile': PROFILES[check.id], 'target_sha256': values['sensor_target_sha256'],
                    'prepared_sha256': values['sensor_prepared_sha256'], 'manifest_sha256': values['sensor_manifest_sha256'],
                    'implementation': 'kubernetes', 'engine_started': False})
        return self._artifact(draft, workdir, {'safe_facts': result, 'required_branches': REQUIRED_BRANCHES[check.id]})

    @staticmethod
    def _artifact(draft, workdir, payload):
        path = Path(workdir) / 'engine-sensor.json'
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(canonical_json(payload))
            draft.artifacts.append(DraftArtifact(name=path.name, path=path, collector='engine_sensor'))
        except Exception:
            return incomplete_artifact_capture(draft)
        return draft
