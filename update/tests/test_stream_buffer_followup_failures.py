"""Preserve produced facts when final transport/report metadata fails."""
import pytest
from guard.executors import kubernetes_stream_buffer as stream
from guard.executors import kubernetes_process_rss as rss
from guard.runtime.kubernetes_stream_buffer_http import KubernetesStreamBufferHTTP
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_owner2_product_paths import test_lif03_real_registered_factory_through_saved_pack_rejudgment as run_existing_product_contract
from tests.test_a1_stream_buffer_product import inputs
from guard.adapters.vllm import VllmAdapter
from guard.models import canonical_json,ExecStatus


@pytest.mark.parametrize('boundary,code',[
    ('reader','STREAM_BUFFER_READER_REPORT_UNAVAILABLE'),
    ('http','STREAM_BUFFER_HTTP_REPORT_UNAVAILABLE'),
])
def test_report_failure_preserves_prior_verified_rss_fail_through_stored_pack(tmp_path,group,native_target,monkeypatch,boundary,code):
    cls=rss.KubernetesProcessRSSReader if boundary=='reader' else KubernetesStreamBufferHTTP
    original=cls.report
    def failing_report(self):
        original(self)
        raise OSError('EXAMPLE_PRIVATE_REPORT_ERROR')
    monkeypatch.setattr(cls,'report',failing_report)
    # Existing producer -> native/proc consumer -> registry -> EVD/RES/RPT ->
    # restore/rejudge contract. Only its final report boundary is newly faulty.
    run_existing_product_contract(tmp_path,group,native_target,monkeypatch,'cleanup','FAIL')
    from guard.store import Store
    store=Store.read_only(tmp_path/'product/restored')
    evd,=store.iter_kind('EVD')
    assert code in evd.execution.abort_reason
    assert 'EXAMPLE_PRIVATE_REPORT_ERROR' not in canonical_json(evd.to_dict()).decode()


def test_actual_stream_collect_diagnostic_reaches_abort_reason(tmp_path,group,native_target):
    world,values,pack,_=inputs(tmp_path,group,native_target,'after-timeout')
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    executor=stream.KubernetesStreamBufferExecutor({'target':str(target),'prepared':str(group.manifest_path)},runner=world.runner)
    draft=executor.run(pack.checks['LIF-04'],group.env,values,VllmAdapter(),tmp_path/'evidence')
    assert draft.status==ExecStatus.ABORTED
    assert 'STREAM_BUFFER_PROBE_UNAVAILABLE' in draft.abort_reason
    assert draft.observations['buffer_native_samples'].value
