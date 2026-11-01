"""B fixed producer in the canonical registry/CLI; all injected cases EXAMPLE."""
from copy import deepcopy
import json
from pathlib import Path
import socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.executors import registry,structured_tools_cpu as producer
from guard.models import canonical_json
from guard.store import Store
from tests.builders import T0
from tests.test_structured_tools_target031 import rows
from tools.build_req10_cpu_inputs import prepare,PACK_REFS

PRIVATE='A1_REQ10_PRIVATE_CAPTURE_ERROR'

@pytest.mark.parametrize('profile',['reference','vllm-031'])
@pytest.mark.parametrize('mode',['normal','violation-followup-gap','missing','attachment'])
def test_b_fixed_cpu_registered_cli_and_exact_ref_roundtrip(tmp_path,monkeypatch,profile,mode):
    receipt=prepare(tmp_path/'inputs',profile,origin='EXAMPLE',model='fixture-model')
    assert receipt['pack_ref']==PACK_REFS[profile] and not receipt['actual_execution']
    executor=receipt['executor'];kind=receipt['implementation'];factory=registry.resolve_executor(executor,kind);assert factory is not None
    impl=factory({executor:{'source_root':str(tmp_path/'source'),'model':'fixture-model'}})
    assert type(impl) is (producer.StructuredToolsTarget031CPUExecutor if profile=='vllm-031' else producer.StructuredToolsCPUExecutor)
    seen=[]
    def capture(root,*,model):
        seen.append((root,model))
        if mode=='missing':raise RuntimeError(PRIVATE)
        values=deepcopy(rows())
        if profile=='reference':
            for row in values:row.pop('after_normal_conversion')
        if mode in {'violation-followup-gap','attachment'}:
            values[-1].update(request_accepted=True,outcome='execution_error',events=[])
            if mode=='violation-followup-gap':
                values[0]['outcome']='execution_error'
                if profile=='vllm-031':values[-1]['after_normal_conversion']['outcome']='execution_error'
        return {'profile':producer.TARGET031_PROFILE if profile=='vllm-031' else producer.PROFILE,
            'reference_commit':receipt['source_commit'],'scope':'EXAMPLE injected CPU producer boundary',
            'installed_target':None,'server_preprocessing_verified':False,'runner_consumption_verified':False,'rows':values}
    monkeypatch.setattr(producer,'capture_target031' if profile=='vllm-031' else 'capture_reference',capture)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*args,**kwargs):
            if path.name in {'structured-tools-vllm-031-cpu.json','structured-tools-reference-cpu.json'} and args and args[0]=='x':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',open_file)
    def forbidden(*args,**kwargs):raise RuntimeError('UNEXPECTED_EXTERNAL_OPERATION')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\n'+executor+'='+json.dumps(kind)+'\n['+executor+']\nsource_root='+json.dumps(str(tmp_path/'source'))+'\nmodel="fixture-model"\n',encoding='utf-8')
    runner=CliRunner();common=['--config',str(cfg),'--store',str(tmp_path/'store')]
    def cli(args):
        value=runner.invoke(app,list(map(str,args)));assert value.exit_code==0,(args,value.output);assert PRIVATE not in value.output
    cli(['select','--pack',tmp_path/'inputs/proposal-pack','--env',tmp_path/'inputs/environment.json','--policy',tmp_path/'inputs/policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert len(plan.items)==1 and plan.items[0].selected and not plan.items[0].gaps
    cli(['run','--plan',plan.id,'--pack',tmp_path/'inputs/proposal-pack',*common]);cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert seen==[(str(tmp_path/'source'),'fixture-model')] and registry.resolve_executor(executor,kind) is factory
    assert e.data_origin.value=='EXAMPLE' and e.execution.executor.name==executor
    assert e.execution.status.value==('ABORTED' if mode in {'missing','attachment'} else 'COMPLETED')
    assert res.verdicts.security.value==('FAIL' if mode in {'violation-followup-gap','attachment'} else 'UNKNOWN')
    assert res.verdicts.functional.value==('PASS' if mode=='normal' else 'UNKNOWN')
    assert res.verdicts.performance.value=='UNKNOWN'
    if mode=='missing':
        assert set(e.observations)=={'structured_tools_validator_violation','structured_tools_controls',
            'structured_tools_controls_planned','structured_tools_cpu_rows','runner_consumption_verified'}
        assert all(not obs.collected and obs.value is None for obs in e.observations.values())
        assert not e.artifacts and e.execution.abort_reason=='STRUCTURED_TOOLS_CPU_CAPTURE_UNAVAILABLE'
    else:
        assert e.observations['runner_consumption_verified'].value is False
        assert len(e.observations['structured_tools_controls_planned'].value)==(9 if profile=='vllm-031' else 4)
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for artifact in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[x for k in ('ENV','POL','PLAN','EVD','RES','RPT') for x in store.iter_kind(k)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(x.ref()).to_dict()==x.to_dict() for x in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_builder_keeps_original_pack_and_requires_explicit_origin(tmp_path):
    from tests.builders import baseline_pack
    pack=baseline_pack();before={p.relative_to(pack.path).as_posix():p.read_bytes() for p in pack.path.rglob('*') if p.is_file()}
    for profile in PACK_REFS:
        receipt=prepare(tmp_path/profile,profile,origin='DECLARED',model='fixture-model')
        assert receipt['pack_ref']==PACK_REFS[profile] and receipt['origin']=='DECLARED' and not receipt['enterprise_policy_approval']
    with pytest.raises(ValueError):prepare(tmp_path/'invalid','vllm-031',origin='MEASURED',model='fixture-model')
    assert all((pack.path/n).read_bytes()==raw for n,raw in before.items())
