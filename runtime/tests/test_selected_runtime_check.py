"""Bounded acceptance orchestration, including actual isolated OS/HTTP paths."""
from pathlib import Path
import json

import pytest

from tools import selected_runtime_check as tool
from tools.selected_runtime_check import HTTPResponse


def test_window_contract_distinguishes_unknown_and_truncated():
    assert tool.validate_windows({}) is None
    good = {"source_windows": {"gateway": {"returned": 2, "total": None, "limit": 200, "truncated": False},
        "hubble": {"returned": 50, "total": 80, "limit": 50, "truncated": True},
        "tetragon": {"returned": 0, "total": None, "limit": 50, "truncated": None}}}
    assert tool.validate_windows(good) is True
    good["source_windows"]["hubble"]["truncated"] = False
    assert tool.validate_windows(good) is False


def test_existing_data_directory_is_never_used_for_retention(tmp_path):
    marker = tmp_path / "live-records.jsonl"
    marker.write_text("keep\n")
    with pytest.raises(FileExistsError):
        tool.isolated(tmp_path, "test-model", fixture_count=2)
    assert marker.read_text() == "keep\n"


def test_metadata_sample_failure_records_key_paths_without_body(monkeypatch):
    counter = [0]
    private = "SYNTHETIC_PRIVATE_VALUE_MUST_NOT_BE_SAVED"
    def request(*args, **kwargs):
        if args[1].endswith('/api/status'):
            return HTTPResponse(200, {}, b'{"concurrency":{"in_flight":0,"limit":2}}')
        counter[0] += 1
        return HTTPResponse(400 if counter[0] == 1 else 200, {"X-Request-ID": "a" * 32},
            json.dumps({"error": {"code": "INVALID_UNICODE"}} if counter[0] == 1 else {"private_debug": private}).encode())
    monkeypatch.setattr(tool, "request", request)
    monkeypatch.setattr(tool, "collect", lambda *args: [
        {"outcome": 200, "capture_complete": True, "terminal_seen": True,
         "response_headers": {"x-request-id": key * 32}, "body": json.dumps({"private_debug": private})}
        for key in ("a", "b")])
    cases = tool.live("http://127.0.0.1:1", None, "test-model", "never-save-token", None, forbidden=["private_debug"])
    metadata = next(c for c in cases if c["id"] == "A1-METADATA-SAMPLE")
    assert metadata["result"] == "failed"
    assert metadata["facts"]["enforcement_verified"] is False
    assert private not in json.dumps(cases)
    assert "never-save-token" not in json.dumps(cases)


def test_unavailable_idle_scope_does_not_send_pair_or_claim_success(monkeypatch):
    monkeypatch.setattr(tool, "request", lambda *a, **kw: HTTPResponse(403, {}, b'{}'))
    monkeypatch.setattr(tool, "collect", lambda *a: pytest.fail("pair must not dispatch without its precondition"))
    cases = tool.live("http://127.0.0.1:1", None, "test-model", "synthetic-only", None)
    readiness = next(c for c in cases if c['id'] == 'A1-CONCURRENT-PRECONDITION')
    pair = next(c for c in cases if c['id'] == 'A1-CONCURRENT-UUID')
    assert readiness['result'] == pair['result'] == 'unknown'
    assert readiness['facts']['reason'] == 'status_not_available'


def test_busy_idle_scope_expires_without_post_retry(monkeypatch):
    monkeypatch.setattr(tool, "request", lambda *a, **kw: HTTPResponse(200, {}, b'{"concurrency":{"in_flight":1,"limit":2}}'))
    ready, facts = tool.concurrent_precondition("http://127.0.0.1:1", "synthetic-only", .02)
    assert ready is None and facts['reason'] == 'idle_deadline'
    assert facts['last_in_flight'] == 1


