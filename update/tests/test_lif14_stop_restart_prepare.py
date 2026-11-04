import hashlib
from pathlib import Path
import pytest
from tools.lif14_stop_restart_prepare import digest_file,render


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
