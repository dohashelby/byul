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


def for_side(config, side):
    """Resolve the requested settings for one side without mutating the request."""
    return merge_config(config, config.get("sides", {}).get(side, {}))


CHECK_CONFIG = "check_config"
UPDATE_CHECK_OUTSIDE_SCOPE = "UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE"
GATEWAY_RECORD_SNAPSHOT_OPTION = "gateway_record_snapshot_only"
CILIUM_EGRESS_STATUS_OPTION = "cilium_egress_status_only"
CILIUM_EGRESS_STATUS_SCOPE_ERROR = "CILIUM_EGRESS_STATUS_EXECUTION_SCOPE_INVALID"
INSTALLED_PLUGIN_INVENTORY_OPTION = "installed_plugin_inventory_only"
INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR = "INSTALLED_PLUGIN_INVENTORY_EXECUTION_SCOPE_INVALID"
NORMAL_STREAM_OPTION = "normal_stream_only"
NORMAL_SAMPLING_OPTION = "normal_sampling_only"
NORMAL_TOKEN_INPUT_OPTION = "normal_token_input_only"
NORMAL_REQUEST_LATENCY_OPTION = "normal_request_latency_only"
NORMAL_REQUEST_LATENCY_SCOPE_ERROR = "NORMAL_REQUEST_LATENCY_EXECUTION_SCOPE_INVALID"
NORMAL_COMPILE_CACHE_OPTION = "normal_compile_cache_only"
NORMAL_COMPILE_CACHE_SCOPE_ERROR = "NORMAL_COMPILE_CACHE_EXECUTION_SCOPE_INVALID"
NORMAL_FILE_READS_OPTION = "normal_file_reads_only"
NORMAL_FILE_READS_SCOPE_ERROR = "NORMAL_FILE_READS_EXECUTION_SCOPE_INVALID"
SUP05_CONFIG_PARSER_OPTION = "normal_approved_config_parser_only"
BOOT_IDENTITY_OPTIONS = {"SUP-11": "normal_python_runtime_only", "SUP-09": "normal_model_class_origin_only"}
BOOT_IDENTITY_SCOPE_ERROR = "NORMAL_BOOT_IDENTITY_EXECUTION_SCOPE_INVALID"
SUP05_PREPARATION_RECEIPT = "preparation_receipt"
NORMAL_GRAPH_CAPTURE_OPTION = "normal_graph_capture_only"
NORMAL_GRAPH_CAPTURE_SCOPE_ERROR = "NORMAL_GRAPH_CAPTURE_EXECUTION_SCOPE_INVALID"
NORMAL_SAFETENSORS_READER_OPTION = "normal_safetensors_reader_only"
NORMAL_SAFETENSORS_READER_SCOPE_ERROR = "NORMAL_SAFETENSORS_READER_EXECUTION_SCOPE_INVALID"
NORMAL_SERVING_READINESS_OPTION = "normal_serving_readiness_only"
NORMAL_SERVING_READINESS_SCOPE_ERROR = "NORMAL_SERVING_READINESS_EXECUTION_SCOPE_INVALID"
NORMAL_STARTUP_LOAD_OPTION = "normal_startup_load_only"
NORMAL_STARTUP_LOAD_SCOPE_ERROR = "NORMAL_STARTUP_LOAD_EXECUTION_SCOPE_INVALID"
_NORMAL_ONLY_CASES = {
    "LIF-01": ("supply_boot", NORMAL_STARTUP_LOAD_OPTION, NORMAL_STARTUP_LOAD_SCOPE_ERROR),
    "LIF-03": ("stream_buffer", NORMAL_REQUEST_LATENCY_OPTION, NORMAL_REQUEST_LATENCY_SCOPE_ERROR),
    "LIF-04": ("stream_buffer", NORMAL_STREAM_OPTION, "NORMAL_STREAM_EXECUTION_SCOPE_INVALID"),
    "OBS-02": ("observation_state", NORMAL_SERVING_READINESS_OPTION, NORMAL_SERVING_READINESS_SCOPE_ERROR),
    "PLT-07": ("boundary_egress", CILIUM_EGRESS_STATUS_OPTION, CILIUM_EGRESS_STATUS_SCOPE_ERROR),
    "REQ-09": ("input_validation", NORMAL_TOKEN_INPUT_OPTION, "NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID"),
    "REQ-10": ("sampling_validation", NORMAL_SAMPLING_OPTION, "NORMAL_SAMPLING_EXECUTION_SCOPE_INVALID"),
    "REQ-20": ("engine_sensor", NORMAL_GRAPH_CAPTURE_OPTION, NORMAL_GRAPH_CAPTURE_SCOPE_ERROR),
    "SUP-05": ("supply_boot", NORMAL_FILE_READS_OPTION, NORMAL_FILE_READS_SCOPE_ERROR),
    "SUP-07": ("supply_boot", NORMAL_COMPILE_CACHE_OPTION, NORMAL_COMPILE_CACHE_SCOPE_ERROR),
    "SUP-12": ("supply_boot", NORMAL_SAFETENSORS_READER_OPTION, NORMAL_SAFETENSORS_READER_SCOPE_ERROR),
    "SUP-10": ("supply_boot", INSTALLED_PLUGIN_INVENTORY_OPTION, INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR),
    **{check: ("supply_boot", option, BOOT_IDENTITY_SCOPE_ERROR) for check, option in BOOT_IDENTITY_OPTIONS.items()},
}
RETRY_RECORD_SNAPSHOT_OPTION = "retry_record_snapshot_only"
RETRY_RECORD_SNAPSHOT_SCOPE_ERROR = "RETRY_RECORD_SNAPSHOT_SCOPE_INVALID"
COLLECTION_STATUS_SNAPSHOT_OPTION = "collection_status_snapshot_only"
COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR = "COLLECTION_STATUS_SNAPSHOT_SCOPE_INVALID"
RECORD_SNAPSHOT_OPTIONS = {
    "OBS-01": "record_snapshot_only",
    "PLT-12": "copy_record_snapshot_only",
}
KUBERNETES_BOUND_CHECK_OPTIONS = {
    **RECORD_SNAPSHOT_OPTIONS,
    "LIF-12": RETRY_RECORD_SNAPSHOT_OPTION,
    "OBS-11": COLLECTION_STATUS_SNAPSHOT_OPTION,
    **{check_id: option for check_id, (_, option, _) in _NORMAL_ONLY_CASES.items()},
    "OBS-04": "local_observation_only",
    "PLT-02": "protected_path_metadata_only",
    "PLT-06": "worker_trust_metadata_only",
    "PLT-08": "passive_from_plt01",
}
REVIEWED_NORMAL_OPTION = "reviewed_normal_campaign"
REVIEW_ALL_SELECTED = "review_all_selected_checks"
REVIEWED_NORMAL_CASES = {
    check: (executor, SUP05_CONFIG_PARSER_OPTION if check == "SUP-05" else option)
    for check, (executor, option, _) in _NORMAL_ONLY_CASES.items()
    if check in {"LIF-01", "LIF-03", "LIF-04", "OBS-02", "PLT-07", "REQ-09", "REQ-10", "REQ-20",
                 "SUP-05", "SUP-07", "SUP-09", "SUP-10", "SUP-11", "SUP-12"}
}
REVIEWED_NORMAL_CASES.update({
    "OBS-03": ("log_analysis", None),
    "OBS-04": ("boundary_egress", KUBERNETES_BOUND_CHECK_OPTIONS["OBS-04"]),
    "PLT-01": ("container_config", None),
    "PLT-02": ("unix_ipc", KUBERNETES_BOUND_CHECK_OPTIONS["PLT-02"]),
    "PLT-04": ("credential_exposure", None),
    "PLT-06": ("unix_ipc", KUBERNETES_BOUND_CHECK_OPTIONS["PLT-06"]),
    "PLT-08": ("api_transport", KUBERNETES_BOUND_CHECK_OPTIONS["PLT-08"]),
    "REQ-07": ("template_validation", "req07_normal_only"),
    "REQ-11": ("request_binding", "normal_request_binding_only"),
    "SUP-03": ("template_validation", None),
    "SUP-04": ("container_config", "node_image_verification"),
})


