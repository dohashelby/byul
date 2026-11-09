"""Bounded A1/A4 additions to the existing AWS checks.

live only sends selected synthetic HTTP requests and reads existing APIs.
isolated owns a newly-created test directory and Gateway/Platform listeners;
retention and auth rotation act only on that test stack. No cluster mutation.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import threading
import time
from urllib.parse import urlsplit
from http.client import HTTPConnection
from uuid import uuid4

from typing import NamedTuple, Mapping


class HTTPResponse(NamedTuple):
    status: int
    headers: Mapping[str, str]
    body: bytes

from guard.executors.stream_probe import collect

MAX_RESPONSE = 8 * 1024 * 1024
UUID = re.compile(r"^[0-9a-f]{32}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def origin(value):
    p = urlsplit(value)
    if p.scheme != "http" or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ("", "/"):
        raise ValueError("plain HTTP origin without credentials required")
    return value.rstrip("/")


def request(method, url, *, token=None, body=None, timeout=15, extra_headers=None):
    """Bound connect, headers and body by one absolute runtime watchdog."""
    from serving_runtime.mvp.response_timeout import DeadlineWatchdog, set_response_timeout
    if not math.isfinite(timeout) or timeout <= 0 or timeout > 60:
        raise ValueError("selected HTTP timeout must be finite and within (0,60]")
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("plain HTTP URL without credentials required")
    headers = {"Content-Type": "application/json", "Connection": "close"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if extra_headers:
        headers.update(extra_headers)
    connection = HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    deadline = time.monotonic() + timeout
    watch = DeadlineWatchdog(connection, deadline)
    response = None
    watch.start()
    try:
        # Existing runtime helper also bounds a slow DNS/TCP setup in a daemon
        # worker and closes a connection which completes after the cutoff.
        watch.connect()
        if watch.expired():
            raise TimeoutError("SELECTED_HTTP_DEADLINE")
        if connection.sock is not None:
            connection.sock.settimeout(max(.001, deadline - time.monotonic()))
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        watch.watch_response(response)
        response_socket = connection.sock or getattr(getattr(getattr(response, "fp", None), "raw", None), "_sock", None)
        raw = bytearray()
        while True:
            if watch.expired():
                raise TimeoutError("SELECTED_HTTP_DEADLINE")
            response_socket = set_response_timeout(response_socket, deadline - time.monotonic())
            chunk = response.read1(min(4096, MAX_RESPONSE - len(raw) + 1))
            if watch.expired():
                raise TimeoutError("SELECTED_HTTP_DEADLINE")
            if not chunk:
                if response.length not in (None, 0):
                    raise ValueError("SELECTED_HTTP_INCOMPLETE_BODY")
                break
            raw.extend(chunk)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("SELECTED_RESPONSE_LIMIT")
        result = HTTPResponse(response.status, dict(response.getheaders()), bytes(raw))
        if watch.finish():
            raise TimeoutError("SELECTED_HTTP_DEADLINE")
        return result
    except Exception:
        if watch.expired():
            raise TimeoutError("SELECTED_HTTP_DEADLINE") from None
        raise
    finally:
        watch.cancel()
        if response is not None:
            response.close()
        connection.close()


def document(response):
    value = json.loads(response.body)
    if not isinstance(value, dict):
        raise ValueError("SELECTED_EXPECTED_OBJECT")
    return value


def header(response, name):
    return next((v for k, v in response.headers.items() if k.lower() == name.lower()), None)


def fact(ident, passed, facts, *, scope):
    return {"id": ident, "result": "passed" if passed is True else "failed" if passed is False else "unknown",
            "scope": scope, "facts": facts, "security_verdict": None}


def json_body(model, *, stream=False, prompt="Reply briefly with hello.", max_tokens=8):
    return {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "stream": stream}


def forbidden_paths(value, forbidden, path=""):
    result = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in forbidden:
                result.append(path + "/" + key)
            result.extend(forbidden_paths(item, forbidden, path + "/" + key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            result.extend(forbidden_paths(item, forbidden, path + "/" + str(index)))
    return result


def validate_windows(body):
    windows = body.get("source_windows")
    if not isinstance(windows, dict) or not {"gateway", "hubble", "tetragon"} <= windows.keys():
        return None
    for source, limit in (("gateway", 200), ("hubble", 50), ("tetragon", 50)):
        row = windows[source]
        if not isinstance(row, dict) or type(row.get("returned")) is not int or not 0 <= row["returned"] <= limit or row.get("limit") != limit:
            return False
        total = row.get("total")
        if row.get('truncated') is not None and type(row.get('truncated')) is not bool:
            return False
        if total is not None and (type(total) is not int or total < row["returned"] or row.get("truncated") is not (total > row["returned"])):
            return False
        if total is None and source != "gateway" and row.get("truncated") is not None:
            return False
    return True


def concurrent_precondition(gateway_url, token, timeout, *, required_slots=2):
    """Wait for prior request finalization without retrying an inference POST."""
    deadline = time.monotonic() + min(timeout, 5)
    facts = {"status": None, "polls": 0, "last_in_flight": None, "limit": None}
    while time.monotonic() < deadline:
        try:
            reply = request("GET", gateway_url + "/api/status", token=token,
                            timeout=max(.001, deadline - time.monotonic()))
            facts.update(status=reply.status, polls=facts["polls"] + 1)
            if reply.status != 200:
                return None, {**facts, "reason": "status_not_available"}
            concurrency = document(reply).get("concurrency", {})
            active, limit = concurrency.get("in_flight"), concurrency.get("limit")
            if type(active) is not int or type(limit) is not int or active < 0 or limit < 1:
                return None, {**facts, "reason": "concurrency_not_available"}
            facts.update(last_in_flight=active, limit=limit)
            if limit < required_slots:
                return None, {**facts, "reason": "two_request_capacity_not_configured"}
            if active == 0:
                return True, {**facts, "reason": "idle_before_pair"}
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        except Exception as error:
            return None, {**facts, "reason": "status_read_failed", "error_class": type(error).__name__}
    return None, {**facts, "reason": "idle_deadline"}


def live(gateway_url, platform_url, model, inference_token, viewer_token, *, timeout=15, max_tokens=8, second_token=None, forbidden=()):
    gateway_url = origin(gateway_url)
    if platform_url:
        platform_url = origin(platform_url)
    cases = []
    invalid = json_body(model, max_tokens=max_tokens)
    invalid["messages"][0]["content"] = "\ud800"
    response = request("POST", gateway_url + "/v1/chat/completions", token=inference_token,
                       body=json.dumps(invalid, ensure_ascii=True).encode(), timeout=timeout)
    code = document(response).get("error", {}).get("code")
    rid = header(response, "X-Request-ID")
    cases.append(fact("A1-UTF8", response.status == 400 and code == "INVALID_UNICODE",
                      {"status": response.status, "error_code": code, "request_id": rid}, scope="Gateway Unicode rejection"))
    normal = request("POST", gateway_url + "/v1/chat/completions", token=inference_token,
                     body=json.dumps(json_body(model, max_tokens=max_tokens)).encode(), timeout=timeout)
    cases.append(fact("A1-FOLLOWUP", normal.status == 200, {"status": normal.status, "request_id": header(normal, "X-Request-ID"),
                     "body_bytes": len(normal.body), "body_sha256": hashlib.sha256(normal.body).hexdigest()}, scope="normal request after Unicode rejection"))
    if rid and viewer_token:
        detail = request("GET", gateway_url + "/api/requests/" + rid, token=viewer_token, timeout=timeout)
        raw = document(detail) if detail.status == 200 else {}
        finals = [r for r in raw.get("gateway_records", []) if r.get("phase") == "final"]
        cases.append(fact("A1-UTF8-RECORD", bool(finals) and finals[-1].get("delivery", {}).get("attempted") is False if detail.status == 200 else None,
                          {"detail_status": detail.status, "final_count": len(finals)}, scope="final journal / no engine delivery"))
    # Response completion precedes final journal storage/slot release. Establish
    # a quiet starting point; keep any rejection after dispatch as a failure.
    ready, readiness = concurrent_precondition(gateway_url, viewer_token, timeout)
    cases.append(fact("A1-CONCURRENT-PRECONDITION", ready, readiness,
                      scope="bounded GET-only idle/capacity check before the two-request sample"))
    # Existing UPDATE collector owns the two connection deadlines and framing.
    requests = []
    for index, stream in enumerate((False, True)):
        requests.append({"id": "selected-" + str(index), "method": "POST", "mode": "sse" if stream else "json",
                         "url": gateway_url + "/v1/chat/completions", "headers": {"Authorization": "Bearer " + (second_token if index and second_token else inference_token),
                         "X-Request-ID": "same-client-id"}, "body": json_body(model, stream=stream, max_tokens=max_tokens)})
    rows = collect(requests, timeout) if ready is True else []
    ids = [(r.get("response_headers") or {}).get("x-request-id") for r in rows]
    captures = all(r.get("outcome") == 200 and r.get("capture_complete") is True for r in rows) and len(rows) == 2 and rows[1].get("terminal_seen") is True
    good_ids = len(ids) == 2 and all(isinstance(v, str) and UUID.fullmatch(v) for v in ids) and ids[0] != ids[1]
    cases.append(fact("A1-CONCURRENT-UUID", captures and good_ids if ready is True else None,
                      {"request_ids": ids, "distinct_server_ids": bool(good_ids), "principals": 2 if second_token else 1,
                       "pair_dispatched": ready is True, "precondition_reason": readiness["reason"],
                       "rows": [{k: r.get(k) for k in ("id", "connection_id", "outcome", "capture_complete", "body_bytes", "body_sha256", "terminal_seen", "stop_reason")} for r in rows]},
                      scope="two client connections and server UUIDs; engine parallel execution and response-recipient content are separate"))
    hits = []
    parsed = normal.status == 200
    for raw in [normal.body.decode("utf-8"), *[r.get("body", "") for r in rows]]:
        values = []
        try:
            values = [json.loads(raw)]
        except (ValueError, TypeError):
            for line in raw.splitlines():
                if line.startswith("data:") and line[5:].strip() != "[DONE]":
                    try:
                        values.append(json.loads(line[5:].strip()))
                    except ValueError:
                        parsed = False
        if not values:
            parsed = False
        for value in values:
            hits.extend(forbidden_paths(value, set(forbidden)))
    cases.append(fact("A1-METADATA-SAMPLE", not hits if parsed and captures and forbidden else None,
                      {"forbidden_keys": list(forbidden), "observed_key_paths": sorted(set(hits)), "enforcement_verified": False},
                      scope="selected keys in sampled normal/stream responses; this is no injected-upstream enforcement proof"))
    if viewer_token:
        viewer_status = request("GET", gateway_url + "/api/status", token=viewer_token, timeout=timeout)
        inference_status = request("GET", gateway_url + "/api/status", token=inference_token, timeout=timeout)
        anonymous_status = request("GET", gateway_url + "/api/status", timeout=timeout)
        protected = "GET /api/*" in document(viewer_status).get("auth", {}).get("protected_routes", []) if viewer_status.status == 200 else False
        cases.append(fact("A4-READ-ROLES", [viewer_status.status, inference_status.status, anonymous_status.status] == [200, 403, 401] if protected else None,
                          {"viewer": viewer_status.status, "inference": inference_status.status, "anonymous": anonymous_status.status,
                           "protected": protected}, scope="configured Gateway GET /api/* role boundary"))
    if platform_url:
        events = request("GET", platform_url + "/api/runtime/events", timeout=timeout)
        body = document(events) if events.status == 200 else {}
        cases.append(fact("A4-SOURCE-WINDOW", validate_windows(body), {"status": events.status, "source_windows": body.get("source_windows"),
                          "invalid_rows": len(body.get("invalid", []))}, scope="current source query windows; collection gaps are separate"))
    else:
        cases.append(fact("A4-SOURCE-WINDOW", None, {"reason": "platform_url_not_supplied"}, scope="not run"))
    return cases


class CountingClient:
    def __init__(self, inner):
        self.inner, self.calls = inner, []
    def get(self, path, params=None):
        self.calls.append(path)
        return self.inner.get(path, params)
    def get_bytes(self, path):
        self.calls.append(path)
        return self.inner.get_bytes(path)
    def close(self):
        self.inner.close()


@contextmanager
def platform_server(runtime_url, viewer):
    import uvicorn
    from byul_platform.config import Settings
    from byul_platform.main import create_app
    app = create_app(Settings(runtime_base_url=runtime_url, runtime_viewer_token=viewer))
    source = app.state.byul.runtime
    counter = CountingClient(source.client)
    source.client = counter
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = "http://127.0.0.1:" + str(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, timeout_graceful_shutdown=2))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        if not server.started:
            raise RuntimeError("isolated platform did not start")
        yield url, counter
    finally:
        server.should_exit = True
        thread.join(5)
        listener.close()
        if thread.is_alive():
            raise RuntimeError("isolated platform did not stop")


def retained_fixture_evidence(before, after, deleted, held_requests, held_run):
    """Independently compare real rows with the known fixture's exact hold set."""
    from collections import Counter
    expected = {}
    for name, (rows, corrupt) in before.items():
        expected[name] = [row for row in rows if row.get('request_id') in held_requests or row.get('run_id') == held_run or
                          name == 'gateway_route_rejections.jsonl' and (row.get('scope') or {}).get('run_id') == held_run]
    encode = lambda rows: Counter(json.dumps(row,sort_keys=True,separators=(',',':')) for row in rows)
    matched = {name: bool(expected[name]) and encode(expected[name]) == encode(rows) for name,(rows,corrupt) in after.items()}
    differences = {name: len(before[name][0])-len(rows) for name,(rows,corrupt) in after.items()}
    deltas = {name: value == deleted['files'][name]['removed_lines'] for name,value in differences.items()}
    observation_holds = Counter(row.get('source') for row in expected.get('observations.jsonl',[]))
    gateway_phases = Counter(row.get('phase') for row in expected.get('gateway_records.jsonl',[]))
    valid = (set(before)==set(after)==set(deleted['files']) and all(matched.values()) and all(deltas.values()) and
             all(not corrupt for rows,corrupt in [*before.values(),*after.values()]) and
             observation_holds == {'hubble':1,'tetragon':1} and gateway_phases == {'started':1,'final':1})
    return valid, {'expected_held_lines':{name:len(rows) for name,rows in expected.items()},
                   'actual_remaining_lines':{name:len(rows) for name,(rows,corrupt) in after.items()},
                   'line_differences':differences,'reported_removed_lines_match':deltas,
                   'exact_held_row_multiset_matches':matched,'held_observation_source_counts':dict(observation_holds)}


