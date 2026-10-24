"""Strict external-backend configuration and fixed-destination HTTP helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from http.client import HTTPConnection, HTTPSConnection
import json
import math
import os
from pathlib import Path
import re
import ssl
import stat
from typing import Any, Mapping
from urllib.parse import urlsplit

from .policy import DuplicateKeyError, _object_without_duplicates, _reject_json_constant


_TOP_LEVEL_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "base_url",
        "model_map",
        "credential",
        "metrics_credential",
        "health_path",
        "models_path",
        "version_path",
        "metrics_url",
        "timeouts",
        "metrics_map",
    }
)
# External serving engines this Gateway can relay to. Each keeps its own
# request field names; see BackendConfig.output_tokens_field.
EXTERNAL_BACKEND_KINDS = frozenset({"vllm", "sglang"})
_TIMEOUT_FIELDS = frozenset(
    {"relay_timeout_s", "stream_idle_timeout_s", "request_deadline_s"}
)
_METRIC_FIELDS = frozenset({"metric", "type", "unit", "source_doc", "verified"})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_METRIC_NAME = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]*$")
_SAMPLE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>.*)\})?\s+"
    r"(?P<value>[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?|[-+]Inf|NaN)"
    r"(?:\s+[0-9]+)?$"
)


def is_valid_credential_value(value: str) -> bool:
    """Return whether a secret is safe to place in an HTTP header value."""

    return bool(value) and all(0x21 <= ord(character) <= 0x7E for character in value)


def normalize_credential_value(value: str) -> str:
    """Remove at most one terminal LF or CRLF without trimming other characters."""

    if value.endswith("\r\n"):
        return value[:-2]
    if value.endswith("\n"):
        return value[:-1]
    return value


@dataclass(frozen=True)
class URLTarget:
    """A validated HTTP(S) origin plus an optional fixed path prefix."""

    scheme: str
    host: str
    port: int
    path_prefix: str
    display_url: str

    def path(self, route: str) -> str:
        return f"{self.path_prefix}{route}" if self.path_prefix else route

    def url(self, route: str) -> str:
        return f"{self.display_url}{route}"

    def connection(self, timeout_s: float) -> HTTPConnection:
        if self.scheme == "https":
            return HTTPSConnection(
                self.host,
                self.port,
                timeout=timeout_s,
                context=ssl.create_default_context(),
            )
        return HTTPConnection(self.host, self.port, timeout=timeout_s)


@dataclass(frozen=True)
class BackendConfig:
    """Runtime-safe backend settings. The credential value is never serialized."""

    schema_version: str
    kind: str
    target: URLTarget
    model_map: dict[str, str]
    health_path: str = "/health"
    models_path: str = "/v1/models"
    version_path: str | None = "/version"
    metrics_target: URLTarget | None = None
    metrics_path: str | None = None
    timeouts: dict[str, float] = field(default_factory=dict)
    metrics_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    credential_status: str = "configured"
    credential_configured: bool = False
    metrics_credential_status: str = "not_configured"
    metrics_credential_configured: bool = False
    _authorization: str | None = field(default=None, repr=False, compare=False)
    _metrics_authorization: str | None = field(default=None, repr=False, compare=False)

    @classmethod
    def mock(cls, address: tuple[str, int]) -> "BackendConfig":
        host, port = address
        target = _parse_origin(f"http://{host}:{port}", field_name="mock address")
        return cls(
            schema_version="runtime-backend/mock",
            kind="mock",
            target=target,
            model_map={},
            health_path="/healthz",
        )

    @property
    def base_url(self) -> str:
        return self.target.display_url

    @property
    def external(self) -> bool:
        return self.kind != "mock"

    @property
    def output_tokens_field(self) -> str:
        """The engine's own name for the output-token limit.

        SGLang v0.5.21 marks ``max_tokens`` deprecated in favour of
        ``max_completion_tokens``; vLLM and the mock engine take ``max_tokens``.
        """

        return "max_completion_tokens" if self.kind == "sglang" else "max_tokens"

    @property
    def authorization(self) -> str | None:
        return self._authorization

    @property
    def metrics_authorization(self) -> str | None:
        return self._metrics_authorization

    def mapped_model(self, policy_model: str | None) -> str | None:
        if not isinstance(policy_model, str):
            return None
        if self.kind == "mock":
            return policy_model
        return self.model_map.get(policy_model)

    def request_headers(
        self,
        *,
        request_id: str | None = None,
        content_type: str | None = None,
        metrics: bool = False,
    ) -> dict[str, str]:
        headers: dict[str, str] = {}
        if content_type is not None:
            headers["Content-Type"] = content_type
        if request_id is not None:
            headers["X-Request-ID"] = request_id
        authorization = self._metrics_authorization if metrics else self._authorization
        if authorization is not None:
            headers["Authorization"] = authorization
        return headers

    def connection(self, route: str, timeout_s: float) -> tuple[HTTPConnection, str]:
        return self.target.connection(timeout_s), self.target.path(route)

    def metrics_connection(self, timeout_s: float) -> tuple[HTTPConnection, str]:
        if self.metrics_target is None or self.metrics_path is None:
            raise RuntimeError("metrics endpoint is not configured")
        return self.metrics_target.connection(timeout_s), self.metrics_path

    def safe_status(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base_url": self.base_url,
            "credential": self.credential_status,
            "credential_configured": self.credential_configured,
            "health_path": self.health_path,
            "metrics_configured": self.metrics_target is not None,
            "metrics_credential": self.metrics_credential_status,
            "metrics_credential_configured": self.metrics_credential_configured,
        }


def _require_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")
    return value


def _unknown_fields(document: Mapping[str, Any], allowed: frozenset[str], field_name: str) -> None:
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ValueError(f"unknown {field_name} field(s): {', '.join(unknown)}")


def _route(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or value.startswith("//"):
        raise ValueError(f"{field_name} must be an absolute path")
    if "?" in value or "#" in value or any(ord(character) < 0x20 for character in value):
        raise ValueError(f"{field_name} must not contain a query, fragment, or control character")
    return value


def _parse_origin(value: Any, *, field_name: str) -> URLTarget:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field_name} must be a non-empty URL")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} is invalid") from error
    if (
        parsed.scheme not in {"http", "https"}
        or host is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{field_name} must be an http/https URL without credentials, query, or fragment"
        )
    if any(ord(character) < 0x20 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    path_prefix = parsed.path.rstrip("/")
    if path_prefix and not path_prefix.startswith("/"):
        raise ValueError(f"{field_name} has an invalid path prefix")
    actual_port = port if port is not None else (443 if parsed.scheme == "https" else 80)
    host_display = f"[{host}]" if ":" in host else host
    default_port = 443 if parsed.scheme == "https" else 80
    authority = host_display if actual_port == default_port else f"{host_display}:{actual_port}"
    display_url = f"{parsed.scheme}://{authority}{path_prefix}"
    return URLTarget(parsed.scheme, host, actual_port, path_prefix, display_url)


def _parse_metrics_url(value: Any) -> tuple[URLTarget, str]:
    target = _parse_origin(value, field_name="metrics_url")
    parsed = urlsplit(str(value))
    path = parsed.path or "/"
    origin = URLTarget(
        target.scheme,
        target.host,
        target.port,
        "",
        target.display_url[: -len(target.path_prefix)] if target.path_prefix else target.display_url,
    )
    return origin, path


def _read_credential(
    credential: Any,
    *,
    config_dir: Path,
    environ: Mapping[str, str],
    field_name: str = "credential",
) -> tuple[bool, str, str | None]:
    if credential is None:
        return False, "configured", None
    value = _require_object(credential, field_name)
    if set(value) not in ({"env"}, {"file"}):
        raise ValueError(f"{field_name} must contain exactly one of env or file")
    insecure = False
    secret: str | None = None
    if "env" in value:
        name = value["env"]
        if not isinstance(name, str) or _ENV_NAME.fullmatch(name) is None:
            raise ValueError(f"{field_name}.env must be a valid environment variable name")
        candidate = environ.get(name)
        if isinstance(candidate, str) and candidate:
            secret = candidate
    else:
        configured_path = value["file"]
        if not isinstance(configured_path, str) or not configured_path.strip():
            raise ValueError(f"{field_name}.file must be a non-empty path")
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = config_dir / path
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            insecure = bool(mode & 0o044)
            with path.open("r", encoding="utf-8", newline="") as credential_file:
                candidate = credential_file.read()
        except (OSError, UnicodeError):
            candidate = ""
        if candidate:
            secret = candidate
    if secret is None:
        return True, "missing", None
    secret = normalize_credential_value(secret)
    if not is_valid_credential_value(secret):
        return True, "invalid", None
    return True, "insecure_file" if insecure else "configured", f"Bearer {secret}"


def load_backend_config(
    path: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
) -> BackendConfig:
    """Load and strictly validate one ``runtime-backend/1.0`` JSON file."""

    config_path = Path(path).expanduser()
    try:
        raw = config_path.read_bytes()
    except OSError as error:
        raise ValueError("backend config could not be read") from error
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, DuplicateKeyError, json.JSONDecodeError, ValueError, RecursionError) as error:
        raise ValueError("backend config is not valid strict JSON") from error
    document = _require_object(document, "backend config")
    _unknown_fields(document, _TOP_LEVEL_FIELDS, "backend config")
    required = {"schema_version", "kind", "base_url", "model_map"}
    missing = sorted(required - set(document))
    if missing:
        raise ValueError(f"missing backend config field(s): {', '.join(missing)}")
    if document["schema_version"] != "runtime-backend/1.0":
        raise ValueError("unsupported backend schema_version")
    kind = document["kind"]
    if kind not in EXTERNAL_BACKEND_KINDS:
        raise ValueError("backend kind must be vllm or sglang")

    target = _parse_origin(document["base_url"], field_name="base_url")
    raw_model_map = _require_object(document["model_map"], "model_map")
    model_map: dict[str, str] = {}
    for policy_model, backend_model in raw_model_map.items():
        if (
            not isinstance(policy_model, str)
            or not policy_model.strip()
            or not isinstance(backend_model, str)
            or not backend_model.strip()
        ):
            raise ValueError("model_map keys and values must be non-empty strings")
        model_map[policy_model] = backend_model

    health_path = _route(document.get("health_path", "/health"), "health_path")
    models_path = _route(document.get("models_path", "/v1/models"), "models_path")
    # SGLang v0.5.21 has no /version route; its version is only inside
    # /server_info, which also returns the resolved server arguments. The
    # default therefore skips version collection for sglang (null = disabled).
    raw_version_path = document.get(
        "version_path", "/version" if kind == "vllm" else None
    )
    version_path = (
        None if raw_version_path is None else _route(raw_version_path, "version_path")
    )

    raw_timeouts = document.get("timeouts", {})
    timeouts_document = _require_object(raw_timeouts, "timeouts")
    _unknown_fields(timeouts_document, _TIMEOUT_FIELDS, "timeouts")
    timeouts: dict[str, float] = {}
    for name, value in timeouts_document.items():
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
            or float(value) <= 0
        ):
            raise ValueError(f"timeouts.{name} must be a positive finite number")
        timeouts[name] = float(value)

    raw_metrics_map = document.get("metrics_map", {})
    metrics_document = _require_object(raw_metrics_map, "metrics_map")
    metrics_map: dict[str, dict[str, Any]] = {}
    for field_name, raw_mapping in metrics_document.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise ValueError("metrics_map field names must be non-empty strings")
        mapping = _require_object(raw_mapping, f"metrics_map.{field_name}")
        _unknown_fields(mapping, _METRIC_FIELDS, f"metrics_map.{field_name}")
        if set(mapping) != _METRIC_FIELDS:
            raise ValueError(
                f"metrics_map.{field_name} must contain metric, type, unit, source_doc, verified"
            )
        metric = mapping["metric"]
        if not isinstance(metric, str) or _METRIC_NAME.fullmatch(metric) is None:
            raise ValueError(f"metrics_map.{field_name}.metric is invalid")
        for name in ("type", "unit", "source_doc"):
            if not isinstance(mapping[name], str) or not mapping[name].strip():
                raise ValueError(f"metrics_map.{field_name}.{name} must be a non-empty string")
        if mapping["verified"] is not False:
            raise ValueError(f"metrics_map.{field_name}.verified must be false")
        metrics_map[field_name] = dict(mapping)

    metrics_target: URLTarget | None = None
    metrics_path: str | None = None
    if "metrics_url" in document:
        metrics_target, metrics_path = _parse_metrics_url(document["metrics_url"])

    configured, credential_status, authorization = _read_credential(
        document.get("credential"),
        config_dir=config_path.parent,
        environ=os.environ if environ is None else environ,
    )
    (
        metrics_credential_configured,
        metrics_credential_status,
        metrics_authorization,
    ) = _read_credential(
        document.get("metrics_credential"),
        config_dir=config_path.parent,
        environ=os.environ if environ is None else environ,
        field_name="metrics_credential",
    )
    if not metrics_credential_configured:
        metrics_credential_status = "not_configured"
    return BackendConfig(
        schema_version="runtime-backend/1.0",
        kind=kind,
        target=target,
        model_map=model_map,
        health_path=health_path,
        models_path=models_path,
        version_path=version_path,
        metrics_target=metrics_target,
        metrics_path=metrics_path,
        timeouts=timeouts,
        metrics_map=metrics_map,
        credential_status=credential_status,
        credential_configured=configured,
        metrics_credential_status=metrics_credential_status,
        metrics_credential_configured=metrics_credential_configured,
        _authorization=authorization,
        _metrics_authorization=metrics_authorization,
    )


_COMMENT = re.compile(r"^#\s+(?P<kind>HELP|TYPE)\s+(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)(?:\s+(?P<rest>.*))?$")
_HELP_LIMIT = 200


def _clean_help(text: str) -> str:
    # HELP text comes from the engine; keep it short and printable.
    cleaned = "".join(character if character.isprintable() else " " for character in text)
    return cleaned[:_HELP_LIMIT]


def _label_keys(labels: str) -> list[str]:
    return sorted(set(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"', labels)))


def _declared(table: dict[str, str], metric: str, metric_type: str) -> str | None:
    """Find the TYPE/HELP entry for a mapped metric name.

    The Prometheus text format declares counters under the sample name
    (``x_total``) while OpenMetrics declares the family (``x``); accept both.
    """

    if metric in table:
        return table[metric]
    if metric_type == "counter" and metric.endswith("_total"):
        return table.get(metric[: -len("_total")])
    return None


def parse_prometheus_metrics(
    payload: bytes, metrics_map: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    """Extract only explicitly mapped metric families from Prometheus text.

    Each mapped field also reports the engine's own TYPE declaration and HELP
    text so a mapping whose declared type differs is not reported as ok.
    """

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("metrics response is not UTF-8") from error
    wanted: dict[str, set[str]] = {}
    for mapping in metrics_map.values():
        metric = str(mapping["metric"])
        names = {metric}
        if mapping.get("type") == "histogram":
            names.update({f"{metric}_bucket", f"{metric}_sum", f"{metric}_count"})
        wanted[metric] = names
    extracted: dict[str, list[dict[str, Any]]] = {name: [] for name in wanted}
    declared_types: dict[str, str] = {}
    help_texts: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            comment = _COMMENT.fullmatch(line)
            if comment is not None:
                rest = (comment.group("rest") or "").strip()
                if comment.group("kind") == "TYPE":
                    declared_types[comment.group("name")] = rest
                else:
                    help_texts[comment.group("name")] = _clean_help(rest)
            continue
        match = _SAMPLE.fullmatch(line)
        if match is None:
            continue
        sample_name = match.group("name")
        family = next((name for name, names in wanted.items() if sample_name in names), None)
        if family is None:
            continue
        raw_value = match.group("value")
        try:
            numeric = float(raw_value)
            value: float | str = numeric if math.isfinite(numeric) else raw_value
        except ValueError:
            value = raw_value
        extracted[family].append(
            {
                "sample": sample_name,
                "labels": match.group("labels") or "",
                "value": value,
            }
        )
    result: dict[str, Any] = {}
    for field_name, mapping in metrics_map.items():
        metric = str(mapping["metric"])
        expected_type = str(mapping["type"])
        samples = extracted[metric]
        declared_type = _declared(declared_types, metric, expected_type)
        if declared_type is None:
            type_check = "not_declared"
        elif declared_type == expected_type:
            type_check = "match"
        else:
            type_check = "mismatch"
        if not samples:
            state = "unsupported_or_absent"
        elif type_check == "mismatch":
            state = "type_mismatch"
        else:
            state = "ok"
        result[field_name] = {
            **dict(mapping),
            "state": state,
            "declared_type": declared_type,
            "type_check": type_check,
            "help": _declared(help_texts, metric, expected_type),
            "label_keys": sorted({key for sample in samples for key in _label_keys(sample["labels"])}),
            "samples": samples,
        }
    return result


def unmapped_metric_families(payload: bytes, metrics_map: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return declared metric families that no mapping covers (for map review)."""

    text = payload.decode("utf-8", errors="replace")
    mapped = {str(mapping["metric"]) for mapping in metrics_map.values()}
    mapped |= {name[: -len("_total")] for name in mapped if name.endswith("_total")}
    families = set()
    for raw_line in text.splitlines():
        comment = _COMMENT.fullmatch(raw_line.strip())
        if comment is not None and comment.group("kind") == "TYPE":
            families.add(comment.group("name"))
    return sorted(families - mapped)
