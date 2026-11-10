"""Pinned main producer examples, current consumer, and strict old-consumer limits."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from byul_platform.contracts import Contracts
from byul_platform.sources.runtime import observation_event, request_detail_doc, lab_report_doc

OLD = Path(__file__).parent / 'fixtures/contracts-main-3bd6a995'


def test_frozen_main_fixture_bytes():
    provenance = json.loads((OLD/'provenance.json').read_text())
    assert provenance['commit'] == '3bd6a99511b628b6824a7bafd2de0cb7daa614bc'
    for entry in provenance['files']:
        relative = Path(entry['source_path']).relative_to('contracts')
        assert hashlib.sha256((OLD/relative).read_bytes()).hexdigest() == entry['sha256']


@pytest.mark.parametrize('name', ['runtime-event','runtime-request-detail','runtime-status','runtime-lab-report'])
def test_current_consumer_accepts_exact_main_producer_examples(contracts, name):
    filename = 'runtime-events.jsonl' if name == 'runtime-event' else name+'.json'
    raw = (OLD/'examples'/filename).read_text()
    documents = [json.loads(line) for line in raw.splitlines() if line] if filename.endswith('jsonl') else [json.loads(raw)]
    for doc in documents:
        assert not Contracts(OLD).errors(name, doc)
        assert not contracts.errors(name, doc)


def test_health_and_scope_reference_require_updated_strict_consumer(contracts):
    old = Contracts(OLD)
    documents=[json.loads(line) for line in (OLD/'examples/runtime-events.jsonl').read_text().splitlines() if line]
    scoped=copy.deepcopy(next(doc for doc in documents if isinstance(doc.get('observation'),dict)))
    scoped['observation']['scope_ref']='a'*64
    assert not contracts.errors('runtime-event',scoped)
    assert old.errors('runtime-event',scoped)
    health = observation_event(dict(source='engine_health',kind='engine_unavailable',state='unavailable',
        observed_at='2026-11-08T10:00:00Z',event_key='a'*64,environment='lab',run_id='run',
        target_id='target',target_instance_id='instance',observation_session_id='sid',
        policy=dict(policy_id='p',policy_version='1',sha256='2'*64),data_origin='real'))
    assert not contracts.errors('runtime-event',health)
    assert old.errors('runtime-event',health)


def test_new_query_window_is_optional_but_old_strict_schema_rejects_it(contracts):
    doc = request_detail_doc(dict(request_id='a'*32,gateway_records=[],engine_receipts=[],window=dict(
        truncated=True,gateway_search_complete=False,engine_search_complete=False,max_search_bytes=1024)))
    assert not contracts.errors('runtime-request-detail',doc)
    assert Contracts(OLD).errors('runtime-request-detail',doc)
    doc.pop('query_window')
    assert not Contracts(OLD).errors('runtime-request-detail',doc)


@pytest.mark.parametrize('case_id', ['L-05b','L-05c'])
def test_new_lab_ids_preserve_unknown_and_need_updated_strict_consumer(contracts, case_id):
    original=json.loads((OLD/'examples/runtime-lab-report.json').read_text())
    original['report']['cases'][0]['id']=case_id
    original['report']['cases'][0]['status']='unknown'
    doc=lab_report_doc(original)
    assert not contracts.errors('runtime-lab-report',doc)
    assert Contracts(OLD).errors('runtime-lab-report',doc)
    assert doc['report']['cases'][0]['status']=='unknown'


def test_status_lower_bounds_and_native_detail_require_current_consumer(contracts):
    from byul_platform.sources.runtime import _collector_detail
    doc=json.loads((OLD/'examples/runtime-status.json').read_text())
    doc['observations'].update(counts_lower_bound=True,history_truncated=True)
    assert not contracts.errors('runtime-status',doc)
    assert Contracts(OLD).errors('runtime-status',doc)
    doc['observations'].pop('counts_lower_bound')
    doc['observations'].pop('history_truncated')
    collector=copy.deepcopy(doc['collectors'][0])
    collector['source']='native_receipt_observer'
    collector['detail']=_collector_detail(dict(source='native_receipt_observer',last_value=dict(
        capture_state='stopped',future_observation_available=False,historical_receipts=2)))
    doc['collectors'].append(collector)
    assert not contracts.errors('runtime-status',doc)
    assert Contracts(OLD).errors('runtime-status',doc)
    assert collector['detail']['future_observation_available'] is False
