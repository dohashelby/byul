#!/usr/bin/env python3
"""Convert a complete UPDATE inventory, check visible assets and bind Pod declarations.

The approved launch file has command/args string arrays. Raw Pod input is read
locally; no kubectl, deployment or approval creation is performed by this tool.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.mvp.asset_binding import manifest_from_update_inventory, check_execution_binding
from serving_runtime.mvp.assets import load_manifest, check
from serving_runtime.mvp.policy_bundle import _strict_json
from serving_runtime.mvp.target import load_target


def _read(path: str) -> dict:
    value = _strict_json(Path(path).read_bytes(), path)
    if not isinstance(value, dict):
        raise ValueError("JSON object required")
    return value


def _write_new(path: str, document: dict) -> None:
    raw = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("inventory", "root", "intent", "target", "pod", "approved-launch", "model-host-path", "manifest-out", "report-out"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    # Freeze each external input once. All parsing and provenance hashes use
    # these same bytes, even if an operator rotates the source files later.
    inputs = {}
    for name in ("inventory", "intent", "target", "pod", "approved_launch"):
        path = getattr(args, name)
        raw = Path(path).read_bytes()
        inputs[name] = (raw, _strict_json(raw, path))
    inventory = inputs["inventory"][1]
    manifest = manifest_from_update_inventory(inventory, args.root)
    intent = parse_intent(inputs["intent"][1])
    with tempfile.NamedTemporaryFile(prefix="runtime-target-input-", suffix=".json") as snapshot:
        snapshot.write(inputs["target"][0])
        snapshot.flush()
        loaded_target = load_target(snapshot.name)
    if loaded_target.state != "ok":
        raise ValueError("invalid target registration")
    launch = inputs["approved_launch"][1]
    if set(launch) != {"command", "args"}:
        raise ValueError("approved launch must contain only command and args")
    binding = check_execution_binding(inputs["pod"][1], inputs["target"][1], intent,
                                      model_host_path=args.model_host_path,
                                      expected_command=launch["command"], expected_args=launch["args"])
    _write_new(args.manifest_out, manifest)
    result = check(load_manifest(args.manifest_out))
    report = {"schema_version": "runtime-update-asset-check/1.0", "source_inventory_sha256": inventory["inventory_sha256"],
              "target_sha256": hashlib.sha256(inputs["target"][0]).hexdigest(),
              "intent_sha256": hashlib.sha256(inputs["intent"][0]).hexdigest(),
              "pod_sha256": hashlib.sha256(inputs["pod"][0]).hexdigest(),
              "approved_launch_sha256": hashlib.sha256(inputs["approved_launch"][0]).hexdigest(),
              "asset_check": result, "execution_binding": binding,
              "ready_for_operator_review": result["intact"] and binding["state"] == "match",
              "approval_state": "not_created", "worker_consumption": "unverified"}
    _write_new(args.report_out, report)
    return 0 if report["ready_for_operator_review"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
