"""Public preparation and unchanged owned source boundary for profile18."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.collectors.model_read_scope import build_scope,validate_binding
from guard.adapters import vllm_safe_open_mapping_sensor as sensor
from guard.executors import lifecycle_capture as capture,owned_capture
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
from guard.models import canonical_json,sha256_hex
from tests.test_a1_model_read_scope import reference
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_capture import case,external
from tests.test_kubernetes_lifecycle import group,target as native_target

REPO=Path(__file__).resolve().parents[2]


def test_public_preparation_preserves_scope_and_all_three_original_dependencies(tmp_path,group,native_target,monkeypatch):
    env,target,manifest=reference(tmp_path,group,native_target)
    (tmp_path/'environment.json').write_bytes(canonical_json(env.to_dict()));(tmp_path/'target.json').write_bytes(canonical_json(target))
    original=Path.open
    def opened(path,*args,**kwargs):
        assert not str(path).startswith('/models/UNMOUNTED')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    runner=CliRunner();scope=tmp_path/'scope.json'
    result=runner.invoke(app,['prepare-model-read-scope','--env',str(tmp_path/'environment.json'),'--target',str(tmp_path/'target.json'),
        '--model-manifest',str(manifest),'--model-root','/models/UNMOUNTED','--nonsecret-model-weights','--output',str(scope)])
    assert result.exit_code==0,(result.output,result.exception)
    destination=tmp_path/'assets'
    result=runner.invoke(app,['prepare-lifecycle-sensor','--profile',sensor.PROFILE,'--model-read-scope',str(scope),'--output',str(destination)])
    assert result.exit_code==0,(result.output,result.exception)
    prepared=load_prepared(destination/'manifest.json');validate_binding(prepared,env,target)
    assert prepared['profile']==sensor.PROFILE and prepared['engine_started'] is False and len(prepared['assets'])==7
    for name,digest in {'guard_lifecycle_base.py':sensor.BASE8_SHA256,'guard_weight_iteration.py':sensor.ITERATION16_SHA256,'guard_file_read_scope.py':sensor.SCOPE17_SHA256}.items():
        assert sha256_hex((destination/name).read_bytes())==digest
    assert '/models/UNMOUNTED' not in (destination/'model-read-scope.json').read_text()


@pytest.mark.parametrize('changed',['env','target','model','revision','inventory','version'])
def test_target_and_model_generation_gate_before_any_probe(tmp_path,group,native_target,changed):
    env,target,manifest=reference(tmp_path,group,native_target)
    value=build_scope(env,target,manifest,'/models/public',nonsecret_model_weights=True)
    path=tmp_path/'scope.json';path.write_bytes(canonical_json(value));prepare_assets(tmp_path/'assets',REPO,profile=sensor.PROFILE,model_read_scope=path)
    prepared=load_prepared(tmp_path/'assets/manifest.json');other=env.model_copy(deep=True);selected=deepcopy(target)
    if changed=='env':other.id='ENV-OTHER'
    elif changed=='target':selected['kubernetes']['engine']['pod_uid']='EXAMPLE-other-generation'
    elif changed=='version':other.engine.version='0.32.0'
    else:setattr(other.model,{'model':'id','revision':'revision','inventory':'inventory_sha256'}[changed],'f'*64)
    other=other.seal()
    with pytest.raises(KubernetesCaptureError if changed=='version' else ValueError):
        KubernetesLifecycleReader(selected,other,prepared,runner=lambda *a,**kw:pytest.fail('gate must close before probe'))


@pytest.mark.parametrize('profile',['vllm-native-lifecycle-metadata/16','vllm-native-lifecycle-metadata/17',sensor.PROFILE])
def test_default_owned_reader_uses_existing_extended_two_MiB_contract(case,monkeypatch,profile):
    class ReachedReader(Exception):pass
    calls=[]
    def read(*a,**kw):calls.append(kw);raise ReachedReader
    monkeypatch.setattr(capture,'_read_owned_file',read)
    with pytest.raises(ReachedReader):capture.capture(None,case.target,source=case.source,prepared={'profile':profile})
    assert len(calls)==1 and calls[0]['max_bytes']==2*1024**2 and calls[0]['read_profile']==owned_capture.EXTENDED_RECORDING_PROFILE
