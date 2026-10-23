"""Strict loader for runtime enforcement intent documents."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
from typing import Any


SCHEMA = "runtime-enforcement-intent/1.0"
_FQDN = re.compile(
    r"^(?=.{1,253}\.?$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.?$"
)


class IntentError(ValueError):
    """The intent is not valid for the strict 1.0 schema."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise IntentError(f"duplicate JSON field: {key}")
        document[key] = value
    return document


def _reject_constant(value: str) -> Any:
    raise IntentError(f"non-standard JSON constant is not allowed: {value}")


@dataclass(frozen=True)
class Workload:
    labels: dict[str, str]
    port: int | None = None


@dataclass(frozen=True)
class DNSIntent:
    namespace: str
    labels: dict[str, str]
    port: int


@dataclass(frozen=True)
class EgressAllow:
    fqdn: str | None
    cidr: str | None
    port: int
    protocol: str


@dataclass(frozen=True)
class ProtectedPaths:
    model_dir: str
    cache_dir: str
    secret_paths: tuple[str, ...]


@dataclass(frozen=True)
class EnforcementIntent:
    namespace: str
    gateway: Workload
    engine: Workload
    metrics_collectors: tuple[Workload, ...]
    dns: DNSIntent
    engine_egress_allow: tuple[EgressAllow, ...]
    protected_paths: ProtectedPaths
    allowed_engine_binaries: tuple[str, ...]
    tetragon_mode: str
    probe_path: str | None


