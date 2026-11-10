"""Continuous cache metadata uses original binders and separate durable scope."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import types

import pytest

from serving_runtime.mvp import cache_observation as module
from serving_runtime.mvp.collectors import CollectionFailure
from serving_runtime.mvp.revalidation import RevalidationRequests
from serving_runtime.mvp.target import instance_id
from serving_runtime.enforcement.cache import check_cache
from serving_runtime.enforcement.intent import parse_intent
ROOT = Path(__file__).resolve().parents[1]


def source(tmp_path):
    from tests.test_asset_binding import AssetBindingTest
    target, _, pod = AssetBindingTest().setup_binding()
    raw = (ROOT/'deploy/gpu-lab/intent.aws-lab.json').read_text().replace('REPLACE_IMAGE_DIGEST_HEX','a'*64).replace('REPLACE_PROFILE','test')
    intent = json.loads(raw)
    intent['assets']['cache_root'] = str(tmp_path/'cache')
    intent['assets']['engine_uid'] = os.geteuid() or 2000
    parsed = parse_intent(intent)
    generation = Path(parsed.assets.generation_path)
    generation.mkdir(parents=True, mode=0o700)
    path = generation
    while path != tmp_path:
        path.chmod(0o700)
        if os.geteuid()==0:os.chown(path,2000,2000)
        path = path.parent
    pod['spec']['volumes'][1]['hostPath']['path'] = str(generation)
    pod['spec']['securityContext']={'runAsUser':intent['assets']['engine_uid'],'runAsGroup':0}
    target_path, intent_path = tmp_path/'target.json', tmp_path/'intent.json'
    target_path.write_text(json.dumps(target)); intent_path.write_text(json.dumps(intent))
    settings = {'schema':module.SETTINGS, 'target_path':str(target_path), 'target_sha256':hashlib.sha256(target_path.read_bytes()).hexdigest(),
                'intent_path':str(intent_path), 'intent_sha256':hashlib.sha256(intent_path.read_bytes()).hexdigest(),
                'policy_sha256':'b'*64, 'bundle_id':'bundle-test', 'model_host_path':'/srv/model',
                'expected_command':['vllm'], 'expected_args':['serve','/models'], 'expected_cache_environment':{}}
    producer = module.CacheObservationProducer.__new__(module.CacheObservationProducer)
    producer.settings, producer.reader_gid = settings, os.getegid()
    producer.kubeconfig, producer.kubectl = '/root/credential', '/usr/local/bin/kubectl'
    producer.query = lambda _: {'items':[deepcopy(pod)]}
    producer.source_modules={'fixture':'c'*64}
    producer.source_sha256=module.digest({'schema':module.SOURCE_SCHEMA,'modules':producer.source_modules})
    producer.binding_sha256=module.digest(module._binding(settings))
    return producer, parsed, pod


def reader(producer, document, monkeypatch):
    monkeypatch.setattr(module, 'read_protected', lambda path, *args, **kwargs: module.canonical(document) if path=='snapshot' else Path(path).read_bytes())
    current = {key: producer.settings[key] for key in ('target_sha256','policy_sha256','bundle_id')}
    current['instance_id'] = instance_id('pod1','containerd://1')
    return module.CacheObservationReader('snapshot', reader_gid=os.getegid(), producer_sha256=producer.source_sha256,
        binding_sha256=producer.binding_sha256, current_binding=lambda:current), current


def test_real_metadata_and_pod_binding_flow_to_reader_then_durable_revalidation(tmp_path, monkeypatch):
    producer, _, pod = source(tmp_path)
    monkeypatch.setattr(module, 'read_protected', lambda path, *args, **kwargs: Path(path).read_bytes())
    good = producer.collect()
    feed, _ = reader(producer, good, monkeypatch)
    result, origin = feed.collect()
    assert result['intact'] is True
    assert origin == 'local_file'
    assert result['cache_metadata']['sources'] == ['host_filesystem','kubernetes_api']
    pod['spec']['volumes'][1]['hostPath']['path'] = '/wrong-generation'
    # Producer retrieves a fresh Pod independently from native capture/init.
    producer.query = lambda _: {'items':[deepcopy(pod)]}
    bad = producer.collect()
    feed, _ = reader(producer, bad, monkeypatch)
    changed, _ = feed.collect()
    assert changed['intact'] is False
    assert 'cache_mount_mismatch' in changed['issues']
    journal = RevalidationRequests(tmp_path/'records')
    context = dict(observation_session_id='s', target_id='target', policy={'sha256':'b'*64})
    journal.observe_asset({'intact':False,'items':[{'path':'model.bin','state':'changed'}]}, data_origin='local_file', **context)
    journal.observe_cache(changed, data_origin=origin, **context)
    journal.observe_cache(result, data_origin=origin, **context)
    restarted = RevalidationRequests(tmp_path/'records').summary()
    model = [item for item in restarted['requests'] if item['evidence'].get('paths',{}).get('changed')==['model.bin']]
    assert len(model)==1 and model[0]['current_evidence_present'] is True
    assert any(item['evidence'].get('cache_metadata') for item in restarted['requests'])
    assert '/root/credential' not in json.dumps(bad)
    assert 'expected_args' not in json.dumps(bad)
    assert 'command' not in json.dumps(bad)


@pytest.mark.parametrize('mutation', ['stale','policy','target','instance','source','source_modules','binding','unavailable','incomplete'])
def test_stale_or_misbound_or_incomplete_snapshot_stays_unavailable(tmp_path, monkeypatch, mutation):
    producer, _, _ = source(tmp_path)
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    document=producer.collect()
    feed,current=reader(producer,document,monkeypatch)
    if mutation=='stale':document['generated_at']=(datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat()
    elif mutation=='policy':current['policy_sha256']='d'*64
    elif mutation=='target':current['target_sha256']='d'*64
    elif mutation=='instance':current['instance_id']='different'
    elif mutation=='source':document['producer_sha256']='d'*64
    elif mutation=='source_modules':document['source_modules']['fixture']='d'*64
    elif mutation=='binding':document['binding_sha256']='d'*64
    elif mutation=='unavailable':document['state']='unavailable'
    elif mutation=='incomplete':document['cache_check']['state']='incomplete'
    with pytest.raises(CollectionFailure):feed.collect()


def test_pod_change_during_metadata_scan_produces_explicit_unavailable(tmp_path, monkeypatch):
    producer, _, pod=source(tmp_path)
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    calls=[]
    def query(_):
        value=deepcopy(pod)
        if calls:value['metadata']['uid']='restarted'
        calls.append(1)
        return {'items':[value]}
    producer.query=query
    result=producer.collect()
    assert result['state']=='unavailable'
    assert result['cache_check'] is None


def test_cache_metadata_entry_bound_is_explicit_incomplete(tmp_path):
    _,intent,_=source(tmp_path)
    result=check_cache(intent,max_entries=1)
    assert result['state']=='incomplete'
    assert result['walk_errors'][0]['error']=='EntryBoundExceeded'


def test_cache_configuration_requires_all_pins_and_registered_target(tmp_path):
    from serving_runtime.mvp.config import MVPConfig
    with pytest.raises(ValueError):
        MVPConfig(cache_observation_path='snapshot')
    with pytest.raises(ValueError):
        MVPConfig(cache_observation_reader_gid=10000)
    configured=MVPConfig(cache_observation_path='snapshot', cache_observation_reader_gid=10000,
        cache_observation_producer_sha256='a'*64, cache_observation_binding_sha256='b'*64, target_path='target')
    assert configured.cache_observation_path=='snapshot'


def test_raw_pod_environment_and_approved_secret_arg_never_enter_public_snapshot(tmp_path,monkeypatch):
    producer,_,pod=source(tmp_path)
    secret='PRIVATE_CREDENTIAL_SENTINEL_DO_NOT_EXPORT'
    producer.settings['expected_args'] += ['--api-key',secret]
    pod['spec']['containers'][0]['args'] += ['--api-key',secret]
    pod['spec']['containers'][0]['env']=[{'name':'ENGINE_SECRET','value':secret}]
    producer.query=lambda _:{'items':[deepcopy(pod)]}
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    document=producer.collect()
    assert document['state']=='observed'
    assert secret not in json.dumps(document)
    assert 'ENGINE_SECRET' not in json.dumps(document)


def test_gateway_adds_cache_collector_with_separate_revalidation_callback(tmp_path):
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.config import MVPConfig
    config=MVPConfig(data_dir=str(tmp_path/'data'), policy_path=str(ROOT/'examples/mvp/policy.json'),
        target_path=str(ROOT/'tests/fixtures/bundle-server-vllm.json'),
        cache_observation_path=str(tmp_path/'snapshot'), cache_observation_reader_gid=10000,
        cache_observation_producer_sha256='a'*64, cache_observation_binding_sha256='b'*64)
    from serving_runtime.mvp.policy import load_policy
    app=GatewayApp(config,load_policy(config.policy_path),engine_address=("127.0.0.1",1))
    collectors=app._collectors.collectors
    if isinstance(collectors,dict):collectors=collectors.values()
    cached=[collector for collector in collectors if collector.source=='cache_integrity']
    assert len(cached)==1
    assert cached[0].on_collected==app._evaluate_cache_revalidation


def test_cache_environment_redirect_is_bound_without_exporting_values(tmp_path,monkeypatch):
    producer,_,pod=source(tmp_path)
    pod['spec']['containers'][0]['env']=[{'name':'VLLM_CACHE_ROOT','value':'/unapproved-cache'}]
    producer.query=lambda _:{'items':[deepcopy(pod)]}
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    document=producer.collect()
    assert document['execution_binding']['state']=='mismatch'
    assert 'cache_environment_mismatch' in document['execution_binding']['issues']
    assert '/unapproved-cache' not in json.dumps(document)
    pod['spec']['containers'][0]['env']=[{'name':'VLLM_CACHE_ROOT','valueFrom':{'secretKeyRef':{'name':'private','key':'token'}}}]
    document=producer.collect()
    assert document['state']=='unavailable'
    assert document['error_code']=='CACHE_ENVIRONMENT_UNRESOLVED'
    assert 'secretKeyRef' not in json.dumps(document)


def test_cache_scan_never_follows_directory_replaced_after_enumeration(tmp_path,monkeypatch):
    _,intent,_=source(tmp_path)
    root=Path(intent.assets.cache_root)
    branch=root/'vllm'
    outside=tmp_path/'outside'
    (outside/'PRIVATE_DIRECTORY_METADATA'/'revision'/'profile').mkdir(parents=True)
    original=os.open
    swapped=[]
    def open_directory(path, flags, *args, **kwargs):
        if not swapped and path=='vllm' and kwargs.get('dir_fd') is not None:
            branch.rename(tmp_path/'previous-engine-branch')
            branch.symlink_to(outside,target_is_directory=True)
            swapped.append(True)
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',open_directory)
    observed=check_cache(intent,max_entries=512)
    assert swapped == [True]
    assert 'PRIVATE_DIRECTORY_METADATA' not in json.dumps(observed)
    assert observed['state']=='incomplete'


def test_producer_source_pin_covers_critical_helper_modules_and_entrypoint():
    modules=module.source_bundle_modules()
    assert set(modules)=={'serving_runtime.mvp.cache_observation','serving_runtime.enforcement.cache',
        'serving_runtime.mvp.asset_binding','serving_runtime.enforcement.intent','serving_runtime.mvp.target',
        'serving_runtime.mvp.native_receipts','serving_runtime.mvp.policy','serving_runtime.tools.cache_observation_feed'}
    assert module.source_bundle_sha256()==module.digest({'schema':module.SOURCE_SCHEMA,'modules':modules})


def test_declared_writer_uid_is_checked_without_claiming_effective_process_uid(tmp_path,monkeypatch):
    producer,intent,pod=source(tmp_path)
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    observed=producer.collect()
    identity=observed['execution_binding']['writer_identity']
    assert identity['declared_uid']==intent.assets.engine_uid
    assert identity['provenance']=='pod_security_context'
    assert 'effective_uid' not in identity
    pod['spec']['containers'][0]['securityContext']={'runAsUser':0,'capabilities':{'drop':['ALL']}}
    producer.query=lambda _:{'items':[deepcopy(pod)]}
    observed=producer.collect()
    assert observed['execution_binding']['state']=='mismatch'
    assert 'cache_writer_uid_mismatch' in observed['execution_binding']['issues']
    assert observed['execution_binding']['writer_identity']['capabilities_drop']==['ALL']
    pod['spec']['containers'][0].pop('securityContext')
    pod['spec'].pop('securityContext')
    observed=producer.collect()
    assert observed['state']=='unavailable'
    assert observed['error_code']=='CACHE_WRITER_UID_UNDECLARED'


@pytest.mark.parametrize('historical', ['Failed', 'Succeeded', 'terminating'])
def test_cache_selects_current_running_pod_while_retaining_historical_artifacts(tmp_path,monkeypatch,historical):
    producer,_,pod=source(tmp_path)
    previous=deepcopy(pod)
    previous['metadata']['uid']='previous-pod'
    if historical=='terminating':previous['metadata']['deletionTimestamp']='2026-11-08T00:00:00Z'
    else:previous['status']['phase']=historical
    previous2=deepcopy(previous);previous2['metadata']['uid']='older-pod'
    producer.query=lambda _:{'items':[previous,previous2,deepcopy(pod)]}
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    document=producer.collect()
    assert document['state']=='observed'
    assert document['execution_binding']['pod_uid']==pod['metadata']['uid']
    assert document['execution_binding']['state']=='match'


@pytest.mark.parametrize('second_phase',['Running','Pending'])
def test_cache_refuses_two_active_pods_even_with_one_running(tmp_path,monkeypatch,second_phase):
    producer,_,pod=source(tmp_path)
    another=deepcopy(pod);another['metadata']['uid']='second-active'
    another['status']['phase']=second_phase
    producer.query=lambda _:{'items':[deepcopy(pod),another]}
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    document=producer.collect()
    assert document['state']=='unavailable'
    assert document['error_code']=='CACHE_UNIQUE_POD_REQUIRED'


def test_cache_never_treats_pending_pod_as_running(tmp_path,monkeypatch):
    producer,_,pod=source(tmp_path)
    pod['status']['phase']='Pending'
    producer.query=lambda _:{'items':[deepcopy(pod)]}
    monkeypatch.setattr(module,'read_protected',lambda path,*args,**kwargs:Path(path).read_bytes())
    document=producer.collect()
    assert document['state']=='unavailable'
    assert document['error_code']=='CACHE_RUNNING_POD_REQUIRED'
