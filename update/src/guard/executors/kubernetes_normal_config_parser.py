"""Collect the reviewed, ordinary Qwen config parser from one owned live Pod."""

import json
from pathlib import Path
import re
import stat

from guard.adapters.engine_provider import get_provider
from guard.adapters.vllm_file_read_sensor import dependency
from guard.configuration import SUP05_CONFIG_PARSER_OPTION
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import KubernetesBindingError
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import supervised_source
from .kubernetes_normal_compile_cache import _ns
from .kubernetes_normal_startup_load import _ready_window, _source_profile, _verify_launch, normal_startup_load_transport
from .kubernetes_supply_boot import _model_mount, _model_mount_sha256


PROFILE = "owned-normal-approved-config-parser/1"
VERSION = "0.1.0+k8s-normal-approved-config-parser"
MODEL = "Qwen/Qwen2-VL-2B-Instruct"
MODEL_REVISION = "895c3a49bc3fa70a340399125c650a463535e71c"
INVENTORY = "ad73950ffd6022ad452aef105d66caf2c623a3eeb7dc9eafe983ee416168d432"
CONFIG_SHA = "422adefa19e62dd175961cec85bc0400344fe5bf9b22bd1182e05aaae78556e0"
CONFIG_PATH = "/models/config.json"
CONFIG_SIZE = 1196
SITE = "/cache/home/.local/lib/python3.12/site-packages/"
MONITOR = "272e79f001680fda1e598ab0b8d350527de17ec627dd543013bed89817be4658"
BOOTSTRAP = sha256_hex(b"import owned_startup_trace; owned_startup_trace.bootstrap()\n")
SESSION = r"TRACE-[0-9A-HJKMNP-TV-Z]{26}"
LIMIT = 128 * 1024
READER_SECONDS = 5.0  # supervised_source accepts a positive window no greater than five seconds.
IDENTITY = ("device", "inode", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns")
FILE_FIELDS = ("st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size", "st_mtime_ns", "st_ctime_ns")
GAPS = ["alternate_loader_and_weight_file_scope", "file_link_or_mount_change", "replacement_approval_and_enforcement"]

_SCOPE_READER_BODY = r'''
import hashlib, json, os, re, stat

def fingerprint(st):
    return {name: getattr(st, key) for name, key in zip(NAMES, FIELDS, strict=True)}

def read_fixed(path, maximum):
    *parents, leaf = path.lstrip("/").split("/")
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open("/", flags)
    try:
        for directory in parents:
            child = os.open(directory, flags, dir_fd=root)
            os.close(root)
            root = child
        fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
                raise ValueError("selected file unavailable")
            raw = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            same_name = os.stat(leaf, dir_fd=root, follow_symlinks=False)
        if len(raw) != before.st_size or len(raw) > maximum or not fingerprint(before) == fingerprint(after) == fingerprint(same_name):
            raise ValueError("selected file changed")
        return raw, fingerprint(before)
    finally:
        os.close(root)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

'''


def _scope_reader_source(sources):
    return (f"SITE={SITE!r}\n" f"CONFIG={CONFIG_PATH!r}\n" f"SOURCES={tuple(sources)!r}\n"
            f"SESSION={SESSION!r}\n" f"FIELDS={FILE_FIELDS!r}\n" f"NAMES={IDENTITY!r}\n"
            + _SCOPE_READER_BODY)


_SNAPSHOT_BODY = r'''def anchor():
    with open("/proc/1/stat", "rb") as source:
        ticks = int(source.read(4096).rsplit(b")", 1)[1].split()[19])
    with open("/proc/1/cmdline", "rb") as source:
        argv = source.read(8193)
    if len(argv) > 8192 or @ARGV_TOKEN_MISSING@:
        raise ValueError("stock pid changed")
    with open("/proc/sys/kernel/random/boot_id", "rb") as source:
        boot_sha = digest(source.read(64).strip())
    return dict(pid=1, start_ticks=ticks, boot_sha256=boot_sha,
                **{kind + "_namespace": os.stat("/proc/1/ns/" + node).st_ino
                   for kind, node in (("pid", "pid"), ("mount", "mnt"), ("time", "time"))})

subject = anchor()
profile_path = SITE + "byul_owned_trace_profile.json"
profile_raw, profile_stat = read_fixed(profile_path, 16384)
profile = json.loads(profile_raw)
session = profile["session"]
if not re.fullmatch(SESSION, session) or profile["journal_subdirectory"] != session:
    raise ValueError("trace session unavailable")
monitor = {name: digest(read_fixed(SITE + name, 65536)[0])
           for name in ("owned_startup_trace.py", "byul_owned_trace.pth")}
sources = {name: digest(read_fixed(name, 524288)[0]) for name in SOURCES}
config, config_stat = read_fixed(CONFIG, 1196)
journal, journal_stat = read_fixed("/cache/byul-boot-trace/" + session + "/1.jsonl", 65536)
if (anchor() != subject or read_fixed(CONFIG, 1196) != (config, config_stat)
        or read_fixed(profile_path, 16384) != (profile_raw, profile_stat)):
    raise ValueError("selected boot changed")
print(json.dumps({"process": subject, "session": session, "monitor": monitor, "sources": sources,
    "profile_sha256": digest(profile_raw), "profile_phase": profile["feature_settings"].get("normal_phase"),
    "profile_approval_sha256": profile.get("trial_approval_sha256"),
    "config_sha256": digest(config), "config_identity": config_stat, "journal": journal.decode("utf-8"),
    "journal_uid": journal_stat["uid"], "journal_hash": digest(journal)}))
'''


def _snapshot_program(profile):
    sources = tuple(profile["source_pins"])
    missing = " or ".join(
        rf'b{json.dumps(token)} not in argv.split(b"\x00")' for token in profile["argv_tokens"]
    )
    return supervised_source(
        _scope_reader_source(sources) + _SNAPSHOT_BODY.replace("@ARGV_TOKEN_MISSING@", missing)
    )


def _parser_profile(env):
    profile = get_provider(env.engine.name).evidence_contract("config_parser_source", env)
    if (profile is None
            or (env.model.id, env.model.revision, env.model.inventory_sha256) != (MODEL, MODEL_REVISION, INVENTORY)):
        raise ValueError("SUP05_CONFIG_PARSER_SOURCE_UNQUALIFIED")
    return profile


def parser_transport(target, env, *, duration, binary="kubectl", runner=None):
    _parser_profile(env)
    return normal_startup_load_transport(target, env, duration=duration, binary=binary, runner=runner)


def _journal(checked, profile, window, *, approval_sha256):
    bounds = _ns(window["container_started_at"]), _ns(window["ready_at"])
    pins, points = profile["source_pins"], profile["points"]
    raw = checked["journal"].encode()
    rows = [strict_json(line, limit=4096) for line in raw.splitlines()]
    subject = checked["process"]
    identity = checked["config_identity"]
    session = checked["session"]
    if (not re.fullmatch(SESSION, session) or checked["profile_phase"] != "normal-parser"
            or checked["profile_approval_sha256"] != approval_sha256
            or checked["sources"] != pins or checked["monitor"] != {
                "owned_startup_trace.py": MONITOR, "byul_owned_trace.pth": BOOTSTRAP}
            or checked["config_sha256"] != CONFIG_SHA or identity["size"] != CONFIG_SIZE
            or set(identity) != set(IDENTITY) or not stat.S_ISREG(identity["mode"])
            or not 0 < checked["journal_uid"] <= 2**32-1
            or not raw.endswith(b"\n") or checked["journal_hash"] != sha256_hex(raw)
            or not 1 <= len(rows) < 360 or subject["pid"] != 1):
        raise ValueError("SUP05_CONFIG_PARSER_INPUT_UNVERIFIED")
    header = rows[0]
    if (header["kind"] != "interpreter" or header["actor"] != profile["actor"]
            or any(header.get(key) != value for key, value in subject.items())
            or any(header["sources"].get(path) != digest for path, digest in pins.items())
            or header["trial_approval_sha256"] != approval_sha256
            or header["feature_settings"].get("normal_phase") != "normal-parser"
            or not bounds[0] <= header["receipt_utc_ns"] <= bounds[1]
            or any(row["seq"] != index or row["pid"] != 1 or row["session"] != session
                   for index, row in enumerate(rows))):
        raise ValueError("SUP05_CONFIG_PARSER_PROCESS_UNVERIFIED")

    entries = {}
    returns = {}
    for row in rows[1:]:
        if (row.get("role") not in points or not bounds[0] <= row["receipt_utc_ns"] <= bounds[1]
                or row["monotonic_ns"] < header["monotonic_ns"]):
            continue
        if row["kind"] == "enter":
            point, filename, _ = points[row["role"]]
            if row["point"] == point and row["source_sha256"] == pins[filename]:
                entries[row["seq"]] = row
        elif row["kind"] in {"return", "unwind"}:
            if row["call"] in returns:
                raise ValueError("SUP05_CONFIG_PARSER_TERMINAL_AMBIGUOUS")
            returns[row["call"]] = row

    def pair(call, role):
        start, end = entries.get(call), returns.get(call)
        if (start is None or end is None or start["role"] != role or end["role"] != role or end["kind"] != "return"
                or not start["seq"] < end["seq"] or not start["monotonic_ns"] <= end["monotonic_ns"]
                or end["result"].get(points[role][2]) is not True):
            return None
        return start, end

    native = []
    for call, row in entries.items():
        if row["role"] != "config-parser":
            continue
        parser = pair(call, "config-parser")
        if parser is None:
            continue
        start, end = parser
        result = end["result"]
        observed_file = {"state": "file", **identity}
        if (start["artifact_path_sha256"] != sha256_hex(CONFIG_PATH.encode())
                or result.get("return_kind") != "dict" or result.get("same_artifact_path") is not True
                or start["artifact_at_entry"] != observed_file or result.get("artifact_at_return") != observed_file):
            continue
        hf = pair(start["parent_call"], "hf-config")
        engine = pair(hf[0]["parent_call"], "engine-config") if hf is not None else None
        if engine is None or not (engine[0]["seq"] < hf[0]["seq"] < start["seq"] < end["seq"]
                                  < hf[1]["seq"] < engine[1]["seq"]):
            continue
        native.append({"role": "normal", "file_path_sha256": start["artifact_path_sha256"], "config_sha256": CONFIG_SHA,
                       "eager_weight_reads_verified": False, "engine_pid": subject["pid"], "session": session,
                       "parser_call_return_seq": [start["seq"], end["seq"]],
                       "hf_call_return_seq": [item["seq"] for item in hf],
                       "engine_call_return_seq": [item["seq"] for item in engine],
                       "parser_receipt_utc_ns": [start["receipt_utc_ns"], end["receipt_utc_ns"]]})
    return native


class KubernetesNormalConfigParserExecutor:
    name = "supply_boot"
    version = VERSION
    observation_profile = PROFILE
    artifact_name = "normal_approved_config_parser.json"
    option = SUP05_CONFIG_PARSER_OPTION
    gaps = GAPS
    gap_prefix = "SUP05_CONFIG_PARSER"
    limit = LIMIT

    def __init__(self, cfg, *, runner=None, expected_target_sha256=None, expected_approval_sha256=None):
        self.cfg = cfg
        self.expected_approval = expected_approval_sha256
        self.runner = runner
        self.expected = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (check.id != "SUP-05" or check.revision != 8 or check.executor != self.name
                or check.collection_profile != "supply-boot-substitution-v1" or type(values) is not dict or values
                or type(self.cfg) is not dict or set(self.cfg) != {SUP05_CONFIG_PARSER_OPTION, "target"}
                or self.cfg[SUP05_CONFIG_PARSER_OPTION] is not True or type(self.cfg["target"]) is not str):
            raise ValueError("SUP05_CONFIG_PARSER_SCOPE_INVALID")

    def reader_program(self, profile):
        return _snapshot_program(profile)

    def project(self, observed, env, window, profile):
        return _journal(observed, profile, window, approval_sha256=self.expected_approval)

    def retain_snapshot(self, observed):
        return {key: value for key, value in observed.items() if key != "journal"}

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        if not isinstance(self.expected_approval, str) or not re.fullmatch(r"[0-9a-f]{64}", self.expected_approval):
            raise ValueError(f"{self.gap_prefix}_APPROVAL_UNVERIFIED")
        started = utcnow()
        target = strict_json(dependency(self.cfg["target"], None), limit=65536)
        target_sha = sha256_hex(canonical_json(target))
        if target_sha != self.expected:
            raise ValueError(f"{self.gap_prefix}_TARGET_CHANGED")
        profile = _parser_profile(env)
        program = self.reader_program(profile)
        transport = normal_startup_load_transport(target, env, duration=30, runner=self.runner)
        source = _source_profile(adapter, env)
        facts, bound = [], {}
        gap = f"{self.gap_prefix}_OWNER_UNVERIFIED"
        try:
            window, container = _ready_window(transport)
            if any(target["startup_log_prefix"][key] != value for key, value in window.items()):
                raise ValueError
            _verify_launch(target, container, source)
            mount = _model_mount_sha256(*_model_mount({"pod": transport._verify(capture_snapshot=True)["engine"]}))
            gap = f"{self.gap_prefix}_CAPTURE_UNVERIFIED"
            remaining = min(READER_SECONDS, transport.deadline - transport.clock())
            if remaining <= 0:
                raise ValueError
            engine = transport.binding["engine"]
            observed = transport._call([f"--namespace={engine['namespace']}", "exec", engine["pod"],
                f"--container={engine['container']}", "--", "python3", "-I", "-S", "-B", "-c", program,
                f"{remaining:.9f}"], cap=self.limit, budget=remaining)
            selected = self.project(observed, env, window, profile)
            after, selected_container = _ready_window(transport)
            _verify_launch(target, selected_container, source)
            after_mount = _model_mount_sha256(*_model_mount({"pod": transport._verify(capture_snapshot=True)["engine"]}))
            if after != window or mount != after_mount or transport.report()["collection_gap"]:
                raise ValueError
            bound = self.retain_snapshot(observed)
            facts = selected
            gap = None if facts else f"{self.gap_prefix}_CALL_RETURN_UNAVAILABLE"
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            pass
        cleanup = transport.report()["cleanup_errors"]
        summary = {"profile": self.observation_profile, "target_sha256": target_sha, "pod_uid": transport.binding["engine"]["pod_uid"],
                   "container_id": transport.binding["engine"]["container_id"], "image": env.engine.image_digest,
                   "environment_sha256": env.ref().sha256, "reader_sha256": sha256_hex(program.encode()),
                   "startup_snapshot": bound, "native": facts, "gap": gap, "missing": self.gaps}
        artifact = Path(workdir) / self.artifact_name
        artifact.parent.mkdir(parents=True, exist_ok=True)
        with artifact.open("xb") as stream:
            stream.write(canonical_json(summary))
        origin = DataOrigin(transport.process_observation_origin)
        if (gap or cleanup) and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        settings = {self.option: True}
        return EvidenceDraft(status=ExecStatus.ABORTED if gap or cleanup else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None, "detail": f"Owned ordinary startup: {self.observation_profile}."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations={"supply_boot_sessions": {"value": [summary] if bound else None, "collected": bool(bound)},
                "supply_boot_native_consumption": {"value": facts or None, "collected": bool(facts)},
                "supply_boot_missing": {"value": self.gaps + ([gap] if gap else []), "collected": True},
                "supply_boot_complete": {"value": False, "collected": True},
                "supply_boot_normal_verified": {"value": False, "collected": True},
                "supply_boot_record_authenticated": {"value": False, "collected": True}},
            started_at=started, finished_at=utcnow(), abort_reason=gap, cleanup_errors=cleanup,
            comparability={"fixture_sha256": sha256_hex(canonical_json(settings)),
                "config_sha256": sha256_hex(canonical_json(execution_conditions(target, settings, []))),
                "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={**settings, "target_sha256": target_sha}, artifacts=[
                DraftArtifact(name=artifact.name, path=artifact, collector=f"{self.name}/{self.version}")])
