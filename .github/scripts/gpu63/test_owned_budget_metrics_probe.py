import pytest

from owned_budget_metrics_probe import collect, read_series


def test_only_compatible_pod_budget_metrics_are_returned():
    raw = b'''vllm:num_requests_running 0.0
vllm:kv_cache_usage_perc{engine="0"} 2.5e-2
vllm:kv_cache_usage_perc{engine="1"} 1e999
vllm:request_success_total{finished_reason="stop"} 5
other:metric 2
vllm:nonapproved_metric{token="test-placeholder"} 1
'''
    assert read_series(raw) == {'num_requests_running': [0.0], 'kv_cache_usage_perc': [0.025],
                                'request_success_total.stop': [5.0]}


@pytest.mark.parametrize('target, count', [('http://127.0.0.2:8000', 1), ('http://127.0.0.1:8080', 1),
                                          ('http://127.0.0.1:8000/other', 1), ('http://127.0.0.1:8000', 41)])
def test_mismatched_targets_and_unbounded_reads_are_rejected_before_http(target, count):
    with pytest.raises(ValueError, match='outside'):
        collect(target, '127.0.0.1', count)
