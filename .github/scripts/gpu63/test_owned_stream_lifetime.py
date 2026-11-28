import json
import weakref

import pytest

from owned_lifetime_assessment import CASE_IDS, full_assessment, ownership_timeline, socket_followup
from owned_stream_observer import COUNT_FIELDS, RESOURCE_NAMES, Resources
from owned_stream_poll import client_origin, endpoint_key, read_probe_receipt, receipt_origin, validate_registration
from owned_stream_probe_observer import request_identity, reviewed_lines, sha, stock_source
from owned_stream_result import original_call_points
from owned_tcp_diag import SocketIdentity


class Resource:
    pass


def test_resource_tracking_does_not_keep_objects_alive():
    saved = Resources()
    body_writer = Resource()
    watcher = weakref.ref(body_writer)
    saved.remember("asgi_stream_writer", body_writer)
    assert saved.inventory()["asgi_stream_writer"]["count"] == 1
    del body_writer
    assert watcher() is None
    assert saved.inventory()["asgi_stream_writer"]["reference_state"] == "released"
    assert saved.inventory()["response_transport"]["count"] is None


def test_unobservable_weak_reference_is_not_a_zero():
    sap = Resources()
    sap.remember("asgi_body_iterator", object())
    row = sap.inventory()["asgi_body_iterator"]
    assert row["reference_state"] == "weakref_unavailable" and row["count"] is None


def tick(state, start, end, status="present"):
    return {"state": state, "status": status, "start_ns": start, "end_ns": end}


def test_client_clock_interval_is_bounded_by_kernel_samples():
    rows = [tick("ESTABLISHED", 15, 17), tick("CLOSE_WAIT", 18, 21), tick("LAST_ACK", 25, 27)]
    origin = client_origin(rows, matching_clock=True)
    assert origin == {"kind": "client_endpoint_kernel_transition_bracket", "lower_ns": 18,
                      "upper_ns": 27, "first_transition": "LAST_ACK", "width_ns": 9}
    assert client_origin(rows, matching_clock=False) is None


def test_failed_lookup_does_not_anchor_client_close():
    records = [tick("ESTABLISHED", 10, 12), tick(None, 20, 21, "access_error"), tick(None, 22, 23, "absent")]
    assert client_origin(records, matching_clock=True) is None


def test_registration_accepts_only_named_pods_and_exact_peers():
    socket_info = SocketIdentity(3, "10.12.0.8", 8000, "10.12.0.9", 30256, 12, 55).json()
    cfg = {"session": "test", "namespace_uid": "n", "engine_pod_uid": "engine", "probe_pod_uid": "probe",
           "engine_ip": "10.12.0.8", "probe_ip": "10.12.0.9"}
    obj = {"session": "test", "pod_uid": "engine", "probe_pod_uid": "probe", "namespace_uid": "n",
           "connection_sha256": "2"*64, "case_id": "lif04-slow-close", "native_request_call_sha256": "3"*64, "socket": socket_info}
    assert validate_registration(obj, cfg).cookie == 12
    with pytest.raises(ValueError, match="outside"):
        validate_registration({**obj, "probe_pod_uid": "other"}, cfg)
    with pytest.raises(ValueError, match="outside"):
        validate_registration({**obj, "socket": {**socket_info, "peer_address": "10.12.0.10"}}, cfg)


DEADLINE = {"earliest_ns": 100, "latest_ns": 110}


def observations(*samples):
    return [{"mono_ns": stamp + 1, "sample_started_monotonic_ns": stamp, "phase": "resource", "value": value}
            for stamp, value in samples]


def evaluate(*samples):
    return ownership_timeline(observations(*samples), lambda row: row["value"], DEADLINE)


def test_first_zero_after_window_does_not_establish_timely_release():
    row = evaluate((12, 1), (20, 1), (125, 0))
    assert row["status"] == "release_interval_crosses_deadline"
    assert row["release_interval_ns"] == {"lower": 20, "upper": 126}
    assert evaluate((12, 1), (115, 1), (125, 0))["status"] == "released_after_deadline"


def test_missing_boundary_stays_unknown_and_positive_at_boundary_stays_retained():
    assert evaluate((12, 1), (75, 0), (115, None))["status"] == "deadline_state_unobserved"
    assert evaluate((12, 1), (75, 0), (115, 0))["status"] == "released_within_deadline"
    assert evaluate((12, 1), (75, 0), (115, 1))["status"] == "retained_at_or_after_deadline"
    assert evaluate((12, 0), (115, 0))["status"] == "empty_in_samples_through_deadline"


