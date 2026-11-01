"""Declared model reference scope, bytes pins and public preparation commands."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.collectors.model_read_scope import build_scope,validate_binding
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.models import canonical_json,sha256_hex
from guard.adapters import vllm_file_read_sensor as sensor
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_file_read_sensor import scope_value,MODEL_BYTES

REPO=Path(__file__).resolve().parents[2]


def reference(tmp_path,group,target):
    files=[{'path':'public.safetensors','sha256':sha256_hex(MODEL_BYTES),'bytes':len(MODEL_BYTES)}]
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-public';env.model.revision='EXAMPLE-revision';env.model.inventory_sha256=sha256_hex(canonical_json(files));env=env.seal()
    target=deepcopy(target);target['environment_ref']=env.ref().model_dump(mode='json')
    manifest=tmp_path/'reference.json';manifest.write_bytes(canonical_json({'schema':'guard.model-manifest/1','scope':'all','model_id':env.model.id,'files':files,'inventory_sha256':env.model.inventory_sha256}))
    return env,target,manifest


def test_public_scope_and_sensor_preparation_do_not_read_model_or_start_runtime(tmp_path,group,native_target,monkeypatch):
    env,target,manifest=reference(tmp_path,group,native_target)
    (tmp_path/'environment.json').write_bytes(canonical_json(env.to_dict()));(tmp_path/'target.json').write_bytes(canonical_json(target))
    original=Path.open
    def opened(path,*args,**kwargs):
        assert not str(path).startswith('/models/UNMOUNTED')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    runner=CliRunner();scope=tmp_path/'scope.json'
    result=runner.invoke(app,['prepare-model-read-scope','--env',str(tmp_path/'environment.json'),'--target',str(tmp_path/'target.json'),
        '--model-manifest',str(manifest),'--model-root','/models/UNMOUNTED','--nonsecret-model-weights','--hash-model-bytes','--output',str(scope)])
    assert result.exit_code==0,(result.output,result.exception)
    destination=tmp_path/'assets'
    result=runner.invoke(app,['prepare-lifecycle-sensor','--profile',sensor.PROFILE,'--model-read-scope',str(scope),'--output',str(destination)])
    assert result.exit_code==0,(result.output,result.exception)
    prepared=load_prepared(destination/'manifest.json');validate_binding(prepared,env,target)
    assert prepared['model_read_scope']['data_origin']=='EXAMPLE' and prepared['engine_started'] is False
    assert sha256_hex((destination/'guard_weight_iteration.py').read_bytes())==sensor.ITERATION16_SHA256
    assert sha256_hex((destination/'guard_lifecycle_base.py').read_bytes())==sensor.BASE8_SHA256
    assert '/models/UNMOUNTED' not in (destination/'model-read-scope.json').read_text()


@pytest.mark.parametrize('changed',['absent','secret','origin','limit','duplicate','session','wrong-profile'])
def test_bad_scope_fails_before_output_reservation(tmp_path,changed):
    value=scope_value(tmp_path/'public.safetensors')
    if changed=='secret':value['nonsecret_model_weights']=False
    elif changed=='origin':value['data_origin']='MEASURED'
    elif changed=='limit':value['max_hash_bytes']=True
    elif changed=='duplicate':value['files']*=2
    elif changed=='session':value['session']='b'*32
    path=tmp_path/'scope.json';path.write_bytes(canonical_json(value));destination=tmp_path/'absent-output'
    with pytest.raises(ValueError):prepare_assets(destination,REPO,profile=None if changed=='wrong-profile' else sensor.PROFILE,model_read_scope=None if changed=='absent' else path)
    assert not destination.exists()


@pytest.mark.parametrize('changed',['env','target','model','revision','inventory','version'])
def test_target_generation_model_and_version_binding_before_kubectl(tmp_path,group,native_target,changed):
    env,target,manifest=reference(tmp_path,group,native_target)
    value=build_scope(env,target,manifest,'/models/public',nonsecret_model_weights=True)
    path=tmp_path/'scope.json';path.write_bytes(canonical_json(value));prepare_assets(tmp_path/'assets',REPO,profile=sensor.PROFILE,model_read_scope=path)
    prepared=load_prepared(tmp_path/'assets/manifest.json')
    if changed=='env':env=env.model_copy(deep=True);env.id='ENV-OTHER';env=env.seal()
    elif changed=='target':target=deepcopy(target);target['run_id']='other-run'
    elif changed in {'model','revision','inventory'}:
        prepared['model_read_scope'][{'model':'model_sha256','revision':'revision_sha256','inventory':'inventory_sha256'}[changed]]='0'*64
    else:env=env.model_copy(deep=True);env.engine.version='0.32.0';env=env.seal()
    calls=[]
    with pytest.raises(ValueError):KubernetesLifecycleReader(target,env,prepared,runner=lambda *a,**k:calls.append(a))
    assert calls==[]


@pytest.mark.parametrize('changed',['scope','dependency','sensor'])
def test_prepared_asset_bytes_cannot_be_silently_reapproved(tmp_path,changed):
    scope=tmp_path/'scope.json';scope.write_bytes(canonical_json(scope_value(tmp_path/'public.safetensors')))
    destination=tmp_path/'assets';prepare_assets(destination,REPO,profile=sensor.PROFILE,model_read_scope=scope)
    filename={'scope':'model-read-scope.json','dependency':'guard_weight_iteration.py','sensor':'guard_lifecycle_sensor.py'}[changed]
    with (destination/filename).open('ab') as stream:stream.write(b' ')
    with pytest.raises(ValueError,match='ASSET_BYTES'):load_prepared(destination/'manifest.json')
