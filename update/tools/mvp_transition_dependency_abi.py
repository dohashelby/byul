"""Compile pinned vLLM transition dependencies without importing the engine."""
from __future__ import annotations

import argparse
from inspect import CO_GENERATOR
import json
from pathlib import Path
import platform
import sys
from types import CodeType

from guard.adapters import vllm_ready_endpoint_contract as endpoint
from guard.adapters import vllm_weight_iteration_sensor as weight_iteration
from guard.adapters import vllm_worker_generation_contract as generation
from guard.adapters import vllm_worker_startup_contract as startup
from guard.adapters.vllm_lifecycle_sensor import code_digest
from guard.adapters.vllm_observer_abi import normalized
from guard.adapters.vllm_worker_startup_metadata import POINT_CODES as STARTUP_POINTS
from guard.models import sha256_hex
from guard.update_validation import _plain_path
from tools.mvp_a_input_preflight import _read
from tools.mvp_versions import versions

SCHEMA = "guard-mvp-transition-dependency-abi/1"
SYNC = "instrument_otel.<locals>.sync_wrapper"
_STARTUP_MODULES = (startup.WORKER, startup.TRACING, startup.OTEL, startup.UTILS)
FILES = {
    **{module: startup.SOURCE_FILENAMES[module] for module in _STARTUP_MODULES},
    **generation.FILENAMES, **endpoint.FILENAMES,
}
# The baseline pins come from the exact approved Linux amd64 GPU image's
# installed-source receipt. They are inputs to compile-time review only.
_BASELINE = {
    generation.MP: "3ed0149a8a8e1550c4f062af40e645d1b734e70f99981bc5bf15f0360507648a",
    generation.WRAPPER: "4a1a5266b912fe97991d8dc89d92b923990da9f6cb4b817d00d52e9c0d26dd7c",
    startup.WORKER: "6994436e4547c0996ab555b3e814e8ccea26b650dfc1b378b97d640745309347",
    startup.TRACING: "2d8167917906a5005629b018f79026f7229e3a6a22fa24663385908688eef261",
    startup.OTEL: "215e1f9f16f390124222eaf5b33ace987e059376623c3118a034f94ee00ddbb9",
    startup.UTILS: "2463b04ccfa8fc97d5cdb423accf50bb601c422322e72e241ccc0b1b2610305d",
    endpoint.UTILS: "837e6ff0ab524c06c048d6b42d4f35b77baba2e9d9e57837fe333ca0aaee1cfb",
}
SOURCE_PINS = {
    "baseline": _BASELINE,
    "candidate": {**{module: startup.SOURCE_SHA256[module] for module in _STARTUP_MODULES},
                  **generation.SOURCE_SHA256, **endpoint.SOURCE_SHA256},
}
IMAGE_DIGESTS = {
    "baseline": "sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
    "candidate": "sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b",
}
# The baseline files are bound by the SUP06 OCI receipt and its models/utils.py
# successor, recorded with the original bytes in the baseline fixture provenance.
_WEIGHT_SOURCE_PINS = {
    "baseline": {
        weight_iteration.DEFAULT: "9c9d54b1b650bf5affc924ebb8b8c73711e187ae7486cadd9f737bb1279cc7b8",
        weight_iteration.WEIGHTS: "7443e55184777dcc99f621b0d97d9149d52c2ea8b4304c6621eb4902e1c8ba5b",
        weight_iteration.MODELS: "570a2eb26c7274c9790b6ff42e3fd30403ad6534c68d256f4d867d22e2924919",
    },
    "candidate": weight_iteration.SOURCE_ADDITIONS,
}
_BASELINE_WEIGHT_SOURCE_BYTES = {
    weight_iteration.DEFAULT: 18754,
    weight_iteration.WEIGHTS: 60910,
    weight_iteration.MODELS: 41138,
}
# Linux CI source 0535361f8e875bd385a047ecf578676e0efbdf2d, job 136162548233.
# Compile-only ABI receipt SHA256 350f42e65b60ca64a52afc04a06654ddb76396e15f5c50e19c6eb963d9a428c2.
_BASELINE_WEIGHT_POINT_CODES = {
    "AutoWeightsLoader._load_module": "321e0b283b2c133350486a8ccc897370bd5d80a8d5083ef244760bcb06682603",
    "AutoWeightsLoader._load_param": "63099e39e6815bd09ddd0781f3d747213108f4d141f8d1b010fced01c5c83fdc",
    "DefaultModelLoader.get_all_weights": "49ef474e20290aab4cb7915a455378c541c80799a4f08e0d47530e7a27fcb6d2",
    "weight_utils.pt_weights_iterator": "a81ca82bf7221ef62e2bf5260c55685fc231a23060fbc1b48954b46cf2b50c6e",
    "weight_utils.safetensors_weights_iterator": "2247aec26ae2bc06bb5c676cae7d74b5c52a0335631c93b6bf9dd5446f8cf0f2",
}
_BASELINE_WEIGHT_IDENTITY = (
    "vllm", "0.30.0", "linux", "x86_64", "cpython-3.12.3", 0,
)
_BASELINE_WEIGHT_IMAGES = frozenset(
    repository + "@" + IMAGE_DIGESTS["baseline"]
    for repository in ("docker.io/vllm/vllm-openai", "vllm/vllm-openai")
)
# Independently read from installation layer 7 of each pinned OCI image.
_CONNECTION_SOURCE_PINS = dict.fromkeys(IMAGE_DIGESTS,
    "3b1591b340a6e8f68eab4af6a6ed158d51c9c35bb5b500827b677bf97e8afd51")


