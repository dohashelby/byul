"""Real producer shape for normal -> terminal boot -> refusal -> fresh recovery."""
from copy import deepcopy
import hashlib,json

import pytest
from guard.executors import lab_activation as actor
import importlib.util
from pathlib import Path
_spec=importlib.util.spec_from_file_location('_primary_binding_fixture',Path(__file__).with_name('test_lab_activation_initialization_binding.py'))
_binding=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_binding)
Trial,engine=_binding.Trial,_binding.engine


class Roundtrip(Trial):
    def __init__(self):
        super().__init__();self.stage='before';self.cfg={**self.cfg,'kubectl_context':'owned-lab','restore_timeout_ms':240000}
        self.now=0.
    def actual_pod(self):
        p=engine();p['metadata']['name']='engine-'+self.stage;p['metadata']['uid']='uid-'+self.stage
        p['metadata']['labels']['byul.lab/generation']='faulty' if self.stage=='bad' else 'good'
        p['spec']['containers'][0]['image']='registry/engine@sha256:'+'f'*64
        p['spec']['containers'][0]['env']=[{'name':'PRIVATE_CANARY','value':'never-export-this'}]
        p['spec']['containers'][0]['args']=['never-export-this']
        p['status']['phase']='Running'
        if self.stage!='bad':
            status=p['status']['containerStatuses'][0];status.update(containerID='containerd://'+('c' if self.stage=='before' else 'd')*64,
                ready=True,restartCount=0,state={'running':{'startedAt':'2026-11-06T00:00:02Z'}},lastState={})
            p['status']['conditions']=[{'type':'Ready','status':'True'}]
        return p
    def _pods(self):return [self.actual_pod()]
    def apply(self,name):
        self.stage='bad' if name=='faulty_manifest' else 'after';self.mutated=True
    def _kubectl(self,*args,**kwargs):
        if args[:2]==('get','pod') and args[2].startswith('engine-'):return json.dumps(self.actual_pod()).encode()
        return super()._kubectl(*args,**kwargs)
    def requests(self,items,**options):
        self.requests_sent.append((items,options));item,=items
        if not item.get('headers'):return [{'outcome':503,'complete':True}]
        header=item['headers']['X-Request-Id'];bad=self.stage=='bad'
        return [{'outcome':'error' if bad else 200,'complete':not bad,'bytes':0 if bad else 42,
            'sha256':hashlib.sha256(b'').hexdigest() if bad else 'a'*64,
            'model':None if bad else 'model','normal_shape_verified':not bad,
            'external_request_sha256':hashlib.sha256(header.encode()).hexdigest(),
            'request_body_sha256':hashlib.sha256(json.dumps(item['body']).encode()).hexdigest(),
            'error_kind':'connection-refused' if bad else None,'error_stage':'connect' if bad else None,
            'connection_established':not bad,'request_sent':not bad,'errno':111 if bad else None,'connect_refused':bad}]


def capture_roundtrip():
    lab=Roundtrip();actor._partial_start(lab,{'init_preparation_deadline_ms':10000});assert lab.restore()
    return lab


def test_actual_primary_export_contains_original_attempt_body_identity_and_order():
    lab=capture_roundtrip();case=lab.initialization_case
    assert lab.missing==[] and lab.normal==[True,True]
    assert set(case)=={'context','sequence_scope','normal_before','failure','admission','recovery'}
    assert case['failure']['log_receipt']['previous'] is True
    assert case['failure']['pod_before_log']==case['failure']['pod_after_log']
    stages=[case[k] for k in ('normal_before','failure','admission','recovery')]
    assert all(max(a['sequence'].values())<min(b['sequence'].values()) for a,b in zip(stages,stages[1:]))
    requests=[case[k]['direct_request'] for k in ('normal_before','admission','recovery')]
    assert len({r['request_id_sha256'] for r in requests})==3
    assert len({r['body_sha256'] for r in requests})==1
    assert requests[1]['http']['connect_refused'] is True and requests[1]['http']['request_sent'] is False
    assert requests[2]['http']['normal_shape_verified'] is True
    assert case['normal_before']['pod_before_request']['metadata']['uid']!=case['recovery']['pod_before_request']['metadata']['uid']
    assert b'never-export-this' not in json.dumps(case).encode()


