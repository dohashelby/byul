import httpx
from fastapi.testclient import TestClient
from byul_platform.config import Settings
from byul_platform.main import create_app
from byul_platform.sources.runtime import LiveRuntimeSource, RuntimeClient

L4 = {"index": 0, "name": "NVIDIA L4", "utilization_percent": 37.0, "memory_used_mib": 18120.0,
      "memory_total_mib": 23034.0, "temperature_c": 54.0, "power_w": 71.5}


def test_example_mode_does_not_fabricate_gpu_numbers(example_client):
    item = example_client.get('/api/runtime/gpu-status').json()['item']
    assert item['state'] == 'not_configured'
    assert item['gpus'] == []
    assert item['fresh'] is False


def test_invalid_snapshot_is_error_not_numbers(store_client, monkeypatch):
    monkeypatch.setattr(store_client.app.state.byul.runtime, 'gpu_status',
                        lambda: {'state': 'ok', 'fresh': True, 'gpus': [{**L4, 'utilization_percent': 250}]})
    item = store_client.get('/api/runtime/gpu-status').json()['item']
    assert (item['state'], item['reason'], item['gpus'], item['fresh']) == ('error', 'INVALID_SNAPSHOT', [], False)


def _live(tmp_path, respond):
    runtime = RuntimeClient('http://runtime.test', None, transport=httpx.MockTransport(respond))
    app = create_app(Settings(web_dist=tmp_path / 'missing'))
    return runtime, app


def test_live_snapshot_and_stale_are_passed_through(tmp_path):
    calls = []
    snapshot = {'schema_version': 'runtime-gpu-status/1.0', 'state': 'stale', 'fresh': False,
                'collected_at': '2026-11-09T00:00:00Z', 'reason': None, 'gpus': [L4]}
    def respond(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json=snapshot)
    runtime, app = _live(tmp_path, respond)
    with TestClient(app) as client:
        client.app.state.byul.runtime = LiveRuntimeSource(runtime, 'http://runtime.test')
        item = client.get('/api/runtime/gpu-status').json()['item']
        assert item['state'] == 'stale' and item['fresh'] is False
        assert item['gpus'][0]['name'] == 'NVIDIA L4'
        assert calls == [('GET', '/api/gpu-status')]
        assert client.post('/api/runtime/gpu-status').status_code == 405
    runtime.close()


def test_older_gateway_without_route_is_not_configured(tmp_path):
    runtime, app = _live(tmp_path, lambda request: httpx.Response(404, json={'error': 'not found'}))
    with TestClient(app) as client:
        client.app.state.byul.runtime = LiveRuntimeSource(runtime, 'http://runtime.test')
        assert client.get('/api/runtime/gpu-status').json()['item']['state'] == 'not_configured'
    runtime.close()
