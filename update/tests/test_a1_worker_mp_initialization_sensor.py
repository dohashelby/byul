"""Native20 EXAMPLE composition; no actual worker, vLLM or GPU execution."""
from copy import deepcopy
from pathlib import Path
import types
import pytest
from guard.adapters import vllm_worker_startup_sensor as startup
from guard.adapters import vllm_worker_initialization_sensor as sensor, vllm_worker_initialization_metadata as metadata
from guard.adapters import vllm_initialization_sensor as initialization, vllm_initialization_metadata as init_metadata
from tests.test_a1_worker_startup_sensor import make_world as startup_world
from tests.test_a1_initialization_sensor import make_world as mp_world, invoke


def make_world(tmp_path,patch,*,mode='normal',session='b'*32,path=None,scope_path=None):
    private=types.ModuleType('_worker_mp_original19');private.__file__=startup.__file__
    raw=Path(startup.__file__).read_bytes()
    exec(compile(raw,private.__file__,'exec',dont_inherit=True,optimize=0),vars(private))
    def configure(*args,**kwargs):
        return sensor.configure(private.configure(*args,**kwargs),initialization,private)
    with patch.context() as local:
        local.setattr(startup,'configure',configure)
        local.setattr(startup,'StartupObservation',private.StartupObservation)
        world=startup_world(tmp_path,local,mode=mode,session=session,path=path,scope_path=scope_path,otel_available=True)
    parent=mp_world(session=session,mode='partial-failure' if mode=='partial-failure' else 'normal')
    # Restore only the original EXAMPLE fixture descriptors before exact source admission.
    for cls_name,methods in initialization.TARGETS.items():
        cls=vars(parent.module)[cls_name]
        for name in methods:
            descriptor=vars(cls)[name]
            value=descriptor.__func__ if type(descriptor) is staticmethod else descriptor
            original=value.__wrapped__
            setattr(cls,name,staticmethod(original) if cls_name+'.'+name in initialization.STATIC_POINTS else original)
    world.observer.instrument(parent.module)
    info={'source_sha256':initialization.SOURCE_ADDITIONS[initialization.MP],'code_sha256':'c'*64}
    world.observer.modules[initialization.MP]=info
    world.observer.emit('MODULE',{'module':initialization.MP,'verified':True,**info})
    world.mp=parent
    return world


def packet(world):
    context=world.observer.writer.context
    rows=[{'event':'START','payload':{'profile':sensor.PROFILE,'schema':sensor.SCHEMA,
        'session':world.observer.config['session'],'context':context}},*world.observer.recorded]
    rows=[{**r,'seq':index} for index,r in enumerate(rows)]
    return {'recordings':[{'session_owner_match':True,'listed_file_identity_match':True,
        'capture':{'records':rows,'process_context_verified':True,'image_digest_verified':False}}]}


@pytest.mark.parametrize('mode',['normal','partial-failure'])
def test_original_worker_and_MP_descriptors_and_exceptions_share_explicit_new_profile(tmp_path,monkeypatch,mode):
    world=make_world(tmp_path,monkeypatch,mode=mode)
    assert world.base.PROFILE==sensor.PROFILE and world.base.SCHEMA==sensor.SCHEMA
    assert startup.PROFILE.endswith('/19') and initialization.PROFILE.endswith('/14')
    assert world.start() is None
    if mode=='partial-failure':
        with pytest.raises(Exception,match='WorkerProc initialization failed'):invoke(world.mp)
        assert len(world.mp.terminated)==2
    else:assert invoke(world.mp) is None
    world.close();data=packet(world)
    value=metadata.project(data,{'profile':sensor.PROFILE,'session':world.observer.config['session']})
    parent=init_metadata.project(data,{'init_expected_ranks':['0','1']})
    assert value['events'] and value['model_candidates'] and parent['calls'] and parent['correspondence']
    assert any(c['same_startup_model_root_candidate'] for c in value['model_candidates'])
    assert not value['cross_process_lineage_verified']
    assert all(value[k] is False for k in startup.TRUTH)
    assert all(not c['confirmed_violation'] for c in parent['correspondence'])
    assert type(vars(world.mp.module.WorkerProc)['wait_for_ready']) is staticmethod


@pytest.mark.parametrize('change',['duplicate','missing','profile','session'])
def test_missing_duplicate_and_other_recording_are_not_cross_process_qualification(tmp_path,monkeypatch,change):
    world=make_world(tmp_path,monkeypatch);world.start();invoke(world.mp);world.close()
    data=packet(world);rows=data['recordings'][0]['capture']['records']
    prepared={'profile':sensor.PROFILE,'session':world.observer.config['session']}
    if change in {'profile','session'}:
        rows[0]['payload'][change]='vllm-native-lifecycle-metadata/19' if change=='profile' else 'c'*32
        with pytest.raises(ValueError,match='WORKER_INITIALIZATION_RECORDING_MISMATCH'):metadata.project(data,prepared)
    else:
        if change=='duplicate':
            row=next(deepcopy(r) for r in rows if r['event']=='WORKER_STARTUP' and r['payload']['phase']=='CALL')
            row['seq']=len(rows)+1;rows.append(row)
        else:
            rows[:]=[r for r in rows if r['event'] not in {'WORKER_STARTUP','WORKER_STARTUP_LINK'}]
        value=metadata.project(data,prepared)
        assert not value['cross_process_lineage_verified']
        assert 'worker_startup_unique_call_pair_unavailable' in value['missing'] if change=='duplicate' else not value['events']
        assert init_metadata.project(data,{'init_expected_ranks':['0','1']})['calls']


def test_modified_delegate_source_and_composition_code_are_rejected(tmp_path,monkeypatch):
    path=tmp_path/'modified14.py';path.write_bytes(Path(initialization.__file__).read_bytes()+b'\n')
    with pytest.raises(ValueError,match='WORKER_INITIALIZATION_ASSET_UNAVAILABLE'):
        sensor.artifact(path,sensor.INITIALIZATION14_SHA256)
    with monkeypatch.context() as local:
        local.setattr(sensor,'code_contract',lambda base:'0'*64)
        with pytest.raises(ValueError,match='WORKER_INITIALIZATION_CODE_UNAVAILABLE'):
            sensor.configure(types.SimpleNamespace(),initialization,startup)
