"""Runtime alerts -> webhook (Slack / Discord / generic JSON). Off by default; dry-run unless --send.

Reads only the Gateway read API and sends short Korean summaries when
  - the Gateway denies requests (DENIED),
  - Tetragon detects or blocks (suspicious exec, protected file access) or Hubble drops a flow,
  - a collector / Tetragon status / GPU status / record store / the Gateway itself goes down, and when it recovers.

Never sent: prompts, responses, tokens, IP addresses, Pod or node names, file paths, request IDs.
Only counts, reason codes, rule IDs and fixed state words leave the host.

Event alerts are cursor based: the first run records the current position and does not
replay the backlog. Health alerts are edge triggered (ok -> bad, bad -> ok). Undelivered
alerts stay in a bounded queue in the 0600 state file and are retried on the next cycle.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

SCHEMA = "runtime-alert/1.0"
STATE_SCHEMA = "runtime-alert-state/1.0"
MAX_PENDING = 200
MAX_KEYS = 500
GATEWAY_FAILURES_BEFORE_ALERT = 2
SUSPICIOUS_EXEC = {"outside_allowlist", "suspect_binary", "exec_from_cache"}
BAD_HEALTH = {"stale", "error"}
# A character-class check also accepts credentials, Pod names and addresses.
# Only known protocol vocabulary may leave the host, including in generic JSON.
REASON_CODES = frozenset({
    "REQUEST_ALLOWED", "POLICY_MODEL_NOT_ALLOWED", "MODEL_NOT_ALLOWED", "MAX_TOKENS_EXCEEDED",
    "PRINCIPAL_MODEL_NOT_ALLOWED", "ROLE_NOT_ALLOWED", "AUTH_REQUIRED", "AUTH_INVALID",
    "AUTH_UNAVAILABLE", "CONTEXT_LIMIT_EXCEEDED", "CONCURRENCY_LIMIT", "CONNECTION_LIMIT",
    "POLICY_UNAVAILABLE", "POLICY_EXPIRED", "POLICY_REVOKED", "POLICY_NOT_YET_VALID",
    "CACHE_ISOLATION_UNAVAILABLE", "ASSET_INTEGRITY_FAILED", "ASSET_INTEGRITY_UNAVAILABLE",
    "APPROVAL_REQUIRED", "REVALIDATION_REQUIRED", "STORE_UNAVAILABLE",
})
RULE_IDS = frozenset({"TG-EXEC-01", "TG-SECRET-01", "TG-ASSET-01", "TG-ASSET-02",
                      "TG-CACHE-01", "TG-DENY-01", "TG-HEALTH-01"})
DROP_REASONS = frozenset({"POLICY_DENIED", "Policy denied", "POLICY_DENY", "INVALID_SOURCE_IP",
                          "CT_TRUNCATED_OR_INVALID_HEADER", "CT_MISSING_TCP_ACK_FLAG", "UNKNOWN_L3_TARGET_ADDR"})
ORIGINS = frozenset({"real", "live", "synthetic"})
STATES = frozenset({"ok", "degraded", "stale", "error", "missing", "never", "partial", "disconnected", "not_configured"})
SOURCES = frozenset({"engine_health", "engine_info", "metrics", "record_retention", "asset_integrity",
                     "observation_feed_tetragon", "observation_feed_hubble", "cilium_policy", "target_pods"})
FAILURE_REASONS = frozenset({"UNREACHABLE", "RESPONSE_TOO_LARGE", "INVALID_JSON", "PORT_FORWARD_FAILED"})
LABEL = re.compile(r"[A-Za-z0-9_.-]{1,40}")
FORMATS = ("slack", "discord", "generic")


class GatewayUnavailable(Exception):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: D401 - never follow redirects (token/webhook leak)
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _code(value: object, allowed: frozenset[str] | set[str] = REASON_CODES) -> str:
    return value if isinstance(value, str) and value in allowed else "OTHER"


def _timestamp(value: object) -> str:
    """Normalize before comparing; never persist or send arbitrary timestamp text."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
        if parsed is not None and parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, OverflowError):
        pass
    return ""


def read_env_file(path: Path) -> dict[str, str]:
    info = path.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise SystemExit("env file must not be readable by group/other (chmod 600)")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() in {"ALERT_WEBHOOK_URL", "RUNTIME_VIEWER_TOKEN"}:
            values[key.strip()] = value.strip().strip("\"'")
    return values


