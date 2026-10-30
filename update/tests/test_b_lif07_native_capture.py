"""native21 B bridge 전용 EXAMPLE 계약. serving·GPU·HTTP·지연 실행은 0회다."""
from copy import deepcopy
from pathlib import Path
import json
from tempfile import TemporaryDirectory
import pytest

from guard.collectors.lifecycle import prepare_assets, load_prepared
from guard.collectors.model_read_scope import build_scope
from guard.models import Environment, canonical_json, sha256_hex, new_id
from tests.test_kubernetes_request_binding import env as example_env, target as example_target
from tools import b_lif07_native_capture as tool

PRIVATE = 'EXAMPLE_PRIVATE_NATIVE_BODY_NOT_PERSISTED'
REPO = Path(__file__).resolve().parents[2]


def make_inputs(tmp_path):
    """공개 준비 API로 native21 asset/scope를 만든다. 모델 파일을 읽지 않는다."""
    doc=example_env().model_dump(mode='json');doc.update(id=new_id('ENV'),content_sha256='')
    files=[{'path':'example.safetensors','sha256':'9'*64,'bytes':100}]
    doc['runtime'].update(gpu_count=1,tp_groups=1)
    doc['model'].update(id='EXAMPLE/model',revision='a'*40,inventory_sha256=sha256_hex(canonical_json(files)))
    env=Environment.model_validate(doc).seal();target=example_target(env)
    target['model_mapping']={'schema':'guard.model-request-mapping/1','environment_ref':env.ref().model_dump(mode='json'),
        'repository_id':env.model.id,'revision':env.model.revision,'served_model_name':'fixture-model',
        'host_snapshot':'/EXAMPLE/model','container_mount':'/models/fixture','inventory_sha256':env.model.inventory_sha256}
    native={'schema':'guard-kubernetes-lifecycle-target/1','environment_ref':env.ref().model_dump(mode='json'),'lab':True,
        'run_id':target['kubernetes']['run_id'],'kubernetes':{k:target['kubernetes'][k] for k in ('context','engine')}}
    manifest_path=tmp_path/'model-manifest.json';manifest_path.write_bytes(canonical_json({'schema':'guard.model-manifest/1','scope':'all','model_id':env.model.id,'files':files,'inventory_sha256':env.model.inventory_sha256}))
    scope=build_scope(env,native,manifest_path,'/models/UNMOUNTED',nonsecret_model_weights=True)
    scope_path=tmp_path/'scope.json';scope_path.write_bytes(canonical_json(scope));prepare_assets(tmp_path/'prepared',REPO,profile=tool.NATIVE_PROFILE,model_read_scope=scope_path)
    prepared=load_prepared(tmp_path/'prepared/manifest.json')
    manifest={'schema':'byul-lif07-cohort/1','environment_ref':env.ref().model_dump(mode='json'),'target_sha256':tool._digest(target),
        'session':prepared['session'],'run_id':native['run_id'],'owner':'EXAMPLE-owner','recovery_owner':'EXAMPLE-owner',
        'request':{'model':'fixture-model','messages':[{'role':'user','content':PRIVATE}],'max_tokens':1,'temperature':0}}
    return env,target,prepared,manifest


class UnavailableReader:
    """실 public capture_session이 unavailable packet을 생성하게 하는 EXAMPLE 전송."""
    def __init__(self,target,env,prepared,**kwargs):
        self.target=target;self.binding={**deepcopy(target['kubernetes']),'run_id':target['run_id']};self.calls=[]
    def snapshot(self):
        self.calls.append('snapshot');e=self.binding['engine']
        return {'namespace':{'kind':'Namespace','metadata':{'name':e['namespace'],'uid':e['namespace_uid']}},
            'pod':{'metadata':{'name':e['pod'],'namespace':e['namespace'],'uid':e['pod_uid'],'labels':{'guard.run':self.binding['run_id']}},
                'spec':{'nodeName':e['node'],'containers':[{'name':e['container'],'image':e['image']}]},
                'status':{'phase':'Running','podIP':e['pod_ip'],'conditions':[{'type':'Ready','status':'False'}],
                    'containerStatuses':[{'name':e['container'],'containerID':e['container_id'],'imageID':e['image_id'],
                        'restartCount':e['restart_count'],'state':{'running':{'startedAt':e['started_at']}},'ready':False}]}}}
    def owner_identity(self,*a):return {'started_at':self.binding['engine']['started_at']}
    def read_file(self,*a,**k):raise ValueError(PRIVATE)
    def image_check(self,*a):return False
    def exec_bounded(self,*a,**k):raise ValueError(PRIVATE)
    def seal_session(self):self.calls.append('seal');return {'sealed':True}
    def report(self):return {'collection_gap':True,'node_runtime_image_verified':False,'producer_authenticated':False}