def reviewed_check_settings(check_id):
    _, option = REVIEWED_NORMAL_CASES[check_id]
    if option is None:
        return {}
    return {option: "local-containerd-v1" if check_id == "SUP-04" else True}


def reviewed_normal_executors(raw):
    """Admit only explicit normal modes with a shared owned target on each side."""
    options = raw.get("update", {})
    keys = {REVIEWED_NORMAL_OPTION, "mode", "allowed_checks", "kubernetes_candidate_binding"}
    if type(options) is not dict or set(options) not in (keys, keys | {REVIEW_ALL_SELECTED}):
        return set()
    review_all = REVIEW_ALL_SELECTED in options
    allowed = options.get("allowed_checks")
    if (review_all and options[REVIEW_ALL_SELECTED] is not True
            or options.get(REVIEWED_NORMAL_OPTION) is not True or options.get("mode") != "external"
            or options.get("kubernetes_candidate_binding") != "after_baseline"
            or type(allowed) is not list or not allowed
            or any(type(check) is not str for check in allowed)
            or not review_all and not set(allowed) <= REVIEWED_NORMAL_CASES.keys()
            or len(allowed) != len(set(allowed)) or ("PLT-08" in allowed and "PLT-01" not in allowed)
            or any(key in raw for key in ("lab_activation", "record_attestation", "run_binding"))):
        return set()
    runnable = reviewed_dispatch_scope(raw, frozenset(allowed))
    expected = {cid: settings for cid in runnable if (settings := reviewed_check_settings(cid))}
    executors = {REVIEWED_NORMAL_CASES[cid][0] for cid in runnable}
    if (raw.get(CHECK_CONFIG) != expected or raw.get("executors") != dict.fromkeys(executors, "kubernetes")):
        return set()
    targets = set()
    for name in executors:
        settings = raw.get(name)
        if (type(settings) is not dict or set(settings) != {"target"}
                or type(settings["target"]) is not str or not settings["target"]):
            return set()
        targets.add(settings["target"])
    return executors if len(targets) == 1 else set()


