"""LIF-06 V1 runner dispatch identity; object identity is not tensor content.

The pinned helper calls self.model once. Requests come from its real InputBatch,
attempt identities from CachedRequestState objects, and model/parameter/KV
identities from live weak references. No alias, clock inference, payload copy,
GPU synchronization, or serialized caller assertion qualifies a dispatch.
"""
from __future__ import annotations

import hashlib
import json
import os

from . import vllm_cache_callback as cache
from .vllm_worker_shard_callback import _start_ticks, _tp_rank

RUNNER = 'vllm.v1.worker.gpu_model_runner'
TARGETS = {RUNNER: ('GPUModelRunner._model_forward',)}
SCHEMA = 'guard-vllm-transition-dispatch/1'
MAX_ITEMS = 256


class TransitionDispatchObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    schema = SCHEMA
    profile = 'source-bound-transition-dispatch-candidate-v1'

    def _set_identity(self, values):
        tokens = []
        for index, value in enumerate(values):
            if index >= MAX_ITEMS:
                self.gaps.add('TRANSITION_DISPATCH_OBJECT_BOUND'); return None
            token = self.token(value)
            if token is None:
                return None
            tokens.append(token)
        if not tokens:
            return None
        return self.fingerprint(hashlib.sha256(json.dumps(tokens, separators=(',', ':')).encode()).hexdigest())

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        runner = args[0] if args else None
        fields = cache.base.attrs(runner)
        batch = cache.base.attrs(fields.get('input_batch'))
        ids, states = batch.get('_req_ids'), fields.get('requests')
        requests = None
        if type(ids) is list and len(ids) <= MAX_ITEMS and type(states) is dict:
            requests = [{'request_sha256': self.fingerprint(req), 'attempt_sha256': self.token(states.get(req))}
                        for req in ids if req is not None]
        else:
            self.gaps.add('TRANSITION_DISPATCH_REQUESTS_UNAVAILABLE')
        model, kv = fields.get('model'), fields.get('kv_caches')
        weights = None
        if model is not None:
            try:
                # Parameters are only identified, never copied or read.
                weights = self._set_identity(model.parameters())
            except Exception:
                self.gaps.add('TRANSITION_DISPATCH_WEIGHTS_UNAVAILABLE')
        return {'pid': os.getpid(), 'start_ticks': _start_ticks(), 'rank': _tp_rank(),
                'requests': requests, 'model_sha256': self.token(model), 'weights_sha256': weights,
                'kv_sha256': self._set_identity(kv) if type(kv) is list and kv else None}
