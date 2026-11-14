"""원 소스·해시 사슬의 EXAMPLE 자료만 사용한다. GPU·신호 주입은 하지 않는다."""
from copy import deepcopy
from pathlib import Path

import pytest

from guard.adapters import vllm_rank_fault_input as adapter
from guard.collectors import gpu_common_inputs as common, rank_fault_producer as producer
from tests import test_fault_projection24 as fixture
from tests.test_gpu_common_inputs import ENV


def bundle(kind='exception', *, release=False, recovery=False):
    packet, client, receipt, scope, deadlines = fixture.case(kind)
    if not release and not recovery:
        return packet, client, receipt, scope, deadlines, None
    captures = []
    recovery_client = None
    for recording in packet['recordings']:
        events = [(r['event'], deepcopy(r['payload']), r['monotonic_ns']-fixture.B)
            for r in recording['capture']['records'] if r['event'] not in {'START','MODULE','END'}
            and not (r['event']=='RANK_FAILURE' and r['payload']['phase']=='CALL')]
        # 원래부터 닫히지 않은 worker RPC CALL은 보존한다.
        if recording['pid']==33:
            events.insert(0, ('RANK_FAILURE', fixture.rank_row('WorkerProc._execute_worker_rpc','CALL',kind=kind,stamp=150),150))
            if release:
                row=fixture.control_row(kind,'RELEASE');row['action_receipt_sha256']='9'*64
                events.append(('FAULT_CONTROL',row,600))
        if recovery:
            if recording['pid']==31:
                selection=fixture.selection();selection['metadata']['request_selection'].update(
                    asgi_request_sha256='a'*64, selected_request_id_sha256='b'*64,asgi_invocation_sha256='c'*64)
                events.append(('SELECTION',selection,910))
                terminal=fixture.terminal();terminal.update(phase='ASGI_APP_RETURN',asgi_request_sha256='a'*64,
                    asgi_invocation_sha256='c'*64,operation_started_ns=fixture.B+905,operation_finished_ns=fixture.B+980)
                events.append(('TERMINATION',terminal,980))
            points = ['WorkerProc._execute_worker_rpc'] if recording['pid']==33 else (
                ['MultiprocExecutor.execute_model','FutureWrapper.result'] if recording['pid']==32 else [])
            for i,point in enumerate(points):
                value=fixture.rank_row(point,'RETURN',stamp=930+i*10)
                value['native_request_sha256']=['b'*64]
                events.append(('RANK_FAILURE',value,930+i*10))
            recovery_client={'clock_context':dict(fixture.CLOCK),'outcome':200,'complete':True,
                'request_started_ns':fixture.B+905,'response_finished_ns':fixture.B+990,
                'external_request_sha256':'a'*64}
        captures.append(fixture.recording(recording['pid'],events,True))
    packet['recordings']=captures
    return packet,client,receipt,scope,deadlines,recovery_client


def make(args):
    packet,client,receipt,scope,deadlines,recovery=args
    pod={**fixture.OWNER,'restart_count':0}
    return producer.record(packet,client,receipt,scope,deadlines,pod=pod,environment_ref=ENV,
        target_sha256=fixture.TARGET,prepared_sha256='d'*64,session=fixture.SESSION,recovery_client=recovery)


@pytest.mark.parametrize('kind', sorted(fixture.control.KINDS))
def test_actual_source_faults_project_to_unregistered_facts_without_inventing_recovery(kind):
    value, result, projection = make(bundle(kind))
    assert any(e['event']=='fault_applied' and e['ack'] for e in value['events'])
    assert any(e['event']=='detected' for e in value['events'])
    assert any(e['event']=='client_delivered' for e in value['events'])
    assert not any(e['event'] in {'recovered','fault_released'} for e in value['events'])
    assert not result['supported'] and not result['complete'] and not result['producer_authenticated']
    assert 'RANK_FAULT_RECOVERY_UNOBSERVED' in projection['diagnostic_codes']
    assert ('guard-engine-callback-vllm-rank-fault','1') not in common.SUPPORTED_PRODUCERS
    # 탐지/클라이언트 전달의 관측 프로세스와 장애 대상 worker를 혼동하지 않는다.
    assert all(b['worker_pid']==33 for b in value['bindings'])
    assert any(e['observer_context']['pid']!=33 for e in projection['events'])


