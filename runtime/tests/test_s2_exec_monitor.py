"""Execution monitor windows use the same review and continuity gates."""

import json
from tests import test_tetragon_monitor_window as prior
from tests.test_s2_exec import source
from serving_runtime.enforcement.intent import parse_intent


def test_exec_process_events_require_full_human_review():
    fixture = prior.MonitorWindowTests()
    fixture.setUp()
    try:
        fixture.intent = parse_intent(source())
        fixture.name = "runtime-tg-exec-01"
        fixture.snapshot["policies"][0].update(
            name=fixture.name, method="lsm_bprm_check", namespace=fixture.intent.namespace
        )
        fixture.snapshot["capabilities"][0]["available"] = ["lsm_bprm_check"]
        fixture.snapshot["verifications"][0]["method"] = "lsm_bprm_check"
        fixture.generator = fixture.new_generator()
        fixture.collect()
        event = fixture.event(
            kind="process_exec", rule_id="TG-EXEC-01", binary="/bin/sh", caller_binary="/opt/test/nvcc"
        )
        event.pop("policy_name")
        fixture.append(event)
        result = fixture.collect(10)
        window = result["windows"][0]
        assert window["event_count"] == 1 and window["reviewed_event_count"] == 0
        assert not fixture.ready(result)["checks"]["monitor_period"]
        catalog = json.loads(fixture.generator.catalog.read_text())
        assert catalog["windows"][0]["events"][0]["event"]["caller_binary"] == "/opt/test/nvcc"
        row = catalog["windows"][0]["events"][0]
        fixture.reviews.write_text(
            json.dumps(
                {
                    "reviewer_token_id": "reviewer-example",
                    "events": [{"event_sha256": row["event_sha256"], "verdict": "not_false_positive"}],
                }
            )
        )
        result = fixture.collect(10)
        assert fixture.ready(result)["checks"]["monitor_period"]
        assert fixture.ready(result)["checks"]["allowed_mode"]
        result["windows"][0]["false_positive_count"] = 1
        assert not fixture.ready(result)["checks"]["monitor_period"]
    finally:
        fixture.doCleanups()


def test_gateway_health_record_does_not_invalidate_security_window():
    fixture = prior.MonitorWindowTests()
    fixture.setUp()
    try:
        fixture.collect()
        fixture.append(
            {
                "schema_version": "runtime-engine-health-event/1.0",
                "source": "engine_health",
                "kind": "engine_healthy",
                "observed_at": fixture.event()["observed_at"],
            }
        )
        result = fixture.collect(10)
        assert result["state"] == "ok"
        assert result["windows"][0]["event_count"] == 0
        fixture.append(
            {
                "schema_version": "runtime-engine-health-event/1.0",
                "source": "tetragon",
                "kind": "engine_healthy",
                "observed_at": fixture.event()["observed_at"],
            }
        )
        assert fixture.collect(11)["windows"] == []
    finally:
        fixture.doCleanups()


def test_scope_matches_live_spec_and_keeps_unknown_shell_review_pending():
    from tools.tetragon_monitor_window import scope_execution_review, digest
    from serving_runtime.enforcement.tetragon import render_manifests

    fixture = prior.MonitorWindowTests()
    fixture.setUp()
    try:
        intent = parse_intent(source())
        fixture.name = "runtime-tg-exec-01"
        fixture.snapshot["policies"][0].update(
            name=fixture.name,
            method="lsm_bprm_check",
            spec_sha256=digest(render_manifests(intent)["tetragon-tg-exec-01.json"]["spec"]),
        )
        fixture.generator = fixture.new_generator()
        fixture.collect()
        fixture.append(
            fixture.event(
                binary="/usr/bin/gcc", kind="process_exec", rule_id="TG-EXEC-01", exec_verdict="phase_unknown"
            )
        )
        fixture.append(
            fixture.event(binary="/bin/sh", kind="process_exec", rule_id="TG-EXEC-01", exec_verdict="phase_unknown")
        )
        result = fixture.collect(10)
        catalog = json.loads(fixture.generator.catalog.read_text())
        scoped, selected = scope_execution_review(fixture.snapshot, result, catalog, intent, {}, None)
        assert scoped["windows"][0]["event_count"] == 1
        assert scoped["windows"][0]["reviewed_event_count"] == 0
        assert selected[0]["event"]["binary"] == "/bin/sh"
        assert len(fixture.events.read_text().splitlines()) == 2
        fixture.snapshot["policies"][0]["spec_sha256"] = "f" * 64
        import pytest

        with pytest.raises(ValueError):
            scope_execution_review(fixture.snapshot, result, catalog, intent, {}, None)
    finally:
        fixture.doCleanups()


def test_session_never_generates_human_verdicts_from_caller_names(tmp_path, monkeypatch):
    from labtools import s2_exec_session as session

    monkeypatch.setattr(session, "read_object", lambda path: {})
    monkeypatch.setattr(
        session,
        "scope_execution_review",
        lambda *args: ({}, [{"event_sha256": "a" * 64, "event": {"caller_binary": "/usr/bin/python3"}}]),
    )
    import pytest

    with pytest.raises(ValueError, match="human review file required"):
        session.scoped_review(
            {
                "review": "source",
                "catalog": "catalog",
                "review_authority": "user-approved-shell-scope-no-exemptions",
                "reviewed_callers": ["/usr/bin/python3"],
                "output": str(tmp_path),
            },
            None,
            {},
        )
    assert not list(tmp_path.iterdir())


def test_session_rejects_cold_enforce_before_any_host_action():
    from labtools.s2_exec_session import preflight
    import pytest

    with pytest.raises(ValueError, match="enforce cold startup is disabled"):
        preflight({"startup_mode": "enforce_cold_start"}, None)
