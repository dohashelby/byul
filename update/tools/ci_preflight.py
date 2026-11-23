"""Report checkout pin and profile problems without preparing an engine run."""
from __future__ import annotations

from collections import defaultdict
import importlib
import json
from pathlib import Path
import re

from tools import verify_req10_consumption as source_review


REPO = Path(__file__).resolve().parents[2]
ADAPTERS = Path("update/src/guard/adapters")
ASSETS = ADAPTERS / "assets"
ABI_CATALOG = ASSETS / "vllm_observer_abi.json"
ENGINE_CATALOG = ASSETS / "engine_profiles.json"
LIFECYCLE_CATALOG = ASSETS / "vllm_native_release_rows.json"
RECEIPTS = Path("update/tools/reviewed_manifest_receipts.json")
SHA256 = re.compile(r"[0-9a-f]{64}")
SOURCE_FILE = re.compile(r"[a-z][a-z0-9_]*\.py")


def attempt(issues, context, read):
    try:
        return read()
    except (OSError, ValueError, ImportError, SyntaxError, KeyError, TypeError, AttributeError) as exc:
        issues.append(f"{context}: {type(exc).__name__}: {exc}")
        return None


def load_mapping(path):
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict):
        raise ValueError("expected a JSON object")
    return data


def check_inventory(repo, current, issues):
    path = repo / source_review.MANIFEST_PATH
    if path.is_symlink():
        issues.append(f"{source_review.MANIFEST_PATH}: expected a regular file")
        return
    manifest = attempt(issues, source_review.MANIFEST_PATH, lambda: load_mapping(path))
    if manifest is None:
        return
    expected = manifest.get("files")
    if (manifest.get("schema") != source_review.MANIFEST_SCHEMA
            or manifest.get("paths") != list(source_review.PINNED_PATHS)
            or not isinstance(expected, dict)):
        issues.append(f"{source_review.MANIFEST_PATH}: invalid source inventory")
        return
    for name in sorted(expected.keys() | current.keys()):
        if name not in current:
            issues.append(f"{name}: missing from checkout (pinned by {source_review.MANIFEST_PATH})")
        elif name not in expected:
            issues.append(f"{name}: missing pin in {source_review.MANIFEST_PATH}")
        elif expected[name] != current[name]:
            issues.append(f"{name} ({source_review.MANIFEST_PATH}): expected {expected[name]}, found {current[name]}")


def check_receipt(repo, issues):
    receipts = attempt(issues, RECEIPTS, lambda: source_review.read_receipts(repo / RECEIPTS))
    if (repo / source_review.MANIFEST_PATH).is_symlink():
        return
    digest = attempt(issues, source_review.MANIFEST_PATH, lambda: source_review.manifest_id(repo))
    if receipts is None or digest is None:
        return
    matches = sum(row["manifest_sha256"] == digest for row in receipts)
    if matches != 1:
        issues.append(f"{RECEIPTS}: source inventory {digest} has {matches} matching review records (expected 1)")


def check_source_pin(filename, expected, consumer, current, issues):
    if (not isinstance(filename, str) or not SOURCE_FILE.fullmatch(filename)
            or not isinstance(expected, str) or not SHA256.fullmatch(expected)):
        issues.append(f"{consumer}: invalid source pin declaration for {filename!r}")
        return
    path = (ADAPTERS / filename).as_posix()
    actual = current.get(path, "missing")
    if actual != expected:
        issues.append(f"{path} ({consumer}): expected {expected}, found {actual}")


def check_observer_pins(catalog, current, issues):
    entries = [("base", catalog.get("base")), *sorted(catalog["observers"].items())]
    for name, entry in entries:
        consumer = f"{ABI_CATALOG}, {name}"
        if not isinstance(entry, dict):
            issues.append(f"{consumer}: invalid observer pin declaration")
            continue
        check_source_pin(entry.get("file"), entry.get("sha256"), consumer, current, issues)
        if "dependency" in entry:
            dependency = entry["dependency"]
            if not isinstance(dependency, dict):
                issues.append(f"{consumer}: invalid dependency pin declaration")
                continue
            check_source_pin(dependency.get("file"), dependency.get("sha256"), consumer, current, issues)