def reviewed_dispatch_scope(raw, declared_checks):
    """Keep the original mode checks scoped to their explicitly reviewed entry points."""
    update = raw.get("update", {}) if type(raw) is dict else None
    if type(update) is dict and update.get(REVIEW_ALL_SELECTED) is True and declared_checks is not None:
        return declared_checks & REVIEWED_NORMAL_CASES.keys()
    return declared_checks


def update_allowed_checks(raw, pack):
    """Read the common execution allowlist without changing PLAN selection."""
    sides = raw.get("sides", {})
    if type(sides) is not dict or any(type(side) is not dict for side in sides.values()):
        raise ValueError("UPDATE_SIDES_CONFIGURATION_INVALID")
    for side in sides.values():
        side_options = side.get("update", {})
        if type(side_options) is not dict:
            raise ValueError("UPDATE_CONFIGURATION_INVALID")
        if "allowed_checks" in side_options:
            raise ValueError("UPDATE_ALLOWED_CHECKS_MUST_BE_COMMON")
    options = raw.get("update", {})
    if type(options) is not dict:
        raise ValueError("UPDATE_CONFIGURATION_INVALID")
    if "allowed_checks" not in options:
        return None
    check_ids = options["allowed_checks"]
    if (type(check_ids) is not list
            or any(type(cid) is not str or cid not in pack.checks or pack.checks[cid].retired for cid in check_ids)
            or len(set(check_ids)) != len(check_ids)):
        raise ValueError("UPDATE_ALLOWED_CHECKS_INVALID")
    return frozenset(check_ids)

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


_STAGED_OBSERVER_OWNERS = {
    **{cid: executor for cid, (executor, _, _) in _NORMAL_ONLY_CASES.items() if cid != "SUP-05"},
    "OBS-04": "boundary_egress", "PLT-02": "unix_ipc", "PLT-06": "unix_ipc",
}
STAGED_PASSIVE_HOP_EXECUTORS = frozenset({"container_config", "api_transport"})


