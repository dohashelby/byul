"""Run the test-only fake engine on the lab pod network; never use in production."""

from __future__ import annotations

import signal
import threading

from serving_runtime.mvp.fake_backend import FakeBackend


def main() -> int:
    """Serve the repository's fake backend on ``0.0.0.0:8000`` for lab tests."""

    stopping = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stopping.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    backend = FakeBackend(host="0.0.0.0", port=8000)
    print(
        "TEST ONLY: starting FakeBackend on 0.0.0.0:8000; "
        "this is not a production inference engine.",
        flush=True,
    )
    backend.start()
    try:
        stopping.wait()
    finally:
        backend.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

