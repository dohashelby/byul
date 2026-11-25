"""One requested-configuration merge for preparation, selection and execution."""
from copy import deepcopy
import json
from pathlib import Path


def validate_requested_config(config):
    """Requested inputs cannot set keys reserved for product runtime state."""
    def validate(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str) or key.startswith("_"):
                    raise ValueError("REQUESTED_CONFIG_RESERVED_KEY")
                validate(item)
        elif isinstance(value, list):
            for item in value:
                validate(item)

    if not isinstance(config, dict):
        raise ValueError("REQUESTED_CONFIG_MAPPING_REQUIRED")
    validate(config)
    return config


def merge_config(base, override):
    out = deepcopy(base)
    for key, value in override.items():
        out[key] = merge_config(out.get(key, {}), value) if isinstance(value, dict) else deepcopy(value)
    return out


CHECK_CONFIG = "check_config"
NORMAL_PYTHON_MODE_OPTION = "normal_python_mode_only"
NORMAL_PYTHON_MODE_SCOPE_ERROR = "SUP11_STOCK_MODE_SCOPE_INVALID"


def _python_mode_options(value, path=()):
    if isinstance(value, dict):
        for key, item in value.items():
            location = (*path, key)
            if key == NORMAL_PYTHON_MODE_OPTION:
                yield location, item
            else:
                yield from _python_mode_options(item, location)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _python_mode_options(item, (*path, index))


def normal_python_mode_requested(config):
    return next(_python_mode_options(config), None) is not None


def validate_normal_python_mode_update(config):
    options = dict(_python_mode_options(config))
    if not options:
        return
    locations = {("sides", side, CHECK_CONFIG, "SUP-11", NORMAL_PYTHON_MODE_OPTION)
                 for side in ("baseline", "candidate")}
    update = config.get("update", {})
    if (set(options) != locations or any(value is not True for value in options.values())
            or type(update) is not dict
            or update.get("mode", "external") != "external"
            or update.get("kubernetes_candidate_binding", "prebound") != "prebound"
            or update.get("allowed_checks") not in (None, ["SUP-11"])):
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    settings = [merge_config(config, config["sides"][side]) for side in ("baseline", "candidate")]
    entries = [for_check(side, "supply_boot", "SUP-11")["supply_boot"] for side in settings]
    if (any(side.get("executors") != {"supply_boot": "kubernetes"} or side.get("record_attestation")
            for side in settings)
            or any(type(entry.get(key)) is not str or not entry[key]
                   for entry in entries for key in ("target", "provenance"))
            or any(Path(entries[0][key]).resolve() == Path(entries[1][key]).resolve()
                   for key in ("target", "provenance"))):
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)

# Host inputs consumed by executors or run-binding validation. Remote paths
# such as usage_sink.path and native_observation_path are bound by the target.
_INPUT_FILES = frozenset({
    "target", "gateway_target", "route_target", "receiver_target", "sink_target", "identity_target",
    "profiler_target", "prepared", "worker_prepared", "identity_prepared", "profiler_prepared", "manifest",
    "copy_inventory", "retry_scope", "subjects", "selection", "scenario", "phase_generation",
    "api_binding", "egress_binding", "issuer_event", "record_producer", "engine_producer", "gateway_producer",
})
_MANIFEST_FILES = frozenset({"manifest", "prepared", "worker_prepared", "identity_prepared", "profiler_prepared"})
_RECORD_INPUTS = frozenset({
    "b_common_input", "common_inputs", "cpu_support_input", "worker_shard_input", "supply_reads_input",
    "record_attestation", "runner_consumption", "runner_input",
})


