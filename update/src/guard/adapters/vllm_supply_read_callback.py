"""P2 SUP-03 생산자의 엔진 쪽 관측(개발 담당, 2단계). 기본 모델과 함께 실제로 연 공급 파일.

config·tokenizer·chat template·generation config·하위 모델 파일은 vLLM 함수만이 아니라 transformers 등 여러 곳에서 열린다.
그래서 함수를 감싸지 않고 Python audit hook(`sys.addaudithook`)의 `open` 사건만 읽는다. 원 코드와 열기 동작은 바꾸지 않는다.

- 선언한 공급 루트(설정의 `supply_roots`, 예: 모델 폴더·chat template 파일) 아래 경로만 남긴다.
- 프로세스마다 (상대 경로, 읽기/쓰기) 처음 한 번만 남기고 횟수를 센다. 열 때의 크기·수정 시각을 함께 남긴다.
- 파일 내용은 여기서 읽지 않는다. 내용 해시는 수집 시점에 소유 Pod 안에서 따로 계산한다(`engine_supply_hashes`).
- hook은 어떤 경우에도 예외를 내지 않는다. 관측 실패는 gap 코드로만 남는다.
상대 경로는 승인 목록과 대조하려고 그대로 남긴다. 루트 밖 경로는 남기지 않는다.
"""
from __future__ import annotations

import os
import stat as stat_module
import threading
import time

SCHEMA = 'guard-vllm-supply-read-callback/1'
MAX_FILES = 2048
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


def normalize_roots(values):
    if type(values) not in (list, tuple) or not 1 <= len(values) <= 8:
        raise ValueError('SUPPLY_READ_ROOTS_INVALID')
    roots = []
    for value in values:
        if type(value) is not str or not value.startswith('/') or '\0' in value or len(value) > 512:
            raise ValueError('SUPPLY_READ_ROOTS_INVALID')
        roots.append(os.path.normpath(value))
    return roots


def _write(mode, flags):
    if type(mode) is str and any(c in mode for c in 'wax+'):
        return True
    return type(flags) is int and flags & _WRITE_FLAGS != 0


class SupplyReadObserver:
    def __init__(self, roots, *, sink, process_sha256, clock=None):
        self.roots = normalize_roots(roots)
        self.sink = sink; self.process_sha256 = process_sha256; self.clock = clock or time.monotonic_ns
        self.seen = {}; self.gaps = set(); self.lock = threading.Lock(); self.local = threading.local()

    def _locate(self, path):
        for index, root in enumerate(self.roots):
            if path == root:
                return index, '.'
            if path.startswith(root.rstrip('/') + '/'):
                return index, path[len(root.rstrip('/')) + 1:]
        return None, None

    def hook(self, event, args):
        if event != 'open' or getattr(self.local, 'busy', False):
            return
        self.local.busy = True
        try:
            path = args[0] if args else None
            if isinstance(path, os.PathLike):
                path = os.fspath(path)
            if type(path) is not str or not path.startswith('/'):
                return
            path = os.path.normpath(path)
            index, relative = self._locate(path)
            if index is None:
                return
            write = _write(args[1] if len(args) > 1 else None, args[2] if len(args) > 2 else None)
            key = (index, relative, write)
            with self.lock:
                if key in self.seen:
                    self.seen[key] += 1; return
                if len(self.seen) >= MAX_FILES:
                    self.gaps.add('SUPPLY_READ_FILE_LIMIT'); return
                self.seen[key] = 1
            try:
                info = os.stat(path)
                size, mtime, regular = info.st_size, info.st_mtime_ns, stat_module.S_ISREG(info.st_mode)
            except OSError:
                size = mtime = None; regular = None
            self.sink({'schema': SCHEMA, 'phase': 'OPEN', 'process_sha256': self.process_sha256, 'root': index,
                       'relative': relative, 'write': write, 'regular': regular, 'size': size, 'mtime_ns': mtime,
                       'opened_ns': self.clock()})
        except BaseException:
            try:
                self.gaps.add('SUPPLY_READ_HOOK_UNAVAILABLE')
            except BaseException:
                pass
        finally:
            self.local.busy = False


# ---------------------------------------------------------------------------
# native 읽기: inotify (특권 없음, ctypes만 사용)
# ---------------------------------------------------------------------------
IN_ACCESS, IN_OPEN, IN_Q_OVERFLOW, IN_ISDIR, IN_NONBLOCK = 0x001, 0x020, 0x4000, 0x40000000, 0o4000
_EVENT = 16
MAX_WATCHES = 512