def _runtime():
    runtime = "cpython-" + ".".join(map(str, sys.version_info[:3]))
    if (sys.platform != "linux" or sys.implementation.name != "cpython" or sys.flags.optimize
            or runtime not in STARTUP_POINTS or runtime not in generation.POINT_CODES
            or runtime not in endpoint.POINT_CODES):
        raise ValueError("TRANSITION_DEPENDENCY_ABI_INTERPRETER_UNSUPPORTED")
    return runtime


def _point_codes(path, module, expected_sha256, points, *, filename=None, require_generator=False,
                 expected_bytes=None):
    filename = FILES[module] if filename is None else filename
    raw, _ = _read(_plain_path(path), max_bytes=131072)
    if (expected_bytes is not None and len(raw) != expected_bytes) or sha256_hex(raw) != expected_sha256:
        raise ValueError(f"TRANSITION_DEPENDENCY_SOURCE_MISMATCH: {module}")
    found = {}

    def visit(code):
        qualname = code.co_qualname
        if qualname in points:
            if qualname in found:
                raise ValueError(f"TRANSITION_DEPENDENCY_POINT_AMBIGUOUS: {module}.{qualname}")
            if require_generator and not code.co_flags & CO_GENERATOR:
                raise ValueError(f"TRANSITION_DEPENDENCY_GENERATOR_REQUIRED: {module}.{qualname}")
            found[qualname] = code_digest(normalized(code, filename))
        for value in code.co_consts:
            if type(value) is CodeType:
                visit(value)

    visit(compile(raw, filename, "exec", dont_inherit=True, optimize=0))
    if found.keys() != points:
        raise ValueError(f"TRANSITION_DEPENDENCY_POINT_UNAVAILABLE: {module}")
    return found


