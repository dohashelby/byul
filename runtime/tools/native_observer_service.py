"""Start the existing native watcher from a protected, source-pinned deployment config."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

from serving_runtime.mvp.native_receipts import read_protected
from serving_runtime.mvp.policy import _object_without_duplicates, _reject_json_constant

SCHEMA = "byul-native-observer-service/1"
SCOPES = {"asgi_transport", "frontend_request", "engine_core", "scheduler_state",
          "worker_batch", "kv_references", "gpu_transfers"}


def configuration(path):
    """The system service reads a root-owned config; it never selects a new generation."""
    if os.geteuid() != 0:
        raise ValueError("native observer requires the root host observer")
    doc = json.loads(read_protected(path, 0, limit=65536),
                     object_pairs_hook=_object_without_duplicates, parse_constant=_reject_json_constant)
    required = {"schema", "producer_source_sha256", "environment", "target", "prepared",
                "records", "output", "capture_dir", "reader_gid", "gateway_writer_uid",
                "interval", "duration", "max_delay_ms", "required_scopes", "input_sha256", "kubeconfig"}
    if type(doc) is not dict or set(doc) not in (required, required | {"node_image_verification"}):
        raise ValueError("native observer config fields invalid")
    if doc["schema"] != SCHEMA or type(doc["producer_source_sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", doc["producer_source_sha256"]) is None:
        raise ValueError("native observer config source pin required")
    for name in ("environment", "target", "prepared", "records", "output", "capture_dir", "kubeconfig"):
        value = doc[name]
        if type(value) is not str or not value or not Path(value).is_absolute() or any(c in value for c in "\x00\r\n"):
            raise ValueError("native observer absolute paths required")
    if any(c in doc["kubeconfig"] for c in "%$:"):
        raise ValueError("native observer single Kubernetes credential path required")
    if not read_protected(doc["kubeconfig"], 0, limit=1024 * 1024):
        raise ValueError("native observer protected Kubernetes credential required")
    for name in ("reader_gid", "gateway_writer_uid"):
        if type(doc[name]) is not int or doc[name] < 0:
            raise ValueError("native observer numeric identities required")
    for name, low, high in (("interval", .1, 300), ("duration", .1, 300)):
        value = doc[name]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("native observer finite poll bounds required")
    if type(doc["max_delay_ms"]) is not int or not 1 <= doc["max_delay_ms"] <= 300000:
        raise ValueError("native observer finite projection bound required")
    scopes = doc["required_scopes"]
    if type(scopes) is not list or not scopes or any(type(s) is not str or s not in SCOPES for s in scopes) or len(set(scopes)) != len(scopes):
        raise ValueError("native observer supported scopes required")
    if "node_image_verification" in doc and doc["node_image_verification"] != "local-containerd-v1":
        raise ValueError("native observer image verification invalid")
    pins = doc["input_sha256"]
    if type(pins) is not dict or set(pins) != {"environment", "target", "prepared"}:
        raise ValueError("native observer generation pins required")
    for name, pin in pins.items():
        if type(pin) is not str or re.fullmatch(r"[0-9a-f]{64}", pin) is None:
            raise ValueError("native observer input pin invalid")
        raw = read_protected(doc[name], doc["reader_gid"], limit=16 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != pin:
            raise ValueError("native observer generation input differs from approved pin")
    return doc


def command(doc):
    watcher = Path(__file__).resolve().with_name("update_engine_link.py")
    if hashlib.sha256(watcher.read_bytes()).hexdigest() != doc["producer_source_sha256"]:
        raise ValueError("native observer producer source differs from approved pin")
    args = [sys.executable, "-B", str(watcher), "watch"]
    for key in ("environment", "target", "prepared", "records", "output", "capture_dir",
                "reader_gid", "gateway_writer_uid", "interval", "duration", "max_delay_ms"):
        args.extend(["--" + key.replace("_", "-"), str(doc[key])])
    args.extend(["--required-scopes", *doc["required_scopes"]])
    if doc.get("node_image_verification"):
        args.extend(["--node-image-verification", doc["node_image_verification"]])
    return args


def unit_text(config_path, kubeconfig_path="/etc/serving-runtime/native-kubeconfig"):
    # systemd expands percent specifiers even in quoted arguments. Refuse them,
    # control characters and relative paths before emitting an executable unit.
    def quote(value):
        value = str(value)
        if not Path(value).is_absolute() or any(c in value for c in "%$:\x00\r\n"):
            raise ValueError("native observer unit absolute safe path required")
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    script = Path(__file__).resolve()
    root = script.parents[2]
    quote(root)
    quote(kubeconfig_path)
    pythonpath = ":".join(str(p) for p in (root / "runtime", root / "runtime/src", root / "update/src"))
    return "\n".join([
        "[Unit]", "Description=byul continuous native receipt observer", "After=network-online.target",
        "Wants=network-online.target", "StartLimitIntervalSec=0", "", "[Service]", "Type=simple",
        "User=root", "Group=root", "UMask=0077", "Environment=PATH=/usr/local/bin:/usr/bin:/bin",
        'Environment="PYTHONPATH=' + pythonpath.replace("\\", "\\\\").replace('"', '\\"') + '"',
        'Environment="KUBECONFIG=' + str(kubeconfig_path).replace("\\", "\\\\").replace('"', '\\"') + '"',
        # WorkingDirectory takes a literal path, unlike ExecStart's argv parser.
        # Its path was validated above; quotes would become part of the path.
        "WorkingDirectory=" + str(root / "runtime"),
        "ExecStart=" + " ".join((quote(sys.executable), "-B", quote(script), "run", "--config", quote(config_path))),
        "Restart=on-failure", "RestartSec=5", "RestartPreventExitStatus=2", "KillMode=control-group",
        "TimeoutStopSec=10", "", "[Install]", "WantedBy=multi-user.target", "",
    ])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "check", "render-unit"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    try:
        doc = configuration(args.config)
        argv = command(doc)
        if args.mode == "render-unit":
            print(unit_text(args.config, doc["kubeconfig"]), end="")
            return 0
        if args.mode == "check":
            print("native observer config and producer pin verified")
            return 0
        root = Path(__file__).resolve().parents[2]
        environment = {**os.environ, "PYTHONPATH": ":".join(str(p) for p in (root / "runtime", root / "runtime/src", root / "update/src")), "KUBECONFIG": doc["kubeconfig"]}
        os.execve(sys.executable, argv, environment)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        print("native observer configuration unavailable; verify protected config and source pin", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
