"""Local EXAMPLE assets from the real preparer; no remote executor calls."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path

import pytest

from guard.collectors.lifecycle import prepare_assets
from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import environment
from tools import b03_input_materialize as tool

ROOT=Path(__file__).resolve().parents[2]


def pointer(path):
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def save(path,value):
    path.write_bytes(canonical_json(value))
    return pointer(path)


@pytest.fixture
def inputs(tmp_path,monkeypatch):
    def no_remote(*args,**kwargs):
        raise AssertionError('Remote run/collection is outside preflight')
    monkeypatch.setattr(KubernetesSupplyBootExecutor,'run',no_remote)
    monkeypatch.setattr(KubernetesSupplyBootExecutor,'_collect_role',no_remote)
    pack=load_pack(ROOT/'update/packs/PACK-BASELINE')
    env=environment()
    env_path=tmp_path/'environment.json';save(env_path,env.model_dump(mode='json'))
    spec={'schema':'b03-supply-pair-inputs/1','check_id':'SUP-07',
          'pack_ref':pack.ref().model_dump(mode='json'),'environment':pointer(env_path),'roles':{}}
    now=utcnow()
    values={'supply_environment_sha256':env.ref().sha256,
            'supply_record_source_sha256':'1'*64,
            'supply_validity_start':(now-timedelta(minutes=2)).isoformat(),
            'supply_validity_end':(now+timedelta(minutes=5)).isoformat()}
    cfg={'adapter':{'name':env.engine.name},'executors':{'supply_boot':'kubernetes'},'supply_boot':{}}
    for role in ('normal','challenge'):
        prepared_dir=tmp_path/role
        prepared=prepare_assets(prepared_dir,ROOT)
        target={'lab':True,'environment_ref':env.ref().model_dump(mode='json'),
                'EXAMPLE_non_runtime_role':role}
        target_path=tmp_path/(role+'-target.json');save(target_path,target)
        manifest=prepared_dir/'manifest.json'
        cfg['supply_boot'][role]={'target':str(target_path),'prepared':str(manifest)}
        spec['roles'][role]={'target':pointer(target_path),'prepared':pointer(manifest)}
        values['supply_'+role+'_target_sha256']=sha256_hex(canonical_json(target))
        values['supply_'+role+'_prepared_sha256']=sha256_hex(canonical_json(prepared))
    policy={'data_origin':'EXAMPLE','status':'DRAFT','pack':{'id':pack.ref().id,'revision':pack.ref().revision},
            'values':{'SUP-07':values}}
    spec['policy']=save(tmp_path/'policy.json',policy)
    spec['config']=save(tmp_path/'config.json',cfg)
    return spec,policy,cfg,env


def invoke(tmp_path,inputs,*,policy=None,cfg=None):
    spec=deepcopy(inputs[0])
    if policy is not None:spec['policy']=save(tmp_path/'policy.json',policy)
    if cfg is not None:spec['config']=save(tmp_path/'config.json',cfg)
    spec_path=tmp_path/'spec.json';save(spec_path,spec)
    return tool.materialize(spec_path,ROOT,tmp_path/'receipt')


def test_real_preparer_to_original_file_consumer_keeps_scope(tmp_path,inputs):
    result=invoke(tmp_path,inputs)
    assert result['local_input_checks_passed'] is True
    assert result['ready_for_GPU'] is False and result['engine_GPU_AWS_executed'] is False
    assert result['EVD_RES_RPT_Refs_created'] is False
    assert result['environment_data_origin']==result['policy_data_origin']=='EXAMPLE'
    assert len({r['session'] for r in result['roles']})==2
    assert json.loads((tmp_path/'receipt/supply-pair-preflight.json').read_bytes())==result


@pytest.mark.parametrize('fault',[
    'missing-file','changed-bytes','swapped-config-roles','same-session',
    'target-object-hash','target-environment','expired-policy','wrong-backend',
])
def test_invalid_pair_rejected_before_output(tmp_path,inputs,fault):
    spec,policy,cfg,env=inputs
    if fault=='missing-file':
        Path(spec['roles']['challenge']['target']['path']).unlink()
    elif fault=='changed-bytes':
        Path(spec['roles']['normal']['target']['path']).write_bytes(b'{}')
    elif fault=='swapped-config-roles':
        cfg['supply_boot']['normal'],cfg['supply_boot']['challenge']=cfg['supply_boot']['challenge'],cfg['supply_boot']['normal']
    elif fault=='same-session':
        cfg['supply_boot']['challenge']['prepared']=cfg['supply_boot']['normal']['prepared']
        spec['roles']['challenge']['prepared']=deepcopy(spec['roles']['normal']['prepared'])
        policy['values']['SUP-07']['supply_challenge_prepared_sha256']=policy['values']['SUP-07']['supply_normal_prepared_sha256']
    elif fault=='target-object-hash':
        p=Path(spec['roles']['normal']['target']['path']);target=json.loads(p.read_bytes())
        target['EXAMPLE_changed']=True
        spec['roles']['normal']['target']=save(p,target)
    elif fault=='target-environment':
        p=Path(spec['roles']['normal']['target']['path']);target=json.loads(p.read_bytes())
        target['environment_ref']=None
        spec['roles']['normal']['target']=save(p,target)
        policy['values']['SUP-07']['supply_normal_target_sha256']=sha256_hex(canonical_json(target))
    elif fault=='expired-policy':
        now=utcnow()
        policy['values']['SUP-07']['supply_validity_start']=(now-timedelta(minutes=10)).isoformat()
        policy['values']['SUP-07']['supply_validity_end']=(now-timedelta(minutes=5)).isoformat()
    elif fault=='wrong-backend':
        cfg['executors']['supply_boot']='real'
    with pytest.raises((ValueError,OSError)):
        invoke(tmp_path,inputs,policy=policy,cfg=cfg)
    assert not (tmp_path/'receipt').exists()


def unbound_spec():
    ptr={'path':'never-opened.json','sha256':'a'*64}
    return {'schema':'b03-supply-pair-inputs/1','check_id':'SUP-07','pack_ref':{},
        'environment':deepcopy(ptr),'policy':deepcopy(ptr),'config':deepcopy(ptr),
        'roles':{r:{k:deepcopy(ptr) for k in ('target','prepared')} for r in ('normal','challenge')}}


def reject_pointer_before_io(tmp_path,monkeypatch,spec):
    def no_read(*args,**kwargs):
        raise AssertionError('Malformed pointer must be rejected before pinned read')
    monkeypatch.setattr(tool,'pinned',no_read)
    save(tmp_path/'spec.json',spec)
    with pytest.raises(ValueError,match='^B03_SUPPLY_EXACT_FILE_POINTER_REQUIRED$'):
        tool.materialize(tmp_path/'spec.json',ROOT,tmp_path/'receipt')
    assert not (tmp_path/'receipt').exists()


@pytest.mark.parametrize('field',['environment','policy','config',
    'normal.target','normal.prepared','challenge.target','challenge.prepared'])
def test_null_digest_rejected_for_every_input(tmp_path,monkeypatch,field):
    spec=unbound_spec()
    if '.' in field:
        role,key=field.split('.');spec['roles'][role][key]['sha256']=None
    else:spec[field]['sha256']=None
    reject_pointer_before_io(tmp_path,monkeypatch,spec)


@pytest.mark.parametrize('digest',['','A'*64,'a'*63,'a'*65,123])
def test_noncanonical_digest_rejected_before_io(tmp_path,monkeypatch,digest):
    spec=unbound_spec();spec['environment']['sha256']=digest
    reject_pointer_before_io(tmp_path,monkeypatch,spec)


@pytest.mark.parametrize('path',[None,'',' ','\x00'])
def test_invalid_path_rejected_before_io(tmp_path,monkeypatch,path):
    spec=unbound_spec();spec['environment']['path']=path
    reject_pointer_before_io(tmp_path,monkeypatch,spec)