def staged_observer_executors(candidate):
    """Only explicitly scoped observers can bind a new candidate Pod after baseline."""
    options = candidate.get("update", {})
    if options.get("mode", "external") != "external" or options.get("kubernetes_candidate_binding") != "after_baseline":
        return set()
    if REVIEWED_NORMAL_OPTION in options:
        return reviewed_normal_executors(candidate)
    allowed = options.get("allowed_checks")
    overlays = candidate.get(CHECK_CONFIG, {})
    implementations = candidate.get("executors", {})
    if (type(allowed) is not list or not allowed or type(overlays) is not dict
            or type(implementations) is not dict or candidate.get("lab_activation") or candidate.get("record_attestation")):
        return set()
    if (len(allowed) == 2 and "PLT-01" in allowed and "PLT-08" in allowed
            and overlays == {"PLT-08": {KUBERNETES_BOUND_CHECK_OPTIONS["PLT-08"]: True}}
            and implementations == dict.fromkeys(STAGED_PASSIVE_HOP_EXECUTORS, "kubernetes")):
        control, passive = (candidate.get(name) for name in ("container_config", "api_transport"))
        if (type(control) is dict and control.keys() == {"target"} and isinstance(control["target"], (str, Path))
                and control["target"] and control == passive):
            return set(STAGED_PASSIVE_HOP_EXECUTORS)
        return set()
    if allowed == ["SUP-05"] and overlays == {"SUP-05": {SUP05_CONFIG_PARSER_OPTION: True}}:
        settings = candidate.get("supply_boot")
        if (implementations == {"supply_boot": "kubernetes"} and type(settings) is dict
                and set(settings) == {"target"} and type(settings["target"]) is str and settings["target"]):
            return {"supply_boot"}
        return set()
    executors = set()
    for cid in allowed:
        if type(cid) is not str or cid not in _STAGED_OBSERVER_OWNERS:
            return set()
        check_config = overlays.get(cid)
        option = KUBERNETES_BOUND_CHECK_OPTIONS[cid]
        if type(check_config) is not dict or set(check_config) != {option} or check_config[option] is not True:
            return set()
        executor = _STAGED_OBSERVER_OWNERS[cid]
        settings = candidate.get(executor)
        if implementations.get(executor) != "kubernetes" or type(settings) is not dict or set(settings) != {"target"}:
            return set()
        executors.add(executor)
    return executors


def requested_input_paths(config, *, base_dir=None):
    """Yield approved local input paths and whether discovery is deferred."""
    validate_requested_config(config)
    normal_file_reads = normal_file_reads_requested(config)
    deferred_paths = set()
    staged_observers = set()
    if config.get("update", {}).get("kubernetes_candidate_binding") == "after_baseline":
        candidate = for_side(config, "candidate")
        staged_observers = staged_observer_executors(candidate)
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
                    normal_reference = (normal_file_reads and key == "reference_manifest"
                                        and location[-2:] in {("supply_boot", "normal"), ("SUP-05", "normal")})
                    if (key in _INPUT_FILES or normal_reference or key == "path" and records
                            or key == "environment" and "generations" in location):
                        path = Path(item)
                        deferred = (key == "target" and location[:2] == ("sides", "candidate")
                                    and (path in deferred_paths or len(location) == 3 and location[2] in staged_observers))
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


def normal_token_input_requested(raw):
    """Detect the option before validating its value, check, or paired scope."""
    overlays = raw.get(CHECK_CONFIG)
    settings = (raw.get("input_validation"), *(overlays.values() if type(overlays) is dict else ()))
    return any(type(section) is dict and NORMAL_TOKEN_INPUT_OPTION in section for section in settings)


def _option_occurrences(value, option, path=()):
    if type(value) is dict:
        for key, child in value.items():
            location = (*path, key)
            if key == option:
                yield location, child
            else:
                yield from _option_occurrences(child, option, location)
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _option_occurrences(child, option, (*path, index))


def _validate_normal_option_placement(raw, option, paths, error):
    for path, value in _option_occurrences(raw, option):
        if path[:2] in {("sides", "baseline"), ("sides", "candidate")}:
            path = path[2:]
        if value is not True or path not in paths:
            raise ValueError(error)


def retry_record_snapshot_requested(raw):
    return next(_option_occurrences(raw, RETRY_RECORD_SNAPSHOT_OPTION), None) is not None


def collection_status_snapshot_requested(raw):
    return next(_option_occurrences(raw, COLLECTION_STATUS_SNAPSHOT_OPTION), None) is not None


def normal_request_latency_requested(raw):
    return next(_option_occurrences(raw, NORMAL_REQUEST_LATENCY_OPTION), None) is not None


def normal_compile_cache_requested(raw):
    return next(_option_occurrences(raw, NORMAL_COMPILE_CACHE_OPTION), None) is not None


def validate_sup05_preparation_receipt(value):
    """Validate the path and raw content pin of one saved SUP-05 preparation."""
    if (type(value) is not dict or set(value) != {"path", "sha256"}
            or type(value["path"]) is not str or not value["path"]
            or type(value["sha256"]) is not str or len(value["sha256"]) != 64
            or any(char not in "0123456789abcdef" for char in value["sha256"])):
        raise ValueError("SUP05_NORMAL_PREPARATION_REFERENCE_INVALID")
    return value


def _sup05_preparation_receipts(raw):
    for path, receipt in _option_occurrences(raw, SUP05_PREPARATION_RECEIPT):
        if path[:2] in {("sides", "baseline"), ("sides", "candidate")}:
            path = path[2:]
        if path[0] in {CHECK_CONFIG, "supply_boot"}:
            yield path, receipt


