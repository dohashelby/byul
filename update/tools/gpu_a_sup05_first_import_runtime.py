"""Isolated stdlib entry point for the exact normal SUP-05 launch."""

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import sys
import time
from email.parser import BytesHeaderParser


ROOT = "/guard-lifecycle"
DATA = ROOT + "/data"
IMPORT_DIRECTORY = "python"
IMPORT_ROOT = ROOT + "/" + IMPORT_DIRECTORY
BOOTSTRAP_NAME = "sitecustomize.py"
WAITING = "first-import-waiting.json"
RELEASE = "first-import-release.json"
WAITING_SCHEMA = "guard-sup05-first-import-waiting/1"
RELEASE_SCHEMA = "guard-sup05-first-import-release/1"
MAX_BYTES = 262144
TRUSTED_ASSET_UID = 0


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(raw):
    def pairs(items):
        values = {}
        for key, value in items:
            if key in values:
                raise ValueError
            values[key] = value
        return values
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _read(directory, name):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(name, flags, dir_fd=directory), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_BYTES:
            raise ValueError
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if len(raw) != before.st_size or _identity(before) != _identity(after) or _identity(after) != _identity(current):
            raise ValueError
        return raw


def _matches_fragment(raw, value, pin):
    count, before, after = pin
    needle = value.encode("ascii")
    return (raw[count:count + len(needle)] == needle and _sha(raw[:count]) == before
            and _sha(raw[count + len(needle):]) == after)


