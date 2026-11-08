"""SUP-06: worker·rank별 배정 shard·필수 shard·적재 완료/실패·READY 후보 (개발 담당, B 의존 반환 5, 2026-11-08).

vLLM 0.31.0의 두 모듈을 원 소스 해시로 고정해 감싼다.
- `model_loader.default_loader`(d17ab549…): `DefaultModelLoader._prepare_weights`의 반환(이 프로세스가 읽을 가중치 파일 목록과
  index 파일)과 `DefaultModelLoader.track_weights_loading`의 반환/예외(가중치 적재 직후 불리며 누락 텐서가 있으면 예외를 낸다).
- `v1.executor.multiproc_executor`(11a15543…, REQ-14 분산 관측과 같은 고정값): `WorkerProc.monitor_death_pipe`의 호출.
  worker 생성(모델 적재 포함)이 끝난 뒤 READY를 보내기 직전에 항상 불린다(READY 후보).
`load_weights`·`WorkerProc.__init__`은 `@instrument` 추적 장식자로 감싸여 원 코드 자격을 얻지 못하므로 감싸지 않는다(k3d 실측).
모든 행에 PID·시작 ticks·TP rank(`parallel_state`, 읽지 못하면 None)를 남겨 바인딩에 쓴다.

파일 이름은 세션 secret으로 keyed 요약하고 원 경로·tensor·payload는 남기지 않는다. 필수 shard는 index 파일의 weight_map 값,
index가 없으면 배정된 파일 자체다. 이 관측은 파일 배정·적재 함수의 완료이며 tensor payload의 실제 GPU 적재 증명이 아니다.
"""
from __future__ import annotations

import json
import os

from . import vllm_cache_callback as cache

LOADER = 'vllm.model_executor.model_loader.default_loader'
MP = 'vllm.v1.executor.multiproc_executor'
TARGETS = {LOADER: ('DefaultModelLoader._prepare_weights', 'DefaultModelLoader.track_weights_loading'), MP: ('WorkerProc.monitor_death_pipe',)}
SOURCES = {LOADER: 'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9',
           MP: '11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b'}
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


class WorkerShardObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-worker-shard-callback-candidate-v1'

    def _name(self, path):
        return self.fingerprint('shard:' + os.path.basename(str(path)))

    def _prepared(self, result):
        try:
            folder, files, safetensors, index = result
        except Exception:
            self.gaps.add('WORKER_SHARD_PREPARE_SHAPE_UNAVAILABLE'); return {}
        if type(files) is not list or len(files) > MAX_FILES:
            self.gaps.add('WORKER_SHARD_FILE_SET_UNAVAILABLE'); return {}
        sizes = []
        for path in files:
            try:
                sizes.append(os.stat(path).st_size)
            except OSError:
                sizes.append(None)
        required, index_present = None, False
        try:
            path = os.path.join(str(folder), str(index)) if index else None
            if path and os.path.isfile(path):
                index_present = True
                with open(path, 'rb') as stream:
                    raw = stream.read(1 << 24)
                mapping = json.loads(raw).get('weight_map')
                if type(mapping) is dict and len(set(mapping.values())) <= MAX_FILES:
                    required = sorted({self._name(v) for v in mapping.values() if type(v) is str})
                else:
                    self.gaps.add('WORKER_SHARD_INDEX_UNREADABLE')
        except Exception:
            self.gaps.add('WORKER_SHARD_INDEX_UNREADABLE')
        return {'files': [self._name(p) for p in files], 'file_bytes': sizes, 'safetensors': safetensors is True,
                'index_present': index_present, 'required': required, 'folder_sha256': self.fingerprint('folder:' + str(folder))}

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        who = {'pid': os.getpid(), 'start_ticks': _start_ticks(), 'tp_rank': _tp_rank()}
        if point == 'DefaultModelLoader._prepare_weights':
            return {**who, **(self._prepared(result) if returned else {})}
        if point == 'DefaultModelLoader.track_weights_loading':
            return {**who, 'completed': True} if returned else who
        # WorkerProc.monitor_death_pipe 호출: 생성이 끝난 worker. READY 직전이다.
        rank = cache.base.attrs(receiver).get('rank')
        return {**who, 'rank': rank if type(rank) is int and 0 <= rank < 4096 else None, 'ready_candidate': True}
