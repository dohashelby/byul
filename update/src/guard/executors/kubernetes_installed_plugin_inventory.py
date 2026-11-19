"""SUP-10 installed entry-point metadata on the selected owned engine."""

from pathlib import Path
import re

from guard.adapters.engine_profiles import supports
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import REQUEST_BINDING_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import supervised_source
from .plugin_loading import GROUPS

NORMAL_OPTION = "installed_plugin_inventory_only"
PROFILE = "kubernetes-owned-installed-plugin-inventory/1"
_CHECK_PROFILE = "supply-boot-plugin-v1"
_SCHEMA = "guard-installed-plugin-metadata/1"
_CAP = 8192
_LIMITS = {"root_count": 2, "directory_entries_per_root": 4096, "distribution_count": 1024, "entry_point_count": 16384,
           "metadata_file_bytes": 1048576, "entry_point_file_bytes": 262144, "total_metadata_bytes": 32 * 1024**2}
_READER = f"GROUPS = {GROUPS!r}\nSCHEMA = {_SCHEMA!r}\nLIMITS = {_LIMITS!r}\n" + r'''
import configparser
import hashlib
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import re
import stat
import sys
import sysconfig


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def directory(path):
    if not path.is_absolute() or '..' in path.parts or len(path.parts) > 16:
        raise ValueError()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open('/', flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        return os.dup(fd)
    finally:
        os.close(fd)


def bounded_text(fd, name, ceiling, *, optional=False):
    try:
        child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        if optional:
            return None
        raise
    with os.fdopen(child, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= ceiling:
            raise ValueError()
        raw = stream.read(before.st_size + 1)
        if (len(raw) != before.st_size or identity(os.fstat(stream.fileno())) != identity(before)
                or identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) != identity(before)):
            raise ValueError()
    return raw.decode('utf-8', 'strict')


def installed_roots():
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise ValueError()
    executable = Path(sys.executable)
    prefix = Path(sys.prefix)
    if executable.parent.name == 'bin':
        candidate = executable.parent.parent
        with_directory = directory(candidate)
        try:
            try:
                venv = os.stat('pyvenv.cfg', dir_fd=with_directory, follow_symlinks=False)
            except FileNotFoundError:
                venv = None
            if venv is not None:
                if not stat.S_ISREG(venv.st_mode):
                    raise ValueError()
                prefix = candidate
        finally:
            os.close(with_directory)
    roots = sorted({Path(sysconfig.get_path(key, vars={'base': str(prefix), 'platbase': str(prefix)}))
                    for key in ('purelib', 'platlib')})
    if not 1 <= len(roots) <= LIMITS['root_count'] or any(not path.is_relative_to(prefix) or path == prefix
            or path.name not in {'site-packages', 'dist-packages'} for path in roots):
        raise ValueError()
    return roots


class InstalledMetadata(metadata.Distribution):
    def __init__(self, fields):
        self.fields = fields

    def read_text(self, filename):
        return self.fields.get(filename)

    def locate_file(self, path):
        raise ValueError('Installed metadata reader permits only the supplied text')


def scan(roots, expected_name, expected):
    identities = []
    engine_identities = []
    entries = {group: [] for group in GROUPS}
    entry_total = 0
    bytes_read = 0
    for root in roots:
        root_fd = directory(root)
        try:
            root_before = identity(os.fstat(root_fd))
            with os.scandir(root_fd) as listing:
                for index, item in enumerate(listing):
                    if index >= LIMITS['directory_entries_per_root']:
                        raise ValueError()
                    if not item.name.endswith('.dist-info'):
                        continue
                    if len(identities) >= LIMITS['distribution_count']:
                        raise ValueError()
                    fd = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
                    try:
                        before = identity(os.fstat(fd))
                        fields = {'METADATA': bounded_text(fd, 'METADATA', LIMITS['metadata_file_bytes']),
                                  'entry_points.txt': bounded_text(fd, 'entry_points.txt', LIMITS['entry_point_file_bytes'],
                                                                   optional=True)}
                        bytes_read += sum(len(value.encode('utf-8')) for value in fields.values() if value is not None)
                        if bytes_read > LIMITS['total_metadata_bytes']:
                            raise ValueError()
                        dist = InstalledMetadata(fields)
                        description = dist.metadata
                        name, = description.get_all('Name', [])
                        version, = description.get_all('Version', [])
                        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name)
                                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}', version)):
                            raise ValueError()
                        name = re.sub(r'[-_.]+', '-', name).lower()
                        identity_sha = digest([name, version])
                        identities.append(identity_sha)
                        if digest(name) == expected_name:
                            engine_identities.append(identity_sha)
                        text = fields['entry_points.txt']
                        if text is not None:
                            parser = configparser.ConfigParser(interpolation=None, delimiters=('=',))
                            parser.optionxform = str
                            parser.read_string(text)
                            if parser.defaults():
                                raise ValueError()
                            selected = sorted((section, key, value) for section in parser.sections()
                                              for key, value in parser.items(section, raw=True))
                            parsed = sorted((entry.group, entry.name, entry.value) for entry in dist.entry_points)
                            entry_total += len(parsed)
                            if parsed != selected or entry_total > LIMITS['entry_point_count']:
                                raise ValueError()
                            for group, entry, value in parsed:
                                if group in entries:
                                    if not 0 < len(entry) <= 256 or not 0 < len(value) <= 4096:
                                        raise ValueError()
                                    entries[group].append(digest([identity_sha, group, entry, value]))
                        if identity(os.stat(item.name, dir_fd=root_fd, follow_symlinks=False)) != before:
                            raise ValueError()
                    finally:
                        os.close(fd)
            check = directory(root)
            try:
                if identity(os.fstat(check)) != root_before or identity(os.fstat(root_fd)) != root_before:
                    raise ValueError()
            finally:
                os.close(check)
        finally:
            os.close(root_fd)
    if engine_identities != [expected]:
        raise ValueError()
    return {'schema': SCHEMA, 'roots_sha256': digest([str(root) for root in roots]), 'root_count': len(roots),
            'distribution_count': len(identities), 'distributions_sha256': digest(sorted(identities)),
            'engine_distribution_sha256': expected, 'groups': [
                {'group_sha256': digest(group), 'entry_count': len(values), 'entries_sha256': digest(sorted(values))}
                for group, values in entries.items()]}


try:
    expected_name, expected = sys.argv[1:]
    if any(re.fullmatch(r'[0-9a-f]{64}', value) is None for value in (expected_name, expected)):
        raise ValueError()
    print(json.dumps(scan(installed_roots(), expected_name, expected), sort_keys=True, separators=(',', ':')))
except (OSError, ValueError, TypeError, configparser.Error):
    print(json.dumps({'schema': SCHEMA, 'unavailable': True}))
'''
_PROGRAM = supervised_source(_READER)
_PROGRAM_SHA256 = sha256_hex(_PROGRAM.encode())
_SHA = re.compile(r"[0-9a-f]{64}")
_GROUP_SHA256 = tuple(sha256_hex(canonical_json(group)) for group in GROUPS)
_EMPTY_GROUP_SHA256 = sha256_hex(canonical_json([]))
_MISSING = ["live_interpreter_discovery_paths", "approved_plugin_inventory", "effective_group_filter",
            "loader_reachability", "plugin_import", "hook_execution", "normal_approved_plugin_function"]


