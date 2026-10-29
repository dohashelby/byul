"""SUP-12 fixed untyped/typed decoder CPU slices; no model or daemon execution."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import inspect
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


TYPED_OFFICIAL_NAMES = frozenset({"test_dict_serialization", "test_tensor_serialization", "test_numpy_array_serialization"})


def typed_fingerprint(frames):
    """Hash frame boundaries and bytes; no descriptor or buffer values persist."""
    digest = hashlib.sha256()
    sizes = []
    for frame in frames:
        data = memoryview(frame).tobytes()
        sizes.append(len(data))
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return {"input_frames_sha256": digest.hexdigest(), "frame_bytes": sizes,
            "input_bytes": sum(sizes), "frame_count": len(sizes)}


def collect_typed_trial(decoder, frames, *, case_id, control_frames, control_check):
    """Observe a multi-frame typed call and the same decoder's Python aux state."""
    def control():
        try:
            decoded = decoder.decode(control_frames)
            matched = bool(control_check(decoded))
            outcome, error_type = "decoded_matches" if matched else "decoded_mismatch", None
        except Exception as error:
            outcome, error_type = "exception", type(error).__name__
        return {"outcome": outcome, "exception_type": error_type,
                "aux_buffers_cleared": decoder.aux_buffers == ()}
    before = control()
    if before["outcome"] == "decoded_matches" and before["aux_buffers_cleared"]:
        try:
            decoder.decode(frames)
            outcome, error_type = "decoded", None
        except Exception as error:
            outcome, error_type = "exception", type(error).__name__
    else:
        outcome, error_type = "not_attempted", None
    cleared = decoder.aux_buffers == ()
    after = control()
    return {"case_id": case_id, **typed_fingerprint(frames), "outcome": outcome,
        "exception_type": error_type, "normal_before": before, "normal_after": after,
        "case_aux_buffers_cleared": cleared, "final_aux_buffers_cleared": decoder.aux_buffers == ()}


def typed_frame_cases(encoder_class, value, pack):
    """Small source-shaped descriptor mutations; no enterprise rejection expectation."""
    from copy import deepcopy
    inline = list(encoder_class(size_threshold=256).encode(value))
    auxiliary = list(encoder_class(size_threshold=0).encode(value))
    if len(inline) != 1 or len(auxiliary) != 2:
        raise ValueError("typed fixture frame layout mismatch")
    descriptor = pack.decode(auxiliary[0])
    if type(descriptor) is not list or len(descriptor) != 3 or descriptor[2] != 1:
        raise ValueError("typed fixture descriptor layout mismatch")
    def mutate(index, replacement):
        changed = deepcopy(descriptor)
        changed[index] = replacement
        return [pack.encode(changed), auxiliary[1]]
    return [
        ("inline-normal", inline), ("aux-normal", auxiliary),
        ("dtype-unknown", mutate(0, "byul_unknown_dtype")),
        ("shape-mismatch", mutate(1, [3, 3])),
        ("shape-infer", mutate(1, [2, -1])),
        ("descriptor-missing-field", [pack.encode(descriptor[:2]), auxiliary[1]]),
        ("aux-missing", [auxiliary[0]]),
        ("aux-index-outside", mutate(2, 2)),
        ("aux-index-negative", mutate(2, -1)),
        ("aux-truncated", [auxiliary[0], memoryview(auxiliary[1]).tobytes()[:-1]]),
        ("main-truncated", [memoryview(auxiliary[0]).tobytes()[:-1], auxiliary[1]]),
        ("empty-frame-list", []),
    ]


def typed_official_controls(root, encoder_class, decoder_class, torch, np):
    """Execute the three original plain test functions with their assertions intact."""
    path = root / "tests/v1/test_serial_utils.py"
    tree = ast.parse(path.read_bytes(), filename=str(path))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in TYPED_OFFICIAL_NAMES]
    if {node.name for node in nodes} != TYPED_OFFICIAL_NAMES:
        raise ValueError("typed official test inventory mismatch")
    selected = ast.Module(body=nodes, type_ignores=[])
    namespace = {"torch": torch, "np": np, "MsgpackEncoder": encoder_class, "MsgpackDecoder": decoder_class}
    exec(compile(selected, str(path), "exec"), namespace)
    controls = []
    for name in sorted(TYPED_OFFICIAL_NAMES):
        try:
            namespace[name]()
            outcome, error_type = "passed", None
        except Exception as error:
            outcome, error_type = "exception", type(error).__name__
        controls.append({"case_id": name, "outcome": outcome, "exception_type": error_type})
    return controls


