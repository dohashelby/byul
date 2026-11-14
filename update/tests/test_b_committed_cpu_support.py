"""New optional historical-input boundaries. No old CPU model/Pod is replayed."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import committed_cpu_support as subject
from guard.executors.base import EvidenceDraft
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex
from guard.store import Store
from guard.store.packs import put_pack
from tests.run_binding_support import fixed_transition, transition_table
from tests.builders import evidence, policy
from tests import test_lifecycle_transition as lif
from tests import test_kubernetes_template_supply as sup

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / 'tests/fixtures/consumption_links_k3d_20261108.json'
FIXTURE_SHA = 'bb2fed5910e1a89ac0368f1383046d85152260f658324b2af46e499128bddc68'
# Historical values from the committed A capture, never current/future IDs.
CAPTURE_SCOPE = {
    'session':'f29859da288de2f63870d46901b5f90f',
    'prepared_sha256':'f5a3ee8d4fb33cfefed0253097f1fd0ac5c85eae47279f844985f3230db4569d',
    'pod':{'container_id':'containerd://b04db9c147d410ce18c99b2a2b8fbfd681784f7bcc83fb88216f3a85103996a2',
           'image':'k3d-byul-reg:5111/vllm/vllm-openai-cpu@sha256:045fb648a61cfcd32d4b9ca5d0c391013afadae0178d2e23ac292597f2f64036',
           'pod_uid':'d52fbbda-9324-463e-9c4e-8ec607fe233d','restart_count':0}}
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')


def descriptor():
    assert sha256_hex(FIXTURE.read_bytes()) == FIXTURE_SHA
    return {'path':str(FIXTURE.absolute()),'file_sha256':FIXTURE_SHA,'capture_scope':deepcopy(CAPTURE_SCOPE)}


def negative_draft():
    return EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,
        normal_control={'attempted':True,'succeeded':True},observations={
            'activation_steps':{'value':[],'collected':True},
            'activation_missing':{'value':['EXAMPLE original partial scope'],'collected':True},
            'activation_scope_complete':{'value':False,'collected':True},
            'activation_violations':{'value':['EXAMPLE independent conflict observed before support input'], 'collected':True}})


def assert_fail(draft):
    evd=evidence(check_id='LIF-06',observations={k:v.model_dump() for k,v in draft.observations.items()})
    check=PACK.checks['LIF-06']
    result=RuleTableJudge().judge(check,evd,check.judge.params,{})
    assert result.verdicts['security']=='FAIL'


def test_exact_A_record_to_B_retains_failed_reload_and_explicit_recovery_lineage():
    desc=descriptor();before=deepcopy(desc)
    raw, result=subject.consume('LIF-06',desc)
    assert raw==FIXTURE.read_bytes() and desc==before
    projection=result['projection']
    failed,=[r for r in projection['reloads'] if r['phase']=='RAISE']
    recovered,=[r for r in projection['reloads'] if r['phase']=='RETURN']
    a,b=failed['before']['bytes_sha256'],failed['after']['bytes_sha256']
    assert a!=b and recovered['before']['bytes_sha256']==b and recovered['after']['bytes_sha256']==a
    assert failed['refs'] and recovered['refs']
    assert any(c['reason']=='cpu_parameter_changed_during_failed_reload' for c in projection['violation_candidates'])
    assert projection['selected_request_chains_complete']
    assert projection['weights_contents_verified'] is projection['kv_kernel_reads_verified'] is projection['GPU_verified'] is False
    assert not result['current_target_binding_verified'] and not result['GPU_or_registered_producer_claimed']
    assert result['file_identity']['uid']>=0 and result['file_identity']['ino']>0


@pytest.mark.parametrize('allowed',[None,[]])
def test_original_python_aux_links_and_explicit_empty_list_are_separate_from_whole_supply(allowed):
    desc=descriptor()
    if allowed is not None: desc['allowed_auxiliary_sha256']=allowed
    _,result=subject.consume('SUP-03',desc)
    projection=result['projection']
    assert projection['selected_auxiliary_links_complete']
    assert all(r['config_content_match_observed'] and r['tokenizer_copy_lineage_observed'] for r in projection['requests'])
    assert all(r['config_reads_linked'] and r['tokenizer_reads_linked'] for r in projection['requests'])
    assert projection['auxiliary_policy_supplied'] is (allowed is not None)
    if allowed==[]:
        assert any(c['reason']=='unapproved_auxiliary_read_linked_to_actual_request' for c in projection['violation_candidates'])
    assert not projection['capture_complete'] and projection['diagnostic_codes']
    assert 'native_tokenizer_reads_unattributed' in projection['missing']
    assert not result['whole_question_complete']


@pytest.mark.parametrize('damage',['sha','scope','producer'])
def test_late_support_rejection_preserves_original_negative_and_judge(tmp_path,damage):
    desc=descriptor()
    if damage=='sha': desc['file_sha256']='0'*64
    elif damage=='scope': desc['capture_scope']['pod']['pod_uid']='EXAMPLE other historical capture'
    else:
        data=json.loads(FIXTURE.read_bytes()); data['producer']['source_sha256']='0'*64
        path=tmp_path/'EXAMPLE-invalid-producer.json';path.write_bytes(canonical_json(data))
        desc.update(path=str(path),file_sha256=sha256_hex(path.read_bytes()))
    draft=negative_draft();original=deepcopy(draft.observations)
    assert_fail(draft)
    assert subject.attach(draft,SimpleNamespace(id='LIF-06'),desc,tmp_path/'work') is draft
    assert draft.observations==original and draft.status==ExecStatus.ABORTED
    assert not draft.config['b_cpu_support']['accepted']
    assert draft.config['b_cpu_support']['input_error']['type']=='ValueError'
    assert_fail(draft)


def test_exclusive_output_collision_preserves_old_bytes_and_original_fail(tmp_path):
    work=tmp_path/'work';work.mkdir();old=work/'b_cpu_support_record.json';old.write_bytes(b'EXAMPLE preserve existing artifact')
    draft=negative_draft();original=deepcopy(draft.observations)
    subject.attach(draft,SimpleNamespace(id='LIF-06'),descriptor(),work)
    assert old.read_bytes()==b'EXAMPLE preserve existing artifact'
    assert draft.observations==original and draft.status==ExecStatus.ABORTED
    assert 'B_CPU_SUPPORT_ARTIFACT_UNAVAILABLE' in draft.cleanup_errors
    assert_fail(draft)


def test_disabled_option_has_no_artifact_or_original_state_change(tmp_path,monkeypatch):
    draft=negative_draft();original=draft.model_dump()
    monkeypatch.setattr(subject,'consume',lambda *a,**k:pytest.fail('disabled input must not be read'))
    assert subject.attach(draft,SimpleNamespace(id='LIF-06'),None,tmp_path/'absent') is draft
    assert draft.model_dump()==original and not (tmp_path/'absent').exists()


@pytest.mark.parametrize('damage',['symlink','bool-generation'])
def test_path_and_historical_generation_type_gaps_do_not_gain_current_identity(tmp_path,damage):
    desc=descriptor()
    if damage=='symlink':
        path=tmp_path/'alias';path.symlink_to(FIXTURE);desc['path']=str(path)
    else: desc['capture_scope']['pod']['restart_count']=True
    with pytest.raises(ValueError): subject.consume('SUP-03',desc)


def test_existing_two_registry_factories_to_exact_A_artifacts_EVD_RES_RPT(tmp_path,monkeypatch):
    # Original B controls are EXAMPLE transport doubles. A's old model/Pod is
    # not relaunched, and its record is not joined to these new target IDs.
    env,topology,world,sup_cfg=sup.inputs(tmp_path)
    old,old_env,_=lif.harness(monkeypatch)
    generations,_=lif.subject.load_generations({},old_env,lif.policy())
    for generation in generations.values(): generation.env=env
    old_init=lif.subject.LifecycleTransitionExecutor.__init__
    def lif_init(self,cfg,**kw):
        kw.update(runner=lambda *a:None,clock=old.clock,wait=old.wait)
        old_init(self,cfg,**kw)
        self._reader,self._probe,self._capture=old._reader,old._probe,old._capture
    monkeypatch.setattr(lif.subject.LifecycleTransitionExecutor,'__init__',lif_init)
    old_sup_init=sup.KubernetesTemplateSupplyExecutor.__init__
    def sup_init(self,cfg,**kw):
        kw['runner']=world.runner
        old_sup_init(self,cfg,**kw)
    monkeypatch.setattr(sup.KubernetesTemplateSupplyExecutor,'__init__',sup_init)
    nested=lif.policy()
    values={'transition_inputs_sha256':nested['transition_inputs_sha256'],
        **{f'transition_{p}_at':nested['transition_sample_at'][p] for p in lif.subject.PHASES},
        **{f'transition_{p}_generation':nested['transition_phase_generation'][p] for p in lif.subject.PHASES}}
    # @36: LIF-06 transition values are run-preparation values, not policy values (RB2).
    pol=policy(PACK.ref(),scope={'include':['LIF-06','SUP-03']},values={
        'LIF-06':{},'SUP-03':deepcopy(sup.VALUES)})
    store=Store(tmp_path/'store');put_pack(store,ROOT/'packs/PACK-BASELINE');store.put_many([env,pol])
    plan=select(PACK,[env],pol);store.put(plan)
    raw={'lifecycle_transition':{'cpu_support_input':descriptor()},
         'template_validation':{**sup_cfg,'cpu_support_input':descriptor()},**transition_table('LIF-06',values)}
    original=deepcopy(raw)
    with fixed_transition(values):
        evds=run_plan(store,PACK,plan,RunConfig(executors={'lifecycle_transition':'kubernetes','template_validation':'kubernetes'},adapter='vllm',raw=raw))
    assert raw==original and len(evds)==2
    for evd in evds:
        by_name={a.name:a for a in evd.artifacts}
        assert {'b_cpu_support_record.json','b_cpu_support.json'}<=set(by_name)
        record=(store.root/by_name['b_cpu_support_record.json'].path).read_bytes()
        assert record==FIXTURE.read_bytes() and sha256_hex(record)==FIXTURE_SHA
        report=json.loads((store.root/by_name['b_cpu_support.json'].path).read_bytes())
        assert report['historical_support_only'] and report['result']['accepted']
        assert not report['result']['current_target_binding_verified'] and not report['result']['whole_question_complete']
        assert evd.data_origin==DataOrigin.EXAMPLE
        if evd.check_id=='SUP-03':
            assert evd.observations['template_consumer_verified'].value is False
            assert evd.observations['supply_scope_complete'].value is False
    at=datetime.now(timezone.utc)
    results=judge_plan(store,PACK,plan,as_of=at)
    rpt=build_report(store,PACK,plan,as_of=at);store.put(rpt)
    assert len(results)==2 and all(r.verdicts.security.value=='UNKNOWN' for r in results)
    assert rpt.official is False
    refs=[o.ref().model_dump(mode='json') for o in [*evds,*results,rpt]]
    (tmp_path/'exact-new-support-refs.json').write_bytes(canonical_json({
        'scope':'new B consumption/Store/judge only; old A CPU model and V13 results not rerun',
        'refs':refs,'fixture_sha256':FIXTURE_SHA,'data_origin':'EXAMPLE','new_current_target_link':False}))
