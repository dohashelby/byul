"""#196 profiler-control 생산자의 엔진 쪽 관측(개발 담당, 2단계 P1).

vLLM 0.31.0 profiler 제어 사슬의 세 프로세스를 원 소스 해시로 고정해 감싼다.
- API 서버: `AsyncLLM.start_profile`·`stop_profile` (api_call)
- EngineCore: `EngineCore.profile` (rpc)
- worker: `Worker.profile` (worker_control), `TorchProfilerWrapper._start`·`_stop` (backend 상태)
- `_stop` 반환 뒤 `torch_profiler_dir`의 파일 이름·크기 목록(내용은 읽지 않음)으로 출력 완료와 파일 세대를 남긴다.

세 프로세스 사이에 vLLM이 넘기는 요청 ID가 없다(`is_start`와 접두어만 간다). 그래서 이 관측은 프로세스마다 따로 남고,
사슬의 인과는 생산자 진단으로 "공유 키 없음"을 적는다. 순서만으로 인과를 주장하지 않는다.
gpu_worker·wrapper 소스 해시는 native/15 profiler 센서가 검토한 값과 같다.
"""
from __future__ import annotations

import os

from . import vllm_cache_callback as cache
from .vllm_profiler_sensor import SOURCE_ADDITIONS, WORKER, WRAPPER

ASYNC_LLM = 'vllm.v1.engine.async_llm'
CORE = 'vllm.v1.engine.core'
TARGETS = {ASYNC_LLM: ('AsyncLLM.start_profile', 'AsyncLLM.stop_profile'), CORE: ('EngineCore.profile',),
           WORKER: ('Worker.profile',), WRAPPER: ('TorchProfilerWrapper._start', 'TorchProfilerWrapper._stop')}
SOURCES = {**{k: v for k, v in cache.base.SOURCE_SHA.items() if k in (ASYNC_LLM, CORE)}, WORKER: SOURCE_ADDITIONS[WORKER],
           WRAPPER: SOURCE_ADDITIONS[WRAPPER]}
SCHEMA = 'guard-vllm-profiler-callback/1'
MAX_FILES = 256


def _flag(value, default=True):
    return value if type(value) is bool else default if value is None else None


class ProfilerObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-profiler-callback-candidate-v1'

    def _files(self, receiver):
        directory = cache.base.attrs(cache.base.attrs(receiver).get('profiler_config')).get('torch_profiler_dir')
        if type(directory) is not str or not directory.startswith('/') or '://' in directory:
            return None
        try:
            with os.scandir(directory) as entries:
                rows = sorted((e.name, e.stat(follow_symlinks=False).st_size) for e in entries if e.is_file(follow_symlinks=False))
        except OSError:
            self.gaps.add('PROFILER_CALLBACK_OUTPUT_UNAVAILABLE'); return None
        if len(rows) > MAX_FILES:
            self.gaps.add('PROFILER_CALLBACK_OUTPUT_LIMIT'); rows = rows[:MAX_FILES]
        # 파일 이름은 지문으로, 크기는 그대로 남긴다. 내용은 읽지 않는다.
        return [{'name_sha256': self.fingerprint(name), 'bytes': size} for name, size in rows]

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        if point in ('AsyncLLM.start_profile', 'AsyncLLM.stop_profile'):
            return {'is_start': point.endswith('start_profile')}
        if point in ('EngineCore.profile', 'Worker.profile'):
            row = {'is_start': _flag(args[1] if len(args) > 1 else kwargs.get('is_start'))}
            if point == 'Worker.profile':
                fields = cache.base.attrs(receiver)
                row.update(rank=cache._uint(fields.get('rank')), local_rank=cache._uint(fields.get('local_rank')))
            return row
        row = {'is_start': point.endswith('_start')}
        if point == 'TorchProfilerWrapper._stop' and returned:
            row['output_files'] = self._files(receiver)
        return row
