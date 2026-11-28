"""Observe only the pinned stock Python interpreter and Qwen model startup."""

from pathlib import PurePosixPath
import re

from guard.configuration import BOOT_IDENTITY_OPTIONS, BOOT_IDENTITY_SCOPE_ERROR
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from .kubernetes_lifecycle_capture import supervised_source
from .kubernetes_normal_config_parser import (
    BOOTSTRAP, MONITOR, SESSION, KubernetesNormalConfigParserExecutor, _ns, _scope_reader_source,
)


MODEL_MODULE = "vllm.model_executor.models.qwen2_vl"
MODEL_QUALNAME = "Qwen2VLForConditionalGeneration"
INSTALL = "/usr/local/lib/python3.12/dist-packages/"
MODEL = INSTALL + "vllm/model_executor/models/qwen2_vl.py"
LOADER = INSTALL + "vllm/model_executor/model_loader/base_loader.py"
REGISTRY = INSTALL + "vllm/model_executor/models/registry.py"
LOADER_SHA = "5291c330c4ed5634a9ea1d535364c26162b60251aa2af03fd409b88c34d836e2"
REVIEWED = {
    "0.30.0": ("a08a98aaae52ced32226aa647f58d682ac600b9572651a9b377b97846bc99212",
               "24233e656920e3ae05c45d04993ddcaf4c87bf82d60b82b35305498b63356057"),
    "0.31.0": ("7f1d56e2f4c24ff7a949b0d4e796d6af70fa85a2b97c224652949f8d993dcc22",
               "5021855de72a2b1b04befcb2bb766eab17afd0e7892dc6d4135c8eeef8c99978"),
}
FUNCTIONS = {"model-registry-return": ("_ModelRegistry.resolve_model_cls", REGISTRY),
             "model-loader-return": ("BaseModelLoader.load_model", LOADER)}
MODES = {
    "SUP-11": ("supply-boot-python-mode-v1", "normal_python_runtime.json",
               ["other_interpreter_modes", "startup_mode_enforcement", "original_paired_control"]),
    "SUP-09": ("supply-boot-model-code-v1", "normal_model_class_origin.json",
               ["other_model_code_paths", "registration_or_resolver_enforcement", "original_paired_control"]),
}
FLAG_LIMITS = {"optimize": {0, 1, 2}, "isolated": {0, 1}, "ignore_environment": {0, 1},
               "no_site": {0, 1}, "no_user_site": {0, 1}}

_READER_BODY = r'''
def anchor(pid):
    proc = "/proc/" + str(pid)
    with open(proc + "/stat", "rb") as stream:
        ticks = int(stream.read(4096).rsplit(b")", 1)[1].split()[19])
    with open(proc + "/cmdline", "rb") as stream:
        cmd = stream.read(8193)
    if len(cmd) > 8192:
        raise ValueError("process command too long")
    argv = cmd.split(b"\x00")
    if pid == 1 and b"/usr/local/bin/vllm" in argv and b"/models" in argv:
        actor = "stock-vllm-cli"
    elif pid > 1 and (argv[0] in {b"VLLM::EngineCore", b"VLLM::EngineCore_DP0"}
            or (b"-c" in argv and re.fullmatch(rb"from multiprocessing\.spawn import spawn_main; spawn_main\([\w=, ]+\)",
                                              argv[argv.index(b"-c") + 1]))):
        actor = "stock-multiprocessing-spawn"
    else:
        raise ValueError("owned startup actor unavailable")
    with open("/proc/sys/kernel/random/boot_id", "rb") as source:
        boot = digest(source.read(64).strip())
    return dict(pid=pid, start_ticks=ticks, boot_sha256=boot, actor=actor,
                **{kind + "_namespace": os.stat(proc + "/ns/" + node).st_ino
                   for kind, node in (("pid", "pid"), ("mount", "mnt"), ("time", "time"))})

def process_ids(directory):
    fd = os.open(directory, os.O_DIRECTORY | os.O_RDONLY | os.O_NOFOLLOW)
    try:
        pids = []
        with os.scandir(fd) as directory_entries:
            for row in directory_entries:
                match = re.fullmatch(r"([1-9][0-9]{0,8})\.jsonl", row.name)
                if match is None or not row.is_file(follow_symlinks=False) or len(pids) >= 2:
                    raise ValueError("unexpected startup journal")
                pids.append(int(match[1]))
        if sorted(pids)[:1] != [1] or len(pids) != 2:
            raise ValueError("startup session incomplete")
        return sorted(pids)
    finally:
        os.close(fd)

profile_path = SITE + "byul_owned_trace_profile.json"
profile_raw, profile_stat = read_fixed(profile_path, 16384)
profile = json.loads(profile_raw)
session = profile["session"]
if not re.fullmatch(SESSION, session) or profile["journal_subdirectory"] != session:
    raise ValueError("trace session unavailable")
directory = "/cache/byul-boot-trace/" + session
pids = process_ids(directory)
processes = []
for pid in pids:
    subject = anchor(pid)
    raw, metadata = read_fixed(directory + "/" + str(pid) + ".jsonl", 65536)
    if anchor(pid) != subject:
        raise ValueError("startup actor replaced")
    processes.append({"subject": subject, "journal": raw.decode("utf-8"), "journal_sha256": digest(raw), "uid": metadata["uid"]})
if process_ids(directory) != pids or read_fixed(profile_path, 16384) != (profile_raw, profile_stat):
    raise ValueError("startup session replaced")
monitor = {name: digest(read_fixed(SITE + name, 65536)[0])
           for name in ("owned_startup_trace.py", "byul_owned_trace.pth")}
sources = {name: digest(read_fixed(name, 524288)[0]) for name in FILES}
print(json.dumps({"session": session, "profile_sha256": digest(profile_raw),
    "phase": profile["feature_settings"].get("normal_phase"), "approval_sha256": profile.get("trial_approval_sha256"),
    "monitor": monitor, "sources": sources, "processes": processes}))
'''


