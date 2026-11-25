"""EXAMPLE node GPU metadata survives canonical EVD/RES/RPT/export/restore."""
from copy import deepcopy
import json
from pathlib import Path
import time
import pytest
from typer.testing import CliRunner
from guard.adapters import gpu_process_probe as gpu
from guard.cli import app
from guard.models import canonical_json,load_pack,sha256_hex
from guard.runtime import kubernetes_http as http,gpu_process_source
from guard.store import Store
from tests.builders import policy,T0
from tests.test_a1_resource_recovery_product import inputs
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external
from tests.test_lifecycle_sensor import writer_factory
from tests.test_gpu_process_probe import packet


@pytest.mark.parametrize('bad',[
    {'node_gpu_processes':1},{'node_gpu_processes':'true'},
    {'node_gpu_processes':False,'node_gpu_containerd_address':'/run/k3s/containerd/containerd.sock'},
    {'node_gpu_processes':True,'node_gpu_containerd_address':'/tmp/other.sock'},
    {'node_gpu_processes':True,'node_gpu_containerd_address':True}])
def test_invalid_node_gpu_config_is_refused_before_file_or_transport_access(tmp_path,group,native_target,monkeypatch,bad):
    from guard.executors.kubernetes_resource_recovery import KubernetesResourceRecoveryExecutor
    world,selected,values,pack=inputs(tmp_path,group,native_target,'released-descriptors','runtime-failure')
    def forbidden(*args,**kwargs):raise AssertionError('no file or transport access for an invalid GPU configuration')
    monkeypatch.setattr(Path,'open',forbidden)
    monkeypatch.setattr('guard.executors.kubernetes_resource_recovery.KubernetesRecoveryHTTP',forbidden)
    executor=KubernetesResourceRecoveryExecutor(bad)
    with pytest.raises(ValueError,match='RESOURCE_RECOVERY_CONFIGURATION_INVALID'):
        executor.run(pack.checks['LIF-11'],group.env,values,object(),tmp_path/'unused')


def gpu_read(owners,engine,index,mode):
    scope={'schema':gpu.SELECTION_SCHEMA,
        'anchor':{'host_pid':9000,'start_ticks':9000,'pid_namespace':owners[0]['context']['pid_namespace']},'owners':deepcopy(owners)}
    p=packet(after=index==1);ctx=deepcopy(owners[0]['context'])
    anchor={**ctx,'pid':9000,'start_ticks':9000}
    observer={**ctx,'pid':11000,'start_ticks':11000,'uid':0,'pid_namespace':99}
    rows=[{'host_pid':9000,'context':anchor,'nspid':[9000,1],'handles':[],'stable':True,'diagnostic_codes':[]}]
    devices=[];compute=[]
    for i,owner in enumerate(owners):
        uuid='GPU-00000000-0000-0000-0000-'+str(i+1).zfill(12)
        held = index == 0 or mode == 'same-owner-references'
        memory = 12 + i + 10 * index
        devices.append({'uuid':uuid,'major':195,'minor':i+3,'memory_used_mib':100 if held else 0})
        if held:
            pid=10000+i;current={**owner['context'],'pid':pid}
            rows.append({'host_pid':pid,'context':current,'nspid':[pid,owner['context']['pid']],
                'handles':[{'fd':7,'major':195,'minor':i+3}],'stable':True,'diagnostic_codes':[]})
            compute.append({'host_pid':pid,'uuid':uuid,'memory_used_mib':memory})
    rows.append({'host_pid':11000,'context':observer,'nspid':[11000],'handles':[],'stable':True,'diagnostic_codes':[]})
    if index==1 and mode=='retained-handle':
        rows.append({'host_pid':12000,'context':{**observer,'pid':12000,'start_ticks':12000},'nspid':[12000],
            'handles':[{'fd':9,'major':195,'minor':3}],'stable':True,'diagnostic_codes':[]})
    p.update(observer=observer,devices=devices,compute=compute,compute_confirm=deepcopy(compute),processes=rows,
        selection_sha256=sha256_hex(canonical_json(scope)),pid_inventory_before=[r['host_pid']for r in rows],
        pid_inventory_after=[r['host_pid']for r in rows])
    if index==1 and mode=='partial':p.update(host_inventory_complete=False,explicit_pid_subset=True,
        diagnostic_codes=['GPU_PROCESS_EXPLICIT_PID_SUBSET'])
    gpu.validate_packet(p,scope)
    receipt={'selection':scope,'engine_binding_sha256':sha256_hex(canonical_json(engine)),
        'packet_sha256':sha256_hex(canonical_json(p)),'program_sha256':sha256_hex(gpu_process_source.source().encode()),
        'selection_sha256':p['selection_sha256'],
        'data_origin':'EXAMPLE','actual_gpu_source_qualified':False,'producer_registered':False,'whole_gpu_release_verified':False}
    if mode=='wrong-receipt':receipt['engine_binding_sha256']='0'*64
    return {'schema':'guard-owned-node-gpu-process-read/1','collected':True,'owner_binding_verified':True,
        'clock_window_verified':True,'packet':p,'receipt':receipt,'diagnostic_codes':[]}


