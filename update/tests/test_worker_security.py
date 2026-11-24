"""EXAMPLE worker queues plus real owned temp files; no engine import/run."""
from contextlib import nullcontext
from copy import deepcopy
import errno
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import types
from types import SimpleNamespace as NS

import pytest

from guard.adapters import vllm_worker_security_sensor as sensor
from guard.collectors import worker_security as collector
from guard.runtime import kubernetes_worker_security as reader
from guard.executors import worker_security_projection as projection
from guard.models import canonical_json,sha256_hex,DataOrigin
from tests.test_lifecycle_capture import external  # noqa: F401

REPO=Path(__file__).resolve().parents[2]
FIXTURE=Path(__file__).parent/'fixtures/vllm-0.30.0/vllm/v1/worker/gpu_worker.py.txt'
SESSION='a'*32


def config(ranks=1):
    return sensor.settings({'schema':'guard-worker-security-settings/1','session':SESSION,
        'expected_ranks':list(range(ranks)),'credential_paths':['/root/.aws/credentials'],
        'sentinel_paths':['/guard-security-denied'],'permission_paths':sorted(sensor.PERMISSION_PATHS)})


def assets(external,ranks=1):
    manifest=collector.prepare_assets(external/'worker',REPO,credential_paths=['/root/.aws/credentials'],
        sentinel_paths=['/guard-security-denied'],expected_ranks=list(range(ranks)))
    prepared,cfg=collector.load_prepared(external/'worker/manifest.json')
    assert prepared==manifest
    return prepared,cfg


def worker_module(tmp_path):
    raw=FIXTURE.read_bytes();assert hashlib.sha256(raw).hexdigest()==sensor.SOURCE_SHA256
    path=tmp_path/'gpu_worker.py';path.write_bytes(raw)
    module=types.ModuleType(sensor.MODULE);module.__file__=str(path)
    module.__dict__.update(set_current_vllm_config=lambda cfg:nullcontext(),has_ec_transfer=lambda:False)
    owner=type('Worker',(),{'__module__':sensor.MODULE});module.Worker=owner
    code=sensor.selected_code(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))
    fn=types.FunctionType(code,vars(module),'load_model');fn.__kwdefaults__={'load_dummy_weights':False}
    owner.load_model=fn
    worker=owner();worker.rank=0;worker.parallel_config=NS(tensor_parallel_size=1,pipeline_parallel_size=1,data_parallel_size=1)
    worker.vllm_config=NS(weight_transfer_config=None);worker.calls=[]
    worker.model_runner=NS(load_model=lambda **kw:worker.calls.append(kw))
    worker._maybe_get_memory_pool_context=lambda **kw:nullcontext()
    worker._scoped_allocator_max_split=lambda **kw:nullcontext()
    return module,worker


def test_source_body_pin_is_compiled_on_this_abi_without_engine_import():
    code=sensor.selected_code(compile(FIXTURE.read_bytes(),'vllm/v1/worker/gpu_worker.py','exec',dont_inherit=True,optimize=0))
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]))
    if runtime in collector.BODY_CODES:assert sensor.code_digest(code)==collector.BODY_CODES[runtime]
    else:assert runtime not in collector.BODY_CODES


@pytest.mark.parametrize('mode',['normal','dummy','native-error','observer-error','reload'])
def test_exact_worker_wrapper_preserves_native_result_exception_and_invalidates_reload(tmp_path,monkeypatch,mode):
    module,worker=worker_module(tmp_path);observed=[];invalid=[]
    monkeypatch.setattr(sensor,'read_settings',lambda:config())
    def probe(*args):
        assert worker.calls
        if mode=='observer-error':raise RuntimeError('EXAMPLE_PRIVATE_PROBE')
        observed.append(args)
    monkeypatch.setattr(sensor,'probe',probe)
    monkeypatch.setattr(sensor,'invalidate',lambda *args:invalid.append(args))
    sensor.instrument(module)
    if mode=='native-error':
        error=RuntimeError('EXAMPLE_NATIVE_FAILURE')
        def fail(**kw):raise error
        worker.model_runner.load_model=fail
        with pytest.raises(RuntimeError) as caught:worker.load_model()
        assert caught.value is error and not observed
    else:
        assert worker.load_model(load_dummy_weights=mode=='dummy') is None
        if mode=='reload':assert worker.load_model() is None
        assert len(observed)==(0 if mode in {'dummy','observer-error'} else 2 if mode=='reload' else 1)
        assert bool(invalid)==(mode=='reload')
        assert all(row[0] is worker and row[1] is module.Worker for row in observed)


