"""Source-qualified rank and closed native worker admission observations."""
from __future__ import annotations

from .vllm_observer_pin_rows import candidate_sources, layout
import os

from . import vllm_cache_callback as cache, vllm_consumption_callback as v1, vllm_consumption_v2_callback as v2
from .vllm_worker_shard_callback import _tp_rank, _start_ticks
from .observer_runtime import QualifiedObserver


class _Scoped(QualifiedObserver):
    def _emit(self, row):
        with self.lock:
            row = dict(row)
            if row.get('point', '').startswith('GPUModelRunner.'):
                if not hasattr(self, '_admissions'):
                    self._admissions = {}
                    self._incoming = {}
                if row['point'] in ('GPUModelRunner._update_states', 'GPUModelRunner.add_requests'):
                    if row['phase'] == 'CALL':
                        self._incoming[row['call_sha256']] = [r['request_sha256'] for r in (row.get('fields', {}).get('received') or [])]
                    else:
                        incoming = self._incoming.pop(row['call_sha256'], ())
                        if row['phase'] == 'RETURN':
                            for request in incoming:
                                if len(self._admissions) >= 256 and request not in self._admissions:
                                    self._admissions.pop(next(iter(self._admissions)))
                                    self.gaps.add('SCOPED_ADMISSION_WINDOW_EXCEEDED')
                                self._admissions[request] = self.fingerprint('native-admission:' + row['call_sha256'] + ':' + request)
                row['consumption_scope'] = {'rank': _tp_rank(), 'admissions': dict(self._admissions), 'pid': os.getpid(), 'start_ticks': _start_ticks()}
            else:
                row['consumption_scope'] = {'pid': os.getpid(), 'start_ticks': _start_ticks()}
            # The prepared sensor subclasses route to a bounded file sink; the
            # standalone observer keeps the normal bounded in-memory report.
            super()._emit(row)


class ScopedConsumptionObserver(_Scoped, v1.ConsumptionObserver):
    sources = candidate_sources(v1.ConsumptionObserver.sources, v1.TARGETS)


class ScopedConsumptionV2Observer(_Scoped, v2.ConsumptionV2Observer):
    sources = candidate_sources(v2.SOURCES, v2.TARGETS)
