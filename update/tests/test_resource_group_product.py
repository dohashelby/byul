"""EXAMPLE CLI evidence retains residual namespace references and closed gates."""
from copy import deepcopy
import json
import sys
import time

import pytest
from typer.testing import CliRunner

from guard.adapters import resource_group_probe as probe
from guard.cli import app
from guard.models import canonical_json
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import policy,T0
from tests.test_a1_resource_recovery_product import inputs
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external
from tests.test_lifecycle_sensor import writer_factory


@pytest.mark.parametrize('mode',['retained-alias','collection-error','incomplete','owner-clock-invalid'])
def test_namespace_census_survives_native_evd_report_export_with_selected_absence(tmp_path,group,native_target,monkeypatch,mode):
    world,selected,values,pack=inputs(tmp_path,group,native_target,'released-descriptors','runtime-failure')
    original=world.runner;calls=[]
    def runner(argv,**kwargs):
        scope=json.loads(kwargs['input_bytes'])if 'input_bytes'in kwargs else{}
        if scope.get('schema')!=probe.SELECTION_SCHEMA:return original(argv,**kwargs)
        calls.append(scope)
        if mode=='collection-error':raise OSError('PRIVATE_GROUP_FAILURE')
        now=time.time_ns();mono=time.monotonic_ns();observer={**scope['owners'][0]['context'],'pid':99}
        contexts=[o['context']for o in scope['owners']]+[observer]
        result={'schema':probe.SCHEMA,'selection_sha256':probe.hashlib.sha256(probe.canonical(scope)).hexdigest(),
            'observer':observer,'started_wall_ns':now,'finished_wall_ns':now,'started_monotonic_ns':mono,'finished_monotonic_ns':mono,
            'namespace_scan_complete':mode!='incomplete','explicit_pid_subset':False,
            'processes':[{'pid':c['pid'],'context':deepcopy(c),'parent_pid':1,'stable':True,'generation_stable':True,'diagnostic_codes':[]}for c in contexts],
            'owners':[{'id':o['id'],'status':'match'}for o in scope['owners']],
            'references':[{'pid':42 if len(calls)==1 else 99,'start_ticks':123,'kind':'socket','device':1,'inode':200,'fd':7}],
            'named_shm':[],'diagnostic_codes':['RESOURCE_GROUP_SCAN_INCOMPLETE']if mode=='incomplete'else[],
            'unix_filesystem_paths_removed_verified':False,'gpu_release_verified':False,'whole_group_recovery_verified':False,
            'producer_authenticated':False,'resource_contents_read':False,'processes_signalled':False}
        if mode=='owner-clock-invalid':result['started_wall_ns']+=10**15;result['finished_wall_ns']+=10**15
        return {'returncode':0,'stdout_bytes':canonical_json(result),'capture':{'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}}
    monkeypatch.setattr(http,'capture_pipes',runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',runner)
    pol=policy(pack.ref(),scope={'include':['LIF-11']},values={'LIF-11':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target),('selection',selected)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    config=tmp_path/'guard.toml'
    config.write_text('[executors]\nresource_recovery="kubernetes"\n[adapter]\nname="vllm"\n[resource_recovery]\nnamespace_resources=true\n'
        +'target='+json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))
        +'\nselection='+json.dumps(str(tmp_path/'selection.json'))+'\n')
    cli=CliRunner();common=['--store',str(tmp_path/'store'),'--config',str(config)]
    def invoke(args):
        options=(common[:2]if args[1]=='export'else[])if args[0]=='evidence'else common
        result=cli.invoke(app,list(map(str,args))+options);assert result.exit_code==0,(result.output,result.exception)
    invoke(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json'])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    invoke(['run','--plan',plan.id,'--pack',pack.path]);invoke(['judge','--plan',plan.id,'--as-of',T0.isoformat()])
    invoke(['report','--plan',plan.id,'--as-of',T0.isoformat()]);invoke(['verify'])
    store=Store.read_only(tmp_path/'store');evidence,=store.iter_kind('EVD');result,=store.iter_kind('RES')
    assert evidence.data_origin.value=='EXAMPLE'and result.verdicts.functional.value=='UNKNOWN'
    assert evidence.observations['recovery_selected_absence_observed'].value is True
    assert evidence.observations['recovery_global_resources_released'].value is False
    artifact,=evidence.artifacts;summary=json.loads((tmp_path/'store'/artifact.path).read_text().splitlines()[-1])
    assert len(summary['namespace_resource_censuses'])==2 and len(calls)==2
    if mode in {'retained-alias','incomplete'}:
        projection=summary['namespace_resource_projection']
        assert projection['remaining_kernel_objects']==[{'kind':'socket','device':1,'inode':200}]
        assert not projection['selected_kernel_reference_absence_observed']and not projection['whole_group_recovery_verified']
    else:assert summary['namespace_resource_projection']is None
    # The reused native rank fixture qualifies CPython 3.12.3 only. Other
    # ABIs must preserve its rejection while retaining the independent census.
    native_fixture_qualified=sys.implementation.name=='cpython'and sys.version_info[:3]==(3,12,3)
    assert evidence.execution.status.value==('COMPLETED'if mode=='retained-alias'and native_fixture_qualified else'ABORTED')
    if mode=='retained-alias'and not native_fixture_qualified:
        assert evidence.execution.abort_reason=='RESOURCE_RECOVERY_PROBE_UNAVAILABLE'
        assert 'RESOURCE_RECOVERY_PROBE_UNAVAILABLE'in summary['diagnostic_codes']
    assert b'PRIVATE_GROUP_FAILURE'not in canonical_json(summary)
    invoke(['evidence','export','--output',tmp_path/'bundle.zip'])
    invoke(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert restored.resolve(evidence.ref()).to_dict()==evidence.to_dict()