@pytest.mark.parametrize('damage',['source','body','globals','defaults','closure','class'])
def test_worker_hook_rejects_unreviewed_callable_before_installation(tmp_path,monkeypatch,damage):
    module,worker=worker_module(tmp_path);original=module.Worker.load_model
    monkeypatch.setattr(sensor,'read_settings',lambda:config())
    if damage=='source':Path(module.__file__).write_bytes(FIXTURE.read_bytes()+b'\n')
    elif damage=='body':module.Worker.load_model=lambda self,**kw:None
    elif damage=='globals':
        fn=types.FunctionType(original.__code__,{},'load_model');fn.__kwdefaults__=original.__kwdefaults__;module.Worker.load_model=fn
    elif damage=='defaults':original.__kwdefaults__={'load_dummy_weights':True}
    elif damage=='closure':
        def outer():
            value=0
            def wrapped(self,**kwargs):return value
            return wrapped
        module.Worker.load_model=outer()
    else:module.Worker=NS(load_model=original)
    with pytest.raises(ValueError):sensor.instrument(module)
    assert not hasattr(getattr(module.Worker,'load_model',None),'__wrapped__')


def test_candidate_open_never_reads_secret_contents_and_refuses_symlink(tmp_path,monkeypatch):
    path=tmp_path/'credential';path.write_text('EXAMPLE_PRIVATE_CREDENTIAL')
    alias=tmp_path/'alias';alias.symlink_to(path)
    def forbidden(*a,**kw):raise AssertionError('credential bytes must never be read')
    monkeypatch.setattr(sensor.os,'read',forbidden)
    assert sensor.open_candidate(str(path))=='candidate_readable'
    assert sensor.open_candidate(str(alias))=='unknown'
    assert sensor.open_candidate(str(tmp_path/'absent'))=='absent'


def test_sentinel_removes_only_exclusively_created_test_file(tmp_path):
    assert sensor.sentinel(str(tmp_path),SESSION)=='succeeded'
    assert list(tmp_path.iterdir())==[]
    existing=tmp_path/('.guard-worker-'+SESSION+'-'+str(os.getpid()))
    existing.write_text('EXAMPLE_EXISTING_FILE')
    assert sensor.sentinel(str(tmp_path),SESSION)=='error'
    assert existing.read_text()=='EXAMPLE_EXISTING_FILE'


@pytest.mark.parametrize('damage',[None,'unexpected-target','changed-alias'])
def test_standard_var_run_alias_needs_stable_exact_metadata(monkeypatch,damage):
    before=NS(st_mode=stat.S_IFLNK|0o777,st_dev=1,st_ino=2,st_size=4,st_mtime_ns=3,st_ctime_ns=4)
    changed=NS(**{**vars(before),'st_ino':3});calls=[]
    def lstat(path):
        assert path=='/var/run';calls.append(path)
        return changed if damage=='changed-alias' and len(calls)>1 else before
    monkeypatch.setattr(sensor.os,'lstat',lstat)
    monkeypatch.setattr(sensor.os,'readlink',lambda path:'/other' if damage=='unexpected-target' else '/run')
    opened=[]
    monkeypatch.setattr(sensor,'_open_candidate_nofollow',lambda path:opened.append(path) or 'absent')
    result=sensor.open_candidate('/var/run/secrets/kubernetes.io/serviceaccount/token')
    assert result==('absent' if damage is None else 'unknown')
    assert opened==([] if damage=='unexpected-target' else ['/run/secrets/kubernetes.io/serviceaccount/token'])


