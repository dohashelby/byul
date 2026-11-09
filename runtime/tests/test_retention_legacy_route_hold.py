"""Keep historical route audit and request evidence together while a run is held."""
from datetime import datetime, timedelta, timezone
import json
import pytest
from serving_runtime.mvp.retention import HOLD_SCHEMA, RecordRetention
from serving_runtime.mvp.store import JsonlStore

NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)
OLD = (NOW - timedelta(days=8)).isoformat()
NAME = 'gateway_route_rejections.jsonl'


def write_hold(path, runs):
    path.write_text(json.dumps({'schema': HOLD_SCHEMA, 'run_ids': runs}), encoding='utf-8')


def test_legacy_route_hold_propagates_to_request_files_then_release_deletes(tmp_path):
    route = JsonlStore(tmp_path / NAME)
    route.append({'schema_version': 'runtime-route-rejection/1.0', 'request_id': 'held',
                  'recorded_at': OLD, 'scope': {'run_id': 'retained'}})
    route.append({'schema_version': 'runtime-route-rejection/1.0', 'request_id': 'other',
                  'recorded_at': OLD, 'scope': {'run_id': 'other-run'}})
    gateway = JsonlStore(tmp_path / 'gateway_records.jsonl')
    gateway.append({'request_id': 'held', 'recorded_at': OLD})
    receipts = JsonlStore(tmp_path / 'engine_receipts.jsonl')
    receipts.append({'request_id': 'held', 'received_at': OLD})
    hold = tmp_path / 'hold.json'; write_hold(hold, ['retained'])
    manager = RecordRetention(tmp_path, enforce=True, hold_path=hold)
    summary = manager.run(now=NOW)
    assert summary['files'][NAME]['held_units'] == 1
    assert summary['files'][NAME]['removed_lines'] == 1
    assert [row['request_id'] for row in route.read_all()[0]] == ['held']
    assert len(gateway.read_all()[0]) == len(receipts.read_all()[0]) == 1
    write_hold(hold, [])
    summary = manager.run(now=NOW)
    assert summary['files'][NAME]['removed_lines'] == 1
    assert route.read_all()[0] == gateway.read_all()[0] == receipts.read_all()[0] == []
    assert summary['last_deletion']


@pytest.mark.parametrize('top_run,scope', [
    ('different', {'run_id': 'retained'}), ('retained', {'run_id': 'different'}),
    (None, {'run_id': 'retained'}),
])
def test_either_declared_run_hold_wins_for_v1_route_records(tmp_path, top_run, scope):
    route = JsonlStore(tmp_path / NAME)
    route.append({'schema_version': 'runtime-route-rejection/1.0', 'request_id': 'held',
                  'recorded_at': OLD, 'run_id': top_run, 'scope': scope})
    hold = tmp_path / 'hold.json'; write_hold(hold, ['retained'])
    summary = RecordRetention(tmp_path, enforce=True, hold_path=hold).run(now=NOW)
    assert summary['files'][NAME]['held_units'] == 1 and len(route.read_all()[0]) == 1


@pytest.mark.parametrize('name,schema,scope', [
    ('gateway_records.jsonl', 'runtime-route-rejection/1.0', {'run_id': 'retained'}),
    (NAME, 'unregistered/1.0', {'run_id': 'retained'}),
    (NAME, 'runtime-route-rejection/1.0', ['retained']),
    (NAME, 'runtime-route-rejection/1.0', {'run_id': ['retained']}),
])
def test_legacy_scope_alias_does_not_change_other_file_or_schema_contract(tmp_path, name, schema, scope):
    store = JsonlStore(tmp_path / name)
    store.append({'schema_version': schema, 'recorded_at': OLD, 'request_id': 'not-held', 'scope': scope})
    hold = tmp_path / 'hold.json'; write_hold(hold, ['retained'])
    summary = RecordRetention(tmp_path, enforce=True, hold_path=hold).run(now=NOW)
    assert summary['files'][name]['held_units'] == 0 and store.read_all()[0] == []
