"""Existing OBS02 CLI consumes immutable A2 generation facts; EXAMPLE only."""
from copy import deepcopy
from pathlib import Path
import hashlib,json,socket,subprocess
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors import native_generation_model_files as dedicated
from guard.adapters import vllm_generation_file_metadata as common
from guard.executors.kubernetes_observation_identity import KubernetesObservationIdentityExecutor as Executor
from guard.executors import observation_identity_contract as identity
from guard.models import canonical_json
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import policy,T0
from tests import test_a1_generation_file_product as product
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external

PRIVATE='EXAMPLE_GENERATION_CONSUMER_EXCEPTION_MUST_NOT_PERSIST'
DEDICATED_ERROR='NATIVE_GENERATION_FILE_CONSUMER_UNAVAILABLE'
COMMON_ERROR='GENERATION_FILE_CONTEXT_CONSUMER_UNAVAILABLE'


def observe_collection(monkeypatch):
    records=[];original=Executor.collect
    def observed(self,ctx):
        result=original(self,ctx);records.append((deepcopy(result[3]),ctx._termination_gap));return result
    monkeypatch.setattr(Executor,'collect',observed)
    return records


def saved_objects(root):
    store=Store.read_only(root/'store');restored=Store.read_only(root/'restored')
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)]
    assert len(objects)==6 and store.verify()==restored.verify()==[]
    right=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in restored.iter_kind(kind)]
    assert len(right)==6
    assert sorted((o.kind.value,o.id,o.revision,o.content_sha256) for o in objects)==sorted((o.kind.value,o.id,o.revision,o.content_sha256) for o in right)
    for obj in objects:assert restored.resolve(obj.ref()).to_dict()==obj.to_dict()
    assert PRIVATE not in json.dumps([o.to_dict() for o in objects])
    evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert evd.data_origin.value=='EXAMPLE' and evd.normal_control.succeeded is None
    assert res.verdicts.model_dump(mode='json')=={key:'UNKNOWN' for key in ('security','functional','performance')}
    assert all(evd.observations[key].value is False for key in identity.TRUTH_FIELDS)
    return evd,objects


@pytest.mark.parametrize('version',[20,21,22])
@pytest.mark.parametrize('mode',['normal','partial','later-record-error','attachment','wrong-model','analysis-error','attach-before','attach-after'])
def test_existing_OBS02_dedicated_generation_and_late_failures(tmp_path,group,native_target,writer_factory,monkeypatch,version,mode):
    records=observe_collection(monkeypatch);attach=dedicated.attach_configured_models
    if mode=='analysis-error':
        def unavailable(*a,**k):raise ValueError(PRIVATE)
        monkeypatch.setattr(dedicated,'analyze',unavailable)
    elif mode in ('attach-before','attach-after'):
        def unavailable(trials,value):
            if mode=='attach-after':attach(trials,value)
            raise RuntimeError(PRIVATE)
        monkeypatch.setattr(dedicated,'attach_configured_models',unavailable)
    selected=mode if mode in ('normal','partial','later-record-error','attachment','wrong-model') else 'normal'
    product.test_registered_OBS02_exact_profile_generation_files_and_Refs(tmp_path,group,native_target,writer_factory,monkeypatch,version,selected)
    evd,objects=saved_objects(tmp_path);summary,gap=records[-1];projected=summary['projection']
    models=[m for t in evd.observations['identity_native_models'].value for m in t['configured_model_candidates']]
    rows=[c for m in models for c in m.get('generation_scoped_file_context_candidates',[])]
    assert projected['generation_file_contexts'] is not None
    if mode in ('analysis-error','attach-before','attach-after'):
        assert projected['generation_scoped_file_context_diagnostic']==DEDICATED_ERROR
        assert DEDICATED_ERROR in summary['diagnostic_codes'] and gap
        assert evd.execution.status.value=='ABORTED'
    if mode=='analysis-error':assert projected['generation_scoped_file_contexts'] is None and not rows
    else:
        value=projected['generation_scoped_file_contexts'];assert value['schema']==dedicated.SCHEMA
        assert all(value[key] is False for key in dedicated.TRUTH)
        if mode not in ('wrong-model','attach-before'):assert value['candidates'] and rows
        if mode in ('wrong-model','attach-before'):assert not rows
    if rows:
        assert all(c['recording_profile'].endswith('/'+str(version)) for c in rows)
        assert all(c['startup_pair_facts_available']==(version==20) for c in rows)
        assert all(c[key] is False for c in rows for key in dedicated.TRUTH)
        assert all(c['last_weight_source_event'][1]<m['native_locator']['forward_seq']
            for m in models for c in m.get('generation_scoped_file_context_candidates',[]))
    assert PRIVATE not in json.dumps(summary)
    receipt={'version':version,'case':mode,'source_code_parser_or_gate_substituted':False,'EXAMPLE_library_identity_substitute':True,
        'common_retained':True,'dedicated_candidates':len(rows),'returned_analysis_retained':projected.get('generation_scoped_file_contexts') is not None,
        'late_gap':gap,'actual_serving_worker_GPU_approval_tensor':False,'saved_pack_rejudge_exact':True,
        'refs':[o.ref().model_dump(mode='json') for o in objects],
        'original_export_SHA256':hashlib.sha256((tmp_path/'evidence.zip').read_bytes()).hexdigest()}
    (tmp_path/'a1-generation-dedicated-product-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')


