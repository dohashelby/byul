"""Observe selected in-process resources without keeping their owners alive."""

from __future__ import annotations

import asyncio
from collections import deque
import contextvars
import hashlib
import json
import os
import re
import sys
import time
import weakref
from pathlib import Path

from owned_stream_poll import ASSETS, ENGINE_DATA, NS_PER_MS, case_from_header, clock_identity, read_client_origin, write_atomic
from owned_tcp_diag import identify_open_socket


HEX = re.compile("[0-9a-f]{64}")
SCOPE = contextvars.ContextVar("byul_owned_stream", default=None)
PROTOCOL_FIELDS = ("connections", "tasks", "pipeline", "timeout_keep_alive_task", "flow", "cycle")
RESOURCE_NAMES = (
    "http_protocol", "request_response_cycle", "response_transport", "flow_controller",
    "asgi_task", "keepalive_timer", "asgi_stream_writer", "asgi_body_iterator",
)
COUNT_FIELDS = ("owned_connection_registered_count", "owned_task_registered_count", "owned_pipeline_entries",
                "protocol_keepalive_handle_count", "matched_active_drain_calls", "public_user_write_buffer_bytes")
PHASES = frozenset(("REQUEST_ENTER", "SEND_ENTER", "SEND_RETURN", "SEND_RAISE", "RUN_RETURN", "RUN_RAISE",
                   "DRAIN_ENTER", "DRAIN_RETURN", "DRAIN_RAISE",
                   "CONNECTION_LOST_ENTER", "CONNECTION_LOST_RETURN", "CONNECTION_LOST_RAISE"))


def name(value):
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"


def source_receipt(code):
    path = Path(code.co_filename).resolve()
    if not path.is_file() or not 0 < path.stat().st_size < 1048576:
        raise ValueError("observer source unavailable")
    return {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "symbol": code.co_qualname}


class Resources:
    """Retain resource identity with weak references, counts and simple metadata."""

    def __init__(self):
        self.references = {}
        self.types = {}
        self.pending_drains = set()
        self.drain_entries = 0
        self.writer_returned = None

    def remember(self, label, value):
        if value is None:
            return
        self.types[label] = name(value)
        try:
            self.references[label] = weakref.ref(value)
        except TypeError:
            self.references[label] = None

    def get(self, label):
        reference = self.references.get(label)
        return reference() if reference is not None else None

    def inventory(self):
        return {
            key: {
                "type": self.types.get(key),
                "count": (int(self.get(key) is not None) if self.references[key] is not None else None)
                         if key in self.references else None,
                "reference_state": ("present" if self.get(key) is not None else "released")
                                   if self.references.get(key) is not None else
                                   ("weakref_unavailable" if key in self.types else "unobserved"),
            }
            for key in RESOURCE_NAMES
        }

    def sample(self, *, active_protocol=None):
        protocol = active_protocol or self.get("http_protocol")
        task = self.get("asgi_task")
        conn_set = getattr(protocol, "connections", None) if protocol is not None else self.get("connection_set")
        tasks = getattr(protocol, "tasks", None) if protocol is not None else self.get("task_set")
        pipeline = getattr(protocol, "pipeline", None) if protocol is not None else self.get("pipeline")
        timer = getattr(protocol, "timeout_keep_alive_task", None)
        if timer is not None:
            self.remember("keepalive_timer", timer)
        remembered_timer = self.get("keepalive_timer")
        transport = self.get("response_transport")
        flow = self.get("flow_controller")
        cycle = self.get("request_response_cycle")
        iterator = self.get("asgi_body_iterator")
        write_bytes = None
        if transport is not None:
            try:
                result = transport.get_write_buffer_size()
                if type(result) is int and result >= 0:
                    write_bytes = result
            except (AttributeError, OSError, RuntimeError, NotImplementedError):
                pass
        state = {
            "owned_connection_registered_count": int(protocol in conn_set)
                     if type(conn_set) is set and self.references.get("http_protocol") is not None else None,
            "owned_task_registered_count": int(task in tasks)
                     if type(tasks) is set and self.references.get("asgi_task") is not None else None,
            "owned_pipeline_entries": len(pipeline) if type(pipeline) is deque else None,
            "protocol_keepalive_handle_count": int(timer is not None)
                  if protocol is not None and hasattr(protocol, "timeout_keep_alive_task") else None,
            "keepalive_handle_cancelled": remembered_timer.cancelled() if remembered_timer is not None else None,
            "asgi_task_done": task.done() if task is not None else None,
            "asgi_response_complete": getattr(cycle, "response_complete", None),
            "asgi_cycle_disconnected": getattr(cycle, "disconnected", None),
            "async_iterator_frame_live": iterator.ag_frame is not None if type(iterator).__name__ == "async_generator" else None,
            "response_writer_returned": self.writer_returned,
            "write_paused": getattr(flow, "write_paused", None),
            "matched_active_drain_calls": len(self.pending_drains),
            "matched_drain_entries": self.drain_entries,
            "public_user_write_buffer_bytes": write_bytes,
        }
        return {"counts_and_states": state, "resources": self.inventory()}


