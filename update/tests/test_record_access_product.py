"""Real B collector/manual bridge integration with EXAMPLE Kubernetes replies."""
import json
from types import SimpleNamespace

from guard.collectors import kubernetes_record_access as collector
from guard.collectors import record_access_evidence as bridge
from guard.models import DataOrigin
from guard.store import Store
from tests.test_record_access_evidence import case as prepared_case
from tests.test_kubernetes_record_access import World, PRIVATE


def test_real_collector_reaches_original_pack_evidence_and_restored_verdict(prepared_case, monkeypatch):
    case = prepared_case
    assert case['env'].data_origin == DataOrigin.EXAMPLE
    # The shared metadata protocol fixture explicitly selects fixture-context.
    target = bridge._read(case['args']['target'])
    target['kubernetes']['context'] = 'fixture-context'
    case['args']['target'].write_text(json.dumps(target))
    case['subjects']['target_sha256'] = bridge._digest(target)
    case['args']['subjects'].write_text(json.dumps(case['subjects']))
    calls = []

    def collect(environment, target, subjects, **kwargs):
        world = World(SimpleNamespace(env=environment, target=target, subjects=subjects,
                                      work=kwargs['workdir']))
        value = collector.capture_record_access(environment, target, subjects,
                                                _runner=world.runner, **kwargs)
        calls.append((world, value))
        return value

    # Only the explicit EXAMPLE transport seam is injected; the real collector,
    # public A metadata reader, manual importer and judges run together.
    monkeypatch.setattr(bridge, '_capture', collect)
    result = bridge.run(**case['args'], execute=True)
    assert len(calls) == 1
    world, observed = calls[0]
    assert (world.meta_calls, world.who_calls, world.logs_calls) == (12, 6, 3)
    assert observed['normal_control'] == {'attempted': True, 'succeeded': True}
    assert observed['cleanup'] == {'complete': True, 'collection_gap': False}
    assert all(not path.exists() for path in world.scratch)
    assert result['security'] == 'FAIL' and result['storage_encryption'] == 'UNKNOWN'
    assert result['source_unchanged'] and result['inputs_unchanged'] and result['exact_refs_preserved']
    for folder in ('store', 'restored'):
        store = Store.read_only(case['args']['output'] / folder)
        assert store.verify() == []
        evd, = list(store.iter_kind('EVD'))
        assert evd.environment == case['env'].ref() and not evd.normal_control.attempted
        assert evd.observations['declared_verdicts'].value == {
            'security': 'FAIL', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
        assert len(evd.artifacts) == 1
        record = json.loads((store.root / evd.artifacts[0].path).read_bytes())
        serialized = json.dumps(record)
        assert PRIVATE not in serialized and 'content_sha256' not in serialized
        assert all(subject['username'] not in serialized for subject in case['subjects']['subjects'])
        assert record['data_origin'] == 'EXAMPLE' and record['storage_encryption'] == 'UNKNOWN'