def _pinned_sources(version, parser_pins):
    registry, model = REVIEWED[version]
    return {**parser_pins,
            REGISTRY: registry, LOADER: LOADER_SHA, MODEL: model}


def _module_sha(name):
    relative = PurePosixPath(name).relative_to(INSTALL).with_suffix("")
    return sha256_hex(canonical_json(".".join(relative.parts)))


def _flags(observed):
    if (type(observed) is not dict or set(observed) != {*FLAG_LIMITS, "safe_path"}
            or type(observed["safe_path"]) is not bool
            or any(type(observed[key]) is not int or observed[key] not in wanted for key, wanted in FLAG_LIMITS.items())):
        raise ValueError("NORMAL_BOOT_IDENTITY_FLAGS_UNVERIFIED")
    return observed


def _process(record, session, pins, window, approval_sha256):
    path = record["subject"]
    raw = record["journal"].encode()
    rows = [strict_json(line, limit=4096) for line in raw.splitlines()]
    header = rows[0]
    low, high = (_ns(window[name]) for name in ("container_started_at", "ready_at"))
    if (not 1 <= len(rows) < 360 or not raw.endswith(b"\n") or sha256_hex(raw) != record["journal_sha256"]
            or not 0 < record["uid"] <= 2**32-1 or header["kind"] != "interpreter"
            or any(header.get(key) != value for key, value in path.items())
            or any(header["sources"].get(name) != pin for name, pin in pins.items() if name != MODEL)
            or header["trial_approval_sha256"] != approval_sha256
            or header["feature_settings"].get("normal_phase") != "normal-parser"
            or not low <= header["receipt_utc_ns"] <= high
            or any(row["seq"] != index or row["pid"] != path["pid"] or row["session"] != session
                   for index, row in enumerate(rows))):
        raise ValueError("NORMAL_BOOT_IDENTITY_PROCESS_UNVERIFIED")
    selected = [row for row in rows[1:] if low <= row["receipt_utc_ns"] <= high
                and row["monotonic_ns"] >= header["monotonic_ns"]]
    return header, selected, _flags(header["python_flags"])