@pytest.fixture
def inputs(request,monkeypatch):
    temporary=TemporaryDirectory(prefix='byul-lif07-native-example-');request.addfinalizer(temporary.cleanup)
    value=make_inputs(Path(temporary.name).resolve())
    # 원 full-scope gate는 root의 고정 062af9 검증을 재사용한다. 이 fixture는 EXAMPLE 전송만 검증한다.
    monkeypatch.setattr(tool,'verify_source',lambda:{'reviewed_common_commit':tool.REVIEWED_COMMON_COMMIT,'runtime_hash_approval':False,'source_status':'EXAMPLE'})
    return value


def unavailable_callback_packet(prepared):
    """실 B observer 기본값과 승인 literal의 EXAMPLE 외곽 형상. 실제 source 자격은 False다."""
    from guard.adapters import vllm_worker_readiness as callback, vllm_worker_startup_sensor as startup
    from guard.adapters import vllm_worker_startup_metadata as meta, vllm_worker_startup_contract as contract
    from tests.test_lifecycle_sensor import CONTEXT
    runtime='cpython-3.12.3';context=deepcopy(CONTEXT);token='a'*64
    observer=callback.WorkerReadinessObserver()
    binding={'pid':context['pid'],'session_id_sha256':startup.sha(prepared['session'].encode()),'process_start_sha256':startup.sha(startup.canonical(context)),'call_id_sha256':token}
    default=observer.snapshot('Worker.load_model',(),{},phase='CALL',binding=binding);observer.close()
    codes=meta.POINT_CODES[runtime]
    payload={'schema':startup.EVENT_SCHEMA,'point':'Worker.load_model','phase':'CALL','source_module':contract.WORKER,
        'source_files':contract.SOURCE_SHA256,'source_verified':False,'body_code_sha256':codes['worker']['Worker.load_model'],
        'entry_code_sha256':codes['worker']['Worker.load_model'],'callback_source_sha256':startup.CALLBACK_SHA256,
        'callback_helper_source_sha256':startup.SUPPORT_SHA256,'callback_code_sha256':codes['callback_codes'][contract.CALLBACK_MODULE],
        'callback_helper_code_sha256':codes['callback_codes'][contract.SUPPORT_MODULE],'callback_code_verified':True,
        'entry_kind':'direct-body','inner_body_entry_observed':False,'observer_runtime':runtime,'observer_code_sha256':startup.RUNTIME_CODE_CONTRACTS[runtime],
        'call_sha256':token,'parent_call_sha256':None,'call_started_ns':1,'operation_started_ns':2,'operation_finished_ns':3,
        'callback':default,'callback_error':None,'exception_kind':None,**{k:False for k in startup.TRUTH}}
    from guard.adapters import vllm_worker_generation_sensor as generation
    records=[{'seq':0,'monotonic_ns':0,'event':'BEGIN','payload':{'profile':generation.PROFILE,'schema':generation.SCHEMA,'session':prepared['session'],'context':context}},
        {'seq':1,'monotonic_ns':4,'event':'WORKER_STARTUP','payload':payload}]
    return {'recordings':[{'diagnostic_codes':['LIFECYCLE_SESSION_OWNER_CHANGED',PRIVATE],'session_owner_match':False,'listed_file_identity_match':False,'capture':{'process_context_verified':False,'records':records}}],
        'live_census_stable':False,'recorded_files_covered':False,'diagnostic_codes':[]}


