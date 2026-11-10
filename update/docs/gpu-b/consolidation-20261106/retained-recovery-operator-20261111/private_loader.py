"""새 private source만 읽고 객체를 결합한다. API/CLI/운영 메서드는 호출하지 않는다."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types

CORE = {
    "legacy_driver.py": (31815, "64a1e0fb9f3cdeb469233d706eb099d1978646b14ce90e37f958b682c9d10eab"),
    "original_aws_cli_adapter_v2.py": (16609, "22f25f41ba67d88a022dd922ba82936f6d4dad23618004107d5ab88f58b9de91"),
    "get_command_invocation_once.py": (4587, "7a057bca14ab3f164414d8b8aa2c737d041550ba168b55f4d004c3bc29684656"),
    "copy_helper-exact-download-v1.py": (33089, "5a6d67b20412e3060fbf31a5770ff0018dae39630db07ba909267ebfb49f7080"),
    "health_readonly.py": (5793, "fc0859878bea4ddede1d4d9d006f37e1320cb5162751891d9c41fbf1e2693bd9"),
    "archive_envelope_codec.py": (22363, "6de3190f37d333bd1055e6abe706addb2a606405f701cf710712150852e1591f"),
    "collector_glue.py": (11746, "1077c5270ed530d330d9916356c621279893a5161b44cc8260ada804b27f707f"),
    "recovery_controller.py": (11693, "208deca33fef591ebd1b7a955633583deaa79b9b2370a7b1a8457c7f701d13d4"),
}
NEW = {"private_loader.py", "aws_cli_adapter_v2.py"}
OPTIONAL = {"invoke_recovery_once.py"}
NAMES = ("s2_private_original_driver", "aws_cli_adapter_v2", "s2_private_original_aws_adapter",
         "s2_private_collector_glue", "s2_private_recovery_controller")
OLD_ROOTS = (Path("/home/cloudshell-user/BYUL-SUP16-ROLES-20261111-R8"),
             Path("/home/cloudshell-user/BYUL-SUP16-ROLES-CLI-20261111-R8"))
_ACTIVE = False
_MISSING = object()


def require(value, message):
    if not value:
        raise ValueError(message)


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


@contextmanager
def _directory(root):
    require(root.is_absolute() and ".." not in root.parts, "CANONICAL_ABSOLUTE_PRIVATE_REQUIRED")
    require(not any(root == old or old in root.parents for old in OLD_ROOTS), "OLD_R8_OUTPUT_FORBIDDEN")
    fd = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            new = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = new
        value = os.fstat(fd)
        require(value.st_uid == 1000 and stat.S_IMODE(value.st_mode) == 0o700,
                "OWNER1000_PRIVATE700_REQUIRED")
        yield fd
    finally:
        os.close(fd)


def _read(root, directory, name, pin):
    require(type(name) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}", name), "SINGLE_SOURCE_NAME_REQUIRED")
    require(type(pin) is dict and type(pin.get("bytes")) is int and 0 < pin["bytes"] <= 1024 * 1024
            and type(pin.get("sha256")) is str and re.fullmatch("[0-9a-f]{64}", pin["sha256"]),
            "BOUNDED_SOURCE_PIN_REQUIRED")
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 1000
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                and before.st_size == pin["bytes"], "PRIVATE_REG600_SINGLE_LINK_SOURCE_REQUIRED")
        parts, count = [], 0
        while count <= pin["bytes"]:
            raw = os.read(fd, min(65536, pin["bytes"] + 1 - count))
            if not raw:
                break
            count += len(raw)
            parts.append(raw)
        data = b"".join(parts)
        after = os.fstat(fd)
        named = os.stat(name, dir_fd=directory, follow_symlinks=False)
        parent, actual = os.fstat(directory), root.lstat()
        require(_identity(before) == _identity(after) == _identity(named)
                and (parent.st_dev, parent.st_ino, parent.st_uid, parent.st_mode)
                == (actual.st_dev, actual.st_ino, actual.st_uid, actual.st_mode)
                and len(data) == pin["bytes"] and hashlib.sha256(data).hexdigest() == pin["sha256"],
                "SOURCE_FD_NAMED_IDENTITY_OR_BYTES_CHANGED")
        return data
    finally:
        os.close(fd)


def _module(name, filename, root, raw):
    value = types.ModuleType(name)
    value.__file__ = str(root / filename)
    value.__package__ = ""
    sys.modules[name] = value
    exec(compile(raw, value.__file__, "exec"), value.__dict__)
    return value


@contextmanager
def load_private(root, packet_raw, expected_packet_sha256):
    """load만 가능하다. packet.not_ready를 해소하거나 운영 허가를 만들지 않는다."""
    global _ACTIVE
    require(not _ACTIVE, "ONE_PRIVATE_MODULE_BINDING_AT_A_TIME")
    require(os.getuid() == os.geteuid() == 1000, "LOADER_UID1000_REQUIRED")
    require(type(packet_raw) is bytes and len(packet_raw) <= 1024 * 1024
            and hashlib.sha256(packet_raw).hexdigest() == expected_packet_sha256,
            "EXACT_CANDIDATE_PACKET_BYTES_REQUIRED")
    packet = json.loads(packet_raw)
    root = Path(root)
    require(packet["purpose"] == "retained-R8-envelope-recovery-only"
            and packet["private_path"] == str(root)
            and packet["cli_receipts_path"] == str(root / "CLI"), "NEW_PRIVATE_PACKET_BINDING_REQUIRED")
    pins = packet["local_files"]
    required = set(CORE) | NEW
    require(required <= set(pins) <= required | OPTIONAL, "EXACT_PRIVATE_SOURCE_SET_REQUIRED")
    for name, (size, sha) in CORE.items():
        require(pins[name]["bytes"] == size and pins[name]["sha256"] == sha, "ORIGINAL_CORE_PIN_CHANGED")
    saved = {name: sys.modules.get(name, _MISSING) for name in NAMES}
    runtime = None
    _ACTIVE = True
    try:
        with _directory(root) as directory:
            source = {name: _read(root, directory, name, pin) for name, pin in pins.items()}
            driver = _module(NAMES[0], "legacy_driver.py", root, source["legacy_driver.py"])
            adapter = _module(NAMES[1], "aws_cli_adapter_v2.py", root, source["aws_cli_adapter_v2.py"])
            require(adapter.OUT == root / "CLI" and adapter._ORIGINAL.__file__
                    == str(root / "original_aws_cli_adapter_v2.py"), "PARENT_AND_CHILD_ADAPTER_OUT_REQUIRED")
            glue = _module(NAMES[3], "collector_glue.py", root, source["collector_glue.py"])
            controller = _module(NAMES[4], "recovery_controller.py", root, source["recovery_controller.py"])
            driver.OUT = root
            driver.PACKET = packet
            driver.boto3 = adapter
            driver.SSM = adapter.client("ssm", region_name="ap-northeast-2")
            driver.EC2 = adapter.client("ec2", region_name="ap-northeast-2")
            runtime = types.SimpleNamespace(packet=packet, driver=driver, adapter=adapter,
                original_adapter=adapter._ORIGINAL, glue=glue, controller_module=controller,
                codec_raw=source["archive_envelope_codec.py"], source_sha256={n: hashlib.sha256(b).hexdigest() for n, b in source.items()},
                root=root, closed=False)
            yield runtime
    finally:
        if runtime is not None:
            runtime.closed = True
        for name, previous in saved.items():
            if previous is _MISSING:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        _ACTIVE = False