def test_idle_scope_read_failure_and_small_capacity_stay_unknown(monkeypatch):
    monkeypatch.setattr(tool, "request", lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic-private-error')))
    ready, facts = tool.concurrent_precondition("http://127.0.0.1:1", None, .1)
    assert ready is None and facts['reason'] == 'status_read_failed'
    assert 'synthetic-private-error' not in json.dumps(facts)
    monkeypatch.setattr(tool, "request", lambda *a, **kw: HTTPResponse(200, {}, b'{"concurrency":{"in_flight":0,"limit":1}}'))
    ready, facts = tool.concurrent_precondition("http://127.0.0.1:1", None, .1)
    assert ready is None and facts['reason'] == 'two_request_capacity_not_configured'


def test_rejection_after_idle_is_preserved_without_pair_retry(monkeypatch):
    monkeypatch.setattr(tool, 'request', lambda *a, **kw: HTTPResponse(200, {}, b'{"concurrency":{"in_flight":0,"limit":2}}'))
    calls = []
    def collect(*args):
        calls.append(args)
        return [{'outcome':status,'capture_complete':True,'terminal_seen':True,
                 'response_headers':{'x-request-id':str(index+1)*32},'body':'{}'}
                for index, status in enumerate((429, 200))]
    monkeypatch.setattr(tool, 'collect', collect)
    cases = tool.live('http://127.0.0.1:1', None, 'test-model', 'synthetic-only', None)
    pair = next(c for c in cases if c['id'] == 'A1-CONCURRENT-UUID')
    assert pair['result'] == 'failed' and pair['facts']['pair_dispatched'] is True
    assert pair['facts']['rows'][0]['outcome'] == 429 and len(calls) == 1


def test_aborted_execution_emits_unknown_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("SELECTED_TEST_TOKEN", "synthetic-only-token")
    monkeypatch.setattr(tool, "live", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("private-error-token")))
    output = tmp_path / "result.json"
    assert tool.main(["live", "--gateway-url", "http://127.0.0.1:1", "--model", "test-model", "--inference-token-env", "SELECTED_TEST_TOKEN", "--output", str(output)]) == 2
    result = json.loads(output.read_text())
    assert result["execution"] == "aborted"
    assert result["cases"][0]["result"] == "unknown"
    assert "private-error-token" not in output.read_text()


def test_isolated_200_history_real_http_retention_auth_and_load(tmp_path):
    result = tool.isolated(tmp_path / "owned-stack", "test-model", fixture_count=200)
    assert all(c["result"] == "passed" for c in result["cases"]), result
    load = next(c for c in result["cases"] if c["id"] == "A4-LIST-LOAD")
    assert load["facts"]["detail_gets"] == 0
    assert load["facts"]["list_gets"] == 5
    assert load["facts"]["http_statuses"] == [200] * 5
    assert load["facts"]["gateway_events_per_poll"] == [200] * 5
    assert load["facts"]["exact_projection_per_poll"] == [True] * 5
    assert result["gpu_execution_verified"] is False
    assert (tmp_path / "owned-stack" / "OWNED-SELECTED-RUNTIME-TEST").is_file()
    managed = next(c for c in result['cases'] if c['id']=='A4-RETENTION-MANAGED4')
    assert set(managed['facts']['managed_files'])=={'gateway_records.jsonl','gateway_route_rejections.jsonl','engine_receipts.jsonl','observations.jsonl'}
    assert managed['facts']['legacy_scope_run_hold_preserved'] is True
    assert managed['facts']['cross_file_receipt_hold_preserved'] is True
    projection = next(c for c in result['cases'] if c['id']=='A4-RETENTION-PROJECTION')
    assert projection['facts']['runtime_removed_lines']==projection['facts']['platform_removed_lines']
    assert projection['facts']['runtime_last_deletion_at']==projection['facts']['platform_last_deletion_at']


@pytest.mark.parametrize('options',[{'retention_age_seconds':float('nan')},{'retention_age_seconds':0},{'retention_max_bytes':0}])
def test_invalid_retention_limits_never_create_test_tree(tmp_path,options):
    directory=tmp_path/'new-owned-tree'
    with pytest.raises(ValueError):tool.isolated(directory,'test-model',**options)
    assert not directory.exists()