@pytest.mark.parametrize('kind', ['exception','communication-stall'])
def test_source_release_and_distinct_successful_request_form_all_five_events(kind, monkeypatch):
    value,result,projection=make(bundle(kind,release=True,recovery=True))
    assert {e['event'] for e in value['events']}=={'fault_applied','fault_released','detected','client_delivered','recovered'}
    assert projection['scope_complete'] and not projection['diagnostic_codes']
    assert not result['supported'] and not result['complete']
    source=value['producer']
    # 같은 사례에서만 등록 후 계약 분기를 모의한다. 제품 등록표는 복원된다.
    monkeypatch.setitem(common.SUPPORTED_PRODUCERS,(source['name'],source['version']),source['source_sha256'])
    scope={k:value[k] for k in ('environment_ref','target_sha256','prepared_sha256','session')}
    assert common.validate(value,kind='rank-fault',**scope)['complete']
    assert not projection['producer_authenticated'] and not projection['native_validation']['worker_gpu_release_verified']


@pytest.mark.parametrize('damage', ['release_missing','release_expired','release_unacknowledged','same_request',
    'success_only','worker_missing','future_missing','clock','outcome','incomplete'])
def test_file_release_or_http_success_alone_cannot_create_recovery(damage):
    args=list(bundle(release=True,recovery=True))
    packet,client,receipt,scope,deadlines,recovery=args
    if damage.startswith('release_') or damage in {'worker_missing','future_missing'}:
        for index,item in enumerate(packet['recordings']):
            events=[]
            for row in item['capture']['records']:
                e,p=row['event'],deepcopy(row['payload'])
                if e in {'START','MODULE','END'} or e=='RANK_FAILURE' and p['phase']=='CALL':continue
                if e=='FAULT_CONTROL' and p['phase']=='RELEASE':
                    if damage=='release_missing':continue
                    if damage=='release_expired':p['phase']='EXPIRED'
                    if damage=='release_unacknowledged':p['action_receipt_sha256']=None
                if e=='RANK_FAILURE' and p['native_request_sha256']==['b'*64] and (
                    damage=='worker_missing' and p['point']=='WorkerProc._execute_worker_rpc' or
                    damage=='future_missing' and p['point']=='FutureWrapper.result'):continue
                events.append((e,p,row['monotonic_ns']-fixture.B))
            if item['pid']==33:events.insert(0,('RANK_FAILURE',fixture.rank_row('WorkerProc._execute_worker_rpc','CALL',stamp=150),150))
            packet['recordings'][index]=fixture.recording(item['pid'],events,True)
    elif damage=='same_request':recovery['external_request_sha256']=fixture.EXT
    elif damage=='success_only':args[0]=fixture.case()[0]
    elif damage=='clock':recovery['clock_context']['boot_sha256']='e'*64
    elif damage=='outcome':recovery['outcome']=500
    else:recovery['complete']=False
    projection=adapter.project(*args[:5],recovery_client=args[5])
    assert not any(e['event']=='recovered' for e in projection['events'])
    assert not projection['scope_complete']


def test_missing_detection_does_not_turn_an_upper_notification_into_detection():
    args=list(bundle())
    for item in args[0]['recordings']:
        if item['pid']!=33:continue
        events=[]
        for row in item['capture']['records']:
            if row['event'] in {'START','MODULE','END'}:continue
            if row['event']=='RANK_FAILURE' and row['payload']['point']=='WorkerProc.handle_output':continue
            events.append((row['event'],row['payload'],row['monotonic_ns']-fixture.B))
        item.update(fixture.recording(33,events,True))
    projection=adapter.project(*args[:5])
    assert 'native_detection' in projection['native_validation']['missing']
    assert not any(e['event']=='detected' for e in projection['events'])


