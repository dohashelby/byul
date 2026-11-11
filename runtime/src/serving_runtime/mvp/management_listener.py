"""Read-only, path-scoped HTTP adapter for engine management endpoints."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import sys
import threading
import re
from datetime import datetime, timezone
from uuid import uuid4
from typing import TextIO

from .backend import URLTarget, _parse_origin, _read_credential
from .store import JsonlStore


DEFAULT_ALLOWED_PATHS = ("/metrics", "/health", "/v1/models", "/version")
ACCESS_SCHEMA = "runtime-management-access/1.0"
_MAX_RECORDED_PATH = 2048


def _validate_allowed_path(path: str) -> str:
    """Require one canonical origin-form path; request matching stays byte-exact."""

    if (
        not path.startswith("/")
        or path.startswith("//")
        or path != path.strip()
        or "?" in path
        or "#" in path
        or "%" in path
        or "\\" in path
        or "//" in path
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in path)
    ):
        raise ValueError(f"invalid allowed path: {path!r}")
    segments = path.split("/")[1:]
    if any(segment in {".", ".."} for segment in segments):
        raise ValueError(f"invalid allowed path: {path!r}")
    return path


def _load_credential_file(path: str | Path) -> tuple[str, str]:
    """Load a bearer credential with the backend config's file semantics."""

    credential_path = Path(path).expanduser()
    configured, status, authorization = _read_credential(
        {"file": str(credential_path)},
        config_dir=Path.cwd(),
        environ={},
    )
    if not configured or authorization is None:
        raise ValueError(f"engine credential file is {status}")
    return status, authorization


@dataclass(frozen=True)
class ManagementListenerConfig:
    """Validated immutable settings for one management listener."""

    listen_host: str
    listen_port: int
    engine_target: URLTarget
    allowed_paths: frozenset[str]
    timeout_s: float
    max_response_bytes: int
    authorization: str | None = field(default=None, repr=False, compare=False)
    credential_status: str = "not_configured"
    record_path: Path | None = None
    heartbeat_path: Path | None = None
    target_ref: str | None = None
    run_id: str | None = None
    environment: str | None = None
    policy_ref: str | None = None

    @classmethod
    def build(
        cls,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 8081,
        engine_url: str,
        allowed_paths: tuple[str, ...] | list[str] = DEFAULT_ALLOWED_PATHS,
        timeout_s: float = 5.0,
        max_response_bytes: int = 1024 * 1024,
        credential_file: str | Path | None = None,
        record_path: str | Path | None = None,
        heartbeat_path: str | Path | None = None,
        target_ref: str | None = None,
        run_id: str | None = None,
        environment: str | None = None,
        policy_ref: str | None = None,
    ) -> "ManagementListenerConfig":
        if not isinstance(listen_host, str) or not listen_host.strip():
            raise ValueError("listen host must be non-empty")
        if not isinstance(listen_port, int) or isinstance(listen_port, bool) or not 0 <= listen_port <= 65535:
            raise ValueError("listen port must be between 0 and 65535")
        if (
            not isinstance(timeout_s, (int, float))
            or isinstance(timeout_s, bool)
            or not math.isfinite(float(timeout_s))
            or float(timeout_s) <= 0
        ):
            raise ValueError("timeout must be a positive finite number")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or max_response_bytes <= 0
        ):
            raise ValueError("max response bytes must be a positive integer")
        validated_paths = frozenset(_validate_allowed_path(path) for path in allowed_paths)
        if not validated_paths:
            raise ValueError("at least one allowed path is required")
        target = _parse_origin(engine_url, field_name="engine_url")
        if target.path_prefix:
            _validate_allowed_path(target.path_prefix)
        authorization = None
        credential_status = "not_configured"
        if credential_file is not None:
            credential_status, authorization = _load_credential_file(credential_file)
        for label, value in (("target_ref", target_ref), ("run_id", run_id), ("environment", environment), ("policy_ref", policy_ref)):
            if value is not None and (not isinstance(value, str) or re.fullmatch(r"[\w.:-]{1,128}", value) is None):
                raise ValueError(f"{label} must be a safe configured reference")
        if heartbeat_path is not None and (record_path is None or Path(heartbeat_path).expanduser().absolute() == Path(record_path).expanduser().absolute()):
            raise ValueError("heartbeat requires a distinct record file")
        return cls(
            heartbeat_path=None if heartbeat_path is None else Path(heartbeat_path).expanduser(),
            target_ref=target_ref, run_id=run_id, environment=environment, policy_ref=policy_ref,
            listen_host=listen_host,
            listen_port=listen_port,
            engine_target=target,
            allowed_paths=validated_paths,
            timeout_s=float(timeout_s),
            max_response_bytes=max_response_bytes,
            authorization=authorization,
            credential_status=credential_status,
            record_path=None if record_path is None else Path(record_path).expanduser(),
        )