def run_common_failure_cli(tmp_path,group,target,patch,version):
    world,values,pack=product.inputs(tmp_path,group,target,patch,'normal',version)
    patch.setattr(http,'capture_pipes',world.runner);patch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected external runtime')
    patch.setattr(subprocess,'Popen',forbidden);patch.setattr(socket,'socket',forbidden)
    pol=policy(pack.ref(),scope={'include':['OBS-02']},values={'OBS-02':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    config=tmp_path/'guard.toml';config.write_text('[executors]\nobservation_identity="kubernetes"\n[adapter]\nname="vllm"\n[observation_identity]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();flags=['--store',tmp_path/'store','--config',config]
    def cli(args):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==0;assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*flags])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*flags]);cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*flags])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*flags]);cli(['verify',*flags])
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'evidence.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'evidence.zip','--destination',tmp_path/'restored'])
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',config])
    cli(['verify','--store',tmp_path/'restored','--config',config])
    original=Store.read_only(tmp_path/'store');restored=Store.read_only(tmp_path/'restored')
    assert sorted((o.id,o.content_sha256) for o in original.iter_kind('RES'))==sorted((o.id,o.content_sha256) for o in restored.iter_kind('RES'))


@pytest.mark.parametrize('version',[20,21,22])
@pytest.mark.parametrize('mode',['common-attach-before','common-attach-after'])
def test_common_returned_analysis_survives_later_attach_failure(tmp_path,group,native_target,writer_factory,monkeypatch,version,mode):
    records=observe_collection(monkeypatch);attach=common.attach_configured_models
    def unavailable(trials,value):
        if mode=='common-attach-after':attach(trials,value)
        raise RuntimeError(PRIVATE)
    monkeypatch.setattr(common,'attach_configured_models',unavailable)
    attempts=[]
    def forbidden(*a,**kw):
        attempts.append(True);raise AssertionError('dedicated stage reached after common attachment failure')
    monkeypatch.setattr(dedicated,'analyze',forbidden)
    run_common_failure_cli(tmp_path,group,native_target,monkeypatch,version)
    evd,objects=saved_objects(tmp_path);summary,gap=records[-1];value=summary['projection']
    assert value['generation_file_contexts']['model_candidates']
    assert value['weight_iteration']['model_candidates']
    assert value['safe_open_mappings']['samples'] and value['safe_open_mappings']['contexts']
    assert value['generation_file_context_diagnostic']==COMMON_ERROR and COMMON_ERROR in summary['diagnostic_codes']
    assert value.get('generation_scoped_file_contexts') is None and gap and evd.execution.status.value=='ABORTED'
    assert attempts==[]
    assert PRIVATE not in json.dumps(summary)
    receipt={'version':version,'case':mode,'common_returned_analysis_retained':True,'dedicated_not_attempted':True,
        'source_code_parser_or_gate_substituted':False,'EXAMPLE_library_identity_substitute':True,
        'actual_serving_worker_GPU_approval_tensor':False,'saved_pack_rejudge_exact':True,
        'refs':[o.ref().model_dump(mode='json') for o in objects],
        'original_export_SHA256':hashlib.sha256((tmp_path/'evidence.zip').read_bytes()).hexdigest()}
    (tmp_path/'a1-generation-dedicated-product-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
