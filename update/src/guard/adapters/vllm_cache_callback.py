"""Source-qualified cache lookup callbacks, without native profile installation.

Uses only file pins already present in immutable lifecycle /8. Wrappers call the
original method once and retain its return/exception. Returned cache entries are
selection facts, never proof of GPU tensor consumption or authenticated tenancy.
No caller-supplied verified flag or serialized observation can qualify a hook.
"""
from __future__ import annotations

from .vllm_observer_pin_rows import candidate_sources, layout

import contextvars
from copy import deepcopy
import functools
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
import types
import weakref

from . import vllm_lifecycle_sensor as base
from .vllm_worker_startup_contract import artifact
from .vllm_worker_generation_contract import relative_code

SCHEMA = 'guard-vllm-cache-callback/1'
PROFILE = 'source-bound-cache-callback-candidate-v1'
NATIVE_PROFILE_SUPPORTED = False
TARGETS = {
    'vllm.v1.core.kv_cache_manager': ('KVCacheManager.get_computed_blocks',),
    'vllm.v1.core.block_pool': ('BlockPool.get_cached_block', 'BlockHashToBlockMap.get_one_block'),
}
SOURCES = candidate_sources(base.SOURCE_SHA, TARGETS)
MAX_EVENTS = 1024
MAX_OBJECTS = 256
MAX_BLOCKS = 256
# Pinned sources above the default 128 KiB read bound, by SHA-256 and exact reviewed
# size (vLLM 0.31.0 image). A file of any other size or content is still refused.
SOURCE_BYTES = {'bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a': 335342,
                # vllm.v1.core.sched.scheduler (#196 request-attempt P1)
                'f820bee4f42b878b262a30f3728a97d7413477b2a277748e20f2528ac2ec2f2a': 159016}
from .vllm_observer_pin_rows import SOURCE_BYTES as CANDIDATE_SOURCE_BYTES
_ACTIVE = contextvars.ContextVar('guard_cache_callback_active', default=None)
# Digests of bytes that already matched their pin. The bytes are re-read and
# re-hashed on every call; only the compile of identical bytes is reused.
_COMPILED = {}


def _digest(code, name):
    filename = name.replace('.', '/') + '.py'
    return base.code_digest(relative_code(code, filename))


def _pins(sources, name):
    """A module's reviewed source pins. One SHA-256 string, or a tuple of them when the
    same observer reviews several engine releases (one exact-byte row per release)."""
    pins = sources[name]
    pins = (pins,) if type(pins) is str else pins
    if (type(pins) is not tuple or not pins or len(set(pins)) != len(pins)
            or any(type(pin) is not str or re.fullmatch('[0-9a-f]{64}', pin) is None for pin in pins)):
        raise ValueError('CACHE_CALLBACK_SOURCE_UNSUPPORTED')
    return pins


def _source(module, sources):
    """Read the module file once per pin until exact bytes match. No pin is widened."""
    name = vars(module).get('__name__')
    path = vars(module).get('__file__')
    error = None
    for pin in _pins(sources, name):
        try:
            limits = SOURCE_BYTES | CANDIDATE_SOURCE_BYTES
            return pin, artifact(path, pin, **({'limit': limits[pin]} if pin in limits else {}))
        except ValueError as exc:
            error = exc
    raise error


def source_pin(module, sources):
    """The reviewed pin that the module's current bytes match (for evidence rows)."""
    return _source(module, sources)[0]


def _codes(module, targets=TARGETS, sources=SOURCES):
    name = vars(module).get('__name__')
    if name not in targets or name not in sources or sys.version_info[:2] != (3, 12) or sys.flags.optimize:
        raise ValueError('CACHE_CALLBACK_SOURCE_UNSUPPORTED')
    path = vars(module).get('__file__')
    pin, raw = _source(module, sources)
    key = (name, path, pin)
    if key not in _COMPILED:
        result = {}
        def visit(code):
            result[code.co_qualname] = _digest(code, name)
            for child in code.co_consts:
                if type(child) is types.CodeType:
                    visit(child)
        visit(compile(raw, path, 'exec', dont_inherit=True, optimize=0))
        _COMPILED[key] = result
    return dict(_COMPILED[key])


