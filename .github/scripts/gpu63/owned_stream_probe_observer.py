"""Observe only the five owned requests inside the unmodified stock probe."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import socket
import sys
import time
import weakref
from pathlib import Path
from types import CodeType

from owned_stream_poll import ASSETS, CASE_IDS, PROBE_DATA, case_from_header, clock_identity, endpoint_key, write_atomic
from owned_tcp_diag import ExactDiagnostic, identify_open_socket, observe


FILENAME = "guard-stream-probe"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def stock_source(program, expected):
    tree = ast.parse(program)
    main = tree.body[-1]
    if not isinstance(main, ast.Expr) or not isinstance(main.value, ast.Call):
        raise ValueError("unexpected stock probe wrapper")
    call = main.value
    if not isinstance(call.func, ast.Name) or call.func.id != "exec" or len(call.args) != 2:
        raise ValueError("unexpected stock probe main")
    child = call.args[0]
    if (not isinstance(child, ast.Call) or not isinstance(child.func, ast.Name) or child.func.id != "compile"
            or len(child.args) != 3 or not all(isinstance(arg, ast.Constant) for arg in child.args)):
        raise ValueError("unexpected stock probe source command")
    source, filename, mode = (arg.value for arg in child.args)
    if type(source) is not str or filename != FILENAME or mode != "exec" or sha(source.encode()) != expected:
        raise ValueError("stock probe source digest mismatch")
    return source


def reviewed_lines(source):
    lines = [line.strip() for line in source.splitlines()]
    def unique(text):
        found = [i for i, text_at in enumerate(lines, start=1) if text_at == text]
        if len(found) != 1:
            raise ValueError("stock probe observation point changed")
        return found[0]
    register = unique("sockets[index] = tracked")
    shutdown = unique("tracked.shutdown(socket.SHUT_RDWR)")
    close = unique("tracked.close()")
    if (lines[close:close + 3] != ["response.close()", "conn.close()",
                                   "output.update(client_closed=True, client_close_ms=milliseconds(),"]
            or not register < shutdown < close):
        raise ValueError("stock probe close sequence changed")
    return {register: "opened", shutdown: "shutdown_start", close: "tracked_close_start",
            close + 1: "response_close_start", close + 2: "http_connection_close_start",
            close + 3: "close_block_end"}


def request_identity(request, config):
    case = request.get("id")
    if (case not in CASE_IDS or request.get("host") != config["engine_ip"] or request.get("port") != 8000
            or request.get("path") != "/v1/completions" or request.get("method") != "POST"):
        return None
    token = next((v for k, v in request["headers"].items() if k.lower() == "x-request-id"), None)
    if type(token) is not str or not token.isascii() or case_from_header(token.encode("ascii")) != case:
        return None
    return case


def interval(kind, lower, upper):
    return {"kind": kind, "lower_ns": lower, "upper_ns": upper, "width_ns": upper - lower}


class ProbeObserver:
    def __init__(self, config, expected, lines):
        self.config, self.expected, self.lines = config, expected, lines
        self.clock = clock_identity()
        self.build_sha256 = sha(Path(__file__).read_bytes())
        close_source = Path(socket.socket._real_close.__code__.co_filename).resolve()
        self.stdlib_close_sha256 = sha(close_source.read_bytes())
        self.active = {}
        self.by_socket = {}
        self.monitor = sys.monitoring
        self.ev = self.monitor.events
        self.tool = next((i for i in (5, 4, 3) if self.monitor.get_tool(i) is None), None)
        if self.tool is None:
            raise RuntimeError("probe monitor slot unavailable")

    def install(self):
        m = self.monitor
        m.use_tool_id(self.tool, "byul-owned-client-close")
        m.register_callback(self.tool, self.ev.PY_START, self.on_start)
        m.register_callback(self.tool, self.ev.LINE, self.on_line)
        m.register_callback(self.tool, self.ev.PY_RETURN, self.on_return)
        m.set_events(self.tool, self.ev.PY_START)

    def on_start(self, code, _offset):
        m, close = self.monitor, socket.socket._real_close.__code__
        if code.co_filename == FILENAME and code.co_name == "<module>":
            m.set_events(self.tool, self.ev.NO_EVENTS)
            collect, = [c for c in code.co_consts if isinstance(c, CodeType) and c.co_name == "collect"]
            worker, = [c for c in collect.co_consts if isinstance(c, CodeType) and c.co_name == "worker"]
            m.set_local_events(self.tool, worker, self.ev.LINE)
            m.set_local_events(self.tool, close, self.ev.PY_START | self.ev.PY_RETURN)
        elif code is close:
            owner = sys._getframe(1).f_locals.get("self")
            row = self.match_socket(owner)
            if row is not None and owner.fileno() >= 0:
                row["fd_close_enter_monotonic_ns"] = time.monotonic_ns()

    def match_socket(self, sock):
        value = self.by_socket.get(id(sock))
        return value[1] if value and value[0]() is sock else None

    def save(self, row):
        path = PROBE_DATA / f"client-{row['endpoint_key']}.json"
        try:
            previous = json.loads(path.read_bytes())
        except FileNotFoundError:
            previous = None
        if previous is not None and (previous.get("tuple_reuse_observed") is True
                or previous["socket"] != row["socket"]
                or previous["case_id"] != row["case_id"]):
            write_atomic(path, {"endpoint_key": row["endpoint_key"], "tuple_reuse_observed": True})
        else:
            write_atomic(path, row)

    def opened(self, sock, request):
        case = request_identity(request, self.config)
        if case is None:
            return None
        identity = identify_open_socket(sock)
        if (identity.local_address != self.config["probe_ip"] or identity.peer_address != self.config["engine_ip"]
                or identity.peer_port != 8000):
            return None
        try:
            diag = ExactDiagnostic()
            try:
                positive = observe(diag.query, identity)
            finally:
                diag.close()
        except OSError as exc:
            stamp = time.monotonic_ns()
            positive = {"start_ns": stamp, "end_ns": stamp, "status": "diagnostic_setup_error", "errno": exc.errno}
        row = {"session": self.config["session"], "pod_uid": self.config["probe_pod_uid"], "clock": self.clock,
               "namespace_uid": self.config["namespace_uid"], "pid": os.getpid(), "case_id": case,
               "socket": identity.json(), "initial_diagnostic": positive,
               "endpoint_key": endpoint_key(identity.local_address, identity.local_port, identity.peer_address, identity.peer_port),
               "probe_source_sha256": self.expected, "probe_observer_sha256": self.build_sha256,
               "stdlib_socket_sha256": self.stdlib_close_sha256, "stock_close_calls": {}}
        self.by_socket[id(sock)] = (weakref.ref(sock), row)
        self.save(row)
        return row

    def on_line(self, _code, line):
        point = self.lines.get(line)
        if point is None:
            return
        stamp = time.monotonic_ns()
        local = sys._getframe(1).f_locals
        index = local["index"]
        try:
            if point == "opened":
                self.active[index] = self.opened(local.get("tracked"), local["request"])
                return
            row = self.active.get(index)
            if row is None:
                return
            row["stock_close_calls"][point] = stamp
            if point == "close_block_end":
                entry = row["stock_close_calls"].get("shutdown_start")
                if row["case_id"] == "lif04-slow-close" and entry is not None:
                    row["client_explicit_close_interval"] = interval("client_explicit_close_block", entry, stamp)
                self.save(row)
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            # Supplemental collection cannot alter the stock request result.
            return

    def on_return(self, _code, _offset, _value):
        stamp = time.monotonic_ns()
        owner = sys._getframe(1).f_locals.get("self")
        row = self.match_socket(owner)
        if row is not None and "fd_close_enter_monotonic_ns" in row and owner.fileno() < 0:
            row["client_fd_close_interval"] = interval("client_fd_close_call", row["fd_close_enter_monotonic_ns"], stamp)
            try:
                self.save(row)
            except (KeyError, OSError, TypeError, ValueError):
                return


def install():
    args = sys.orig_argv
    if len(args) != 4 or args[1] != "-c" or FILENAME not in args[2]:
        return
    config = json.loads((ASSETS / "session.json").read_bytes())
    if config["probe_pod_uid"] != os.environ["BYUL_OWNED_STREAM_POD_UID"]:
        raise ValueError("client probe differs from the selected Pod")
    expected = config["reviewed_probe_source_sha256"]
    source = stock_source(args[2], expected)
    ProbeObserver(config, expected, reviewed_lines(source)).install()