def normal_file_reads_requested(raw):
    return (next(_option_occurrences(raw, NORMAL_FILE_READS_OPTION), None) is not None
            or next(_sup05_preparation_receipts(raw), None) is not None)


def approved_config_parser_requested(raw):
    return next(_option_occurrences(raw, SUP05_CONFIG_PARSER_OPTION), None) is not None


def boot_identity_requested(raw):
    return any(next(_option_occurrences(raw, option), None) is not None for option in BOOT_IDENTITY_OPTIONS.values())


def kubernetes_bound_option(check_id, settings):
    if check_id == "SUP-05" and type(settings) is dict and SUP05_CONFIG_PARSER_OPTION in settings:
        return SUP05_CONFIG_PARSER_OPTION
    return KUBERNETES_BOUND_CHECK_OPTIONS.get(check_id)


def normal_graph_capture_requested(raw):
    return next(_option_occurrences(raw, NORMAL_GRAPH_CAPTURE_OPTION), None) is not None


def normal_safetensors_reader_requested(raw):
    return next(_option_occurrences(raw, NORMAL_SAFETENSORS_READER_OPTION), None) is not None


def normal_startup_load_requested(raw):
    return next(_option_occurrences(raw, NORMAL_STARTUP_LOAD_OPTION), None) is not None


def installed_plugin_inventory_requested(raw):
    return next(_option_occurrences(raw, INSTALLED_PLUGIN_INVENTORY_OPTION), None) is not None


def cilium_egress_status_requested(raw):
    return next(_option_occurrences(raw, CILIUM_EGRESS_STATUS_OPTION), None) is not None


def normal_serving_readiness_requested(raw):
    """Detect a serving-readiness opt-in before validating its placement or value."""
    return next(_option_occurrences(raw, NORMAL_SERVING_READINESS_OPTION), None) is not None


