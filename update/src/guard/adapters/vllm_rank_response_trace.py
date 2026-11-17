"""Separate passive source trace candidate; the registered RPC SDK is unchanged.

Only exact original source frames inside a bound deferred-result call are read.
Response payloads, exception messages and engine outputs are never inspected.
These rows alone do not establish fault causality, delivery, recovery or release.
"""
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import types

from . import vllm_rank_ipc_identity as identity
from . import vllm_rank_ipc_sources as sources
from . import vllm_rank_rpc_hooks as rpc

PROFILES = Path(__file__).with_name('assets') / 'vllm_rank_response_trace_profiles.json'
SCHEMA = 'guard-rank-response-source-trace-candidate/1'
MAX_ROWS = 8192
MAX_FRAMES = 64


def profile(image):
    value = json.loads(PROFILES.read_bytes())
    runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
    rows = [row for row in value['profiles'] if row['image'] == image and row['runtime'] == runtime]
    if value['schema'] != 'guard-rank-response-trace-profiles/1' or len(rows) != 1 or sys.flags.optimize:
        raise ValueError('RANK_RESPONSE_PROFILE_UNAVAILABLE')
    return rows[0]


class ResponseTrace:
    def __init__(self, *, observer, key, emit, on_gap=None):
        if type(observer) is not rpc.RPCObserver or type(key) is not bytes or len(key) != 32 or key != observer.key:
            raise ValueError('RANK_RESPONSE_OBSERVER_UNAVAILABLE')
        self.observer, self.key, self.emit = observer, key, emit
        self.on_gap = on_gap
        self.profile = profile(observer.image)
        self.pid = os.getpid()
        self.serial = 0
        self.frames = {}
        self.callback = self._trace
        self.owner_thread = None
        self.disabled = False
        self.diagnostic = None

    def _hash(self, kind, value):
        return hmac.new(self.key, json.dumps([kind, self.pid, value], sort_keys=True,
                        separators=(',', ':')).encode(), sha256).hexdigest()

    def _fresh(self, module_name, frame):
        row = self.profile['sources'][module_name]
        module = sys.modules.get(module_name)
        if type(module) is not types.ModuleType or frame.f_globals is not vars(module):
            raise ValueError('RANK_RESPONSE_SOURCE_UNAVAILABLE')
        path = vars(module).get('__file__')
        if type(path) is not str:
            raise ValueError('RANK_RESPONSE_SOURCE_UNAVAILABLE')
        sources._bytes(path, row)
        point = frame.f_code.co_qualname
        if sources.code_digest(sources.relative_code(frame.f_code, row['path'])) != row['body_code_sha256'][point]:
            raise ValueError('RANK_RESPONSE_SOURCE_DRIFT')
        return row

    def _root(self, frame, module_name, point):
        """Revalidate the existing wrapper's actual same-Future request binding."""
        original = self.observer.functions.get(module_name, {}).get(point)
        installed = self.observer.installed.get((module_name, point))
        parent = frame.f_back
        if (original is None or original.__code__ is not frame.f_code or installed is None
                or parent is None or parent.f_code is not installed[2].__code__
                or parent.f_globals is not vars(rpc) or parent.f_locals.get('self') is not self.observer):
            return None
        spec = self.observer.sources[module_name]['hooks'][point]
        if spec.get('kind') != 'deferred-result':
            return None
        self.observer._fresh(spec['source_modules'])
        proof = parent.f_locals.get('proof')
        owner = frame.f_locals.get('self')
        context = rpc.ipc.base._context()
        bound = self.observer._deferred(module_name, point, owner, context)
        if bound is None or type(proof) is not dict:
            return None
        binding, requests = bound
        if (proof.get('event') != 'CALL' or proof.get('point') != point or proof.get('context') != context
                or proof.get('deferred_rpc') != binding or proof.get('request_hmac_sha256') != requests
                or len(requests) != 1 or re.fullmatch('[0-9a-f]{64}', requests[0]) is None
                or proof.get('object_hmac_sha256') != binding['future_object_hmac_sha256']
                or type(proof.get('call_hmac_sha256')) is not str
                or re.fullmatch('[0-9a-f]{64}', proof['call_hmac_sha256']) is None):
            raise ValueError('RANK_RESPONSE_REQUEST_UNVERIFIED')
        return {'context': dict(context), 'request_hmac_sha256': list(requests),
                'deferred_rpc': dict(binding), 'deferred_call_hmac_sha256': proof['call_hmac_sha256']}

    def _ancestor(self, frame):
        parent = frame.f_back
        try:
            for _ in range(16):
                if parent is None:
                    return None
                value = self.frames.get(id(parent))
                if value is not None:
                    return value
                parent = parent.f_back
            return None
        finally:
            parent = None

    def _queue(self, frame, spec):
        """Fingerprint only the reader's original shared-memory FD, no queue read."""
        module = sys.modules[frame.f_globals['__name__']]
        queue_type = vars(module).get(spec['queue_class'])
        buffer_type = vars(module).get(spec['buffer_class'])
        owner = frame.f_locals.get('self')
        if type(queue_type) is not type or type(buffer_type) is not type or type(owner) is not queue_type:
            raise ValueError('RANK_RESPONSE_QUEUE_UNAVAILABLE')
        # Exact source classes have their own standard instance-dict descriptors.
        descriptor = vars(queue_type).get('__dict__')
        if type(descriptor) is not types.GetSetDescriptorType:
            raise ValueError('RANK_RESPONSE_QUEUE_UNAVAILABLE')
        state = descriptor.__get__(owner, queue_type)
        if state.get('_is_local_reader') is not True or state.get('_is_writer') is not False:
            raise ValueError('RANK_RESPONSE_QUEUE_UNAVAILABLE')
        buffer = state.get('buffer')
        descriptor = vars(buffer_type).get('__dict__')
        if type(buffer) is not buffer_type or type(descriptor) is not types.GetSetDescriptorType:
            raise ValueError('RANK_RESPONSE_QUEUE_UNAVAILABLE')
        memory = descriptor.__get__(buffer, buffer_type).get('shared_memory')
        return identity.shared_memory_endpoint(memory, key=self.key)

    def _row(self, frame, state, event, error=None):
        if self.serial >= MAX_ROWS:
            raise ValueError('RANK_RESPONSE_ROW_BOUND')
        self.serial += 1
        row = {'schema': SCHEMA, 'profile': self.profile['id'], 'sequence': self.serial,
               'process': dict(state['binding']['context']), 'thread_hmac_sha256': self._hash('thread', threading.get_ident()),
               'operation_hmac_sha256': state['operation'], 'binding': state['binding'],
               'frame_call_hmac_sha256': state['call'], 'parent_frame_call_hmac_sha256': state['parent'],
               'point': state['point'], 'source_module': state['module'],
               'source_sha256': self.profile['sources'][state['module']]['sha256'],
               'body_code_sha256': self.profile['sources'][state['module']]['body_code_sha256'][state['point']],
               'event': event, 'instruction_offset': frame.f_lasti, 'line': frame.f_lineno,
               'started_ns': state['started_ns'], 'observed_ns': time.monotonic_ns(),
               'reader_endpoint': state['queue'], 'exception': None,
               'producer_registered': False, 'fault_causality_verified': False,
               'upper_delivery_verified': False, 'recovery_verified': False, 'whole_resources_released': False}
        if error is not None:
            # The base slots bypass user-defined exception attributes/repr/str.
            cause = BaseException.__dict__['__cause__'].__get__(error, type(error))
            row['exception'] = {'object_hmac_sha256': self._hash('exception', id(error)),
                'cause_hmac_sha256': None if cause is None else self._hash('exception', id(cause)),
                'kind': 'timeout' if type(error) is TimeoutError else 'runtime-error' if type(error) is RuntimeError else 'other'}
        elif event == 'UNWIND':
            row['exception'] = state['pending']
        self.emit(row)
        return row

    def _trace(self, frame, event, argument):
        if self.disabled or os.getpid() != self.pid:
            if sys.gettrace() is self.callback:
                sys.settrace(None)
            return None
        try:
            if event == 'call':
                module = frame.f_globals.get('__name__')
                point = frame.f_code.co_qualname
                selected = self.profile['sources'].get(module, {}).get('points', {}).get(point)
                if selected is None:
                    return None
                self._fresh(module, frame)
                if selected['kind'] == 'root':
                    binding = self._root(frame, module, point)
                    if binding is None:
                        return None
                    root = id(frame)
                    operation = self._hash('operation', [binding['deferred_call_hmac_sha256'], self.serial + 1])
                    parent_call = None
                else:
                    ancestor = self._ancestor(frame)
                    if ancestor is None:
                        return None
                    if ancestor['kind'] != selected['parent_kind']:
                        raise ValueError('RANK_RESPONSE_PARENT_UNVERIFIED')
                    binding, root, operation = ancestor['binding'], ancestor['root'], ancestor['operation']
                    parent_call = ancestor['call']
                    argument_name = selected.get('future_owner_argument')
                    if argument_name is not None:
                        owner = frame.f_locals.get(argument_name)
                        if self.observer.identity_observer._name(owner, binding['context']) != binding['deferred_rpc']['future_object_hmac_sha256']:
                            raise ValueError('RANK_RESPONSE_FOREIGN_DEFERRED_DRAIN')
                if len(self.frames) >= MAX_FRAMES:
                    raise ValueError('RANK_RESPONSE_FRAME_BOUND')
                state = {'module': module, 'point': point, 'kind': selected['kind'], 'binding': binding, 'root': root,
                         'operation': operation, 'started_ns': time.monotonic_ns(), 'pending': None,
                         'call': self._hash('frame-call', [operation, self.serial + 1]), 'parent': parent_call,
                         'queue': self._queue(frame, selected) if selected['kind'] == 'reader' else None}
                self.frames[id(frame)] = state
                frame.f_trace_lines = False
                self._row(frame, state, 'CALL')
            else:
                state = self.frames.get(id(frame))
                if state is None:
                    return None
                if event == 'exception':
                    if type(argument) is not tuple or len(argument) != 3 or not isinstance(argument[1], BaseException):
                        raise ValueError('RANK_RESPONSE_EXCEPTION_UNAVAILABLE')
                    row = self._row(frame, state, 'EXCEPTION', argument[1])
                    state['pending'] = dict(row['exception'])
                elif event == 'return':
                    source = self._fresh(state['module'], frame)
                    # The return argument can be model output and is never read.
                    normal = frame.f_lasti in source['return_offsets'][state['point']]
                    if not normal and state['pending'] is None:
                        raise ValueError('RANK_RESPONSE_UNWIND_UNAVAILABLE')
                    self._row(frame, state, 'RETURN' if normal else 'UNWIND')
                    del self.frames[id(frame)]
                    if state['root'] == id(frame):
                        self.frames = {key: value for key, value in self.frames.items() if value['root'] != id(frame)}
            return self.callback
        except BaseException:
            # Never propagate an observer error into the engine or serialize it.
            self.disabled, self.diagnostic = True, 'RANK_RESPONSE_OBSERVATION_INCOMPLETE'
            self.frames.clear()
            if self.on_gap is not None:
                try:
                    self.on_gap(self.diagnostic)
                except BaseException:
                    pass
            return None

    def install(self):
        if (self.owner_thread is not None or self.disabled or os.getpid() != self.pid
                or sys.gettrace() is not None or threading.gettrace() is not None
                or sys.getprofile() is not None or threading.getprofile() is not None):
            raise ValueError('RANK_RESPONSE_TRACE_ALREADY_ACTIVE')
        self.owner_thread = threading.get_ident()
        threading.settrace(self.callback)
        try:
            sys.settrace(self.callback)
        except BaseException:
            threading.settrace(None)
            self.owner_thread = None
            raise

    def close(self):
        if self.owner_thread != threading.get_ident():
            raise ValueError('RANK_RESPONSE_TRACE_OWNER_REQUIRED')
        self.disabled = True
        if sys.gettrace() is self.callback:
            sys.settrace(None)
        if threading.gettrace() is self.callback:
            threading.settrace(None)
        self.frames.clear()