def requested_input_paths(config, *, base_dir=None):
    """Yield approved local input paths and whether discovery is deferred."""
    validate_requested_config(config)
    deferred_paths = set()
    if config.get("update", {}).get("kubernetes_candidate_binding") == "after_baseline":
        candidate = merge_config(config, config.get("sides", {}).get("candidate", {}))
        implementations = candidate.get("executors", {})
        for executor in ("request_binding", "input_validation"):
            path = candidate.get(executor, {}).get("target")
            if (isinstance(path, (str, Path))
                    and (not implementations or implementations.get(executor) == "kubernetes")):
                deferred_paths.add(Path(path))

    def manifest_assets(path):
        # A prepared manifest is already validated by its owner at runtime.
        # Commit the declared child files here too so that changed assets
        # invalidate the preparation before a new execution starts.
        source = Path(base_dir) / path if base_dir is not None else path
        if source.is_file():
            with source.open("rb") as stream:
                raw = stream.read(262145)
            if len(raw) > 262144:
                return
            try:
                manifest = json.loads(raw)
            except (ValueError, UnicodeError):
                return
            if not isinstance(manifest, dict):
                return
            assets = manifest.get("assets")
            names = set(assets) if isinstance(assets, dict) else set()
            if manifest.get("schema") == "byul-engine-callback-cohort-group/1" and isinstance(manifest.get("parts"), list):
                names.update(part["file"] for part in manifest["parts"]
                             if isinstance(part, dict) and isinstance(part.get("file"), str))
            for name in sorted(names):
                if name not in {"", ".", ".."} and Path(name).name == name:
                    yield path.parent / name

    def walk(value, location=()):
        if isinstance(value, dict):
            for key, item in value.items():
                field = (*location, key)
                records = any(part in _RECORD_INPUTS for part in location)
                if isinstance(item, (str, Path)):
                    if (key in _INPUT_FILES or key == "path" and records
                            or key == "provenance" and location[-2:] == (CHECK_CONFIG, "SUP-11")
                            or key == "environment" and "generations" in location):
                        path = Path(item)
                        deferred = (key == "target" and location[:2] == ("sides", "candidate")
                                    and path in deferred_paths)
                        yield field, path, deferred
                        if key in _MANIFEST_FILES:
                            for asset in manifest_assets(path):
                                yield (*field, "assets", asset.name), asset, False
                    elif key == "prepared_dir" and "native_input_capture" in location:
                        directory = Path(item)
                        manifest = directory / "manifest.json"
                        yield (*field, "manifest.json"), manifest, False
                        yield (*field, "settings.json"), directory / "settings.json", False
                        for asset in manifest_assets(manifest):
                            yield (*field, "assets", asset.name), asset, False
                    elif records and location[-1:] == ("records",):
                        yield field, Path(item), False
                else:
                    yield from walk(item, field)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                field = (*location, index)
                if (isinstance(item, (str, Path)) and location[-1:] == ("records",)
                        and any(part in _RECORD_INPUTS for part in location)):
                    yield field, Path(item), False
                else:
                    yield from walk(item, field)

    yield from walk(config)


def for_check(raw, executor, check_id):
    """Run config as one check sees it: `[check_config.<CHECK>]` merged over its executor section.

    Several checks share one executor (LIF-05/06/16, OBS-01/PLT-12, LIF-07/09);
    a per-check entry lets one `guard update` give each its own target,
    prepared or generations (RB2-S, 2026-11-14 user decision).  The merge is the
    same as for sides (tables merge, other values replace) and touches only
    that check's executor section.  The table stays in the requested config, so
    it is part of the PLAN execution recipe hash.
    """
    table = (raw or {}).get(CHECK_CONFIG)
    if table is None:
        return raw
    if type(table) is not dict or any(type(k) is not str or type(v) is not dict for k, v in table.items()):
        raise ValueError("CHECK_CONFIG_INVALID")
    entry = table.get(check_id)
    if not entry:
        return raw
    section = raw.get(executor, {})
    if type(section) is not dict:
        raise ValueError("CHECK_CONFIG_INVALID")
    out = dict(raw)
    out[executor] = merge_config(section, entry)
    return out
