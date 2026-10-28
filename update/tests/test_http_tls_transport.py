"""Owned loopback TLS and EXAMPLE transports; no engine or external service."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from guard.executors import http_probe

FIXTURES = Path(__file__).parent / "fixtures"
CERT = FIXTURES / "http-probe-localhost-cert.pem"
KEY = FIXTURES / "http-probe-example-key.pem"


@pytest.mark.parametrize("url,host,port,tls", [
    ("http://example.invalid/path", "example.invalid", 80, False),
    ("https://example.invalid/path", "example.invalid", 443, True),
    ("https://example.invalid:8443/path", "example.invalid", 8443, True),
    ("http://example.invalid:8080/path", "example.invalid", 8080, False),
    ("HTTPS://example.invalid/path", "example.invalid", 443, True),
    ("https://[::1]:9443/path", "::1", 9443, True),
])
@pytest.mark.parametrize("mode", ["http", "tcp"])
def test_scheme_and_explicit_port_select_transport_without_network(monkeypatch, url, host, port, tls, mode):
    calls = []

    class Connection:
        def connect(self):
            calls.append("connect")
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
        def getresponse(self):
            return SimpleNamespace(status=200, reason="OK", read=lambda limit: b"{}")
        def close(self):
            calls.append("closed")

    def constructor(is_tls):
        def create(actual_host, actual_port, *, timeout):
            calls.append((is_tls, actual_host, actual_port, timeout))
            return Connection()
        return create
    monkeypatch.setattr(http_probe.http.client, "HTTPConnection", constructor(False))
    monkeypatch.setattr(http_probe.http.client, "HTTPSConnection", constructor(True))
    monkeypatch.setattr(http_probe.socket, "create_connection", lambda address, *, timeout:
        (calls.append((address, timeout)) or SimpleNamespace(close=lambda: calls.append("closed"))))
    out = http_probe.probe({"id": "EXAMPLE", "url": url, "mode": mode}, 2)
    if mode == "tcp":
        assert calls == [((host, port), 2), "closed"]  # No request or TLS bytes.
        assert out["outcome"] == "connected"
    else:
        assert calls[0] == (tls, host, port, 2)
        assert calls[1:] == ["connect", ("GET", "/path", {"body": None, "headers": {}}), "closed"]
        assert out["outcome"] == 200


@pytest.mark.parametrize("url", ["ftp://example.invalid/", "not-a-url", "https://example.invalid:bad/", "https://example.invalid:0/"])
@pytest.mark.parametrize("mode", ["http", "tcp"])
def test_invalid_url_cannot_fall_back_to_plaintext(monkeypatch, url, mode):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid URL attempted network I/O")
    monkeypatch.setattr(http_probe.socket, "create_connection", forbidden)
    monkeypatch.setattr(http_probe.http.client, "HTTPConnection", forbidden)
    monkeypatch.setattr(http_probe.http.client, "HTTPSConnection", forbidden)
    out = http_probe.probe({"url": url, "mode": mode}, 1)
    assert out["outcome"] == "error" and out["phase"] == "connect"
    assert out["elapsed_ms"] >= 0


@pytest.fixture
def tls_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"EXAMPLE":true}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT, KEY)  # Disposable test-only certificate/key.
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        assert not thread.is_alive()


def trust_test_ca(monkeypatch):
    original = http.client.HTTPSConnection.__init__
    context = ssl.create_default_context(cafile=str(CERT))
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs, context=context)
    monkeypatch.setattr(http.client.HTTPSConnection, "__init__", initialize)


def test_verified_tls_preserves_path_status_and_keeps_key_out_of_result(monkeypatch, tls_server):
    trust_test_ca(monkeypatch)
    port, requests = tls_server
    out = http_probe.probe({"url": f"https://localhost:{port}/v1/models?example=1", "want_body": True,
                            "headers": {"Authorization": "Bearer EXAMPLE_TLS_SENTINEL"}}, 3)
    assert out["outcome"] == 200 and out["phase"] == "response"
    assert out["body"] == '{"EXAMPLE":true}'
    assert requests == [("/v1/models?example=1", "Bearer EXAMPLE_TLS_SENTINEL")]
    assert "EXAMPLE_TLS_SENTINEL" not in json.dumps(out)
    assert "headers" not in out


@pytest.mark.parametrize("failure", ["untrusted", "hostname"])
def test_tls_verification_failure_never_sends_http_or_retries_plaintext(monkeypatch, tls_server, failure):
    if failure == "hostname":
        trust_test_ca(monkeypatch)
    port, requests = tls_server
    def no_plaintext(*args, **kwargs):
        pytest.fail("TLS verification failure attempted plaintext fallback")
    monkeypatch.setattr(http_probe.http.client, "HTTPConnection", no_plaintext)
    # The fixture certificate names localhost, not 127.0.0.1.
    host = "localhost" if failure == "untrusted" else "127.0.0.1"
    out = http_probe.probe({"url": f"https://{host}:{port}/v1/models",
                            "headers": {"Authorization": "Bearer EXAMPLE_TLS_SENTINEL"}}, 3)
    assert out["outcome"] == "error" and out["phase"] == "after_connect"
    assert "SSLCertVerificationError" in out["detail"]
    assert "EXAMPLE_TLS_SENTINEL" not in json.dumps(out)
    assert requests == []


def test_tls_handshake_timeout_is_not_a_tcp_blocking_outcome():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)  # TCP accepts, but this owned socket never replies to TLS.
    try:
        out = http_probe.probe({"url": f"https://127.0.0.1:{server.getsockname()[1]}/v1/models"}, 0.3)
        assert out["outcome"] == "error" and out["phase"] == "after_connect"
        assert "TimeoutError" in out["detail"]
    finally:
        server.close()


@pytest.mark.parametrize("exception,expected", [(TimeoutError("EXAMPLE TCP timeout"), "timeout"),
                                               (ConnectionRefusedError("EXAMPLE TCP refused"), "refused")])
def test_https_tcp_failure_keeps_original_network_classification(monkeypatch, exception, expected):
    closed = []
    def connect():
        raise exception
    monkeypatch.setattr(http_probe.http.client, "HTTPSConnection", lambda *args, **kwargs:
        SimpleNamespace(sock=None, connect=connect, close=lambda: closed.append(True)))
    out = http_probe.probe({"url": "https://example.invalid/v1/models"}, 1)
    assert out["outcome"] == expected and out["phase"] == "connect"
    assert closed == [True]


def test_standalone_probe_uses_tls_outside_guard_checkout(tmp_path, tls_server):
    port, requests = tls_server
    script = tmp_path / "http_probe.py"
    script.write_bytes(Path(http_probe.__file__).read_bytes())
    spec = {"requests": [{"id": "EXAMPLE", "url": f"https://localhost:{port}/v1/models"}], "timeout": 3}
    child = subprocess.run([sys.executable, "-I", str(script)], cwd=tmp_path,
        input=json.dumps(spec), capture_output=True, text=True,
        env={**os.environ, "SSL_CERT_FILE": str(CERT.resolve())}, check=True)
    (out,) = json.loads(child.stdout)
    assert out["id"] == "EXAMPLE" and out["outcome"] == 200
    assert requests == [("/v1/models", None)]
    assert not child.stderr


def test_product_records_transport_profile_in_comparison_conditions(tmp_path, monkeypatch):
    from tests.test_direct_route import World
    from tests.test_fixed_route_execution import run
    from guard.models import canonical_json, sha256_hex
    from guard.executors.http_common import HttpCheckExecutor
    configs = []
    finish = HttpCheckExecutor._finish
    def record_config(self, ctx):
        draft = finish(self, ctx)
        configs.append(draft.config)
        return draft
    monkeypatch.setattr(HttpCheckExecutor, "_finish", record_config)
    routes = {"/v1/models": "get", "/v1/completions": "post", "/invocations": "post"}
    evd, result, summary, submitted = run(tmp_path / "fixed", monkeypatch, "REQ-01",
        World(False, engine_routes=routes), "0.31.0", sorted(f"{method.upper()} {path}" for path, method in routes.items()))
    assert evd.execution.status.value == "COMPLETED", evd.execution.abort_reason
    assert len(configs) == 1
    assert configs[0]["settings"]["http_probe_profile"] == "http-probe/2"
    assert evd.comparability.config_sha256 == sha256_hex(canonical_json(configs[0]))
    assert evd.execution.executor.config_sha256 == evd.comparability.config_sha256
    assert submitted and result.verdicts.security.value == "PASS"


def test_http_only_kubernetes_transport_retains_its_source_and_profile(tmp_path):
    from tests.test_kubernetes_sampling import inputs, env, run
    from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP
    target = inputs()
    target["engine"]["trusted_url"] = target["engine"]["trusted_url"].replace("http:", "https:")
    calls = []
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP(target, env(), duration=1, runner=lambda *args, **kwargs: calls.append(args))
    assert calls == []
    draft, _ = run(tmp_path)
    assert draft.config["settings"]["http_probe_profile"] == "http-probe/1"
    assert draft.comparability.fixture_sha256 == "4703cf633155da142282657b355ec2205a7d34bb38e35c17fe1b84dad49f669f"
