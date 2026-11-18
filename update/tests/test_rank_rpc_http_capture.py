"""Signed EXAMPLE HTTP metadata and exact installed async-source pin tests."""
from hashlib import sha256
import hmac
import json
from pathlib import Path
import sys
import types

import pytest

from guard.adapters import vllm_rank_ipc_runtime as runtime
from guard.adapters import vllm_rank_ipc_sources as sources
from tools import rank_ipc_candidates as tool
from tests.test_rank_ipc_runtime import _prepared
from tests.test_rank_rpc_http import setup,invoke


@pytest.mark.parametrize('module_name',['uvicorn.protocols.http.h11_impl','uvicorn.protocols.http.httptools_impl'])
def test_exact_installed_async_send_body_is_qualified_without_executing_server(module_name):
    profile,=[p for p in json.loads(sources.PROFILES.read_bytes())['profiles']if p.get('ipc_topology',{}).get('kind')=='broadcast-workers']
    row=profile['rpc_sources'][module_name];path=Path(__file__).parent/'fixtures/vllm-0.30.0'/Path(row['path']+'.txt')
    raw=path.read_bytes();assert sha256(raw).hexdigest()==row['sha256']and len(raw)==row['bytes']
    code=sources._codes(compile(raw,str(path),'exec',dont_inherit=True,optimize=0))['RequestResponseCycle.send']
    assert code.co_flags&0x80 and not code.co_freevars
    module=types.ModuleType(module_name);module.__file__=str(path)
    module.RequestResponseCycle=type('RequestResponseCycle',(),{'send':types.FunctionType(code,vars(module),'send')})
    if sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))!=profile['runtime']:
        with pytest.raises(ValueError):sources._qualified(module,profile,'rpc_sources')
    else:
        qualified=sources._qualified(module,profile,'rpc_sources')
        assert qualified['engine_group_verified']is qualified['producer_registered']is qualified['observer_installed']is False


@pytest.fixture
def capture(tmp_path,setup):
    module,observer,rows,owner=setup;root,settings,manifest=_prepared(tmp_path)
    relative='guard/adapters/assets/vllm_rank_ipc_profiles.json'
    raw=tool.canonical({'schema':'guard-rank-ipc-source-candidates/1','profiles':[observer.profile]});(root/relative).write_bytes(raw)
    settings['assets'][relative]={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
    raw=tool.canonical(settings);(root/'settings.json').write_bytes(raw)
    manifest.update(assets=settings['assets'],settings_sha256=sha256(raw).hexdigest());(root/'manifest.json').write_bytes(tool.canonical(manifest))
    observer.emit=runtime.SnapshotWriter(settings,manifest['settings_sha256'])
    invoke(owner,{'type':'http.response.start','status':500});invoke(owner,{'type':'http.response.body','body':b'PRIVATE_BODY'})
    folder=root/'data'/settings['session'];review=tool.verify_capture(root,folder)
    assert review['snapshots']==review['source_rows_verified']==4 and not review['gaps']
    assert 'PRIVATE_BODY'not in json.dumps(review)and settings['key']not in json.dumps(review)
    return root,settings,folder


@pytest.mark.parametrize('damage',['header','bool-status','missing-status','path','extra','body-status','more-body','wrong-kind'])
def test_resigned_invalid_native_http_metadata_is_refused(capture,damage):
    root,settings,folder=capture
    for path in folder.glob('pid-*.json'):
        record=json.loads(path.read_bytes());row=record['snapshot']['payload'];sent=row['http_send']
        if row['event']=='RETURN'and sent['type']==('http.response.body'if damage in{'body-status','more-body'}else'http.response.start'):break
    else:pytest.fail('native original-send return is required')
    if damage=='header':sent['external_request_hmac_sha256']='0'*64
    elif damage=='bool-status':sent['status']=True
    elif damage=='missing-status':del sent['status']
    elif damage=='path':sent['path']='/v1/other'
    elif damage=='extra':sent['body']='PRIVATE_BODY'
    elif damage=='body-status':sent['status']=200
    elif damage=='more-body':sent['more_body']=1
    else:sent['type']='http.other'
    record['hmac_sha256']=hmac.new(bytes.fromhex(settings['key']),tool.canonical(record['snapshot']),sha256).hexdigest()
    path.write_bytes(tool.canonical(record))
    with pytest.raises(ValueError,match='RANK_RPC_SOURCE_RECORD_INVALID'):tool.verify_capture(root,folder)
