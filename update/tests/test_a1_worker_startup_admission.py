"""Closed public preparation, unchanged original dependency bytes and target gates."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.collectors.lifecycle import prepare_assets,load_prepared
from guard.adapters import vllm_worker_startup_sensor as sensor
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
from guard.models import canonical_json,sha256_hex
from tests.test_a1_model_read_scope import reference
from tests.test_lifecycle_capture import case
from tests.test_kubernetes_lifecycle import group,target as native_target
REPO=Path(__file__).resolve().parents[2]


def test_existing_public_cli_prepares_eleven_fixed_assets_without_worker_or_model_reads(tmp_path,group,native_target,monkeypatch):
    env,target,manifest=reference(tmp_path,group,native_target)
    for name,value in [('environment',env.to_dict()),('target',target)]: (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
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
    prepared=load_prepared(destination/'manifest.json')
    assert prepared['profile']==sensor.PROFILE and len(prepared['assets'])==11 and prepared['engine_started'] is False
    for name,digest in {'guard_lifecycle_base.py':sensor.BASE8_SHA256,'guard_weight_iteration.py':sensor.ITERATION16_SHA256,
        'guard_file_read_scope.py':sensor.SCOPE17_SHA256,'guard_safe_open_mapping.py':sensor.MAPPING18_SHA256,
        'guard_worker_startup_contract.py':sensor.CONTRACT_SHA256,'guard_worker_readiness_callback.py':sensor.CALLBACK_SHA256,
        'guard_worker_readiness_support.py':sensor.SUPPORT_SHA256}.items():
        assert sha256_hex((destination/name).read_bytes())==digest


@pytest.mark.parametrize('change',['env','target','model','revision','inventory','version'])
def test_new_profile_preserves_exact_target_model_and_native032_rejection_before_probe(tmp_path,group,native_target,change):
    from guard.collectors.model_read_scope import build_scope
    env,target,manifest=reference(tmp_path,group,native_target)
    scope=tmp_path/'scope.json';scope.write_bytes(canonical_json(build_scope(env,target,manifest,'/models/public',nonsecret_model_weights=True)))
    prepare_assets(tmp_path/'assets',REPO,profile=sensor.PROFILE,model_read_scope=scope)
    prepared=load_prepared(tmp_path/'assets/manifest.json');other=env.model_copy(deep=True);selected=deepcopy(target)
    if change=='env':other.id='ENV-OTHER'
    elif change=='target':selected['kubernetes']['engine']['pod_uid']='EXAMPLE-other'
    elif change=='version':other.engine.version='0.32.0'
    else:setattr(other.model,{'model':'id','revision':'revision','inventory':'inventory_sha256'}[change],'f'*64)
    other=other.seal()
    with pytest.raises(KubernetesCaptureError if change=='version' else ValueError):
        KubernetesLifecycleReader(selected,other,prepared,runner=lambda *a,**kw:pytest.fail('gate must close before a probe'))


def test_missing_scope_or_modified_original_callback_asset_is_not_auto_approved(tmp_path,group,native_target):
    with pytest.raises(ValueError,match='MODEL_READ_SCOPE_REQUIRED'):prepare_assets(tmp_path/'missing',REPO,profile=sensor.PROFILE)
    from guard.collectors.model_read_scope import build_scope
    env,target,manifest=reference(tmp_path,group,native_target);scope=tmp_path/'scope.json'
    scope.write_bytes(canonical_json(build_scope(env,target,manifest,'/models/public',nonsecret_model_weights=True)))
    prepare_assets(tmp_path/'assets',REPO,profile=sensor.PROFILE,model_read_scope=scope)
    path=tmp_path/'assets/guard_worker_readiness_callback.py';path.write_bytes(path.read_bytes()+b'\n')
    with pytest.raises(ValueError,match='LIFECYCLE_ASSET_BYTES_INVALID'):load_prepared(tmp_path/'assets/manifest.json')

