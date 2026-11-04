"""Real Torch CPU producer -> current registry/consumer contract, EXAMPLE I/O.

Requires an already owned /guard-profiler-traces root (0700, current UID).
No dependency installation, GPU call, AWS, engine deployment or policy approval.
The pinned vLLM method bodies and real Torch CPU activity are distinguished from
the EXAMPLE API/Kubernetes/process-binding boundary supplied by existing tests.
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',choices=('normal','trace-collision','backend-stop-failure','followup-error','replacement','unsupported-probe'),
        default=['normal','trace-collision','backend-stop-failure','followup-error'])
    args=parser.parse_args()
    if sys.version_info[:2]!=(3,12) or sys.flags.optimize:
        raise ValueError('LIF14_CPU_REQUIRES_PYTHON312_ASSERTIONS')
    args.output.mkdir(parents=True,exist_ok=False)
    import torch
    import pytest
    from guard.adapters import vllm_profiler_sensor as sensor
    from guard.executors import kubernetes_profiler_control as control
    from guard.executors.registry import build_executor
    from guard.models import canonical_json,load_pack,utcnow
    from guard.core.select import select
    from guard.core.run import run_plan,RunConfig
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.store import Store
    from guard.store.packs import put_pack,get_pack
    from guard.store.bundle import verify_complete,export_bundle,restore_bundle
    from guard.runtime import profiler_owned_trace as fixture
    from tests.test_kubernetes_profiler_control import inputs
    from tests.builders import policy
    from tools.build_kubernetes_profiler_control_pack import derive
    from tempfile import TemporaryDirectory
    results=[]
    for mode in args.cases:
        with TemporaryDirectory(prefix='byul-lif14-contract-') as temp,pytest.MonkeyPatch.context() as patch:
            temp=Path(temp);external=temp/'external';external.mkdir()
            env,world,cfg,values=inputs(temp,external,patch,'normal')
            session=world.prepared['session'];trace=Path(fixture.ROOT)/session
            native=world.native;native.wrapper.torch=torch
            profile=native.profile
            if mode=='replacement':world.mode='replacement'
            if mode=='unsupported-probe':patch.setattr(torch.autograd,'_profiler_enabled',lambda:False)
            profile.profiler_config=type('Config',(),{})()
            profile.profiler_config.torch_profiler_dir=str(trace)
            profile._profiler_kwargs={'activities':[torch.profiler.ProfilerActivity.CPU],
                'on_trace_ready':torch.profiler.tensorboard_trace_handler(str(trace),worker_name='byul-lif14-cpu')}
            if mode!='trace-collision':trace.mkdir(mode=0o700)
            cfg['owned_trace_fault']=mode=='trace-collision'
            if cfg['owned_trace_fault']:values['profiler_case']='prepared-stop-failure'
            original_runner=world.runner;original_stop=None;failed_once=False
            def runner(argv,**kwargs):
                nonlocal original_stop,failed_once
                if 'exec' in argv and '-c' in argv and argv[argv.index('-c')+1]==Path(fixture.__file__).read_text(encoding='utf-8'):
                    code=argv[argv.index('-c')+1:]
                    ran=subprocess.run([sys.executable,'-c',*code],input=kwargs.get('input_bytes',b''),capture_output=True,timeout=kwargs['timeout'])
                    return {'returncode':ran.returncode,'stdout_bytes':ran.stdout,'stderr_bytes':ran.stderr,
                        'capture':{'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}}
                if 'exec' in argv and kwargs.get('input_bytes'):
                    request,=json.loads(kwargs['input_bytes'])['requests']
                    slot=request['id']
                    if slot in {'normal-active','normal-next'}:
                        with torch.profiler.record_function('byul_lif14_cpu_work'):
                            (torch.ones((4,4))+1).sum().item()
                    if slot=='stop' and mode in {'backend-stop-failure','followup-error','replacement'} and not failed_once:
                        backend=profile.profiler;original_stop=backend.stop
                        def fail():raise RuntimeError('PRIVATE_BACKEND_STOP_FAILURE')
                        backend.stop=fail;failed_once=True
                    if slot=='next-start' and original_stop is not None:
                        original_stop();original_stop=None
                    if slot=='normal-after' and mode=='followup-error':raise OSError('PRIVATE_FOLLOWUP_FAILURE')
                return original_runner(argv,**kwargs)
            class Reader:
                def __init__(self,target,env,prepared,**kwargs):self.binding={'engine':world.target['kubernetes']['engine']}
                def snapshot(self):return {}
                def seal_session(self):return {'sealed':not world.replaced}
                def report(self):return {}
            patch.setattr(control,'KubernetesLifecycleReader',Reader)
            patch.setattr(control.KubernetesProfilerControlExecutor,'_capture',lambda self,*a:world.packet())
            destination=args.output/mode;destination.mkdir()
            shutil.copytree(Path(cfg['prepared']).parent,destination/'prepared15')
            (destination/'target.json').write_bytes(Path(cfg['target']).read_bytes())
            derive(Path(__file__).resolve().parents[1]/'packs/PACK-BASELINE',destination/'pack')
            pack=load_pack(destination/'pack')
            check=pack.checks['LIF-14']
            executor=build_executor('profiler_observation','kubernetes',{'profiler_observation':cfg})
            assert type(executor).__name__=='KubernetesProfilerObservationExecutor'
            patch.setattr('guard.runtime.kubernetes_http.capture_pipes',runner)
            drafts=[];original_run=control.KubernetesProfilerControlExecutor.run
            def record_run(self,*a,**kw):
                draft=original_run(self,*a,**kw);drafts.append(draft);return draft
            patch.setattr(control.KubernetesProfilerControlExecutor,'run',record_run)
            try:
                store=Store(destination/'store');put_pack(store,pack.path)
                pol=policy(pack.ref(),scope={'include':['LIF-14']},values={'LIF-14':values})
                store.put_many([env,pol]);plan=select(pack,[env],pol);store.put(plan)
                evd,=run_plan(store,pack,plan,RunConfig(executors={'profiler_observation':'kubernetes'},
                    adapter='vllm',raw={'profiler_observation':cfg}))
                draft,=drafts
                obs={k:v.model_dump(mode='json') for k,v in draft.observations.items()}
                cycles=obs['profiler_backend_cycles']['value'];restarts=obs['profiler_backend_restarts']['value']
                count=0 if mode=='unsupported-probe' else 1 if mode=='replacement' else 2
                assert len(cycles)==count and len(restarts)==int(count==2),(mode,len(cycles),restarts)
                assert all(c['worker_binding_verified'] for c in cycles)
                if restarts:assert restarts[0]['next_backend_stopped_verified'] and restarts[0]['next_trace_output_completed_verified']
                mismatch=mode in {'backend-stop-failure','followup-error','replacement'}
                assert bool(obs['activation_violations']['value'])==mismatch
                if mode=='trace-collision':
                    assert obs['profiler_owned_fault_verified']['value'] and obs['profiler_owned_recovery_verified']['value']
                    assert cycles[0]['stop_phase']=='RAISE' and cycles[0]['backend_collection_stopped_verified']
                if mode=='followup-error':assert draft.status.value=='ABORTED' and obs['activation_violations']['value']
                if mode=='replacement':
                    assert draft.status.value=='ABORTED' and obs['activation_violations']['value']
                    # Local CPU ownership only, not permission to act on a
                    # replaced GPU Pod. The actual executor refused that call.
                    profile.profiler.stop()
                assert torch._C._autograd._profiler_enabled() is False
                # Exercise the actual bounded writer -> JSONL admission ->
                # profiler consumer as well. Only the owned sink directory and
                # external physical-binding assertions remain EXAMPLE inputs.
                from guard.executors.lifecycle_capture import parse_recording
                from guard.adapters import vllm_profiler_metadata
                base=native.base;data=temp/'writer-sink';data.mkdir(mode=0o700)
                original_open=base._open_directory
                base._open_directory=lambda p:os.open(data,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
                try:
                    config={'session':session,'output_dir':'/guard-lifecycle/data/'+session,
                        'control_path':'/guard-lifecycle/data/'+session+'/SEALED'}
                    context=world.packet()['recordings'][0]['capture']['records'][0]['payload']['context']
                    writer=base.Writer(config,world.prepared['assets']['settings.json']['sha256'],context)
                    for row in native.observer.recorded:writer.write(row['event'],row['payload'])
                    writer.close('SEALED')
                finally:base._open_directory=original_open
                raw=next(data.glob('pid-*.jsonl')).read_bytes()
                records,summary=parse_recording(raw,world.prepared)
                current=world.packet()['recordings'][0]
                parsed={'recordings':[{'capture':{'records':records,'summary':summary,
                    'process_context_verified':current['capture']['process_context_verified'],'image_digest_verified':False},
                    'session_owner_match':current['session_owner_match'],'listed_file_identity_match':current['listed_file_identity_match']}]}
                reprojected=vllm_profiler_metadata.project(parsed,['0'])
                expected_final=0 if mode=='replacement' else count
                assert summary['recording_closed'] and len(reprojected['backend_cycles'])==expected_final
                assert any(c['confirmed_wrapper_backend_violation'] for c in reprojected['backend_cycles'])==(mismatch and mode!='replacement')
                (destination/'writer-produced.jsonl').write_bytes(raw)
                (destination/'writer-consumer-projection.json').write_bytes(canonical_json(reprojected))
                now=utcnow();res,=judge_plan(store,pack,plan,as_of=now)
                rpt=build_report(store,pack,plan,as_of=now);store.put(rpt)
                verify_complete(store)
                # Preserve the seed judge verbatim, including its axis mapping;
                # this CPU contract is not an engine security conclusion.
                assert res.verdicts.security.value==('FAIL' if mismatch else 'UNKNOWN')
                export_bundle(store.root,destination/'bundle.zip')
                restore_bundle(destination/'bundle.zip',destination/'restored')
                restored=Store(destination/'restored');verify_complete(restored)
                replay,=judge_plan(restored,get_pack(restored,pack.ref()),plan,as_of=now)
                assert replay.ref()==res.ref()
                for obj in (env,pol,plan,evd,res,rpt):
                    (destination/(obj.kind.value+'.json')).write_bytes(canonical_json(obj.to_dict()))
                (destination/'observations.json').write_bytes(canonical_json(obs))
                (destination/'producer-records.json').write_bytes(canonical_json(world.packet()))
                (destination/'profiler-control.json').write_bytes(draft.artifacts[0].path.read_bytes())
                results.append({'case':mode,'execution':draft.status.value,'backend_cycles':len(cycles),
                    'native_backend_stop_mismatch':mismatch,'owned_fault_verified':obs['profiler_owned_fault_verified']['value'],
                    'owned_recovery_verified':obs['profiler_owned_recovery_verified']['value'],
                    'EVD_ref':evd.ref().model_dump(mode='json'),'RES_ref':res.ref().model_dump(mode='json'),
                    'RPT_ref':rpt.ref().model_dump(mode='json'),'saved_pack_replay_identical':True,
                    'bounded_writer_parse_consumer':True})
            finally:
                if original_stop is not None:original_stop()
                if torch._C._autograd._profiler_enabled():profile.profiler.stop()
    receipt={'python':sys.version.split()[0],'torch':torch.__version__,'torch_build_cuda':torch.version.cuda,
        'actual_operations':'CPU only','GPU_executed':False,'boundary_origin':'EXAMPLE API/Kubernetes/physical process binding',
        'actual_producer':'pinned vLLM method bodies and actual Torch C backend/CPU trace','results':results}
    (args.output/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(receipt))


if __name__=='__main__':main()
