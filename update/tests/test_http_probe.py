"""http_probe 분류: 상태코드 | refused | timeout | unreachable | dns_error | error (루프백만 사용)."""

import errno
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from guard.executors.http_probe import _classify_connect_error, probe


@pytest.mark.parametrize(
    "exc, expected",
    [
        (ConnectionRefusedError(errno.ECONNREFUSED, "refused"), "refused"),
        (socket.timeout("timed out"), "timeout"),
        (TimeoutError("timed out"), "timeout"),
        (OSError(errno.EHOSTUNREACH, "No route to host"), "unreachable"),
        (OSError(errno.ENETUNREACH, "Network is unreachable"), "unreachable"),
        (socket.gaierror(-2, "Name or service not known"), "dns_error"),
        (ConnectionResetError(errno.ECONNRESET, "reset"), "error"),
        (OSError(errno.EACCES, "denied"), "error"),
    ],
)
def test_connect_error_classification(exc, expected):
    assert _classify_connect_error(exc) == expected


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 알려진 제한: 닫힌 루프백 포트가 연결 거부 대신 timeout으로 보고됨 (지원 플랫폼 macOS·Linux, DECISIONS 2026-10-19)")
def test_refused_on_closed_port():
    assert probe({"method": "GET", "url": f"http://127.0.0.1:{_free_port()}/x"}, 1.0)["outcome"] == "refused"


def test_accepted_but_silent_is_error_not_timeout():
    """연결은 됐는데 응답이 없으면 대상 포트에 도달한 것이므로 차단 근거(timeout)가 아니다."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        out = probe({"method": "GET", "url": f"http://127.0.0.1:{srv.getsockname()[1]}/x"}, 0.3)
        assert out["outcome"] == "error" and out["phase"] == "after_connect"
    finally:
        srv.close()


def test_http_status_and_body():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(401 if "Authorization" not in self.headers else 200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/v1/models"
        assert probe({"method": "GET", "url": url}, 2)["outcome"] == 401
        out = probe({"method": "GET", "url": url, "headers": {"Authorization": "Bearer k"}, "want_body": True}, 2)
        assert out["outcome"] == 200 and out["body"] == '{"ok": true}'
        assert "headers" not in out  # 요청 헤더(키)는 결과에 남기지 않는다
    finally:
        httpd.shutdown()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 알려진 제한: 닫힌 루프백 포트가 연결 거부 대신 timeout으로 보고됨 (지원 플랫폼 macOS·Linux, DECISIONS 2026-10-19)")
def test_tcp_probe_refused_and_connected_without_sending_bytes():
    from guard.executors.http_probe import tcp_probe

    assert probe({"mode": "tcp", "url": f"http://127.0.0.1:{_free_port()}"}, 1.0)["outcome"] == "refused"
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    try:
        out = tcp_probe({"url": f"http://127.0.0.1:{srv.getsockname()[1]}"}, 1.0)
        assert out["outcome"] == "connected"
        conn, _ = srv.accept()
        conn.settimeout(0.5)
        assert conn.recv(10) == b""  # 바이트를 보내지 않고 닫았다
        conn.close()
    finally:
        srv.close()
