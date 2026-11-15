"""M-A3의 읽기 전용 IPC 식별 후보. 엔진 소속·호출 인과를 인증하지 않는다.

부모와 worker의 동일 요청 ID만으로 그룹을 확정하지 않도록 실제 pipe 또는
공유 메모리의 커널 객체를 세션 키로 지문화한다. 데이터·이름·포인터를 읽거나
원 FD를 닫지 않는다. 정확 소스 관측기와 닫힌 기록에 연결하는 작업은 별도다.
"""
from hashlib import sha256
import hmac
import json
from multiprocessing.connection import Connection
from multiprocessing.shared_memory import SharedMemory
import os
from pathlib import Path
import stat
import time

SCHEMA = 'guard-rank-ipc-identity/1'


def _identity(value):
    return value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)


def _capture(value, expected_type, attribute, kind, key):
    # 정확한 표준 라이브러리 형식만 받는다. 임의 객체의 property/fileno는 호출하지 않는다.
    if (type(value) is not expected_type or type(key) is not bytes or len(key) != 32
            or os.name != 'posix'):
        raise ValueError('RANK_IPC_IDENTITY_UNAVAILABLE')
    duplicate = None
    try:
        data = object.__getattribute__(value, '__dict__')
        fd = data.get(attribute)
        if type(fd) is not int or fd < 0:
            raise ValueError
        started = time.monotonic_ns()
        before = os.fstat(fd)
        duplicate = os.dup(fd)
        held = os.fstat(duplicate)
        after = os.fstat(fd)
        if (len({_identity(v) for v in (before, held, after)}) != 1
                or data.get(attribute) != fd
                or not (stat.S_ISFIFO(held.st_mode) if kind == 'pipe' else stat.S_ISREG(held.st_mode))):
            raise ValueError
        boot = Path('/proc/sys/kernel/random/boot_id').read_bytes()
        namespace = os.readlink('/proc/self/ns/ipc')
        if not 1 <= len(boot) <= 64 or len(namespace) > 128:
            raise ValueError
        identity = {'kind': kind, 'kernel_object': list(_identity(held)),
                    'boot_sha256': sha256(boot).hexdigest(), 'ipc_namespace': namespace}
        return {'schema': SCHEMA, 'kind': kind,
                'identity_hmac_sha256': hmac.new(
                    key, json.dumps(identity, sort_keys=True, separators=(',', ':')).encode(), sha256).hexdigest(),
                'observation_started_ns': started, 'observation_finished_ns': time.monotonic_ns(),
                'source_authenticated': False, 'engine_group_verified': False}
    except (OSError, ValueError, TypeError, AttributeError):
        raise ValueError('RANK_IPC_IDENTITY_UNAVAILABLE') from None
    finally:
        if duplicate is not None:
            os.close(duplicate)


def pipe_endpoint(connection, *, key):
    """원 pipe를 읽지 않고 Connection의 실제 커널 객체만 지문화한다."""
    return _capture(connection, Connection, '_handle', 'pipe', key)


def shared_memory_endpoint(memory, *, key):
    """SharedMemory의 payload·name property를 읽지 않고 실제 FD만 지문화한다."""
    return _capture(memory, SharedMemory, '_fd', 'shared-memory', key)


def process_context(pid):
    """동일 UID·PID/time namespace의 실제 시작 세대를 읽는다. PID만으로 결합하지 않는다."""
    if type(pid) is not int or not 0 < pid < 2**31 or os.name != 'posix':
        raise ValueError('RANK_IPC_PROCESS_UNAVAILABLE')
    fd = None
    try:
        root = Path('/proc') / str(pid)
        fd = os.open(root / 'stat', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid():
            raise ValueError
        def start():
            raw = os.read(fd, 8193)
            if not 1 <= len(raw) <= 8192 or int(raw.split(b' ', 1)[0]) != pid:
                raise ValueError
            return int(raw[raw.rfind(b')') + 2:].split()[19])
        ticks = start()
        namespaces = tuple(os.stat(root / 'ns' / name).st_ino for name in ('pid', 'time'))
        if namespaces != tuple(os.stat(Path('/proc/self/ns') / name).st_ino for name in ('pid', 'time')):
            raise ValueError
        offsets = (root / 'timens_offsets').read_bytes()
        if len(offsets) > 4096:
            raise ValueError
        os.lseek(fd, 0, os.SEEK_SET)
        if (start() != ticks or ticks < 0 or ticks >= 2**63
                or namespaces != tuple(os.stat(root / 'ns' / name).st_ino for name in ('pid', 'time'))
                or offsets != (root / 'timens_offsets').read_bytes()
                or os.fstat(fd).st_uid != before.st_uid):
            raise ValueError
        return {'pid': pid, 'start_ticks': ticks, 'uid': before.st_uid,
                'boot_sha256': sha256(Path('/proc/sys/kernel/random/boot_id').read_bytes()).hexdigest(),
                'pid_namespace': namespaces[0], 'time_namespace': namespaces[1],
                'time_offsets_sha256': sha256(offsets).hexdigest()}
    except (OSError, ValueError, IndexError):
        raise ValueError('RANK_IPC_PROCESS_UNAVAILABLE') from None
    finally:
        if fd is not None:
            os.close(fd)