@pytest.mark.parametrize('damage',['extra','ranks-bool','rank-gap','path-traversal','path-symlink-spelling','foreign-sentinel'])
def test_settings_are_closed_and_owned(damage):
    cfg=config()
    if damage=='extra':cfg['secret']='EXAMPLE_PRIVATE'
    elif damage=='ranks-bool':cfg['expected_ranks']=[False]
    elif damage=='rank-gap':cfg['expected_ranks']=[1]
    elif damage=='path-traversal':cfg['credential_paths']=['/root/../etc/passwd']
    elif damage=='path-symlink-spelling':cfg['credential_paths']=['/root//credential']
    else:cfg['sentinel_paths']=['/models']
    with pytest.raises(ValueError):sensor.settings(cfg)


def sample(prepared,cfg,rank=0):
    return {'schema':sensor.SCHEMA,'profile':sensor.PROFILE,'session':cfg['session'],'rank':rank,
        'source_module':sensor.MODULE,'source_sha256':sensor.SOURCE_SHA256,
        'body_code_sha256':collector.BODY_CODES['cpython-3.12.3'],'observer_runtime':'cpython-3.12.3',
        'sensor_sha256':prepared['assets']['guard_worker_security.py']['sha256'],
        'settings_sha256':prepared['assets']['settings.json']['sha256'],
        'context':{'pid':42+rank,'start_ticks':99,'uid_effective':1000,'gid_effective':1000,'cap_eff':'0',
            'pid_namespace_inode':12,'mount_namespace_inode':13,'time_namespace_inode':14,
            'time_offsets_sha256':'d'*64,'boot_sha256':'b'*64,'mountinfo_sha256':'e'*64},
        'started_ns':10,'finished_ns':20,'environment_names':['EXAMPLE_MARKER_NAME'],
        'credential_results':{p:'absent' for p in cfg['credential_paths']},
        'sentinel_results':{p:'denied' for p in cfg['sentinel_paths']},
        'write_results':{p:'denied' for p in cfg['permission_paths']},
        'mounts':[{'path':'/models','read_only':True,'noexec':True,'filesystem':'ext4'}],
        'device_fds':['/dev/nvidia0'],'source_verified':True,'producer_authenticated':False,
        'scope':'selected-owned-tp-worker-after-load-model'}


def packet(records,cfg):
    by_rank={r['rank']:r for r in records}
    return {'schema':'guard-worker-security-live-read/1','rows':[{
        'rank':rank,'available':rank in by_rank,'record':by_rank.get(rank),
        'record_sha256':sha256_hex(json.dumps(by_rank[rank],sort_keys=True,separators=(',',':')).encode()) if rank in by_rank else None,
        'diagnostic':None if rank in by_rank else 'WORKER_RECORD_UNAVAILABLE'} for rank in cfg['expected_ranks']]}


def test_whole_roster_requires_every_distinct_current_worker_and_no_registration_is_inherited(external):
    prepared,cfg=assets(external,ranks=2);first=sample(prepared,cfg);second=sample(prepared,cfg,1)
    records,complete=collector.validate_packet(packet([first,second],cfg),prepared,cfg)
    assert records==[first,second] and complete
    assert collector.validate_packet(packet([first],cfg),prepared,cfg)==([first],False)
    second['context']=first['context']
    with pytest.raises(ValueError,match='DUPLICATE_PROCESS'):collector.validate_packet(packet([first,second],cfg),prepared,cfg)
    assert not collector.qualification('vllm','0.30.0','example.invalid/unqualified@sha256:'+'a'*64,[first])


