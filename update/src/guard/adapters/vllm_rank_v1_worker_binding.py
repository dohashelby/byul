"""정확 worker 루프의 queue 수신/응답만 읽는 후속 /2 후보.

모델 함수/출력·예외 본문은 읽지 않는다. 수신/응답을 모델 소비·전체 그룹 수락이나
장애/판정 사건으로 바꾸지 않으며, 기존 /1 제어점/등록을 열지 않는다.
"""
import enum
import functools
from hashlib import sha256
import hmac
import json
import os
import re
import sys
import threading
import time
import types

from . import vllm_rank_ipc_hooks as ipc
from . import vllm_rank_ipc_sources as sources
from . import vllm_rank_request_identity as request_identity

SCHEMA = 'guard-rank-rpc-hook-candidate/2'
FALSE_FIELDS = ('source_authenticated', 'engine_group_verified', 'scope_complete', 'producer_registered')


def _fingerprint(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _native_ids(values, key):
    # /28의 native-request domain을 그대로 사용한다. 원 ID는 반환/기록하지 않는다.
    request_identity._identifiers(values)
    return [hmac.new(key, b'guard-native-request-binding/1\0native-request\0' + value.encode('utf-8'),
                     sha256).hexdigest() for value in values]


class _Objects:
    """기존 공통 객체 HMAC의 상한·weakref·fork 처리를 재사용한다."""
    def _name(self, value, context):
        return ipc.IPCObserver._name(self, value, context)

    def __init__(self, key):
        self.key, self.pid, self.serial, self.objects = key, os.getpid(), 0, {}
        self.lock = threading.RLock()


class WorkerObserver:
    def __init__(self, *, image, key, session, fresh_prepared_sha256, binding_key_asset_sha256, emit, verify_observer):
        runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
        rows = json.loads(sources.V1_REVIEWS.read_bytes())['profiles']
        matches = [p for p in rows if p['image'] == image and p['runtime'] == runtime and 'worker_binding' in p]
        if (len(matches) != 1 or os.name != 'posix' or sys.flags.optimize != 0
                or type(key) is not bytes or len(key) != 32 or not callable(emit) or not callable(verify_observer)
                or type(session) is not str or re.fullmatch('[0-9a-f]{32}', session) is None
                or not _fingerprint(fresh_prepared_sha256) or not _fingerprint(binding_key_asset_sha256)):
            raise ValueError('RANK_V1_BINDING_SETTINGS_INVALID')
        self.profile, = matches
        self.spec = self.profile['worker_binding']
        self.image, self.key, self.session, self.emit = image, key, session, emit
        self.verify_observer = verify_observer
        self.fresh_prepared_sha256, self.binding_key_asset_sha256 = fresh_prepared_sha256, binding_key_asset_sha256
        self.modules, self.functions, self.installed = {}, {}, {}
        self.objects, self.pending = _Objects(key), {}
        self.pid, self.serial, self.lock = os.getpid(), 0, threading.RLock()

    def _fork(self):
        if self.pid != os.getpid():
            self.pid, self.serial, self.lock, self.pending = os.getpid(), 0, threading.RLock(), {}

    def _call(self, context):
        self._fork()
        with self.lock:
            if self.serial >= 4096:
                raise ValueError('RANK_V1_CALL_BOUND')
            self.serial += 1
            raw = json.dumps(['worker-rpc-receive/2', context, self.serial], sort_keys=True, separators=(',', ':')).encode()
            return hmac.new(self.key, raw, sha256).hexdigest()

    def _fresh(self):
        self.verify_observer()
        # 다른 정확 source loader가 먼저 맡은 비감싸기 모듈도 원 함수/bytes로
        # 대조한다. queue는 이미 설치한 정확 wrapper가 있어야 한다.
        for name in self.profile['sources']:
            if name not in self.modules and name != self.spec['queue_module']:
                module = sys.modules.get(name)
                if type(module) is types.ModuleType:
                    self.attach(module)
        if set(self.modules) != set(self.profile['sources']):
            raise ValueError('RANK_V1_SOURCE_GRAPH_INCOMPLETE')
        for name, module in self.modules.items():
            spec = self.profile['sources'][name]
            sources._bytes(vars(module)['__file__'], spec)
            for point, original in self.functions[name].items():
                if (original.__globals__ is not vars(module)
                        or sources.code_digest(sources.relative_code(original.__code__, spec['path']))
                           != spec['body_code_sha256'][point]):
                    raise ValueError('RANK_V1_SOURCE_DRIFT')
                installed = self.installed.get((name, point))
                if installed is None:
                    current = sources._function(module, point)
                else:
                    cls, member = point.split('.')
                    current = type.__getattribute__(vars(module)[cls], '__dict__').get(member)
                if current is not (installed if installed is not None else original):
                    raise ValueError('RANK_V1_BINDING_REPLACED')

    def _parent(self, frame, queue, attribute):
        self._fresh()
        name, point = self.spec['loop_module'], self.spec['loop_point']
        original = self.functions[name][point]
        if (frame.f_code is not original.__code__ or frame.f_globals is not original.__globals__):
            raise ValueError('RANK_V1_PARENT_FRAME_UNVERIFIED')
        owner = frame.f_locals.get('self', ipc.MISSING)
        cls = vars(self.modules[name])[point.split('.')[0]]
        if type(owner) is not cls or ipc._plain(owner, attribute) is not queue:
            raise ValueError('RANK_V1_QUEUE_OWNER_UNVERIFIED')
        worker, rank = ipc._plain(owner, 'worker'), ipc._plain(owner, 'rank')
        if worker is ipc.MISSING or type(rank) is not int or not 0 <= rank < 4096:
            raise ValueError('RANK_V1_WORKER_UNAVAILABLE')
        return owner, worker, rank

    def _requests(self, envelope):
        if type(envelope) is not tuple or len(envelope) != 3:
            raise ValueError('RANK_V1_RPC_ENVELOPE_UNAVAILABLE')
        method, args, kwargs = envelope
        if (type(method) is not str or method != self.spec['rpc_method']
                or type(args) is not tuple or len(args) != 1 or type(kwargs) is not dict or kwargs):
            raise ValueError('RANK_V1_RPC_ENVELOPE_UNAVAILABLE')
        value = args[0]
        layout = self.spec['request_layout']
        if request_identity.no_scheduled_work(value, layout=layout, total_field='total_num_scheduled_tokens'):
            return None
        rank_ids = request_identity.requests(value, layout=layout, key=self.key)['request_hmac_sha256']
        mapping = request_identity.member(value, layout['field'], layout['type'])
        before = request_identity._dictionary(mapping)
        fresh_ids = _native_ids(tuple(k for k, _ in before), self.key)
        if (request_identity.member(value, layout['field'], layout['type']) is not mapping
                or request_identity._dictionary(mapping) != before
                or request_identity._fingerprints(tuple(k for k, _ in before), self.key) != rank_ids):
            raise ValueError('RANK_V1_REQUEST_CHANGED')
        return value, rank_ids, fresh_ids

    def _record(self, *, point, event, context, call, started, owner, worker, queue, rank,
                rank_ids, fresh_ids, receive=None, status=None):
        name = self.spec['queue_module']
        value = {'schema': SCHEMA, 'session': self.session, 'image': self.image, 'profile': self.profile['id'],
                 'fresh_profile': self.spec['fresh_profile'], 'fresh_prepared_sha256': self.fresh_prepared_sha256,
                 'binding_key_asset_sha256': self.binding_key_asset_sha256,
                 'source_module': name, 'source_sha256': self.profile['sources'][name]['sha256'],
                 'body_code_sha256': self.profile['sources'][name]['body_code_sha256'][point],
                 'source_graph': {n: p['sha256'] for n, p in self.profile['sources'].items()},
                 'point': point, 'event': event, 'context': context, 'observation_ns': time.monotonic_ns(),
                 'operation_started_ns': started, 'call_hmac_sha256': call,
                 'parent_receive_call_hmac_sha256': receive, 'worker_rank': rank,
                 'owner_object_hmac_sha256': self.objects._name(owner, context),
                 'worker_object_hmac_sha256': self.objects._name(worker, context),
                 'queue_object_hmac_sha256': self.objects._name(queue, context),
                 'request_hmac_sha256': rank_ids, 'request_binding_hmac_sha256': fresh_ids,
                 'rpc_parent_frame_verified': True, 'rpc_parent_worker_verified': True,
                 'response_status': status, 'gaps': [], **dict.fromkeys(FALSE_FIELDS, False)}
        self.emit(value)
        return value

    def _received(self, frame, queue, result, started):
        self._fork()
        owner, worker, rank = self._parent(frame, queue, 'rpc_broadcast_mq')
        context = ipc.base._context()
        owner_name = self.objects._name(owner, context)
        self.pending.pop(owner_name, None)
        if (type(result) is tuple and len(result) == 3
                and (type(result[0]) is bytes or type(result[0]) is str and result[0] != self.spec['rpc_method'])):
            return
        selected = self._requests(result)
        if selected is None:
            self._gap(self.spec['receive_point'], 'RANK_V1_IDLE_UNBOUND')
            return
        value, rank_ids, fresh_ids = selected
        call = self._call(context)
        record = self._record(point=self.spec['receive_point'], event='worker-rpc-received', context=context,
                              call=call, started=started, owner=owner, worker=worker, queue=queue, rank=rank,
                              rank_ids=rank_ids, fresh_ids=fresh_ids)
        if len(self.pending) >= 256:
            raise ValueError('RANK_V1_PENDING_BOUND')
        # 값/원문/원 출력은 보존하지 않는다. worker/queue 수명은 객체 HMAC의 weakref로 대조한다.
        self.pending[owner_name] = {'frame_id': id(frame), 'input_id': id(value), 'call': call,
                                    'context': context, 'rank_ids': rank_ids, 'fresh_ids': fresh_ids,
                                    'rank': rank, 'worker': record['worker_object_hmac_sha256']}

    def _response(self, frame, queue, obj):
        self._fork()
        owner, worker, rank = self._parent(frame, queue, 'worker_response_mq')
        context = ipc.base._context()
        name = self.objects._name(owner, context)
        pending = self.pending.pop(name, None)
        local = frame.f_locals
        method = local.get('method')
        if type(method) is bytes or type(method) is str and method != self.spec['rpc_method']:
            return None
        selected = self._requests((method, local.get('args'), local.get('kwargs')))
        if selected is None:
            return None
        if (type(pending) is not dict or pending['frame_id'] != id(frame) or pending['context'] != context
                or pending['rank'] != rank or pending['worker'] != self.objects._name(worker, context)):
            raise ValueError('RANK_V1_RECEIVE_UNVERIFIED')
        value, rank_ids, fresh_ids = selected
        if (id(value) != pending['input_id'] or rank_ids != pending['rank_ids'] or fresh_ids != pending['fresh_ids']
                or type(obj) is not tuple or len(obj) != 2):
            raise ValueError('RANK_V1_RESPONSE_SCOPE_UNVERIFIED')
        cls = type.__getattribute__(type(owner), '__dict__').get('ResponseStatus')
        if (type(cls) is not enum.EnumType or type(obj[0]) is not cls
                or type.__getattribute__(cls, '__module__') != self.spec['loop_module']
                or type.__getattribute__(cls, '__qualname__') != type.__getattribute__(type(owner), '__qualname__') + '.ResponseStatus'):
            raise ValueError('RANK_V1_RESPONSE_STATUS_UNAVAILABLE')
        members = type.__getattribute__(cls, '__dict__')
        if obj[0] is members.get('SUCCESS'):
            status = 'success'
        elif obj[0] is members.get('FAILURE'):
            status = 'failure'
        else:
            raise ValueError('RANK_V1_RESPONSE_STATUS_UNAVAILABLE')
        return owner, worker, rank, context, pending, status

    def _gap(self, point, code='RANK_V1_BINDING_UNAVAILABLE'):
        if code not in ('RANK_V1_BINDING_UNAVAILABLE', 'RANK_V1_IDLE_UNBOUND'):
            code = 'RANK_V1_BINDING_UNAVAILABLE'
        try:
            self.emit({'schema': 'guard-rank-rpc-diagnostic/2', 'session': self.session, 'point': point,
                       'gaps': [code], **dict.fromkeys(FALSE_FIELDS, False)})
        except BaseException:
            pass

    def _wrap(self, original, point, receive):
        @functools.wraps(original)
        def observed(*args, **kwargs):
            frame = sys._getframe(1)
            loop = self.functions.get(self.spec['loop_module'], {}).get(self.spec['loop_point'])
            relevant = (loop is not None and frame.f_code is loop.__code__ and frame.f_globals is loop.__globals__)
            queue = ipc.IPCObserver._argument(original, args, kwargs, 'self')
            started, response = time.monotonic_ns(), None
            try:
                if relevant and not receive:
                    try:
                        obj = ipc.IPCObserver._argument(original, args, kwargs, 'obj')
                        response = self._response(frame, queue, obj)
                    except BaseException:
                        self._gap(point)
                result = original(*args, **kwargs)
                try:
                    if relevant and receive:
                        self._received(frame, queue, result, started)
                    elif response is not None:
                        owner, worker, rank, context, pending, status = response
                        self._fresh()
                        self._record(point=point, event='worker-response-published', context=context,
                                     call=self._call(context), started=started, owner=owner, worker=worker,
                                     queue=queue, rank=rank, rank_ids=pending['rank_ids'], fresh_ids=pending['fresh_ids'],
                                     receive=pending['call'], status=status)
                except BaseException:
                    self._gap(point)
                return result
            finally:
                frame = None
        return observed

    def attach(self, module):
        qualified = sources.qualify_current_v1(module, image=self.image)
        name, changes = vars(module)['__name__'], []
        if name not in self.profile['sources'] or name in self.modules:
            raise ValueError('RANK_V1_SOURCE_UNAVAILABLE')
        if name == self.spec['queue_module']:
            for point, receive in ((self.spec['receive_point'], True), (self.spec['publish_point'], False)):
                cls, member = point.split('.')
                owner, original = vars(module)[cls], qualified['functions'][point]
                if type.__getattribute__(owner, '__dict__').get(member) is not original:
                    raise ValueError('RANK_V1_SOURCE_UNAVAILABLE')
                changes.append((owner, member, original, self._wrap(original, point, receive), point))
        applied = []
        try:
            for owner, member, original, observed, point in changes:
                setattr(owner, member, observed)
                applied.append((owner, member, original))
                self.installed[name, point] = observed
        except BaseException:
            for owner, member, original in reversed(applied):
                setattr(owner, member, original)
            for *_, point in changes:
                self.installed.pop((name, point), None)
            raise
        self.modules[name], self.functions[name] = module, qualified['functions']
        return {'hooks_installed': len(applied), **dict.fromkeys(FALSE_FIELDS, False)}