def test_actual_public_capture_and_project_default_reaches_bridge_without_truth_upgrade(inputs):
    result=tool.collect_startup(*inputs,_reader_factory=UnavailableReader)
    assert result['data_origin']=='EXAMPLE' and result['requests']==result['pod_changes']==0 and not result['snapshots']
    assert result['native21']['missing'] and result['native_collection']['parser_diagnostic_codes']
    assert result['cleanup']['selected_session_seal_attempted'] and result['cleanup']['selected_session_sealed']
    assert result['cleanup']['final_capture_attempted'] and result['cleanup']['api_epoch_same'] is True
    assert result['native_collection']['node_runtime_image_verified'] is False and result['native_collection']['producer_authenticated'] is False
    assert result['upper_enginecore_ready_send_observed'] is None and result['rank_coverage_verified'] is None
    assert result['native_collection']['recorded_files_covered'] is False and len(result['native_collection']['capture_states'])==2
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('phase',['seal','final_capture','post_snapshot'])
def test_late_failure_preserves_initial_actual_callback_fact_and_independent_cleanup(inputs,phase):
    seen=[];made=[]
    class Reader(UnavailableReader):
        def __init__(self,*a,**k):super().__init__(*a,**k);made.append(self)
        def snapshot(self):
            if phase=='post_snapshot' and self.calls.count('snapshot'):raise ValueError(PRIVATE)
            return super().snapshot()
        def seal_session(self):
            self.calls.append('seal')
            if phase=='seal':raise ValueError(PRIVATE)
            return {'sealed':True}
    def capture(*args,**kwargs):
        seen.append('capture')
        if len(seen)==2 and phase=='final_capture':raise ValueError(PRIVATE)
        return unavailable_callback_packet(inputs[2])
    result=tool.collect_startup(*inputs,_reader_factory=Reader,_capture_session=capture)
    assert len(result['native_collection']['capture_states'])==2 and result['native_collection']['recorded_files_covered'] is False
    assert len(seen)==2 and made[0].calls.count('seal')==1 and result['cleanup']['post_snapshot_attempted']
    assert result['snapshots'] and result['snapshots'][0]['status']=='unavailable' and result['snapshots'][0]['stage']=='initial'
    assert result['snapshots'][0]['native_outer']['source_verified'] is False
    assert 'LIFECYCLE_SESSION_OWNER_CHANGED' in result['native_collection']['parser_diagnostic_codes']
    assert result['native21']['events'] and not result['native21']['actual_worker_verified'] and not result['native21']['gpu_completion_verified']
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('change',['profile19','profile20','session','target','inventory','model','lab','version'])
def test_input_contract_rejects_before_reader_or_target_io(inputs,change):
    env,target,prepared,manifest=deepcopy(inputs)
    if change.startswith('profile'):prepared['profile']='vllm-native-lifecycle-metadata/'+change.removeprefix('profile')
    elif change=='session':manifest['session']='0'*32
    elif change=='target':target['kubernetes']['engine']['pod_uid']='other'
    elif change=='inventory':prepared['model_read_scope']['inventory_sha256']='0'*64
    elif change=='model':target['model_mapping']['repository_id']='other/model'
    elif change=='lab':target['lab']=False
    else:env.engine.version='0.32.0';env=env.seal()
    called=[]
    with pytest.raises(ValueError):tool.collect_startup(env,target,prepared,manifest,_reader_factory=lambda *a,**k:called.append(a))
    assert not called


def test_per_recording_projection_error_does_not_discard_other_recording(inputs):
    packet=unavailable_callback_packet(inputs[2]);broken=deepcopy(packet['recordings'][0]);broken['capture']['records'][1]['payload']['source_files']={}
    packet['recordings'].append(broken)
    projected,rows,closes,generation_closes,processes,gaps=tool._projection(packet,inputs[2],'initial')
    assert len(rows)==1 and len(projected['events'])==1 and len(processes)==2 and 'LIF07_NATIVE_PARTIAL_PROJECTION' in gaps
    assert rows[0]['native_outer']['native_record_seq']==1 and rows[0]['seq'] is None


def test_default_has_no_ready_claim_and_reuses_A_send_semantics(inputs):
    # 실제 READY/other 판정·send/close pairing은 원 A projector/기존49사례를 재사용한다.
    from guard.adapters import vllm_worker_generation_metadata as meta
    value=meta.project(None,inputs[2]);assert not value['ready_sends'] and all(value[k] is False for k in meta.TRUTH)
    assert set(tool.PROJECTION_MISSING_CODES)>={'READY_send_return_is_not_receiver_or_service_admission'}


def test_reader_construction_failure_is_safe_and_does_not_claim_cleanup(inputs):
    def factory(*a,**k):raise OSError(PRIVATE)
    value=tool.collect_startup(*inputs,_reader_factory=factory)
    assert not value['cleanup']['selected_session_seal_attempted'] and not value['cleanup']['final_capture_attempted']
    assert value['cleanup']['owned_host_complete'] is None and value['gaps'] and PRIVATE not in json.dumps(value)