def test_only_a_real_exact_absence_ends_post_deadline_retention():
    last = tick("TIME_WAIT", 120, 121, "time_wait")
    failed = tick(None, 140, 141, "access_error")
    absent = tick(None, 155, 156, "absent")
    row = socket_followup([last, failed], DEADLINE, {"reason": "horizon_reached_with_lookup_uncertainty"})
    assert row["status"] == "disappearance_unconfirmed" and row["first_exact_absence_after_last_owned"] is None
    row = socket_followup([last, failed, absent], DEADLINE, {})
    assert row["first_exact_absence_is_after_five_second_window"] and row["intervening_lookup_failures"] == ["access_error"]
    assert row["disappearance_interval_ns"] == {"lower": 120, "upper": 156}


def test_time_wait_then_listener_reports_lookup_transition_without_implying_reclamation():
    sample = tick("TIME_WAIT", 121, 122, "time_wait")
    other = tick(None, 124, 125, "identity_changed") | {"cookie_errno": 2}
    bogus = tick(None, 126, 127, "listener_reply")
    fallback = tick(None, 130, 131, "listener_reply") | {"cookie_errno": 2}
    repeated = tick(None, 137, 138, "listener_reply") | {"cookie_errno": 2}
    result = socket_followup([sample, other, bogus, fallback, repeated], DEADLINE, {})
    assert result["status"] == "disappearance_unconfirmed" and result["first_exact_absence_after_last_owned"] is None
    observed = result["time_wait_then_listener_fallback"]
    assert observed["first_listener_fallback"] == fallback
    assert observed["last_listener_fallback"] == repeated
    assert observed["connected_lookup_transition_interval_ns"] == {"lower": 121, "upper": 131}
    assert len(observed["listener_fallback_samples"]) == 2
    assert observed["proves_exact_absence_or_memory_release"] is False


def complete_cases():
    cases = []
    for case in CASE_IDS:
        cases.append({"status": "independent_observation_collected", "http": {"case_id": case},
                      "endpoints": {role: {"window": {"status": "absent_observed_within_window", "positive_control": True}}
                                    for role in ("engine", "probe")},
                      "resource_assessment": {
                          "components": {key: {"status": "released_within_deadline"} for key in RESOURCE_NAMES}
                                        | {"response_debug_buffer": {"status": "conditional_inactive"}},
                          "counts_and_queues": {key: {"status": "empty_in_samples_through_deadline"} for key in COUNT_FIELDS}}})
    return cases


def test_whole_result_is_derived_from_owned_components_and_both_exact_endpoints():
    cases = complete_cases()
    assert full_assessment(cases)["whole_resource_reclamation_verified"]
    first = cases[0]
    first["endpoints"]["probe"]["window"]["status"] = "control_block_at_or_after_deadline"
    result = full_assessment(cases)
    assert not result["whole_resource_reclamation_verified"]
    assert result["by_case"][CASE_IDS[0]]["observed_retention_at_or_after_deadline"] == {"tcp_probe": "control_block_at_or_after_deadline"}


def test_whole_result_requires_every_original_case_without_duplicates_or_extras():
    cases = complete_cases()
    assert full_assessment(cases[:1])["missing_case_ids"] == list(CASE_IDS[1:])
    partial = full_assessment(cases + [cases[0], {"http": {"case_id": "unexpected"}}])
    assert not partial["whole_resource_reclamation_verified"]
    assert partial["duplicate_case_ids"] == [CASE_IDS[0]]
    assert partial["unexpected_case_ids"] == ["unexpected"]
    assert not partial["by_case"][CASE_IDS[0]]["whole_resource_reclamation_verified"]


def test_whole_result_reports_required_component_names_counts_and_positive_controls():
    cases = complete_cases()
    case = cases[0]
    case["resource_assessment"]["components"].pop("response_debug_buffer")
    case["resource_assessment"]["counts_and_queues"].pop("owned_pipeline_entries")
    case["endpoints"]["engine"]["window"]["positive_control"] = False
    case["endpoints"]["probe"]["window"].pop("positive_control")
    result = full_assessment(cases)
    assert not result["whole_resource_reclamation_verified"]
    assert result["by_case"][CASE_IDS[0]]["missing_same_generation_positive_controls"] == ["engine", "probe"]
    assert result["by_case"][CASE_IDS[0]]["missing_producer_fields"] == {
        "components": ["response_debug_buffer"], "counts_and_queues": ["owned_pipeline_entries"]}


