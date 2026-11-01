"""F1 target registration frame: declared target, observed instance, and drift.

The registration file says which target this Gateway is approved to front.
The registry compares it with the policy, the backend configuration, the
engine API values already collected by ``engine_info``, and (optionally) a
read-only Kubernetes Pod snapshot. A Pod replacement or container restart is
a new observed instance, so evidence from an earlier instance is never shown as
belonging to the current one. Conflicts are reported; nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
from typing import Any, Callable

from .policy import DuplicateKeyError, _object_without_duplicates, _reject_json_constant
from .kube_context import validate_kube_context
from .store import JsonlStore


SCHEMA_VERSION = "runtime-target/1.0"
SNAPSHOT_SCHEMA = "runtime-target-snapshot/1.0"
POD_SNAPSHOT_SCHEMA = "runtime-target-pods-snapshot/1.0"
POD_SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024
_TOP = {"schema_version", "target_id", "target_ref", "engine", "model", "baseline_ref", "kubernetes"}
_ENGINE = {"kind", "version", "image_digest"}
_MODEL = {"id", "revision"}
_KUBERNETES = {"context", "namespace", "selector", "container"}
_NAME = re.compile(r"[a-z0-9]([-a-z0-9.]{0,251}[a-z0-9])?")
_LABEL_KEY = re.compile(r"([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?")
_LABEL_VALUE = re.compile(r"([A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?)?")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_ERROR_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class KubernetesSelector:
    context: str
    namespace: str
    selector: tuple[tuple[str, str], ...]
    container: str

    def label_selector(self) -> str:
        return ",".join(f"{key}={value}" for key, value in self.selector)


def validate_kubernetes_name(value: object, field: str = "name") -> str:
    if not isinstance(value, str) or _NAME.fullmatch(value) is None or len(value) > 63:
        raise ValueError(f"{field} must be a Kubernetes name")
    return value


def parse_label_selector(value: str) -> tuple[tuple[str, str], ...]:
    """Parse the equality-only selector form accepted by target registration."""

    if not isinstance(value, str) or not value:
        raise ValueError("selector must be a non-empty KEY=VALUE list")
    pairs: dict[str, str] = {}
    for item in value.split(","):
        key, separator, label_value = item.partition("=")
        if (
            separator != "="
            or not key
            or _LABEL_KEY.fullmatch(key) is None
            or _LABEL_VALUE.fullmatch(label_value) is None
            or len(label_value) > 63
            or key in pairs
        ):
            raise ValueError("selector must contain unique valid KEY=VALUE labels")
        pairs[key] = label_value
    return validate_label_selector(pairs)


def validate_label_selector(
    selector: dict[object, object],
) -> tuple[tuple[str, str], ...]:
    for key, value in selector.items():
        if (
            not isinstance(key, str)
            or _LABEL_KEY.fullmatch(key) is None
            or not isinstance(value, str)
            or _LABEL_VALUE.fullmatch(value) is None
            or len(value) > 63
        ):
            raise ValueError("selector must contain valid label keys and values")
    return tuple(sorted(selector.items()))


@dataclass(frozen=True)
class TargetRegistration:
    state: str  # not_configured | ok | error
    path: str | None
    sha256: str | None
    loaded_at: str
    target_id: str | None = None
    target_ref: str | None = None
    engine_kind: str | None = None
    engine_version: str | None = None
    image_digest: str | None = None
    model_id: str | None = None
    model_revision: str | None = None
    baseline_ref: str | None = None
    kubernetes: KubernetesSelector | None = None
    error_code: str | None = None
    error_message: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "path": self.path,
            "sha256": self.sha256,
            "loaded_at": self.loaded_at,
            "target_id": self.target_id,
            "target_ref": self.target_ref,
            "engine": {
                "kind": self.engine_kind,
                "version": self.engine_version,
                "image_digest": self.image_digest,
            },
            "model": {"id": self.model_id, "revision": self.model_revision},
            "baseline_ref": self.baseline_ref,
            "kubernetes": (
                {
                    "context": self.kubernetes.context,
                    "namespace": self.kubernetes.namespace,
                    "selector": dict(self.kubernetes.selector),
                    "container": self.kubernetes.container,
                }
                if self.kubernetes
                else None
            ),
            "error_code": self.error_code,
            "error_message": self.error_message,
        }


def _optional_string(document: dict[str, Any], key: str, where: str) -> str | None:
    value = document.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{where}.{key} must be a non-empty string of at most 256 characters")
    return value


def _object(document: dict[str, Any], key: str, allowed: set[str]) -> dict[str, Any]:
    value = document.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    unknown = sorted(value.keys() - allowed)
    if unknown:
        raise ValueError(f"{key} has unknown field(s): {', '.join(unknown)}")
    return value


def _parse(document: object) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("target registration must be a JSON object")
    unknown = sorted(document.keys() - _TOP)
    if unknown:
        raise ValueError(f"unknown target field(s): {', '.join(unknown)}")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    target_id = document.get("target_id")
    if not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("target_id must be a non-empty string")
    engine = _object(document, "engine", _ENGINE)
    kind = engine.get("kind")
    if kind not in {"mock", "vllm", "sglang"}:
        raise ValueError("engine.kind must be mock, vllm, or sglang")
    image_digest = _optional_string(engine, "image_digest", "engine")
    if image_digest is not None and _DIGEST.fullmatch(image_digest) is None:
        raise ValueError("engine.image_digest must be sha256:<64 lowercase hex>")
    model = _object(document, "model", _MODEL)
    model_id = model.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model.id must be a non-empty string")
    kubernetes: KubernetesSelector | None = None
    if document.get("kubernetes") is not None:
        k8s = _object(document, "kubernetes", _KUBERNETES)
        context = k8s.get("context")
        try:
            context = validate_kube_context(context)
        except ValueError as error:
            raise ValueError("kubernetes.context is invalid") from error
        namespace = k8s.get("namespace")
        container = k8s.get("container")
        for name, value in (("namespace", namespace), ("container", container)):
            validate_kubernetes_name(value, f"kubernetes.{name}")
        selector = k8s.get("selector")
        if not isinstance(selector, dict) or not selector:
            raise ValueError("kubernetes.selector must be a non-empty label object")
        normalized_selector = validate_label_selector(selector)
        kubernetes = KubernetesSelector(
            context, namespace, normalized_selector, container
        )
    return {
        "target_id": target_id,
        "target_ref": _optional_string(document, "target_ref", "target"),
        "engine_kind": kind,
        "engine_version": _optional_string(engine, "version", "engine"),
        "image_digest": image_digest,
        "model_id": model_id,
        "model_revision": _optional_string(model, "revision", "model"),
        "baseline_ref": _optional_string(document, "baseline_ref", "target"),
        "kubernetes": kubernetes,
    }


def load_target(path: str | Path | None) -> TargetRegistration:
    loaded_at = _utc_now()
    if path is None:
        return TargetRegistration("not_configured", None, None, loaded_at)
    path_text = str(path)

    def error(code: str, message: str, digest: str | None = None) -> TargetRegistration:
        return TargetRegistration(
            "error", path_text, digest, loaded_at, error_code=code, error_message=message
        )

    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return error("TARGET_NOT_FOUND", "target registration not found")
    except OSError as failure:
        return error("TARGET_READ_ERROR", f"could not read target registration: {type(failure).__name__}")
    digest = hashlib.sha256(raw).hexdigest()
    try:
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except DuplicateKeyError as failure:
        return error("DUPLICATE_KEY", str(failure), digest)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return error("MALFORMED_JSON", "target registration is not valid JSON", digest)
    try:
        fields = _parse(document)
    except ValueError as failure:
        return error("INVALID_TARGET", str(failure), digest)
    return TargetRegistration("ok", path_text, digest, loaded_at, **fields)


def pods_command(
    selector: KubernetesSelector, context: str | None = None
) -> list[str]:
    return [
        "kubectl",
        "--context",
        validate_kube_context(context or selector.context),
        "--namespace",
        selector.namespace,
        "get",
        "pods",
        "--selector",
        selector.label_selector(),
        "-o",
        "json",
    ]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _image_digest(image_id: str | None) -> str | None:
    if image_id is None:
        return None
    match = re.search(r"sha256:[0-9a-f]{64}", image_id)
    return match.group(0) if match else None


def instance_id(pod_uid: str, container_id: str) -> str:
    return hashlib.sha256(f"{pod_uid}\0{container_id}".encode("utf-8")).hexdigest()[:16]


def summarize_pods(document: object, container: str) -> dict[str, Any]:
    """Reduce a ``kubectl get pods -o json`` document to the observed instance."""

    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise ValueError("Pod response has no items array")
    pods: list[dict[str, Any]] = []
    for item in document["items"]:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        spec = item.get("spec") if isinstance(item.get("spec"), dict) else {}
        status = item.get("status") if isinstance(item.get("status"), dict) else {}
        statuses = status.get("containerStatuses")
        matched = None
        if isinstance(statuses, list):
            matched = next(
                (
                    entry
                    for entry in statuses
                    if isinstance(entry, dict) and entry.get("name") == container
                ),
                None,
            )
        container_view = None
        if matched is not None:
            restart_count = matched.get("restartCount")
            container_view = {
                "name": container,
                "image": _text(matched.get("image")),
                "image_digest": _image_digest(_text(matched.get("imageID"))),
                "container_id": _text(matched.get("containerID")),
                "restart_count": restart_count if isinstance(restart_count, int) and not isinstance(restart_count, bool) else None,
                "ready": matched.get("ready") is True,
                "running": isinstance(matched.get("state"), dict) and "running" in matched["state"],
            }
        pods.append(
            {
                "name": _text(metadata.get("name")),
                "uid": _text(metadata.get("uid")),
                "node": _text(spec.get("nodeName")),
                "phase": _text(status.get("phase")),
                "start_time": _text(status.get("startTime")),
                "deleting": metadata.get("deletionTimestamp") is not None,
                "container": container_view,
            }
        )
    pods.sort(key=lambda pod: pod["name"] or "")
    # Keep finished Pods in the observation, but never treat them as current targets.
    live = [
        pod for pod in pods
        if not pod["deleting"] and pod["phase"] not in {"Failed", "Succeeded"}
    ]
    if not live:
        state, current = "not_found", None
    elif len(live) > 1:
        # Several Pods match: report the conflict instead of picking one.
        state, current = "multiple_targets", None
    elif live[0]["container"] is None:
        state, current = "container_missing", None
    elif not live[0]["uid"] or not live[0]["container"]["container_id"] or not live[0]["container"]["running"]:
        state, current = "not_running", None
    else:
        state = "ok"
        pod = live[0]
        current = {
            "instance_id": instance_id(pod["uid"], pod["container"]["container_id"]),
            "pod": pod["name"],
            "pod_uid": pod["uid"],
            "node": pod["node"],
            "container": pod["container"]["name"],
            "container_id": pod["container"]["container_id"],
            "restart_count": pod["container"]["restart_count"],
            "image": pod["container"]["image"],
            "image_digest": pod["container"]["image_digest"],
        }
    return {"state": state, "pods": pods, "instance": current}


class KubernetesTargetCollector:
    """Read-only Pod lookup for the registered selector (fixed argv, no shell)."""

    def __init__(
        self,
        selector: KubernetesSelector,
        *,
        timeout_s: float,
        context: str | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.selector = selector
        if context is not None and context != selector.context:
            raise ValueError("kube context must match the target registration kubernetes.context")
        self.context = validate_kube_context(selector.context)
        self.timeout_s = timeout_s
        self.runner = runner

    def collect(self) -> tuple[dict[str, Any], str]:
        result = self.runner(
            pods_command(self.selector, self.context),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=self.timeout_s,
            check=False,
            shell=False,
        )
        if result.returncode != 0:
            raise OSError("Pod query failed")
        try:
            document = json.loads(result.stdout)
        except (json.JSONDecodeError, RecursionError) as error:
            raise ValueError("Pod response is not valid JSON") from error
        summary = summarize_pods(document, self.selector.container)
        summary["context"] = self.context
        summary["namespace"] = self.selector.namespace
        summary["selector"] = dict(self.selector.selector)
        return summary, "kubernetes_api"


class TargetPodSnapshotCollector:
    """Read a host-produced Kubernetes Pod snapshot without following links."""

    def __init__(
        self,
        path: str | Path,
        selector: KubernetesSelector,
        *,
        stale_after_s: float,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if stale_after_s <= 0:
            raise ValueError("target Pod snapshot stale window must be positive")
        self.path = Path(path).expanduser()
        self.selector = selector
        self.stale_after_s = float(stale_after_s)
        self.now = now

    def _read(self) -> bytes:
        descriptor = os.open(
            self.path,
            os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW,
        )
        try:
            information = os.fstat(descriptor)
            if not stat.S_ISREG(information.st_mode):
                raise OSError("target Pod snapshot is not a regular file")
            if information.st_size > POD_SNAPSHOT_MAX_BYTES:
                raise ValueError("target Pod snapshot exceeds 4 MiB")
            chunks: list[bytes] = []
            remaining = POD_SNAPSHOT_MAX_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > POD_SNAPSHOT_MAX_BYTES:
                raise ValueError("target Pod snapshot exceeds 4 MiB")
            return raw
        finally:
            os.close(descriptor)

    @staticmethod
    def _timestamp(value: object) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError("target Pod snapshot collected_at is invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("target Pod snapshot collected_at is invalid") from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("target Pod snapshot collected_at has no timezone")
        return parsed.astimezone(timezone.utc)

    def collect(self) -> tuple[dict[str, Any], str]:
        raw = self._read()
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_object_without_duplicates,
                parse_constant=_reject_json_constant,
            )
        except DuplicateKeyError as error:
            raise ValueError("target Pod snapshot has a duplicate JSON key") from error
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as error:
            raise ValueError("target Pod snapshot is not valid strict JSON") from error
        fields = {
            "schema",
            "context",
            "namespace",
            "selector",
            "collected_at",
            "state",
            "error",
            "items",
        }
        if not isinstance(document, dict) or set(document) - fields:
            raise ValueError("target Pod snapshot fields are invalid")
        if document.get("schema") != POD_SNAPSHOT_SCHEMA:
            raise ValueError("target Pod snapshot schema is invalid")
        expected_selector = dict(self.selector.selector)
        if (
            document.get("context") != self.selector.context
            or document.get("namespace") != self.selector.namespace
            or document.get("selector") != expected_selector
        ):
            raise ValueError("target Pod snapshot does not match the target registration")
        collected_at = self._timestamp(document.get("collected_at"))
        state = document.get("state")
        if state not in {"ok", "error"}:
            raise ValueError("target Pod snapshot state is invalid")
        if state == "error":
            error_value = document.get("error")
            if (
                not isinstance(error_value, dict)
                or set(error_value) != {"class", "code"}
                or not all(
                    isinstance(error_value.get(name), str)
                    and _ERROR_TOKEN.fullmatch(error_value[name]) is not None
                    for name in ("class", "code")
                )
                or "items" in document
            ):
                raise ValueError("target Pod snapshot error fields are invalid")
            raise OSError(
                f"target Pod snapshot writer error ({error_value['code']})"
            )
        if document.get("error") is not None or not isinstance(document.get("items"), list):
            raise ValueError("target Pod snapshot ok fields are invalid")
        age_s = (self.now().astimezone(timezone.utc) - collected_at).total_seconds()
        if age_s > self.stale_after_s:
            raise OSError("target Pod snapshot is stale")
        summary = summarize_pods({"items": document["items"]}, self.selector.container)
        summary.update(
            {
                "context": self.selector.context,
                "namespace": self.selector.namespace,
                "selector": expected_selector,
                "collected_at": document["collected_at"],
                "origin_note": "host_snapshot",
            }
        )
        # The host file is only a credential-free transport. The values still
        # come from a real kubectl API read, so the contracts grade remains
        # MEASURED through the existing kubernetes_api origin mapping.
        return summary, "kubernetes_api"


class TargetRegistry:
    """Hold the registration, track observed instances, and compare values."""

    def __init__(
        self,
        registration: TargetRegistration,
        *,
        data_dir: str | Path,
        observation_session_id: str,
    ) -> None:
        self.registration = registration
        self.observation_session_id = observation_session_id
        self.store = JsonlStore(Path(data_dir) / "target_snapshots.jsonl")
        self._lock = threading.Lock()
        self._current: dict[str, Any] | None = None
        self._k8s_state = "not_collected"
        self._observed_at: str | None = None
        self._events_this_session = 0
        self._store_error: str | None = None
        # The last instance written to the history, possibly by an earlier run.
        self._last_recorded = self._last_recorded_instance()

    def _last_recorded_instance(self) -> dict[str, Any] | None:
        try:
            records, _corrupt = self.store.read_all()
        except OSError:
            return None
        for record in reversed(records):
            if record.get("target_id") == self.registration.target_id:
                instance = record.get("instance")
                return instance if isinstance(instance, dict) else None
        return None

    def gateway_decision(self, policy_target_id: str | None) -> tuple[str, str] | None:
        """Return (reason_code, message) when requests must be held, else None."""

        if self.registration.state == "not_configured":
            return None
        if self.registration.state != "ok":
            return ("TARGET_UNAVAILABLE", "target registration is unavailable")
        if policy_target_id != self.registration.target_id:
            return ("TARGET_MISMATCH", "policy target does not match the registered target")
        return None

    def observe_kubernetes(self, value: Any, _data_origin: str | None) -> None:
        if not isinstance(value, dict):
            return
        state = value.get("state")
        instance = value.get("instance") if isinstance(value.get("instance"), dict) else None
        now = _utc_now()
        with self._lock:
            self._k8s_state = state if isinstance(state, str) else "unknown"
            self._observed_at = now
            self._current = instance
            if instance is None:
                return
            previous = self._last_recorded
            new_id = instance["instance_id"]
            old_id = previous.get("instance_id") if previous else None
            if new_id == old_id:
                if self._events_this_session:
                    return
                # Same instance as the last run: record once that this session
                # re-confirmed it, so the history shows the session boundary.
                event = "instance_confirmed"
            else:
                event = "instance_started" if old_id is None else "instance_changed"
            record = {
                "schema_version": SNAPSHOT_SCHEMA,
                "event": event,
                "recorded_at": now,
                "observation_session_id": self.observation_session_id,
                "target_id": self.registration.target_id,
                "instance": instance,
                "previous_instance_id": old_id,
                "reason": (
                    _change_reason(previous, instance)
                    if event == "instance_changed"
                    else None
                ),
            }
            try:
                self.store.append(record)
            except OSError as error:
                # Preserve the last durable cursor so the next observation of
                # this same instance retries the missing history record.
                self._store_error = type(error).__name__
                return
            self._store_error = None
            self._events_this_session += 1
            self._last_recorded = instance

    def current_instance(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._current) if self._current else None

    def record_context(self) -> dict[str, Any]:
        """Target fields attached to every request record."""

        with self._lock:
            current = self._current
            k8s_state = self._k8s_state
        return {
            "registration": self.registration.state,
            "target_id": self.registration.target_id,
            "instance_id": current["instance_id"] if current else None,
            "instance_state": (
                k8s_state if self.registration.kubernetes is not None else "not_configured"
            ),
        }

    def hold(
        self,
        *,
        policy_target_id: str | None,
        backend_kind: str,
        engine_info: dict[str, Any] | None,
        kubernetes: dict[str, Any] | None,
    ) -> tuple[str, str] | None:
        """Full admission check: registration, then observed-value comparison."""

        decision = self.gateway_decision(policy_target_id)
        if decision is not None or self.registration.state != "ok":
            return decision
        rows = self._comparison_rows(
            policy_target_id=policy_target_id,
            backend_kind=backend_kind,
            engine_info=engine_info,
            kubernetes=kubernetes,
        )
        return comparison_hold(
            rows,
            kubernetes_declared=self.registration.kubernetes is not None,
            kubernetes=kubernetes,
        )

    def _comparison_rows(
        self,
        *,
        policy_target_id: str | None,
        backend_kind: str,
        engine_info: dict[str, Any] | None,
        kubernetes: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        registration = self.registration
        rows: list[dict[str, Any]] = []
        if registration.state == "ok":
            rows.append(_row("policy.target_id", registration.target_id, policy_target_id, "policy", True))
            rows.append(_row("engine.kind", registration.engine_kind, backend_kind, "backend_config", True))
            collector_state = engine_info.get("state") if engine_info else "disconnected"
            value = engine_info.get("last_value") if engine_info else None
            for name, expected in (("engine.version", registration.engine_version),):
                if collector_state != "ok" or not isinstance(value, dict):
                    rows.append(_unknown(name, expected, "engine_api", f"engine_info {collector_state}"))
                elif value.get(name) is None:
                    rows.append(_unknown(name, expected, "engine_api", "not reported by engine"))
                else:
                    rows.append(_row(name, expected, value.get(name), "engine_api", collector_state == "ok"))
            if collector_state != "ok" or not isinstance(value, dict):
                rows.append(
                    _unknown(
                        "model.id",
                        registration.model_id,
                        "engine_api",
                        f"engine_info {collector_state}",
                    )
                )
            else:
                model_ids = value.get("model.ids")
                if isinstance(model_ids, list) and all(
                    isinstance(item, str) for item in model_ids
                ):
                    rows.append(
                        _membership_row(
                            "model.id",
                            registration.model_id,
                            model_ids,
                            "engine_api",
                        )
                    )
                elif value.get("model.id") is not None:
                    rows.append(
                        _row(
                            "model.id",
                            registration.model_id,
                            value.get("model.id"),
                            "engine_api",
                            True,
                        )
                    )
                else:
                    rows.append(
                        _unknown(
                            "model.id",
                            registration.model_id,
                            "engine_api",
                            "not reported by engine",
                        )
                    )
            rows.append(
                _unknown(
                    "model.revision",
                    registration.model_revision,
                    "engine_api",
                    "engine API does not expose the revision; use the asset manifest",
                )
            )
            if registration.kubernetes is not None:
                snapshot_instance = (
                    kubernetes.get("instance")
                    if isinstance(kubernetes, dict)
                    and isinstance(kubernetes.get("instance"), dict)
                    else None
                )
                instance = snapshot_instance or self.current_instance()
                k8s_state = kubernetes.get("state") if kubernetes else "disconnected"
                if instance is None:
                    rows.append(_unknown("engine.image_digest", registration.image_digest, "kubernetes_api", f"pod snapshot {k8s_state}"))
                else:
                    rows.append(_row("engine.image_digest", registration.image_digest, instance.get("image_digest"), "kubernetes_api", True))
        return rows

    def status(
        self,
        *,
        policy_target_id: str | None,
        backend_kind: str,
        engine_info: dict[str, Any] | None,
        kubernetes: dict[str, Any] | None,
        history_limit: int = 20,
    ) -> dict[str, Any]:
        registration = self.registration
        rows = self._comparison_rows(
            policy_target_id=policy_target_id,
            backend_kind=backend_kind,
            engine_info=engine_info,
            kubernetes=kubernetes,
        )
        try:
            records, corrupt = self.store.read_all()
        except OSError:
            records, corrupt = [], []
        history = [
            record for record in records if record.get("target_id") == registration.target_id
        ][-history_limit:]
        counts = {"match": 0, "mismatch": 0, "unknown": 0, "not_declared": 0}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        with self._lock:
            tracker = {
                "state": self._k8s_state if registration.kubernetes else "not_configured",
                "observed_at": self._observed_at,
                "current": dict(self._current) if self._current else None,
                "events_this_session": self._events_this_session,
            }
        hold = self.gateway_decision(policy_target_id)
        if hold is None and registration.state == "ok":
            hold = comparison_hold(
                rows,
                kubernetes_declared=registration.kubernetes is not None,
                kubernetes=kubernetes,
            )
        return {
            "registration": registration.view(),
            "gateway_hold": {"reason_code": hold[0], "message": hold[1]} if hold else None,
            "comparison": rows,
            "counts": counts,
            "kubernetes": kubernetes,
            "instance": tracker,
            "history": list(reversed(history)),
            "history_store": {
                "path": str(self.store.path),
                "corrupt_lines": len(corrupt),
                "last_error": self._store_error,
            },
            "limits": [
                "model.id 일치는 weight 해시·revision·실제 로딩을 증명하지 않음",
                "Pod 관측은 대상 등록의 kubernetes.context에서 직접 읽기 전용 조회하거나 같은 API 조회의 host snapshot을 읽음",
                "선언한 값이 다르거나 확인되지 않으면 신규 요청을 503으로 보류(model.revision은 engine API로 확인할 수 없어 보류 조건에서 제외, 자산 manifest로 확인)",
            ],
        }


# The engine API cannot expose these by design; they are checked through the
# asset manifest and do not gate requests on their own.
_NON_GATING_FIELDS = frozenset({"model.revision"})


def comparison_hold(
    rows: list[dict[str, Any]],
    *,
    kubernetes_declared: bool,
    kubernetes: dict[str, Any] | None,
) -> tuple[str, str] | None:
    """Hold new requests when a declared value differs or cannot be confirmed.

    Plan §4.1/§7.3: a confirmed target mismatch, multiple matching targets or
    missing metadata are not estimated; admission waits (503) until fixed.
    """

    gating = [row for row in rows if row["field"] not in _NON_GATING_FIELDS]
    for row in gating:
        if row["state"] == "mismatch":
            return ("TARGET_MISMATCH", f"observed {row['field']} differs from the registered target")
    if kubernetes_declared:
        state = kubernetes.get("state") if isinstance(kubernetes, dict) else None
        if state != "ok":
            return ("TARGET_UNAVAILABLE", f"target pod snapshot is {state or 'not collected'}")
    for row in gating:
        if row["state"] == "unknown":
            return ("TARGET_UNVERIFIED", f"{row['field']} could not be confirmed ({row['note'] or 'not observed'})")
    return None


def _row(name: str, expected: Any, observed: Any, source: str, observed_ok: bool) -> dict[str, Any]:
    if expected is None:
        state = "not_declared"
    elif not observed_ok or observed is None:
        state = "unknown"
    else:
        state = "match" if expected == observed else "mismatch"
    return {"field": name, "expected": expected, "observed": observed, "source": source, "state": state, "note": None}


def _membership_row(
    name: str, expected: Any, observed: list[str], source: str
) -> dict[str, Any]:
    if expected is None:
        state = "not_declared"
    else:
        state = "match" if expected in observed else "mismatch"
    return {
        "field": name,
        "expected": expected,
        "observed": list(observed),
        "source": source,
        "state": state,
        "note": None,
    }


def _unknown(name: str, expected: Any, source: str, note: str) -> dict[str, Any]:
    return {
        "field": name,
        "expected": expected,
        "observed": None,
        "source": source,
        "state": "not_declared" if expected is None else "unknown",
        "note": note,
    }


def _change_reason(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> str | None:
    if previous is None or current is None:
        return None
    if previous.get("pod_uid") != current.get("pod_uid"):
        return "pod_replaced"
    if previous.get("container_id") != current.get("container_id"):
        return "container_restarted"
    return None