@pytest.mark.parametrize('mode',['absent-references','retained-handle','partial','final-collection-error','wrong-receipt'])
def test_optional_node_gpu_references_survive_product_artifacts_without_full_gpu_recovery(tmp_path,group,native_target,monkeypatch,mode):
    world,selected,values,pack=inputs(tmp_path,group,native_target,'released-descriptors','runtime-failure')
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    calls=[]
    def capture(reader,owners,**kwargs):
        assert kwargs=={'containerd_address':'/run/k3s/containerd/containerd.sock'}
        index=len(calls);calls.append(True)
        if index==1 and mode=='final-collection-error':raise OSError('PRIVATE_GPU_FAILURE')
        return gpu_read(owners,reader.binding['engine'],index,mode)
    monkeypatch.setattr('guard.executors.node_gpu_process.collect_for_reader',capture)
    pol=policy(pack.ref(),scope={'include':['LIF-11']},values={'LIF-11':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target),('selection',selected)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    config=tmp_path/'guard.toml'
    config.write_text('[executors]\nresource_recovery="kubernetes"\n[adapter]\nname="vllm"\n[resource_recovery]\n'
        +'node_gpu_processes=true\nnode_gpu_containerd_address="/run/k3s/containerd/containerd.sock"\n'
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
    assert len(calls)==2 and len(summary['node_gpu_process_censuses'])==2
    projection=summary['node_gpu_process_projection']
    if mode in{'absent-references','retained-handle','partial'}:
        assert projection['selected_gpu_reference_absence_observed']is(mode=='absent-references')
        assert projection['gpu_release_verified']is projection['whole_group_recovery_verified']is False
        if mode=='retained-handle':assert projection['remaining_device_handles']and not projection['remaining_compute']
    else:assert projection is None
    if mode in{'partial','final-collection-error','wrong-receipt'}:assert evidence.execution.status.value=='ABORTED'
    if mode=='final-collection-error':assert summary['node_gpu_process_censuses'][0]['packet']is not None
    assert b'PRIVATE_GPU_FAILURE'not in canonical_json(summary)
    invoke(['evidence','export','--output',tmp_path/'bundle.zip'])
    invoke(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert restored.resolve(evidence.ref()).to_dict()==evidence.to_dict()


@pytest.mark.parametrize(('mode', 'stages'), [
    ('absent-references', ['before']), ('same-owner-references', ['before', 'after']),
    ('late-after', ['before']), ('unbound-after', ['before']),
    ('inconsistent-wall-after', ['before']), ('changed-memory-after', ['before']), ('wrong-receipt', []),
])
def test_canonical_lif11_retains_only_in_window_owned_positive_gpu_references(
        tmp_path, group, native_target, monkeypatch, mode, stages):
    from guard.adapters.engine_provider import PROVIDERS
    from guard.core.run import _normalize
    from guard.executors.kubernetes_resource_recovery import KubernetesResourceRecoveryExecutor

    world, selected, values, _ = inputs(tmp_path, group, native_target, 'released-descriptors', 'runtime-failure')
    captures = []
    wall_offset_ns = time.time_ns()

    def capture(reader, owners, **options):
        index = len(captures)
        fixture_mode = 'same-owner-references' if mode in {
            'late-after', 'unbound-after', 'inconsistent-wall-after', 'changed-memory-after'} else mode
        record = gpu_read(owners, reader.binding['engine'], index, fixture_mode)
        offset = wall_offset_ns + (3_600_000_000_000 if index == 1 and mode == 'late-after' else 0)
        for key in ('started_wall_ns', 'finished_wall_ns'):
            record['packet'][key] += offset
        if index == 1 and mode == 'inconsistent-wall-after':
            record['packet']['finished_wall_ns'] += 101_000_000
            record['packet'].update(host_inventory_complete=False, diagnostic_codes=['GPU_PROCESS_CLOCK_UNVERIFIED'])
            gpu.validate_packet(record['packet'], record['receipt']['selection'])
            assert record['clock_window_verified'] is True and record['owner_binding_verified'] is True
        if index == 1 and mode == 'changed-memory-after':
            record['packet']['compute_confirm'][0]['memory_used_mib'] += 1
            record['packet'].update(host_inventory_complete=False, diagnostic_codes=['GPU_PROCESS_NVML_INVENTORY_CHANGED'])
            gpu.validate_packet(record['packet'], record['receipt']['selection'])
        record['receipt']['packet_sha256'] = sha256_hex(canonical_json(record['packet']))
        if index == 1 and mode == 'unbound-after':
            record['owner_binding_verified'] = False
        captures.append(record)
        return record

    monkeypatch.setattr('guard.executors.node_gpu_process.collect_for_reader', capture)
    target, selection_path = tmp_path / 'target.json', tmp_path / 'selection.json'
    target.write_bytes(canonical_json(world.target))
    selection_path.write_bytes(canonical_json(selected))
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['LIF-11']
    executor = KubernetesResourceRecoveryExecutor({'target': str(target), 'selection': str(selection_path),
        'prepared': str(group.manifest_path), 'node_gpu_processes': True}, runner=world.runner)
    draft = executor.run(check, group.env, values, PROVIDERS[group.env.engine.name].adapter(), tmp_path / 'work')
    observations = _normalize(check, draft)['observations']
    held = [step for step in observations['activation_steps']['value']
            if step['step'] == 'owned-node-held-gpu-references']
    assert [step['stage'] for step in held] == stages
    assert observations['activation_scope_complete']['value'] is False
    assert observations['activation_violations']['value'] is None and draft.data_origin.value == 'EXAMPLE'
    for step in held:
        index = 0 if step['stage'] == 'before' else 1
        record = captures[index]
        assert step['packet_sha256'] == record['receipt']['packet_sha256']
        assert step['selection_sha256'] == record['receipt']['selection_sha256']
        assert not step['gpu_release_verified'] and not step['producer_authenticated']
        expected = [{'owner_id': owner['id'],
                     'device_sha256': sha256_hex(canonical_json([values['recovery_target_sha256'], device['uuid']])),
                     'process_memory_used_mib': 12 + i + 10 * index}
                    for i, (owner, device) in enumerate(zip(selected['owners'], record['packet']['devices'], strict=True))]
        assert step['compute_and_device_handle_references'] == expected
        serialized = canonical_json(step).decode()
        assert 'host_pid' not in serialized and 'GPU-' not in serialized and 'nspid' not in serialized
