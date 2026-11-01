"""소유 컨테이너 안에서 고정 읽기 전용 helper를 실행하고 JSON 한 개만 받는다."""
from __future__ import annotations

from .owned_capture import strict_json


def run_helper(docker, container, source, args, *, timeout, max_bytes=262144):
    result = docker.exec_bounded(container, ["python3", "-c", source, *map(str, args)], timeout=timeout, max_bytes=max_bytes)
    capture = getattr(result, "capture", None)
    if (result.returncode != 0 or type(capture) is not dict or capture.get("complete") is not True
            or capture.get("truncated") is not False or capture.get("timed_out") is not False):
        raise ValueError("OWNED_HELPER_INCOMPLETE")
    raw = getattr(result, "stdout_bytes", None)
    return strict_json(raw if type(raw) is bytes else result.stdout.encode("utf-8"))
