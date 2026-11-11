"""Selected acceptance wrapper boundaries; real UID probes on root Linux only."""
from dataclasses import replace
import errno
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pytest
from serving_runtime.enforcement.intent import parse_intent

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('selected_asset_boundaries', ROOT/'tools/selected_asset_boundaries.py')
tool = importlib.util.module_from_spec(spec); spec.loader.exec_module(tool)


def intent():
    raw = (ROOT/'deploy/gpu-lab-sglang/intent.aws-lab.json').read_bytes()
    return parse_intent(json.loads(raw.replace(b'REPLACE_IMAGE_DIGEST_HEX', b'a'*64).replace(b'REPLACE_PROFILE', b'cpu')))


def test_read_only_inspection_preserves_inputs_and_exposes_scope(tmp_path):
    i = intent(); model=tmp_path/'model';model.mkdir()
    cache=tmp_path/'cache';cache.mkdir();leaf=Path(i.assets.cache_generation.path(str(cache)));leaf.mkdir(parents=True)
    i=replace(i,assets=replace(i.assets,cache_root=str(cache),engine_uid=os.getuid()))
    (model/'config.json').write_text('unchanged')
    before=(model/'config.json').read_bytes()
    bridge={'schema_version':'runtime-update-asset-check/1.0','asset_check':{'intact':True,'total':1,'items':[{'path':'config.json'}]},'ready_for_operator_review':True,'intent_sha256':'intent-sha','execution_binding':{'state':'match','model_host_path':str(model),
       'cache_host_path':str(leaf),'pod_uid':'pod','container_id':'container'}}
    result=tool.inspect_host(i,'intent-sha',str(model),bridge,'bridge-sha',(0,os.getuid()))
    assert result['state']=='observed' and result['source_binding_state']=='match'
    assert result['model_parent_metadata']['entries'][-1]['uid']==os.getuid()
    assert result['existing_check_cache']['current_generation']==str(leaf)
    assert result['actual_engine_cache_consumption']=='unverified'
    assert result['actual_condition_change_regeneration']=='unverified'
    assert result['approval_created'] is False and result['mutated_active_assets'] is False
    assert (model/'config.json').read_bytes()==before and list(model.iterdir())==[model/'config.json']
    bridge['intent_sha256']='rotated'
    assert tool.inspect_host(i,'intent-sha',str(model),bridge,'bridge-sha',(0,))['state']=='unknown'


def test_parent_symlink_is_not_traversed(tmp_path):
    secret=tmp_path/'real';secret.mkdir();(secret/'nested').mkdir()
    link=tmp_path/'link';link.symlink_to(secret,target_is_directory=True)
    observed=tool.metadata_chain(link/'nested')
    assert observed['state']=='blocked_symlink_or_non_directory'
    assert observed['entries'][-1]['path']==str(link)
    assert str(link/'nested') not in [r['path'] for r in observed['entries']]


def test_denial_requires_real_identity_and_permission_errno(monkeypatch):
    facts={'uid':2000,'gid':2000,'groups':[],'succeeded':False,'errno':errno.ENOENT}
    def run(*a,**kw):
        assert set(kw['env'])=={'PATH','LC_ALL'}
        assert 'PRIVATE_TOKEN' not in json.dumps(kw['env'])
        return subprocess.CompletedProcess([],0,json.dumps(facts).encode(),b'')
    monkeypatch.setattr(tool.subprocess,'run',run)
    assert tool.run_probe(2000,2000,'overwrite','/missing','deny')['state']=='unknown'
    facts['errno']=errno.EPERM
    assert tool.run_probe(2000,2000,'overwrite','/fixture','deny')['state']=='passed'
    facts['uid']=0
    assert tool.run_probe(2000,2000,'overwrite','/fixture','deny')['state']=='unknown'
    facts.update(uid=2000,succeeded=True)
    assert tool.run_probe(2000,2000,'overwrite','/fixture','deny')['state']=='failed'


def test_no_root_means_unexecuted_not_passed(tmp_path,monkeypatch):
    monkeypatch.setattr(tool.os,'geteuid',lambda:501)
    result=tool.isolated_uid_fixture(intent(),tmp_path/'must-not-exist','/active/model',engine_gid=2000)
    assert result['state']=='unknown' and result['probes']==[]
    assert not (tmp_path/'must-not-exist').exists()


def test_output_never_overwrites_and_cannot_land_in_live_assets(tmp_path):
    live=tmp_path/'active';live.mkdir();out=live/'new.json'
    with pytest.raises(ValueError):tool.validate_output(str(out),(str(live),))
    alias=tmp_path/'alias';alias.symlink_to(live,target_is_directory=True)
    with pytest.raises(ValueError):tool.validate_output(str(alias/'new.json'),(str(live),))
    original=tmp_path/'original.json';original.write_text('original')
    with pytest.raises(FileExistsError):tool.write_new(original,{'state':'passed'})
    assert original.read_text()=='original'


