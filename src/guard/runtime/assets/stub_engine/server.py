"""실험실 stub 엔진: 표준 라이브러리만. vLLM OpenAI 서버의 경로 모양만 흉내낸다(모델 없음, 인증 없음).

엔진 `--api-key`가 꺼진 상태를 재현한다. 추론 결과는 고정 문자열이다.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8000
MODEL = "lab-stub"
ROUTES = {
    "/health": "get",
    "/v1/models": "get",
    "/v1/completions": "post",
    "/invocations": "post",
}
OPENAPI = {
    "openapi": "3.1.0",
    "info": {"title": "lab-stub (vLLM route shape)", "version": "0.1.0"},
    "paths": {path: {method: {"responses": {"200": {"description": "ok"}}}} for path, method in ROUTES.items()},
}


def _completion() -> dict:
    return {
        "id": "cmpl-lab", "object": "text_completion", "created": int(time.time()), "model": MODEL,
        "choices": [{"index": 0, "text": " ok", "finish_reason": "length"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "lab-stub/0.1"

    def _send(self, status: int, body: dict | None = None) -> None:
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _drain(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200)
        elif self.path == "/v1/models":
            self._send(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})
        elif self.path == "/openapi.json":
            self._send(200, OPENAPI)
        else:
            self._send(404, {"detail": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        self._drain()
        if self.path in ("/v1/completions", "/invocations"):
            self._send(200, _completion())
        else:
            self._send(404, {"detail": "Not Found"})

    def log_message(self, fmt: str, *args) -> None:
        # 요청 줄만 남긴다(헤더는 남기지 않음)
        print(f'{self.address_string()} "{self.requestline}" {args[1] if len(args) > 1 else ""}', flush=True)


if __name__ == "__main__":
    print(f"lab-stub listening on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
