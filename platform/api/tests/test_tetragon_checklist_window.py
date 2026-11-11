"""Checklist rules survive a busy general Tetragon feed within the bounded tail."""
from datetime import datetime, timedelta, timezone
import json
import os
import threading

import httpx

from byul_platform.config import Settings
from byul_platform.state import build_state
from byul_platform.routes.runtime import checks, events
from byul_platform.sources.checklist_data import CHECKS
from byul_platform.sources.runtime import LiveRuntimeSource, RuntimeClient
from serving_runtime.enforcement.events import ObservationQuery


def observation(n, rule="TG-EXEC-01"):
    return {"event_key": f"{n:064x}", "source": "tetragon", "rule_id": rule,
            "kind": "exec" if rule == "TG-EXEC-01" else "file_access",
            "observed_at": (datetime(2026, 11, 11, tzinfo=timezone.utc) + timedelta(seconds=n)).isoformat(),
            "data_origin": "real", "action_mode": "monitor", "phase": ["inferring"],
            "code_load_verdict": "detected", "exec_verdict": "allowed_in_phase"}


def test_events_and_checks_retain_rules_deduplicate_and_sort_busy_journal(tmp_path, monkeypatch):
    query = ObservationQuery(tmp_path / "observations.jsonl")
    query.store.check_writable()
    rows = [observation(0, "TG-CODE-01"), observation(1, "TG-SECRET-01")]
    rows += [observation(n) for n in range(2, 3002)]
    rows += [observation(3002, "TG-CODE-01")]  # returned by both general and rule queries
    with query.store._open(os.O_WRONLY | os.O_APPEND) as stream:
        stream.write("".join(json.dumps(row) + "\n" for row in rows).encode())
    monkeypatch.setattr(query.store, "read_all", lambda: (_ for _ in ()).throw(AssertionError("unbounded read")))
    calls = []
    def transport(request):
        calls.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/api/requests":
            return httpx.Response(200, json={"requests": [], "gateway_records": {}})
        assert request.url.path == "/api/observations"
        return httpx.Response(200, json=query.query(dict(request.url.params)))
    original = RuntimeClient.__init__
    def init(self, base_url, token, **kw):
        original(self, base_url, token, transport=httpx.MockTransport(transport))
    monkeypatch.setattr(RuntimeClient, "__init__", init)
    state = build_state(Settings(runtime_base_url="http://runtime.test", platform_data_dir=tmp_path / "platform"))
    try:
        body = events(state)
        assert body["invalid"] == []
        ids = [row["event_id"] for row in body["items"]]
        assert len(ids) == len(set(ids)) == 52
        assert ids[0] == f"feed:{3002:064x}"
        assert ids[-2:] == [f"feed:{1:064x}", f"feed:{0:064x}"]
        assert body["truncated"] is True
        assert body["source_windows"]["tetragon"] == {
            "total": 3003, "returned": 52, "limit": 100, "truncated": True}
        items = {r["framework_id"]: r for r in checks(state)["items"]}
        assert items["CHK-SUP-008"]["observed_events"] == 2
        assert items["CHK-PLT-004"]["observed_events"] == 1
    finally:
        state.close()
    rules = {r for check in CHECKS for r in check["rules"] if r.startswith("TG-")}
    grouped = [params for _, params in calls if "rule_ids" in params]
    assert set(grouped[0]["rule_ids"].split(",")) == rules
    # The second endpoint reuses the normal short observation TTL.
    assert len(grouped) == 1 and not any("rule_id" in p for _, p in calls)


def test_rule_fanout_is_parallel_capped_and_keeps_lower_bound():
    rules = {r for check in CHECKS for r in check["rules"] if r.startswith("TG-")}
    barrier = threading.Barrier(len(rules))
    class Client:
        def get(self, path, params):
            if "rule_id" in params:
                barrier.wait(timeout=5)
                offset = sorted(rules).index(params["rule_id"]) * 100
                return {"observations": [observation(offset + n, params["rule_id"]) for n in range(49, -1, -1)],
                        "counts": {"total": 50}, "window": {"truncated": True, "counts_lower_bound": True}}
            return {"observations": [], "counts": {"total": 250}, "window": {"truncated": True}}
    batch = LiveRuntimeSource(Client(), "http://runtime.test")._observation_events()
    assert len(batch.candidates) == 10 * len(rules)
    assert batch.truncated
    assert batch.source_windows["tetragon"]["total"] is None
    assert batch.source_windows["tetragon"]["total_lower_bound"] == 250


def test_rule_slice_does_not_mark_complete_general_feed_truncated():
    rows = [observation(n, 'TG-CODE-01') for n in range(20)]
    class Client:
        def get(self, path, params):
            if params['source'] != 'tetragon':
                return {'observations': [], 'counts': {'total': 0}}
            return {'observations': rows[:10] if 'rule_ids' in params else rows,
                    'counts': {'total': 20, 'rule_id': {'TG-CODE-01': 20}},
                    'window': {'rule_groups': {'max_rules': 5, 'per_rule_limit': 10}}}
    batch = LiveRuntimeSource(Client(), 'http://runtime.test')._observation_events()
    assert len(batch.candidates) == 20 and not batch.truncated
    assert batch.source_windows['tetragon'] == {
        'returned': 20, 'total': 20, 'limit': 100, 'truncated': False}
