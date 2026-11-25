"""Offline plan for the bounded, ordinary SUP-05 first-import handoff."""

from pathlib import Path
import re

from guard.adapters import vllm_approved_file_read_composition as file_reads
from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.collectors.lifecycle import _observer_bootstrap
from guard.models import canonical_json, sha256_hex
from tools import gpu_a_sup05_first_import_runtime as waiter


SCHEMA = "guard-sup05-first-import/1"
ASSET_VOLUME = "guard-sup05-assets"
DATA_VOLUME = "guard-sup05-data"
IDENTITY = {"namespace": "metadata.namespace", "pod": "metadata.name", "pod_uid": "metadata.uid", "node": "spec.nodeName"}
POD_FIELDS = {key: "GUARD_SUP05_" + key.upper() for key in IDENTITY}
NONROOT_WRAPPER = "/usr/local/bin/vllm-nonroot-entrypoint.sh"
NONROOT_WRAPPER_SHA256 = "123bda914b7b5c59e3b37a2667ff62e64be26a49f4dd58435c76152e10090765"
_DIGEST_PLACEHOLDER = sha256_hex(b"guard-sup05-first-import-scope-placeholder")


def _fragment(raw):
    needle = _DIGEST_PLACEHOLDER.encode("ascii")
    if raw.count(needle) != 1:
        raise ValueError("SUP05_FIRST_IMPORT_SOURCE_UNAVAILABLE")
    before, after = raw.split(needle)
    return len(before), sha256_hex(before), sha256_hex(after)


def expected_launch(version, run_id, launch):
    """Bind by exact existing launch and by the known generated code around its one digest."""
    from guard.adapters.engine_provider import VllmProvider
    from guard.adapters.vllm_lifecycle_sensor import CONFIG_SCHEMA

    release = VllmProvider().release_profile(version)
    profile = file_reads.PROFILES.get(version)
    if (not catalog.approved_file_read(profile) or release is None or launch["engine"] != "V1"
            or launch["runner"] != "v1" or release["launch"]["command"] != launch["command"]):
        raise ValueError("SUP05_FIRST_IMPORT_NORMAL_LAUNCH_REQUIRED")
    return {"abi": file_reads.RUNTIME, "version": version, "install_root": file_reads.INSTALL_ROOT,
            "profile": profile, "run_id": run_id,
            "command": launch["command"], "argv": launch["argv"], "identity": POD_FIELDS,
            "wrapper": (NONROOT_WRAPPER, NONROOT_WRAPPER_SHA256) if launch["command"] == [NONROOT_WRAPPER] else None,
            "launch_sha256": sha256_hex(canonical_json(launch)), "settings_schema": CONFIG_SCHEMA,
            "assets": sorted(catalog.asset_names(profile)),
            "fixed": {"guard_lifecycle_base.py": catalog.runtime_spec(profile)["base_sha256"],
                      file_reads.PRIMITIVE_FILE: file_reads.PRIMITIVE_SHA256},
            "sensor": _fragment(catalog.render_source(profile, model_read_scope_sha256=_DIGEST_PLACEHOLDER)),
            "bootstrap": _fragment(_observer_bootstrap(_DIGEST_PLACEHOLDER))}


def pod_command(expected):
    raw = Path(waiter.__file__).read_text(encoding="utf-8")
    return ["python3", "-I", "-S", "-B", "-c", raw + "\nlaunch(" + repr(expected) + ")\n"]


def identity_env():
    return [{"name": POD_FIELDS[key], "valueFrom": {"fieldRef": {"apiVersion": "v1", "fieldPath": field}}}
            for key, field in IDENTITY.items()]


def mounts():
    return [{"name": ASSET_VOLUME, "mountPath": waiter.ROOT, "readOnly": True},
            {"name": DATA_VOLUME, "mountPath": waiter.DATA}]


def volumes(directories):
    return [{"name": name, "hostPath": {"path": directories[name], "type": "Directory"}}
            for name in (ASSET_VOLUME, DATA_VOLUME)]


def _mounts_sha256(pod, engine):
    return sha256_hex(canonical_json({"mounts": engine["volumeMounts"], "volumes": pod["spec"]["volumes"]}))