@pytest.mark.parametrize('damage',['hash_chain','owner','session','source','pid','clock'])
def test_unbound_raw_source_never_creates_acknowledged_fault(damage):
    args=list(bundle())
    if damage=='session':args[3]['session']='a'*32
    elif damage=='owner':args[0]['before']['owner']['pod_uid']='other-pod'
    elif damage=='clock':args[1]['clock_context']['boot_sha256']='e'*64
    else:
        row=next(r for r in args[0]['recordings'][2]['capture']['records'] if r['event']=='FAULT_CONTROL')
        row['payload']['source_sha256']='0'*64 if damage=='source' else row['payload']['source_sha256']
        if damage=='pid':args[0]['recordings'][2]['capture']['records'][0]['payload']['context']['pid']=99
        else:row['previous_sha256']='0'*64
    projection=adapter.project(*args[:5])
    assert not projection['events'] and not projection['scope_complete']


def test_external_scope_mismatch_is_rejected_and_no_fake_binding_is_created():
    args=list(bundle());args[3]['target_sha256']='a'*64
    with pytest.raises(common.GpuInputError,match='GPU_INPUT_SCOPE_MISMATCH'):make(args)
    args=list(bundle());args[1]['clock_context']['boot_sha256']='e'*64
    with pytest.raises(common.GpuInputError,match='GPU_INPUT_BINDING_INVALID'):make(args)


def test_source_identity_binds_existing_engine_observer_bytes(monkeypatch):
    before=producer.producer();read=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:read(p)+(b'\n' if p.name=='vllm_rank_failure_sensor.py' else b''))
    assert producer.producer()['source_sha256']!=before['source_sha256']


def input_document(args):
    packet,client,receipt,scope,deadlines,recovery=args
    return dict(packet=packet,client=client,injection_receipt=receipt,source_scope=scope,deadlines=deadlines,
        recovery_client=recovery,pod={**fixture.OWNER,'restart_count':0},environment_ref=ENV,
        target_sha256=fixture.TARGET,prepared_sha256='d'*64,session=fixture.SESSION)


def test_input_tool_keeps_original_digest_source_projection_and_unregistered_status(tmp_path):
    from tools.rank_fault_inputs import prepare
    from guard.models import canonical_json
    import hashlib,json
    source=tmp_path/'source.json';raw=canonical_json(input_document(bundle(release=True,recovery=True)))
    source.write_bytes(raw)
    output=tmp_path/'input.json';result=prepare(source,output)
    assert result==json.loads(output.read_bytes())
    assert result['input_sha256']==hashlib.sha256(raw).hexdigest()
    assert result['data_origin']=='DECLARED' and result['engine_executed'] is False
    assert result['source_projection']['scope_complete']
    assert not result['validation']['supported'] and not result['validation']['complete']
    with pytest.raises(FileExistsError):prepare(source,output)
    assert source.read_bytes()==raw


@pytest.mark.parametrize('damage',['unknown','missing','unbound','duplicate_json','symlink'])
def test_invalid_input_tool_sources_do_not_write_output(tmp_path,damage):
    from tools.rank_fault_inputs import prepare
    from guard.models import canonical_json
    source=tmp_path/'source.json';output=tmp_path/'result.json'
    value=input_document(bundle())
    if damage=='unknown':value['invented']='not-read'
    elif damage=='missing':del value['prepared_sha256']
    elif damage=='unbound':value['source_scope']['target_sha256']='a'*64
    source.write_bytes(canonical_json(value))
    if damage=='duplicate_json':source.write_bytes(b'{"packet":{},"packet":{}}')
    if damage=='symlink':
        link=tmp_path/'link.json';link.symlink_to(source);source=link
    with pytest.raises(ValueError):prepare(source,output)
    assert not output.exists()


def test_late_client_error_remains_a_source_violation_and_observed_delivery():
    args=list(bundle())
    args[1]['response_finished_ns']=fixture.B+3_000_000
    projection=adapter.project(*args[:5])
    assert any(v['code']=='affected_request_error_DEADLINE_EXCEEDED' for v in projection['native_validation']['violations'])
    assert any(e['event']=='client_delivered' for e in projection['events'])
    assert projection['native_validation']['new_security_verdict'] is None
