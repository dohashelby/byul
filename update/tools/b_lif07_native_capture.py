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
# 원 public session 32 files × native21 2048 rows × initial/final 관측, 정책 수치가 아니다.
MAX_PROCESS_RECORDINGS = 2 * 32
MAX_NATIVE21_ROWS = MAX_PROCESS_RECORDINGS * 2048
MAX_CLOSE_ROWS = MAX_NATIVE21_ROWS
MAX_CALLBACK_SNAPSHOTS = 512
APPROVED_SOURCE_COMMIT = 'b88b4a6491a9a0c2908b962b837247c0cea80b55'
REVIEWED_COMMON_COMMIT = '062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89'
GATE_SOURCE_COMMIT = 'c39a36b7a6f306b910f0314b0deee10b55390dc4'
GATE_SHA256 = '5ac8204da0bc92858ab64591078d4ddb19aa2ed10a25f5437031611c1620282e'
# A158 검토 Gitblob + 원 stream wrapper를 오프라인 계산한 승인 literal이다.
HTTP_REVIEWED_COMMON_COMMIT = 'f277fecd6df1e59505c0ad731032593f6fb22885'
# PR171 검토 full gate 원본은 역사 A152 gate와 별도로 고정한다.
PR171_GATE_SOURCE_COMMIT = 'dd5077ce4989853c369ffce9fdcb7795d9801ec0'
PR171_GATE_SHA256 = '00979cf6b5139e0337d1f7976acad2b816e1018c4422cf8a44a087b0c28fc233'
PR171_REVIEWED_COMMON_COMMIT = 'df6255e96a877ebfbe185230ed87320aa84d2c30'
# B가 검토한 A native20~22 통합394. 기존 공개392·네 정확 조합은 별도 유지한다.
NATIVE20_GATE_SOURCE_COMMIT = 'd8904d0ee9d666d9d0070dbf2003b15f4e0b14bd'
NATIVE20_GATE_SHA256 = '61c29e98ece3c27c3fb978c180b86c6ef1a2ba6bf60edcf9263a2fc3943acab7'
NATIVE20_REVIEWED_COMMON_COMMIT = 'adcde856375a4de43d4a5d789c00de875e43e321'
HTTP_APPROVED_REVIEWED_COMMON_COMMITS = frozenset({HTTP_REVIEWED_COMMON_COMMIT, PR171_REVIEWED_COMMON_COMMIT, NATIVE20_REVIEWED_COMMON_COMMIT})
HTTP_EXPECTED_SOURCES = {
    'collector':'ab933ca4a869272d37143643f2bec0519c17e52af1a33814ae86f92338d312a6',
    'binding':'a63c81627e579129c64f2831f56ddc7cb1dfbb9b27cd98110541c10e21050db7',
    'probe':'96df266f30e713e43f13cc1a042896894cd061c5af7b7ffd286e8a13ae91e23d',
}
# gate bytes와 검토 common은 독립 허용 목록이 아니라 승인된 정확한 조합이다.
APPROVED_GATE_PROFILES = frozenset({
    (GATE_SHA256, REVIEWED_COMMON_COMMIT),
    ('67b917029c6458fc16cc53be787134d652fdb096673721a486febe4d68a0c0b6',
     '9d8987a5a306ffce28fcb735e012c400589303f2'),
    ('15701b1e83eab8665414456b3072bcad73ee2220ed02e9f63a06a3441b55d157',
     HTTP_REVIEWED_COMMON_COMMIT),
    (PR171_GATE_SHA256, PR171_REVIEWED_COMMON_COMMIT),
    (NATIVE20_GATE_SHA256, NATIVE20_REVIEWED_COMMON_COMMIT),
})
APPROVED_REVIEWED_COMMON_COMMITS = frozenset(pair[1] for pair in APPROVED_GATE_PROFILES)
CALLBACK_SHA256 = 'a583d32f31d74def618256557ee9e7aee801d5fe1ff55e27b6221d7f2420a965'
GAPS = frozenset({'LIF07_NATIVE_READER_UNAVAILABLE','LIF07_NATIVE_PRE_SNAPSHOT_UNAVAILABLE',
 'LIF07_NATIVE_POST_SNAPSHOT_UNAVAILABLE','LIF07_NATIVE_API_EPOCH_CHANGED','LIF07_NATIVE_INITIAL_CAPTURE_UNAVAILABLE',
 'LIF07_NATIVE_FINAL_CAPTURE_UNAVAILABLE','LIF07_NATIVE_SESSION_SEAL_UNAVAILABLE','LIF07_NATIVE_PROJECTION_UNAVAILABLE',
 'LIF07_NATIVE_PARTIAL_PROJECTION','LIF07_NATIVE_OBSERVER_CLOSE_UNVERIFIED','LIF07_NATIVE_PARENT_CLEANUP_UNVERIFIED',
 'LIF07_NATIVE_CENSUS_OR_COVERAGE_UNVERIFIED','LIF07_NATIVE_PARSER_ADDITIONAL_GAP','LIF07_NATIVE_SNAPSHOT_BOUND'})
