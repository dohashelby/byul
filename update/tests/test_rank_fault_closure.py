"""고정 자료의 TP2·해제·세대 결합 검증. 엔진·GPU를 실행하지 않는다."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from guard.adapters import vllm_rank_fault_input as adapter
from guard.adapters import vllm_rank_fault_closure as closure
from guard.models import canonical_json
from tests import test_fault_projection24 as f
from tests.test_rank_fault_producer import bundle, input_document


def normal_packet(template, *, new=False):
    shift = 900 if new else 0
    external, request, invocation = ('a', 'b', 'c') if new else ('d', 'e', 'f')
    pids = (41, 42, 43, 44) if new else (31, 32, 33, 34)
    selected = f.selection()
    selected.update(observation_started_ns=f.B+shift+8, observation_finished_ns=f.B+shift+10)
    selected['metadata']['request_selection'].update(asgi_request_sha256=external*64,
        selected_request_id_sha256=request*64, asgi_invocation_sha256=invocation*64)
    terminal = f.terminal()
    terminal.update(phase='ASGI_APP_RETURN', asgi_request_sha256=external*64,
        asgi_invocation_sha256=invocation*64, operation_started_ns=f.B+shift+5,
        operation_finished_ns=f.B+shift+(80 if new else 45))
    def rank(point, stamp, index=None):
        row = f.rank_row(point, 'RETURN', stamp=shift+stamp)
        row.update(native_request_sha256=[request*64], executor_sha256=('9' if new else '7')*64)
        if row['role'] == 'executor': row['world_size'] = 2
        if index is not None: row['rank'] = index
        return ('RANK_FAILURE', row, shift+stamp)
    packet = {**deepcopy(template), 'recordings': [
        f.recording(pids[0], [('SELECTION', selected, shift+10), ('TERMINATION', terminal, shift+(80 if new else 45))], True),
        f.recording(pids[1], [rank('MultiprocExecutor.execute_model',25), rank('FutureWrapper.result',70 if new else 40)], True),
        f.recording(pids[2], [rank('WorkerProc._execute_worker_rpc',30,0)], True),
        f.recording(pids[3], [rank('WorkerProc._execute_worker_rpc',31,1)], True)]}
    client = {'clock_context':dict(f.CLOCK), 'outcome':200, 'complete':True,
        'request_started_ns':f.B+shift+5, 'response_finished_ns':f.B+shift+(90 if new else 49),
        'external_request_sha256':external*64}
    return packet, client


def evidence_bundle():
    args = list(bundle('delay', release=True))
    packet, client, injection, scope, deadlines, _ = args
    for p in (packet, injection['pre_signal_packet']):
        for recording in p['recordings']:
            for row in recording['capture']['records']:
                if row['event']=='RANK_FAILURE' and row['payload']['role']=='executor':
                    row['payload']['world_size']=2
            f.rehash(recording)
        rank_one = f.rank_row('WorkerProc._execute_worker_rpc','CALL',kind='delay',stamp=150)
        rank_one['rank']=1
        p['recordings'].append(f.recording(34,[('RANK_FAILURE',rank_one,150)],p is packet))
    normal, normal_client = normal_packet(packet)
    recovered, recovery_client = normal_packet(packet, new=True)
    resume = {'schema':closure.RESUME_SCHEMA, 'scope_sha256':sha256(canonical_json(scope)).hexdigest(),
        'injection_sha256':sha256(canonical_json(injection)).hexdigest(), 'ready_receipt_sha256':'f'*64,
        'context':f.context(33), 'signal':18, 'before_state':'T', 'after_state':'S',
        'before_started_ns':f.B+500, 'before_finished_ns':f.B+510,
        'signal_started_ns':f.B+520, 'signal_finished_ns':f.B+530,
        'after_started_ns':f.B+540, 'after_finished_ns':f.B+550}
    extra = {'schema':closure.SCHEMA, 'normal_packet':normal, 'normal_client':normal_client,
        'release_receipt':resume, 'recovery_packet':recovered, 'recovery_client':recovery_client,
        'expected_recovery':{'workers':[{'rank':i,'context':f.context(43+i)} for i in range(2)],
            'executor_context':f.context(42),'executor_sha256':'9'*64}}
    return args, extra


def project(args, extra):
    return adapter.project(*args[:5], closure_evidence=extra)


def test_two_rank_observation_physical_resume_and_expected_new_generation_are_separate_facts():
    args, extra = evidence_bundle()
    result = project(args, extra)
    facts = result['source_closure']
    assert facts['tp2_normal_observed'], result
    assert facts['physical_release_verified'], result
    assert facts['new_generation_recovery_observed'], result
    assert {r['event'] for r in result['events']} == {'fault_applied','fault_released','recovered'}
    # 기존 TP1 투영이 거부한 탐지·상위 전달은 TP2 관측으로 대신 확정하지 않는다.
    assert 'native_detection' in result['native_validation']['missing']
    assert 'upper_notification' in result['native_validation']['missing']
    assert next(r for r in result['events'] if r['event']=='recovered')['context']['pid']==43
    assert not facts['cross_process_group_verified'] and not facts['scope_complete']
    assert not result['scope_complete'] and not result['native_validation']['whole_kind_complete']
    assert 'source_single_rank_executor' in result['native_validation']['missing']
    assert result['native_validation']['new_security_verdict'] is None
    assert not result['native_validation']['worker_gpu_release_verified']


@pytest.mark.parametrize('damage',['missing_rank','duplicate_rank','same_pid','world_one','wrong_output',
    'wrong_executor','clock','open','coverage','foreign_source','client_incomplete','after_arm','call_rank'])
def test_invalid_normal_topology_cannot_prove_tp2_or_later_facts(damage):
    args, extra = evidence_bundle()
    p = extra['normal_packet']
    if damage=='missing_rank': p['recordings'].pop()
    elif damage=='same_pid': p['recordings'][-1]=deepcopy(p['recordings'][-2])
    elif damage=='clock': extra['normal_client']['clock_context']['time_namespace']=99
    elif damage=='coverage': p['recorded_files_covered']=False
    elif damage=='client_incomplete': extra['normal_client']['complete']=False
    elif damage=='after_arm': extra['normal_client']['response_finished_ns']=f.B+250
    else:
        for recording in p['recordings']:
            rows=recording['capture']['records']
            if damage=='open': rows[:]=[r for r in rows if r['event']!='END']
            for row in rows:
                value=row['payload']
                if damage=='foreign_source' and row['event']=='SELECTION':value['source_sha256']='0'*64
                if row['event']!='RANK_FAILURE':continue
                if damage=='duplicate_rank' and value['role']=='worker':value['rank']=0
                if damage=='world_one' and value['role']=='executor':value['world_size']=1
                if damage=='wrong_output' and value['role']=='executor':value['configured_output_rank']=1
                if damage=='wrong_executor' and value['role']=='future':value['executor_sha256']='0'*64
                if damage=='call_rank' and value['role']=='worker' and value['phase']=='CALL':value['rank']=7
            if damage=='foreign_source':
                previous='0'*64
                for seq,row in enumerate(rows):
                    row.update(seq=seq,previous_sha256=previous)
                    previous=sha256(f.canonical_row(row)).hexdigest()
                raw=b''.join(f.canonical_row(row) for row in rows)
                recording['capture']['provenance'].update(capture_sha256=sha256(raw).hexdigest(),bytes=len(raw))
            else:
                f.rehash(recording)
    facts=project(args,extra)['source_closure']
    assert not facts['tp2_normal_observed'] and not facts['physical_release_verified']
    assert not facts['new_generation_recovery_observed']


@pytest.mark.parametrize('field,value',[('signal',19),('signal',True),('before_state','S'),('after_state','T'),
    ('after_state','Z'),('scope_sha256','0'*64),('injection_sha256','0'*64),('ready_receipt_sha256','0'*64),
    ('signal_started_ns',True),('after_finished_ns',f.B+500),('before_started_ns',f.B+100),('signal_finished_ns',f.B+700)])
def test_no_barrier_file_or_unbound_signal_receipt_can_prove_physical_resume(field,value):
    args,extra=evidence_bundle();extra['release_receipt'][field]=value
    result=project(args,extra);facts=result['source_closure']
    assert facts['tp2_normal_observed'] and not facts['physical_release_verified']
    assert not any(r['event'] in {'fault_released','recovered'} for r in result['events'])


@pytest.mark.parametrize('damage',['pid_reuse','same_generation','wrong_expected','missing_future','missing_rank',
    'earlier_request','incomplete','same_request','foreign_owner','clock','unacknowledged_release'])
def test_new_generation_requires_independent_expected_processes_and_complete_source_path(damage):
    args,extra=evidence_bundle();p=extra['recovery_packet']
    if damage=='same_generation':
        for item in p['recordings']:
            item['pid']-=10
            item['capture']['records'][0]['payload']['context']['pid']-=10
            f.rehash(item)
        extra['expected_recovery']={'workers':[{'rank':i,'context':f.context(33+i)} for i in range(2)],
            'executor_context':f.context(32),'executor_sha256':'9'*64}
    elif damage=='wrong_expected':extra['expected_recovery']['workers'][0]['context']['pid']=99
    elif damage=='earlier_request':extra['recovery_client']['request_started_ns']=f.B+500
    elif damage=='incomplete':extra['recovery_client']['complete']=False
    elif damage=='same_request':extra['recovery_client']['external_request_sha256']=f.EXT
    elif damage=='foreign_owner':p['after']['owner']['pod_uid']='foreign-pod'
    elif damage=='clock':extra['recovery_client']['clock_context']['time_namespace']=99
    elif damage=='missing_rank':p['recordings'].pop()
    elif damage=='unacknowledged_release':
        for item in args[0]['recordings']:
            for row in item['capture']['records']:
                if row['event']=='FAULT_CONTROL' and row['payload']['phase']=='RELEASE':row['payload']['phase']='EXPIRED'
            f.rehash(item)
    else:
        for item in p['recordings']:
            rows=item['capture']['records']
            if damage=='missing_future':rows[:]=[r for r in rows if r['payload'].get('point')!='FutureWrapper.result']
            if damage=='pid_reuse' and item['pid']==43:rows[0]['payload']['context']['start_ticks']=101
            f.rehash(item)
    result=project(args,extra)
    assert not result['source_closure']['new_generation_recovery_observed']
    assert not any(r['event']=='recovered' for r in result['events'])


def test_same_pid_with_new_start_ticks_and_independent_expected_generation_is_distinct():
    args,extra=evidence_bundle()
    for item in extra['recovery_packet']['recordings']:
        item['pid']-=10;item['start_ticks']=200
        context=item['capture']['records'][0]['payload']['context']
        context.update(pid=item['pid'],start_ticks=200)
        f.rehash(item)
    extra['expected_recovery']={'workers':[{'rank':i,'context':dict(f.context(33+i),start_ticks=200)} for i in range(2)],
        'executor_context':dict(f.context(32),start_ticks=200),'executor_sha256':'9'*64}
    result=project(args,extra)
    assert result['source_closure']['new_generation_recovery_observed']
    recovered=next(e for e in result['events'] if e['event']=='recovered')
    assert recovered['context']['pid']==33 and recovered['context']['start_ticks']==200
    assert not result['scope_complete']


def test_extended_tool_uses_v4_preserves_unknown_and_keeps_legacy_exact_schema(tmp_path):
    from tools.rank_fault_inputs import prepare
    args,extra=evidence_bundle();value=input_document(args)
    value.update(schema='guard-rank-fault-source-input/2',closure_evidence=extra)
    path=tmp_path/'source.json';out=tmp_path/'out.json';path.write_bytes(canonical_json(value))
    result=prepare(path,out)
    assert result==json.loads(out.read_bytes())
    assert result['record']['schema']=='guard-gpu-common-input/4'
    assert not result['validation']['supported'] and not result['validation']['complete']
    assert result['source_projection']['source_closure']['new_generation_recovery_observed']
    assert result['engine_executed'] is False and result['data_origin']=='DECLARED'


def test_late_client_violation_is_preserved_while_new_generation_is_missing():
    _,extra=evidence_bundle();args=list(bundle('delay',release=True))
    args[1]['response_finished_ns']=f.B+3_000_000
    extra['expected_recovery']['workers']=[]
    result=project(args,extra)
    assert any(v['code']=='affected_request_error_DEADLINE_EXCEEDED' for v in result['native_validation']['violations'])
    assert any(r['event']=='client_delivered' for r in result['events'])
    assert not result['scope_complete'] and not result['source_closure']['new_generation_recovery_observed']


def test_owned_python_child_actual_stop_resume_checks_receipt_without_engine_claim():
    """이 시험이 만든 단일 Python 자식만 신호 제어하고 반드시 회수한다."""
    import os
    from pathlib import Path
    import selectors
    import signal
    import subprocess
    import sys
    import time
    child=subprocess.Popen([sys.executable,'-c',"import signal;print('ready',flush=True);signal.pause()"],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        with selectors.DefaultSelector() as ready:
            ready.register(child.stdout,selectors.EVENT_READ)
            assert ready.select(3)
            assert child.stdout.readline()==b'ready\n'
        proc=Path('/proc')/str(child.pid)
        def state():
            before=time.monotonic_ns();fields=(proc/'stat').read_text().rsplit(')',1)[1].split()
            return before,time.monotonic_ns(),fields[0],int(fields[19])
        _,_,_,ticks=state()
        os.kill(child.pid,signal.SIGSTOP)
        stop_limit=time.monotonic()+3
        while state()[2]!='T':
            assert time.monotonic()<stop_limit
            time.sleep(.001)
        stop_observed=time.monotonic_ns()
        before_start,before_end,before_state,current_ticks=state()
        assert current_ticks==ticks
        signal_start=time.monotonic_ns();os.kill(child.pid,signal.SIGCONT);signal_end=time.monotonic_ns()
        resume_limit=time.monotonic()+3
        while True:
            after_start,after_end,after_state,current_ticks=state()
            assert current_ticks==ticks
            if after_state!='T':break
            assert time.monotonic()<resume_limit
            time.sleep(.001)
        context={'pid':child.pid,'start_ticks':ticks,'uid':os.getuid(),
            'pid_namespace':(proc/'ns/pid').stat().st_ino,
            'boot_sha256':sha256(Path('/proc/sys/kernel/random/boot_id').read_bytes()).hexdigest(),
            'time_namespace':(proc/'ns/time').stat().st_ino,
            'time_offsets_sha256':sha256((proc/'timens_offsets').read_bytes()).hexdigest()}
        scope=f.case('delay')[3]
        injection={'state_observed_ns':stop_observed}
        value={'schema':closure.RESUME_SCHEMA,'scope_sha256':sha256(canonical_json(scope)).hexdigest(),
            'injection_sha256':sha256(canonical_json(injection)).hexdigest(),'ready_receipt_sha256':'f'*64,
            'context':context,'signal':int(signal.SIGCONT),'before_state':before_state,'after_state':after_state,
            'before_started_ns':before_start,'before_finished_ns':before_end,
            'signal_started_ns':signal_start,'signal_finished_ns':signal_end,
            'after_started_ns':after_start,'after_finished_ns':after_end}
        active={'p':{'ready_receipt_sha256':'f'*64},'context':context}
        release={'time':time.monotonic_ns()}
        assert closure._resume(value,injection,scope,active,release)==release['time']
        # 실제 OS 동작을 했어도 엔진 관측·GPU 자격·보안 판정 자료는 아니다.
        value['context']=dict(context,start_ticks=ticks+1)
        with pytest.raises(ValueError):closure._resume(value,injection,scope,active,release)
    finally:
        if child.poll() is None:
            os.kill(child.pid,signal.SIGCONT)
            child.terminate()
            try:child.wait(timeout=3)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
        child.stdout.close()


@pytest.mark.parametrize('filename',['vllm_rank_fault_closure.py','gpu_common_input_v4.py','vllm_fault_projection.py'])
def test_producer_identity_includes_new_evidence_and_common_v4_bytes(monkeypatch,filename):
    from pathlib import Path
    from guard.collectors import rank_fault_producer
    old=rank_fault_producer.producer()['source_sha256'];read=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:read(p)+(b'\n' if p.name==filename else b''))
    assert rank_fault_producer.producer()['source_sha256']!=old
