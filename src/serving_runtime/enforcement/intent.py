"""Strict loader for runtime enforcement intent documents."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import re
from typing import Any


SCHEMA = "runtime-enforcement-intent/1.0"
SCHEMA_V11 = "runtime-enforcement-intent/1.1"
SCHEMAS = frozenset({SCHEMA, SCHEMA_V11})
PHASES = ("loading", "compiling", "inferring")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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
    management_port: int | None = None


@dataclass(frozen=True)
class EndpointRef:
    labels: dict[str, str]
    port: int
    protocol: str


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
    endpoint: dict[str, str] | None = None


@dataclass(frozen=True)
class AcquireIntent:
    """SP4: the model acquisition Job/Pod, separate from the serving engine."""

    labels: dict[str, str]
    egress_allow: tuple[EgressAllow, ...]


@dataclass(frozen=True)
class TestReceiver:
    """A team-owned fixture that allowed and denied tasks both try to reach."""

    name: str
    labels: dict[str, str]
    port: int
    protocol: str
    allowed_from: tuple[str, ...]


@dataclass(frozen=True)
class CacheGeneration:
    engine: str
    image_id: str
    model_revision: str
    compile_profile: str

    def path(self, cache_root: str) -> str:
        return str(
            Path(cache_root)
            / self.engine
            / self.image_id
            / self.model_revision
            / self.compile_profile
        )


@dataclass(frozen=True)
class AssetsIntent:
    """SP1: supply paths, accounts, and the cache generation for one engine."""

    tokenizer_dir: str | None
    manifest_path: str
    cache_root: str
    cache_generation: CacheGeneration
    engine_uid: int
    acquire_uid: int | None
    probe_uid: int | None

    @property
    def generation_path(self) -> str:
        return self.cache_generation.path(self.cache_root)


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
    schema: str = SCHEMA
    acquire: AcquireIntent | None = None
    test_receivers: tuple[TestReceiver, ...] = ()
    observer_kube_api: bool = False
    assets: AssetsIntent | None = None
    phase_binaries: tuple[tuple[str, tuple[str, ...]], ...] = ()
    suspect_binaries: tuple[str, ...] = ()

    def binaries_for(self, phase: str) -> tuple[str, ...]:
        return dict(self.phase_binaries).get(phase, ())


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


def _workload(
    value: Any, path: str, *, engine: bool = False, v11: bool = False
) -> Workload:
    allowed = {"labels", "port"} if engine else {"labels"}
    if engine and v11:
        allowed = allowed | {"management_port"}
    item = _object(value, path, allowed)
    labels = _labels(_required(item, "labels", path), f"{path}.labels")
    if engine:
        port = _port(item.get("port", 8000), f"{path}.port")
        management = item.get("management_port")
        if management is not None:
            management = _port(management, f"{path}.management_port")
            if management == port:
                raise IntentError(f"{path}.management_port must differ from port")
        return Workload(labels, port, management)
    return Workload(labels)


def _uid(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 2**31 - 1:
        raise IntentError(f"{path} must be a non-root integer UID")
    return value


def _segment(value: Any, path: str) -> str:
    text = _nonempty_string(value, path)
    if not _SEGMENT.fullmatch(text) or text in {".", ".."}:
        raise IntentError(f"{path} must be a single safe path segment")
    return text


def _egress_list(raw: Any, path: str, *, allow_endpoint: bool) -> tuple[EgressAllow, ...]:
    if not isinstance(raw, list):
        raise IntentError(f"{path} must be an array")
    egress: list[EgressAllow] = []
    for index, raw_item in enumerate(raw):
        item_path = f"{path}[{index}]"
        allowed = {"fqdn", "cidr", "port", "protocol"}
        if allow_endpoint:
            allowed = allowed | {"endpoint"}
        item = _object(raw_item, item_path, allowed)
        kinds = [name for name in ("fqdn", "cidr", "endpoint") if name in item]
        if len(kinds) != 1:
            choices = "fqdn, cidr, or endpoint" if allow_endpoint else "fqdn or cidr"
            raise IntentError(f"{item_path} must contain exactly one of {choices}")
        fqdn = _nonempty_string(item["fqdn"], f"{item_path}.fqdn") if "fqdn" in item else None
        if fqdn is not None and not _FQDN.fullmatch(fqdn):
            raise IntentError(f"{item_path}.fqdn is not a valid DNS name")
        cidr = _nonempty_string(item["cidr"], f"{item_path}.cidr") if "cidr" in item else None
        if cidr is not None:
            try:
                cidr = str(ipaddress.ip_network(cidr, strict=True))
            except ValueError as error:
                raise IntentError(f"{item_path}.cidr is not a canonical network") from error
        endpoint = (
            _labels(item["endpoint"], f"{item_path}.endpoint") if "endpoint" in item else None
        )
        protocol = _nonempty_string(_required(item, "protocol", item_path), f"{item_path}.protocol")
        if protocol not in {"TCP", "UDP"}:
            raise IntentError(f"{item_path}.protocol must be TCP or UDP")
        egress.append(
            EgressAllow(
                fqdn,
                cidr,
                _port(_required(item, "port", item_path), f"{item_path}.port"),
                protocol,
                endpoint,
            )
        )
    return tuple(egress)


def _binary_list(raw: Any, path: str) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise IntentError(f"{path} must be an array")
    values = tuple(_absolute_path(item, f"{path}[{index}]") for index, item in enumerate(raw))
    if len(set(values)) != len(values):
        raise IntentError(f"{path} must not contain duplicates")
    return values


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
            # 1.1 only; rejected below for 1.0 documents.
            "acquire",
            "test_receivers",
            "observer_kube_api",
            "assets",
            "phase_binaries",
            "suspect_binaries",
        },
    )
    schema = _required(root, "schema", "$")
    if schema not in SCHEMAS:
        raise IntentError(f"$.schema must be {SCHEMA!r} or {SCHEMA_V11!r}")
    v11 = schema == SCHEMA_V11
    v11_fields = {
        "acquire",
        "test_receivers",
        "observer_kube_api",
        "assets",
        "phase_binaries",
        "suspect_binaries",
    }
    if not v11:
        present = sorted(v11_fields & set(root))
        if present:
            raise IntentError(f"$ has unknown fields: {', '.join(present)}")
    namespace = _nonempty_string(_required(root, "namespace", "$"), "$.namespace")
    gateway = _workload(_required(root, "gateway", "$"), "$.gateway")
    engine = _workload(_required(root, "engine", "$"), "$.engine", engine=True, v11=v11)

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

    egress = _egress_list(
        root.get("engine_egress_allow", []), "$.engine_egress_allow", allow_endpoint=False
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

    acquire: AcquireIntent | None = None
    receivers: list[TestReceiver] = []
    assets: AssetsIntent | None = None
    phase_binaries: list[tuple[str, tuple[str, ...]]] = []
    suspect: tuple[str, ...] = ()
    kube_api = False
    if v11:
        if "acquire" in root:
            acquire_raw = _object(root["acquire"], "$.acquire", {"labels", "egress_allow"})
            acquire = AcquireIntent(
                _labels(_required(acquire_raw, "labels", "$.acquire"), "$.acquire.labels"),
                _egress_list(
                    acquire_raw.get("egress_allow", []),
                    "$.acquire.egress_allow",
                    allow_endpoint=True,
                ),
            )
            if acquire.labels == engine.labels:
                raise IntentError("$.acquire.labels must differ from $.engine.labels")
            # SP4: the engine must not be allowed the acquisition destinations.
            acquire_targets = {
                (item.fqdn, item.cidr, item.port, item.protocol)
                for item in acquire.egress_allow
                if item.endpoint is None
            }
            shared = [
                item.fqdn or item.cidr
                for item in egress
                if (item.fqdn, item.cidr, item.port, item.protocol) in acquire_targets
            ]
            if shared:
                raise IntentError(
                    "$.engine_egress_allow must not include acquisition destinations: "
                    + ", ".join(sorted(shared))
                )
        raw_receivers = root.get("test_receivers", [])
        if not isinstance(raw_receivers, list):
            raise IntentError("$.test_receivers must be an array")
        names: set[str] = set()
        for index, raw_item in enumerate(raw_receivers):
            path = f"$.test_receivers[{index}]"
            item = _object(raw_item, path, {"name", "labels", "port", "protocol", "allowed_from"})
            name = _segment(_required(item, "name", path), f"{path}.name")
            if name in names:
                raise IntentError(f"{path}.name must be unique")
            names.add(name)
            protocol = _nonempty_string(item.get("protocol", "TCP"), f"{path}.protocol")
            if protocol not in {"TCP", "UDP"}:
                raise IntentError(f"{path}.protocol must be TCP or UDP")
            allowed_from = _required(item, "allowed_from", path)
            if (
                not isinstance(allowed_from, list)
                or not allowed_from
                or any(role not in {"acquire", "observer"} for role in allowed_from)
                or len(set(allowed_from)) != len(allowed_from)
            ):
                raise IntentError(f"{path}.allowed_from must list acquire and/or observer")
            if "acquire" in allowed_from and acquire is None:
                raise IntentError(f"{path}.allowed_from uses acquire but $.acquire is missing")
            receivers.append(
                TestReceiver(
                    name,
                    _labels(_required(item, "labels", path), f"{path}.labels"),
                    _port(_required(item, "port", path), f"{path}.port"),
                    protocol,
                    tuple(allowed_from),
                )
            )
        kube_api = root.get("observer_kube_api", False)
        if not isinstance(kube_api, bool):
            raise IntentError("$.observer_kube_api must be a boolean")
        if "assets" in root:
            assets_raw = _object(
                root["assets"],
                "$.assets",
                {
                    "tokenizer_dir",
                    "manifest_path",
                    "cache_root",
                    "cache_generation",
                    "engine_uid",
                    "acquire_uid",
                    "probe_uid",
                },
            )
            generation_raw = _object(
                _required(assets_raw, "cache_generation", "$.assets"),
                "$.assets.cache_generation",
                {"engine", "image_id", "model_revision", "compile_profile"},
            )
            generation = CacheGeneration(
                *(
                    _segment(
                        _required(generation_raw, name, "$.assets.cache_generation"),
                        f"$.assets.cache_generation.{name}",
                    )
                    for name in ("engine", "image_id", "model_revision", "compile_profile")
                )
            )
            uids = {
                name: (_uid(assets_raw[name], f"$.assets.{name}") if name in assets_raw else None)
                for name in ("acquire_uid", "probe_uid")
            }
            assets = AssetsIntent(
                (
                    _absolute_path(assets_raw["tokenizer_dir"], "$.assets.tokenizer_dir")
                    if "tokenizer_dir" in assets_raw
                    else None
                ),
                _absolute_path(_required(assets_raw, "manifest_path", "$.assets"), "$.assets.manifest_path"),
                _absolute_path(_required(assets_raw, "cache_root", "$.assets"), "$.assets.cache_root"),
                generation,
                _uid(_required(assets_raw, "engine_uid", "$.assets"), "$.assets.engine_uid"),
                uids["acquire_uid"],
                uids["probe_uid"],
            )
            declared = [assets.engine_uid, *(uid for uid in uids.values() if uid is not None)]
            if len(set(declared)) != len(declared):
                raise IntentError("$.assets engine/acquire/probe UIDs must be different accounts")
            if protected.cache_dir != assets.generation_path:
                raise IntentError(
                    "$.protected_paths.cache_dir must equal the cache generation path "
                    f"{assets.generation_path}"
                )
            for supply in filter(None, (protected.model_dir, assets.tokenizer_dir)):
                if Path(supply).is_relative_to(assets.cache_root) or Path(
                    assets.cache_root
                ).is_relative_to(supply):
                    raise IntentError("$.assets.cache_root must be separate from supply paths")
        if "phase_binaries" in root:
            phases_raw = _object(root["phase_binaries"], "$.phase_binaries", set(PHASES))
            for phase in PHASES:
                if phase in phases_raw:
                    phase_binaries.append(
                        (phase, _binary_list(phases_raw[phase], f"$.phase_binaries.{phase}"))
                    )
        if "suspect_binaries" in root:
            suspect = _binary_list(root["suspect_binaries"], "$.suspect_binaries")
            allowed_any = set(binaries).union(*(set(items) for _, items in phase_binaries))
            overlap = sorted(allowed_any & set(suspect))
            if overlap:
                raise IntentError(f"$.suspect_binaries overlaps allowed binaries: {', '.join(overlap)}")

    return EnforcementIntent(
        namespace,
        gateway,
        engine,
        collectors,
        dns,
        egress,
        protected,
        binaries,
        mode,
        probe,
        schema=schema,
        acquire=acquire,
        test_receivers=tuple(receivers),
        observer_kube_api=kube_api,
        assets=assets,
        phase_binaries=tuple(phase_binaries),
        suspect_binaries=suspect,
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
