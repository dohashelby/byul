"""Typed, non-secret TOML defaults for the existing MVP argument parser."""

import argparse
from contextlib import redirect_stdout
from copy import deepcopy
import io
from dataclasses import fields
from pathlib import Path
import tomllib
from typing import get_args, get_type_hints

from .config import MVPConfig

ALIASES = {
    "policy_path": "policy",
    "backend_config_path": "backend_config",
    "relay_timeout_s": "relay_timeout",
    "stream_idle_timeout_s": "stream_idle_timeout",
    "request_deadline_s": "request_deadline",
    "max_concurrent_requests": "max_concurrency",
    "request_header_timeout_s": "request_header_timeout",
    "request_body_timeout_s": "request_body_timeout",
    "revocations_path": "revocations",
    "policy_approvals_path": "policy_approvals",
    "baseline_path": "baseline",
    "manifest_path": "manifest",
    "collector_interval_s": "collector_interval",
    "collector_stale_after_s": "collector_stale_after",
    "asset_manifest_path": "asset_manifest",
    "asset_interval_s": "asset_interval",
    "retention_interval_s": "retention_interval",
    "retention_hold_path": "retention_hold",
    "lab_report_path": "lab_report",
    "cache_observation_path": "cache_observation",
    "native_receipt_feed_path": "native_receipt_feed",
    "tetragon_state_path": "tetragon_state",
    "gpu_state_path": "gpu_state",
    "engine_receipts_path": "engine_receipts",
    "tetragon_identities_path": "tetragon_identities",
    "observation_feeds": "observation_feed",
    "observation_heartbeats": "observation_heartbeat",
    "observation_intent_path": "observation_intent",
    "auth_config_path": "auth_config",
    "target_path": "target",
    "observation_exceptions_path": "observation_exceptions",
    "observation_timeline_path": "observation_timeline",
}
FEED_FIELDS = {"observation_feeds", "observation_heartbeats"}
FEED_KINDS = {"hubble", "tetragon", "management_listener"}
CHOICES = {"backend": {"mock", "vllm", "sglang"}, "observation_origin": {"synthetic", "real"}}


def _feed_paths(name, value):
    if type(value) is not dict:
        raise ValueError(f"node config: {name} must be a table of kind = path")
    result = []
    for kind, path in value.items():
        if kind not in FEED_KINDS:
            raise ValueError(f"node config: {name} has unknown kind: {kind}")
        if type(path) is not str or not path.strip():
            raise ValueError(f"node config: {name}.{kind} must be a non-empty path")
        result.append((kind, path))
    return result


def _typed_value(name, value, annotation):
    if name in FEED_FIELDS:
        return _feed_paths(name, value)
    alternatives = get_args(annotation)
    expected = next((kind for kind in alternatives if kind is not type(None)), annotation)
    if expected is float:
        if type(value) not in (int, float):
            raise ValueError(f"node config: {name} must be float")
        try:
            return float(value)
        except OverflowError as error:
            raise ValueError(f"node config: {name} is outside the float range") from error
    if type(value) is not expected:
        raise ValueError(f"node config: {name} must be {expected.__name__}")
    if name in CHOICES and value not in CHOICES[name]:
        raise ValueError(f"node config: {name} must be one of {', '.join(sorted(CHOICES[name]))}")
    return value


def load_node_defaults(path):
    try:
        with Path(path).open("rb") as source:
            document = tomllib.load(source)
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"node config: invalid TOML: {error}") from error
    known = {field.name for field in fields(MVPConfig)}
    annotations = get_type_hints(MVPConfig)
    defaults = {}
    for name, value in document.items():
        if name == "host":
            raise ValueError("node config: host is fixed to 127.0.0.1")
        if name == "policy_bundle":
            raise ValueError("node config: policy_bundle is a verified result and cannot be configured")
        if name not in known:
            raise ValueError(f"node config: unknown key: {name}")
        defaults[ALIASES.get(name, name)] = _typed_value(name, value, annotations[name])
    return defaults


def apply_node_defaults(parser, arguments):
    probe = argparse.ArgumentParser(prog=parser.prog, add_help=False, allow_abbrev=False)
    probe.add_argument("--config")
    options, _unknown = probe.parse_known_args(arguments)
    if options.config is None:
        return None
    defaults = load_node_defaults(options.config)
    explicit_parser = deepcopy(parser)
    explicit_parser.set_defaults(observation_feed=None, observation_heartbeat=None)
    # Parse with argparse itself so aliases, abbreviations and option values
    # follow the real parser. Help is printed once by the real parse below.
    with redirect_stdout(io.StringIO()):
        try:
            explicit, _unknown = explicit_parser.parse_known_args(arguments)
        except SystemExit as error:
            if error.code != 0:
                raise
            explicit = argparse.Namespace()
    explicit = vars(explicit)
    # append actions copy defaults; an explicit CLI list replaces its TOML list.
    for destination in ("observation_feed", "observation_heartbeat"):
        if explicit.get(destination) is not None:
            defaults[destination] = []
    parser.set_defaults(**defaults)
    return options.config