def run_typed(root):
    from tools.b1_integration_cpu import verify_tool_checkout
    from guard.executors.structured_tools_cpu import _verify_source
    verify_tool_checkout(Path(__file__).resolve().parents[2])
    pin = verify_source(root)
    _verify_source(root, expected_commit=pin["commit"])
    start = time.perf_counter()
    report = {"schema": "byul-b2-typed-decoder-cpu/1", "reference": pin,
        "fixture_origin": "EXAMPLE", "enterprise_policy_status": "UNSET",
        "engine_inference_executed": False, "worker_or_gpu_reclamation_verified": False,
        "scope": "actual full typed MsgpackEncoder/Decoder on CPU Tensor and ndarray; Python reader aux reference cleanup only",
        "official_test_assertions_modified": False,
        "own_tool_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "official_controls": [], "trials": []}
    previous = os.environ.get("VLLM_ALLOW_INSECURE_SERIALIZATION")
    os.environ["VLLM_ALLOW_INSECURE_SERIALIZATION"] = "0"
    try:
        import torch
        import numpy as np
        from msgspec import msgpack
        from vllm.v1.serial_utils import MsgpackEncoder, MsgpackDecoder
        if torch.version.cuda is not None:
            raise ValueError("CPU-only torch required")
        for target in (MsgpackEncoder, MsgpackDecoder):
            if Path(inspect.getfile(target)).resolve() != root / "vllm/v1/serial_utils.py":
                raise ValueError("typed target import mismatch")
        report["dependencies"] = {name: importlib.metadata.version(name) for name in ("torch", "numpy", "msgspec", "pyzmq")}
        report["python"] = sys.version.split()[0]
        report["official_controls"] = typed_official_controls(root, MsgpackEncoder, MsgpackDecoder, torch, np)
        if not all(row["outcome"] == "passed" for row in report["official_controls"]):
            report["status"] = "official_controls_not_passed"
            return report
        values = (("tensor", torch.arange(4, dtype=torch.float32).reshape(2, 2), torch.Tensor, torch.equal),
                  ("ndarray", np.arange(4, dtype=np.float32).reshape(2, 2), np.ndarray, np.array_equal))
        for kind, value, target_type, equal in values:
            control = list(MsgpackEncoder(size_threshold=0).encode(value))
            decoder = MsgpackDecoder(target_type)
            for name, frames in typed_frame_cases(MsgpackEncoder, value, msgpack):
                row = collect_typed_trial(decoder, frames, case_id=kind + "-" + name,
                    control_frames=control, control_check=lambda decoded: equal(value, decoded))
                report["trials"].append(row)
        report["status"] = "completed"
        report["all_normal_controls_passed"] = all(row[phase]["outcome"] == "decoded_matches"
            and row[phase]["aux_buffers_cleared"] for row in report["trials"] for phase in ("normal_before", "normal_after"))
        report["all_aux_references_cleared"] = all(row["case_aux_buffers_cleared"] and row["final_aux_buffers_cleared"] for row in report["trials"])
        _verify_source(root, expected_commit=pin["commit"])
        verify_source(root)
    except Exception as error:
        report.update(status="execution_error", exception_type=type(error).__name__)
    finally:
        if previous is None:
            os.environ.pop("VLLM_ALLOW_INSECURE_SERIALIZATION", None)
        else:
            os.environ["VLLM_ALLOW_INSECURE_SERIALIZATION"] = previous
        report["parent_seconds"] = time.perf_counter() - start
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("untyped", "typed"), default="untyped")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run_typed(args.source.resolve()) if args.profile == "typed" else run(args.source.resolve())
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    if args.profile == "typed":
        print(f"Typed decoder CPU: {len(report['trials'])} cases; {report['status']}; enterprise policy unset")
        return 0 if (report['status'] == 'completed' and report.get('all_normal_controls_passed')
                     and report.get('all_aux_references_cleared')) else 2
    print(f"Actual decoder CPU: {len(report['trials'])} cases; reference assertions passed; enterprise policy unset")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
