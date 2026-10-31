"""Real PLT07 registry/CLI; only owned Kubernetes I/O is EXAMPLE."""
from copy import deepcopy
from pathlib import Path
import json,socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,load_pack
from guard.store import Store
from guard.runtime import kubernetes_http as http
from tests.builders import baseline_pack,policy,T0
from tests.test_kubernetes_boundary_egress_consumer import context,ACTOR,PRIVATE
from tools.build_kubernetes_boundary_egress_pack import derive

@pytest.mark.parametrize('mode',['completed','normal','denied','timeout','ack-mismatch','no-final-clock','later-transport','cleanup',
    'receiver-restart','receipt-peer','prefix','report-error','attachment'])
def test_real_factory_original_independent_fail_and_exact_refs(tmp_path,monkeypatch,mode):
    ctx,receiver,world=context(monkeypatch,'normal' if mode in {'report-error','attachment'} else mode)
    original_inputs=deepcopy((ctx.target,receiver,ctx.policy_values,ACTOR))
    derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    if mode=='report-error':
        old=http.KubernetesHTTP.report
        def report(self):
            if world.execs:raise OSError(PRIVATE)
            return old(self)
        monkeypatch.setattr(http.KubernetesHTTP,'report',report)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*args,**kwargs):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['PLT-07']},values={'PLT-07':ctx.policy_values})
    for name,data in [('environment',ctx.env.to_dict()),('target',ctx.target),('receiver',receiver),('policy',pol.to_dict())]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(data))
    cfg=tmp_path/'guard.toml';a=ACTOR
    cfg.write_text('[executors]\nboundary_egress="kubernetes"\n[adapter]\nname="vllm"\n[boundary_egress]\ntarget='+json.dumps(str(tmp_path/'target.json'))
        +'\nreceiver_target='+json.dumps(str(tmp_path/'receiver.json'))+'\nactor_spec={uid='+str(a['uid'])+',gid='+str(a['gid'])+',groups='+json.dumps(a['groups'])+'}'
        +'\nconnect_timeout=0.1\ntotal_timeout=10\nmax_duration_seconds=30\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,expected=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==expected,(result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    fail=mode not in {'denied','timeout','receiver-restart','receipt-peer','prefix'}
    assert e.data_origin.value=='EXAMPLE' and res.verdicts.security.value==('FAIL' if fail else 'UNKNOWN')
    assert res.verdicts.functional.value==res.verdicts.performance.value=='UNKNOWN'
    assert e.normal_control.attempted is False and e.normal_control.succeeded is None
    assert e.observations['egress_scope_complete'].value is False
    assert bool(e.observations['forbidden_egress_successes'].value) is fail
    assert e.execution.status.value==('COMPLETED' if mode in {'completed','normal','ack-mismatch','no-final-clock'} else 'ABORTED')
    assert bool(e.execution.cleanup_errors) is (mode=='cleanup')
    if fail:
        trial,=e.observations['egress_trials'].value;assert trial['verified'] and trial['actor_groups']==[55,2000]
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    assert original_inputs==(ctx.target,receiver,ctx.policy_values,ACTOR)
    objects=[r for k in ('ENV','POL','PLAN','EVD','RES','RPT') for r in store.iter_kind(k)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(r.ref()).to_dict()==r.to_dict() for r in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])

def test_original_policy_eleven_judge_five_branches_and_other76_bytes(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    before,after=seed.checks['PLT-07'],pack.checks['PLT-07'];assert len(after.policy_params)==13 and len(before.policy_params)==11
    assert [p.model_dump() for p in after.policy_params[:11]]==[p.model_dump() for p in before.policy_params]
    assert len(before.legacy.scenario_ids)==5 and receipt['unchanged_definition_bytes']==76
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence','judge','functional_probes'):
        assert getattr(before,name)==getattr(after,name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/PLT-07.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()

@pytest.mark.parametrize('bad',['actor','receiver','connect','total','duration','unknown-config','lab','version'])
def test_explicit_config_has_no_default_actor_receiver_or_larger_scope(tmp_path,monkeypatch,bad):
    from guard.executors.kubernetes_egress_product import KubernetesEgressProductExecutor
    ctx,receiver,world=context(monkeypatch,'completed');derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    cfg={'target':str(tmp_path/'target.json'),'receiver_target':str(tmp_path/'receiver.json'),'actor_spec':deepcopy(ACTOR),
        'connect_timeout':.1,'total_timeout':10,'max_duration_seconds':30}
    if bad=='actor':cfg.pop('actor_spec')
    if bad=='receiver':cfg.pop('receiver_target')
    if bad=='connect':cfg.pop('connect_timeout')
    if bad=='total':cfg['total_timeout']=31
    if bad=='duration':cfg['max_duration_seconds']=True
    if bad=='unknown-config':cfg['worker_only']=True
    if bad=='lab':ctx.target['lab']=False
    if bad=='version':ctx.env=ctx.env.model_copy(deep=True);ctx.env.engine.version='0.32.0';ctx.env=ctx.env.seal()
    (tmp_path/'target.json').write_bytes(canonical_json(ctx.target));(tmp_path/'receiver.json').write_bytes(canonical_json(receiver))
    calls=[]
    def forbidden(*a,**kw):calls.append(True);raise AssertionError('no external I/O')
    executor=KubernetesEgressProductExecutor(cfg,runner=forbidden)
    with pytest.raises(ValueError):executor.run(pack.checks['PLT-07'],ctx.env,ctx.policy_values,object(),tmp_path/'work')
    assert calls==[]