def isolated(data_dir, model, *, backend="mock", backend_config=None, timeout=15, fixture_count=200,
             retention_age_seconds=1, retention_max_bytes=4096):
    """Own the entire directory; caller must supply a new, explicit path."""
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.auth import token_digest
    from serving_runtime.mvp.backend import load_backend_config
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.mock_engine import MockEngine
    from serving_runtime.mvp.policy import load_policy
    from serving_runtime.mvp.retention import HOLD_SCHEMA, MANAGED_FILES, RecordRetention
    from serving_runtime.mvp.store import JsonlStore
    if type(fixture_count) is not int or not 2 <= fixture_count <= 200:
        raise ValueError("fixture count must be within 2..200")
    if (not math.isfinite(retention_age_seconds) or not .1 <= retention_age_seconds <= 604800 or
            type(retention_max_bytes) is not int or not 1 <= retention_max_bytes <= 5*1024**3):
        raise ValueError("bounded positive retention age/bytes required")
    directory = Path(data_dir).expanduser().absolute()
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    (directory / "OWNED-SELECTED-RUNTIME-TEST").write_text("synthetic fixture / isolated test stack only\n")
    inference, viewer = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    auth = {"schema_version": "runtime-auth/1.0", "auth_ref": "ISOLATED-SYNTHETIC-AUTH", "principals": [
        {"principal_id": "isolated-inference", "roles": ["inference"], "token_sha256": token_digest(inference)},
        {"principal_id": "isolated-viewer", "roles": ["viewer"], "token_sha256": token_digest(viewer)}],
        "route_roles": {"POST /v1/chat/completions": ["inference"], "GET /api/*": ["viewer"]}}
    policy = {"schema_version": "runtime-mvp/1.0", "policy_id": "isolated", "policy_version": "1", "target_id": "isolated", "allowed_models": [model],
              "output_default_tokens": 8, "output_max_tokens": 16, "max_body_bytes": 65536}
    auth_path, policy_path = directory / "auth.json", directory / "policy.json"
    hold = directory / "hold.json"
    hold.write_text(json.dumps({"schema":HOLD_SCHEMA,"request_ids":[],"run_ids":[]}));hold.chmod(0o600)
    for path, value in ((auth_path, auth), (policy_path, policy)):
        path.write_text(json.dumps(value)); path.chmod(0o600)
    config = MVPConfig(gateway_port=0, engine_port=0, backend=backend, backend_config_path=str(backend_config) if backend_config else None,
                       policy_path=str(policy_path), auth_config_path=str(auth_path), data_dir=str(directory / "records"),
                       run_id="selected-isolated-" + uuid4().hex, environment="isolated-acceptance",
                       retention_max_age_days=retention_age_seconds/86400,retention_max_bytes=retention_max_bytes,
                       retention_hold_path=str(hold),retention_interval_s=.1)
    engine = MockEngine(port=0).start() if backend == "mock" else None
    def start(cfg):
        return GatewayApp(cfg, load_policy(policy_path), engine_address=engine.address if engine else None,
                          backend_config=load_backend_config(backend_config) if backend != "mock" else None).start()
    gateway = None
    try:
        gateway = start(config)
        gateway_url = "http://127.0.0.1:" + str(gateway.address[1])
        ids = []
        for _ in range(2):
            response = request("POST", gateway_url + "/v1/chat/completions", token=inference,
                               body=json.dumps(json_body(model)).encode(), timeout=timeout)
            if response.status != 200:
                raise ValueError("isolated normal engine control did not return 200")
            ids.append(header(response, "X-Request-ID"))
        idle, _ = concurrent_precondition(gateway_url,viewer,timeout,required_slots=1)
        if idle is not True:raise ValueError("owned engine control finalization not confirmed")
        store = JsonlStore(Path(config.data_dir) / "gateway_records.jsonl")
        records, corrupt = store.read_all()
        sample = next(r for r in records if r.get("request_id") == ids[0] and r.get("phase") == "final")
        for _ in range(fixture_count - 2):
            record = json.loads(json.dumps(sample))
            record.update(request_id=uuid4().hex, data_origin="synthetic")
            store.append(record)
        held_run = "synthetic-retention-held-" + uuid4().hex
        route_ids = [uuid4().hex, uuid4().hex]
        route_store=JsonlStore(Path(config.data_dir)/"gateway_route_rejections.jsonl")
        receipt_store=JsonlStore(Path(config.data_dir)/"engine_receipts.jsonl")
        for index,rid in enumerate(route_ids):
            route={"schema_version":"runtime-route-rejection/1.0","event_id":uuid4().hex,"request_id":rid,"recorded_at":now(),
                   "method":"POST","path":"/synthetic-retention-fixture","response_code":404,"reason":"NOT_FOUND",
                   "forwarded_to_engine":False,"actor":{"state":"unattributed","principal_id":None},
                   "scope":{"run_id":held_run if index==1 else config.run_id,"environment":"isolated-acceptance"},"data_origin":"synthetic"}
            if index==0:route["run_id"]=config.run_id
            route_store.append(route)
            receipt_store.append({"request_id":rid,"received_at":now(),"run_id":config.run_id,"data_origin":"synthetic"})
        observations = JsonlStore(Path(config.data_dir) / "observations.jsonl")
        for source in ("hubble", "tetragon"):
            for index in range(60):
                observations.append({"source": source, "kind": "network_flow" if source == "hubble" else "process_exec", "observed_at": now(),
                    "data_origin": "synthetic", "run_id": held_run if index==0 else config.run_id, "environment": "isolated-acceptance", "event_key": hashlib.sha256(f"{source}/{index}".encode()).hexdigest()})
        from byul_platform.sources.runtime import gateway_event
        final_records, final_corrupt = store.read_all()
        expected_events = {"req:" + row["request_id"]: gateway_event(row) for row in final_records
                           if row.get("phase") == "final"}
        if final_corrupt or len(expected_events) != fixture_count:
            raise ValueError("isolated final request fixture is incomplete")
        with platform_server(gateway_url, viewer) as (platform_url, counter):
            before = len(counter.calls)
            responses = [request("GET", platform_url + "/api/runtime/events", timeout=timeout) for _ in range(5)]
            batches = [document(response) for response in responses]
            projected = [[event for event in batch.get("items", [])
                          if event.get("source") == "gateway" and event.get("category") == "request"]
                         for batch in batches]
            projection_matches = [len(events) == fixture_count and
                                  {event.get("event_id") for event in events} == set(expected_events) and
                                  all({key: event.get(key) for key in expected_events[event["event_id"]]} ==
                                      expected_events[event["event_id"]] for event in events)
                                  for events in projected]
            calls = counter.calls[before:]
            detail_calls = sum(bool(re.fullmatch(r"/api/requests/[0-9a-f]{32}", path)) for path in calls)
            cases = [fact("A4-LIST-LOAD", detail_calls == 0 and calls.count("/api/requests") == 5 and
                          all(response.status == 200 for response in responses) and all(projection_matches),
                          {"fixture_requests": fixture_count, "polls": 5, "detail_gets": detail_calls, "list_gets": calls.count("/api/requests"),
                           "synthetic_request_records": fixture_count - 2, "actual_normal_engine_requests": 2,
                           "http_statuses": [response.status for response in responses],
                           "gateway_events_per_poll": [len(events) for events in projected],
                           "exact_projection_per_poll": projection_matches}, scope="isolated real HTTP/OS list cache; synthetic history load")]
            cases.append(fact("A4-SOURCE-WINDOW-FIXTURE", validate_windows(batches[-1]) is True and
                         all(batches[-1]["source_windows"][s]["total"] == 60 and batches[-1]["source_windows"][s]["returned"] == 50 for s in ("hubble", "tetragon")),
                         {"source_windows": batches[-1].get("source_windows"), "synthetic_observations": 120}, scope="isolated source query window fixture"))
            public = platform_url + "/api/runtime/requests/" + ids[0]
            pre = [request("GET", public + suffix, timeout=timeout).status for suffix in ("", "/evidence")]
            hold.write_text(json.dumps({"schema": HOLD_SCHEMA, "request_ids": [ids[1]], "run_ids": [held_run]}))
            managed = [Path(config.data_dir)/item.name for item in MANAGED_FILES]
            before_report = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in managed}
            before_rows={p.name:JsonlStore(p).read_all() for p in managed}
            size_report=RecordRetention(config.data_dir,max_age_days=7,max_bytes=retention_max_bytes,hold_path=hold).run()
            future = datetime.now(timezone.utc) + timedelta(seconds=retention_age_seconds+1)
            options={"max_age_days":retention_age_seconds/86400,"max_bytes":retention_max_bytes,"hold_path":hold}
            report = RecordRetention(config.data_dir,**options).run(now=future)
            report_unchanged = before_report=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in managed}
            deleted = RecordRetention(config.data_dir, enforce=True,**options).run(now=future)
            post = [request("GET", public + suffix, timeout=timeout).status for suffix in ("", "/evidence")]
            events = document(request("GET", platform_url + "/api/runtime/events", timeout=timeout))
            held = request("GET", platform_url + "/api/runtime/requests/" + ids[1], timeout=timeout).status
            cases.append(fact("A4-RETENTION-FRESH", pre == [200, 200] and post == [404, 404] and held == 200 and
                              not any(e.get("scope", {}).get("request_id") == ids[0] for e in events["items"]) and report["mode"] == "report_only" and report_unchanged and deleted["deleted"] is True and not deleted.get("errors"),
                              {"before_detail_evidence": pre, "after_detail_evidence": post, "held_detail": held, "report_only": report["mode"],
                               "report_only_bytes_unchanged": report_unchanged, "deletion_applied": deleted["deleted"], "retention_errors": deleted.get("errors", [])}, scope="owned test data directory only; forward test clock / hold"))
            remaining={p.name:JsonlStore(p).read_all() for p in managed}
            actual_holds,hold_facts=retained_fixture_evidence(before_rows,remaining,deleted,{ids[1],route_ids[1]},held_run)
            all_four=(set(deleted['files'])=={p.name for p in managed} and report_unchanged and
                      all(f['removed_lines']>0 and f['held_units']>0 and f['durability']=='confirmed' for f in deleted['files'].values()) and
                      actual_holds and not deleted['errors'])
            held_legacy=any(r.get('request_id')==route_ids[1] for r in remaining['gateway_route_rejections.jsonl'][0])
            held_receipt=any(r.get('request_id')==route_ids[1] for r in remaining['engine_receipts.jsonl'][0])
            cases.append(fact('A4-RETENTION-MANAGED4',all_four and held_legacy and held_receipt,
                         {'managed_files':deleted['files'],'limits':deleted['limits'],'forward_test_clock':future.isoformat(),
                          'report_only_all_four_unchanged':report_unchanged,'request_hold_detail_http':held,
                          'legacy_scope_run_hold_preserved':held_legacy,'cross_file_receipt_hold_preserved':held_receipt,
                          'remaining_lines':{name:len(rows) for name,(rows,corrupt) in remaining.items()},
                          'actual_hold_comparison':hold_facts,
                          'synthetic_route_records':2,'synthetic_receipt_records':2,'synthetic_observations':120,
                          'retention_log_state':deleted['retention_log']['state']},scope='owned four managed JSONL files; explicit synthetic history + two actual normal controls'))
            cases.append(fact('A4-RETENTION-SIZE-CANDIDATES',True if sum(f['removable_by_size'] for f in size_report['files'].values())>0 else None,
                         {'max_bytes':retention_max_bytes,'size_only_report_age_days':7,'mode':size_report['mode'],
                          'removable_by_size':{name:f['removable_by_size'] for name,f in size_report['files'].items()}},scope='owned report-only size threshold; holds remain legal'))
            projection=None;projection_deadline=time.monotonic()+min(timeout,3)
            while time.monotonic()<projection_deadline:
                runtime_doc=document(request('GET',gateway_url+'/api/collectors',token=viewer,timeout=max(.001,projection_deadline-time.monotonic())))
                platform_doc=document(request('GET',platform_url+'/api/runtime/status',timeout=max(.001,projection_deadline-time.monotonic())))
                raw_ret=next((c.get('last_value') or {} for c in runtime_doc.get('collectors',[]) if c.get('source')=='record_retention'),{})
                api_ret=next((c.get('detail') or {} for c in (platform_doc.get('item') or {}).get('collectors',[]) if c.get('source')=='record_retention'),{})
                raw_last=raw_ret.get('last_deletion') or {};api_last=api_ret.get('last_deletion') or {}
                counts={name:f['removed_lines'] for name,f in (raw_last.get('files') or {}).items()}
                api_counts={f['name']:f['removed_lines'] for f in api_last.get('files',[])}
                projection={'runtime_removed_lines':counts,'platform_removed_lines':api_counts,
                            'runtime_last_deletion_at':raw_last.get('ran_at'),'platform_last_deletion_at':api_last.get('ran_at'),
                            'platform_max_age_days':api_ret.get('max_age_days'),'platform_max_bytes':api_ret.get('max_bytes'),
                            'runtime_mode':raw_ret.get('mode'),'platform_mode':api_ret.get('mode'),
                            'runtime_holds_state':(raw_ret.get('holds') or {}).get('state'),'platform_holds_state':api_ret.get('holds_state'),
                            'runtime_errors':raw_ret.get('errors'),'platform_errors':api_ret.get('errors'),
                            'platform_contract_valid':platform_doc.get('invalid') is None}
                if counts and counts==api_counts and raw_last.get('ran_at')==api_last.get('ran_at')==deleted['ran_at']:break
                time.sleep(min(.01,max(0,projection_deadline-time.monotonic())))
            cases.append(fact('A4-RETENTION-PROJECTION',bool(projection) and projection['runtime_removed_lines']==projection['platform_removed_lines'] and
                         projection['runtime_last_deletion_at']==projection['platform_last_deletion_at']==deleted['ran_at'] and
                         projection['platform_max_bytes']==retention_max_bytes and projection['platform_max_age_days']==retention_age_seconds/86400 and
                         projection['runtime_mode']==projection['platform_mode']=='report_only' and
                         projection['runtime_holds_state']==projection['platform_holds_state']=='ok' and
                         projection['runtime_errors']==projection['platform_errors']==[] and projection['platform_contract_valid'],
                         projection or {},scope='owned runtime collector to Platform API deletion history and selected limits'))
            # Reconfigure by restarting only the owned Gateway. Production auth
            # loading semantics remain unchanged; the Platform client stays warm.
            port = gateway.address[1]
            gateway.stop(); gateway = None
            auth["principals"][1]["disabled"] = True
            auth_path.write_text(json.dumps(auth))
            config = replace(config, gateway_port=port)
            gateway = start(config)
            denied_detail = request("GET", platform_url + "/api/runtime/requests/" + ids[1], timeout=timeout).status
            denied_list = request("GET", platform_url + "/api/runtime/events", timeout=timeout).status
            denied_evidence = request("GET", platform_url + "/api/runtime/requests/" + ids[1] + "/evidence", timeout=timeout).status
            gateway.stop(); gateway = None
            auth["principals"][1]["disabled"] = False
            auth_path.write_text(json.dumps(auth))
            gateway = start(config)
            recovered = request("GET", platform_url + "/api/runtime/requests/" + ids[1], timeout=timeout).status
            cases.append(fact("A4-AUTH-FRESH", [denied_detail, denied_list, denied_evidence, recovered] == [502, 502, 502, 200],
                              {"disabled_detail": denied_detail, "disabled_list": denied_list, "disabled_evidence": denied_evidence, "recovered_detail": recovered,
                               "auth_change_method": "owned_gateway_restart"}, scope="warm Platform and real runtime permission failure/recovery"))
        return {"cases": cases, "owned_test_data_dir": str(directory), "backend": backend,
                "engine_scope": "mock HTTP control" if backend == "mock" else "same configured engine via isolated Gateway", "gpu_execution_verified": False}
    finally:
        if gateway is not None:
            gateway.stop()
        if engine is not None:
            engine.stop()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("live", "isolated", "requests", "separation", "lifecycle"):
        command = sub.add_parser(name)
        command.add_argument("--output", required=True, type=Path)
        command.add_argument("--model", required=True)
        command.add_argument("--timeout", type=float, default=15)
    live_parser = sub.choices["live"]
    live_parser.add_argument("--gateway-url", required=True)
    live_parser.add_argument("--platform-url")
    live_parser.add_argument("--inference-token-env", required=True)
    live_parser.add_argument("--second-inference-token-env")
    live_parser.add_argument("--viewer-token-env")
    live_parser.add_argument("--forbidden-key", action="append", default=[])
    for name in ("requests", "separation", "lifecycle"):
        request_parser = sub.choices[name]
        request_parser.add_argument("--gateway-url", required=True)
        request_parser.add_argument("--platform-url")
        request_parser.add_argument("--inference-token-env", required=True)
        request_parser.add_argument("--viewer-token-env", required=True)
    lifecycle_parser = sub.choices["lifecycle"]
    lifecycle_parser.add_argument("--replacement-model", required=True)
    lifecycle_parser.add_argument("--phase", choices=("before", "after"), required=True)
    lifecycle_parser.add_argument("--before-report", type=Path)
    lifecycle_parser.add_argument("--backend-config", type=Path)
    isolated_parser = sub.choices["isolated"]
    isolated_parser.add_argument("--test-data-dir", required=True, type=Path)
    isolated_parser.add_argument("--backend", choices=("mock", "vllm", "sglang"), default="mock")
    isolated_parser.add_argument("--backend-config", type=Path)
    isolated_parser.add_argument("--fixture-count", type=int, default=200)
    isolated_parser.add_argument("--retention-age-seconds", type=float, default=1)
    isolated_parser.add_argument("--retention-max-bytes", type=int, default=4096)
    copies_parser = sub.add_parser("copies")
    copies_parser.add_argument("--output", required=True, type=Path)
    copies_parser.add_argument("--timeout", type=float, default=15)
    copies_parser.add_argument("--file", action="append", required=True, type=Path)
    copies_parser.add_argument("--marker-env", action="append", default=[])
    copies_parser.add_argument("--credential-env", action="append", default=[])
    copies_parser.add_argument("--allowed-reader")
    copies_parser.add_argument("--denied-reader")
    copies_parser.add_argument("--volume-evidence", type=Path)
    observe_parser = sub.add_parser("observe")
    observe_parser.add_argument("--output",required=True,type=Path)
    observe_parser.add_argument("--timeout",type=float,default=15)
    observe_parser.add_argument("--gateway-url",required=True)
    observe_parser.add_argument("--platform-url",required=True)
    observe_parser.add_argument("--viewer-token-env",required=True)
    observe_parser.add_argument("--source",choices=("hubble","tetragon"),required=True)
    observe_parser.add_argument("--expected-state",choices=("ok","partial","error","stale","not_configured"))
    observe_parser.add_argument("--wait-seconds",type=float,default=5)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 60:
        parser.error("timeout must be finite and within 1..60 seconds per operation")
    if args.output.exists():
        parser.error("output must be a new path")
    started = now()
    try:
        if args.mode == "observe":
            from tools.selected_observation_checks import observe
            viewer=os.environ.get(args.viewer_token_env)
            result=observe(args.gateway_url,args.platform_url,viewer,args.source,expected_state=args.expected_state,
                           wait_seconds=args.wait_seconds,timeout=args.timeout)
            if viewer and viewer in json.dumps(result):raise ValueError("SELECTED_CREDENTIAL_EXPORT_BLOCKED")
            result['gpu_execution_verified']=False
        elif args.mode == "copies":
            from tools.selected_copy_checks import copies
            names = args.marker_env + args.credential_env
            if not names or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", n) or not os.environ.get(n) for n in names):
                parser.error("existing nonempty marker/credential environment references required")
            values = {n: os.environ[n] for n in names}
            result = copies(args.file, values, allowed_reader=args.allowed_reader, denied_reader=args.denied_reader,
                            timeout=args.timeout, volume_evidence=args.volume_evidence)
            encoded = json.dumps(result)
            if any(value in encoded for value in values.values()):
                raise ValueError("SELECTED_CREDENTIAL_EXPORT_BLOCKED")
            result["gpu_execution_verified"] = False
        elif args.mode in ("live", "requests", "separation", "lifecycle"):
            token = os.environ.get(args.inference_token_env)
            if not token:
                parser.error("inference token environment value is missing")
            if args.mode in ("requests", "separation", "lifecycle"):
                if args.mode == "requests":
                    from tools.selected_request_checks import request_matrix
                    result = request_matrix(args.gateway_url, args.platform_url, args.model, token,
                                            os.environ.get(args.viewer_token_env), timeout=args.timeout)
                elif args.mode == "separation":
                    from tools.selected_response_checks import separation
                    result = separation(args.gateway_url, args.model, token,
                                        os.environ.get(args.viewer_token_env), timeout=args.timeout)
                else:
                    from tools.selected_model_checks import model_phase
                    result = model_phase(args.gateway_url, args.platform_url, args.model, args.replacement_model,
                                         token, os.environ.get(args.viewer_token_env), args.phase, timeout=args.timeout,
                                         before_report=args.before_report, backend_config=args.backend_config)
                encoded = json.dumps(result)
                if any(value and value in encoded for value in (token, os.environ.get(args.viewer_token_env))):
                    raise ValueError("SELECTED_CREDENTIAL_EXPORT_BLOCKED")
                result["gpu_execution_verified"] = False
            else:
                result = {"cases": live(args.gateway_url, args.platform_url, args.model, token,
                           os.environ.get(args.viewer_token_env) if args.viewer_token_env else None, timeout=args.timeout,
                           second_token=os.environ.get(args.second_inference_token_env) if args.second_inference_token_env else None,
                           forbidden=args.forbidden_key), "gpu_execution_verified": False}
        else:
            if not 2 <= args.fixture_count <= 200 or (args.backend != "mock" and not args.backend_config):
                parser.error("fixture count must be 2..200 and external backend requires backend-config")
            result = isolated(args.test_data_dir, args.model, backend=args.backend, backend_config=args.backend_config,
                              timeout=args.timeout, fixture_count=args.fixture_count,
                              retention_age_seconds=args.retention_age_seconds,retention_max_bytes=args.retention_max_bytes)
    except Exception as error:
        # Keep infrastructure/collector failure as UNKNOWN; never persist an
        # exception message that might include a credential or response body.
        result = {"execution": "aborted", "error_class": type(error).__name__,
                  "gpu_execution_verified": False,
                  "cases": [fact("TOOL-EXECUTION", None, {"error_class": type(error).__name__}, scope="execution incomplete")]}
    import sys
    modules = ["tools.selected_request_checks", "tools.selected_response_checks", "tools.selected_copy_checks", "tools.selected_model_checks", "tools.selected_observation_checks", "byul_platform.sources.checklist_data", "guard.executors.stream_probe", "serving_runtime.mvp.validator", "serving_runtime.mvp.retention", "byul_platform.sources.runtime", "serving_runtime.mvp.response_timeout"]
    local_sources = {}
    for name in modules:
        module = sys.modules.get(name)
        path = getattr(module, "__file__", None)
        if path:
            local_sources[name] = {"path": str(Path(path).resolve()), "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()}
    result["local_tool_source_files"] = local_sources
    result["remote_source_identity_verified"] = False
    reused = {"live": ["UPDATE stream_probe.collect", "runtime DeadlineWatchdog"],
              "requests": ["runtime QueryService/route-audit API", "platform request API", "runtime DeadlineWatchdog"],
              "separation": ["UPDATE stream_probe.collect", "runtime QueryService API", "runtime DeadlineWatchdog"],
              "isolated": ["runtime RecordRetention", "platform LiveRuntimeSource", "runtime DeadlineWatchdog"],
              "copies": ["POSIX file metadata/read", "existing OS reader accounts"]}
    reused["lifecycle"] = ["runtime QueryService API", "platform request API", "runtime DeadlineWatchdog"]
    reused["observe"] = ["runtime status/collector API", "platform status/events/checks API", "runtime DeadlineWatchdog"]
    result.update(schema="byul-selected-runtime-check/1", mode=args.mode, started_at=started, finished_at=now(),
                  source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), reused=reused[args.mode])
    with args.output.open("x") as output:
        json.dump(result, output, ensure_ascii=False, allow_nan=False, indent=2)
    return 2 if result.get("execution") == "aborted" else 1 if any(c["result"] == "failed" for c in result["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