def test_verified_stock_probe_sites_and_the_same_scoped_request():
    raw = ('sockets[index] = tracked\ntracked.shutdown(socket.SHUT_RDWR)\ntracked.close()\n'
           'response.close()\nconn.close()\noutput.update(client_closed=True, client_close_ms=milliseconds(),\nx=1)\n')
    wrapper = f"exec(compile({raw!r}, 'guard-stream-probe', 'exec'), {{'__name__':'__main__'}})"
    assert stock_source(wrapper, sha(raw.encode())) == raw
    assert reviewed_lines(raw) == {1: "opened", 2: "shutdown_start", 3: "tracked_close_start",
                                   4: "response_close_start", 5: "http_connection_close_start", 6: "close_block_end"}
    with pytest.raises(ValueError, match="digest"):
        stock_source(wrapper, "0" * 64)
    token = "byul-lif04-" + "f" * 32 + "-slow-close"
    req = {"id": "lif04-slow-close", "host": "10.12.0.8", "port": 8000, "path": "/v1/completions",
           "method": "POST", "headers": {"X-Request-Id": token}}
    assert request_identity(req, {"engine_ip": "10.12.0.8"}) == req["id"]
    assert request_identity({**req, "port": 8001}, {"engine_ip": "10.12.0.8"}) is None
    assert request_identity({**req, "id": "lif04-normal"}, {"engine_ip": "10.12.0.8"}) is None


def test_intentional_close_prefers_whole_stock_close_block_after_live_control():
    fd = {"kind": "client_fd_close_call", "lower_ns": 20, "upper_ns": 24}
    explicit = {"kind": "client_explicit_close_block", "lower_ns": 18, "upper_ns": 26}
    row = {"case_id": "lif04-slow-close", "initial_diagnostic": {"end_ns": 15, "status": "present"},
           "client_fd_close_interval": fd, "client_explicit_close_interval": explicit}
    assert receipt_origin(row) == explicit
    assert receipt_origin({**row, "case_id": "lif04-normal"}) == fd
    assert receipt_origin({**row, "initial_diagnostic": {"end_ns": 30, "status": "present"}}) is None
    assert receipt_origin({**row, "initial_diagnostic": {"end_ns": 15, "status": "access_error"}}) is None


def test_probe_join_uses_exact_mirror_and_run_binding_without_request_secret(tmp_path):
    server = SocketIdentity(3, "10.12.0.8", 8000, "10.12.0.9", 30256, 12, 55).json()
    client = SocketIdentity(4, "10.12.0.9", 30256, "10.12.0.8", 8000, 13, 66).json()
    key = endpoint_key("10.12.0.9", 30256, "10.12.0.8", 8000)
    cfg = {"session": "session", "namespace_uid": "ns", "probe_pod_uid": "probe", "reviewed_probe_source_sha256": "source",
           "probe_observer_sha256": "observer"}
    clock = {"time_namespace": 18}
    row = {"socket": client, "endpoint_key": key, "probe_request_header_sha256": "9" * 64, "session": "session", "clock": clock,
           "namespace_uid": "ns", "pod_uid": "probe", "probe_source_sha256": "source", "probe_observer_sha256": "observer",
           "case_id": "lif04-slow-close", "initial_diagnostic": {"status": "present", "request_sha256": "omit"}}
    path = tmp_path / f"client-{key}.json"
    path.write_text(json.dumps(row))
    registration = {"socket": server, "case_id": "lif04-slow-close"}
    projected = read_probe_receipt(registration, cfg, clock, 4, tmp_path)
    assert projected["case_id"] == row["case_id"] and projected["socket"] == client
    assert "probe_request_header_sha256" not in projected and "request_sha256" not in projected["initial_diagnostic"]
    assert read_probe_receipt({**registration, "case_id": "lif04-slow"}, cfg, clock, 4, tmp_path) is None
    assert read_probe_receipt(registration, cfg, clock, 5, tmp_path) is None
    path.write_text(json.dumps({**row, "socket": {**client, "local_port": 30257}}))
    assert read_probe_receipt(registration, cfg, clock, 4, tmp_path) is None
    path.write_text(json.dumps({"endpoint_key": key, "tuple_reuse_observed": True}))
    assert read_probe_receipt(registration, cfg, clock, 4, tmp_path) is None


def test_stock_hash_is_consumed_in_memory_only_for_one_native_call_and_connection():
    trial = {'case_id':'lif04-normal','request_id_sha256':'product-only-identity'}
    enter = {'phase':'REQUEST_ENTER','request_sha256':'product-only-identity','connection_sha256':'tag','call_sha256':'call'}
    different = {**enter,'request_sha256':'other','connection_sha256':'other-tag','call_sha256':'other-call'}
    assert original_call_points([trial], [different,enter]) == {'lif04-normal':('tag','call')}
    assert original_call_points([trial], [enter,enter]) == {}
    assert original_call_points([trial,{**trial,'case_id':'lif04-slow'}], [enter]) == {}
