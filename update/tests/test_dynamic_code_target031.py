"""EXAMPLE boundaries; CPU loaders are executed separately after source freeze."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
from guard.executors.dynamic_code_cpu import fixtures,identity
from tools import b1_dynamic_code_cpu as collector


def test_reviewed_fixture_manifest_is_fixed_and_history_independent(tmp_path):
    shutil.copytree(collector.FIXTURE,tmp_path/'fixture')
    assert collector.verify_fixture(tmp_path/'fixture')==collector.FIXTURE_SHA256
    (tmp_path/'fixture/model/config.json').write_text('PRIVATE_CHANGED')
    with pytest.raises(ValueError,match='FIXTURE_MISMATCH'):
        collector.verify_fixture(tmp_path/'fixture')


@pytest.mark.parametrize('kind',['extra','symlink','missing'])
def test_fixture_rejects_unapproved_file_set(tmp_path,kind):
    folder=tmp_path/'fixture';shutil.copytree(collector.FIXTURE,folder)
    if kind=='extra':(folder/'model/shadow.py').write_text('')
    elif kind=='symlink':
        path=folder/'model/config.json';path.unlink();path.symlink_to(collector.FIXTURE/'model/config.json')
    else:(folder/'builtin/config.json').unlink()
    with pytest.raises(ValueError):collector.verify_fixture(folder)


def test_profiles_pin_exact_source_with_no_optimization_restriction(tmp_path,monkeypatch):
    from guard.executors import structured_tools_cpu as backend
    seen=[]
    monkeypatch.setattr(collector,'verify_checkout',lambda:None)
    monkeypatch.setattr(backend,'_verify_source',lambda root,**kw:seen.append(kw['expected_commit']))
    monkeypatch.setattr(backend,'_validate_target031_dependencies',lambda:seen.append('deps'))
    monkeypatch.setattr(collector,'verify_fixture',lambda folder:collector.FIXTURE_SHA256)
    # verify_profile must permit each required child mode; parent capture gates separately.
    for mode in (0,1,2):
        monkeypatch.setattr(collector.sys,'flags',SimpleNamespace(optimize=mode))
        collector.verify_profile(tmp_path,'reference5d')
        collector.verify_profile(tmp_path,'target031')
    assert seen==[collector.SOURCE_COMMIT,collector.TARGET031_COMMIT,'deps']*3
    with pytest.raises(ValueError):collector.verify_profile(tmp_path,'HEAD')


@pytest.mark.parametrize('error,status',[(OSError,'unavailable'),(ValueError,'mismatch')])
def test_postcheck_classifies_failure_without_exception_text(tmp_path,monkeypatch,error,status):
    def failed(*a,**kw):raise error('PRIVATE_DIAGNOSTIC')
    monkeypatch.setattr(collector,'verify_profile',failed)
    assert collector.post_validation(tmp_path,'target031','a'*64)=={'status':status,'error_type':error.__name__}


@pytest.fixture
def example_capture(monkeypatch):
    monkeypatch.setattr(collector,'verify_profile',lambda *a,**kw:dict(collector.FIXTURE_SHA256))
    monkeypatch.setattr(collector,'post_validation',lambda *a,**kw:{'status':'verified'})
    commands=[]
    def launch(argv,**kw):
        commands.append((argv,kw))
        case=next(c for c in fixtures() if c['case_id']==argv[argv.index('--case')+1])
        folder=Path(argv[argv.index('--workdir')+1]);profile=argv[argv.index('--source-profile')+1]
        collection=argv[argv.index('--collection-id')+1]
        row={**case,'case_identity':identity(case),'outcome':'accepted' if case['trust'] else 'rejected_at_trust_gate',
             'before':{'outcome':'accepted'},'after':{'outcome':'accepted'},
             'events':[{'phase':'import'},{'phase':'constructor'}] if case['trust'] else [],
             'event_collection_status':'completed','selected_fixture_source_matches':True,
             'source_profile':profile,'source_commit':collector.SOURCE_PROFILES[profile],
             'collection_id':collection,'target_started':True,'python_optimize':{'normal':0,'O':1,'OO':2}[case['mode']],
             'source_validation':{'pre':'verified','post':{'status':'verified'},'boundary':'source_fixture_component'}}
        # Independent EXAMPLE violation for the error-preservation regressions.
        if case['case_id']=='autoconfig-False-O':row['events']=[{'phase':'import'}]
        (folder/'case.json').write_text(json.dumps(row))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(collector.subprocess,'run',launch)
    return commands


def test_one_capture_retains_twelve_modes_controls_and_shared_identity(tmp_path,example_capture):
    record=collector.capture(tmp_path/'source',tmp_path/'output',source_profile='target031')
    assert record['capture_status']=='completed' and len(record['rows'])==12
    assert len(example_capture)==12
    assert len({r['collection_id'] for r in record['rows']})==1
    assert record['source_commit']==collector.TARGET031_COMMIT
    assert datetime.fromisoformat(record['capture_started_at'])<=datetime.fromisoformat(record['capture_finished_at'])
    assert datetime.fromisoformat(record['capture_started_at']).utcoffset() is not None
    assert record['own_producer_sha256']==hashlib.sha256(Path(collector.__file__).read_bytes()).hexdigest()
    assert record['observations']['SUP-09']['dynamic_code_confirmed_violations']['value']==['autoconfig-False-O']
    assert record['observations']['SUP-11']['dynamic_mode_confirmed_violations']['value']==['autoconfig-False-O']
    assert all(kw['env']['VLLM_PLUGINS']=='' for _,kw in example_capture)
    assert len({kw['env']['HF_MODULES_CACHE'] for _,kw in example_capture})==12
    for argv,_ in example_capture:
        mode=argv[argv.index('--case')+1].rsplit('-',1)[1]
        assert ('-O' in argv)==(mode=='O') and ('-OO' in argv)==(mode=='OO')


def test_auxiliary_failure_preserves_independent_violations(tmp_path,example_capture,monkeypatch):
    original=Path.open
    def failed(path,*a,**kw):
        if path==tmp_path/'output/result.json':raise OSError('PRIVATE_WRITE_ERROR')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'open',failed)
    record=collector.capture(tmp_path/'source',tmp_path/'output')
    assert record['capture_status']=='incomplete' and len(record['rows'])==12
    assert record['auxiliary_write']=={'status':'failed','error_type':'OSError'}
    assert record['observations']['SUP-09']['dynamic_code_confirmed_violations']['value']==['autoconfig-False-O']
    assert 'PRIVATE_' not in json.dumps(record)


def test_parent_post_failure_preserves_measured_rows(tmp_path,example_capture,monkeypatch):
    monkeypatch.setattr(collector,'post_validation',lambda *a,**kw:{'status':'unavailable','error_type':'OSError'})
    record=collector.capture(tmp_path/'source',tmp_path/'output')
    assert record['capture_status']=='incomplete' and len(record['rows'])==12
    assert record['observations']['SUP-11']['dynamic_mode_confirmed_violations']['value']==['autoconfig-False-O']


@pytest.mark.parametrize('opt',['-O','-OO'])
def test_optimized_parent_rejected_before_source_or_output(tmp_path,opt):
    update=Path(collector.__file__).resolve().parents[1]
    output=tmp_path/'output'
    result=subprocess.run([sys.executable,opt,collector.__file__,'--source-root',str(tmp_path/'source'),
        '--workdir',str(output)],cwd=update,capture_output=True,text=True,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(update/'src'),str(update)])})
    assert result.returncode!=0 and 'Parent must run normally' in result.stderr
    assert not output.exists()


def test_foreign_component_gate_never_called(tmp_path):
    update=Path(collector.__file__).resolve().parents[1]
    foreign=tmp_path/'foreign/tools';foreign.mkdir(parents=True)
    (foreign/'__init__.py').write_text('')
    marker=tmp_path/'executed'
    (foreign/'lif14_profiler_cpu.py').write_text('def verify_component(*a):\n from pathlib import Path\n Path('+repr(str(marker))+').touch()\n')
    result=subprocess.run([sys.executable,collector.__file__,'--source-root',str(tmp_path/'source'),
        '--workdir',str(tmp_path/'output')],cwd=update,capture_output=True,text=True,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(foreign.parent),str(update/'src'),str(update)])})
    assert result.returncode!=0 and 'COMPONENT_GATE_IDENTITY_MISMATCH' in result.stderr
    assert not marker.exists() and not (tmp_path/'output').exists()


def test_incomplete_cli_exit(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'argv',['dynamic','--source-root',str(tmp_path),'--workdir',str(tmp_path/'output')])
    monkeypatch.setattr(collector,'capture',lambda *a,**kw:{'capture_status':'incomplete','rows':[],
        'source_profile':'reference5d','collection_id':'example'})
    assert collector.main()==2


@pytest.mark.parametrize('mode,opt',[('normal',None),('O','-O'),('OO','-OO')])
def test_real_child_optimization_reaches_precheck_with_expected_mode(tmp_path,mode,opt):
    update=Path(collector.__file__).resolve().parents[1]
    folder=tmp_path/mode;folder.mkdir();marker=folder/'precheck'
    script='''
import json
from pathlib import Path
from tools import b1_dynamic_code_cpu as c
case=next(x for x in c.fixtures() if x['mode']==MODE)
def precheck(*a,**kw):
    Path(MARKER).touch()
    raise OSError('PRIVATE_NOT_AN_ACTUAL_TARGET')
c.verify_profile=precheck
c.execute(case,Path(FOLDER),source_root=Path(FOLDER),collection_id='EXAMPLE')
'''.replace('MODE',repr(mode)).replace('MARKER',repr(str(marker))).replace('FOLDER',repr(str(folder)))
    command=[sys.executable,*([opt] if opt else []),'-c',script]
    process=subprocess.run(command,cwd=update,capture_output=True,text=True,
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(update/'src'),str(update)])})
    assert process.returncode==0 and marker.exists()
    record=json.loads((folder/'case.json').read_text())
    assert record['python_optimize']=={'normal':0,'O':1,'OO':2}[mode]
    assert record['error_type']=='OSError' and record['target_started'] is False
    assert record['source_validation']['pre']=='unavailable'
    assert 'PRIVATE_' not in json.dumps(record)
