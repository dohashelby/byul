"""Socket-free tests for backend response reads and deadlines."""

import time
import threading
from types import SimpleNamespace
import unittest

from serving_runtime.mvp.engine_client import EngineClient, RequestDeadlineExceeded


class ClosedSocket:
    def __init__(self):
        self.timeout_attempts = 0

    def settimeout(self, _timeout):
        self.timeout_attempts += 1
        raise OSError(9, "Bad file descriptor")


class Response:
    status = 200

    def __init__(self, chunks, socket):
        self.chunks = iter(chunks)
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=socket))

    def getheaders(self):
        return [("Content-Type", "application/json")]

    def read1(self, _size):
        chunk = next(self.chunks, b"")
        return chunk() if callable(chunk) else chunk


class Connection:
    sock = None

    def __init__(self, response):
        self.response = response
        self.closed = False

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class HeaderSocket:
    def __init__(self):
        self.closed = threading.Event()

    def shutdown(self, _how):
        self.closed.set()

    def settimeout(self, _timeout):
        pass

    def close(self):
        self.closed.set()


class DrippingHeaderConnection(Connection):
    def __init__(self):
        super().__init__(None)
        self.sock = HeaderSocket()
        self.parts = 0

    def getresponse(self):
        while not self.sock.closed.wait(0.005):
            self.parts += 1
        raise OSError("header socket closed")


class SlowConnectConnection(Connection):
    def __init__(self):
        super().__init__(None)
        self.sock = None
        self.finished = threading.Event()

    def connect(self):
        time.sleep(0.07)
        self.sock = HeaderSocket()
        self.finished.set()


class Backend:
    def __init__(self, connection):
        self.connection_object = connection

    def connection(self, path, _timeout):
        return self.connection_object, path

    def request_headers(self, **_kwargs):
        return {}


class EngineClientDeadlineTests(unittest.TestCase):
    def test_shared_deadline_interrupts_dripping_headers(self):
        connection = DrippingHeaderConnection()
        deadline_at = time.monotonic() + 0.03
        with self.assertRaises(RequestDeadlineExceeded):
            EngineClient(Backend(connection), 0.5).request(
                "GET", "/health", deadline_at=deadline_at)
        self.assertGreater(connection.parts, 0)
        self.assertTrue(connection.sock.closed.is_set())

    def test_shared_deadline_bounds_connection_setup_without_socket_yet(self):
        connection = SlowConnectConnection()
        started = time.monotonic()
        with self.assertRaises(RequestDeadlineExceeded):
            EngineClient(Backend(connection), 0.5).request(
                "GET", "/health", deadline_at=started + 0.02)
        self.assertLess(time.monotonic() - started, 0.06)
        self.assertTrue(connection.finished.wait(0.2))
        self.assertTrue(connection.closed)

    def test_watchdog_is_cancelled_after_success(self):
        socket = HeaderSocket()
        connection = Connection(Response([b"ok", b""], socket))
        connection.sock = socket
        status, _, body = EngineClient(Backend(connection), 0.03).request(
            "GET", "/health")
        time.sleep(0.05)
        self.assertEqual((status, body), (200, b"ok"))
        self.assertFalse(socket.closed.is_set())

    def test_shared_deadline_interrupts_blocked_response_body(self):
        socket = HeaderSocket()

        def blocked_chunk():
            if not socket.closed.wait(0.2):
                raise AssertionError("watchdog did not close the body socket")
            raise OSError("body socket closed")

        connection = Connection(Response([blocked_chunk], socket))
        connection.sock = socket
        with self.assertRaises(RequestDeadlineExceeded):
            EngineClient(Backend(connection), 0.5).request(
                "GET", "/health", deadline_at=time.monotonic() + 0.03)
        self.assertTrue(socket.closed.is_set())

    def test_closed_response_socket_does_not_hide_buffered_body(self):
        socket = ClosedSocket()
        connection = Connection(Response([b'{"status":', b'"ok"}', b""], socket))

        status, headers, body = EngineClient(Backend(connection), 1).request("GET", "/health")

        self.assertEqual((status, headers["Content-Type"], body),
                         (200, "application/json", b'{"status":"ok"}'))
        self.assertEqual(socket.timeout_attempts, 1)
        self.assertTrue(connection.closed)

    def test_deadline_after_read_is_enforced_with_closed_socket(self):
        for shared_deadline in (False, True):
            with self.subTest(shared_deadline=shared_deadline):
                socket = ClosedSocket()

                def slow_chunk():
                    time.sleep(0.03)
                    return b'{"status":"ok"}'

                connection = Connection(Response([slow_chunk], socket))
                client = EngineClient(Backend(connection), 1 if shared_deadline else 0.01)
                deadline_at = time.monotonic() + 0.01 if shared_deadline else None
                error_type = RequestDeadlineExceeded if shared_deadline else TimeoutError

                with self.assertRaises(error_type):
                    client.request("GET", "/health", deadline_at=deadline_at)
                self.assertEqual(socket.timeout_attempts, 1)
                self.assertTrue(connection.closed)


if __name__ == "__main__":
    unittest.main()
