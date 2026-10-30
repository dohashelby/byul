"""B 원본은 보존하며 실제 반환 경계와 source bytes 거부를 확인하는 CPU EXAMPLE."""
from copy import deepcopy
import hashlib
import sys
import types

import pytest

from guard.adapters import vllm_graph_buffers as graph
from guard.collectors import log_minimization_evidence as bridge
from guard.store import Store
from tests.test_log_minimization_evidence import case as prepared_case
from tests.test_vllm_graph_buffers import Object


@pytest.mark.parametrize('field', ['policy_values_sha256', 'policy_ref'])
def test_returned_capture_policy_mismatch_reaches_consumer_and_creates_no_evidence(
        prepared_case, monkeypatch, field):
    case = prepared_case
    returned = deepcopy(case['capture'])
    returned[field] = 'EXAMPLE_WRONG_POLICY_BINDING'
    calls = []

    def capture(environment, target, policy_values, **kwargs):
        assert environment.ref() == case['env'].ref()
        assert kwargs['expected_policy_ref'] == case['pol'].ref()
        assert bridge._digest(policy_values) == bridge._digest(case['values'])
        calls.append(True)
        return returned

    monkeypatch.setattr(bridge, '_capture', capture)
    with pytest.raises(ValueError, match='^OBS03_CAPTURE_BINDING_INVALID$'):
        bridge.run(**case['args'], execute=True)
    assert calls == [True]
    assert list(Store.read_only(case['args']['output'] / 'store').iter_kind('EVD')) == []


CONTEXT_SOURCE = (
    b'_forward_context = None\n'
    b'def is_forward_context_available():\n'
    b'    return _forward_context is not None\n'
    b'def get_forward_context():\n'
    b'    assert _forward_context is not None\n'
    b'    return _forward_context\n'
)
# 검토한 EXAMPLE LF bytes의 고정 값. 제품의 vLLM pin은 변경하지 않는다.
EXAMPLE_CONTEXT_SHA256 = '88811d9a428405b3bd36395d907ab229a5a9b011cee2bb33f21029d825fc5169'


@pytest.mark.parametrize('line_endings', ['LF', 'CRLF'])
def test_graph_source_requires_exact_bytes_even_when_compiled_code_is_equal(
        tmp_path, monkeypatch, line_endings):
    assert hashlib.sha256(CONTEXT_SOURCE).hexdigest() == EXAMPLE_CONTEXT_SHA256
    path = tmp_path / 'forward_context.py'
    raw = CONTEXT_SOURCE if line_endings == 'LF' else CONTEXT_SOURCE.replace(b'\n', b'\r\n')
    path.write_bytes(raw)
    module = types.ModuleType('vllm.forward_context')
    module.__file__ = str(path)
    exec(compile(CONTEXT_SOURCE, str(path), 'exec'), vars(module))
    monkeypatch.setitem(sys.modules, 'vllm.forward_context', module)
    monkeypatch.setattr(graph, 'FORWARD_CONTEXT_SHA256', EXAMPLE_CONTEXT_SHA256)
    observer = graph.GraphBufferObserver()
    try:
        result = observer.snapshot('CUDAGraphWrapper.__call__',
            (Object(runtime_mode=Object(_name_='FULL'), concrete_cudagraph_entries={}),),
            {}, phase='CALL', binding={'pid': __import__('os').getpid(),
                'call_id_sha256': 'c' * 64, 'session_id_sha256': 'd' * 64,
                'process_start_sha256': 'e' * 64, 'native_request_id_sha256': 'f' * 64})
        expected = 'absent' if line_endings == 'LF' else 'unavailable'
        assert result['metadata']['forward_context_state'] == expected
        assert ('FORWARD_CONTEXT_PROVENANCE_UNAVAILABLE' in result['gaps']) == (line_endings == 'CRLF')
        assert 'actual_worker_observed' not in result
    finally:
        observer.close()
