#!/usr/bin/env python3
"""Run the fixed three-block Gateway performance comparison from plan v1.4.

The tool never changes a runtime mode.  B0, B1, and B2 must already be
available at the three Gateway URLs in the input plan.  Prompt and response
text is used in memory only and is not written to any output artifact.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import statistics
import time
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit
from uuid import uuid4


PLAN_SCHEMA = "runtime-perf-plan/1.0"
REPORT_SCHEMA = "runtime-perf-report/1.0"
RAW_SCHEMA = "runtime-perf-raw/1.0"
DEFAULT_WARMUP = 20
DEFAULT_NORMAL = 100
DEFAULT_STREAM = 100
BLOCK_ORDERS = (
    ("B0", "B1", "B2"),
    ("B1", "B2", "B0"),
    ("B2", "B0", "B1"),
)
_TOKEN = re.compile(r"[\x21-\x7e]{16,512}")


class PlanError(ValueError):
    """The comparison plan is malformed or unsafe to use."""


@dataclass(frozen=True)
class Mode:
    gateway_url: str
    description: str


@dataclass(frozen=True)
class PerfPlan:
    source_sha256: str
    modes: dict[str, Mode]
    model: str
    prompts: tuple[str, ...]
    prompt_bundle_sha256: str
    prompt_source: str
    max_tokens: int
    # None = not sent. The default Gateway profile (D-09) rejects temperature,
    # so it may only be set when every mode's policy allows the field.
    temperature: float | None
    n: int
    concurrency: int
    request_timeout_s: float


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PlanError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise PlanError(f"non-standard JSON constant is not allowed: {value}")


def _load_json(raw: bytes, description: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except UnicodeDecodeError as error:
        raise PlanError(f"{description} must be UTF-8") from error
    except (json.JSONDecodeError, RecursionError) as error:
        raise PlanError(f"{description} is not valid JSON: {error}") from error


def _positive_integer(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise PlanError(f"{field} must be a positive integer")
    return value


def _positive_number(value: object, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        raise PlanError(f"{field} must be a positive finite number")
    return float(value)


def _gateway_url(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlanError(f"{field} must be a non-empty URL")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as error:
        raise PlanError(f"{field} is not a valid URL") from error
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise PlanError(
            f"{field} must be an http(s) base URL without credentials, query, or fragment"
        )
    return value.rstrip("/")


def _prompts(value: object, field: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise PlanError(f"{field} must be a non-empty array of non-empty strings")
    return tuple(value)


def load_plan(path: str | Path) -> PerfPlan:
    """Load and strictly validate one runtime-perf-plan/1.0 document."""

    plan_path = Path(path)
    try:
        raw = plan_path.read_bytes()
    except OSError as error:
        raise PlanError(f"performance plan could not be read: {type(error).__name__}") from error
    document = _load_json(raw, "performance plan")
    if not isinstance(document, dict):
        raise PlanError("performance plan must be a JSON object")
    allowed = {
        "schema",
        "modes",
        "model",
        "prompts",
        "prompt_file",
        "max_tokens",
        "temperature",
        "n",
        "concurrency",
        "request_timeout_s",
    }
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise PlanError(f"unknown performance plan field(s): {', '.join(unknown)}")
    required = {"schema", "modes", "model", "max_tokens", "n"}
    missing = sorted(required - set(document))
    if missing:
        raise PlanError(f"missing performance plan field(s): {', '.join(missing)}")
    if document["schema"] != PLAN_SCHEMA:
        raise PlanError(f"schema must be {PLAN_SCHEMA}")

    raw_modes = document["modes"]
    if not isinstance(raw_modes, dict) or set(raw_modes) != {"B0", "B1", "B2"}:
        raise PlanError("modes must contain exactly B0, B1, and B2")
    modes: dict[str, Mode] = {}
    for name in ("B0", "B1", "B2"):
        item = raw_modes[name]
        if not isinstance(item, dict) or set(item) != {"gateway_url", "description"}:
            raise PlanError(f"modes.{name} must contain gateway_url and description")
        description = item["description"]
        if not isinstance(description, str) or not description.strip():
            raise PlanError(f"modes.{name}.description must be a non-empty string")
        modes[name] = Mode(
            _gateway_url(item["gateway_url"], f"modes.{name}.gateway_url"),
            description,
        )

    model = document["model"]
    if not isinstance(model, str) or not model.strip():
        raise PlanError("model must be a non-empty string")
    has_inline = "prompts" in document
    has_file = "prompt_file" in document
    if has_inline == has_file:
        raise PlanError("exactly one of prompts or prompt_file is required")
    if has_inline:
        prompts = _prompts(document["prompts"], "prompts")
        prompt_source = "inline"
    else:
        prompt_file = document["prompt_file"]
        if not isinstance(prompt_file, str) or not prompt_file:
            raise PlanError("prompt_file must be a non-empty path")
        prompt_path = Path(prompt_file)
        if not prompt_path.is_absolute():
            prompt_path = plan_path.parent / prompt_path
        try:
            prompt_raw = prompt_path.read_bytes()
        except OSError as error:
            raise PlanError(
                f"prompt_file could not be read: {type(error).__name__}"
            ) from error
        prompts = _prompts(_load_json(prompt_raw, "prompt_file"), "prompt_file")
        prompt_source = "file"

    temperature = document.get("temperature")
    if temperature is not None and (
        not isinstance(temperature, (int, float))
        or isinstance(temperature, bool)
        or not math.isfinite(float(temperature))
    ):
        raise PlanError("temperature must be a finite number or null")
    prompt_encoding = json.dumps(
        list(prompts), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return PerfPlan(
        source_sha256=hashlib.sha256(raw).hexdigest(),
        modes=modes,
        model=model,
        prompts=prompts,
        prompt_bundle_sha256=hashlib.sha256(prompt_encoding).hexdigest(),
        prompt_source=prompt_source,
        max_tokens=_positive_integer(document["max_tokens"], "max_tokens"),
        temperature=None if temperature is None else float(temperature),
        n=_positive_integer(document["n"], "n"),
        concurrency=_positive_integer(document.get("concurrency", 2), "concurrency"),
        request_timeout_s=_positive_number(
            document.get("request_timeout_s", 300), "request_timeout_s"
        ),
    )


def _read_inference_token(path: Path) -> str:
    try:
        information = path.stat()
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PlanError("token env file could not be read") from error
    if information.st_mode & 0o077:
        raise PlanError("token env file must not be accessible by group or others")
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key not in {
            "RUNTIME_INFERENCE_TOKEN",
            "RUNTIME_VIEWER_TOKEN",
        }:
            raise PlanError("token env file has an unsupported line")
        if key in values or _TOKEN.fullmatch(value) is None:
            raise PlanError("token env file has a duplicate key or invalid token")
        values[key] = value
    try:
        return values["RUNTIME_INFERENCE_TOKEN"]
    except KeyError as error:
        raise PlanError("token env file must contain RUNTIME_INFERENCE_TOKEN") from error


def _endpoint(base_url: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(base_url)
    scheme = parsed.scheme
    host = parsed.hostname or ""
    port = parsed.port or (443 if scheme == "https" else 80)
    path = parsed.path.rstrip("/") + "/v1/chat/completions"
    return scheme, host, port, path


def _usage_tokens(document: object) -> int | None:
    if not isinstance(document, dict):
        return None
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return None
    for field in ("completion_tokens", "output_tokens"):
        value = usage.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _error_code(document: object, status: int) -> str:
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            if isinstance(code, str) and code:
                return code[:128]
    if status == 401:
        return "AUTH_HTTP_401"
    if status == 403:
        return "ACCESS_HTTP_403"
    return f"HTTP_{status}"


def _decode_json(raw: bytes) -> object | None:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None


def _request_once(
    gateway_url: str,
    payload: dict[str, Any],
    *,
    stream: bool,
    timeout_s: float,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Issue one request and return measurements without returning body text."""

    scheme, host, port, path = _endpoint(gateway_url)
    connection_type = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(host, port, timeout=timeout_s)
    started = time.monotonic_ns()
    ttft_ms: float | None = None
    status: int | None = None
    request_id: str | None = None
    output_tokens: int | None = None
    error_code: str | None = None
    success = False
    finished_ns: int | None = None
    try:
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
        }
        if bearer_token is not None:
            headers["Authorization"] = f"Bearer {bearer_token}"
        connection.request(
            "POST",
            path,
            body=body,
            headers=headers,
        )
        response = connection.getresponse()
        status = response.status
        request_id = response.getheader("X-Request-ID")
        if not 200 <= status < 300:
            document = _decode_json(_read_body(response))
            finished_ns = time.monotonic_ns()
            error_code = _error_code(document, status)
        elif not stream:
            document = _decode_json(_read_body(response))
            finished_ns = time.monotonic_ns()
            if not isinstance(document, dict):
                error_code = "INVALID_JSON_RESPONSE"
            else:
                output_tokens = _usage_tokens(document)
                success = True
        else:
            saw_data = False
            saw_done = False
            while True:
                line = response.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped.startswith(b"data:"):
                    continue
                data = stripped[5:].strip()
                if data == b"[DONE]":
                    saw_done = True
                    finished_ns = time.monotonic_ns()
                    break
                if not data:
                    continue
                if not saw_data:
                    ttft_ms = (time.monotonic_ns() - started) / 1_000_000
                    saw_data = True
                document = _decode_json(data)
                tokens = _usage_tokens(document)
                if tokens is not None:
                    output_tokens = tokens
            if not saw_data:
                error_code = "STREAM_NO_DATA"
            elif not saw_done:
                error_code = "STREAM_INCOMPLETE"
            else:
                success = True
        # The Gateway releases its concurrency slot only after writing the final
        # record and closing the connection. Wait for that close (outside the
        # measured time) so the next request does not hit a 429 that the client's
        # own concurrency would not explain.
        _drain_to_close(response)
    except Exception as error:  # Preserve failed requests and continue the block.
        error_code = f"TRANSPORT_{type(error).__name__}"[:128]
    finally:
        end_ns = finished_ns if finished_ns is not None else time.monotonic_ns()
        e2e_ms = (end_ns - started) / 1_000_000
        connection.close()
    return {
        "success": success,
        "http_status": status,
        "request_id": request_id,
        "error_code": error_code,
        "e2e_ms": round(e2e_ms, 6),
        "ttft_ms": None if ttft_ms is None else round(ttft_ms, 6),
        "output_tokens": output_tokens,
    }


