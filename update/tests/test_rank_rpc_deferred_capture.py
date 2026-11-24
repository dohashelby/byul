"""Closed signed Future snapshots are source facts, without GPU qualification."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json

import pytest

from guard.adapters import vllm_rank_ipc_runtime as runtime
from tools import rank_ipc_candidates as tool
from tests.test_rank_ipc_runtime import _prepared
from tests.test_rank_rpc_deferred import deferred


@pytest.fixture
def capture(tmp_path,deferred):
    module,observer,rows,owner,scheduled=deferred
    root,settings,manifest=_prepared(tmp_path)
    relative='guard/adapters/assets/vllm_rank_ipc_profiles.json'
    raw=tool.canonical({'schema':'guard-rank-ipc-source-candidates/1','profiles':[observer.profile]})
    (root/relative).write_bytes(raw)
    settings['assets'][relative]={'bytes':len(raw),'sha256':sha256(raw).hexdigest()}
    raw=tool.canonical(settings);(root/'settings.json').write_bytes(raw)
    manifest.update(assets=settings['assets'],settings_sha256=sha256(raw).hexdigest())
    (root/'manifest.json').write_bytes(tool.canonical(manifest))
    observer.emit=runtime.SnapshotWriter(settings,manifest['settings_sha256'])
    owner.future.set_exception(TimeoutError('PRIVATE_ERROR'))
    future=owner.execute(scheduled)
    with pytest.raises(TimeoutError):future.result()
    folder=root/'data'/settings['session']
    result=tool.verify_capture(root,folder)
    assert result['snapshots']==result['source_rows_verified']==4 and result['gaps']==[]
    assert result['rows'][-1]['exception_kind']=='timeout'
    assert settings['key']not in json.dumps(result)and 'PRIVATE_ERROR'not in json.dumps(result)
    assert result['producer_registered']is result['engine_group_verified']is result['source_authenticated']is False
    return root,settings,folder


@pytest.mark.parametrize('damage',['future-object','origin-call','origin-point','bool-time','time-order','extra-binding','exception-kind','missing-kind','call-exception'])
def test_even_resigned_inconsistent_deferred_snapshot_is_refused(capture,damage):
    root,settings,folder=capture
    for path in folder.glob('pid-*.json'):
        record=json.loads(path.read_bytes());row=record['snapshot']['payload']
        if row.get('event')==('CALL'if damage=='call-exception'else'RAISE')and row.get('point')=='NativeFuture.result':break
    else:pytest.fail('original Future result observation is required')
    if damage=='future-object':row['deferred_rpc']['future_object_hmac_sha256']='0'*64
    elif damage=='origin-call':row['deferred_rpc']['origin_call_hmac_sha256']=row['call_hmac_sha256']
    elif damage=='origin-point':row['deferred_rpc']['point']='NativeFuture.result'
    elif damage=='bool-time':row['deferred_rpc']['origin_started_ns']=True
    elif damage=='time-order':row['deferred_rpc']['origin_returned_ns']=row['operation_started_ns']+1
    elif damage=='extra-binding':row['deferred_rpc']['raw_message']='PRIVATE_ERROR'
    elif damage=='exception-kind':row['exception_kind']='PRIVATE_ERROR'
    elif damage=='missing-kind':del row['exception_kind']
    else:row['exception_kind']='timeout'
    record['hmac_sha256']=hmac.new(bytes.fromhex(settings['key']),tool.canonical(record['snapshot']),sha256).hexdigest()
    path.write_bytes(tool.canonical(record))
    with pytest.raises(ValueError,match='RANK_RPC_SOURCE_RECORD_INVALID'):tool.verify_capture(root,folder)