class SupplyWatcher:
    """선언 공급 루트를 inotify로 지켜 native 코드(Rust tokenizers 등)가 연 파일까지 남긴다.

    - Pod 안에서 하나만 돈다(세션 폴더의 잠금 파일을 먼저 만든 프로세스). 여러 프로세스가 같은 사건을 겹쳐 남기지 않는다.
    - 폴더 루트는 하위 폴더까지 지킨다. 파일 루트는 그 파일 자체를 지킨다(bind mount된 파일은 부모 폴더 감시로 보이지 않는다).
    - inotify는 연 프로세스를 알려 주지 않는다. 같은 노드의 다른 Pod가 같은 파일을 열어도 섞인다.
      그래서 이 행은 프로세스 귀속이 없다는 표시와 함께 쓴다(소비자는 Tetragon 등 독립 근거로 귀속을 보완한다).
    - 큐가 넘치면(IN_Q_OVERFLOW) 놓친 사건이 있다는 gap을 남긴다.
    """

    def __init__(self, roots, *, sink, process_sha256, clock=None, libc=None):
        self.roots = normalize_roots(roots); self.sink = sink; self.process_sha256 = process_sha256
        self.clock = clock or time.monotonic_ns; self.seen = set(); self.watches = {}; self.gaps = set()
        self.libc = libc
        self.fd = None

    def _libc(self):
        if self.libc is None:
            import ctypes
            self.libc = ctypes.CDLL('libc.so.6', use_errno=True)
        return self.libc

    def _watch(self, directory, root_index, prefix, only=None):
        if len(self.watches) >= MAX_WATCHES:
            self.gaps.add('SUPPLY_INOTIFY_WATCH_LIMIT'); return
        wd = self._libc().inotify_add_watch(self.fd, directory.encode(), IN_OPEN | IN_ACCESS)
        if wd < 0:
            self.gaps.add('SUPPLY_INOTIFY_WATCH_UNAVAILABLE'); return
        self.watches[wd] = (root_index, prefix, only)

    def start(self):
        try:
            self.fd = self._libc().inotify_init1(IN_NONBLOCK)
            if self.fd < 0:
                raise OSError
            for index, root in enumerate(self.roots):
                if os.path.isdir(root):
                    for current, dirs, _ in os.walk(root):
                        dirs.sort()
                        relative = os.path.relpath(current, root)
                        self._watch(current, index, '' if relative == '.' else relative + '/')
                elif os.path.isfile(root):
                    # 파일 하나를 bind mount한 경우 부모 폴더 감시로는 사건이 오지 않는다(k3d run 18). 파일 자체를 지킨다.
                    self._watch(root, index, '', only='.')
            threading.Thread(target=self._run, name='guard-supply-watch', daemon=True).start()
            return True
        except BaseException:
            self.gaps.add('SUPPLY_INOTIFY_UNAVAILABLE'); return False

    def handle(self, data):
        """inotify 사건 bytes를 행으로 옮긴다(시험에서 직접 부른다)."""
        import struct
        offset = 0
        while offset + _EVENT <= len(data):
            wd, mask, _, length = struct.unpack_from('iIII', data, offset)
            name = data[offset + _EVENT:offset + _EVENT + length].split(b'\0', 1)[0].decode('utf-8', 'replace')
            offset += _EVENT + length
            if mask & IN_Q_OVERFLOW:
                self.gaps.add('SUPPLY_INOTIFY_OVERFLOW')
                self.sink({'schema': SCHEMA, 'phase': 'GAP', 'code': 'SUPPLY_INOTIFY_OVERFLOW', 'process_sha256': self.process_sha256})
                continue
            watch = self.watches.get(wd)
            if watch is None or mask & IN_ISDIR:
                continue
            root_index, prefix, only = watch
            if only == '.':
                relative = '.'  # 파일 자체 감시: 사건에 이름이 없다
            elif not name:
                continue
            else:
                relative = prefix + name
            key = (root_index, relative)
            if key in self.seen:
                continue
            self.seen.add(key)
            self.sink({'schema': SCHEMA, 'phase': 'INOTIFY', 'process_sha256': self.process_sha256, 'root': root_index,
                       'relative': relative, 'write': False, 'regular': True, 'size': None, 'mtime_ns': None,
                       'opened_ns': self.clock(), 'process_attributed': False})

    def _run(self):
        import select
        while True:
            try:
                ready, _, _ = select.select([self.fd], [], [], 1.0)
                if ready:
                    self.handle(os.read(self.fd, 65536))
            except BaseException:
                self.gaps.add('SUPPLY_INOTIFY_READ_UNAVAILABLE'); return