class AccessRecorder:
    """Write minimal cumulative access records without request/response contents."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        stream: TextIO | None = None,
        scope: dict | None = None,
        heartbeat_path: Path | None = None,
    ) -> None:
        self._heartbeat_path = heartbeat_path
        self._feed_path = path
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = None
        self._lock = threading.Lock()
        self._allowed_count = 0
        self._denied_count = 0
        self._stream = stream
        self._scope = dict(scope or {})
        self._write_failure_count = 0
        self._write_failed = False
        self._last_write_error_class = None
        self._last_write_error_at = None
        self._store = None
        if path is not None:
            store = JsonlStore(path)
            store.check_writable()
            self._store = store

    def start_heartbeat(self) -> None:
        if self._heartbeat_path is None:
            return
        self._write_heartbeat("running")
        def pulse():
            while not self._heartbeat_stop.wait(1.0):
                self._write_heartbeat("running")
        self._heartbeat_thread = threading.Thread(target=pulse, daemon=True)
        self._heartbeat_thread.start()

    def _write_heartbeat(self, state: str) -> None:
        if self._heartbeat_path is None:
            return
        status = self.status()
        document = {"schema":"runtime-observation-feed-heartbeat/1.0", "kind":"management_listener",
            "feed_path":str(self._feed_path.absolute()), "started_at":self._started_at,
            "updated_at":datetime.now(timezone.utc).isoformat(), "state":state,
            "processes":[{"name":"management_listener", "running":state=="running", "returncode":None}],
            "recording":status}
        temporary = self._heartbeat_path.with_name("."+self._heartbeat_path.name+"."+uuid4().hex+".tmp")
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            with os.fdopen(fd,"w") as output:
                output.write(json.dumps(document)+"\n");output.flush();os.fsync(output.fileno())
            os.replace(temporary,self._heartbeat_path)
        except OSError:
            # A missing/stale heartbeat exposes a writer failure to its collector.
            pass
        finally:
            try:temporary.unlink()
            except FileNotFoundError:pass

    def record(self, *, allowed: bool, path: str, status_code: int, method: str = "UNKNOWN") -> None:
        safe_path = re.sub(r"[\x00-\x1f\x7f]", "?", path.split("?", 1)[0].split("#", 1)[0])
        # Absolute-form probes can carry userinfo; keep the path spelling while
        # excluding credentials in their authority.
        if "://" in safe_path:
            scheme, suffix = safe_path.split("://", 1)
            authority, slash, tail = suffix.partition("/")
            safe_path = scheme + "://" + authority.rsplit("@", 1)[-1] + slash + tail
        safe_path = safe_path[:_MAX_RECORDED_PATH]
        safe_method = method if re.fullmatch(r"[A-Z]{1,32}", method) else "OTHER"
        with self._lock:
            if allowed:
                self._allowed_count += 1
            else:
                self._denied_count += 1
            record = {
                "schema": ACCESS_SCHEMA,
                "event_id": uuid4().hex,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "method": safe_method,
                "scope": self._scope,
                "actor": {"state": "unattributed", "principal_id": None},
                "recording": {"write_failure_count": self._write_failure_count, "last_write_error_class": self._last_write_error_class},
                "allowed_count": self._allowed_count,
                "denied_count": self._denied_count,
                "path": safe_path,
                "status_code": status_code,
                "allowed": allowed,
            }
            line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
            self._write_failed = False
            for sink in (self._store, self._stream):
                if sink is None:
                    continue
                try:
                    if sink is self._store:
                        sink.append(record)
                    else:
                        sink.write(line)
                        sink.flush()
                except OSError as error:
                    self._write_failed = True
                    self._write_failure_count += 1
                    self._last_write_error_class = type(error).__name__
                    self._last_write_error_at = datetime.now(timezone.utc).isoformat()

    def status(self) -> dict:
        with self._lock:
            return {"write_failed": self._write_failed, "allowed_count": self._allowed_count, "denied_count": self._denied_count,
                    "write_failure_count": self._write_failure_count,
                    "last_write_error_class": self._last_write_error_class,
                    "last_write_error_at": self._last_write_error_at}

    def close(self) -> None:
        self._heartbeat_stop.set()
        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=2)
        self._write_heartbeat("stopped")
        with self._lock:
            self._store = None


@dataclass(frozen=True)
class ProxyResponse:
    status: int
    content_type: str
    body: bytes


class EngineManagementProxy:
    """Make fixed-destination requests with no client-provided headers."""

    def __init__(self, config: ManagementListenerConfig) -> None:
        self.config = config

    def request(self, method: str, path: str) -> ProxyResponse:
        connection = self.config.engine_target.connection(self.config.timeout_s)
        headers = {"Accept-Encoding": "identity"}
        if self.config.authorization is not None:
            headers["Authorization"] = self.config.authorization
        try:
            connection.request(
                method,
                self.config.engine_target.path(path),
                headers=headers,
            )
            response = connection.getresponse()
            declared_length = response.getheader("Content-Length")
            if declared_length is not None:
                try:
                    parsed_length = int(declared_length, 10)
                except ValueError as error:
                    raise OSError("engine returned an invalid Content-Length") from error
                if parsed_length < 0 or parsed_length > self.config.max_response_bytes:
                    raise ResponseTooLargeError
            body = response.read(self.config.max_response_bytes + 1)
            if len(body) > self.config.max_response_bytes:
                raise ResponseTooLargeError
            content_type = response.getheader("Content-Type", "application/octet-stream")
            if (
                not content_type
                or len(content_type) > 256
                or any(ord(character) < 0x20 or ord(character) > 0x7E for character in content_type)
            ):
                content_type = "application/octet-stream"
            return ProxyResponse(response.status, content_type, body)
        finally:
            connection.close()


class ResponseTooLargeError(OSError):
    """The engine response exceeded the configured in-memory relay bound."""


class ManagementHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        config: ManagementListenerConfig,
        recorder: AccessRecorder,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.proxy = EngineManagementProxy(config)
        super().__init__((config.listen_host, config.listen_port), ManagementRequestHandler)


class ManagementRequestHandler(BaseHTTPRequestHandler):
    """Enforce method/body/path restrictions before contacting the engine."""

    server: ManagementHTTPServer
    protocol_version = "HTTP/1.1"

    def __getattr__(self, name: str):
        if name.startswith("do_"):
            return self._method_not_allowed
        raise AttributeError(name)

    def do_GET(self) -> None:
        self._read_only_request(head=False)

    def do_HEAD(self) -> None:
        self._read_only_request(head=True)

    def _method_not_allowed(self) -> None:
        self.close_connection = True
        self._reply(
            405,
            b'{"error":{"code":"METHOD_NOT_ALLOWED"}}',
            allowed=False,
            extra_headers={"Allow": "GET, HEAD"},
        )

    def _read_only_request(self, *, head: bool) -> None:
        request_target = self._request_target()
        if request_target not in self.server.config.allowed_paths:
            self.close_connection = True
            self._reply(
                404,
                b'{"error":{"code":"PATH_NOT_ALLOWED"}}',
                allowed=False,
                head=head,
            )
            return
        if self._has_request_body():
            self.close_connection = True
            self._reply(
                400,
                b'{"error":{"code":"REQUEST_BODY_NOT_ALLOWED"}}',
                allowed=False,
                head=head,
            )
            return
        try:
            response = self.server.proxy.request(self.command, request_target)
            if head and response.status == 405:
                # Some engine read endpoints implement GET only. Obtain that
                # same bounded representation and suppress its body below.
                response = self.server.proxy.request("GET", request_target)
        except TimeoutError:
            self.close_connection = True
            self._reply(
                504,
                b'{"error":{"code":"ENGINE_TIMEOUT"}}',
                allowed=True,
                head=head,
            )
            return
        except ResponseTooLargeError:
            self.close_connection = True
            self._reply(
                502,
                b'{"error":{"code":"ENGINE_RESPONSE_TOO_LARGE"}}',
                allowed=True,
                head=head,
            )
            return
        except (OSError, HTTPException):
            self.close_connection = True
            self._reply(
                502,
                b'{"error":{"code":"ENGINE_CONNECTION_FAILED"}}',
                allowed=True,
                head=head,
            )
            return
        self._reply(
            response.status,
            response.body,
            allowed=True,
            head=head,
            content_type=response.content_type,
        )

    def _has_request_body(self) -> bool:
        if self.headers.get_all("Transfer-Encoding"):
            return True
        lengths = self.headers.get_all("Content-Length", [])
        return len(lengths) > 1 or (len(lengths) == 1 and lengths[0].strip() != "0")

    def _request_target(self) -> str:
        """Return the pre-normalization request-target from the request line.

        Python 3.12 deliberately folds a leading ``//`` in ``self.path`` to a
        single slash. The adapter's boundary is stricter, so authorization and
        recording use the original request-target instead.
        """

        parts = self.requestline.split()
        if len(parts) != 3 or parts[0] != self.command:
            return ""
        return parts[1]

    def _record_path(self) -> str:
        # Keep the original path spelling (including // or an absolute-form
        # target), but do not persist query/fragment contents.
        return self._request_target().split("?", 1)[0].split("#", 1)[0]

    def _reply(
        self,
        status: int,
        body: bytes,
        *,
        allowed: bool,
        head: bool = False,
        content_type: str = "application/json",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.server.recorder.record(
            allowed=allowed,
            path=self._record_path(),
            status_code=status,
            method=self.command,
        )
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        # AccessRecorder is the only access log. It deliberately excludes
        # request/response bodies and raw headers.
        return


def _configured_scope(config: ManagementListenerConfig) -> dict:
    return {"target_ref": config.target_ref, "run_id": config.run_id,
            "environment": config.environment, "policy_ref": config.policy_ref}


class ManagementListener:
    """Lifecycle helper used by tests and embedders."""

    def __init__(
        self,
        config: ManagementListenerConfig,
        *,
        record_stream: TextIO | None = None,
    ) -> None:
        self.config = config
        self.recorder = AccessRecorder(config.record_path, stream=record_stream, scope=_configured_scope(config), heartbeat_path=config.heartbeat_path)
        try:
            self.server = ManagementHTTPServer(config, self.recorder)
        except Exception:
            self.recorder.close()
            raise
        self.thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self.server.server_address[:2]
        return str(host), int(port)

    def start(self) -> "ManagementListener":
        self.recorder.start_heartbeat()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self) -> None:
        if self.thread is not None:
            self.server.shutdown()
        self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None
        self.recorder.close()


def run(config: ManagementListenerConfig) -> None:
    """Run the listener until interrupted; emit access JSONL to stdout."""

    recorder = AccessRecorder(config.record_path, stream=sys.stdout, scope=_configured_scope(config), heartbeat_path=config.heartbeat_path)
    try:
        server = ManagementHTTPServer(config, recorder)
    except Exception:
        recorder.close()
        raise
    recorder.start_heartbeat()
    host, port = server.server_address[:2]
    print(
        f"management listener: http://{host}:{port} -> {config.engine_target.display_url}",
        file=sys.stderr,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        recorder.close()