def _function(module, point):
    owner, method = point.split('.')
    cls = vars(module).get(owner)
    if not isinstance(cls, type):
        raise ValueError('CACHE_CALLBACK_ENTRY_UNSUPPORTED')
    value = vars(cls).get(method)
    if type(value) is not types.FunctionType or value.__globals__ is not vars(module):
        raise ValueError('CACHE_CALLBACK_ENTRY_UNSUPPORTED')
    return cls, method, value


def _uint(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


class CacheCallbackObserver:
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = PROFILE
    def __init__(self, session, *, sink=None, context_provider=None, clock=None):
        if type(session) is not str or re.fullmatch('[0-9a-f]{32}', session) is None:
            raise ValueError('CACHE_CALLBACK_SESSION_INVALID')
        self.session = session
        self.pid = os.getpid()
        self.injected_context = context_provider is not None
        self.context = (context_provider or base._context)()
        self.process_sha256 = base.sha(json.dumps(self.context, sort_keys=True, separators=(',', ':')).encode())
        self.sink = sink; self.clock = clock or time.monotonic_ns
        self.secret = os.urandom(32); self.objects = {}; self.object_serial = 0; self.sequence = 0
        self.events = []; self.gaps = set(); self.lock = threading.RLock()

    def fingerprint(self, value):
        # HMAC prevents low-entropy user/request/salt dictionary attacks against
        # the evidence. No cross-session or cross-process equality is claimed.
        if type(value) is str and len(value) <= 4096:
            raw = b's:' + value.encode('utf-8', 'surrogatepass')
        elif type(value) is bytes and len(value) <= 4096:
            raw = b'b:' + value
        elif type(value) is int and 0 <= value < 2**63:
            raw = b'i:' + str(value).encode()
        else:
            return None
        return hmac.new(self.secret, raw, hashlib.sha256).hexdigest()

    def token(self, value):
        if value is None:
            return None
        with self.lock:
            prior = self.objects.get(id(value))
            if prior is not None:
                if prior[0]() is value: return prior[1]
                del self.objects[id(value)]
            if len(self.objects) >= MAX_OBJECTS:
                self.gaps.add('CACHE_CALLBACK_OBJECT_LIMIT'); return None
            # Never retain outputs/tensors merely to identify them. A dead weak
            # reference cannot qualify a later object reusing the same address.
            key = id(value)
            def released(reference):
                with self.lock:
                    prior = self.objects.get(key)
                    if prior is not None and prior[0] is reference: self.objects.pop(key, None)
            try: reference = weakref.ref(value, released)
            except TypeError:
                self.gaps.add('CACHE_CALLBACK_WEAK_OBJECT_IDENTITY_UNAVAILABLE'); return None
            self.object_serial += 1
            token = self.fingerprint('object:' + str(self.object_serial))
            self.objects[id(value)] = (reference, token)
            return token

    def _block(self, value):
        return {'object_token': self.token(value),
            'block_id_sha256': self.fingerprint(_uint(base.attrs(value).get('block_id')))}

    def _blocks(self, values):
        if type(values) not in (list, tuple) or len(values) > MAX_BLOCKS:
            self.gaps.add('CACHE_CALLBACK_BLOCK_SHAPE_UNAVAILABLE'); return None
        return [self._block(value) for value in values]

    def _media(self, features):
        """REQ-16: 요청의 멀티모달 항목별 토큰 위치(offset·length)와 식별자 지문. 원 데이터·식별자 원문은 남기지 않는다."""
        if features is None or type(features) in (list, tuple) and not features:
            return []
        if type(features) not in (list, tuple) or len(features) > 16:
            self.gaps.add('CACHE_CALLBACK_MEDIA_SHAPE_UNAVAILABLE'); return None
        out = []
        for feature in features:
            fields = base.attrs(feature); position = base.attrs(fields.get('mm_position'))
            offset, length = _uint(position.get('offset')), _uint(position.get('length'))
            if offset is None or length is None or not length:
                self.gaps.add('CACHE_CALLBACK_MEDIA_SHAPE_UNAVAILABLE'); return None
            out.append({'offset': offset, 'length': length, 'identifier_sha256': self.fingerprint(fields.get('identifier'))})
        return out

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        active = _ACTIVE.get()
        active = active if active and active['observer'] is self and active['pid'] == os.getpid() and active['thread'] == threading.get_ident() else None
        row = {'receiver_token': self.token(receiver), 'request_sha256': active['request_sha256'] if active else None}
        if point == 'KVCacheManager.get_computed_blocks':
            request = args[1] if len(args) > 1 else kwargs.get('request')
            fields = base.attrs(request)
            hashes = fields.get('block_hashes')
            row.update(request_sha256=self.fingerprint(fields.get('request_id')),
                request_cache_salt_sha256=self.fingerprint(fields.get('cache_salt')),
                request_lora_name_sha256=self.fingerprint(base.attrs(fields.get('lora_request')).get('lora_name')),
                request_block_hashes_sha256=[self.fingerprint(v) for v in hashes] if type(hashes) in (list, tuple) and len(hashes) <= MAX_BLOCKS else None,
                request_media_ranges=self._media(fields.get('mm_features')),
                block_pool_token=self.token(base.attrs(receiver).get('block_pool')))
            if returned:
                if type(result) is tuple and len(result) == 3:
                    blocks = base.attrs(result[0]).get('blocks')
                    row.update(num_computed_tokens=_uint(result[1]), shared_prefix_boundary=_uint(result[2]),
                        returned_group_blocks=[self._blocks(v) for v in blocks] if type(blocks) is tuple and len(blocks) <= 64 else None)
                else:
                    self.gaps.add('CACHE_CALLBACK_COMPUTED_RESULT_UNAVAILABLE')
        elif point == 'BlockPool.get_cached_block':
            key = args[1] if len(args) > 1 else kwargs.get('block_hash')
            groups = args[2] if len(args) > 2 else kwargs.get('kv_cache_group_ids')
            row.update(lookup_block_hash_sha256=self.fingerprint(key),
                kv_cache_group_ids=list(groups) if type(groups) is list and len(groups) <= 64 and all(_uint(g) is not None for g in groups) else None)
            if returned:
                row.update(cache_lookup_hit=result is not None, returned_blocks=self._blocks(result) if result is not None else [])
        else:
            key = args[1] if len(args) > 1 else kwargs.get('key')
            row['lookup_key_with_group_sha256'] = self.fingerprint(key)
            if returned:
                row.update(cache_lookup_hit=result is not None, returned_block=self._block(result) if result is not None else None)
        return row

    def _emit(self, row):
        with self.lock:
            if len(self.events) >= MAX_EVENTS:
                self.gaps.add('CACHE_CALLBACK_EVENT_LIMIT'); return
            self.events.append(deepcopy(row))
        if self.sink is not None:
            try: self.sink(deepcopy(row))
            except BaseException:
                with self.lock: self.gaps.add('CACHE_CALLBACK_SINK_UNAVAILABLE')

    def _now(self):
        """An observation clock must never control native call semantics."""
        try:
            value = self.clock()
            if type(value) is int and 0 <= value < 2**63: return value
        except BaseException:
            pass
        with self.lock: self.gaps.add('CACHE_CALLBACK_CLOCK_UNAVAILABLE')
        return None

    def wrap(self, module, point):
        """Return an explicitly selected callback; no module/class mutation.

        A future approved profile installer may assign this wrapper. This module
        itself does not prepare assets, update immutable pins or claim deployment.
        """
        name = vars(module).get('__name__')
        if point not in self.targets.get(name, ()):
            raise ValueError('CACHE_CALLBACK_POINT_UNSUPPORTED')
        cls, method, original = _function(module, point)
        expected = _codes(module, self.targets, self.sources).get(point)
        if expected is None or _digest(original.__code__, name) != expected:
            raise ValueError('CACHE_CALLBACK_ENTRY_UNSUPPORTED')
        pin = source_pin(module, self.sources)
        self.__dict__.setdefault('_source_pins', {})[name] = pin

        def qualified():
            current = vars(cls).get(method)
            if os.getpid() != self.pid:
                self.gaps.add('CACHE_CALLBACK_PROCESS_CHANGED'); return False
            return (current in (original, wrapped, getattr(wrapped, 'guard_outer', None)) and original.__globals__ is vars(module)
                and _digest(original.__code__, name) == expected and _codes(module, self.targets, self.sources).get(point) == expected)

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            with self.lock:
                self.sequence += 1; call = self.fingerprint('call:' + str(self.sequence))
            parent = _ACTIVE.get()
            parent = parent if parent and parent['observer'] is self and parent['pid'] == os.getpid() and parent['thread'] == threading.get_ident() else None
            started = self._now(); state = 'CALL'; result = None; before = False; snapshot = {}
            try:
                before = bool(qualified())
                if before: snapshot = self._snapshot(point, args, kwargs)
                else: self.gaps.add('CACHE_CALLBACK_ENTRY_REPLACED')
            except BaseException: self.gaps.add('CACHE_CALLBACK_ENTRY_UNAVAILABLE')
            frame = {'observer': self, 'pid': os.getpid(), 'thread': threading.get_ident(), 'call': call,
                'request_sha256': snapshot.get('request_sha256') or (parent['request_sha256'] if parent else None),
                'request_sha256_list': snapshot.get('native_request_sha256') or (parent.get('request_sha256_list') if parent else None)}
            token = _ACTIVE.set(frame)
            try:
                common = {'schema': self.schema, 'session_sha256': base.sha(self.session.encode()), 'process_sha256': self.process_sha256,
                    'call_sha256': call, 'parent_call_sha256': parent['call'] if parent else None, 'point': point,
                    'source_file_sha256': pin, 'entry_code_sha256': expected,
                    'entry_qualified_before': before, 'runtime_context_injected': self.injected_context,
                    'authenticated_identity_verified': False, 'actual_tensor_consumption_verified': False,
                    'transitive_dependencies_verified': False, 'native_profile_installed': False}
                self._emit({**common, 'phase': state, 'operation_started_ns': started, 'operation_finished_ns': started, 'fields': snapshot})
                native_started = self._now()
                try:
                    result = original(*args, **kwargs); state = 'RETURN'; return result
                except BaseException:
                    state = 'RAISE'; raise
                finally:
                    finished = self._now(); after = False; fields = {}
                    try:
                        after = bool(qualified())
                        # Keep independent entry facts when a later observation
                        # fails; the original return/exception stays unchanged.
                        if before and after: fields = self._snapshot(point, args, kwargs, result, state == 'RETURN')
                        elif before: self.gaps.add('CACHE_CALLBACK_TERMINAL_ENTRY_REPLACED')
                    except BaseException: self.gaps.add('CACHE_CALLBACK_TERMINAL_UNAVAILABLE')
                    self._emit({**common, 'phase': state, 'entry_qualified_after': after,
                        'operation_started_ns': started, 'operation_finished_ns': finished,
                        'native_started_ns': native_started, 'native_finished_ns': finished, 'fields': fields})
            finally:
                _ACTIVE.reset(token)
        return wrapped

    def report(self):
        with self.lock:
            return {'profile': self.profile, 'events': deepcopy(self.events), 'missing': sorted(self.gaps),
                'session_sha256': base.sha(self.session.encode()), 'process_sha256': self.process_sha256,
                'native_profile_supported': False, 'native_profile_installed': False,
                'runtime_context_injected': self.injected_context, 'producer_authenticated': False,
                'identity_salt_issuer_and_normalizer_implemented': False,
                'actual_tensor_consumption_verified': False}


def project(report, policy_values):
    """Retain original policy values and independently bound native facts."""
    rows = [deepcopy(r) for r in report['events'] if r['phase'] == 'RETURN'
        and r['entry_qualified_before'] is True and r.get('entry_qualified_after') is True]
    return {'profile': PROFILE, 'policy_values': deepcopy(policy_values), 'source_bound_returns': rows,
        'missing': sorted(set(report['missing']) | {'trusted_identity_to_cache_group_unimplemented',
            'key_context_construction_sourcepoint_unimplemented', 'GPU_cache_consumer_and_output_unverified',
            'native_profile_installer_unimplemented'}), 'scope_complete': False,
        'native_profile_supported': False, 'producer_authenticated': False}