def test_original_independent_ready_failure_survives_unavailable_normal_baseline(monkeypatch,tmp_path):
    for name in ("faulty","good","update"):(tmp_path/(name+".yaml")).write_text(name)
    monkeypatch.chdir(tmp_path)
    spec=importlib.util.spec_from_file_location('_primary_failure_fixture',Path(__file__).with_name('test_lab_activation.py'))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    facts,_=fixture.scenario('LIF-09',faulty_ready=True)
    assert any(row['code']=='PARTIAL_INITIALIZATION_REPORTED_READY' for row in facts['activation_violations'])
    assert facts['activation_scope_complete'] is False


def test_missing_recovery_shape_is_not_a_successful_repaired_boot():
    lab=Roundtrip();actor._partial_start(lab,{'init_preparation_deadline_ms':10000})
    original=lab.requests
    def incomplete(items,**options):
        rows=original(items,**options)
        if lab.stage=='after':rows[0]['normal_shape_verified']=False
        return rows
    lab.requests=incomplete
    assert lab.restore() is False and lab.initialization_case['recovery'] is None


def test_product_pipeline_calls_terminal_validator_after_restore(monkeypatch):
    import sys
    from types import SimpleNamespace
    lab=Roundtrip();lab.cfg['initialization_failure_kind']='model';calls=[]
    def project(receipt,*,expected):
        assert lab.stage=='after' and receipt['recovery']['direct_request']['http']['normal_shape_verified'] is True
        assert expected=={'context':'owned-lab','namespace':'lab','namespace_uid':'namespace-id','container':'engine',
            'probe_container':'probe','port':8000,'model':'model','failure_kind':'model',
            'failure_marker_sha256':hashlib.sha256(lab.cfg['failure_marker'].encode()).hexdigest()}
        calls.append(receipt)
        return {'activation_steps':[{'step':'strict-terminal-replay'}],'activation_missing':[],
            'activation_violations':[],'activation_scope_complete':True}
    monkeypatch.setitem(sys.modules,'guard.executors.initialization_terminal_case',SimpleNamespace(project_terminal_case=project))
    monkeypatch.setattr(actor,'Lab',lambda *_args,**_kwargs:lab)
    facts,_=actor.run_scenario('LIF-09',lab.cfg,{'init_preparation_deadline_ms':10000})
    assert len(calls)==1 and facts['activation_scope_complete'] is True
    assert facts['activation_steps'][-1]['step']=='strict-terminal-replay'


def test_missing_canonical_module_keeps_raw_case_and_complete_false(monkeypatch):
    import sys
    lab=Roundtrip();lab.cfg['initialization_failure_kind']='model'
    monkeypatch.setitem(sys.modules,'guard.executors.initialization_terminal_case',None)
    monkeypatch.setattr(actor,'Lab',lambda *_args,**_kwargs:lab)
    facts,_=actor.run_scenario('LIF-09',lab.cfg,{'init_preparation_deadline_ms':10000})
    assert facts['activation_scope_complete'] is False
    assert lab.initialization_case['recovery'] is not None and lab.stage=='after'
    assert any(gap.startswith('INITIALIZATION_CANONICAL_UNAVAILABLE:') for gap in facts['activation_missing'])


