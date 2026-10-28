"""SUP-12 fixed untyped decoder CPU slice; no tensors, model, daemon or I/O payloads."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import pickle
import subprocess
import sys
import time
from types import ModuleType, SimpleNamespace

from guard.executors.serialization_decode import analyze_decoder_trial, collect_decoder_trial

PIN_PATH = Path(__file__).resolve().parents[1] / "docs/b2-decoder/source-pin.json"


def verify_source(root):
    pin = json.loads(PIN_PATH.read_text())
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != pin["commit"]:
        raise ValueError("reference commit mismatch")
    for name, expected in pin["files"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("reference file bytes mismatch")
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", "vllm"], text=True).strip():
        raise ValueError("reference source tree is modified")
    return pin


def source_scope(root):
    import cloudpickle
    import msgspec
    import zmq

    pin = verify_source(root)
    # Execute the actual environment getter; change only this child process.
    path = root / "vllm/envs.py"
    tree = ast.parse(path.read_bytes(), filename=str(path))
    mapping = next(node.value for node in tree.body if isinstance(node, ast.AnnAssign)
                   and isinstance(node.target, ast.Name) and node.target.id == "environment_variables")
    getter = next(value for key, value in zip(mapping.keys, mapping.values)
                  if isinstance(key, ast.Constant) and key.value == "VLLM_ALLOW_INSECURE_SERIALIZATION")
    getter = eval(compile(ast.Expression(getter), str(path), "eval"), {"os": os})
    logger = logging.getLogger("b2.decoder.reference")
    logger.addHandler(logging.NullHandler()); logger.propagate = False
    logger.warning_once = logger.warning  # Logging-only dependency adapter.
    scope = {"__name__": "b2_reference_decoder", "msgpack": msgspec.msgpack, "pickle": pickle,
        "cloudpickle": cloudpickle, "logger": logger, "PIN_MEMORY": False, "zmq": zmq}
    path = root / "vllm/v1/serial_utils.py"
    tree = ast.parse(path.read_bytes(), filename=str(path))
    names = {"MsgpackDecoder", "_log_insecure_serialization_warning", "bytestr", "CUSTOM_TYPE_PICKLE",
             "CUSTOM_TYPE_CLOUDPICKLE", "CUSTOM_TYPE_RAW_VIEW"}

    def declared(node):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            return node.name
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            return node.target.id
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            return node.targets[0].id
        return None

    nodes = [node for node in tree.body if declared(node) in names]
    if {declared(node) for node in nodes} != names:
        raise ValueError("decoder definition inventory mismatch")
    tree.body = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes]
    ast.fix_missing_locations(tree)
    exec(compile(tree, str(path), "exec"), scope)
    return scope, getter, pin


def run(root):
    import cloudpickle
    import msgspec

    start = time.perf_counter()
    scope, getter, pin = source_scope(root)
    counters = dict.fromkeys(("callback", "pickle_global", "file_open", "socket_connect", "process_start"), 0)
    active = False

    def audit(event, args):
        if not active:
            return
        category = {"pickle.find_class": "pickle_global", "open": "file_open", "socket.connect": "socket_connect",
                    "subprocess.Popen": "process_start", "os.system": "process_start"}.get(event)
        if category:
            counters[category] += 1

    sys.addaudithook(audit)
    # Inert, locally authored reducer: an in-memory counter, no paths/commands.
    fixture_module = ModuleType("_byul_b2_decoder_fixture")

    def callback():
        counters["callback"] += 1
        return {"guard_callback": True}

    callback.__module__ = fixture_module.__name__
    callback.__qualname__ = "callback"
    fixture_module.callback = callback
    sys.modules[fixture_module.__name__] = fixture_module

    class Fixture:
        def __reduce__(self):
            return fixture_module.callback, ()

    pack = msgspec.msgpack
    payloads = {"native": pack.encode({"native": True}),
        "pickle": pack.encode(pack.Ext(1, pickle.dumps(Fixture()))),
        "cloudpickle": pack.encode(pack.Ext(2, cloudpickle.dumps(Fixture()))),
        "raw_view": pack.encode(pack.Ext(3, b"synthetic raw view")),
        "unknown_extension": pack.encode(pack.Ext(125, b"synthetic extension")),
        "malformed": b"\xc1", "truncated": b"\xc7\x20\x01"}
    control = pack.encode({"guard_control": True})
    previous = os.environ.get("VLLM_ALLOW_INSECURE_SERIALIZATION")
    trials = []
    try:
        for mode, value in (("unset", None), ("enabled", "1")):
            if value is None:
                os.environ.pop("VLLM_ALLOW_INSECURE_SERIALIZATION", None)
            else:
                os.environ["VLLM_ALLOW_INSECURE_SERIALIZATION"] = value
            flag = getter()
            scope["envs"] = SimpleNamespace(VLLM_ALLOW_INSECURE_SERIALIZATION=flag)
            for name, payload in payloads.items():
                decoder = scope["MsgpackDecoder"]()
                active = True
                try:
                    trial = collect_decoder_trial(decoder, payload, case=name, control_payload=control,
                        effect_probe=lambda: dict(counters), rejection_types=(NotImplementedError, msgspec.DecodeError))
                finally:
                    active = False
                expected_callback = flag and name in {"pickle", "cloudpickle"}
                reject = name in {"unknown_extension", "malformed", "truncated"} or (
                    name in {"pickle", "cloudpickle"} and not flag)
                assert trial["effect_counts"]["callback"] == int(expected_callback)
                assert trial["decode_state"] == ("rejected" if reject else "decoded")
                assert trial["normal_before"] and trial["normal_after"]
                assert decoder.aux_buffers == ()
                trials.append({"mode": mode, "reference_flag_value": flag, **trial,
                    "final_aux_buffers_cleared": True,
                    "enterprise_assessment": analyze_decoder_trial(trial),
                    "example_assessment": analyze_decoder_trial(trial, forbidden_effects=["callback"],
                                                                 expected_rejection=reject)})
    finally:
        if previous is None:
            os.environ.pop("VLLM_ALLOW_INSECURE_SERIALIZATION", None)
        else:
            os.environ["VLLM_ALLOW_INSECURE_SERIALIZATION"] = previous
        sys.modules.pop(fixture_module.__name__, None)
    return {"schema": "byul-b2-decoder-cpu/1", "reference": pin, "reference_execution_origin": "MEASURED",
        "fixture_origin": "EXAMPLE", "enterprise_policy_status": "UNSET", "engine_inference_executed": False,
        "scope": "verbatim untyped MsgpackDecoder and actual env getter; no tensor/typed multimodal/daemon/loader path",
        "adapters": ["environment snapshot from actual getter", "PIN_MEMORY=False unused by untyped cases",
                     "standard logger warning_once alias", "locally authored in-memory reducer"],
        "audit_scope": "CPython pickle.find_class/open/socket.connect/subprocess.Popen/os.system only; no args retained; not OS-wide tracing",
        "dependencies": {name: importlib.metadata.version(name) for name in ("msgspec", "cloudpickle", "pyzmq")},
        "python": sys.version.split()[0], "trials": trials, "assertions_passed": True,
        "parent_seconds": time.perf_counter() - start}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.source.resolve())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    print(f"Actual decoder CPU: {len(report['trials'])} cases; reference assertions passed; enterprise policy unset")


if __name__ == "__main__":
    main()