def _object(value: Any, path: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IntentError(f"{path} must be an object")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise IntentError(f"{path} has unknown fields: {', '.join(unknown)}")
    return value


def _required(document: dict[str, Any], field: str, path: str) -> Any:
    if field not in document:
        raise IntentError(f"{path}.{field} is required")
    return document[field]


def _nonempty_string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise IntentError(f"{path} must be a non-empty string without outer whitespace")
    return value


def _labels(value: Any, path: str) -> dict[str, str]:
    labels = _object(value, path, set(value) if isinstance(value, dict) else set())
    if not labels:
        raise IntentError(f"{path} must contain at least one label")
    normalized: dict[str, str] = {}
    for key, item in labels.items():
        normalized[_nonempty_string(key, f"{path} key")] = _nonempty_string(
            item, f"{path}.{key}"
        )
    return normalized


def _port(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise IntentError(f"{path} must be an integer from 1 through 65535")
    return value


def _absolute_path(value: Any, path: str) -> str:
    text = _nonempty_string(value, path)
    candidate = Path(text)
    if not candidate.is_absolute():
        raise IntentError(f"{path} must be an absolute path")
    if ".." in candidate.parts:
        raise IntentError(f"{path} must not contain parent traversal")
    return text


def _workload(value: Any, path: str, *, engine: bool = False) -> Workload:
    allowed = {"labels", "port"} if engine else {"labels"}
    item = _object(value, path, allowed)
    labels = _labels(_required(item, "labels", path), f"{path}.labels")
    if engine:
        return Workload(labels, _port(item.get("port", 8000), f"{path}.port"))
    return Workload(labels)


def parse_intent(document: Any) -> EnforcementIntent:
    """Validate and normalize one strict ``runtime-enforcement-intent/1.0`` object."""

    root = _object(
        document,
        "$",
        {
            "schema",
            "namespace",
            "gateway",
            "engine",
            "metrics_collectors",
            "dns",
            "engine_egress_allow",
            "protected_paths",
            "allowed_engine_binaries",
            "tetragon_mode",
            "probe_path",
        },
    )
    if _required(root, "schema", "$") != SCHEMA:
        raise IntentError(f"$.schema must be {SCHEMA!r}")
    namespace = _nonempty_string(_required(root, "namespace", "$"), "$.namespace")
    gateway = _workload(_required(root, "gateway", "$"), "$.gateway")
    engine = _workload(_required(root, "engine", "$"), "$.engine", engine=True)

    raw_collectors = _required(root, "metrics_collectors", "$")
    if not isinstance(raw_collectors, list):
        raise IntentError("$.metrics_collectors must be an array")
    collectors = tuple(
        _workload(item, f"$.metrics_collectors[{index}]")
        for index, item in enumerate(raw_collectors)
    )

    dns_raw = _object(root.get("dns", {}), "$.dns", {"namespace", "labels", "port"})
    dns = DNSIntent(
        _nonempty_string(dns_raw.get("namespace", "kube-system"), "$.dns.namespace"),
        _labels(dns_raw.get("labels", {"k8s-app": "kube-dns"}), "$.dns.labels"),
        _port(dns_raw.get("port", 53), "$.dns.port"),
    )

    raw_egress = root.get("engine_egress_allow", [])
    if not isinstance(raw_egress, list):
        raise IntentError("$.engine_egress_allow must be an array")
    egress: list[EgressAllow] = []
    for index, raw_item in enumerate(raw_egress):
        path = f"$.engine_egress_allow[{index}]"
        item = _object(raw_item, path, {"fqdn", "cidr", "port", "protocol"})
        has_fqdn = "fqdn" in item
        has_cidr = "cidr" in item
        if has_fqdn == has_cidr:
            raise IntentError(f"{path} must contain exactly one of fqdn or cidr")
        fqdn = _nonempty_string(item["fqdn"], f"{path}.fqdn") if has_fqdn else None
        if fqdn is not None and not _FQDN.fullmatch(fqdn):
            raise IntentError(f"{path}.fqdn is not a valid DNS name")
        cidr = _nonempty_string(item["cidr"], f"{path}.cidr") if has_cidr else None
        if cidr is not None:
            try:
                cidr = str(ipaddress.ip_network(cidr, strict=True))
            except ValueError as error:
                raise IntentError(f"{path}.cidr is not a canonical network") from error
        protocol = _nonempty_string(
            _required(item, "protocol", path), f"{path}.protocol"
        )
        if protocol not in {"TCP", "UDP"}:
            raise IntentError(f"{path}.protocol must be TCP or UDP")
        egress.append(
            EgressAllow(
                fqdn,
                cidr,
                _port(_required(item, "port", path), f"{path}.port"),
                protocol,
            )
        )

    protected_raw = _object(
        _required(root, "protected_paths", "$"),
        "$.protected_paths",
        {"model_dir", "cache_dir", "secret_paths"},
    )
    raw_secrets = _required(protected_raw, "secret_paths", "$.protected_paths")
    if not isinstance(raw_secrets, list):
        raise IntentError("$.protected_paths.secret_paths must be an array")
    protected = ProtectedPaths(
        _absolute_path(
            _required(protected_raw, "model_dir", "$.protected_paths"),
            "$.protected_paths.model_dir",
        ),
        _absolute_path(
            _required(protected_raw, "cache_dir", "$.protected_paths"),
            "$.protected_paths.cache_dir",
        ),
        tuple(
            _absolute_path(item, f"$.protected_paths.secret_paths[{index}]")
            for index, item in enumerate(raw_secrets)
        ),
    )

    raw_binaries = _required(root, "allowed_engine_binaries", "$")
    if not isinstance(raw_binaries, list):
        raise IntentError("$.allowed_engine_binaries must be an array")
    binaries = tuple(
        _absolute_path(item, f"$.allowed_engine_binaries[{index}]")
        for index, item in enumerate(raw_binaries)
    )
    mode = _nonempty_string(root.get("tetragon_mode", "monitor"), "$.tetragon_mode")
    if mode not in {"monitor", "enforce_probe"}:
        raise IntentError("$.tetragon_mode must be monitor or enforce_probe")
    probe = root.get("probe_path")
    if probe is not None:
        probe = _absolute_path(probe, "$.probe_path")
    if mode == "enforce_probe" and probe is None:
        raise IntentError("$.probe_path is required in enforce_probe mode")

    return EnforcementIntent(
        namespace,
        gateway,
        engine,
        collectors,
        dns,
        tuple(egress),
        protected,
        binaries,
        mode,
        probe,
    )


def load_intent(path: str | Path) -> EnforcementIntent:
    try:
        document = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise IntentError(f"intent could not be read as JSON: {error}") from error
    return parse_intent(document)