PARSER_DIAGNOSTIC_CODES = frozenset({'LIFECYCLE_INITIAL_INVENTORY_UNAVAILABLE','LIFECYCLE_FINAL_INVENTORY_UNAVAILABLE',
 'LIFECYCLE_PROCESS_RECORDING_UNAVAILABLE','LIFECYCLE_SESSION_OWNER_CHANGED','LIFECYCLE_LISTED_FILE_CHANGED',
 'LIFECYCLE_SESSION_CENSUS_UNVERIFIED','LIFECYCLE_IMAGE_RECHECK_UNAVAILABLE',
 'LIFECYCLE_RECHECK_UNAVAILABLE','LIFECYCLE_IMAGE_BINDING_UNVERIFIED','LIFECYCLE_PROCESS_BINDING_UNVERIFIED',
 'LIFECYCLE_INITIAL_OWNER_RECHECK_UNAVAILABLE'})
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
    gate_sha256 = None if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
    approved_pair = (gate_sha256, gate.REVIEWED_COMMON_COMMIT) in APPROVED_GATE_PROFILES
    if (path.is_symlink() or not approved_pair
            or Path(gate.__file__).resolve() != path or Path(inspect.getsourcefile(gate.verify)).resolve() != path
            or callback.is_symlink() or hashlib.sha256(callback.read_bytes()).hexdigest() != CALLBACK_SHA256):
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
            or env.runtime.gpu_count != 1 or env.runtime.tp_groups != 1
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
    snapshots, closes, generation_closes, recordings, gaps, validated = [], [], [], [], set(), set()
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
            gaps.add('LIF07_NATIVE_PARTIAL_PROJECTION'); projected = None
        if projected is not None:
            key = _recording_key(process)
            if key is not None: validated.add(key)
        for key in ('events','model_candidates','generation_events','generation_candidates','ready_sends'):
            for row in (projected[key] if projected is not None else []):
                item = deepcopy(row); item.update(stage=stage,recording=index); result[key].append(item)
        for value in (projected['missing'] if projected is not None else []):
            result['missing'].append(value if value in allowed_missing else 'LIF07_NATIVE_ADDITIONAL_PROJECTION_GAP')
        for event in (projected['events'] if projected is not None else []):
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
            try:
                if row['event']=='WORKER_STARTUP_CLOSE':
                    startup.validate_close(payload)
                    closes.append({**process,'event_seq':row['seq'],'result':deepcopy(payload['result'])})
                elif row['event']=='WORKER_GENERATION_CLOSE':
                    meta.validate_close(payload)
                    generation_closes.append({**process,'event_seq':row['seq'],**{k:payload[k] for k in ('local_observer_closed','cleared_local_worker_bindings')}})
            except (ValueError,KeyError,TypeError):
                validated.discard(_recording_key(process));gaps.add('LIF07_NATIVE_PARTIAL_PROJECTION')
    if not result['events'] and not result['generation_events']:
        default = meta.project(None,prepared); result['missing'] += default['missing']
    result['missing'] = sorted(set(result['missing']))
    return result,snapshots,closes,generation_closes,recordings,gaps,validated


def _recording_key(row):
    values = tuple(row.get(k) for k in ('recording','pid','start_ticks'))
    if any(type(v) is not int or not 0 <= v < 2**63 for v in values) or values[1] == 0 or values[2] == 0:
        return None
    return values


