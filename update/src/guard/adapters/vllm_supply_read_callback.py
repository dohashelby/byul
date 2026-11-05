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