def _measure(side, directory, runtime):
    pins = SOURCE_PINS[side]
    points = {
        generation.MP: generation.TARGETS[generation.MP].keys() | endpoint.POINTS,
        generation.WRAPPER: generation.TARGETS[generation.WRAPPER].keys(),
        startup.WORKER: startup.SPAN_NAMES.keys(),
        startup.TRACING: {"instrument"},
        startup.OTEL: {*startup.BACKEND_NAMES, SYNC},
        startup.UTILS: set(),  # Source pin only in the existing startup qualifier.
        endpoint.UTILS: endpoint.HELPERS,
    }
    measured = {module: _point_codes(directory / filename, module, pins[module], points[module])
                for module, filename in FILES.items()}
    by_profile = {
        "/20": {**measured[startup.WORKER], "otel_sync": measured[startup.OTEL][SYNC]},
        "/21": {point: measured[module][point] for module, targets in generation.TARGETS.items()
                for point in targets} | {"otel-sync-wrapper": measured[startup.OTEL][SYNC]},
        "/22": {point: measured[generation.MP][point] for point in endpoint.POINTS}
        | measured[endpoint.UTILS],
    }
    approved = {
        "/20": {**STARTUP_POINTS[runtime]["worker"], "otel_sync": STARTUP_POINTS[runtime]["otel_sync"]},
        "/21": generation.POINT_CODES[runtime],
        "/22": endpoint.POINT_CODES[runtime],
    }
    matching = {profile: actual == approved[profile] for profile, actual in by_profile.items()}
    if side == "candidate" and not all(matching.values()):
        raise ValueError("TRANSITION_DEPENDENCY_REFERENCE_ABI_MISMATCH")
    return {
        "source_sha256": pins,
        "point_codes": by_profile,
        "matches_approved_point_codes": matching,
        "tracing_dependencies": {**measured[startup.TRACING],
                                 **{key: measured[startup.OTEL][key] for key in startup.BACKEND_NAMES}},
    }


def _measure_weight_iteration(side, directory, runtime):
    pins = _WEIGHT_SOURCE_PINS[side]
    approved = weight_iteration.RUNTIME_POINT_CODES.get(runtime)
    if approved is None or approved.keys() != weight_iteration.POINTS:
        raise ValueError("TRANSITION_WEIGHT_ITERATION_REFERENCE_ABI_UNAVAILABLE")
    modules = set(weight_iteration.POINT_SOURCES.values())
    if pins.keys() != modules:
        raise ValueError("TRANSITION_WEIGHT_ITERATION_SOURCE_SCOPE_INCOMPLETE")
    measured = {}
    source_sizes = _BASELINE_WEIGHT_SOURCE_BYTES if side == "baseline" else {}
    for module in sorted(modules):
        selected = {point for point, source in weight_iteration.POINT_SOURCES.items() if source == module}
        filename = module.replace(".", "/") + ".py"
        # /16 identifies module-level functions as weight_utils.<function> in its table.
        qualnames = {point.removeprefix("weight_utils."): point for point in selected}
        codes = _point_codes(directory / filename, module, pins[module], qualnames.keys(),
                              filename=filename, require_generator=True, expected_bytes=source_sizes.get(module))
        measured.update({qualnames[name]: digest for name, digest in codes.items()})

    matching = measured == approved
    if side == "candidate" and not matching:
        raise ValueError("TRANSITION_WEIGHT_ITERATION_REFERENCE_ABI_MISMATCH")
    return {
        "complete": True, "source_sha256": pins, "point_codes": measured,
        "matches_approved_point_codes": matching,
    }


def baseline_weight_profile(engine, version, image, *, sys_platform, machine, runtime, optimize):
    """Return a compile reference for the exact baseline identity, or None."""
    identity = (engine, version, sys_platform, machine, runtime, optimize)
    if (identity != _BASELINE_WEIGHT_IDENTITY or type(optimize) is not int
            or type(image) is not str or image not in _BASELINE_WEIGHT_IMAGES):
        return None
    return {
        "source_sha256": dict(_WEIGHT_SOURCE_PINS["baseline"]),
        "source_bytes": dict(_BASELINE_WEIGHT_SOURCE_BYTES),
        "point_codes": dict(_BASELINE_WEIGHT_POINT_CODES),
    }


