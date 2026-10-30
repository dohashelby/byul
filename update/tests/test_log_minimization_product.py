"""실제 B 수집·manual 연결과 불완전 EXAMPLE 로그의 통합 회귀."""
from copy import deepcopy
import json
from types import SimpleNamespace

from guard.collectors import kubernetes_log_minimization as collector
from guard.collectors import log_minimization_evidence as bridge
from guard.models import DataOrigin
from guard.store import Store
from tests.test_log_minimization_evidence import case as prepared_case
from tests.test_kubernetes_log_minimization import World, PRIVATE


def test_complete_violation_before_utf8_damage_reaches_original_evidence_and_restore(prepared_case, monkeypatch):
    case = prepared_case
    assert case['env'].data_origin == DataOrigin.EXAMPLE
    captures = []

    def capture(environment, target, values, **kwargs):
        lifecycle = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': target['lab'],
            'run_id': target['kubernetes']['run_id'], 'environment_ref': environment.ref().model_dump(mode='json'),
            'kubernetes': {'context': target['kubernetes']['context'], 'engine': deepcopy(target['kubernetes']['engine'])}}
        world = World(SimpleNamespace(env=environment, target=target, lifecycle=lifecycle,
            ref=kwargs['expected_policy_ref'], values=values, work=kwargs['workdir']), mode='utf8-suffix')
        result = collector.capture_log_minimization(environment, target, values,
            _runner=world.runner, _transport_factory=world.factory, **kwargs)
        captures.append((world, result))
        return result

    monkeypatch.setattr(bridge, '_capture', capture)
    summary = bridge.run(**case['args'], execute=True)
    assert len(captures) == 1
    world, observed = captures[0]
    assert len(world.requests) == 3 and world.reads == 1 and world.meta > 0
    assert observed['analysis_binding_verified'] and observed['source_generation_verified']
    assert observed['field_violation_count'] > 0 and observed['forbidden_error_input_count'] == 1
    assert observed['source_reads'][0]['complete'] is False
    assert summary['security'] == 'FAIL' and summary['exact_refs_preserved']
    assert summary['source_unchanged'] and summary['inputs_unchanged']
    assert all(not path.exists() for path in world.scratch)
    for folder in ('store', 'restored'):
        store = Store.read_only(case['args']['output'] / folder)
        assert store.verify() == []
        evidence, = list(store.iter_kind('EVD'))
        assert evidence.plan == case['plan'].ref() and evidence.environment == case['env'].ref()
        assert not evidence.normal_control.attempted
        assert evidence.observations['declared_verdicts'].value == {
            'security': 'FAIL', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
        assert evidence.observations['log_required_branches_tested'].value == []
        assert evidence.observations['log_fields_seen'].value is None
        record = json.loads((store.root / evidence.artifacts[0].path).read_bytes())
        text = json.dumps(record)
        assert PRIVATE not in text and 'b2error-' not in text and 'byul-req13-error-' not in text
        assert 'text_original_sha256' not in text and 'unexpected_' not in text