def project(packet, version, window, check, *, parser_pins, approval_sha256):
    pins = _pinned_sources(version, parser_pins)
    session = packet["session"]
    processes = packet["processes"]
    expected = [item["subject"] for item in processes]
    namespace_keys = ("pid_namespace", "mount_namespace", "time_namespace", "boot_sha256")
    if (not re.fullmatch(SESSION, session) or packet["sources"] != pins or packet["phase"] != "normal-parser"
            or packet["approval_sha256"] != approval_sha256
            or packet["monitor"] != {"owned_startup_trace.py": MONITOR, "byul_owned_trace.pth": BOOTSTRAP}
            or len(processes) != 2 or expected[0]["pid"] != 1 or expected[1]["pid"] <= 1
            or [item["actor"] for item in expected] != ["stock-vllm-cli", "stock-multiprocessing-spawn"]
            or any(expected[0][key] != expected[1][key] for key in namespace_keys)):
        raise ValueError("NORMAL_BOOT_IDENTITY_INPUT_UNVERIFIED")
    matched = []
    for process in processes:
        header, rows, flags = _process(process, session, pins, window, approval_sha256)
        if check == "SUP-11":
            modules = {_module_sha(name): pin for name, pin in pins.items() if name != MODEL}
            observed = [row["loaded_module"] for row in rows if row["kind"] == "enter" and row.get("loaded_module")
                        and row["source_sha256"] == row["loaded_module"]["file_sha256"]
                        and modules.get(row["loaded_module"]["module_name_sha256"]) == row["source_sha256"]]
            if observed:
                matched.append({"role": "normal", "pid": header["pid"], "actor": header["actor"], "sources": observed,
                                "python_flags": flags, "session": session, "start_ticks": header["start_ticks"]})
            continue
        entered = {row["seq"]: row for row in rows if row["kind"] == "enter" and row.get("role") in FUNCTIONS}
        returns = {}
        for row in rows:
            if row.get("role") in FUNCTIONS and row["kind"] in {"return", "unwind"}:
                if row["call"] in returns:
                    raise ValueError("NORMAL_BOOT_IDENTITY_RETURN_AMBIGUOUS")
                returns[row["call"]] = row
        for call, end in returns.items():
            start = entered.get(call)
            role = end["role"]
            point, source = FUNCTIONS[role]
            if (end["kind"] != "return" or start is None or start["role"] != role or start["point"] != point
                    or start["source_sha256"] != pins[source] or not call < end["seq"]
                    or start["monotonic_ns"] > end["monotonic_ns"]):
                continue
            model = {"module": MODEL_MODULE, "qualname": MODEL_QUALNAME, "source_file": MODEL,
                     "source_sha256": pins[MODEL], "model": "/models", "trust_remote_code": False}
            if end["result"].get("model_class_origin") == model:
                matched.append({"role": "normal", "observed_return": role, "pid": header["pid"], "session": session,
                                "point": point, "call_return_seq": [call, end["seq"]], "model_origin": model})
    if check == "SUP-11":
        return matched if len(matched) == 2 else []
    return matched if set(item["observed_return"] for item in matched) == set(FUNCTIONS) else []


class KubernetesNormalBootIdentityExecutor(KubernetesNormalConfigParserExecutor):
    version = "0.1.0+k8s-normal-boot-identity"
    limit = 256 * 1024

    def validate_policy(self, check, values):
        if (check.id not in MODES or check.revision != 7 or check.executor != self.name
                or type(values) is not dict or values or type(self.cfg) is not dict):
            raise ValueError(BOOT_IDENTITY_SCOPE_ERROR)
        self.option = BOOT_IDENTITY_OPTIONS[check.id]
        if set(self.cfg) != {self.option, "target"} or self.cfg[self.option] is not True or type(self.cfg["target"]) is not str:
            raise ValueError(BOOT_IDENTITY_SCOPE_ERROR)
        profile, self.artifact_name, self.gaps = MODES[check.id]
        if check.collection_profile != profile:
            raise ValueError(BOOT_IDENTITY_SCOPE_ERROR)
        self.observation_profile = self.option
        self.gap_prefix = check.id.replace("-", "") + "_NORMAL_BOOT"
        self.check = check.id

    def reader_program(self, profile):
        sources = tuple(profile["source_pins"])
        return supervised_source(_scope_reader_source(sources)
                                 + f"FILES={(*sources, LOADER, REGISTRY, MODEL)!r}\n" + _READER_BODY)

    def project(self, observed, env, window, profile):
        return project(observed, env.engine.version, window, self.check,
                       parser_pins=profile["source_pins"], approval_sha256=self.expected_approval)

    def retain_snapshot(self, observed):
        return {**{key: value for key, value in observed.items() if key != "processes"},
                "processes": [{key: value for key, value in item.items() if key != "journal"}
                              for item in observed["processes"]]}
