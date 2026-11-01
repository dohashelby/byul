"""Real registry/CLI for fixed A2 IPC consumer; only kubectl I/O is EXAMPLE."""
from copy import deepcopy
import json,socket,subprocess
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,load_pack
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_model_preparation_integration import full_inputs
from tests.test_kubernetes_unix_ipc_consumer import IPCWorld,VALUES,ACTORS,KW,PRIVATE
from tests.test_kubernetes_process_consumers import PRIVATE as NORMAL_PRIVATE
from tools.build_kubernetes_unix_ipc_pack import derive


def inputs(tmp_path,mode):
    env,target=full_inputs()
    normal=mode.removeprefix('normal-') if mode.startswith('normal-') else None
    world=IPCWorld(target,'violation' if normal or mode in {'attachment','report-error'} else mode,normal)
    derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    cfg={'target':str(tmp_path/'target.json'),'actor_specs':deepcopy(ACTORS),'connect_timeout':KW['connect_timeout'],
        'total_timeout':KW['total_timeout'],'max_duration_seconds':30}
    if mode=='lab-false':target['lab']=False
    if mode=='version032':
        env=env.model_copy(deep=True);env.engine.version='0.32.0';env=env.seal();target['environment_ref']=env.ref().model_dump(mode='json')
    return env,target,world,pack,cfg


def write_config(path,cfg):
    rows=['[executors]','unix_ipc="kubernetes"','[adapter]','name="vllm"','[unix_ipc]','target='+json.dumps(cfg['target'])]
    for key in ('connect_timeout','total_timeout','max_duration_seconds'):
        if key in cfg:rows.append(key+'='+str(cfg[key]).lower())
    if 'actor_specs' in cfg:
        rows.append('actor_specs=['+','.join('{uid='+str(a['uid'])+',gid='+str(a['gid'])+',groups='+json.dumps(a['groups'])+'}' for a in cfg['actor_specs'])+']')
    path.write_text('\n'.join(rows)+'\n',encoding='utf-8')


@pytest.mark.parametrize('mode',['normal','violation','partial','later-metadata','actor-namespace','actor-restart','owner-restart',
 'invalid-groups','invalid-raw','cleanup','cleanup-incomplete','child-reap','normal-wrong-id','normal-wrong-model',
 'normal-cleanup','normal-exception','attachment','report-error','lab-false','version032'])
def test_real_registered_factory_canonical_cli_independent_fail_and_exact_refs(tmp_path,monkeypatch,mode):
    env,target,world,pack,cfg=inputs(tmp_path,mode);before=deepcopy((VALUES,ACTORS))
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    if mode=='report-error':
        original=http.KubernetesHTTP.report
        def report(self):
            if world.packets:raise OSError(PRIVATE)
            return original(self)
        monkeypatch.setattr(http.KubernetesHTTP,'report',report)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['PLT-02']},values={'PLT-02':deepcopy(VALUES)})
    for name,data in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(data))
    config=tmp_path/'guard.toml';write_config(config,cfg);runner=CliRunner();common=['--store',tmp_path/'store','--config',config]
    def cli(argv,expected=0):
        result=runner.invoke(app,list(map(str,argv)));assert result.exit_code==expected,(result.output,result.exception)
        assert PRIVATE not in result.output and NORMAL_PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='PLT-02').selected
    cleanup=mode in {'cleanup','cleanup-incomplete','child-reap','normal-cleanup'}
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if cleanup else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    fail=mode in {'violation','partial','later-metadata','cleanup','child-reap','normal-wrong-id','normal-wrong-model','normal-cleanup','normal-exception','attachment','report-error'}
    assert res.verdicts.security.value==('FAIL' if fail else 'UNKNOWN')
    assert res.verdicts.functional.value==res.verdicts.performance.value=='UNKNOWN'
    assert e.data_origin.value=='EXAMPLE' and (VALUES,ACTORS)==before
    if mode in {'lab-false','version032'}:
        assert world.ipc_reads==0 and not world.packets and e.execution.status.value=='ABORTED'
    else:
        assert world.ipc_reads==1 and e.observations['ipc_scope_complete'].value is False
        assert bool(e.observations['unix_ipc_violations'].value)==fail
        if mode=='normal':assert e.execution.status.value=='COMPLETED' and e.normal_control.succeeded is True
        if mode in {'normal','violation','partial','later-metadata','cleanup','child-reap','attachment','report-error'}:
            assert e.observations['unix_ipc_trials'].value[0]['actor_groups']==[55,2000]
        if cleanup:assert e.execution.cleanup_errors and e.execution.status.value=='ABORTED'
        if mode in {'attachment','report-error','normal-exception','normal-wrong-id','partial','later-metadata'}:assert e.execution.status.value=='ABORTED'
    assert PRIVATE not in canonical_json(e.to_dict()).decode() and NORMAL_PRIVATE not in canonical_json(e.to_dict()).decode()
    for artifact in e.artifacts:
        raw=(tmp_path/'store'/artifact.path).read_bytes();assert PRIVATE.encode() not in raw and NORMAL_PRIVATE.encode() not in raw
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',config])
    assert [o.ref() for o in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',config])


@pytest.mark.parametrize('damage',['missing-actors','missing-groups','boolean-groups','same-uid','missing-connect','budget','nan'])
def test_explicit_configuration_has_no_actor_defaults_or_execution(tmp_path,damage):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_ipc_product import KubernetesIPCProductExecutor
    env,target,world,pack,cfg=inputs(tmp_path,'normal');(tmp_path/'target.json').write_bytes(canonical_json(target))
    if damage=='missing-actors':cfg.pop('actor_specs')
    elif damage=='missing-groups':cfg['actor_specs'][0].pop('groups')
    elif damage=='boolean-groups':cfg['actor_specs'][0]['groups']=[True]
    elif damage=='same-uid':cfg['actor_specs'][0]['uid']=1000
    elif damage=='missing-connect':cfg.pop('connect_timeout')
    elif damage=='budget':cfg['max_duration_seconds']=5
    else:cfg['max_duration_seconds']=float('nan')
    exe=KubernetesIPCProductExecutor(cfg,runner=world.runner)
    with pytest.raises(ValueError):exe.run(pack.checks['PLT-02'],env,VALUES,VllmAdapter(),tmp_path/'work')
    assert world.ipc_reads==0 and not world.packets


def test_pack_preserves_original_criteria_policy_control_judge_and_76_bytes(tmp_path):
    seed=baseline_pack();derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    for name in ('question','scope','normal_control','challenge_steps','policy_params','required_observations','judge','legacy','functional_probes','satisfied_if','fail_criteria','unknown_criteria'):
        assert getattr(pack.checks['PLT-02'],name)==getattr(seed.checks['PLT-02'],name)
    for key in seed.checks:
        if key!='PLT-02':assert (seed.path/'checks'/f'{key}.yaml').read_bytes()==(pack.path/'checks'/f'{key}.yaml').read_bytes()