def _observer_closed(result, validated_final):
    """초기 재관측은 유지하되 최종 선정 recording의 정확한 close census만 집계한다."""
    states=[v for v in result['native_collection']['capture_states'] if v['stage']=='final']
    if (len(states)!=1 or not states[0]['capture_succeeded'] or states[0]['recorded_files_covered'] is not True
            or states[0]['live_census_stable'] is not True):return None
    selected=[r for r in result['native_collection']['process_recordings'] if r['stage']=='final']
    keys=[_recording_key(r) for r in selected]
    if (not selected or None in keys or len(set(keys))!=len(keys)
            or any(k not in validated_final for k in keys)
            or any(not all(r[k] is True for k in ('process_context_verified','session_owner_match','listed_file_identity_match')) for r in selected)):
        return None
    groups={k:[] for k in keys}
    for row in result['observer_closes']:
        if row['stage']!='final':continue
        key=_recording_key(row)
        if key not in groups:return None
        groups[key].append(row)
    for rows in groups.values():
        if len(rows)!=1:return False
        value=rows[0]['result']
        if value['complete'] is not True or value['pending_calls']!=0 or value['collection_gap'] is not False:return False
    return True


def _diagnostics(packet):
    values=[packet.get('diagnostic_codes',[])]
    for row in packet.get('recordings',[]):
        values.append(row.get('diagnostic_codes',[]))
        capture=row.get('capture')
        if type(capture) is dict:values.append(capture.get('diagnostic_codes',[]))
    codes=set();unknown=False
    for value in values:
        if type(value) is not list:unknown=True;continue
        for code in value:
            if type(code) is str and code in PARSER_DIAGNOSTIC_CODES:codes.add(code)
            else:unknown=True
    return codes,unknown


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
        'native_collection':{'initial_capture_attempted':False,'final_capture_attempted':False,'process_recordings':[],'capture_states':[],
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
    reader = None; epochs = {}; gaps = set(); packet_values = []; validated_final=set()
    def take(stage):
        result['native_collection'][stage+'_capture_attempted'] = True
        if stage=='final': result['cleanup']['final_capture_attempted'] = True
        state={'stage':stage,'capture_succeeded':False,'live_census_stable':None,'recorded_files_covered':None}
        result['native_collection']['capture_states'].append(state)
        engine=reader.binding['engine']
        packet=capture(reader,native,container=engine['container'],image=engine['image'],prepared=prepared,
            owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
        if type(packet) is not dict:
            raise ValueError('LIF07_NATIVE_PACKET_UNAVAILABLE')
        state.update(capture_succeeded=True,live_census_stable=packet.get('live_census_stable') is True,recorded_files_covered=packet.get('recorded_files_covered') is True)
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
            projected,snapshots,closes,generation_closes,recordings,partial,validated=_projection(packet,prepared,stage)
            for key in ('events','model_candidates','generation_events','generation_candidates','ready_sends'):result['native21'][key].extend(projected[key])
            result['native21']['missing']=sorted(set(result['native21']['missing']+projected['missing']))
            result['snapshots']+=snapshots;result['observer_closes']+=closes;result['generation_closes']+=generation_closes
            result['native_collection']['process_recordings']+=recordings;gaps.update(partial)
            if stage=='final':validated_final.update(validated)
        except Exception:gaps.add('LIF07_NATIVE_PROJECTION_UNAVAILABLE')
        codes,unknown=_diagnostics(packet)
        result['native_collection']['parser_diagnostic_codes'].extend(codes)
        if unknown:gaps.add('LIF07_NATIVE_PARSER_ADDITIONAL_GAP')
        if stage=='final':result['cleanup']['final_capture_complete']=packet.get('recorded_files_covered') is True
        if not packet.get('live_census_stable') or not packet.get('recorded_files_covered'):gaps.add('LIF07_NATIVE_CENSUS_OR_COVERAGE_UNVERIFIED')
    states=result['native_collection']['capture_states']
    if states:
        for key in ('live_census_stable','recorded_files_covered'):
            result['native_collection'][key]=all(v['capture_succeeded'] and v[key] is True for v in states)
    if not packet_values:result['native21']=meta.project(None,prepared)
    if len(result['snapshots'])>MAX_CALLBACK_SNAPSHOTS:
        result['native_collection']['snapshot_omitted_count']=len(result['snapshots'])-MAX_CALLBACK_SNAPSHOTS
        result['snapshots']=result['snapshots'][:MAX_CALLBACK_SNAPSHOTS];gaps.add('LIF07_NATIVE_SNAPSHOT_BOUND')
    if 'initial' in epochs and 'final' in epochs:
        result['cleanup']['api_epoch_same']=epochs['initial']==epochs['final']
        if not result['cleanup']['api_epoch_same']:gaps.add('LIF07_NATIVE_API_EPOCH_CHANGED')
    result['cleanup']['worker_observer_closed']=_observer_closed(result,validated_final)
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