def verify_release(expected, waiting_raw, release_raw, assets):
    """Check the fixed permit and the exact reviewed observer bytes. Pure offline helper too."""
    release = _json(release_raw)
    if (type(release) is not dict or release.keys() != {
            "schema", "waiting_sha256", "profile", "session", "manifest_sha256", "environment_ref", "target_sha256"}
            or release["schema"] != RELEASE_SCHEMA or release["waiting_sha256"] != _sha(waiting_raw)
            or release["profile"] != expected["profile"] or type(release["session"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", release["session"]) is None):
        raise ValueError
    session = release["session"]
    manifest_raw = assets["manifest.json"]
    manifest = _json(manifest_raw)
    files = manifest["assets"]
    if (_sha(manifest_raw) != release["manifest_sha256"] or manifest["schema"] != "guard-lifecycle-assets/1"
            or manifest["session"] != session or manifest["profile"] != expected["profile"]
            or set(files) != set(expected["assets"]) or set(assets) != {"manifest.json", *files}):
        raise ValueError
    for name, item in files.items():
        raw = assets[name]
        if type(item) is not dict or item != {"bytes": len(raw), "sha256": _sha(raw)}:
            raise ValueError
    if any(_sha(assets[name]) != digest for name, digest in expected["fixed"].items()):
        raise ValueError
    scope_raw = assets["model-read-scope.json"]
    scope = _json(scope_raw)
    directory = DATA + "/" + session
    settings = {"schema": expected["settings_schema"], "session": session,
                "output_dir": directory, "control_path": directory + "/SEALED"}
    sensor = assets["guard_lifecycle_sensor.py"]
    if (_json(assets["settings.json"]) != settings or scope["session"] != session
            or scope["environment_ref"] != release["environment_ref"] or scope["target_sha256"] != release["target_sha256"]
            or scope["hash_model_bytes"] is not True or scope["nonsecret_model_weights"] is not True
            or not _matches_fragment(sensor, _sha(scope_raw), expected["sensor"])
            or not _matches_fragment(assets["sitecustomize.py"], _sha(sensor), expected["bootstrap"])):
        raise ValueError
    return session


def _trusted_directory(directory, path):
    info = os.fstat(directory)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != TRUSTED_ASSET_UID
            or stat.S_IMODE(info.st_mode) & 0o022
            or _identity(info) != _identity(os.stat(path, follow_symlinks=False))):
        raise ValueError
    return info


def _fixed_inventory(directory, names, subdirectories):
    pending = names.union(subdirectories)
    seen = {}
    reader = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        with os.scandir(reader) as entries:
            for entry in entries:
                name = entry.name
                if name not in pending:
                    raise ValueError
                pending.remove(name)
                info = entry.stat(follow_symlinks=False)
                if name in subdirectories:
                    expected = subdirectories[name]
                    if not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != (expected.st_dev, expected.st_ino):
                        raise ValueError
                else:
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != TRUSTED_ASSET_UID
                            or stat.S_IMODE(info.st_mode) & 0o022):
                        raise ValueError
                    seen[name] = _identity(info)
    finally:
        os.close(reader)
    if pending:
        raise ValueError
    return seen


def _inventory(directory, data, imports, expected):
    """Verify fixed asset names and the isolated read-only Python import directory."""
    root = _trusted_directory(directory, ROOT)
    import_root = _trusted_directory(imports, IMPORT_ROOT)
    output = os.fstat(data)
    current = os.stat(DATA, follow_symlinks=False)
    if (output.st_uid != os.geteuid() or stat.S_IMODE(output.st_mode) != 0o700
            or (output.st_dev, output.st_ino) != (current.st_dev, current.st_ino)):
        raise ValueError
    files = _fixed_inventory(directory, {"manifest.json", RELEASE, *expected["assets"]},
                             {"data": output, IMPORT_DIRECTORY: import_root})
    import_files = _fixed_inventory(imports, {BOOTSTRAP_NAME}, {})
    return _identity(root), files, _identity(import_root), import_files


def _release_assets(directory, imports, expected):
    assets = {name: _read(directory, name) for name in ("manifest.json", *expected["assets"])}
    if _read(imports, BOOTSTRAP_NAME) != assets[BOOTSTRAP_NAME]:
        raise ValueError
    return assets


def _engine_env(environment):
    return {**environment, "PYTHONPATH": IMPORT_ROOT, "GUARD_LIFECYCLE_CONFIG": ROOT + "/settings.json"}


def _installed_version(expected, flags):
    root = os.open(expected["install_root"], flags)
    try:
        names = []
        with os.scandir(root) as entries:
            for index, entry in enumerate(entries):
                if index >= 4096:
                    raise ValueError
                if re.fullmatch(r"vllm-[a-zA-Z0-9._+-]+\.dist-info", entry.name):
                    names.append(entry.name)
        name, = names
        folder = os.open(name, flags, dir_fd=root)
        try:
            metadata = BytesHeaderParser().parsebytes(_read(folder, "METADATA"))
        finally:
            os.close(folder)
    finally:
        os.close(root)
    versions = metadata.get_all("Version")
    if metadata.get_all("Name") != ["vllm"] or versions != [expected["version"]]:
        raise ValueError
    return versions[0]


def _verify_launch_files(expected, flags):
    """Verify interpreter identity from the installed console script without starting it."""
    directory = os.open("/usr/local/bin", flags)
    try:
        if shutil.which("vllm") != "/usr/local/bin/vllm":
            raise ValueError
        line = _read(directory, "vllm").split(b"\n", 1)[0]
        if not re.fullmatch(rb"#!/[a-zA-Z0-9._/-]+", line) or not os.path.samefile(line[2:].decode("ascii"), sys.executable):
            raise ValueError
        if expected["wrapper"] is not None:
            path, digest = expected["wrapper"]
            if os.path.dirname(path) != "/usr/local/bin" or _sha(_read(directory, os.path.basename(path))) != digest:
                raise ValueError
    finally:
        os.close(directory)


def launch(expected):
    """Wait as PID 1 and replace it with the immutable reviewed command."""
    try:
        if (os.getpid() != 1 or os.geteuid() != 2000 or sys.platform != "linux" or os.uname().machine != "x86_64"
                or not sys.flags.isolated or not sys.flags.no_site or not sys.flags.dont_write_bytecode
                or sys.implementation.name + "-" + ".".join(map(str, sys.version_info[:3])) != expected["abi"]
                or sys.argv[1:] != expected["argv"] or os.environ.get("VLLM_USE_V2_MODEL_RUNNER") != "0"
                or any(name == "vllm" or name.startswith("vllm.") for name in sys.modules)):
            raise ValueError
        def stop(_signum, _frame):
            raise SystemExit("SUP05_FIRST_IMPORT_STOPPED")
        signal.signal(signal.SIGTERM, stop)
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY
        _verify_launch_files(expected, flags)
        version = _installed_version(expected, flags)
        root = os.open(ROOT, flags)
        try:
            data = os.open(DATA, flags)
        except OSError:
            os.close(root)
            raise
        try:
            mode = os.fstat(data)
            if mode.st_uid != os.geteuid() or stat.S_IMODE(mode.st_mode) != 0o700:
                raise ValueError
            owner = {key: os.environ[name] for key, name in expected["identity"].items()}
            row = {"schema": WAITING_SCHEMA, "owner": owner, "run_id": expected["run_id"], "pid": 1,
                   "nonce": os.urandom(16).hex(), "launch_sha256": expected["launch_sha256"],
                   "observed_engine_version": version}
            waiting_raw = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            marker_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            with os.fdopen(os.open(WAITING, marker_flags, 0o600, dir_fd=data), "wb") as stream:
                if stream.write(waiting_raw) != len(waiting_raw):
                    raise OSError
            end = time.monotonic() + 600
            while True:
                if time.monotonic() >= end:
                    raise SystemExit("SUP05_FIRST_IMPORT_TIMED_OUT")
                try:
                    os.stat(RELEASE, dir_fd=root, follow_symlinks=False)
                    break
                except FileNotFoundError:
                    time.sleep(0.25)
            imports = os.open(IMPORT_DIRECTORY, flags, dir_fd=root)
            try:
                inventory = _inventory(root, data, imports, expected)
                release = _read(root, RELEASE)
                assets = _release_assets(root, imports, expected)
                session = verify_release(expected, waiting_raw, release, assets)
                os.mkdir(session, mode=0o700, dir_fd=data)
                _verify_launch_files(expected, flags)
                if _installed_version(expected, flags) != version or _inventory(root, data, imports, expected) != inventory:
                    raise ValueError
            finally:
                os.close(imports)
        finally:
            os.close(data)
            os.close(root)
        argv = [*expected["command"], *expected["argv"]]
        os.execvpe(argv[0], argv, _engine_env(os.environ))
    except (AttributeError, KeyError, OSError, RecursionError, TypeError, ValueError):
        raise SystemExit("SUP05_FIRST_IMPORT_UNVERIFIED") from None