def installed_plugin_inventory_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Admit the exact non-lab target and existing closed group vocabulary without collection."""
    if (type(target) is not dict or target.get("lab") is not False or target.get("synthetic") is True
            or {group.partition(".")[0] for group in GROUPS} != {env.engine.name}
            or not supports(env.engine.name, env.engine.version)):
        raise ValueError("SUP10_INVENTORY_TARGET_UNVERIFIED")
    return KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner, profile=REQUEST_BINDING_PROFILE)


def _inventory(transport, expected_name, expected):
    before = transport._verify(capture_snapshot=True)["engine"]
    engine = transport.binding["engine"]
    remaining = min(5, transport.deadline - transport.clock())
    if remaining <= 0:
        raise ValueError("SUP10_INVENTORY_UNAVAILABLE")
    result = transport._call([f"--namespace={engine['namespace']}", "exec", engine["pod"],
        f"--container={engine['container']}", "--", "python3", "-I", "-S", "-B", "-c", _PROGRAM,
        str(remaining), expected_name, expected], cap=_CAP, budget=remaining)
    after = transport._verify(capture_snapshot=True)["engine"]
    if before["metadata"]["uid"] != after["metadata"]["uid"]:
        raise ValueError("SUP10_INVENTORY_UNAVAILABLE")
    if (type(result) is not dict or set(result) != {"schema", "roots_sha256", "root_count", "distribution_count",
                                                      "distributions_sha256", "engine_distribution_sha256", "groups"}
            or result["schema"] != _SCHEMA or result["engine_distribution_sha256"] != expected
            or type(result["root_count"]) is not int or not 1 <= result["root_count"] <= _LIMITS["root_count"]
            or type(result["distribution_count"]) is not int or not 1 <= result["distribution_count"] <= _LIMITS["distribution_count"]
            or any(type(result[key]) is not str or _SHA.fullmatch(result[key]) is None
                   for key in ("roots_sha256", "distributions_sha256"))
            or type(result["groups"]) is not list or len(result["groups"]) != len(_GROUP_SHA256)):
        raise ValueError("SUP10_INVENTORY_UNAVAILABLE")
    for row, group in zip(result["groups"], _GROUP_SHA256, strict=True):
        if (type(row) is not dict or set(row) != {"group_sha256", "entry_count", "entries_sha256"}
                or row["group_sha256"] != group or type(row["entry_count"]) is not int
                or not 0 <= row["entry_count"] <= _LIMITS["entry_point_count"]
                or type(row["entries_sha256"]) is not str or _SHA.fullmatch(row["entries_sha256"]) is None
                or (row["entry_count"] == 0) != (row["entries_sha256"] == _EMPTY_GROUP_SHA256)):
            raise ValueError("SUP10_INVENTORY_UNAVAILABLE")
    if sum(row["entry_count"] for row in result["groups"]) > _LIMITS["entry_point_count"]:
        raise ValueError("SUP10_INVENTORY_UNAVAILABLE")
    return result


class KubernetesInstalledPluginInventoryExecutor:
    name = "supply_boot"
    version = "0.1.0+k8s-installed-metadata"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "SUP-10"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE or check.policy_params
                or type(values) is not dict or values
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("SUP10_INVENTORY_SCOPE_INVALID")

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        duration = self.cfg.get("max_duration_seconds", 12)
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha = sha256_hex(canonical_json(target))
            if adapter is None or target_sha != self._expected_target_sha256:
                raise ValueError
            transport = installed_plugin_inventory_transport(target, env, duration=duration,
                binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self.runner)
        except (KubernetesBindingError, KeyError, OSError, TypeError, ValueError):
            raise ValueError("SUP10_INVENTORY_TARGET_UNVERIFIED") from None

        expected_name = sha256_hex(canonical_json(env.engine.name))
        expected = sha256_hex(canonical_json([env.engine.name, env.engine.version]))
        try:
            sample = _inventory(transport, expected_name, expected)
        except (KubernetesBindingError, KeyError, OSError, TypeError, ValueError):
            sample = None
        report = transport.report()
        cleanup = list(report["cleanup_errors"])
        collected = sample is not None and not cleanup and not report["collection_gap"]
        session = {"collection_profile": PROFILE, "scope": "isolated_interpreter_dist_info", "inventory": sample,
                   "inventory_limits": _LIMITS, "freshness": "stable_per_file_during_bounded_reads",
                   "inventory_source_sha256": _PROGRAM_SHA256, "target_sha256": target_sha,
                   "environment_sha256": env.ref().sha256, "binding_sha256": report["binding_sha256"]}
        missing = list(_MISSING) + ([] if collected else ["installed_metadata_inventory"])
        error = None if collected else "SUP10_INVENTORY_UNAVAILABLE"
        artifacts = []
        try:
            path = Path(workdir) / "installed_plugin_inventory.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json({"observed_session": session if collected else None, "missing": missing}))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("SUP10_INVENTORY_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        observations = {item.key: {"value": None, "collected": False} for item in check.required_observations}
        observations.update(supply_boot_sessions={"value": [session] if collected else None, "collected": collected},
                            supply_boot_missing={"value": missing, "collected": True},
                            supply_boot_complete={"value": False, "collected": True})
        settings = {NORMAL_OPTION: True, "max_duration_seconds": duration, "inventory_source_sha256": _PROGRAM_SHA256}
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None,
                            "detail": "Installed distribution metadata only. Loader and approval were not observed."},
            challenges=[{"step": item, "attempted": collected and index == 0} for index, item in enumerate(check.challenge_steps)],
            observations=observations, artifacts=artifacts,
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "groups": _GROUP_SHA256})),
                           "config_sha256": sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                                if target.get("comparison_config") else None,
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver, "cpu": env.runtime.cpu} if not error else {},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, started_at=started, finished_at=utcnow(),
            abort_reason=error, cleanup_errors=cleanup)