def test_canonical_unknown_cannot_remove_an_independent_failure(monkeypatch):
    import sys
    from types import SimpleNamespace
    lab=Roundtrip();lab.cfg['initialization_failure_kind']='model'
    def project(_receipt,*,expected):
        return {'activation_steps':[],'activation_missing':['original-canonical-gap'],
            'activation_violations':[],'activation_scope_complete':False}
    def scenario(instance,_values):instance.normal.append(True);instance.violate('INDEPENDENT_ORIGINAL_FAILURE')
    monkeypatch.setitem(sys.modules,'guard.executors.initialization_terminal_case',SimpleNamespace(project_terminal_case=project))
    monkeypatch.setattr(actor,'Lab',lambda *_args,**_kwargs:lab)
    monkeypatch.setitem(actor.SCENARIOS,'LIF-09',scenario)
    facts,_=actor.run_scenario('LIF-09',lab.cfg,{})
    assert facts['activation_scope_complete'] is False
    assert facts['activation_violations']==[{'code':'INDEPENDENT_ORIGINAL_FAILURE'}]
    assert 'original-canonical-gap' in facts['activation_missing']


def test_official_initialization_entry_pins_both_manifests_before_mutation(monkeypatch,tmp_path):
    import sys
    from types import SimpleNamespace
    from guard.models import Policy
    from tools import b_lif08_fault_control as tool
    lab=Roundtrip();lab.cfg['initialization_failure_kind']='model'
    for key in ('good_manifest','faulty_manifest'):
        path=tmp_path/(key+'.yaml');path.write_text(key)
        lab.cfg[key]=str(path);lab.cfg[key+'_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    def project(receipt,*,expected):
        assert receipt['recovery'] and lab.stage=='after'
        return {'activation_steps':[],'activation_missing':[],'activation_violations':[],'activation_scope_complete':True}
    module=SimpleNamespace(project_terminal_case=project,__file__=actor.__file__)
    monkeypatch.setitem(sys.modules,'guard.executors.initialization_terminal_case',module)
    monkeypatch.setattr(actor,'Lab',lambda *_args,**_kwargs:lab)
    policy=Policy(id='POL-test',data_origin='DECLARED',status='DRAFT',pack={
        'kind':'PACK','id':'PACK-test','revision':1,'sha256':'a'*64},values={'LIF-09':{'init_preparation_deadline_ms':10000}}).seal()
    bad={**lab.cfg,'faulty_manifest_sha256':'0'*64}
    with pytest.raises(ValueError,match='MANIFEST_HASH'):tool.run_initialization(bad,policy,'f'*40)
    assert lab.stage=='before' and lab.mutated is False
    result=tool.run_initialization(lab.cfg,policy,'f'*40)
    assert result['activation']['activation_scope_complete'] is True
    assert result['native_stage_ack_observed'] is False and result['receipt']['recovery']
    assert result['manifest_refs']['faulty_manifest']==lab.cfg['faulty_manifest_sha256']


def test_node_image_option_is_forwarded_to_actual_reader_without_redefining_proof(monkeypatch):
    import guard.executors.kubernetes_lifecycle_capture as reader_module
    import guard.executors.lifecycle_session as session_module
    spec=importlib.util.spec_from_file_location('_primary_control_fixture',Path(__file__).with_name('test_lab_activation_control_workflow.py'))
    fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
    cfg={**fixture.BASE,'fault_control_node_image_verification':'local-containerd-v1'}
    lab=actor.Lab(cfg);seen=[]
    class Reader:
        owner_identity=None;read_file=None;image_check=None
        binding={"engine":{"container":"engine","image":"pinned"}}
        def __init__(self,*_args,**kwargs):seen.append(kwargs)
        def snapshot(self):pass
        def report(self):return {'collection_gap':False,'node_runtime_image_verified':False}
    monkeypatch.setattr(reader_module,'KubernetesLifecycleReader',Reader)
    monkeypatch.setattr(session_module,'capture_session',lambda *_args,**_kwargs:{'image_digest_verified':False})
    result=actor._control_capture(lab,{'target':{},'env':None,'prepared':{},'binding':{'engine':{'container':'engine','image':'pinned'}}})
    assert seen[0]['node_image_verification']=='local-containerd-v1'
    assert result['packet']['image_digest_verified'] is False
