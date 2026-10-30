"""LIF07 native21 관측 bridge. HTTP·Pod 변경·지연 없이 선정 session을 수집·seal한다."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import math
from pathlib import Path
import re

from guard.models import canonical_json, sha256_hex

SCHEMA = 'byul-lif07-worker-readiness-capture/1'
PROFILE = 'owned-worker-phase-readiness/1'
NATIVE_PROFILE = 'vllm-native-lifecycle-metadata/21'
APPROVED_SOURCE_COMMIT = 'b88b4a6491a9a0c2908b962b837247c0cea80b55'
REVIEWED_COMMON_COMMIT = '062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89'
GATE_SOURCE_COMMIT = 'c39a36b7a6f306b910f0314b0deee10b55390dc4'
GATE_SHA256 = '5ac8204da0bc92858ab64591078d4ddb19aa2ed10a25f5437031611c1620282e'
CALLBACK_SHA256 = 'a583d32f31d74def618256557ee9e7aee801d5fe1ff55e27b6221d7f2420a965'
GAPS = frozenset({'LIF07_NATIVE_READER_UNAVAILABLE','LIF07_NATIVE_PRE_SNAPSHOT_UNAVAILABLE',
 'LIF07_NATIVE_POST_SNAPSHOT_UNAVAILABLE','LIF07_NATIVE_API_EPOCH_CHANGED','LIF07_NATIVE_INITIAL_CAPTURE_UNAVAILABLE',
 'LIF07_NATIVE_FINAL_CAPTURE_UNAVAILABLE','LIF07_NATIVE_SESSION_SEAL_UNAVAILABLE','LIF07_NATIVE_PROJECTION_UNAVAILABLE',
 'LIF07_NATIVE_PARTIAL_PROJECTION','LIF07_NATIVE_OBSERVER_CLOSE_UNVERIFIED','LIF07_NATIVE_PARENT_CLEANUP_UNVERIFIED',
 'LIF07_NATIVE_CENSUS_OR_COVERAGE_UNVERIFIED','LIF07_NATIVE_PARSER_ADDITIONAL_GAP','LIF07_NATIVE_SNAPSHOT_BOUND'})
PARSER_DIAGNOSTIC_CODES = frozenset({'LIFECYCLE_INITIAL_INVENTORY_UNAVAILABLE','LIFECYCLE_FINAL_INVENTORY_UNAVAILABLE',
 'LIFECYCLE_PROCESS_RECORDING_UNAVAILABLE','LIFECYCLE_SESSION_OWNER_CHANGED','LIFECYCLE_LISTED_FILE_CHANGED',
 'LIFECYCLE_SESSION_CENSUS_UNVERIFIED'})
PROJECTION_MISSING_CODES = frozenset({'worker_generation_observation_unavailable','worker_generation_object_link_unavailable',
 'worker_selected_ready_send_unavailable','worker_generation_observer_close_unavailable_or_ambiguous',
 'worker_generation_unique_call_pair_unavailable','worker_ready_pipe_unique_call_pair_unavailable',
 'worker_main_terminal_not_observed','worker_startup_callback_unavailable','worker_startup_callback_metadata_unavailable',
 'worker_startup_unique_call_pair_unavailable','worker_startup_observer_call_bound','worker_startup_observer_close_unavailable',
 'worker_startup_observer_close_ambiguous','worker_startup_observer_close_incomplete','worker_startup_root_call_pair_unavailable',
 'worker_startup_method_observation_unavailable','worker_startup_model_root_link_unavailable',
 'actual_worker_and_parent_cohort_generation_unverified','READY_send_return_is_not_receiver_or_service_admission',
 'failure_repair_lineage_and_full_preparation_unverified','inner_method_body_entry_and_GPU_completion_unverified','actual_worker_assignment_and_READY_send_unverified','approval_and_inspection_authority_unverified','LIF07_NATIVE_ADDITIONAL_PROJECTION_GAP',
 'worker_generation_calls-bound','worker_generation_callable-changed','worker_generation_selected-pipe-unavailable','worker_generation_pipe-code-unavailable','worker_generation_other-thread','worker_generation_worker-object-unbound','worker_generation_worker-object-unqualified','worker_generation_worker-object-bound','worker_generation_constructor-worker-ambiguous','worker_generation_wrapper-outside-constructor'})


def _digest(value): return sha256_hex(canonical_json(value))
def _sha(value): return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def verify_source():
    """기존 full-scope ancestor/index/archive/shadow 검증만 호출한다."""
    from tools import verify_req10_consumption as gate
    root = Path(__file__).resolve().parents[2]
    path = root / 'update/tools/verify_req10_consumption.py'
    callback = root / 'update/src/guard/adapters/vllm_worker_readiness.py'
    if (path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != GATE_SHA256
            or Path(gate.__file__).resolve() != path or Path(inspect.getsourcefile(gate.verify)).resolve() != path
            or callback.is_symlink() or hashlib.sha256(callback.read_bytes()).hexdigest() != CALLBACK_SHA256
            or gate.REVIEWED_COMMON_COMMIT != REVIEWED_COMMON_COMMIT):
        raise ValueError('LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE')
    return gate.verify(root)


def _inputs(env, target, prepared, manifest, duration, binary):
    from guard.collectors.model_read_scope import validate_binding
    from guard.collectors.model_mapping import validate_model_mapping
    from guard.adapters.vllm_lifecycle_profiles import source_hashes
    from guard.executors.kubernetes_lifecycle_capture import validate_target
    if any(type(v) is not dict for v in (target,prepared,manifest)):
        raise ValueError('LIF07_NATIVE_TYPED_INPUTS_REQUIRED')
    if (not env.hash_ok() or env.engine.name != 'vllm' or env.engine.version != '0.31.0'
            or target.get('lab') is not True or target.get('environment_ref') != env.ref().model_dump(mode='json')
            or prepared.get('profile') != NATIVE_PROFILE or prepared.get('public_commit') != 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
            or prepared.get('source_sha256') != source_hashes(NATIVE_PROFILE)
            or type(prepared.get('session')) is not str or re.fullmatch('[0-9a-f]{32}', prepared['session']) is None
            or type(duration) not in (int,float) or not math.isfinite(duration) or not 0 < duration <= 300
            or type(binary) is not str or not binary.strip()):
        raise ValueError('LIF07_NATIVE21_EXACT_PREPARED_ENV_REQUIRED')
    native = {'schema':'guard-kubernetes-lifecycle-target/1','environment_ref':env.ref().model_dump(mode='json'),
        'lab':target['lab'],'run_id':target['kubernetes']['run_id'],
        'kubernetes':{k:deepcopy(target['kubernetes'][k]) for k in ('context','engine')}}
    validate_target(native,env); validate_binding(prepared,env,native); validate_model_mapping(env,target.get('model_mapping'))
    if (manifest.get('schema') != 'byul-lif07-cohort/1' or manifest.get('environment_ref') != env.ref().model_dump(mode='json')
            or manifest.get('target_sha256') != _digest(target) or manifest.get('session') != prepared['session']
            or manifest.get('run_id') != native['run_id']
            or any(type(manifest.get(k)) is not str or not manifest[k].strip() for k in ('owner','recovery_owner'))):
        raise ValueError('LIF07_NATIVE_EXACT_MANIFEST_BINDING_REQUIRED')
    return native


def _epoch(reader):
    from guard.runtime.kubernetes_binding import verify_owned_pod
    value = reader.snapshot()
    owner = verify_owned_pod(value['pod'],value['namespace']['metadata'],reader.binding['engine'],reader.binding['run_id'],require_ready=False)
    return _digest(owner)


def _projection(packet, prepared, stage):
    """원 A projector를 재사용한다. 다른 process recording의 실패로 앞선 facts를 지우지 않는다."""
    from guard.adapters import vllm_worker_generation_metadata as meta
    from guard.adapters import vllm_worker_generation_sensor as sensor
    from guard.adapters import vllm_worker_startup_metadata as startup
    result = meta.project(None,prepared)
    result['missing'] = []
    snapshots, closes, generation_closes, recordings, gaps = [], [], [], [], set()
    allowed_missing = PROJECTION_MISSING_CODES | {'worker_generation_'+v for v in sensor.GAPS}
    for index, recording in enumerate((packet or {}).get('recordings',[])):
        capture = recording.get('capture') or {}; rows = capture.get('records',[])
        context = rows[0]['payload']['context'] if rows else {}
        process = {'stage':stage,'recording':index,'pid':context.get('pid'),'start_ticks':context.get('start_ticks'),
            'process_context_verified':capture.get('process_context_verified') is True,
            'session_owner_match':recording.get('session_owner_match') is True,
            'listed_file_identity_match':recording.get('listed_file_identity_match') is True}
        # 실제 경로의 packet은 공개 capture_session/parser가 생성한다.
        recordings.append(process)
        try: projected = meta.project({'recordings':[recording]},prepared)
        except (ValueError,KeyError,TypeError,OSError,RuntimeError):
            gaps.add('LIF07_NATIVE_PARTIAL_PROJECTION'); continue
        for key in ('events','model_candidates','generation_events','generation_candidates','ready_sends'):
            for row in projected[key]:
                item = deepcopy(row); item.update(stage=stage,recording=index); result[key].append(item)
        for value in projected['missing']:
            result['missing'].append(value if value in allowed_missing else 'LIF07_NATIVE_ADDITIONAL_PROJECTION_GAP')
        for event in projected['events']:
            callback = event.get('callback')
            if type(callback) is not dict: continue
            item = deepcopy(callback)
            item['stage'] = stage
            item['native_outer'] = {'stage':stage,'recording':index,'native_record_seq':event['event_seq'],
                'pid':event['pid'],'start_ticks':event['start_ticks'],
                'source_sha256':event['source_files'].get(event['source_module']),
                'code_sha256':event['body_code_sha256'],'entry_code_sha256':event['entry_code_sha256'],
                'observer_runtime':event['observer_runtime'],'source_verified':event['source_verified'],
                'callback_code_verified':event['callback_code_verified'],'callback_error':event['callback_error'],
                'inner_body_entry_observed':event['inner_body_entry_observed'],'entry_kind':event['entry_kind'],
                'process_binding_verified':event['process_binding_verified'],
                'process_domain':{k:context.get(k) for k in ('pid','start_ticks','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256')},
                'source_contract_verified':bool(event['source_verified'] and event['callback_code_verified']),
                'actual_worker_verified':False,'gpu_completion_verified':False}
            snapshots.append(item)
        for row in rows:
            payload = row['payload']
            if row['event']=='WORKER_STARTUP_CLOSE':
                startup.validate_close(payload)
                closes.append({**process,'event_seq':row['seq'],'result':deepcopy(payload['result'])})
            elif row['event']=='WORKER_GENERATION_CLOSE':
                meta.validate_close(payload)
                generation_closes.append({**process,'event_seq':row['seq'],**{k:payload[k] for k in ('local_observer_closed','cleared_local_worker_bindings')}})
    if not result['events'] and not result['generation_events']:
        default = meta.project(None,prepared); result['missing'] += default['missing']
    result['missing'] = sorted(set(result['missing']))
    return result,snapshots,closes,generation_closes,recordings,gaps


def collect_startup(env,target,prepared,manifest,*,duration=180,binary='kubectl',_runner=None,_reader_factory=None,_capture_session=None):
    """실제 요청/Pod/지연 0. selected session seal은 별도의 bounded control I/O다."""
    native = _inputs(env,target,prepared,manifest,duration,binary)
    proof = verify_source()
    from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
    from guard.executors.lifecycle_session import capture_session
    from guard.adapters import vllm_worker_generation_metadata as meta
    reader_factory = _reader_factory or KubernetesLifecycleReader
    capture = _capture_session or capture_session
    injected = any(v is not None for v in (_runner,_reader_factory,_capture_session))
    now = datetime.now(timezone.utc).isoformat()
    result = {'schema':SCHEMA,'profile':PROFILE,'environment_ref':env.ref().model_dump(mode='json'),
        'target_sha256':_digest(target),'session':prepared['session'],'run_id':manifest['run_id'],
        'started_at':now,'finished_at':now,'data_origin':'EXAMPLE' if injected or env.data_origin.value=='EXAMPLE' else 'DECLARED',
        'snapshots':[],'native21':meta.project(None,prepared),'observer_closes':[],'generation_closes':[],
        'native_collection':{'initial_capture_attempted':False,'final_capture_attempted':False,'process_recordings':[],
            'live_census_stable':None,'recorded_files_covered':None,'parser_diagnostic_codes':[],
            'reader_collection_gap':None,'snapshot_omitted_count':0,'node_runtime_image_verified':False,'producer_authenticated':False,'record_authenticity_verified':False},
        'cleanup':{'pre_snapshot_attempted':False,'pre_snapshot_verified':False,'post_snapshot_attempted':False,
            'post_snapshot_verified':False,'api_epoch_same':None,'selected_session_seal_attempted':False,'selected_session_sealed':None,
            'final_capture_attempted':False,'final_capture_complete':None,'worker_observer_closed':None,'owned_host_complete':None,'gpu_model_recovery_verified':None},
        'gaps':[],'requests':0,'pod_changes':0,'controlled_delay_activated':None,'upper_enginecore_ready_send_observed':None,
        'ready_receiver_verified':None,'service_gate_verified':None,'expected_ranks':None,'rank_coverage_verified':None,
        'source_validation':{'reviewed_common_commit':proof['reviewed_common_commit'],'approved_native_source_commit':APPROVED_SOURCE_COMMIT,
            'runtime_hash_approval':False,'producer_authenticated':False}}
    result['native21']['missing']=[]
    reader = None; epochs = {}; gaps = set(); packet_values = []
    def take(stage):
        result['native_collection'][stage+'_capture_attempted'] = True
        if stage=='final': result['cleanup']['final_capture_attempted'] = True
        engine=reader.binding['engine']
        packet=capture(reader,native,container=engine['container'],image=engine['image'],prepared=prepared,
            owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
        if type(packet) is not dict:
            raise ValueError('LIF07_NATIVE_PACKET_UNAVAILABLE')
        packet_values.append((stage,packet))
    try:
        reader=reader_factory(native,env,prepared,binary=binary,duration=duration,runner=_runner)
        result['cleanup']['pre_snapshot_attempted']=True
        try: epochs['initial']=_epoch(reader); result['cleanup']['pre_snapshot_verified']=True
        except Exception: gaps.add('LIF07_NATIVE_PRE_SNAPSHOT_UNAVAILABLE')
        try: take('initial')
        except Exception: gaps.add('LIF07_NATIVE_INITIAL_CAPTURE_UNAVAILABLE')
    except Exception: gaps.add('LIF07_NATIVE_READER_UNAVAILABLE')
    finally:
        if reader is not None:
            result['cleanup']['selected_session_seal_attempted']=True
            try: result['cleanup']['selected_session_sealed']=reader.seal_session().get('sealed') is True
            except Exception: result['cleanup']['selected_session_sealed']=False
            if not result['cleanup']['selected_session_sealed']:gaps.add('LIF07_NATIVE_SESSION_SEAL_UNAVAILABLE')
            try: take('final')
            except Exception: gaps.add('LIF07_NATIVE_FINAL_CAPTURE_UNAVAILABLE')
            result['cleanup']['post_snapshot_attempted']=True
            try: epochs['final']=_epoch(reader); result['cleanup']['post_snapshot_verified']=True
            except Exception: gaps.add('LIF07_NATIVE_POST_SNAPSHOT_UNAVAILABLE')
    for stage,packet in packet_values:
        try:
            projected,snapshots,closes,generation_closes,recordings,partial=_projection(packet,prepared,stage)
            for key in ('events','model_candidates','generation_events','generation_candidates','ready_sends'):result['native21'][key].extend(projected[key])
            result['native21']['missing']=sorted(set(result['native21']['missing']+projected['missing']))
            result['snapshots']+=snapshots;result['observer_closes']+=closes;result['generation_closes']+=generation_closes
            result['native_collection']['process_recordings']+=recordings;gaps.update(partial)
        except Exception:gaps.add('LIF07_NATIVE_PROJECTION_UNAVAILABLE')
        for code in packet.get('diagnostic_codes',[]):
            if code in PARSER_DIAGNOSTIC_CODES:result['native_collection']['parser_diagnostic_codes'].append(code)
            else:gaps.add('LIF07_NATIVE_PARSER_ADDITIONAL_GAP')
        result['native_collection']['live_census_stable']=packet.get('live_census_stable') is True
        result['native_collection']['recorded_files_covered']=packet.get('recorded_files_covered') is True
        if stage=='final':result['cleanup']['final_capture_complete']=packet.get('recorded_files_covered') is True
        if not packet.get('live_census_stable') or not packet.get('recorded_files_covered'):gaps.add('LIF07_NATIVE_CENSUS_OR_COVERAGE_UNVERIFIED')
    if not packet_values:result['native21']=meta.project(None,prepared)
    if len(result['snapshots'])>512:
        result['native_collection']['snapshot_omitted_count']=len(result['snapshots'])-512
        result['snapshots']=result['snapshots'][:512];gaps.add('LIF07_NATIVE_SNAPSHOT_BOUND')
    if 'initial' in epochs and 'final' in epochs:
        result['cleanup']['api_epoch_same']=epochs['initial']==epochs['final']
        if not result['cleanup']['api_epoch_same']:gaps.add('LIF07_NATIVE_API_EPOCH_CHANGED')
    if result['observer_closes']:
        result['cleanup']['worker_observer_closed']=all(r['result']['complete'] and r['result']['pending_calls']==0 and not r['result']['collection_gap'] for r in result['observer_closes'])
    if result['cleanup']['worker_observer_closed'] is not True:gaps.add('LIF07_NATIVE_OBSERVER_CLOSE_UNVERIFIED')
    if reader is not None:
        try:
            report=reader.report();result['native_collection']['reader_collection_gap']=report['collection_gap']
            result['cleanup']['owned_host_complete']=report.get('parent_cleanup_unverified') is not True
            if not result['cleanup']['owned_host_complete']:gaps.add('LIF07_NATIVE_PARENT_CLEANUP_UNVERIFIED')
        except Exception:gaps.add('LIF07_NATIVE_PARENT_CLEANUP_UNVERIFIED')
    result['native_collection']['parser_diagnostic_codes']=sorted(set(result['native_collection']['parser_diagnostic_codes']))
    result['gaps']=sorted(gaps);result['finished_at']=datetime.now(timezone.utc).isoformat()
    return result