@pytest.mark.parametrize('corruption',[None,'empty-observations','wrong-removed-count','unheld-route-left'])
def test_retention_report_never_substitutes_for_actual_held_rows(corruption):
    import copy
    held_run='held-run';held_requests={'held-request','held-route'}
    expected={
        'gateway_records.jsonl':[{'request_id':'held-request','phase':p} for p in ('started','final')],
        'gateway_route_rejections.jsonl':[{'request_id':'held-route','scope':{'run_id':held_run}}],
        'engine_receipts.jsonl':[{'request_id':'held-route'}],
        'observations.jsonl':[{'run_id':held_run,'source':s} for s in ('hubble','tetragon')]}
    before={name:(rows+[{'request_id':'remove-me','run_id':'other'}],[]) for name,rows in expected.items()}
    after={name:(copy.deepcopy(rows),[]) for name,rows in expected.items()}
    deleted={'files':{name:{'removed_lines':1,'held_units':1} for name in expected}}
    if corruption=='empty-observations':after['observations.jsonl']=([],[])
    elif corruption=='wrong-removed-count':deleted['files']['gateway_records.jsonl']['removed_lines']=99
    elif corruption=='unheld-route-left':after['gateway_route_rejections.jsonl'][0].append({'request_id':'remove-me','run_id':'other'})
    passed,facts=tool.retained_fixture_evidence(before,after,deleted,held_requests,held_run)
    assert passed is (corruption is None)
    if corruption=='empty-observations':assert facts['actual_remaining_lines']['observations.jsonl']==0


def test_live_reuses_real_gateway_and_existing_stream_collector(tmp_path):
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.mock_engine import MockEngine
    from serving_runtime.mvp.policy import load_policy
    path = Path(__file__).parents[1] / "examples/mvp/policy.json"
    engine = MockEngine(port=0).start()
    gateway = None
    try:
        gateway = GatewayApp(MVPConfig(gateway_port=0, engine_port=0, policy_path=str(path), data_dir=str(tmp_path)),
                             load_policy(path), engine_address=engine.address).start()
        url = "http://127.0.0.1:" + str(gateway.address[1])
        with tool.platform_server(url, "unused-synthetic-token") as (platform, _):
            cases = tool.live(url, platform, "test-model", "unused-synthetic-token", None,
                              forbidden=["private_debug", "prompt_token_ids"])
        assert all(c["result"] == "passed" for c in cases), {c["id"]: c["result"] for c in cases}
        uuid = next(c for c in cases if c["id"] == "A1-CONCURRENT-UUID")
        assert uuid["facts"]["distinct_server_ids"] is True
    finally:
        if gateway:
            gateway.stop()
        engine.stop()


def test_live_waits_for_post_response_finalization_before_pair(tmp_path, monkeypatch):
    import threading
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.mock_engine import MockEngine
    from serving_runtime.mvp.policy import load_policy
    path = Path(__file__).parents[1] / "examples/mvp/policy.json"
    engine = MockEngine(port=0).start()
    gateway = None
    final_pending, release_final = threading.Event(), threading.Event()
    observed_busy = []
    try:
        gateway = GatewayApp(MVPConfig(gateway_port=0, engine_port=0, policy_path=str(path), data_dir=str(tmp_path)),
                             load_policy(path), engine_address=engine.address).start()
        append = gateway._records.append
        def delayed_final(record):
            if record.get('phase') == 'final' and record.get('delivery', {}).get('attempted') and not final_pending.is_set():
                final_pending.set()
                assert release_final.wait(3), 'test final journal was not released'
            return append(record)
        monkeypatch.setattr(gateway._records, 'append', delayed_final)
        original = tool.request
        def read_status(*args, **kwargs):
            reply = original(*args, **kwargs)
            if args[0] == 'GET' and args[1].endswith('/api/status'):
                active = tool.document(reply)['concurrency']['in_flight']
                if active:
                    observed_busy.append(active)
                    release_final.set()
            return reply
        monkeypatch.setattr(tool, 'request', read_status)
        url = 'http://127.0.0.1:' + str(gateway.address[1])
        cases = tool.live(url, None, 'test-model', 'synthetic-only', None)
        readiness = next(c for c in cases if c['id'] == 'A1-CONCURRENT-PRECONDITION')
        pair = next(c for c in cases if c['id'] == 'A1-CONCURRENT-UUID')
        assert final_pending.is_set() and observed_busy and all(active == 1 for active in observed_busy)
        assert readiness['result'] == pair['result'] == 'passed'
        assert readiness['facts']['polls'] >= 2 and readiness['facts']['last_in_flight'] == 0
    finally:
        release_final.set()
        if gateway:
            gateway.stop()
        engine.stop()


