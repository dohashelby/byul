"""Timeout handling for backend HTTP requests and response sockets."""

import socket
import threading
import time
from typing import Any


class DeadlineWatchdog:
    """Interrupt a backend connection at an absolute request deadline."""

    def __init__(self, connection: Any, deadline_at: float) -> None:
        self.connection = connection
        self.deadline_at = deadline_at
        self.response: Any = None
        self._lock = threading.Lock()
        self._active = True
        self._expired = False
        self._timer = threading.Timer(max(0, deadline_at - time.monotonic()), self._interrupt)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def connect(self) -> None:
        """Bound DNS and TCP setup, when the connection exposes connect()."""

        connect = getattr(self.connection, "connect", None)
        if connect is None:
            return
        done = threading.Event()
        errors: list[BaseException] = []

        def establish() -> None:
            try:
                connect()
            except BaseException as error:
                errors.append(error)
            finally:
                if self.expired():
                    try:
                        self.connection.close()
                    except OSError:
                        pass
                done.set()

        worker = threading.Thread(target=establish, daemon=True)
        worker.start()
        if not done.wait(max(0, self.deadline_at - time.monotonic())):
            self._interrupt()
            raise TimeoutError("backend connection deadline exceeded")
        if self.expired():
            raise TimeoutError("backend connection deadline exceeded")
        if errors:
            raise errors[0]

    def watch_response(self, response: Any) -> None:
        with self._lock:
            self.response = response

    def expired(self) -> bool:
        return self._expired or time.monotonic() >= self.deadline_at

    def cancel(self) -> None:
        with self._lock:
            self._active = False
            self._timer.cancel()

    def finish(self) -> bool:
        """Atomically cancel the watchdog and report whether the deadline passed."""

        with self._lock:
            expired = self._expired or time.monotonic() >= self.deadline_at
            self._active = False
            self._timer.cancel()
            return expired

    def _interrupt(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._expired = True
            response = self.response
            connection = self.connection
            response_socket = getattr(
                getattr(getattr(response, "fp", None), "raw", None), "_sock", None
            )
            sockets = (getattr(connection, "sock", None), response_socket)
            unique_sockets: list[Any] = []
            for sock in sockets:
                if sock is not None and all(sock is not prior for prior in unique_sockets):
                    unique_sockets.append(sock)
            for item in unique_sockets:
                try:
                    item.shutdown(socket.SHUT_RDWR)
                except (OSError, AttributeError):
                    pass
                try:
                    item.close()
                except (OSError, AttributeError):
                    pass
            try:
                connection.close()
            except OSError:
                pass


def set_response_timeout(response_socket: Any, timeout: float) -> Any:
    """Keep the connection timeout when a detached response socket is closed."""

    if response_socket is None:
        return None
    try:
        response_socket.settimeout(max(0.001, timeout))
    except OSError:
        # HTTP/1.0 may leave a closed socket in fp.raw._sock after getresponse().
        # Buffered bytes remain readable under the connection's initial timeout.
        return None
    return response_socket
