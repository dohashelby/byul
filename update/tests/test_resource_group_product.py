"""EXAMPLE CLI evidence retains residual namespace references and closed gates."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import pytest
from typer.testing import CliRunner

from guard.adapters import resource_group_probe as probe
from guard.adapters import resource_recovery_probe as selected_probe
from guard.cli import app
from guard.core.judge import judge_evidence
from guard.core.run import _normalize
from guard.executors.registry import build_executor
from guard.models import DataOrigin, Evidence, canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime import resource_group_source
from guard.store import Store
from tests.builders import dummy_ref, policy,T0
from tests.test_a1_resource_recovery_product import inputs
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external
from tests.test_lifecycle_sensor import writer_factory
from tests.test_resource_group_probe import owned_child


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


@pytest.mark.parametrize(('later_unavailable', 'selection_case'), [
    (False, 'owner-only'), (True, 'owner-only'), (False, 'exact-selected-object'),
    (False, 'wrong-inode'), (False, 'mapping-only-after'),
])
def test_canonical_lif11_keeps_held_owned_namespace_receipts_from_real_producer(
        tmp_path, group, native_target, owned_child, later_unavailable, selection_case):
    child, scope = owned_child
    world, _, values, _ = inputs(tmp_path, group, native_target, 'retained', 'runtime-failure')
    selected = {'schema': selected_probe.SELECTION_SCHEMA, 'owners': [
        {**scope['owners'][0], 'rank': 0, 'require_absent': True}], 'resources': []}
    if selection_case != 'owner-only':
        source = probe.observe(scope, process_ids=[child.pid])
        mapped, = [row for row in source['references'] if row['fd'] is None]
        descriptors = [row['fd'] for row in source['references'] if row['kind'] == 'posix-shm'
                       and (row['device'], row['inode']) == (mapped['device'], mapped['inode'])
                       and row['fd'] is not None]
        selected['resources'] = [{'id': 'e' * 64, 'kind': 'posix-shm-fd', 'owner_id': scope['owners'][0]['id'],
            'fd': min(descriptors), 'device': mapped['device'],
            'inode': mapped['inode'] + (selection_case == 'wrong-inode'), 'require_absent': False}]
    values.update(recovery_selection_sha256=sha256_hex(canonical_json(selected)), recovery_expected_ranks=['0'],
        recovery_require_absent_kinds=['process'], recovery_phase='before-failure',
        recovery_deadline_basis='after-verified-failure')
    target_file = tmp_path / 'canonical-target.json'
    target_file.write_bytes(canonical_json(world.target))
    selection_file = tmp_path / 'canonical-selection.json'
    selection_file.write_bytes(canonical_json(selected))
    namespace_packets = []

    def runner(argv, **kwargs):
        request = json.loads(kwargs['input_bytes']) if 'input_bytes' in kwargs else {}
        if request.get('schema') == probe.SELECTION_SCHEMA:
            assert request == scope
            if namespace_packets and later_unavailable:
                raise OSError('EXAMPLE_NAMESPACE_NO_SECOND_READ')
            result = probe.observe(scope, process_ids=[child.pid])
            if namespace_packets and selection_case == 'mapping-only-after':
                # Synthetic projection input retaining the producer's exact map row.
                result['references'] = [row for row in result['references']
                                        if row['kind'] != 'posix-shm' or row['fd'] is None]
            namespace_packets.append(result)
        elif request.get('schema') == selected_probe.SELECTION_SCHEMA:
            assert request == selected
            result = selected_probe.observe(request)
        else:
            return world.runner(argv, **kwargs)
        return {'returncode': 0, 'stdout_bytes': canonical_json(result), 'stderr_bytes': b'',
            'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}

    executor = build_executor('resource_recovery', 'kubernetes', {'resource_recovery': {
        'target': str(target_file), 'prepared': str(group.manifest_path), 'selection': str(selection_file),
        'namespace_resources': True}})
    executor.runner = runner
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['LIF-11']
    draft = executor.run(check, group.env, values, object(), tmp_path / 'canonical-evidence')
    evd = Evidence(id='EVD-OWNED-NAMESPACE-EXAMPLE', data_origin=draft.data_origin, plan=dummy_ref('PLAN'),
        environment=group.env.ref(), check_id=check.id, side='single', execution={'status': draft.status},
        **_normalize(check, draft)).seal()
    observations = {key: field.value for key, field in evd.observations.items()}
    held = [row for row in observations['activation_steps'] if row['step'] == 'owned-namespace-held-resource-census']
    assert [row['stage'] for row in held] == (['before'] if later_unavailable else ['before', 'after'])
    for row, packet in zip(held, namespace_packets, strict=True):
        assert row['packet_sha256'] == sha256_hex(canonical_json(packet))
        assert row['target_sha256'] == values['recovery_target_sha256']
        assert row['selection_sha256'] == sha256_hex(canonical_json(scope))
        assert row['probe_source_sha256'] == resource_group_source.GROUP_SHA256
        assert row['matched_owner_ids'] == [scope['owners'][0]['id']]
        assert {reference['kind'] for reference in row['held_references']} == {'socket', 'posix-shm'}
        source_forms = {}
        for source in packet['references']:
            digest = sha256_hex(canonical_json([source['kind'], source['device'], source['inode']]))
            form = 'memory-mapping' if source['fd'] is None else 'file-descriptor'
            source_forms.setdefault((source['kind'], digest), set()).add(form)
        map_only = selection_case == 'mapping-only-after' and row['stage'] == 'after'
        expected_forms = {'memory-mapping'} if map_only else {'file-descriptor', 'memory-mapping'}
        assert expected_forms in source_forms.values()
        assert row['held_references'] == [
            {'owner_id': scope['owners'][0]['id'], 'kind': kind, 'resource_sha256': digest, 'reference_forms': sorted(forms)}
            for (kind, digest), forms in sorted(source_forms.items())
        ]
        if selection_case in {'exact-selected-object', 'mapping-only-after'}:
            resource, = selected['resources']
            digest = sha256_hex(canonical_json(['posix-shm', resource['device'], resource['inode']]))
            assert row['resource_selection_sha256'] == values['recovery_selection_sha256']
            assert row['selected_posix_shm_mappings'] == [
                {'selected_resource_id': resource['id'], 'owner_id': resource['owner_id'], 'resource_sha256': digest}]
        else:
            assert 'selected_posix_shm_mappings' not in row and 'resource_selection_sha256' not in row
        assert not row['global_release_verified'] and not row['namespace_scan_complete']
    assert ('owned_namespace_held_reference_census_incomplete' in observations['activation_missing']) is later_unavailable
    assert observations['activation_violations'] is None and observations['activation_scope_complete'] is False
    result = judge_evidence(check, evd, policy(pack.ref()), utcnow())
    assert evd.data_origin is DataOrigin.EXAMPLE and result.verdicts.security.value == 'UNKNOWN'