def check_webhook_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        raise SystemExit("ALERT_WEBHOOK_URL is invalid") from None
    if not host or parts.fragment or any(c.isspace() or ord(c) < 32 for c in url) or port == 0:
        raise SystemExit("ALERT_WEBHOOK_URL is invalid")
    local = host == "localhost"
    try:
        local = local or ipaddress.ip_address(host).is_loopback
    except ValueError:
        pass
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise SystemExit("ALERT_WEBHOOK_URL must be https (http only for loopback tests)")
    if parts.username or parts.password:
        raise SystemExit("ALERT_WEBHOOK_URL must not contain credentials before the host")
    return url


# ---------------------------------------------------------------- Gateway reads

class GatewayReader:
    def __init__(self, base_url: str, token: str | None = None, timeout: float = 10, opener=None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = opener or build_opener(_NoRedirect())

    def get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = self.base_url + path + ("?" + urlencode(params) if params else "")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        try:
            with self.opener.open(Request(url, headers=headers), timeout=self.timeout) as response:
                body = response.read(8 * 1024 * 1024 + 1)
        except HTTPError as error:
            raise GatewayUnavailable(f"HTTP_{error.code}") from None
        except (URLError, OSError, TimeoutError):
            raise GatewayUnavailable("UNREACHABLE") from None
        if len(body) > 8 * 1024 * 1024:
            raise GatewayUnavailable("RESPONSE_TOO_LARGE")
        try:
            value = json.loads(body)
        except ValueError:
            raise GatewayUnavailable("INVALID_JSON") from None
        if not isinstance(value, dict):
            raise GatewayUnavailable("INVALID_JSON")
        return value


# ---------------------------------------------------------------- alert building

def _alert(kind: str, severity: str, title: str, *, count: int | None = None,
           details: dict[str, Any] | None = None, first_at: str | None = None,
           last_at: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "severity": severity, "title": title, "count": count,
            "details": details or {}, "first_at": first_at, "last_at": last_at, "created_at": _now()}


def _new_items(items: list[dict[str, Any]], at_key: str, key_fn: Callable[[dict], str],
               cursor: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remember a bounded set of visible keys, including late arrivals behind the watermark."""
    dated = [(at, key_fn(item), item) for item in items if (at := _timestamp(item.get(at_key)))]
    if cursor is None:  # first run: remember the position, do not replay history
        latest = max((at for at, _, _ in dated), default="")
        return [], {"at": latest, "keys": sorted(k for at, k, _ in dated if at == latest)[:MAX_KEYS],
                    "recent_keys": list(dict.fromkeys(k for _, k, _ in dated))[-MAX_KEYS:]}
    seen_at, seen_keys = _timestamp(cursor.get("at")), set(cursor.get("keys", []))
    recent = cursor.get("recent_keys")
    seen = set(recent) if recent is not None else seen_keys.copy()
    # Polling sources may deliver an earlier event late. Remember the whole
    # visible page, rather than discarding everything behind a time watermark.
    fresh = []
    for at, key, item in dated:
        eligible = recent is not None or at >= seen_at
        if eligible and key not in seen:
            fresh.append((at, key, item))
            seen.add(key)
    latest = max([seen_at] + [at for at, _, _ in dated])
    keys = {k for at, k, _ in dated if at == latest}
    if latest == seen_at:
        keys |= seen_keys
    remembered = list(dict.fromkeys((recent or []) + [k for _, k, _ in dated]))[-MAX_KEYS:]
    return [item for _, _, item in fresh], {"at": latest, "keys": sorted(keys)[:MAX_KEYS], "recent_keys": remembered}


def _fingerprint(item: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()[:24]


def _span(items: list[dict[str, Any]], key: str) -> tuple[str | None, str | None]:
    times = sorted(at for item in items if (at := _timestamp(item.get(key))))
    return (times[0], times[-1]) if times else (None, None)


def denied_alerts(response: dict[str, Any], cursor, limit: int):
    items = [item for item in response.get("requests", []) if isinstance(item, dict)]
    def request_key(item):
        rid = item.get("request_id")
        # Keep compatibility with existing cursors without retaining arbitrary text.
        return rid if isinstance(rid, str) and re.fullmatch(r"[0-9a-f]{32}", rid) else _fingerprint({"id": rid})
    new, cursor = _new_items(items, "recorded_at", request_key, cursor)
    if not new:
        return [], cursor
    reasons = Counter(_code(item.get("reason_code")) for item in new)
    first, last = _span(new, "recorded_at")
    details = {"reason_codes": dict(reasons.most_common(10))}
    if len(items) >= limit and len(new) == len(items):
        details["truncated"] = True  # more may exist beyond one page
    return [_alert("gateway_denied", "warning", "Gateway 요청 차단", count=len(new),
                   details=details, first_at=first, last_at=last)], cursor


def _tetragon_class(item: dict[str, Any]) -> str | None:
    if item.get("kind") == "process_exec":
        return "exec" if item.get("exec_verdict") in SUSPICIOUS_EXEC else None
    if item.get("kind") == "file_access":
        return "file"
    return None


def observation_alerts(response: dict[str, Any], source: str, cursor, total_before: int | None):
    items = [item for item in response.get("observations", []) if isinstance(item, dict)]
    new, cursor = _new_items(items, "observed_at", _fingerprint, cursor)
    total = (response.get("counts") or {}).get("total")
    alerts = []
    if source == "tetragon":
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in new:
            cls = _tetragon_class(item)
            if cls:
                mode = "enforce" if item.get("action_mode") == "enforce" else "monitor"
                groups.setdefault((cls, mode), []).append(item)
        for (cls, mode), group in sorted(groups.items()):
            blocked = mode == "enforce"
            name = {"exec": "허용 목록 밖 실행", "file": "보호 파일 접근"}[cls]
            first, last = _span(group, "observed_at")
            alerts.append(_alert(
                f"tetragon_{cls}_{'blocked' if blocked else 'detected'}", "critical" if blocked else "warning",
                f"Tetragon {name} {'차단' if blocked else '탐지'}", count=len(group),
                details={"rule_ids": dict(Counter(_code(i.get("rule_id"), RULE_IDS) for i in group).most_common(10)),
                         **({"exec_verdicts": dict(Counter(_code(i.get("exec_verdict"), SUSPICIOUS_EXEC) for i in group))} if cls == "exec" else {}),
                         "data_origin": sorted({_code(i.get("data_origin"), ORIGINS) for i in group})},
                first_at=first, last_at=last))
    else:
        dropped = [item for item in new if item.get("verdict") == "dropped"]
        if dropped:
            first, last = _span(dropped, "observed_at")
            alerts.append(_alert("hubble_dropped", "critical", "Cilium 네트워크 차단", count=len(dropped),
                                 details={"reasons": dict(Counter(_code(i.get("reason"), DROP_REASONS) for i in dropped).most_common(10)),
                                          "data_origin": sorted({_code(i.get("data_origin"), ORIGINS) for i in dropped})},
                                 first_at=first, last_at=last))
    if (alerts and isinstance(total, int) and isinstance(total_before, int)
            and len(items) >= 50 and len(new) == len(items) and total - total_before > len(new)):
        for alert in alerts:
            alert["details"]["truncated"] = True  # only the latest 50 are visible per poll
    return alerts, cursor, total if isinstance(total, int) else total_before


def health_states(status: dict, collectors: dict, summary: dict, tetragon: dict | None,
                  gpu: dict | None) -> dict[str, str]:
    states: dict[str, str] = {}
    for item in collectors.get("collectors", []):
        if isinstance(item, dict) and item.get("source"):
            states["collector:" + _code(item["source"], SOURCES)] = _code(item.get("state"), STATES)
    store = summary.get("record_store") or {}
    states["record_store"] = "ok" if store.get("ok") is True else "error"
    if tetragon is not None:
        states["tetragon_status"] = _code(tetragon.get("state"), STATES)
    if gpu is not None:
        states["gpu_status"] = _code(gpu.get("state"), STATES)
    gateway = _code(status.get("status"), STATES)
    states["gateway"] = "ok" if gateway == "ok" else gateway
    return states


HEALTH_NAMES = {"record_store": "요청 기록 저장소", "tetragon_status": "Tetragon 상태 수집",
                "gpu_status": "GPU 지표 수집", "gateway": "Gateway 상태"}


def _health_name(key: str) -> str:
    return HEALTH_NAMES.get(key) or ("수집기 " + key.split(":", 1)[1] if key.startswith("collector:") else key)


def _is_bad(key: str, value: str) -> bool:
    if key == "gateway":
        return value != "ok"
    return value in BAD_HEALTH


def health_alerts(previous: dict[str, str], current: dict[str, str], first_run: bool) -> list[dict[str, Any]]:
    alerts = []
    for key in sorted(set(previous) | set(current)):
        before, after = previous.get(key), current.get(key)
        if after is None:
            continue
        before = _code(before, STATES) if before is not None else None
        if _is_bad(key, after) and (first_run or before is None or not _is_bad(key, before) or before != after):
            alerts.append(_alert("health_down", "critical", f"{_health_name(key)} 이상: {after}",
                                 details={"target": key, "state": after, "previous": before}))
        elif not _is_bad(key, after) and before is not None and _is_bad(key, before):
            alerts.append(_alert("health_recovered", "info", f"{_health_name(key)} 복구: {after}",
                                 details={"target": key, "state": after, "previous": before}))
    return alerts


# ---------------------------------------------------------------- one cycle

def empty_state() -> dict[str, Any]:
    return {"schema_version": STATE_SCHEMA, "initialized": False, "cursors": {}, "totals": {},
            "health": {}, "gateway_failures": 0, "gateway_down_alerted": False,
            "pending": [], "dropped": 0}


def unreachable(state: dict[str, Any], reason: str) -> list[dict[str, Any]]:
    """Count a failed cycle; alert once after consecutive failures (edge triggered)."""
    state["gateway_failures"] = int(state.get("gateway_failures", 0)) + 1
    if state["gateway_failures"] >= GATEWAY_FAILURES_BEFORE_ALERT and not state.get("gateway_down_alerted"):
        state["gateway_down_alerted"] = True
        return [_alert("gateway_unreachable", "critical", "Gateway 조회 실패",
                       details={"reason": (reason if re.fullmatch(r"HTTP_[1-5][0-9]{2}", reason)
                                            else _code(reason, FAILURE_REASONS)),
                                "consecutive_failures": state["gateway_failures"]})]
    return []


def collect(reader: GatewayReader, state: dict[str, Any], *, request_limit: int = 200,
            tetragon: bool = True, gpu: bool = True) -> list[dict[str, Any]]:
    """Advance state and return new alerts. Gateway failures become one edge-triggered alert."""
    first_run = not state.get("initialized")
    try:
        status = reader.get("/api/status")
        collectors = reader.get("/api/collectors")
        summary = reader.get("/api/summary")
        denied = reader.get("/api/requests", {"result": "DENIED", "limit": str(request_limit)})
        obs = {source: reader.get("/api/observations", {"source": source}) for source in ("tetragon", "hubble")}
        tetragon_state = reader.get("/api/tetragon-status") if tetragon else None
        gpu_state = reader.get("/api/gpu-status") if gpu else None
    except GatewayUnavailable as error:
        return unreachable(state, str(error))
    alerts = []
    if state.get("gateway_down_alerted"):
        alerts.append(_alert("gateway_reachable", "info", "Gateway 조회 복구"))
    state["gateway_failures"], state["gateway_down_alerted"] = 0, False

    cursors = state.setdefault("cursors", {})
    totals = state.setdefault("totals", {})
    found, cursors["denied"] = denied_alerts(denied, None if first_run else cursors.get("denied"), request_limit)
    alerts += found
    for source in ("tetragon", "hubble"):
        found, cursors[source], totals[source] = observation_alerts(
            obs[source], source, None if first_run else cursors.get(source), totals.get(source))
        alerts += found
    current = health_states(status, collectors, summary, tetragon_state, gpu_state)
    alerts += health_alerts(state.get("health", {}), current, first_run)
    state["health"] = current
    state["initialized"] = True
    return alerts


# ---------------------------------------------------------------- delivery

def render_text(alerts: list[dict[str, Any]], label: str) -> str:
    icon = {"critical": "🔴", "warning": "🟠", "info": "🟢"}
    lines = [f"[byul runtime · {label}] 알림 {len(alerts)}건"]
    for alert in alerts:
        count = f" {alert['count']}건" if alert.get("count") is not None else ""
        extra = []
        for key in ("reason_codes", "rule_ids", "reasons", "exec_verdicts"):
            if alert["details"].get(key):
                extra.append(", ".join(f"{k} {v}" for k, v in alert["details"][key].items()))
        if alert["details"].get("truncated"):
            extra.append("일부만 집계됨")
        if "synthetic" in alert["details"].get("data_origin", []):
            extra.append("합성 자료 포함")
        when = f" ({alert['first_at']} ~ {alert['last_at']})" if alert.get("first_at") else ""
        lines.append(f"{icon.get(alert['severity'], '•')} {alert['title']}{count}{when}"
                     + (f" — {' / '.join(extra)}" if extra else ""))
    return "\n".join(lines)


def payload(alerts: list[dict[str, Any]], label: str, fmt: str) -> dict[str, Any]:
    text = render_text(alerts, label)
    if fmt == "slack":
        return {"text": text}
    if fmt == "discord":
        return {"content": text[:1900], "allowed_mentions": {"parse": []}}
    return {"schema_version": SCHEMA, "label": label, "sent_at": _now(), "alerts": alerts, "text": text}


def deliver(url: str, body: dict[str, Any], opener=None, timeout: float = 10) -> bool:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json", "User-Agent": "byul-runtime-alerts"})
    try:
        with (opener or build_opener(_NoRedirect())).open(request, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (HTTPError, URLError, OSError, TimeoutError):
        return False


def queue(state: dict[str, Any], alerts: list[dict[str, Any]]) -> None:
    pending = state.setdefault("pending", []) + alerts
    overflow = max(0, len(pending) - MAX_PENDING)
    state["pending"] = pending[overflow:]
    state["dropped"] = int(state.get("dropped", 0)) + overflow


def flush(state: dict[str, Any], send: Callable[[list[dict[str, Any]]], bool], batch: int = 20) -> int:
    """Send pending alerts in batches; stop at the first failure and keep the rest."""
    sent = 0
    if state.get("dropped"):
        state["pending"].insert(0, _alert("alerts_dropped", "warning", "전송 대기 초과로 일부 알림 누락",
                                          count=state["dropped"]))
        state["dropped"] = 0
    while state.get("pending"):
        part = state["pending"][:batch]
        if not send(part):
            break
        state["pending"] = state["pending"][batch:]
        sent += len(part)
    return sent


# ---------------------------------------------------------------- state file

def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError):
        return empty_state()  # unreadable state: restart from the current position, never replay
    if not isinstance(value, dict) or value.get("schema_version") != STATE_SCHEMA:
        return empty_state()
    return {**empty_state(), **value}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".next")
    temporary.unlink(missing_ok=True)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as target:
        os.fchmod(target.fileno(), 0o600)
        json.dump(state, target, ensure_ascii=False, separators=(",", ":"))
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(path)


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--gateway-url", help="Gateway 주소(예: 포트포워드한 http://127.0.0.1:18080)")
    target.add_argument("--context", help="kubectl context(매 주기 Gateway 포트포워드)")
    parser.add_argument("--namespace", default="serving")
    parser.add_argument("--kubeconfig")
    parser.add_argument("--env-file", type=Path, help="0600 파일: ALERT_WEBHOOK_URL, 선택 RUNTIME_VIEWER_TOKEN")
    parser.add_argument("--state", type=Path, default=Path("/var/lib/serving-runtime/alert-state.json"))
    parser.add_argument("--format", choices=FORMATS, default="slack")
    parser.add_argument("--label", default="lab", help="메시지 앞 서버 구분 이름(영문·숫자)")
    parser.add_argument("--send", action="store_true", help="실제 웹훅 전송. 없으면 보낼 내용만 출력")
    parser.add_argument("--no-tetragon", action="store_true")
    parser.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", type=float, default=30)
    args = parser.parse_args(argv)
    if not LABEL.fullmatch(args.label):
        parser.error("label must be 1-40 letters, digits, '.', '_' or '-'")
    if args.interval < 5:
        parser.error("interval must be at least 5 seconds")
    env = read_env_file(args.env_file) if args.env_file else {}
    webhook = None
    if args.send:
        if not env.get("ALERT_WEBHOOK_URL"):
            parser.error("--send requires --env-file with ALERT_WEBHOOK_URL")
        webhook = check_webhook_url(env["ALERT_WEBHOOK_URL"])
    token = env.get("RUNTIME_VIEWER_TOKEN") or None

    def send(alerts: list[dict[str, Any]]) -> bool:
        body = payload(alerts, args.label, args.format)
        if webhook is None:
            print(json.dumps(body, ensure_ascii=False), flush=True)
            return True
        return deliver(webhook, body)

    while True:
        state = load_state(args.state)
        if args.gateway_url:
            connection = nullcontext(args.gateway_url)
        else:
            from serving_runtime.bundle_tools import _port_forward
            connection = _port_forward(args.context, args.namespace, args.kubeconfig, 8080)
        try:
            with connection as url:
                alerts = collect(GatewayReader(url, token), state,
                                 tetragon=not args.no_tetragon, gpu=not args.no_gpu)
        except (OSError, RuntimeError, subprocess.SubprocessError):  # port-forward could not start (StatusUnavailable is a RuntimeError)
            alerts = unreachable(state, "PORT_FORWARD_FAILED")
        queue(state, alerts)
        save_state(args.state, state)  # Persist cursors and retry queue before network delivery.
        sent = flush(state, send)
        save_state(args.state, state)
        print(json.dumps({"new": len(alerts), "sent": sent, "pending": len(state["pending"]),
                          "mode": "send" if webhook else "dry_run"}), file=sys.stderr, flush=True)
        if not args.watch:
            return 0 if not state["pending"] else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
