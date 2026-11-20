"""SUP-06: worker·rank별 배정 shard·필수 shard·적재 완료/실패·READY 후보 (개발 담당, B 의존 반환 5, 2026-11-08).

vLLM 0.30.0과 0.31.0의 두 모듈을 설치 소스 해시로 고정해 감싼다. GPU 생산자 등록에는 실제 TP2 관측이 필요하다.
- `model_loader.default_loader`(9c9d54b1… / d17ab549…): `DefaultModelLoader._prepare_weights`의 반환(가중치 파일 목록과 제공된 index 파일)과
  `DefaultModelLoader.track_weights_loading`의 반환/예외(가중치 적재 직후 불리며 누락 텐서가 있으면 예외를 낸다).
- `v1.executor.multiproc_executor`(3ed0149a… / 11a15543…): `WorkerProc.monitor_death_pipe`의 호출.
  worker 생성(모델 적재 포함)이 끝난 뒤 READY를 보내기 직전에 항상 불린다(READY 후보).
`load_weights`·`WorkerProc.__init__`은 `@instrument` 추적 장식자로 감싸여 원 코드 자격을 얻지 못하므로 감싸지 않는다(k3d 실측).
모든 행에 PID·시작 ticks·TP rank(`parallel_state`, 읽지 못하면 None)를 남겨 바인딩에 쓴다.

파일 이름은 세션 secret으로 keyed 요약하고 원 경로·tensor·payload는 남기지 않는다. index 값을 반환한 경우 기존 index 관측을
남긴다. 세 요소 반환의 필수 shard는 같은 호출의 검증된 index 필터 결과에서만 얻는다. tensor payload의 실제 GPU 적재는 별도 증거가 필요하다.
"""
from __future__ import annotations

import functools
import json
import os
import stat
import sys
import threading
import types

from . import vllm_cache_callback as cache
from .vllm_observer_pin_rows import candidate_sources

LOADER = 'vllm.model_executor.model_loader.default_loader'
MP = 'vllm.v1.executor.multiproc_executor'
WEIGHTS = 'vllm.model_executor.model_loader.weight_utils'
FILTER = 'filter_duplicate_safetensors_files'
PREPARE = 'DefaultModelLoader._prepare_weights'
_INDEX_FRAME_KEY = 'worker_shard_selected_index'
_INDEX_PENDING = object()
_INDEX_SOURCES = {
    LOADER: '9c9d54b1b650bf5affc924ebb8b8c73711e187ae7486cadd9f737bb1279cc7b8',
    WEIGHTS: '7443e55184777dcc99f621b0d97d9149d52c2ea8b4304c6621eb4902e1c8ba5b',
}
_INDEX_TARGETS = {LOADER: (PREPARE,), WEIGHTS: (FILTER,)}
TARGETS = {LOADER: (PREPARE, 'DefaultModelLoader.track_weights_loading'), MP: ('WorkerProc.monitor_death_pipe',)}
SOURCES = candidate_sources({
    LOADER: ('d17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
             _INDEX_SOURCES[LOADER]),
    MP: '11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b',
}, (MP,))
SCHEMA = 'guard-vllm-worker-shard-callback/1'
MAX_FILES = 256


def _tp_rank():
    try:
        from vllm.distributed import parallel_state
        rank = parallel_state.get_tensor_model_parallel_rank()
        return rank if type(rank) is int and 0 <= rank < 4096 else None
    except Exception:
        return None


def _start_ticks():
    try:
        raw = open('/proc/self/stat', 'rb').read(8192).decode('utf-8', 'replace')
        return int(raw[raw.rfind(')') + 2:].split()[19])
    except Exception:
        return None