def bounded_metadata_scopes(configurations, pack, allowed_checks):
    """Bind the explicitly configured metadata cases without enabling sibling collectors."""
    name = "required_event_record"
    error = "OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID"
    scopes = []
    gateway_modes = []
    parser_modes = []
    declared_checks = allowed_checks
    for raw in configurations:
        scoped = {}
        overlays = raw.get(CHECK_CONFIG)
        bindings = raw.get("run_binding")
        parser_mode = approved_config_parser_requested(raw)
        parser_modes.append(parser_mode)
        campaign = reviewed_normal_executors(raw)
        options = raw.get("update", {})
        if type(options) is dict and {REVIEWED_NORMAL_OPTION, REVIEW_ALL_SELECTED} & options.keys() and not campaign:
            raise ValueError("REVIEWED_NORMAL_CAMPAIGN_SCOPE_INVALID")
        allowed_checks = reviewed_dispatch_scope(raw, declared_checks)
        if parser_mode:
            _validate_normal_option_placement(raw, SUP05_CONFIG_PARSER_OPTION,
                {(CHECK_CONFIG, "SUP-05", SUP05_CONFIG_PARSER_OPTION)}, NORMAL_FILE_READS_SCOPE_ERROR)
            if ((not campaign and (allowed_checks != {"SUP-05"} or staged_observer_executors(raw) != {"supply_boot"}))
                    or normal_file_reads_requested(raw)
                    or (bindings is not None and (type(bindings) is not dict or "SUP-05" in bindings))):
                raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_SERVING_READINESS_OPTION, {
            ("observation_state", NORMAL_SERVING_READINESS_OPTION),
            (CHECK_CONFIG, "OBS-02", NORMAL_SERVING_READINESS_OPTION),
        }, NORMAL_SERVING_READINESS_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_REQUEST_LATENCY_OPTION, {
            (CHECK_CONFIG, "LIF-03", NORMAL_REQUEST_LATENCY_OPTION),
        }, NORMAL_REQUEST_LATENCY_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_STARTUP_LOAD_OPTION, {
            (CHECK_CONFIG, "LIF-01", NORMAL_STARTUP_LOAD_OPTION),
        }, NORMAL_STARTUP_LOAD_SCOPE_ERROR)
        for cid, marker in BOOT_IDENTITY_OPTIONS.items():
            _validate_normal_option_placement(raw, marker, {(CHECK_CONFIG, cid, marker)}, BOOT_IDENTITY_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_COMPILE_CACHE_OPTION, {
            (CHECK_CONFIG, "SUP-07", NORMAL_COMPILE_CACHE_OPTION),
        }, NORMAL_COMPILE_CACHE_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_FILE_READS_OPTION, {
            (CHECK_CONFIG, "SUP-05", NORMAL_FILE_READS_OPTION),
        }, NORMAL_FILE_READS_SCOPE_ERROR)
        receipt_present = False
        for path, receipt in _sup05_preparation_receipts(raw):
            receipt_present = True
            if path not in {(CHECK_CONFIG, "SUP-05", SUP05_PREPARATION_RECEIPT),
                            ("supply_boot", SUP05_PREPARATION_RECEIPT)}:
                raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
            validate_sup05_preparation_receipt(receipt)
        _validate_normal_option_placement(raw, NORMAL_GRAPH_CAPTURE_OPTION, {
            (CHECK_CONFIG, "REQ-20", NORMAL_GRAPH_CAPTURE_OPTION),
        }, NORMAL_GRAPH_CAPTURE_SCOPE_ERROR)
        _validate_normal_option_placement(raw, NORMAL_SAFETENSORS_READER_OPTION, {
            (CHECK_CONFIG, "SUP-12", NORMAL_SAFETENSORS_READER_OPTION),
        }, NORMAL_SAFETENSORS_READER_SCOPE_ERROR)
        _validate_normal_option_placement(raw, INSTALLED_PLUGIN_INVENTORY_OPTION, {
            (CHECK_CONFIG, "SUP-10", INSTALLED_PLUGIN_INVENTORY_OPTION),
        }, INSTALLED_PLUGIN_INVENTORY_SCOPE_ERROR)
        _validate_normal_option_placement(raw, CILIUM_EGRESS_STATUS_OPTION, {
            (CHECK_CONFIG, "PLT-07", CILIUM_EGRESS_STATUS_OPTION),
        }, CILIUM_EGRESS_STATUS_SCOPE_ERROR)
        if type(overlays) is dict and any(
                check_id != "REQ-09" and type(section) is dict and NORMAL_TOKEN_INPUT_OPTION in section
                for check_id, section in overlays.items()):
            raise ValueError("NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID")
        for check_id, option in RECORD_SNAPSHOT_OPTIONS.items():
            overlay = overlays.get(check_id) if type(overlays) is dict else None
            sections = (raw.get(name), overlay)
            gateway = check_id == "OBS-01" and any(
                type(settings) is dict and GATEWAY_RECORD_SNAPSHOT_OPTION in settings for settings in sections)
            if not gateway and not any(type(settings) is dict and option in settings for settings in sections):
                continue
            settings = for_check(raw, name, check_id).get(name, {})
            if (settings.get(option) is not True or gateway and
                    (settings.get(GATEWAY_RECORD_SNAPSHOT_OPTION) is not True
                     or raw.get("update", {}).get("kubernetes_candidate_binding", "prebound") != "prebound")):
                raise ValueError(error)
            scoped[check_id] = (settings, bindings.get(check_id) if type(bindings) is dict else None)
        if scoped:
            if (allowed_checks is None or set(scoped) != {cid for cid in allowed_checks if pack.checks[cid].executor == name}
                    or raw.get("update", {}).get("mode", "external") != "external"
                    or raw.get("executors", {}).get(name) != "kubernetes"
                    or raw.get("record_attestation") or raw.get("lab_activation")):
                raise ValueError(error)
            sessions = [binding.get("record_session") for _, binding in scoped.values() if type(binding) is dict]
            if len(sessions) == 2 and sessions[0] == sessions[1]:
                raise ValueError("OBSERVATION_RECORD_SNAPSHOT_SESSIONS_MUST_DIFFER")
        retry_option = RETRY_RECORD_SNAPSHOT_OPTION
        retry_error = RETRY_RECORD_SNAPSHOT_SCOPE_ERROR
        retry_executor = "request_termination"
        _validate_normal_option_placement(raw, retry_option, {
            (retry_executor, retry_option),
            (CHECK_CONFIG, "LIF-12", retry_option),
        }, retry_error)
        retry_base = raw.get(retry_executor)
        retry_overlay = overlays.get("LIF-12") if type(overlays) is dict else None
        retry_in_base = type(retry_base) is dict and retry_option in retry_base
        if retry_in_base or type(retry_overlay) is dict and retry_option in retry_overlay:
            settings = for_check(raw, retry_executor, "LIF-12").get(retry_executor, {})
            update = raw.get("update", {})
            if (allowed_checks is None or "LIF-12" not in allowed_checks
                    or raw.get("executors", {}).get(retry_executor) != "kubernetes"
                    or update.get("mode", "external") != "external"
                    or update.get("kubernetes_candidate_binding", "prebound") != "prebound"
                    or raw.get("lab_activation")
                    or retry_in_base and any(cid != "LIF-12" and pack.checks[cid].executor == retry_executor
                                             for cid in allowed_checks)):
                raise ValueError(retry_error)
            scoped["LIF-12"] = (settings, bindings.get("LIF-12") if type(bindings) is dict else None)
        if collection_status_snapshot_requested(raw):
            status_option = COLLECTION_STATUS_SNAPSHOT_OPTION
            status_error = COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR
            _validate_normal_option_placement(raw, status_option, {(CHECK_CONFIG, "OBS-11", status_option)}, status_error)
            executor = "log_integrity"
            settings = for_check(raw, executor, "OBS-11").get(executor, {})
            update = raw.get("update", {})
            status_binding = bindings.get("OBS-11") if type(bindings) is dict else None
            if (settings.get(status_option) is not True or allowed_checks is None
                    or {cid for cid in allowed_checks if pack.checks[cid].executor == executor} != {"OBS-11"}
                    or raw.get("executors", {}).get(executor) != "kubernetes"
                    or update.get("mode", "external") != "external"
                    or update.get("kubernetes_candidate_binding", "prebound") != "prebound"
                    or raw.get("lab_activation") or raw.get("record_attestation")
                    or type(status_binding) is not dict or set(status_binding) != {"record_session"}):
                raise ValueError(status_error)
            scoped["OBS-11"] = (settings, status_binding)
        stream_cases = {}
        for check_id, (executor, option, scope_error) in _NORMAL_ONLY_CASES.items():
            overlay = overlays.get(check_id) if type(overlays) is dict else None
            if not any(type(settings) is dict and option in settings for settings in (raw.get(executor), overlay)):
                continue
            settings = for_check(raw, executor, check_id).get(executor, {})
            if (settings[option] is not True or allowed_checks is None
                    or (executor not in {"stream_buffer", "boundary_egress", "supply_boot"}
                        and {cid for cid in allowed_checks if pack.checks[cid].executor == executor} != {check_id})
                    or raw.get("executors", {}).get(executor) != "kubernetes"
                    or raw.get("update", {}).get("mode", "external") != "external"
                    or raw.get("lab_activation")
                    or (check_id in {"LIF-01", "LIF-03", "OBS-02", "PLT-07", "REQ-09", "REQ-10", "REQ-20",
                                     "SUP-05", "SUP-07", "SUP-10", "SUP-12", *BOOT_IDENTITY_OPTIONS}
                        and raw.get("record_attestation"))
                    or (check_id in {"LIF-01", "LIF-03", "OBS-02", "PLT-07", "REQ-20", "SUP-05", "SUP-07", "SUP-10", "SUP-12", *BOOT_IDENTITY_OPTIONS}
                        and "run_binding" in raw
                        and (type(bindings) is not dict or check_id in bindings))
                    or (check_id in {"LIF-01", "PLT-07", "REQ-20", "SUP-07", "SUP-10", "SUP-12", *BOOT_IDENTITY_OPTIONS}
                        and set(settings) - {option, "target", "kubectl_binary", "max_duration_seconds"})):
                raise ValueError(scope_error)
            scoped[check_id] = (settings, None)
            if executor == "stream_buffer":
                stream_cases[check_id] = settings
        if stream_cases:
            stream_error = (NORMAL_REQUEST_LATENCY_SCOPE_ERROR if "LIF-03" in stream_cases
                            else _NORMAL_ONLY_CASES["LIF-04"][2])
            if set(stream_cases) != {cid for cid in allowed_checks if pack.checks[cid].executor == "stream_buffer"}:
                raise ValueError(stream_error)
            if "LIF-03" in stream_cases:
                _validate_normal_option_placement(raw, NORMAL_STREAM_OPTION, {
                    (CHECK_CONFIG, "LIF-04", NORMAL_STREAM_OPTION),
                }, stream_error)
                base = raw.get("stream_buffer", {})
                target = base.get("target")
                if (type(target) is not str or not target
                        or any(settings.get("target") != target or {"prepared", "common_inputs"} & settings.keys()
                               for settings in stream_cases.values())
                        or ("run_binding" in raw and (type(bindings) is not dict or set(stream_cases) & bindings.keys()))):
                    raise ValueError(stream_error)
        supply_cases = {cid: scoped[cid][0] for cid in ("LIF-01", "SUP-07", "SUP-12", "SUP-10", *BOOT_IDENTITY_OPTIONS)
                        if cid in scoped}
        if parser_mode:
            parser_settings = for_check(raw, "supply_boot", "SUP-05")["supply_boot"]
            scoped["SUP-05"] = (parser_settings, None)
            if campaign:
                supply_cases["SUP-05"] = parser_settings
        if receipt_present and "SUP-05" not in scoped:
            raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
        if "SUP-05" in scoped and not parser_mode:
            settings, _ = scoped["SUP-05"]
            supply_checks = {cid for cid in allowed_checks if pack.checks[cid].executor == "supply_boot"}
            normal = settings.get("normal")
            if (supply_checks != {"SUP-05", *supply_cases} or set(settings) - {
                    NORMAL_FILE_READS_OPTION, SUP05_PREPARATION_RECEIPT, "normal", "kubectl_binary", "max_duration_seconds"}
                    or type(normal) is not dict or set(normal) != {"target", "prepared", "reference_manifest"}
                    or any(type(path) is not str or not path for path in normal.values())
                    or raw.get("update", {}).get("kubernetes_candidate_binding", "prebound") != "prebound"):
                raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
            if supply_cases:
                target = next(iter(supply_cases.values())).get("target")
                if (type(target) is not str or not target or normal["target"] == target
                        or any(case.get("target") != target for case in supply_cases.values())):
                    raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
        elif supply_cases:
            anchor = next(iter(supply_cases))
            _, _, supply_error = _NORMAL_ONLY_CASES[anchor]
            if set(supply_cases) != {cid for cid in allowed_checks if pack.checks[cid].executor == "supply_boot"}:
                raise ValueError(supply_error)
            if anchor != "SUP-10":
                target = raw.get("supply_boot", {}).get("target")
                if (type(target) is not str or not target
                        or any(settings.get("target") != target for settings in supply_cases.values())):
                    raise ValueError(supply_error)
        if "PLT-07" in scoped:
            executor = "boundary_egress"
            egress_checks = {cid for cid in allowed_checks if pack.checks[cid].executor == executor}
            target = raw.get(executor, {}).get("target")
            if (egress_checks not in ({"PLT-07"}, {"PLT-07", "OBS-04"})
                    or type(target) is not str or not target or scoped["PLT-07"][0].get("target") != target):
                raise ValueError(CILIUM_EGRESS_STATUS_SCOPE_ERROR)
            local_option = KUBERNETES_BOUND_CHECK_OPTIONS["OBS-04"]
            _validate_normal_option_placement(raw, local_option, {
                (CHECK_CONFIG, "OBS-04", local_option),
            }, CILIUM_EGRESS_STATUS_SCOPE_ERROR)
            if "OBS-04" in egress_checks:
                local = for_check(raw, executor, "OBS-04").get(executor, {})
                if (local.get(local_option) is not True or local.get("target") != target
                        or set(local) - {local_option, "target", "kubectl_binary", "max_duration_seconds",
                                         "usage_wait_seconds"}
                        or (type(bindings) is dict and "OBS-04" in bindings)):
                    raise ValueError(CILIUM_EGRESS_STATUS_SCOPE_ERROR)
                scoped["OBS-04"] = (local, None)
        scopes.append(scoped)
        gateway_modes.append(scoped.get("OBS-01", ({}, None))[0].get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True)
    for check_id, (_, _, scope_error) in _NORMAL_ONLY_CASES.items():
        sides = [check_id in scoped for scoped in scopes]
        if any(sides) and (len(sides) != 2 or not all(sides)):
            raise ValueError(scope_error)
    status_sides = ["OBS-11" in scoped for scoped in scopes]
    if any(status_sides) and (len(status_sides) != 2 or not all(status_sides)):
        raise ValueError(COLLECTION_STATUS_SNAPSHOT_SCOPE_ERROR)
    if any(scopes) and (len(scopes) != 2 or set(scopes[0]) != set(scopes[1])):
        raise ValueError(error)
    if any(gateway_modes) and not all(gateway_modes):
        raise ValueError(error)
    if any(parser_modes) and (len(parser_modes) != 2 or not all(parser_modes)):
        raise ValueError(NORMAL_FILE_READS_SCOPE_ERROR)
    return scopes
