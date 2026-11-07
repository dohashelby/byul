import hashlib
import os
from pathlib import Path
import pytest
from guard.adapters import vllm_profiler_sensor
from guard.collectors.lifecycle import prepare_assets,load_prepared
from tools.lif14_stop_restart_prepare import digest_file,render,reserve_preparation_output,prepare_runtime_layout


def template():
    return {'metadata':{'namespace':'old','labels':{'guard.run':'old'}},
        'spec':{'template':{'metadata':{'labels':{'guard.run':'old'}},'spec':{
            'nodeName':'old','nodeSelector':{'kubernetes.io/hostname':'old'},'containers':[{
                'image':'example.invalid/unchanged@sha256:'+'a'*64,'args':['--tensor-parallel-size','1','--profiler-config',
                    '{"profiler":"torch","torch_profiler_dir":"/tmp/trace","torch_profiler_with_stack":false,"ignore_frontend":true}'],
                'volumeMounts':[{'name':'trace','mountPath':'/tmp/trace'}]}],
            'volumes':[{'name':name,'hostPath':{'path':'/old'}} for name in ('sensor','data','trace','model','template')]}}}}


def test_render_binds_new_native15_trace_session_without_deploying_or_changing_image(tmp_path):
    seed=template();doc=render(seed,{'session':'a'*32},tmp_path,namespace='byul-lif14',run_id='run-1',
        node='actual-node-input',model=Path('/owned/model'),chat=Path('/owned/chat'))
    spec=doc['spec']['template']['spec'];engine,=spec['containers']
    assert spec['nodeName']=='actual-node-input' and 'nodeSelector' not in spec
    assert '/guard-profiler-traces/'+'a'*32 in engine['args'][-1]
    assert engine['image']==seed['spec']['template']['spec']['containers'][0]['image']
    assert engine['args'][:2]==['--tensor-parallel-size','1']
    assert seed['metadata']['namespace']=='old' and not list(tmp_path.iterdir())


def test_real_file_hash_and_symlink_denial(tmp_path):
    file=tmp_path/'input';file.write_bytes(b'owned-input')
    assert digest_file(file)==(11,hashlib.sha256(b'owned-input').hexdigest())
    link=tmp_path/'link';link.symlink_to(file)
    with pytest.raises(ValueError,match='PREPARATION_INPUT_SYMLINK'):digest_file(link)


def test_unknown_profiler_settings_rejected(tmp_path):
    doc=template();doc['spec']['template']['spec']['containers'][0]['args'][-1]='{"profiler":"other"}'
    with pytest.raises(ValueError,match='PREPARATION_PROFILER_TEMPLATE_CHANGED'):
        render(doc,{'session':'a'*32},tmp_path,namespace='n',run_id='r',node='actual-node',model=Path('/m'),chat=Path('/c'))


def test_parent_segments_cannot_escape_output_protection(tmp_path):
    protected=tmp_path/'source';protected.mkdir()
    (tmp_path/'intermediate').mkdir()
    output=tmp_path/'intermediate/../source/forbidden'
    # The old lexical absolute()/is_relative_to() guard missed this spelling.
    assert not output.absolute().is_relative_to(protected)
    with pytest.raises(ValueError):reserve_preparation_output(output,protected=[protected])
    assert not (protected/'forbidden').exists() and not list(protected.iterdir())


def test_other_git_workspace_and_input_links_are_not_created_in(tmp_path):
    other=tmp_path/'other';other.mkdir();(other/'.git').write_text('EXAMPLE linked-worktree marker')
    with pytest.raises(ValueError,match='PREPARATION_OUTPUT_IN_GIT_WORKSPACE'):
        reserve_preparation_output(other/'forbidden',protected=[])
    assert not (other/'forbidden').exists()
    link=tmp_path/'link';link.symlink_to(tmp_path/'ordinary')
    with pytest.raises(ValueError):reserve_preparation_output(link/'forbidden',protected=[])
    assert not (tmp_path/'ordinary').exists()