def _source_identity(path):
    info = os.lstat(path)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class WorkerShardObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-worker-shard-callback-candidate-v1'

    def _name(self, path):
        return self.fingerprint('shard:' + os.path.basename(str(path)))

    def index_alias_change(self, module):
        """Return an optional change for the reviewed loader's already imported index helper."""
        helper = sys.modules.get(WEIGHTS)
        if (sys.implementation.name != 'cpython' or type(module) is not types.ModuleType
                or sys.modules.get(LOADER) is not module or type(helper) is not types.ModuleType):
            return None
        loader_globals, helper_globals = vars(module), vars(helper)
        original = helper_globals.get(FILTER)
        if (type(original) is not types.FunctionType or original.__globals__ is not helper_globals
                or loader_globals.get(FILTER) is not original):
            return None
        try:
            _, _, loader = cache._function(module, PREPARE)
            helper_path = helper_globals['__file__']
            identity = _source_identity(helper_path)
            loader_code, helper_code = loader.__code__, original.__code__
            if (cache._codes(module, _INDEX_TARGETS, _INDEX_SOURCES).get(PREPARE) != cache._digest(loader_code, LOADER)
                    or cache._codes(helper, _INDEX_TARGETS, _INDEX_SOURCES).get(FILTER) != cache._digest(helper_code, WEIGHTS)
                    or _source_identity(helper_path) != identity):
                return None
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return None

        def bound():
            return (sys.modules.get(LOADER) is module and sys.modules.get(WEIGHTS) is helper
                    and loader_globals.get(FILTER) is wrapped and helper_globals.get(FILTER) is original
                    and loader.__code__ is loader_code and original.__code__ is helper_code
                    and helper_globals.get('__file__') == helper_path
                    and _source_identity(helper_path) == identity)

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            selected = False
            try:
                active = cache._ACTIVE.get()
                caller = sys._getframe(1)
                if (type(active) is dict and active.get('observer') is self
                        and active.get('pid') == self.pid == os.getpid()
                        and active.get('thread') == threading.get_ident()
                        and caller.f_code is loader_code and caller.f_globals is loader_globals):
                    first = _INDEX_FRAME_KEY not in active
                    active[_INDEX_FRAME_KEY] = _INDEX_PENDING if first else None
                    selected = (first and len(args) == 3 and not kwargs and type(args[0]) is list
                                and all(type(value) is str and value for value in args[1:]) and bound())
            except BaseException:
                pass
            result = original(*args, **kwargs)
            try:
                if (selected and cache._ACTIVE.get() is active and active.get(_INDEX_FRAME_KEY) is _INDEX_PENDING and bound()
                        and type(result) is list and result is not args[0] and 0 < len(result) <= MAX_FILES
                        and all(type(path) is str and path for path in result)):
                    active[_INDEX_FRAME_KEY] = result
            except BaseException:
                pass
            return result

        return ((module, FILTER, original), wrapped)

    def _prepared(self, result):
        if type(result) is not tuple or len(result) not in (3, 4):
            self.gaps.add('WORKER_SHARD_PREPARE_SHAPE_UNAVAILABLE'); return {}
        folder, files, safetensors = result[:3]
        if type(files) is not list or len(files) > MAX_FILES:
            self.gaps.add('WORKER_SHARD_FILE_SET_UNAVAILABLE'); return {}
        sizes = []
        for path in files:
            try:
                sizes.append(os.stat(path).st_size)
            except OSError:
                sizes.append(None)
        prepared = {'files': [self._name(p) for p in files], 'file_bytes': sizes, 'safetensors': safetensors is True,
                    'index_present': None, 'required': None, 'folder_sha256': self.fingerprint('folder:' + str(folder))}
        if len(result) == 3:
            active = cache._ACTIVE.get()
            if (safetensors is True and type(active) is dict and active.get('observer') is self
                    and active.get('pid') == self.pid == os.getpid() and active.get('thread') == threading.get_ident()
                    and active.get(_INDEX_FRAME_KEY) is files):
                prepared['index_present'] = True
                prepared['required'] = sorted(set(prepared['files']))
            return prepared
        index = result[3]
        if index is None or type(index) is str and index == '':
            return {**prepared, 'index_present': False}
        if type(index) is not str or os.path.isabs(index) or '..' in index.split(os.sep):
            self.gaps.add('WORKER_SHARD_INDEX_UNREADABLE')
            return prepared
        path = os.path.join(str(folder), index)
        try:
            identity = os.lstat(path)
        except FileNotFoundError:
            return {**prepared, 'index_present': False}
        except (OSError, ValueError):
            self.gaps.add('WORKER_SHARD_INDEX_UNREADABLE')
            return prepared
        prepared['index_present'] = True
        try:
            if stat.S_ISLNK(identity.st_mode):
                identity = os.stat(path)
            if not stat.S_ISREG(identity.st_mode):
                raise ValueError
            descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise ValueError
                with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                    raw = stream.read(1 << 24)
            finally:
                os.close(descriptor)
            mapping = json.loads(raw).get('weight_map')
            if (type(mapping) is not dict or not mapping or any(type(v) is not str or not v for v in mapping.values())
                    or len(set(mapping.values())) > MAX_FILES):
                raise ValueError
            prepared['required'] = sorted({self._name(v) for v in mapping.values()})
        except Exception:
            self.gaps.add('WORKER_SHARD_INDEX_UNREADABLE')
        return prepared

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        who = {'pid': os.getpid(), 'start_ticks': _start_ticks(), 'tp_rank': _tp_rank()}
        if point == PREPARE:
            return {**who, **(self._prepared(result) if returned else {})}
        if point == 'DefaultModelLoader.track_weights_loading':
            return {**who, 'completed': True} if returned else who
        # WorkerProc.monitor_death_pipe 호출: 생성이 끝난 worker. READY 직전이다.
        rank = cache.base.attrs(receiver).get('rank')
        return {**who, 'rank': rank if type(rank) is int and 0 <= rank < 4096 else None, 'ready_candidate': True}
