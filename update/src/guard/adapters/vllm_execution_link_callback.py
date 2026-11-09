"""Opt-in actual V1 API/scheduler/worker/model/Sampler connection observations.

SchedulerOutput is fingerprinted on both sides of the real executor transport.
Exact request IDs are HMACed without tag/prefix normalization. CPU tensors are
read only for small sampling metadata; GPU tensors are never synchronized.
"""
from __future__ import annotations

import hashlib
import functools
import json
import os
import sys
import types

from . import vllm_cache_callback as cache
from . import vllm_consumption_callback as consumption
from . import vllm_transition_dispatch_callback as dispatch
from .vllm_worker_shard_callback import _start_ticks, _tp_rank

SCHEMA = 'guard-vllm-execution-links/1'
INPUT, RUNNER = consumption.INPUT, consumption.RUNNER
SCHEDULER = 'vllm.v1.core.sched.scheduler'
SAMPLER = 'vllm.v1.sample.sampler'
STRUCTURED = 'vllm.v1.structured_output.utils'
TARGETS = {INPUT: ('InputProcessor.process_inputs', 'InputProcessor.assign_request_id'),
           SCHEDULER: ('Scheduler.add_request', 'Scheduler.schedule', 'Scheduler._free_request'),
           RUNNER: ('GPUModelRunner._update_states', 'GPUModelRunner._model_forward', 'GPUModelRunner._preprocess', 'GPUModelRunner._sample', 'GPUModelRunner._gather_mm_embeddings', 'GPUModelRunner.reload_weights'),
           SAMPLER: ('Sampler.forward',), STRUCTURED: ('apply_grammar_bitmask',)}
SOURCES = {**cache.base.SOURCE_SHA, SAMPLER: 'd299305528479ff505b416b759db208fcf08ca6e86cc1f7cfc38be85bbd0e844',
           STRUCTURED: '4c16c4f5faa22b2d53f0712a2f2de647d36de9e908fcba4114397eaeebdba4fc'}
MAX_BATCH = 256


