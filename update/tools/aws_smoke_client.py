"""Bootstrap-only normal/SSE validation. Emit booleans, never response content.

Copied into an owned Python Job by prepare_aws_smoke.py. No guard dependency,
credentials, model installation or checklist verdicts. All bodies stay in RAM.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import sys
import threading

MAX_BODY = 1024 * 1024
SCHEMA = "byul-bootstrap-response/1"


class SmokeError(Exception):
    pass


def validate_normal(raw):
    try:
        value = json.loads(raw.decode("utf-8"))
        choice = value["choices"][0]
        if (value.get("error") or value["model"] != "test-model"
                or type(choice["text"]) is not str or not choice["text"]
                or not choice["finish_reason"] or type(value["usage"]["completion_tokens"]) is not int
                or value["usage"]["completion_tokens"] <= 0):
            raise ValueError
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise SmokeError("NORMAL_SCHEMA_UNVERIFIED") from None
    return {"model_verified": True, "text_nonempty": True, "finish_seen": True, "positive_completion_usage": True}


def validate_stream(raw):
    try:
        text = raw.decode("utf-8")
        messages = [line.removeprefix("data: ").strip() for line in text.splitlines() if line.startswith("data: ")]
        if not messages or len(messages) > 4096 or messages[-1] != "[DONE]":
            raise ValueError
        text_seen = finished = False
        for message in messages[:-1]:
            item = json.loads(message)
            if item.get("error") or item["model"] != "test-model":
                raise ValueError
            for choice in item.get("choices", []):
                text_seen |= bool(choice.get("text"))
                finished |= choice.get("finish_reason") is not None
        if not text_seen or not finished:
            raise ValueError
    except (ValueError, TypeError, KeyError, AttributeError):
        raise SmokeError("SSE_SCHEMA_UNVERIFIED") from None
    return {"all_models_verified": True, "text_seen": True, "finish_seen": True, "done_last": True,
            "data_frames": len(messages) - 1}


def request(ip, path, body, deadline):
    # Own-process termination also bounds a drip-fed body and stdin/IO stalls.
    timer = threading.Timer(deadline, lambda: os._exit(124))
    timer.daemon = True
    timer.start()
    connection = http.client.HTTPConnection(ip, 8000, timeout=min(5, deadline))
    try:
        connection.request("GET" if body is None else "POST", path,
                           body=None if body is None else json.dumps(body),
                           headers={} if body is None else {"Content-Type": "application/json"})
        response = connection.getresponse()
        raw = response.read(MAX_BODY + 1)
        if response.status != 200 or len(raw) > MAX_BODY:
            raise SmokeError("HTTP_OR_BODY_UNVERIFIED")
        return raw
    except SmokeError:
        raise
    except Exception:
        raise SmokeError("HTTP_CAPTURE_UNAVAILABLE") from None
    finally:
        connection.close()
        timer.cancel()


def main(argv=None):
    try:
        ip = str(ipaddress.IPv4Address((argv or sys.argv[1:])[0]))
        request(ip, "/health", None, 30)
        body = {"model": "test-model", "prompt": "Say hello.", "max_tokens": 8}
        normal = validate_normal(request(ip, "/v1/completions", body, 120))
        stream = validate_stream(request(ip, "/v1/completions", {**body, "stream": True}, 120))
        value = {"schema": SCHEMA, "health_200": True, "normal": normal, "stream": stream}
        print(json.dumps(value, sort_keys=True))
        return 0
    except SmokeError as exc:
        print(json.dumps({"schema": SCHEMA, "complete": False, "diagnostic": str(exc)}))
        return 1
    except Exception:
        print(json.dumps({"schema": SCHEMA, "complete": False, "diagnostic": "SMOKE_CONFIGURATION_INVALID"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
