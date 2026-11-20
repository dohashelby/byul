"""Compile pinned vLLM transition dependencies without importing the engine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
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
# The saved SUP06 OCI receipt (SHA256 bb913cec393eb8bb8d2768a50cedc02f82c2481c0311713918cb3e23d969edc7)
# covers these two files only. It does not establish the installed models/utils.py.
_WEIGHT_SOURCE_PINS = {
    "baseline": {
        weight_iteration.DEFAULT: "9c9d54b1b650bf5affc924ebb8b8c73711e187ae7486cadd9f737bb1279cc7b8",
        weight_iteration.WEIGHTS: "7443e55184777dcc99f621b0d97d9149d52c2ea8b4304c6621eb4902e1c8ba5b",
    },
    "candidate": weight_iteration.SOURCE_ADDITIONS,
}


def _runtime():
    runtime = "cpython-" + ".".join(map(str, sys.version_info[:3]))
    if (sys.platform != "linux" or sys.implementation.name != "cpython" or sys.flags.optimize
            or runtime not in STARTUP_POINTS or runtime not in generation.POINT_CODES
            or runtime not in endpoint.POINT_CODES):
        raise ValueError("TRANSITION_DEPENDENCY_ABI_INTERPRETER_UNSUPPORTED")
    return runtime


def _point_codes(path, module, expected_sha256, points, *, filename=None):
    filename = FILES[module] if filename is None else filename
    raw, _ = _read(_plain_path(path), max_bytes=131072)
    if sha256_hex(raw) != expected_sha256:
        raise ValueError(f"TRANSITION_DEPENDENCY_SOURCE_MISMATCH: {module}")
    found = {}

    def visit(code):
        qualname = code.co_qualname
        if qualname in points:
            if qualname in found:
                raise ValueError(f"TRANSITION_DEPENDENCY_POINT_AMBIGUOUS: {module}.{qualname}")
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
    unverified = {}
    measured = {}
    for module in sorted(set(weight_iteration.POINT_SOURCES.values())):
        selected = {point for point, source in weight_iteration.POINT_SOURCES.items() if source == module}
        if module not in pins:
            unverified[module] = sorted(selected)
            continue
        filename = module.replace(".", "/") + ".py"
        # /16 identifies module-level functions as weight_utils.<function> in its table.
        qualnames = {point.removeprefix("weight_utils."): point for point in selected}
        codes = _point_codes(directory / filename, module, pins[module], qualnames.keys(), filename=filename)
        measured.update({qualnames[name]: digest for name, digest in codes.items()})

    complete = not unverified
    matching = measured == approved if complete else None
    if side == "candidate" and matching is not True:
        raise ValueError("TRANSITION_WEIGHT_ITERATION_REFERENCE_ABI_MISMATCH")
    return {
        "complete": complete, "source_sha256": pins, "point_codes": measured,
        "unverified_modules": unverified, "matches_approved_point_codes": matching,
    }


def measure(baseline, candidate, *, include_weight_iteration=False):
    """Return candidate inputs for review with the existing code hash semantics."""
    runtime = _runtime()
    releases = versions()
    if any(releases[side]["image"].rsplit("@", 1)[-1] != pin for side, pin in IMAGE_DIGESTS.items()):
        raise ValueError("TRANSITION_DEPENDENCY_IMAGE_MISMATCH")
    sides = {}
    for side, root in (("baseline", baseline), ("candidate", candidate)):
        directory = Path(root)
        sides[side] = {"image": releases[side]["image"], **_measure(side, directory, runtime)}
        if include_weight_iteration:
            sides[side]["weight_iteration"] = _measure_weight_iteration(side, directory, runtime)
    return {
        "schema": SCHEMA, "runtime": runtime, "engine_executed": False, "producer_registered": False,
        "sides": sides,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, type=Path, help="Pinned 0.30.0 extraction root containing vllm/")
    parser.add_argument("--candidate", required=True, type=Path, help="Pinned 0.31.0 extraction root containing vllm/")
    parser.add_argument("--weight-iteration", action="store_true",
        help="Also replay reviewed /16 weight-iterator dependencies and mark unverified installed slots")
    args = parser.parse_args(argv)
    print(json.dumps(measure(args.baseline, args.candidate, include_weight_iteration=args.weight_iteration),
        sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