def config_digest(config):
    if config is None:
        return None
    return hashlib.sha256(json.dumps(config.to_dict(), sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


class ExecutionLinkObserver(dispatch.TransitionDispatchObserver):
    targets, sources, schema = TARGETS, SOURCES, SCHEMA
    profile = 'source-bound-execution-links-cpu-v1'

    def wrap_assignment(self, module):
        """The actual ID assignment is a staticmethod; retain that descriptor."""
        return self._wrap_function(module, module.InputProcessor, 'assign_request_id', 'InputProcessor.assign_request_id', static=True)

    def wrap_grammar(self, module):
        return self._wrap_function(module, module, 'apply_grammar_bitmask', 'apply_grammar_bitmask', static=False)

    def _wrap_function(self, module, owner, attribute, point, *, static):
        descriptor = vars(owner).get(attribute)
        if static and type(descriptor) is not staticmethod:
            raise ValueError('EXECUTION_LINK_ASSIGNMENT_UNSUPPORTED')
        original = descriptor.__func__ if static else descriptor
        if type(original) is not types.FunctionType:
            raise ValueError('EXECUTION_LINK_FUNCTION_UNSUPPORTED')
        name = vars(module)['__name__']
        expected = cache._codes(module, self.targets, self.sources).get(point)
        if cache._digest(original.__code__, name) != expected:
            raise ValueError('EXECUTION_LINK_ASSIGNMENT_UNSUPPORTED')
        def qualified():
            current = vars(owner).get(attribute)
            function = current.__func__ if static and type(current) is staticmethod else current
            return (os.getpid() == self.pid and function in (original, wrapped)
                    and original.__globals__ is vars(module)
                    and cache._codes(module, self.targets, self.sources).get(point) == cache._digest(original.__code__, name) == expected)
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            with self.lock:
                self.sequence += 1; call = self.fingerprint('call:' + str(self.sequence))
            before = qualified()
            common = {'schema': SCHEMA, 'session_sha256': cache.base.sha(self.session.encode()), 'process_sha256': self.process_sha256,
                      'call_sha256': call, 'point': point, 'source_file_sha256': self.sources[name], 'entry_code_sha256': expected,
                      'entry_qualified_before': before, 'runtime_context_injected': self.injected_context}
            def emit(phase):
                try:
                    after = qualified()
                    self._emit({**common, 'phase': phase, 'entry_qualified_after': after,
                                'fields': self._snapshot(point, args, kwargs, returned=phase == 'RETURN') if before and after else {}})
                except BaseException:
                    self.gaps.add('EXECUTION_LINK_ASSIGNMENT_OBSERVATION_FAILED')
            emit('CALL')
            try:
                result = original(*args, **kwargs)
            except BaseException:
                emit('RAISE'); raise
            emit('RETURN'); return result
        return staticmethod(wrapped) if static else wrapped

    def _hash(self, value):
        return self.fingerprint(hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest())

    def _params(self, value):
        out = consumption.params_summary(value)
        if out is not None:
            attrs = cache.base.attrs(value)
            allowed = attrs.get('allowed_token_ids')
            out['allowed_token_ids_sha256'] = self._hash(sorted(set(allowed))) if type(allowed) is list else None
            out['structured_outputs_present'] = attrs.get('structured_outputs') is not None
            for name in ('frequency_penalty', 'presence_penalty', 'repetition_penalty'):
                try:
                    out[name] = round(float(attrs.get(name)), 6)
                except (TypeError, ValueError):
                    out[name] = None
        return out

    def _core_input(self, value):
        f = cache.base.attrs(value)
        return self._hash({'external': f.get('external_req_id') or f.get('request_id'),
                           'prompt_token_ids': f.get('prompt_token_ids'), 'params': self._params(f.get('sampling_params')),
                           'mm_features': self._features(f.get('mm_features'))})

    def _features(self, items):
        if items is None:
            return None
        values = []
        for item in items:
            data = cache.base.attrs(item); position = cache.base.attrs(data.get('mm_position'))
            mask = position.get('is_embed')
            values.append({'modality': data.get('modality'), 'identifier': data.get('identifier'),
                           'offset': position.get('offset'), 'length': position.get('length'),
                           'is_embed': mask.tolist() if mask is not None and str(mask.device) == 'cpu' and mask.numel() <= 65536 else None})
        return self._hash(values)

    def _schedule(self, value):
        f = cache.base.attrs(value)
        new, cached, scheduled = f.get('scheduled_new_reqs'), cache.base.attrs(f.get('scheduled_cached_reqs')), f.get('num_scheduled_tokens')
        if type(new) is not list or len(new) > MAX_BATCH or type(scheduled) is not dict or len(scheduled) > MAX_BATCH:
            self.gaps.add('EXECUTION_LINK_SCHEDULE_UNAVAILABLE'); return None
        # Include actual request identity, token/block assignments and cached state
        # counters so repeated decode steps cannot be joined just by request ID.
        payload = {'scheduled': sorted(scheduled.items()), 'finished': sorted(f.get('finished_req_ids') or []),
                   'cached': {name: cached.get(name) for name in ('req_ids', 'num_computed_tokens', 'num_output_tokens', 'new_block_ids', 'new_token_ids')},
                   'resumed': sorted(cached.get('resumed_req_ids') or []), 'new': []}
        for item in new:
            data = cache.base.attrs(item)
            payload['new'].append({name: data.get(name) for name in ('req_id', 'prompt_token_ids', 'block_ids', 'num_computed_tokens')})
            payload['new'][-1]['mm_features_sha256'] = self._features(data.get('mm_features'))
        return self._hash(payload)

    def _parent(self, points):
        frame = sys._getframe(1)
        try:
            for _ in range(12):
                if frame is None:
                    break
                name = frame.f_globals.get('__name__')
                point = frame.f_code.co_qualname
                if name == RUNNER and point in points:
                    module = sys.modules.get(name)
                    expected = cache._codes(module, {RUNNER: tuple(points)}, SOURCES).get(point)
                    if frame.f_globals is vars(module) and cache._digest(frame.f_code, name) == expected:
                        return dict(frame.f_locals)
                frame = frame.f_back
        finally:
            del frame
        return {}

    def _requests(self, runner):
        f = cache.base.attrs(runner); batch = cache.base.attrs(f.get('input_batch'))
        ids, states = batch.get('_req_ids'), f.get('requests')
        if type(ids) is not list or len(ids) > MAX_BATCH or type(states) is not dict:
            return []
        return [{'request_sha256': self.fingerprint(req), 'state_sha256': self.token(states.get(req)),
                 'num_computed_tokens': cache.base.attrs(states.get(req)).get('num_computed_tokens'),
                 'mm_features_sha256': self._features(cache.base.attrs(states.get(req)).get('mm_features')),
                 'params': self._params(cache.base.attrs(states.get(req)).get('sampling_params'))} for req in ids if req is not None]

    def _tensor_digest(self, tensor):
        if tensor is None or str(tensor.device) != 'cpu' or tensor.numel() * tensor.element_size() > 2 * 1024**2:
            return None
        return self.fingerprint(hashlib.sha256(memoryview(tensor.detach().contiguous().numpy()).cast('B')).hexdigest())

    def _mm_inputs(self, runner, wire, tensor):
        context = getattr(self, 'mm_context', {}).get((self.token(runner), wire))
        return {'inputs_embeds_object_sha256': self.token(tensor),
                'selected_embeddings_sha256': self._tensor_digest(tensor[context[0]]) if context and tensor is not None and str(tensor.device) == 'cpu' else None}

    def _cpu_parameter(self, model):
        """One named parameter, bounded CPU bytes; no GPU transfer or copy."""
        try:
            name, parameter = next(model.named_parameters())
            size = parameter.numel() * parameter.element_size()
            if str(parameter.device) != 'cpu' or not parameter.is_contiguous() or not 0 < size <= 256 * 1024**2:
                self.gaps.add('EXECUTION_LINK_PARAMETER_SCOPE_UNSUPPORTED'); return None
            raw = memoryview(parameter.detach().numpy()).cast('B')
            return {'name_sha256': self.fingerprint(name), 'bytes': size,
                    'bytes_sha256': self.fingerprint(hashlib.sha256(raw).hexdigest())}
        except Exception:
            self.gaps.add('EXECUTION_LINK_PARAMETER_BYTES_UNAVAILABLE'); return None

    def _metadata(self, value):
        f = cache.base.attrs(value)
        def values(name):
            tensor = f.get(name)
            if tensor is None:
                return None
            # All inspected metadata are CPU tensors in this profile. Never .cpu().
            if str(tensor.device) != 'cpu' or tensor.numel() > MAX_BATCH * 65536:
                self.gaps.add('EXECUTION_LINK_METADATA_NOT_CPU'); return None
            return tensor.tolist()
        mask = f.get('allowed_token_ids_mask')
        allowed_ids = [self._hash((~r).nonzero().flatten().tolist()) for r in mask] if mask is not None and str(mask.device) == 'cpu' else None
        tensors = [f.get(k) for k in ('temperature', 'top_p', 'top_k', 'frequency_penalties', 'presence_penalties', 'repetition_penalties', 'allowed_token_ids_mask')]
        cpu_only = all(t is None or str(t.device) == 'cpu' for t in tensors)
        return {'cpu_only': cpu_only, 'temperature': values('temperature'), 'top_p': values('top_p'), 'top_k': values('top_k'),
                'allowed_token_ids_sha256': allowed_ids,
                'all_greedy': f.get('all_greedy'), 'no_penalties': f.get('no_penalties'),
                'frequency_penalty': values('frequency_penalties') if not f.get('no_penalties') else None,
                'presence_penalty': values('presence_penalties') if not f.get('no_penalties') else None,
                'repetition_penalty': values('repetition_penalties') if not f.get('no_penalties') else None,
                'allowed_tokens': None if f.get('allowed_token_ids_mask') is None else [int((~r).sum()) for r in f.get('allowed_token_ids_mask')]
                if str(f.get('allowed_token_ids_mask').device) == 'cpu' else None}

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        f = cache.base.attrs(receiver)
        who = {'pid': os.getpid(), 'start_ticks': _start_ticks(), 'rank': _tp_rank()}
        if point == 'GPUModelRunner.reload_weights':
            iterator = args[1] if len(args) > 1 else kwargs.get('weights_iterator')
            return {**who, 'model_sha256': self.token(f.get('model')), 'cpu_parameter': self._cpu_parameter(f.get('model')),
                    'iterator_supplied': iterator is not None, 'checkpoint_format': kwargs.get('is_checkpoint_format', args[3] if len(args) > 3 else True)}
        if point == 'apply_grammar_bitmask':
            parent = self._parent({'GPUModelRunner.sample_tokens'})
            runner = parent.get('self'); scheduled = args[0] if args else kwargs.get('scheduler_output')
            grammar = args[1] if len(args) > 1 else kwargs.get('grammar_output')
            logits = args[3] if len(args) > 3 else kwargs.get('logits')
            return {**who, 'schedule_sha256': self._schedule(scheduled), 'execution_parent_verified': bool(parent),
                    'requests': self._requests(runner), 'logits_same_as_model_output': logits is parent.get('logits') and logits is not None,
                    'grammar_present': grammar is not None}
        if point == 'InputProcessor.assign_request_id':
            req = args[0] if args else kwargs.get('request'); data = cache.base.attrs(req)
            return {**who, 'request_sha256': self.fingerprint(data.get('request_id')),
                    'external_sha256': self.fingerprint(data.get('external_req_id')), 'core_input_sha256': self._core_input(req)}
        if point == 'InputProcessor.process_inputs':
            params = args[3] if len(args) > 3 else kwargs.get('params')
            out = {'request_sha256': self.fingerprint(args[1] if len(args) > 1 else kwargs.get('request_id')), 'params': self._params(params)}
            if returned:
                data = cache.base.attrs(result)
                out.update(request_sha256=self.fingerprint(data.get('request_id')), params=self._params(data.get('sampling_params')),
                           core_input_sha256=self._core_input(result),
                           mm_features_sha256=self._features(data.get('mm_features')),
                           prompt_sha256=self._hash(data.get('prompt_token_ids')),
                           tokenizer_sha256=self.token(cache.base.attrs(f.get('renderer')).get('tokenizer')))
            return {**who, **out}
        if point in ('Scheduler.add_request', 'Scheduler._free_request'):
            req = args[1] if len(args) > 1 else kwargs.get('request')
            data = cache.base.attrs(req)
            return {**who, 'request_sha256': self.fingerprint(data.get('request_id')), 'request_object_sha256': self.token(req),
                    'mm_features_sha256': self._features(data.get('mm_features')),
                    'preemptions': data.get('num_preemptions'), 'params': self._params(data.get('sampling_params'))}
        if point == 'Scheduler.schedule':
            scheduled = cache.base.attrs(result).get('num_scheduled_tokens') if returned else None
            states = f.get('requests') or {}
            return {**who, 'schedule_sha256': self._schedule(result) if returned else None,
                    'request_sha256s': [self.fingerprint(v) for v in scheduled] if type(scheduled) is dict else [],
                    'attempts': [{'request_sha256': self.fingerprint(v), 'preemptions': cache.base.attrs(states.get(v)).get('num_preemptions'),
                                  'request_object_sha256': self.token(states.get(v))} for v in scheduled if v in states] if type(scheduled) is dict else []}
        if point == 'GPUModelRunner._update_states':
            scheduled = args[1] if len(args) > 1 else kwargs.get('scheduler_output')
            return {**who, 'schedule_sha256': self._schedule(scheduled), 'requests': self._requests(receiver) if returned else []}
        if point in ('GPUModelRunner._model_forward', 'GPUModelRunner._gather_mm_embeddings', 'GPUModelRunner._preprocess'):
            parent = self._parent({'GPUModelRunner.execute_model'})
            scheduled = parent.get('scheduler_output')
            out = {**who, 'schedule_sha256': self._schedule(scheduled) if scheduled is not None else None,
                   'execution_parent_verified': bool(parent), 'requests': self._requests(receiver),
                   'hf_config_sha256': config_digest(cache.base.attrs(f.get('model_config')).get('hf_config')),
                   'model_sha256': self.token(f.get('model'))}
            if point.endswith('_model_forward'):
                out['cpu_parameter'] = self._cpu_parameter(f.get('model'))
                tensor = kwargs.get('inputs_embeds') if 'inputs_embeds' in kwargs else args[4] if len(args) > 4 else None
                out['inputs_embeds_present'] = tensor is not None
                out.update(self._mm_inputs(receiver, out['schedule_sha256'], tensor))
            elif point.endswith('_preprocess'):
                if returned and type(result) is tuple:
                    out.update(self._mm_inputs(receiver, out['schedule_sha256'], result[1]))
            elif returned and type(result) is tuple:
                out['embedding_shapes'] = [list(t.shape) for t in result[0]]
                out['mask_tokens'] = int(result[1].sum()) if str(result[1].device) == 'cpu' else None
                out['gathered_embeddings_sha256'] = None
                if (result[0] and str(result[1].device) == 'cpu' and result[1].numel() <= 65536
                        and all(str(t.device) == 'cpu' for t in result[0])
                        and sum(t.numel() * t.element_size() for t in result[0]) <= 2 * 1024**2):
                    import torch
                    digest = self._tensor_digest(torch.cat(result[0]))
                    out['gathered_embeddings_sha256'] = digest
                    if not hasattr(self, 'mm_context'):
                        self.mm_context = {}
                    if len(self.mm_context) >= 256:
                        self.mm_context.pop(next(iter(self.mm_context)))
                    self.mm_context[(self.token(receiver), out['schedule_sha256'])] = (result[1].nonzero().flatten().tolist(), digest)
            return out
        if point == 'GPUModelRunner._sample':
            parent = self._parent({'GPUModelRunner.sample_tokens'})
            return {**who, 'schedule_sha256': self._schedule(parent['scheduler_output']) if 'scheduler_output' in parent else None,
                    'execution_parent_verified': bool(parent), 'requests': self._requests(receiver)}
        if point == 'Sampler.forward':
            parent = self._parent({'GPUModelRunner._sample'})
            sample = self._parent({'GPUModelRunner.sample_tokens'})
            runner = parent.get('self'); batch = cache.base.attrs(cache.base.attrs(runner).get('input_batch'))
            metadata = args[2] if len(args) > 2 else kwargs.get('sampling_metadata')
            logits = args[1] if len(args) > 1 else kwargs.get('logits')
            return {**who, 'schedule_sha256': self._schedule(sample['scheduler_output']) if 'scheduler_output' in sample else None,
                    'execution_parent_verified': bool(parent) and bool(sample), 'requests': self._requests(runner),
                    'metadata_same_as_batch': metadata is batch.get('sampling_metadata'),
                    'grammar_present': sample.get('grammar_output') is not None,
                    'logits_same_as_model_output': logits is sample.get('logits') and logits is not None,
                    'metadata': self._metadata(metadata) if not returned else None,
                    'sampled_shape': list(result.sampled_token_ids.shape) if returned and result is not None else None}
        return who
