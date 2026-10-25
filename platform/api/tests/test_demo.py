"""데모 자료: 계약을 통과하고, 변환 규칙을 지키며, 예시 모드로 그대로 뜬다."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from byul_platform.config import Settings
from byul_platform.demo import build
from byul_platform.main import create_app


def test_demo_data_is_valid_and_served(tmp_path):
    out = tmp_path / "demo"
    counts = build(out, requests=200, now=datetime(2026, 10, 25, 12, tzinfo=UTC))
    assert counts["gateway"] >= 200 and counts["hubble"] > 0 and counts["tetragon"] > 0
    app = create_app(Settings(examples_override=out, web_dist=tmp_path / "x"))
    with TestClient(app) as c:
        events = c.get("/api/runtime/events").json()
        assert events["invalid"] == []
        items = events["items"]
        assert all(e["data_origin"] == "EXAMPLE" for e in items)
        # Hubble DROPPED도 거부 확인이 아니다
        assert {e["outcome"] for e in items if e["source"] == "hubble"} == {"observed"}
        assert {"denied", "detected", "enforce_attempted"} <= {e["outcome"] for e in items}
        summary = c.get("/api/runtime/summary").json()
        assert summary["total"] == counts["gateway"]
        assert len(c.get("/api/update/results").json()["items"]) == 2
        reval = c.get("/api/update/revalidation").json()
        assert reval["invalid"] == [] and len(reval["items"]) == 4
        assert c.get("/api/evidence").json()["invalid"] == []
        assert c.get("/api/meta").json()["example_mode"] is True