def test_completed_late_read_is_rejected_and_both_handles_close(monkeypatch):
    import time
    class SlowResponse:
        status = 200
        length = 0
        fp = None
        closed = False
        def read1(self, limit):
            time.sleep(.06)
            return b"{}"
        def getheaders(self):
            return []
        def close(self):
            self.closed = True
    response = SlowResponse()
    class Connection:
        sock = None
        closed = False
        def connect(self):
            pass
        def request(self, *args, **kwargs):
            pass
        def getresponse(self):
            return response
        def close(self):
            self.closed = True
    connection = Connection()
    monkeypatch.setattr(tool, "HTTPConnection", lambda *args, **kwargs: connection)
    with pytest.raises(TimeoutError, match="SELECTED_HTTP_DEADLINE"):
        tool.request("GET", "http://127.0.0.1/", timeout=.02)
    assert response.closed and connection.closed


def test_slow_drip_body_obeys_absolute_deadline_and_disconnects():
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer
    disconnected = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "200")
            self.end_headers()
            try:
                for _ in range(200):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(.015)
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="SELECTED_HTTP_DEADLINE"):
            tool.request("GET", "http://127.0.0.1:" + str(server.server_port), timeout=.12, token="synthetic-only")
        assert time.monotonic() - started < .5
        assert disconnected.wait(.5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


def test_response_cap_closes_handles_without_returning_partial_success(monkeypatch):
    class Response:
        fp = None
        length = 100
        closed = False
        def read1(self, limit):
            return b"x" * limit
        def close(self):
            self.closed = True
    response = Response()
    class Connection:
        sock = None
        closed = False
        def connect(self): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return response
        def close(self): self.closed = True
    connection = Connection()
    monkeypatch.setattr(tool, "HTTPConnection", lambda *args, **kwargs: connection)
    monkeypatch.setattr(tool, "MAX_RESPONSE", 32)
    with pytest.raises(ValueError, match="SELECTED_RESPONSE_LIMIT"):
        tool.request("GET", "http://127.0.0.1/", timeout=1)
    assert response.closed and connection.closed


@pytest.mark.parametrize('corruption', ['missing-event', 'changed-projection'])
def test_inline_load_requires_complete_exact_projection_on_every_poll(tmp_path, monkeypatch, corruption):
    original = tool.document
    changed = False

    def document(response):
        nonlocal changed
        value = original(response)
        if not changed and isinstance(value, dict) and 'source_windows' in value and 'items' in value:
            event = next((event for event in value['items']
                          if event.get('source') == 'gateway' and event.get('category') == 'request'), None)
            if event is not None:
                changed = True
                if corruption == 'missing-event':
                    value['items'].remove(event)
                else:
                    event['outcome'] = 'corrupted-fixture-projection'
        return value

    monkeypatch.setattr(tool, 'document', document)
    result = tool.isolated(tmp_path / 'owned-stack', 'test-model', fixture_count=2)
    load = next(case for case in result['cases'] if case['id'] == 'A4-LIST-LOAD')
    assert changed is True
    assert load['facts']['detail_gets'] == 0
    assert load['facts']['list_gets'] == 5
    assert load['facts']['http_statuses'] == [200] * 5
    assert load['facts']['exact_projection_per_poll'] == [False, True, True, True, True]
    assert load['result'] == 'failed'
