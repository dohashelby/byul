"""Full LIF11 definition, selected census/independent FAIL and canonical Ref flow."""
from copy import deepcopy
import ast,json,socket,subprocess,time
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.adapters import resource_recovery_probe as probe,vllm_initialization_sensor as initialization,vllm_rank_failure_sensor as rank
from guard.cli import app
from guard.collectors import lifecycle
from guard.executors.resource_recovery_contract import normalize_policy
from guard.models import canonical_json,load_pack,sha256_hex
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_resource_recovery import source as census_source
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_a1_initialization_sensor import make_world as init_world,invoke as init_invoke,REPO
from tests.test_a1_rank_failure_sensor import make_world as rank_world,invoke as rank_invoke
from tests.test_a1_resource_recovery import selection,packet,values
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tools.build_kubernetes_resource_recovery_pack import derive

PRIVATE='EXAMPLE_RECOVERY_PRODUCT_PRIVATE'


class World(BaseWorld):
    def __init__(self,group,target,mode,branch):
        super().__init__(group,target,mode);self.branch=branch;self.census_count=0;self.native_added=False
    def add_native(self):
        if self.native_added:return
        self.native_added=True
        if self.branch=='runtime-failure':world=rank_world(self.group.prepared['session']);rank_invoke(world,'owned-lif11-normal')
        else:
            world=init_world(self.group.prepared['session'],'partial-failure')
            try:init_invoke(world)
            except Exception:pass
        additions=deepcopy(world.observer.recorded)
        def change(rows):
            rows[-1:-1]=additions;rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],additions[-1]['monotonic_ns'])
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)
    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            value=json.loads(kwargs['input_bytes'])
            if value.get('schema')==probe.SELECTION_SCHEMA:
                assert '-i' in argv and '--container=engine' in argv
                command=argv[argv.index('--')+1:];assert command[:5]==['python3','-I','-S','-B','-c']
                call=next(n for n in ast.walk(ast.parse(command[5])) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='compile')
                assert ast.literal_eval(call.args[0])==census_source()
                self.census_count+=1;result=packet(value)
                if self.census_count==2:
                    if self.mode=='census-failure':raise OSError(PRIVATE)
                    if self.mode=='invalid-census':result['private']=PRIVATE
                    if self.mode=='clock-skew':
                        result['started_wall_ns']+=86400*10**9;result['finished_wall_ns']+=86400*10**9
                    if self.mode not in {'retained','conditional','before-deadline','attachment','native-one-missing','native-partial','seal-failure','http-cleanup','retained-http-failure'}:
                        for row in result['resources']:row['status']='absent'
                    if self.mode=='fd-reused':
                        for row in result['resources']:row['status']='replaced'
                    if self.mode=='pid-reused':
                        result['owners'][0]['status']='replaced';result['owners'][0]['context']['start_ticks']+=1
                        for row in result['resources']:row['status']='owner-unavailable'
                pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
                return {'returncode':0,'stdout_bytes':canonical_json(result),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
            request,=value['requests'];self.sent.append(request);slot=request['id'].removeprefix('lif11-')
            assert slot in {'before','after'} and request['body']=={'model':'fixture-model','prompt':'hello','max_tokens':1,'temperature':0,'stream':False}
            self.add_native();body=json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]})
            row={'id':request['id'],'method':'POST','mode':'json','outcome':200,'body':body,'body_sha256':sha256_hex(body.encode()),
                'body_bytes':len(body.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False}
            if self.mode=='retained-http-failure':row['id']='unbound'
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='http-cleanup' and slot=='after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        return super().runner(argv,**kwargs)


def inputs(tmp_path,group,target,mode,branch):
    environment=group.env.model_copy(deep=True);environment.model.id='EXAMPLE-model';environment.model.revision='EXAMPLE-revision';environment.model.inventory_sha256='b'*64
    if mode=='version032':environment.engine.version='0.32.0'
    group.env=environment.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    sensor=rank if branch=='runtime-failure' else initialization
    folder=group.root/'recovery-prepared';group.prepared=lifecycle.prepare_assets(folder,REPO,profile=sensor.PROFILE);group.manifest_path=folder/'manifest.json'
    def header(rows):
        rows[0]['payload'].update(schema=sensor.SCHEMA,profile=sensor.PROFILE,session=group.prepared['session'],
            settings_sha256=group.prepared['assets']['settings.json']['sha256'],sensor_sha256=group.prepared['assets']['guard_lifecycle_sensor.py']['sha256'])
    group.raw_by_pid={pid:revised(raw,header) for pid,raw in group.raw_by_pid.items()}
    world=World(group,target,mode,branch);selected=selection();pol=values(selected)
    if mode=='conditional':pol['recovery_deadline_basis']='after-verified-failure'
    if mode=='before-deadline':
        from datetime import timedelta
        from guard.models import utcnow
        pol['recovery_required_absent_after']=(utcnow()+timedelta(seconds=30)).isoformat()
    pol.update(recovery_environment_sha256=group.env.ref().sha256,recovery_target_sha256=sha256_hex(canonical_json(world.target)),
        recovery_prepared_sha256=sha256_hex(canonical_json(group.prepared)),recovery_branch=branch)
    derive(baseline_pack().path,tmp_path/'pack');return world,selected,pol,load_pack(tmp_path/'pack')


@pytest.mark.parametrize('branch',['runtime-failure','partial-initialization'])
@pytest.mark.parametrize('mode',['released-descriptors','retained','conditional','before-deadline','fd-reused','pid-reused','census-failure',
    'invalid-census','clock-skew','engine-replaced','seal-failure','native-one-missing','native-partial','attachment','http-cleanup',
    'retained-http-failure','actual-lab-false','version032'])
def test_both_registered_cli_branches_independent_fail_unknown_and_exact_refs(tmp_path,group,native_target,monkeypatch,mode,branch):
    world,selected,values_,pack=inputs(tmp_path,group,native_target,mode,branch)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['LIF-11']},values={'LIF-11':values_})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target),('selection',selected)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nresource_recovery="kubernetes"\n[adapter]\nname="vllm"\n[resource_recovery]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))+'\nselection='+json.dumps(str(tmp_path/'selection.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='LIF-11').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    fail=mode in {'retained','seal-failure','native-one-missing','native-partial','attachment','http-cleanup','retained-http-failure'}
    assert e.data_origin.value=='EXAMPLE' and res.verdicts.functional.value==('FAIL' if fail else 'UNKNOWN')
    assert res.verdicts.security.value==res.verdicts.performance.value=='UNKNOWN' and e.normal_control.succeeded is None
    if mode in {'actual-lab-false','version032'}:
        assert not world.sent and not world.census_count
        assert e.observations['recovery_absolute_deadline_violated'].value is None
    else:
        assert e.observations['recovery_absolute_deadline_violated'].value==fail
        assert all(e.observations[k].value is False for k in ('recovery_global_resources_released','recovery_next_normal_launch_verified','recovery_scope_complete'))
        assert len(world.sent)==(1 if mode in {'engine-replaced','retained-http-failure'} else 2)
        if mode=='released-descriptors':assert e.observations['recovery_selected_absence_observed'].value is True
    if mode in {'census-failure','invalid-census','clock-skew','engine-replaced','native-partial','attachment','http-cleanup','retained-http-failure','actual-lab-false','version032'}:assert e.execution.status.value=='ABORTED'
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for artifact in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_full_two_branches_and_original_76_definition_bytes_remain(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF11_complete']
    assert set(pack.checks['LIF-11'].legacy['scenario_ids'])=={'SC-18-02','SC-17-01'}
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['LIF-11'],name)==getattr(pack.checks['LIF-11'],name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/LIF-11.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('key,value',[('recovery_deadline_basis','auto'),('recovery_branch','ray'),('recovery_expected_ranks',['0','0']),
    ('recovery_max_clock_skew_ms',True),('recovery_required_resource_kinds',['guess']),('recovery_phase','complete')])
def test_policy_never_infers_deadline_failure_or_ownership(key,value):
    pol=values(selection());pol[key]=value
    with pytest.raises(ValueError,match='RESOURCE_RECOVERY_POLICY_INVALID'):normalize_policy(pol)
