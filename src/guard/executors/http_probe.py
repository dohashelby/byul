"""표준 라이브러리만 쓰는 HTTP 요청기. 결과를 상태코드 | refused | timeout | unreachable | dns_error | error로 분류한다.

guard 패키지 밖(probe 컨테이너 안)에서도 단독 실행된다:
    python http_probe.py < '{"timeout": 3, "requests": [{"id": "...", "method": "GET", "url": "...", "headers": {...}}]}'
결과는 stdout에 JSON 목록. 요청 목록(키 포함)은 stdin으로만 받는다(명령 인자·환경에 남기지 않음).

분류 기준
- 연결 단계 ECONNREFUSED → refused
- 연결 단계 시간 초과 → timeout
- 연결 단계 ENETUNREACH/EHOSTUNREACH → unreachable
- 이름 해석 실패 → dns_error
- 연결은 됐는데 응답 전에 끊김·시간 초과 등 → error (대상 포트에 도달했으므로 차단 근거가 아니다)
- HTTP 응답 → 상태코드(int)
"""

from __future__ import annotations

import errno
import http.client
import json
import socket
import sys
import time
from urllib.parse import urlsplit

UNREACHABLE_ERRNOS = {errno.ENETUNREACH, errno.EHOSTUNREACH}
PREVIEW = 160
MAX_BODY = 1_000_000


def _classify_connect_error(exc: BaseException) -> str:
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, OSError) and exc.errno in UNREACHABLE_ERRNOS:
        return "unreachable"
    return "error"


def probe(req: dict, timeout: float) -> dict:
    """요청 1개. 반환: {id, outcome, phase, detail, elapsed_ms, body_bytes, body_preview}."""
    started = time.monotonic()
    out: dict = {"id": req.get("id"), "method": req.get("method", "GET"), "url": req["url"]}
    parts = urlsplit(req["url"])
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    body = req.get("body")
    data = json.dumps(body).encode() if body is not None else None
    headers = dict(req.get("headers") or {})
    if data is not None:
        headers.setdefault("Content-Type", "application/json")
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        try:
            conn.connect()
        except BaseException as exc:  # noqa: BLE001 - 분류가 목적
            out.update(outcome=_classify_connect_error(exc), phase="connect", detail=f"{type(exc).__name__}: {exc}")
            return out
        try:
            conn.request(out["method"], path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(MAX_BODY if req.get("want_body") else 4096)
            out.update(outcome=resp.status, phase="response", detail=resp.reason,
                       body_bytes=len(raw), body_preview=raw[:PREVIEW].decode("utf-8", "replace"))
            if req.get("want_body"):
                out["body"] = raw.decode("utf-8", "replace")
        except BaseException as exc:  # noqa: BLE001
            out.update(outcome="error", phase="after_connect", detail=f"{type(exc).__name__}: {exc}")
        return out
    finally:
        conn.close()
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)


def main() -> None:
    spec = json.loads(sys.stdin.read())
    timeout = float(spec.get("timeout", 3))
    results = [probe(r, timeout) for r in spec["requests"]]
    sys.stdout.write(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