def test_fresh_output_is_canonical_private_and_does_not_overwrite(tmp_path):
    source=tmp_path/'source';source.mkdir();(tmp_path/'intermediate').mkdir()
    output=reserve_preparation_output(tmp_path/'intermediate/../fresh',protected=[source])
    assert output==tmp_path/'fresh' and output.stat().st_mode&0o777==0o700
    with pytest.raises(ValueError):reserve_preparation_output(output,protected=[source])


def test_real_native15_producer_layout_preserves_assets_and_supplies_nested_mountpoint(tmp_path):
    repo=Path(__file__).resolve().parents[2]
    output=reserve_preparation_output(tmp_path/'prepared',protected=[repo])
    prepared=prepare_assets(output/'prepared15',repo,profile=vllm_profiler_sensor.PROFILE)
    original={p.name:p.read_bytes() for p in (output/'prepared15').iterdir()}
    prepare_runtime_layout(output,prepared)
    mountpoint=output/'prepared15/data'
    assert mountpoint.is_dir() and not list(mountpoint.iterdir())
    for directory in (mountpoint,output/'data',output/'data'/prepared['session'],output/'trace'):
        assert not directory.is_symlink()
        assert directory.stat().st_uid==os.geteuid() and directory.stat().st_mode&0o777==0o700
    assert not (output/'trace'/prepared['session']).exists()
    assert load_prepared(output/'prepared15/manifest.json')==prepared
    assert {name:(output/'prepared15'/name).read_bytes() for name in original}==original
    with pytest.raises(FileExistsError):prepare_runtime_layout(output,prepared)
    assert not list(mountpoint.iterdir())


@pytest.mark.parametrize('kind',['file','directory','symlink'])
def test_layout_rejects_existing_nested_mountpoint_without_overwriting(tmp_path,kind):
    output=tmp_path/'output';output.mkdir(mode=0o700)
    sensor=output/'prepared15';sensor.mkdir(mode=0o700)
    other=tmp_path/'other';other.mkdir();(other/'retained').write_bytes(b'original')
    point=sensor/'data'
    if kind=='file':point.write_bytes(b'original')
    elif kind=='directory':point.mkdir();(point/'retained').write_bytes(b'original')
    else:point.symlink_to(other,target_is_directory=True)
    with pytest.raises(FileExistsError):prepare_runtime_layout(output,{'session':'a'*32})
    assert not (output/'data').exists() and not (output/'trace').exists()
    assert (other/'retained').read_bytes()==b'original'
    if kind=='file':assert point.read_bytes()==b'original'
    elif kind=='directory':assert (point/'retained').read_bytes()==b'original'
    else:assert point.is_symlink()


def test_layout_rejects_linked_sensor_parent_and_unsafe_session_before_writes(tmp_path):
    output=tmp_path/'output';output.mkdir(mode=0o700)
    other=tmp_path/'other';other.mkdir(mode=0o700)
    (output/'prepared15').symlink_to(other,target_is_directory=True)
    with pytest.raises(OSError):prepare_runtime_layout(output,{'session':'a'*32})
    assert not list(other.iterdir()) and not (output/'data').exists()
    with pytest.raises(ValueError,match='PREPARATION_SESSION_INVALID'):
        prepare_runtime_layout(output,{'session':'../escape'})
    link=tmp_path/'link';link.symlink_to(output,target_is_directory=True)
    with pytest.raises(ValueError,match='PREPARATION_LAYOUT_SYMLINK'):
        prepare_runtime_layout(link,{'session':'a'*32})


@pytest.mark.parametrize('unsafe',['output','prepared15'])
def test_layout_rejects_writable_parent_before_creating_mountpoint(tmp_path,unsafe):
    output=tmp_path/'output';output.mkdir(mode=0o700)
    sensor=output/'prepared15';sensor.mkdir(mode=0o700)
    (output if unsafe=='output' else sensor).chmod(0o777)
    with pytest.raises(ValueError,match='PREPARATION_LAYOUT_OWNER_OR_MODE'):
        prepare_runtime_layout(output,{'session':'a'*32})
    assert not list(sensor.iterdir()) and not (output/'data').exists()