def _measure_connection(path, side, runtime):
    generation_points = generation.CONNECTION_CODES.get(runtime)
    endpoint_points = endpoint.DELEGATE_CODES.get(runtime)
    if (generation.CONNECTION_FILENAME != endpoint.CONNECTION_FILENAME
            or generation_points is None or generation_points.keys() != {"send", "close"}
            or endpoint_points is None or endpoint_points.keys() != {"recv", "fileno"}):
        raise ValueError("TRANSITION_CONNECTION_REFERENCE_ABI_UNAVAILABLE")
    points = {f"_ConnectionBase.{name}": name for name in generation_points.keys() | endpoint_points.keys()}
    pin = _CONNECTION_SOURCE_PINS[side]
    codes = _point_codes(Path(path), "multiprocessing.connection", pin, points.keys(),
                         filename=generation.CONNECTION_FILENAME)
    measured = {points[qualname]: digest for qualname, digest in codes.items()}
    expected = {"/21": generation_points, "/22": endpoint_points}
    by_profile = {profile: {point: measured[point] for point in table} for profile, table in expected.items()}
    return {
        "source_scope": "image_installation_layer",
        "source_path": "usr/lib/" + generation.CONNECTION_FILENAME,
        "source_sha256": pin,
        "point_codes": by_profile,
        "matches_approved_point_codes": {profile: actual == expected[profile] for profile, actual in by_profile.items()},
    }


def measure(baseline, candidate, *, include_weight_iteration=False, baseline_connection=None, candidate_connection=None):
    """Return candidate inputs for review with the existing code hash semantics."""
    if (baseline_connection is None) != (candidate_connection is None):
        raise ValueError("TRANSITION_CONNECTION_PAIRED_INPUTS_REQUIRED")
    runtime = _runtime()
    releases = versions()
    if any(releases[side]["image"].rsplit("@", 1)[-1] != pin for side, pin in IMAGE_DIGESTS.items()):
        raise ValueError("TRANSITION_DEPENDENCY_IMAGE_MISMATCH")
    sides = {}
    for side, root, connection in (("baseline", baseline, baseline_connection),
                                   ("candidate", candidate, candidate_connection)):
        directory = Path(root)
        sides[side] = {"image": releases[side]["image"], **_measure(side, directory, runtime)}
        if include_weight_iteration:
            weights = _measure_weight_iteration(side, directory, runtime)
            if side == "baseline":
                release = releases[side]
                profile = baseline_weight_profile(release["engine"], release["version"], release["image"],
                    sys_platform=sys.platform, machine=platform.machine(), runtime=runtime, optimize=sys.flags.optimize)
                weights["matches_baseline_point_codes"] = None if profile is None else (
                    weights["source_sha256"] == profile["source_sha256"]
                    and weights["point_codes"] == profile["point_codes"])
            sides[side]["weight_iteration"] = weights
        if connection is not None:
            sides[side]["stdlib_connection"] = _measure_connection(connection, side, runtime)
    return {
        "schema": SCHEMA, "runtime": runtime, "engine_executed": False, "producer_registered": False,
        "sides": sides,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path, help="Pinned 0.30.0 extraction root containing vllm/")
    parser.add_argument("--candidate", required=True, type=Path, help="Pinned 0.31.0 extraction root containing vllm/")
    parser.add_argument("--weight-iteration", action="store_true",
        help="Also compile the three pinned /16 dependencies and verify their five generator points")
    parser.add_argument("--baseline-connection", type=Path, help="Pinned baseline installed connection.py source file")
    parser.add_argument("--candidate-connection", type=Path, help="Pinned candidate installed connection.py source file")
    args = parser.parse_args(argv)
    if (args.baseline_connection is None) != (args.candidate_connection is None):
        parser.error("--baseline-connection and --candidate-connection must be supplied together")
    print(json.dumps(measure(args.baseline, args.candidate, include_weight_iteration=args.weight_iteration,
        baseline_connection=args.baseline_connection, candidate_connection=args.candidate_connection),
        sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