def _read_body(response: Any) -> bytes:
    """Read exactly the declared body without letting http.client close early."""

    length = response.getheader("Content-Length")
    if length is not None and length.isdigit():
        return response.fp.read(int(length))
    return response.fp.read()


def _drain_to_close(response: Any) -> None:
    # Only a close-delimited response (HTTP/1.0 or "Connection: close", as the
    # MVP Gateway sends) ends with a server close; a keep-alive server would
    # just hold the socket open until the timeout.
    fp = getattr(response, "fp", None)
    if fp is None or not getattr(response, "will_close", False):
        return
    while fp.read(65536):
        pass


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(fraction * len(ordered)) - 1)
    return ordered[index]


def _latency_stats(values: list[float]) -> dict[str, Any]:
    return {
        "denominator": len(values),
        "mean_ms": None if not values else round(statistics.fmean(values), 6),
        "p50_ms": None if not values else round(_percentile(values, 0.50) or 0.0, 6),
        "p95_ms": None if not values else round(_percentile(values, 0.95) or 0.0, 6),
    }


def _summarize_phase(
    rows: list[dict[str, Any]], elapsed_s: float, *, included: bool
) -> dict[str, Any]:
    successful = [row for row in rows if row["success"]]
    error_codes: dict[str, int] = {}
    http_statuses: dict[str, int] = {}
    for row in rows:
        if row["http_status"] is not None:
            key = str(row["http_status"])
            http_statuses[key] = http_statuses.get(key, 0) + 1
        if not row["success"]:
            key = row["error_code"] or "UNKNOWN_ERROR"
            error_codes[key] = error_codes.get(key, 0) + 1
    token_values = [
        row["output_tokens"]
        for row in successful
        if isinstance(row["output_tokens"], int)
    ]
    return {
        "included_in_metrics": included,
        "attempted": len(rows),
        "successful": len(successful),
        "errors": len(rows) - len(successful),
        "error_codes": dict(sorted(error_codes.items())),
        "http_statuses": dict(sorted(http_statuses.items())),
        "wall_duration_s": round(elapsed_s, 6),
        "completed_requests_per_second": round(len(successful) / elapsed_s, 6)
        if elapsed_s > 0
        else None,
        "e2e": _latency_stats([float(row["e2e_ms"]) for row in successful]),
        "ttft": _latency_stats(
            [float(row["ttft_ms"]) for row in successful if row["ttft_ms"] is not None]
        ),
        "output_tokens": {
            "denominator": len(token_values),
            "total": sum(token_values),
            "mean": None if not token_values else round(statistics.fmean(token_values), 6),
        },
    }


