"""기존 검토 invoker/loader/parent를 새 고정 Session recovery child에 결합. candidate는 실행 금지."""
from pathlib import Path
import argparse,hashlib,json,os,sys,types

ORIGINAL_INVOKER=(13965,'4feb36f05b4dede32bec1e319304128e0af56b9e6ede235d91e33db6e292faf2')
STREAM=(19502,'8e2261a0162271288397f962526a9086cc1edf0b983ff6bc4c941fae3bf039e7')
CORE=(23681,'180e6b0abb5ba02a6c7e958f3f560d0947c533a52f2566ca3e6d6ca4c1edbe40')
CONTENT='3c6fff6f4fd03784f52e47b5b008ef991762d01c0db5dfebf1e3105cc4510dee'

def module(name,path,raw):
    obj=types.ModuleType(name);obj.__file__=str(path);sys.modules[name]=obj
    exec(compile(raw,str(path),'exec'),obj.__dict__);return obj

def original_invoker(root,expected_packet_sha):
    # 원 invoker의 정확 source pin 검증 뒤 load_packet이 기존UID/GID/경로/11source검사를 그대로 수행.
    p=root/'invoke_recovery_once.py';raw=p.read_bytes()
    assert (len(raw),hashlib.sha256(raw).hexdigest())==ORIGINAL_INVOKER
    inv=module('s2_session_reused_original_invoker',p,raw)
    packet_raw,packet=inv.load_packet(expected_packet_sha)
    return inv,packet_raw,packet

def checked_components(loader,root,packet):
    pins=packet['session_component_files']
    assert set(pins)=={'session_recovery.py','gb12_stream.py','invoke_session_recovery.py'}
    assert (pins['gb12_stream.py']['bytes'],pins['gb12_stream.py']['sha256'])==STREAM
    assert (pins['session_recovery.py']['bytes'],pins['session_recovery.py']['sha256'])==CORE
    assert packet['session_document']=='AWS-StartNonInteractiveCommand'
    assert packet['session_document_Content_sha256']==CONTENT
    assert packet['session_scope']=='fixed-GB12-readonly-recovery-only-no-model'
    with loader._directory(root) as fd:
        source={n:loader._read(root,fd,n,p) for n,p in pins.items()}
    assert source['invoke_session_recovery.py']==Path(__file__).read_bytes()
    return source

def construct_health(runtime):
    # qualified loader가 만든 실제 동일 packet 객체만 원 guard에 공급한다.
    return runtime.controller_module.Controller(runtime.driver,runtime.glue,runtime.packet,runtime.codec_raw)

def check_runtime_binding(runtime,core,stream,source):
    health=construct_health(runtime)
    assert health.packet is runtime.packet and runtime.driver.PACKET is runtime.packet
    supervisor=core.Recovery(runtime,stream,source['gb12_stream.py'],runtime.packet['session_component_files']['gb12_stream.py'])
    assert supervisor.driver is runtime.driver
    return True

def child_once(inv,runtime,core,stream,source,packet,packet_sha):
    parent=json.loads((runtime.root/'collect-invocation-once-v1.json').read_bytes())
    assert parent['packet_sha256']==packet_sha and parent['PID']==os.getppid()
    assert parent['parent_start_ticks']==inv.start_ticks(os.getppid())
    original=None;result=None
    try:
        assert not packet['not_ready'] and packet['execution_ready'] is True
        runtime.driver.uncertainty_barrier('before-session-recovery-child')
        health=construct_health(runtime)
        supervisor=core.Recovery(runtime,stream,source['gb12_stream.py'],packet['session_component_files']['gb12_stream.py'])
        result=supervisor.run_once(health,packet['collector_request_id'],str(runtime.root/'GB2026111112-original-evidence.zip'))
    except BaseException as error:original=error
    try:
        # 실제 새 core의 stopped 원기록이 있을 때만 기존 parent outcome 계약으로 연결.
        path=runtime.root/'stream-selected-stopped.json'
        if path.exists():
            raw=path.read_bytes();assert len(raw)<4096
            value=json.loads(raw);assert value['InstanceId']=='i-05e8da8ea99593196' and value['exact_off_UTC'] is None
            assert value['collector_request_id']==packet['collector_request_id']
            runtime.driver.put('recovery-selected-stopped.json',{'at':value['at'],'InstanceId':value['InstanceId'],
                'state':'stopped','collector_request_id':packet['collector_request_id'],
                'source_path':str(path),'source_bytes':len(raw),'source_sha256':hashlib.sha256(raw).hexdigest(),
                'exact_off_transition_UTC':None,'IP_SSM_cost_closeout_complete_claimed':False})
        outcome=inv.child_outcome(packet,packet_sha,original,result)
        inv.record('recovery-child-outcome-v1.json',outcome)
    except BaseException as secondary:
        if original is None:raise
        original.add_note('Session child outcome preservation: '+type(secondary).__name__)
    if original is not None:raise original.with_traceback(original.__traceback__)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['check','collect','child'])
    p.add_argument('--packet-sha256',required=True);a=p.parse_args();root=Path(__file__).parent
    inv,raw,packet=original_invoker(root,a.packet_sha256)
    loader=inv.loader_from_private(packet);source=checked_components(loader,root,packet)
    names=('s2_session_fixed_core','s2_session_fixed_stream');missing=object();saved={n:sys.modules.get(n,missing) for n in names}
    try:
        core=module(names[0],root/'session_recovery.py',source['session_recovery.py'])
        stream=module(names[1],root/'gb12_stream.py',source['gb12_stream.py'])
        with loader.load_private(root,raw,a.packet_sha256) as runtime:
            if a.mode=='check':
                verified=False
                if runtime.packet['not_ready'] is False:
                    verified=check_runtime_binding(runtime,core,stream,source)
                print(json.dumps({'source_only_check':True,'API_GPU_Session_called':False,'packet_sha256':a.packet_sha256,
                    'session_sources':{n:hashlib.sha256(b).hexdigest() for n,b in source.items()},
                    'actual_qualified_health_constructor_binding_verified':verified,'runtime_ready_claimed':False}),flush=True)
                return 0
            assert packet['not_ready'] is False and packet['execution_ready'] is True
            assert packet['session_required_verification_complete'] is True
            if a.mode=='collect':
                # 원 parent 함수의 실행 대상만 검증된 이 entry로 결합. 함수/원 wait/group/outcome bytes 불변.
                inv.__file__=str(Path(__file__))
                return inv.run_parent(packet,a.packet_sha256)
            child_once(inv,runtime,core,stream,source,runtime.packet,a.packet_sha256)
            return 0
    finally:
        for name,old in saved.items():
            if old is missing:sys.modules.pop(name,None)
            else:sys.modules[name]=old

if __name__=='__main__':raise SystemExit(main())