def render_hold(pod, engine, *, version, run_id, launch, run_root):
    expected = expected_launch(version, run_id, launch)
    engine["command"] = pod_command(expected)
    engine["env"] += identity_env()
    directories = {ASSET_VOLUME: run_root + "/sup05-lifecycle", DATA_VOLUME: run_root + "/sup05-lifecycle-data"}
    engine["volumeMounts"] += mounts()
    pod["spec"]["volumes"] += volumes(directories)
    return {"schema": SCHEMA, "profile": expected["profile"], "host_directories": directories,
            "command_sha256": sha256_hex(canonical_json(engine["command"])),
            "environment_sha256": sha256_hex(canonical_json(engine["env"])),
            "mounts_sha256": _mounts_sha256(pod, engine)}


def verified_command(render, pod, engine, namespace):
    """Check only stable, ordinary hold changes against the separately pinned render."""
    hold = render["sup05_first_import"]
    expected = expected_launch(render["engine_version"], render["run_id"], render["launch"])
    command = pod_command(expected)
    directories = hold["host_directories"]
    pod_spec = pod["spec"]
    assets = Path(directories[ASSET_VOLUME])
    data = Path(directories[DATA_VOLUME])
    if (hold["schema"] != SCHEMA or hold["profile"] != expected["profile"]
            or hold["command_sha256"] != sha256_hex(canonical_json(command)) or engine["command"] != command
            or hold["environment_sha256"] != sha256_hex(canonical_json(engine["env"]))
            or hold["mounts_sha256"] != _mounts_sha256(pod, engine)
            or any(row not in engine["env"] for row in identity_env()) or engine.get("envFrom")
            or assets.name != "sup05-lifecycle" or data.name != "sup05-lifecycle-data"
            or not assets.is_absolute() or ".." in assets.parts or assets.parent != data.parent
            or assets.parent.name != render["run_id"]
            or [row for row in engine["volumeMounts"] if row["name"] in directories] != mounts()
            or [row for row in pod_spec["volumes"] if row["name"] in directories] != volumes(directories)
            or engine["workingDir"] != "/cache" or engine["securityContext"] != {
                "allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}}
            or pod_spec["securityContext"] != {"runAsUser": 2000, "runAsGroup": 0, "runAsNonRoot": True,
                                              "seccompProfile": {"type": "RuntimeDefault"}}
            or pod_spec.get("automountServiceAccountToken") is not False
            or pod_spec.get("shareProcessNamespace") or pod_spec.get("initContainers")
            or pod_spec.get("ephemeralContainers") or len(pod_spec["containers"]) != 1
            or namespace["metadata"].get("labels", {}).get("byul.lab/owned") != "true"):
        raise ValueError("SUP05_FIRST_IMPORT_POD_UNVERIFIED")
    return command


def verify_waiting(render, engine, waiting_raw):
    expected = expected_launch(render["engine_version"], render["run_id"], render["launch"])
    waiting = waiter._json(waiting_raw)
    owner = {key: engine[key] for key in IDENTITY}
    if (type(waiting) is not dict or waiting.keys() != {
            "schema", "owner", "run_id", "pid", "nonce", "launch_sha256", "observed_engine_version"}
            or waiting["schema"] != waiter.WAITING_SCHEMA or type(waiting["pid"]) is not int or waiting["pid"] != 1
            or waiting["owner"] != owner or waiting["run_id"] != expected["run_id"]
            or waiting["launch_sha256"] != expected["launch_sha256"]
            or waiting["observed_engine_version"] != expected["version"]
            or type(waiting["nonce"]) is not str or re.fullmatch("[a-f0-9]{32}", waiting["nonce"]) is None):
        raise ValueError("SUP05_FIRST_IMPORT_OWNER_UNVERIFIED")
    return expected, waiting["observed_engine_version"]


def release_document(expected, waiting_raw, verified):
    """Convert only the existing checked publication into the fixed waiter's input."""
    release = {"schema": waiter.RELEASE_SCHEMA, "waiting_sha256": sha256_hex(waiting_raw),
               "profile": verified["profile"], "session": verified["session"],
               "manifest_sha256": verified["prepared_file_sha256"],
               "environment_ref": verified["environment_ref"], "target_sha256": verified["target_sha256"]}
    raw = canonical_json(release)
    waiter.verify_release(expected, waiting_raw, raw, verified["files"])
    return raw