@pytest.mark.parametrize('damage',['raw','source','abi','body','sensor','settings','pid-bool','clock','env-value','candidate-extra','outcome','mount','device','authenticated','verified'])
def test_closed_worker_record_rejects_extra_raw_values_and_unreviewed_claims(external,damage):
    prepared,cfg=assets(external);row=sample(prepared,cfg)
    if damage=='raw':row['credential_contents']='EXAMPLE_PRIVATE'
    elif damage=='source':row['source_sha256']='0'*64
    elif damage=='abi':row['observer_runtime']='cpython-unreviewed'
    elif damage=='body':row['body_code_sha256']='0'*64
    elif damage=='sensor':row['sensor_sha256']='0'*64
    elif damage=='settings':row['settings_sha256']='0'*64
    elif damage=='pid-bool':row['context']['pid']=True
    elif damage=='clock':row['finished_ns']=9
    elif damage=='env-value':row['environment_names']=['TOKEN=EXAMPLE_PRIVATE']
    elif damage=='candidate-extra':row['credential_results']['/unrequested']='absent'
    elif damage=='outcome':row['credential_results'][cfg['credential_paths'][0]]='PASS'
    elif damage=='mount':row['mounts'][0]['read_only']='true'
    elif damage=='device':row['device_fds']=['/etc/passwd']
    elif damage=='authenticated':row['producer_authenticated']=True
    else:row['source_verified']=False
    with pytest.raises(ValueError,match='WORKER_SECURITY_RECORD_INVALID'):collector.validate_record(row,prepared,cfg)


@pytest.mark.parametrize('damage',['settings','sensor','sitecustomize','manifest-claim'])
def test_prepared_assets_reject_tamper_even_if_operator_updates_manifest_hash(external,damage):
    prepared,cfg=assets(external);root=external/'worker'
    name={'settings':'settings.json','sensor':'guard_worker_security.py','sitecustomize':'sitecustomize.py'}.get(damage)
    if name:
        path=root/name;path.chmod(0o600);raw=path.read_bytes()+b'\n#EXAMPLE_changed';path.write_bytes(raw)
        prepared['assets'][name]={'sha256':sha256_hex(raw),'bytes':len(raw)}
    else:prepared['gpu_qualified']=True
    (root/'manifest.json').write_bytes(canonical_json(prepared))
    with pytest.raises(ValueError):collector.load_prepared(root/'manifest.json')


def test_fixed_live_reader_pin_and_generation_checks_with_owned_cpu_child(tmp_path,monkeypatch):
    raw=reader.ASSET.read_bytes();assert sha256_hex(raw)==reader.SOURCE_SHA256
    module=types.ModuleType('_worker_reader_test');exec(compile(raw,str(reader.ASSET),'exec'),vars(module))
    root=tmp_path/'root';root.mkdir();directory=root/'data'/SESSION;directory.mkdir(parents=True,mode=0o700);directory.chmod(0o700)
    source=b'EXAMPLE_SENSOR';settings=b'EXAMPLE_SETTINGS'
    (root/'guard_worker_security.py').write_bytes(source);(root/'settings.json').write_bytes(settings)
    opened=module.os.open
    def open_file(path,*args,**kwargs):return opened(str(root) if path=='/guard-worker-security' else path,*args,**kwargs)
    monkeypatch.setattr(module.os,'open',open_file)
    process=subprocess.Popen([sys.executable,'-I','-S','-c','import sys; sys.stdin.buffer.read(1)'],stdin=subprocess.PIPE)
    try:
        value={'context':module.live(process.pid),'rank':0,'session':SESSION,
            'sensor_sha256':sha256_hex(source),'settings_sha256':sha256_hex(settings),'finished_ns':time.monotonic_ns()}
        path=directory/'rank-0.json';path.write_text(json.dumps(value));path.chmod(0o600)
        spec={'session':SESSION,'ranks':[0],'sensor_sha256':sha256_hex(source),'settings_sha256':sha256_hex(settings)}
        assert module.run(spec)['rows'][0]['available'] is True
        value['context']['start_ticks']+=1;path.write_text(json.dumps(value))
        assert module.run(spec)['rows'][0]['available'] is False
        value['context']['start_ticks']-=1;path.write_text(json.dumps(value))
        (directory/'rank-0.invalid').write_text('EXAMPLE_RELOADED')
        assert module.run(spec)['rows'][0]['available'] is False
    finally:
        process.communicate(b'x',timeout=10)