class Observer:
    def __init__(self, emitter):
        self.emitter = emitter.__code__
        self.pid = os.getpid()
        self.owner_uid = os.environ["BYUL_OWNED_STREAM_POD_UID"]
        self.session = None
        self.config = None
        self.connections = {}
        self.protocol_sources = {}
        self.writer_code = None
        self.completion_codes = set()
        self.clock = clock_identity()
        self.process_start_ticks = int(Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19])
        self.monitor = sys.monitoring
        self.tool = next((i for i in (5, 4, 3) if self.monitor.get_tool(i) is None), None)
        if self.tool is None:
            raise RuntimeError("local monitoring tool slot unavailable")

    def event(self, kind, **body):
        document = {"event": kind, "mono_ns": time.monotonic_ns(), "utc_ns": time.time_ns(),
                    "pid": self.pid, "pod_uid": self.owner_uid, **body}
        raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
        path = ENGINE_DATA / f"resources-{self.pid}.jsonl"
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)

    def install(self):
        # Select the already installed product emitter. Local monitoring does
        # not wrap the HTTP request path, change the loop, or edit the sensor.
        monitor = self.monitor
        monitor.use_tool_id(self.tool, "byul-owned-stream-lifetime")
        monitor.register_callback(self.tool, monitor.events.PY_START, self.started)
        monitor.register_callback(self.tool, monitor.events.PY_RETURN, self.returned)
        monitor.set_local_events(self.tool, self.emitter, monitor.events.PY_START)
        self.event("loader", emitter=source_receipt(self.emitter),
                   observer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())

    def ensure_config(self):
        if self.config is None:
            config = json.loads((ASSETS / "session.json").read_bytes())
            if config["engine_pod_uid"] != self.owner_uid or config["budget_ms"] != 5000:
                raise ValueError("observer does not match selected deployment")
            self.config = config
            self.session = config["session"]

    def register(self, tag, call, transport, cycle):
        self.ensure_config()
        scope = getattr(cycle, "scope", None)
        if type(scope) is not dict or scope.get("path") != "/v1/completions":
            raise ValueError("owned observation request path differs")
        ids = [v for k,v in scope["headers"] if k.lower()==b"x-request-id"]
        case = case_from_header(ids[0]) if len(ids) == 1 else None
        if case is None:
            raise ValueError("owned observation case unavailable")
        protocol = transport.get_protocol()
        kind = name(protocol)
        if kind not in ("uvicorn.protocols.http.httptools_impl.HttpToolsProtocol",
                        "uvicorn.protocols.http.h11_impl.H11Protocol"):
            raise ValueError("unreviewed active HTTP protocol")
        if kind not in self.protocol_sources:
            completion = type(protocol).on_response_complete.__code__
            receipt = source_receipt(completion)
            expected = self.config["reviewed_uvicorn_sources"][kind.rsplit(".", 1)[0]]
            if receipt["sha256"] != expected:
                raise ValueError("active Uvicorn installation differs from reviewed source")
            python_source = Path(completion.co_filename).read_text(encoding="utf-8")
            receipt["reviewed_protocol_attributes"] = [key for key in PROTOCOL_FIELDS if f"self.{key}" in python_source]
            self.protocol_sources[kind] = receipt
            self.completion_codes.add(completion)
            self.monitor.set_local_events(self.tool, completion, self.monitor.events.PY_RETURN)
        start = time.monotonic_ns()
        identity = identify_open_socket(transport.get_extra_info("socket"))
        if (identity.local_address, identity.local_port, identity.peer_address) != (
                self.config["engine_ip"], 8000, self.config["probe_ip"]):
            raise ValueError("socket peers differ from authorized test Pods")
        document = {"connection_sha256": tag, "native_request_call_sha256": call, "case_id": case, "socket": identity.json(),
                    "session": self.session, "namespace_uid": self.config["namespace_uid"], "pod_uid": self.owner_uid,
                    "probe_pod_uid": self.config["probe_pod_uid"], "registered_monotonic_ns": start,
                    "registration_finished_monotonic_ns": time.monotonic_ns(), "clock": self.clock,
                    "pid": self.pid, "process_start_ticks": self.process_start_ticks,
                    "protocol": kind, "protocol_source": self.protocol_sources[kind]}
        write_atomic(ENGINE_DATA / f"owned-{tag}.json", document)
        resources = Resources()
        resources.remember("http_protocol", protocol)
        resources.remember("connection_set", getattr(protocol, "connections", None))
        resources.remember("task_set", getattr(protocol, "tasks", None))
        resources.remember("response_transport", transport)
        resources.remember("flow_controller", getattr(protocol, "flow", None))
        resources.remember("pipeline", getattr(protocol, "pipeline", None))
        resources.remember("request_response_cycle", getattr(protocol, "cycle", None))
        resources.remember("asgi_task", asyncio.current_task())
        self.connections[tag] = resources
        self.install_writer()
        return resources

    def install_writer(self):
        if self.writer_code is not None:
            return
        module = sys.modules.get("starlette.responses")
        body = getattr(getattr(module, "StreamingResponse", None), "stream_response", None)
        if body is not None:
            code = body.__code__
            receipt = source_receipt(code)
            if code.co_qualname == "StreamingResponse.stream_response" and receipt["file"] == "responses.py":
                self.event("writer_source", source=receipt)
                self.writer_code = code
                mono = self.monitor.events
                self.monitor.set_local_events(self.tool, code, mono.PY_START | mono.PY_RETURN)

    def snapshot(self, tag, phase, protocol=None, product_call=None):
        resources = self.connections[tag]
        started = time.monotonic_ns()
        values = resources.sample(active_protocol=protocol)
        self.event("resources", connection_sha256=tag, phase=phase, product_call_sha256=product_call,
                   sample_started_monotonic_ns=started,
                   **values)
        return values

    def sample_followup(self, tag, horizon, pending=None):
        stamp = time.monotonic_ns()
        phase = "client_deadline_sample" if pending is None else "post_deadline_followup"
        states = self.snapshot(tag, phase)
        values = {k: item["count"] for k, item in states["resources"].items()}
        values.update({k: states["counts_and_states"][k] for k in COUNT_FIELDS})
        if pending is None:
            pending = {k for k, value in values.items() if value is not None and value > 0}
        pending = {k for k in pending if values[k] != 0}
        if pending and stamp < horizon:
            loop = asyncio.get_running_loop()
            loop.call_later(min(3, (horizon - stamp) / 1e9), self.sample_followup, tag, horizon, pending)
        else:
            self.event("app_followup_stop", connection_sha256=tag, horizon_ns=horizon,
                       pending_last_values={k: values[k] for k in sorted(pending)},
                       reason="right_censored_at_followup_horizon" if pending else "no_pending_owned_resource_in_followup")

    def sample_client_deadline(self, tag, remaining=16):
        loop = asyncio.get_running_loop()
        try:
            origin = read_client_origin(tag, self.config, self.clock)
        except (OSError, KeyError, TypeError, ValueError) as exc:
            self.event("observer_error", connection_sha256=tag, phase="client_anchor", error_type=type(exc).__name__)
            return
        if origin is not None:
            sample_at = origin["upper_ns"] + (self.config["budget_ms"] + 20) * NS_PER_MS
            horizon = origin["upper_ns"] + self.config["followup_ms"] * NS_PER_MS
            loop.call_later(max(0, (sample_at - time.monotonic_ns()) / 1e9), self.sample_followup, tag, horizon)
        elif remaining:
            loop.call_later(0.1, self.sample_client_deadline, tag, remaining - 1)

    def record_debug(self, tag, cycle, resources):
        if getattr(resources, "debug_read", False) or cycle is None:
            return
        scope = getattr(cycle, "scope", None)
        app = scope.get("app") if type(scope) is dict else None
        if name(app) != "fastapi.applications.FastAPI":
            return
        flag = None
        module = sys.modules.get("vllm.envs")
        try:
            observed = getattr(module, "VLLM_DEBUG_LOG_API_SERVER_RESPONSE")
            flag = observed if type(observed) is bool else None
        except (AttributeError, TypeError, ValueError):
            pass
        names = []
        for middleware in app.user_middleware:
            options = getattr(middleware, "kwargs", getattr(middleware, "options", {}))
            handler = options.get("dispatch")
            if handler is not None:
                names.append(getattr(handler, "__name__", ""))
        resources.debug_read = True
        registered = "log_response" in names
        self.event("response_logging", connection_sha256=tag, effective=flag, installed_handler_registered=registered,
                   response_debug_buffer_state="inactive" if flag is False and not registered else "unobserved")

    def started(self, code, _offset):
        if os.getpid() != self.pid:
            return
        try:
            context = sys._getframe(1).f_locals
            if code is self.writer_code:
                tag = SCOPE.get()
                resources = self.connections.get(tag)
                if resources is not None and name(writer := context.get("self")) == "starlette.responses.StreamingResponse":
                    resources.remember("asgi_stream_writer", writer)
                    resources.remember("asgi_body_iterator", getattr(writer, "body_iterator", None))
                    resources.writer_returned = False
                    self.snapshot(tag, "body_writer_start")
                return
            phase = context.get("phase")
            entry = context.get("entry")
            tag = entry.get("connection", "") if type(entry) is dict else ""
            if code is not self.emitter or phase not in PHASES or not HEX.fullmatch(tag):
                return
            call = context.get("call")
            resources = self.connections.get(tag)
            if (resources is None and phase == "REQUEST_ENTER" and type(call) is str
                    and HEX.fullmatch(call) and len(self.connections) < 5):
                resources = self.register(tag, call, context["transport"], context["cycle"])
            if resources is None:
                return
            if phase == "REQUEST_ENTER":
                SCOPE.set(tag)
            if phase == "CONNECTION_LOST_ENTER":
                write_atomic(ENGINE_DATA / f"close-{tag}.json", {
                    "connection_sha256": tag, "session": self.session, "pod_uid": self.owner_uid,
                    "mono_ns": time.monotonic_ns(), "clock": self.clock,
                })
            if phase == "DRAIN_ENTER":
                resources.drain_entries += 1
                resources.pending_drains.add(context.get("call"))
            elif phase in ("DRAIN_RETURN", "DRAIN_RAISE"):
                resources.pending_drains.discard(context.get("call"))
            self.record_debug(tag, context.get("cycle"), resources)
            self.snapshot(tag, phase, product_call=context.get("call"))
            if phase in ("CONNECTION_LOST_RETURN", "CONNECTION_LOST_RAISE"):
                loop = asyncio.get_running_loop()
                for delay in (0.0, 0.025, 0.1, 0.25, 1.0, 2.5, 4.9, 5.15):
                    loop.call_later(delay, self.snapshot, tag, "post_close_sample")
                loop.call_soon(self.sample_client_deadline, tag)
            elif phase in ("RUN_RETURN", "RUN_RAISE") and SCOPE.get() == tag:
                SCOPE.set(None)
        except (AttributeError, OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            self.event("observer_error", reason=type(exc).__name__, detail=str(exc)[:180])

    def returned(self, code, _offset, _result):
        if os.getpid() != self.pid:
            return
        try:
            if code is self.writer_code and (tag := SCOPE.get()) in self.connections:
                self.connections[tag].writer_returned = True
                self.snapshot(tag, "body_writer_return")
            if code in self.completion_codes:
                active = sys._getframe(1).f_locals.get("self")
                for tag, resources in self.connections.items():
                    if resources.get("http_protocol") is active and active is not None:
                        self.snapshot(tag, "response_complete", active)
        except (AttributeError, OSError, ValueError, RuntimeError, TypeError) as exc:
            self.event("observer_error", reason=type(exc).__name__, detail=str(exc)[:180])


def install():
    product = sys.modules.get("guard_lifecycle_sensor")
    observer = getattr(getattr(product, "_BASE", None), "Observer", None)
    emitter = getattr(observer, "stream_emit", None)
    if emitter is not None and ENGINE_DATA.is_dir():
        Observer(emitter).install()
