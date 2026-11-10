"""새 private의 부모·GET 자식에 동일한 원 adapter를 결합하는 import shim.

원 adapter bytes는 변경하지 않는다. import 시 API/CLI/root()를 호출하지 않는다.
"""
import hashlib
import os
from pathlib import Path
import stat
import sys
import types

_ROOT = Path(__file__).parent
_NAME = "original_aws_cli_adapter_v2.py"
_SHA = "22f25f41ba67d88a022dd922ba82936f6d4dad23618004107d5ab88f58b9de91"
_SIZE = 16609


def _identity(value):
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
            value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _original_bytes():
    directory = os.open(_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    source = None
    try:
        parent = os.fstat(directory)
        if parent.st_uid != 1000 or stat.S_IMODE(parent.st_mode) != 0o700:
            raise PermissionError("NEW_PRIVATE_ADAPTER_PARENT_REQUIRED")
        source = os.open(_NAME, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(source)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 1000
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or before.st_size != _SIZE):
            raise PermissionError("EXACT_PRIVATE_ORIGINAL_ADAPTER_METADATA_REQUIRED")
        parts, size = [], 0
        while size <= _SIZE:
            block = os.read(source, min(65536, _SIZE + 1 - size))
            if not block:
                break
            size += len(block)
            parts.append(block)
        raw = b"".join(parts)
        after = os.fstat(source)
        named = os.stat(_NAME, dir_fd=directory, follow_symlinks=False)
        current_root = _ROOT.lstat()
        if (_identity(before) != _identity(after) or _identity(after) != _identity(named)
                or (parent.st_dev, parent.st_ino, parent.st_uid, parent.st_mode)
                != (current_root.st_dev, current_root.st_ino, current_root.st_uid, current_root.st_mode)
                or len(raw) != _SIZE or hashlib.sha256(raw).hexdigest() != _SHA):
            raise ValueError("PRIVATE_ORIGINAL_ADAPTER_BYTES_OR_IDENTITY_CHANGED")
        return raw
    finally:
        if source is not None:
            os.close(source)
        os.close(directory)


_raw = _original_bytes()
_ORIGINAL = types.ModuleType("s2_private_original_aws_adapter")
_ORIGINAL.__file__ = str(_ROOT / _NAME)
sys.modules[_ORIGINAL.__name__] = _ORIGINAL
exec(compile(_raw, _ORIGINAL.__file__, "exec"), _ORIGINAL.__dict__)
# 원문 초기화의 옛 OUT는 Path 객체일 뿐이다. 어떤 원문 함수도 호출하기 전에 바인딩한다.
_ORIGINAL.OUT = _ROOT / "CLI"
if _ORIGINAL.INSTANCE != "i-05e8da8ea99593196" or _ORIGINAL.REGION != "ap-northeast-2":
    raise ValueError("ORIGINAL_ADAPTER_TARGET_CHANGED")


def __getattr__(name):
    if name.startswith("_"):
        raise AttributeError(name)
    return getattr(_ORIGINAL, name)