@pytest.mark.skipif(sys.platform!='linux' or os.geteuid()!=0,reason='root Linux required for actual UID transition')
@pytest.mark.parametrize('engine_gid',[2000,0])
def test_actual_root_engine_other_uid_tamper_and_readback(engine_gid):
    # /tmp is a real sticky root directory on the isolated Linux test host.
    # A new own subtree only; never point probes at model or active cache.
    root=Path(tempfile.mkdtemp(prefix='byul-selected-assets-parent-',dir='/tmp'))
    os.chmod(root,0o755)
    try:
        i=intent(); sandbox=root/'new-fixture'
        result=tool.isolated_uid_fixture(i,sandbox,'/active/model',engine_gid=engine_gid)
        assert result['state']=='passed',result
        assert len(result['probes'])==12
        assert {p['uid'] for p in result['probes']}=={0,2000,65534}
        assert all(p['identity_verified'] for p in result['probes'])
        assert all(Path(p['path']).is_relative_to(sandbox) for p in result['probes'])
        assert next(p for p in result['probes'] if p['action']=='replace')['source_path'].startswith(str(sandbox/'untrusted'))
        assert result['original_fixture_unchanged'] and result['fixture_reads_match']
        assert result['fixture_asset_check']['intact'] is False # allowed engine extra is reported
        assert result['fixture_asset_check']['extra_file_count']==1
        assert result['existing_check_cache']['state']=='ok'
        assert result['actual_engine_cache_consumption']=='unverified'
        leaf=Path(result['existing_check_cache']['current_generation'])
        role_checks=[tool.run_probe(u,g,'access',leaf,'observe') for u,g in ((0,0),(2000,engine_gid),(65534,65534))]
        assert [p['write_access'] for p in role_checks]==[True,True,False]
        assert role_checks[0]['state']==role_checks[1]['state']=='observed'
        # EACCES on ancestor traversal leaves exists unavailable for nobody.
        assert role_checks[2]['state'] in ('unknown','observed')
        assert (leaf/'artifact.bin').read_bytes()==tool.FIXTURE
        before=sorted(str(p) for p in sandbox.rglob('*'))
        with pytest.raises(FileExistsError):tool.isolated_uid_fixture(i,sandbox,'/active/model',engine_gid=2000)
        assert sorted(str(p) for p in sandbox.rglob('*'))==before
        with pytest.raises(ValueError):tool.prepare_sandbox_root(root/'forbidden', (root,))
    finally:shutil.rmtree(root)


@pytest.mark.skipif(sys.platform!='linux' or os.geteuid()!=0,reason='root Linux required for actual UID transition')
def test_unsafe_fixture_permissions_do_not_hide_successful_other_uid_replace(monkeypatch):
    root=Path(tempfile.mkdtemp(prefix='byul-selected-assets-negative-',dir='/tmp'));os.chmod(root,0o755)
    original=tool._tree
    def unsafe_tree(*args):
        leaf=original(*args)
        for path in (leaf,*list(leaf.parents)[:3]):os.chmod(path,0o755)
        os.chmod(leaf,0o777)
        return leaf
    monkeypatch.setattr(tool,'_tree',unsafe_tree)
    try:
        report=tool.isolated_uid_fixture(intent(),root/'new-fixture','/active/model',engine_gid=2000)
        assert report['state']=='failed'
        replacement=next(p for p in report['probes'] if p['action']=='replace')
        assert replacement['succeeded'] is True and replacement['state']=='failed'
        assert report['original_fixture_unchanged'] is False
        assert report['existing_check_cache']['state']=='problems'
        assert report['actual_engine_cache_consumption']=='unverified'
    finally:shutil.rmtree(root)


def test_wrong_report_shape_is_rejected_without_deriving_an_observation(tmp_path):
    i=intent()
    for report in ({}, {'schema_version':'runtime-update-asset-check/1.0','execution_binding':[],'asset_check':{}}, {'schema_version':'other','execution_binding':{},'asset_check':{}}):
        with pytest.raises(ValueError):tool.inspect_host(i,'sha','/active/model',report,'report-sha',(0,))
    source=tmp_path/'list.json';source.write_text('[]')
    with pytest.raises(ValueError):tool.frozen_json(source)


def test_approved_file_owner_mode_without_content_or_symlink_following(tmp_path):
    model=tmp_path/'model';model.mkdir();(model/'config.json').write_text('MODEL_CONTENT_MUST_NOT_BE_OUTPUT')
    report=tool.approved_file_metadata(model,{'total':1,'items':[{'path':'config.json'}]},(os.getuid(),))
    assert report['state']=='observed' and report['content_read'] is False
    assert report['entries'][0]['owner_is_declared_trusted_writer'] is True
    assert 'MODEL_CONTENT_MUST_NOT_BE_OUTPUT' not in json.dumps(report)
    (model/'config.json').unlink();(model/'config.json').symlink_to('/etc/passwd')
    blocked=tool.approved_file_metadata(model,{'total':1,'items':[{'path':'config.json'}]},(0,))
    assert blocked['state']=='unknown' and blocked['entries'][0]['state']=='blocked_non_regular'
    missing=tool.approved_file_metadata(model,{'total':1,'items':[{'path':'../outside'}]},(0,))
    assert missing['state']=='unknown'
