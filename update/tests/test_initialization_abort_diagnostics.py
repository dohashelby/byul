"""M09 metadata regression: retain the original diagnostic without changing status."""
from types import SimpleNamespace
import pytest
from guard.executors.registry import build_executor
from guard.executors.http_common import HttpCheckExecutor
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin,ExecStatus


@pytest.mark.parametrize('gap,codes,cleanup,original,expected',[
    (True,['INITIALIZATION_READY_ENDPOINT_PARTIAL'],[],None,'INITIALIZATION_READY_ENDPOINT_PARTIAL'),
    (True,['INITIALIZATION_READY_ENDPOINT_PARTIAL'],[],'EXAMPLE original failure','EXAMPLE original failure'),
    (True,[],['INITIALIZATION_PARENT_CLEANUP_UNVERIFIED'],None,'INITIALIZATION_PARENT_CLEANUP_UNVERIFIED'),
    (True,[],[],None,'INITIALIZATION_COLLECTION_INCOMPLETE'),
    (False,[],[],None,None),
])
def test_abort_reason_carries_fixed_collector_codes_without_replacing_original(monkeypatch,gap,codes,cleanup,original,expected):
    draft=EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,
        normal_control={'attempted':True,'succeeded':None},
        observations={'kept':{'value':['EXAMPLE original observation'],'collected':True}},abort_reason=original)
    snapshot=draft.observations.copy()
    monkeypatch.setattr(HttpCheckExecutor,'_finish',lambda self,ctx:draft)
    executor=build_executor('initialization','kubernetes',{})
    ctx=SimpleNamespace(_init_gap=gap,_init_cleanup=cleanup,_init_diagnostic_codes=codes)
    result=executor._finish(ctx)
    assert result is draft and result.observations==snapshot
    assert result.status==(ExecStatus.ABORTED if gap else ExecStatus.COMPLETED)
    assert result.abort_reason==expected
    assert result.cleanup_errors==cleanup
