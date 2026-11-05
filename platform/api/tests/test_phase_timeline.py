from pathlib import Path
import json

from byul_platform.models import PhaseTimelineResponse, SourceInfo
from byul_platform.sources.runtime import ExampleRuntimeSource


def test_example_phase_timeline_is_marked():
    source = ExampleRuntimeSource(Path("unused"))
    body = source.phase_timeline()
    parsed = PhaseTimelineResponse.model_validate({**body, "source": SourceInfo(name="runtime", mode="example")})
    assert [entry.phase for entry in parsed.entries] == ["loading", "compiling", "inferring"]
    assert parsed.data_origin == "synthetic"
    assert parsed.source.mode == "example"
    assert "pod_uid" not in parsed.model_dump_json()


def test_phase_timeline_route_uses_source_and_generated_payload():
    from types import SimpleNamespace
    from byul_platform.routes.runtime import phase_timeline

    runtime = ExampleRuntimeSource(Path("unused"))
    state = SimpleNamespace(runtime=runtime, source=lambda _kind: {"name": "runtime", "mode": "example", "detail": None})
    body = phase_timeline(state=state)
    assert body["entries"][0]["phase"] == "loading"
    assert body["source"]["mode"] == "example"


def test_live_source_reads_only_timeline_get():
    import httpx
    from byul_platform.sources.runtime import LiveRuntimeSource, RuntimeClient

    seen = []
    payload = ExampleRuntimeSource(Path("unused")).phase_timeline()
    def respond(request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json=payload)
    client = RuntimeClient("http://runtime.test", "viewer-token", transport=httpx.MockTransport(respond))
    try:
        assert LiveRuntimeSource(client, "http://runtime.test").phase_timeline()["data_origin"] == "synthetic"
        assert seen == [("GET", "/api/phase-timeline", "Bearer viewer-token")]
    finally:
        client.close()


def test_example_timeline_covers_synthetic_tetragon_records(tmp_path):
    observed = "2026-10-25T01:03:00+00:00"
    (tmp_path / "runtime-events.jsonl").write_text(
        json.dumps({"source": "tetragon", "data_origin": "EXAMPLE", "occurred_at": observed}) + "\n",
        encoding="utf-8",
    )
    timeline = ExampleRuntimeSource(tmp_path).phase_timeline()
    from datetime import datetime
    point = datetime.fromisoformat(observed)
    assert any(datetime.fromisoformat(entry["from"]) <= point < datetime.fromisoformat(entry["until"])
               for entry in timeline["entries"])
    assert timeline["data_origin"] == "synthetic"