@pytest.mark.parametrize('registered',[False,True])
def test_projection_requires_registration_and_keeps_observer_origin_ceiling(external,registered):
    prepared,cfg=assets(external);row=sample(prepared,cfg)
    target={'schema':'EXAMPLE'};target_sha=sha256_hex(canonical_json(target))
    snap={'profile':'kubernetes-owned-worker-security-v1','target_sha256':target_sha,
        'prepared_sha256':sha256_hex(canonical_json(prepared)),'available':True,'qualified_registered':registered,
        'records':[row],'whole_selected_tp_roster_verified':True,'data_origin':'EXAMPLE'}
    process={'binding_verified_before_after':True,'mount_declarations_stable':True,
        'mount_declarations':[{'mount_path':'/models'}],
        'packet':{'capture_subject':{'pid_namespace_inode':12},'pid_inventory_stable':True,'process_metadata_complete':True,
            'processes':[{'pid':42,'start_ticks':99,'uid_effective':1000,'cap_eff':'0',
                          'mount_namespace_inode':13,'pid_identity_stable':True}]}}
    ctx=NS(_worker_security_prepared=external/'worker/manifest.json',target=target,
        cprobe=NS(worker_security_snapshot=lambda p:snap),_kubernetes_process_data_origin=DataOrigin.DECLARED,
        policy_values={'credential_candidate_paths':cfg['credential_paths'],'forbidden_mount_paths':['/forbidden']})
    observations={key:{'value':None,'collected':False} for key in ('worker_env_names','process_scan_complete',
        'credential_candidate_results','credential_candidates_complete','forbidden_mount_violations')}
    _,scope=projection.credentials(ctx,process,observations)
    assert scope['provider_scope_verified'] is False
    if registered:
        assert observations['worker_env_names']=={'value':['EXAMPLE_MARKER_NAME'],'collected':True}
        assert observations['credential_candidate_results']['value']=={cfg['credential_paths'][0]:'absent'}
        assert observations['forbidden_mount_violations']['value']==[]
        assert ctx._kubernetes_process_data_origin==DataOrigin.EXAMPLE
    else:assert all(not value['collected'] for value in observations.values())


def test_partial_worker_writable_mount_is_positive_evidence_but_never_complete_scope(external,monkeypatch):
    prepared,cfg=assets(external,ranks=2);row=sample(prepared,cfg)
    row['mounts'].append({'path':'/tmp','read_only':False,'noexec':False,'filesystem':'tmpfs'})
    row['write_results']['/tmp']='succeeded'
    monkeypatch.setattr(projection,'capture',lambda *args:({'available':True},[row],False,cfg))
    observations={'capabilities_effective':{'value':['CHOWN'],'collected':True},
        'root_uid_present':{'value':None,'collected':False},'exec_writable_mounts':{'value':None,'collected':False}}
    _,scope=projection.permissions(NS(),{},observations)
    assert observations['capabilities_effective']['value']==['CHOWN']
    assert observations['exec_writable_mounts']=={'value':['/tmp'],'collected':True}
    assert scope['whole_permission_scope_verified'] is False and not scope['selected_tp_worker_roster_verified']


def test_later_worker_auxiliary_error_never_erases_prior_permission_facts(monkeypatch):
    ctx=NS(_worker_security_prepared='/EXAMPLE/missing')
    observations={'capabilities_effective':{'value':['CHOWN'],'collected':True},
        'root_uid_present':{'value':True,'collected':True},'exec_writable_mounts':{'value':['/EXAMPLE'],'collected':True}}
    original=deepcopy(observations)
    _,scope=projection.permissions(ctx,{},observations)
    assert observations==original and scope['whole_permission_scope_verified'] is False