def check_lifecycle_pins(current, issues):
    # These declarations are also used by startup_assets() and stream_assets().
    # Read them independently of the catalog so collisions do not hide pin errors.
    startup = load_adapter("vllm_baseline_startup_composition", issues)
    profiler = load_adapter("vllm_baseline_profiler_composition", issues)
    stream = load_adapter("vllm_baseline_stream_composition", issues)
    if startup is not None:
        profiles = startup.STARTUP_PROFILE
        if profiler is not None:
            profiles += f", {profiler.PROFILER_PROFILE}"
        pins = attempt(issues, profiles, lambda: [
            (logical, digest) for logical, (_, digest) in startup.DEPENDENCIES.items()
        ])
        for logical, digest in pins or []:
            check_source_pin(f"{logical}.py", digest, profiles, current, issues)
    if profiler is not None:
        pins = attempt(issues, profiler.PROFILER_PROFILE, lambda: [
            (logical, digest) for logical, digest in profiler.EXTRA_ASSETS.values()
        ])
        for logical, digest in pins or []:
            check_source_pin(f"{logical}.py", digest, profiler.PROFILER_PROFILE, current, issues)
    if stream is not None:
        check_source_pin("vllm_stream_buffer_sensor.py", stream.EXTENSION_SHA256, stream.PROFILE, current, issues)


def load_adapter(name, issues):
    return attempt(issues, ADAPTERS / f"{name}.py", lambda: importlib.import_module(f"guard.adapters.{name}"))


def check_identities(rows, fields, issues):
    for field in fields:
        consumers = defaultdict(list)
        for context, row in rows:
            value = row.get(field) if isinstance(row, dict) else None
            if not isinstance(value, str) or not value:
                issues.append(f"{context}: missing or invalid {field}")
            else:
                consumers[value].append(context)
        for value, locations in sorted(consumers.items()):
            if len(locations) > 1:
                issues.append(f"duplicate {field} {value!r}: {', '.join(locations)}")


def profile_rows(repo, path):
    rows = load_mapping(repo / path).get("profiles")
    if not isinstance(rows, list):
        raise ValueError("expected a profiles array")
    return [(f"{path}, profiles[{index}]", row) for index, row in enumerate(rows)]


def check_lifecycle_points(configured_rows, issues):
    for context, row in configured_rows:
        sources = row.get("sources") if isinstance(row, dict) else None
        if not isinstance(sources, dict):
            continue  # The product catalog reports malformed rows independently.
        owners = defaultdict(list)
        for module, source in sources.items():
            classes = source.get("classes") if isinstance(source, dict) else None
            if not isinstance(classes, dict):
                continue
            for name, methods in classes.items():
                if isinstance(methods, list):
                    for method in methods:
                        if isinstance(method, str):
                            owners[f"{name}.{method}"].append(module)
        for point, modules in sorted(owners.items()):
            if len(modules) > 1:
                issues.append(f"{context} ({row.get('profile')}): duplicate point {point!r}: {', '.join(modules)}")


def check_profiles(repo, issues):
    engine_rows = attempt(issues, ENGINE_CATALOG, lambda: profile_rows(repo, ENGINE_CATALOG))
    if engine_rows is not None:
        check_identities(engine_rows, ("id",), issues)
    attempt(issues, ENGINE_CATALOG, lambda: importlib.import_module("guard.adapters.engine_profiles"))

    configured_rows = attempt(issues, LIFECYCLE_CATALOG, lambda: profile_rows(repo, LIFECYCLE_CATALOG))
    release_path = ADAPTERS / "vllm_release_observation_sensor.py"
    release = load_adapter("vllm_release_observation_sensor", issues)
    release_rows = []
    if release is not None:
        release_rows = attempt(issues, release_path, lambda: [
            (f"{release_path}, PROFILES[{name!r}]", {"profile": profile, "schema": schema})
            for name, (profile, schema) in release.PROFILES.items()
        ]) or []
    check_identities([*(configured_rows or []), *release_rows], ("profile", "schema"), issues)
    check_lifecycle_points(configured_rows or [], issues)
    load_adapter("vllm_lifecycle_profiles", issues)
    catalog = attempt(issues, LIFECYCLE_CATALOG, lambda: importlib.import_module("guard.adapters.vllm_lifecycle_catalog"))
    if catalog is not None:
        for profile in catalog.ROWS:
            attempt(issues, f"{LIFECYCLE_CATALOG}, {profile}", lambda: catalog.runtime_spec(profile))


def collect_issues(repo):
    issues = []
    current = attempt(issues, "reviewed source files", lambda: source_review.scan(repo))
    check_receipt(repo, issues)
    if current is not None:
        check_inventory(repo, current, issues)
        abi = attempt(issues, ABI_CATALOG, lambda: importlib.import_module("guard.adapters.vllm_observer_abi"))
        if abi is not None:
            catalog = attempt(issues, ABI_CATALOG, lambda: abi.read_catalog(repo / ABI_CATALOG))
            if catalog is not None:
                check_observer_pins(catalog, current, issues)
        attempt(issues, "lifecycle dependency pins", lambda: check_lifecycle_pins(current, issues))
    check_profiles(repo, issues)
    return issues


def main():
    issues = collect_issues(REPO)
    print("### Update pin and profile preflight\n")
    if not issues:
        print("Checkout source pins and profile declarations matched.")
        return 0
    for issue in issues:
        print("- " + issue.replace("\r", r"\r").replace("\n", r"\n"))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