RequestFunction = Callable[..., dict[str, Any]]


def _run_phase(
    plan: PerfPlan,
    mode_name: str,
    phase: str,
    count: int,
    *,
    block_index: int,
    run_index: int,
    request_function: RequestFunction,
    bearer_token: str | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    stream = phase == "stream"
    started = time.monotonic_ns()
    rows: list[dict[str, Any]] = []

    def issue(request_index: int) -> dict[str, Any]:
        prompt = plan.prompts[request_index % len(plan.prompts)]
        payload = {
            "model": plan.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": plan.max_tokens,
            "n": plan.n,
            "stream": stream,
        }
        if plan.temperature is not None:
            payload["temperature"] = plan.temperature
        request_kwargs = {
            "stream": stream,
            "timeout_s": plan.request_timeout_s,
        }
        if bearer_token is not None:
            request_kwargs["bearer_token"] = bearer_token
        measured = request_function(
            plan.modes[mode_name].gateway_url,
            payload,
            **request_kwargs,
        )
        return {
            "meter_type": "builtin",
            "block_index": block_index,
            "run_index": run_index,
            "mode": mode_name,
            "phase": phase,
            "request_index": request_index + 1,
            "included_in_metrics": phase != "warmup",
            **measured,
        }

    with ThreadPoolExecutor(max_workers=plan.concurrency) as executor:
        futures = [executor.submit(issue, index) for index in range(count)]
        for future in as_completed(futures):
            rows.append(future.result())
    elapsed_s = (time.monotonic_ns() - started) / 1_000_000_000
    rows.sort(key=lambda row: row["request_index"])
    return _summarize_phase(rows, elapsed_s, included=phase != "warmup"), rows


def _change(candidate: float | None, baseline: float | None, *, decrease: bool) -> float | None:
    if candidate is None or baseline is None or baseline <= 0:
        return None
    value = (1 - candidate / baseline) if decrease else (candidate / baseline - 1)
    return round(value * 100, 6)


def _phase_comparison(
    baseline: dict[str, Any], candidate: dict[str, Any], *, stream: bool
) -> dict[str, Any]:
    latency = {
        name: _change(candidate["e2e"][name], baseline["e2e"][name], decrease=False)
        for name in ("mean_ms", "p50_ms", "p95_ms")
    }
    result: dict[str, Any] = {
        "latency_increase_pct": latency,
        "throughput_decrease_pct": _change(
            candidate["completed_requests_per_second"],
            baseline["completed_requests_per_second"],
            decrease=True,
        ),
        "baseline_successful": baseline["successful"],
        "candidate_successful": candidate["successful"],
        "baseline_errors": baseline["errors"],
        "candidate_errors": candidate["errors"],
    }
    if stream:
        result["ttft_increase_pct"] = {
            name: _change(
                candidate["ttft"][name], baseline["ttft"][name], decrease=False
            )
            for name in ("mean_ms", "p50_ms", "p95_ms")
        }
    unavailable = []
    if any(value is None for value in latency.values()):
        unavailable.append("e2e_latency")
    if result["throughput_decrease_pct"] is None:
        unavailable.append("throughput")
    if stream and any(value is None for value in result["ttft_increase_pct"].values()):
        unavailable.append("ttft")
    result["status"] = "unavailable" if unavailable else "calculated"
    result["unavailable_metrics"] = unavailable
    return result


def build_block_comparisons(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate B1/B2 changes from the B0 run in the same block."""

    by_mode = {run["mode"]: run for run in runs}
    result: dict[str, Any] = {}
    for candidate_name in ("B1", "B2"):
        candidate = by_mode[candidate_name]
        baseline = by_mode["B0"]
        result[f"{candidate_name}_vs_B0"] = {
            "normal": _phase_comparison(
                baseline["phases"]["normal"],
                candidate["phases"]["normal"],
                stream=False,
            ),
            "stream": _phase_comparison(
                baseline["phases"]["stream"],
                candidate["phases"]["stream"],
                stream=True,
            ),
        }
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def execute_plan(
    plan: PerfPlan,
    *,
    warmup_count: int = DEFAULT_WARMUP,
    normal_count: int = DEFAULT_NORMAL,
    stream_count: int = DEFAULT_STREAM,
    block_count: int = len(BLOCK_ORDERS),
    request_function: RequestFunction = _request_once,
    run_id: str | None = None,
    bearer_token: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute selected fixed blocks and return the report and raw table."""

    for name, value in (
        ("warmup_count", warmup_count),
        ("normal_count", normal_count),
        ("stream_count", stream_count),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if not isinstance(block_count, int) or isinstance(block_count, bool) or not 1 <= block_count <= 3:
        raise ValueError("block_count must be 1, 2, or 3")

    actual_run_id = run_id or (
        datetime.now(timezone.utc).strftime("perf-%Y%m%dT%H%M%S.%fZ-") + uuid4().hex[:8]
    )
    started_at = _utc_now()
    overall_started = time.monotonic_ns()
    blocks: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    run_index = 0
    for block_index, order in enumerate(BLOCK_ORDERS[:block_count], start=1):
        runs: list[dict[str, Any]] = []
        for mode_name in order:
            run_index += 1
            phases: dict[str, Any] = {}
            run_rows: list[dict[str, Any]] = []
            for phase, count in (
                ("warmup", warmup_count),
                ("normal", normal_count),
                ("stream", stream_count),
            ):
                summary, rows = _run_phase(
                    plan,
                    mode_name,
                    phase,
                    count,
                    block_index=block_index,
                    run_index=run_index,
                    request_function=request_function,
                    bearer_token=bearer_token,
                )
                phases[phase] = summary
                run_rows.extend(rows)
            error_total = sum(phase["errors"] for phase in phases.values())
            runs.append(
                {
                    "meter_type": "builtin",
                    "run_index": run_index,
                    "mode": mode_name,
                    "description": plan.modes[mode_name].description,
                    "gateway_url": plan.modes[mode_name].gateway_url,
                    "status": "ok" if error_total == 0 else "failed",
                    "error_total": error_total,
                    "phases": phases,
                }
            )
            raw_rows.extend(run_rows)
        blocks.append(
            {
                "block_index": block_index,
                "order": list(order),
                "status": "ok" if all(run["status"] == "ok" for run in runs) else "failed",
                "runs": runs,
                "comparisons": build_block_comparisons(runs),
            }
        )

    elapsed_s = (time.monotonic_ns() - overall_started) / 1_000_000_000
    budget_matches = (
        warmup_count == DEFAULT_WARMUP
        and normal_count == DEFAULT_NORMAL
        and stream_count == DEFAULT_STREAM
        and block_count == len(BLOCK_ORDERS)
    )
    report = {
        "schema": REPORT_SCHEMA,
        "run_id": actual_run_id,
        "started_at": started_at,
        "finished_at": _utc_now(),
        "wall_duration_s": round(elapsed_s, 6),
        "timing_basis": "client time.monotonic_ns; no cross-host clock subtraction",
        "measurement_type": "builtin",
        "plan": {
            "schema": PLAN_SCHEMA,
            "sha256": plan.source_sha256,
            "model": plan.model,
            "max_tokens": plan.max_tokens,
            "temperature": plan.temperature,
            "n": plan.n,
            "concurrency": plan.concurrency,
            "request_timeout_s": plan.request_timeout_s,
            "input_bundle": {
                "sha256": plan.prompt_bundle_sha256,
                "count": len(plan.prompts),
                "source": plan.prompt_source,
            },
            "modes": {
                name: {
                    "gateway_url": mode.gateway_url,
                    "description": mode.description,
                }
                for name, mode in plan.modes.items()
            },
        },
        "execution_budget": {
            "warmup_per_run": warmup_count,
            "normal_per_run": normal_count,
            "stream_per_run": stream_count,
            "blocks": block_count,
            "plan_budget": budget_matches,
            "label": "계획 예산" if budget_matches else "계획 예산 아님",
            "reference": {
                "warmup_per_run": DEFAULT_WARMUP,
                "normal_per_run": DEFAULT_NORMAL,
                "stream_per_run": DEFAULT_STREAM,
                "blocks": len(BLOCK_ORDERS),
            },
        },
        "comparison_formulas": {
            "latency_increase_pct": "(candidate / B0 - 1) * 100",
            "throughput_decrease_pct": "(1 - candidate / B0) * 100",
        },
        "blocks": blocks,
        "external_measurements": {
            "measurement_type": "external",
            "status": "not_attached",
            "results": [],
            "note": (
                "vLLM benchmark 결과를 나중에 별도 측정기 결과로 연결하는 자리. "
                "Gateway가 거부할 수 있는 확장 필드를 기본 옵션이 보낼 수 있어 이번 도구는 설치·호출하지 않음"
            ),
        },
        "privacy": {
            "prompt_text_stored": False,
            "response_text_stored": False,
            "input_representation": "SHA-256 and count only",
        },
    }
    raw_rows.sort(
        key=lambda row: (
            row["block_index"],
            row["run_index"],
            {"warmup": 0, "normal": 1, "stream": 2}[row["phase"]],
            row["request_index"],
        )
    )
    raw = {
        "schema": RAW_SCHEMA,
        "run_id": actual_run_id,
        "measurement_type": "builtin",
        "input_bundle": {
            "sha256": plan.prompt_bundle_sha256,
            "count": len(plan.prompts),
        },
        "prompt_text_stored": False,
        "response_text_stored": False,
        "rows": raw_rows,
    }
    return report, raw


def report_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Gateway 성능 비교 요약",
        "",
        f"- 실행 ID: `{report['run_id']}`",
        f"- 측정기: `{report['measurement_type']}`",
        f"- 실행 예산: **{report['execution_budget']['label']}**",
        "- B0도 엔진 직접 호출이 아니라 계획에 적은 같은 Gateway 경유 URL을 사용함",
        "- 실제 수치는 정식 GPU 환경에서 산출하며, 작은 실행 결과는 절차 확인용임",
        "- 프롬프트·응답 원문 미저장(입력은 SHA-256과 개수만 기록)",
        "",
    ]
    for block in report["blocks"]:
        lines.extend(
            [
                f"## 블록 {block['block_index']} — {' → '.join(block['order'])}",
                "",
                f"- 상태: **{block['status']}**",
                "",
                "| 모드 | 상태 | 일반 성공/오류 | 일반 E2E p95(ms) | 일반 완료 req/s | stream 성공/오류 | stream TTFT p95(ms) | stream E2E p95(ms) | stream 완료 req/s |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for run in block["runs"]:
            normal = run["phases"]["normal"]
            stream = run["phases"]["stream"]
            lines.append(
                "| "
                + " | ".join(
                    (
                        run["mode"],
                        run["status"],
                        f"{normal['successful']}/{normal['errors']}",
                        str(normal["e2e"]["p95_ms"]),
                        str(normal["completed_requests_per_second"]),
                        f"{stream['successful']}/{stream['errors']}",
                        str(stream["ttft"]["p95_ms"]),
                        str(stream["e2e"]["p95_ms"]),
                        str(stream["completed_requests_per_second"]),
                    )
                )
                + " |"
            )
        lines.extend(["", "비교율:", ""])
        for name, comparison in block["comparisons"].items():
            for phase in ("normal", "stream"):
                item = comparison[phase]
                lines.append(
                    f"- {name} {phase}: E2E p95 증가 "
                    f"`{item['latency_increase_pct']['p95_ms']}`%, 처리량 감소 "
                    f"`{item['throughput_decrease_pct']}`%, 상태 `{item['status']}`"
                )
        lines.append("")
    lines.extend(
        [
            "## 외부 측정기",
            "",
            "이번 실행에는 외부 결과가 붙지 않았습니다. vLLM benchmark는 Gateway가 거부할 수 있는 확장 필드의 실제 지원 여부를 확인한 뒤 `external` 측정기로 연결합니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _encoded_json(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(
        "utf-8"
    )


def write_outputs(
    report: dict[str, Any], raw: dict[str, Any], output_dir: str | Path
) -> dict[str, Path]:
    """Write all three artifacts with O_EXCL; never replace existing evidence."""

    directory = Path(output_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    run_id = report["run_id"]
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        raise ValueError("report run_id is not safe for an artifact filename")
    paths = {
        "report_json": directory / f"{run_id}-report.json",
        "summary_markdown": directory / f"{run_id}-summary.md",
        "raw_json": directory / f"{run_id}-raw.json",
    }
    payloads = {
        "report_json": _encoded_json(report),
        "summary_markdown": report_markdown(report).encode("utf-8"),
        "raw_json": _encoded_json(raw),
    }
    descriptors: dict[str, int] = {}
    created: list[Path] = []
    try:
        for name, path in paths.items():
            descriptors[name] = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            created.append(path)
        for name, descriptor in descriptors.items():
            with os.fdopen(descriptor, "wb") as output:
                descriptors[name] = -1
                output.write(payloads[name])
                output.flush()
                os.fsync(output.fileno())
    except Exception:
        for descriptor in descriptors.values():
            if descriptor >= 0:
                os.close(descriptor)
        for path in created:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="운영자가 준비한 B0/B1/B2 Gateway URL로 고정 3블록 성능 비교를 실행합니다."
    )
    parser.add_argument("plan", help="runtime-perf-plan/1.0 JSON")
    parser.add_argument("--out-dir", required=True, help="배타 생성할 보고서 폴더")
    parser.add_argument(
        "--token-env-file",
        help="소유자 전용 env 파일의 RUNTIME_INFERENCE_TOKEN으로 Bearer 인증",
    )
    parser.add_argument("--warmup-count", type=int, default=DEFAULT_WARMUP)
    parser.add_argument("--normal-count", type=int, default=DEFAULT_NORMAL)
    parser.add_argument("--stream-count", type=int, default=DEFAULT_STREAM)
    parser.add_argument(
        "--blocks",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help="고정 순서 목록의 앞 N개 블록만 실행(기본 3)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = load_plan(args.plan)
        bearer_token = (
            _read_inference_token(Path(args.token_env_file).expanduser())
            if args.token_env_file
            else None
        )
        report, raw = execute_plan(
            plan,
            warmup_count=args.warmup_count,
            normal_count=args.normal_count,
            stream_count=args.stream_count,
            block_count=args.blocks,
            bearer_token=bearer_token,
        )
        paths = write_outputs(report, raw, args.out_dir)
    except (OSError, PlanError, ValueError) as error:
        print(f"성능 비교 실패: {error}", file=os.sys.stderr)
        return 1
    print(f"JSON 보고서: {paths['report_json']}")
    print(f"Markdown 요약: {paths['summary_markdown']}")
    print(f"원시 요청 표: {paths['raw_json']}")
    print(report["execution_budget"]["label"])
    return 0 if all(block["status"] == "ok" for block in report["blocks"]) else 3


if __name__ == "__main__":
    raise SystemExit(main())
