"""정확 RPC 안의 동일 요청·worker 연결을 읽는 미등록 후보.

원 함수/예외를 보존하고 초기 IPC 관측과 같은 객체 HMAC을 사용한다.
이 구성요소 자체는 장애를 주입하거나 생산자를 등록하지 않는다.
"""
from contextvars import ContextVar
import functools
from hashlib import sha256
import hmac
import inspect
import json
import os
import sys
import threading
import time
import types
import weakref

from . import vllm_rank_ipc_hooks as ipc
from . import vllm_rank_ipc_sources as sources
from . import vllm_rank_request_identity as request_identity
from . import vllm_rank_rpc_control as control_code


class RPCObserver:
    def __init__(self, *, image, key, emit, identity_observer):
        self.image, self.key, self.emit = image, key, emit
        self.identity_observer = identity_observer
        self.profile = identity_observer.profile
        self.sources = self.profile.get('rpc_sources', {})
        self.modules, self.functions, self.installed = {}, {}, {}
        self.control = None
        self.current = ContextVar('guard_rank_rpc_candidate', default=None)
        self.pid, self.serial, self.lock = os.getpid(), 0, threading.RLock()
        self.deferred = {}

    def _call(self, context):
        if self.pid != os.getpid():
            self.pid, self.serial, self.lock = os.getpid(), 0, threading.RLock()
            self.deferred = {}
        with self.lock:
            self.serial += 1
            if self.serial > 4096:
                raise ValueError('RANK_RPC_CALL_BOUND')
            raw = json.dumps(['rpc-call', context, self.serial], sort_keys=True, separators=(',', ':')).encode()
            return hmac.new(self.key, raw, sha256).hexdigest()

    @staticmethod
    def _class_cell(original,module,point):
        if original.__code__.co_freevars:
            cells=original.__closure__
            if (original.__code__.co_freevars!=('__class__',)or cells is None or len(cells)!=1
                    or cells[0].cell_contents is not vars(module)[point.split('.')[0]]):
                raise ValueError('RANK_RPC_CLASS_CELL_UNVERIFIED')
        elif original.__closure__ is not None:raise ValueError('RANK_RPC_CLASS_CELL_UNVERIFIED')

    def _defer(self,module_name,point,spec,owner,result,returned):
        target=spec.get('deferred_result')
        if target is None:return
        module=self.modules[module_name];future_type=vars(module).get(target['class'])
        if type(future_type)is not type or type(result)is not future_type:return
        self._fresh(spec['source_modules'])
        with self.lock:
            stale=[key for key,row in self.deferred.items()if row[0]()is None or row[1]()is None]
            for key in stale:del self.deferred[key]
            if len(self.deferred)>=256:raise ValueError('RANK_RPC_DEFERRED_BOUND')
            key=id(result)
            if key in self.deferred and self.deferred[key][0]()is result:
                raise ValueError('RANK_RPC_DEFERRED_DUPLICATE_ORIGIN')
            context=returned['context']
            binding={'schema':'guard-rank-deferred-request-binding/1','source_module':module_name,'point':point,
                'origin_call_hmac_sha256':returned['call_hmac_sha256'],
                'origin_object_hmac_sha256':returned['object_hmac_sha256'],
                'origin_started_ns':returned['operation_started_ns'],'origin_returned_ns':returned['observation_ns'],
                'future_object_hmac_sha256':self.identity_observer._name(result,context)}
            self.deferred[key]=(weakref.ref(result),weakref.ref(owner),binding,context,list(returned['request_hmac_sha256']))

    def _deferred(self,module_name,point,owner,context):
        if self.pid!=os.getpid():
            self.pid,self.serial,self.lock,self.deferred=os.getpid(),0,threading.RLock(),{}
            return None
        with self.lock:
            row=self.deferred.get(id(owner))
            if row is None or row[0]()is not owner:return None
            origin_owner=row[1]()
            if origin_owner is None or row[3]!=context:raise ValueError('RANK_RPC_DEFERRED_ORIGIN_UNAVAILABLE')
            binding=row[2];origin_spec=self.sources[module_name]['hooks'][binding['point']]
            target=origin_spec.get('deferred_result')
            if (binding['source_module']!=module_name or target!={'class':point.split('.')[0],'point':point}
                    or type(owner)is not vars(self.modules[module_name])[target['class']]
                    or self.identity_observer._name(owner,context)!=binding['future_object_hmac_sha256']
                    or self.identity_observer._name(origin_owner,context)!=binding['origin_object_hmac_sha256']):
                raise ValueError('RANK_RPC_DEFERRED_ORIGIN_UNVERIFIED')
            self._fresh(origin_spec['source_modules'])
            return dict(binding),list(row[4])

    def _fresh(self, required):
        if not set(required) <= set(self.modules):
            raise ValueError('RANK_RPC_SOURCE_GRAPH_INCOMPLETE')
        for name in required:
            module, expected = self.modules[name], self.sources[name]
            sources._bytes(vars(module)['__file__'], expected)
            for point, original in self.functions[name].items():
                self._class_cell(original,module,point)
                if (original.__globals__ is not vars(module)
                        or original.__qualname__ != point
                        or sources.code_digest(sources.relative_code(original.__code__, expected['path']))
                           != expected['body_code_sha256'][point]):
                    raise ValueError('RANK_RPC_SOURCE_DRIFT')
                binding = self.installed.get((name, point))
                if binding is not None and vars(binding[0]).get(binding[1]) is not binding[2]:
                    raise ValueError('RANK_RPC_HOOK_REPLACED')

    @staticmethod
    def _root(original, args, kwargs, selector):
        value = ipc.IPCObserver._argument(original, args, kwargs, selector['root'])
        for index in selector.get('indexes', []):
            if type(index) is not int or type(value) is not tuple or not 0 <= index < len(value):
                return ipc.MISSING
            value = value[index]
        return value

    def _requests(self, original, args, kwargs, spec):
        if 'rpc_envelope' in spec:
            envelope = self._root(original, args, kwargs, spec['rpc_envelope'])
            if type(envelope) is not tuple or len(envelope) != 4:
                return None
            method, rpc_args, rpc_kwargs, output_rank = envelope
            if type(method) is not str or method != spec['rpc_method']:
                return None
            if (type(rpc_args) is not tuple or len(rpc_args) != 1
                    or type(rpc_kwargs) is not dict or rpc_kwargs
                    or not (output_rank is None or type(output_rank) is int and output_rank == 0)):
                raise ValueError('RANK_RPC_REQUEST_UNAVAILABLE')
        value = self._root(original, args, kwargs, spec['requests'])
        if value is None:
            return None
        layout = self.profile['request_layouts'][spec['requests']['layout']]
        no_work = spec.get('no_scheduled_work')
        if no_work is not None:
            if type(no_work) is not dict or set(no_work) != {'total_field'}:
                raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            if request_identity.no_scheduled_work(value, layout=layout, total_field=no_work['total_field']):
                return ipc.MISSING
        return request_identity.requests(value, layout=layout, key=self.key)['request_hmac_sha256']

    @staticmethod
    def _parent_frame(parent):
        frame = sys._getframe(1)
        try:
            for _ in range(20):
                if frame is None:
                    return False
                if (frame.f_code is parent['original'].__code__
                        and frame.f_globals is parent['original'].__globals__
                        and frame.f_locals.get('self') is parent['owner']):
                    return True
                frame = frame.f_back
            return False
        finally:
            frame = None

    def _metadata(self, module_name, point, spec, context, owner, ids, call, event, started,
                  *, parent=None, result=ipc.MISSING, original_invoked=False, idle=False,
                  deferred=None,exception_kind=None,http_send=None):
        self._fresh(spec['source_modules'])
        expected = self.sources[module_name]
        value = {'schema': 'guard-rank-rpc-hook-candidate/1', 'image': self.image,
                 'profile': self.profile['id'], 'source_module': module_name,
                 'source_sha256': expected['sha256'], 'body_code_sha256': expected['body_code_sha256'][point],
                 'point': point, 'phase': spec['phase'], 'role': spec['role'], 'event': event,
                 'context': context, 'call_hmac_sha256': call,
                 'operation_started_ns': started, 'observation_ns': time.monotonic_ns(),
                 'request_hmac_sha256': ids, 'object_hmac_sha256': self.identity_observer._name(owner, context),
                 'source_graph': {name: self.sources[name]['sha256'] for name in spec['source_modules']},
                 'rpc_parent_call_hmac_sha256': None, 'rpc_parent_frame_verified': False,
                 'rpc_parent_object_hmac_sha256': None, 'original_invoked': original_invoked,
                 'rpc_parent_worker_verified': False, 'source_authenticated': False,
                 'engine_group_verified': False, 'producer_registered': False, 'gaps': []}
        parent_spec = spec.get('parent')
        if idle:
            value['idle_basis'] = {'empty_scheduled_requests': True, 'total_scheduled_tokens': 0,
                                   'request_bound': False, 'fault_eligible': False}
        if parent_spec is not None and not idle:
            valid = (type(parent) is dict and parent['point'] == parent_spec['point']
                     and parent['context'] == context and parent['ids'] == ids
                     and ipc._path(parent['owner'], parent_spec['worker_path']) is owner
                     and self._parent_frame(parent))
            if valid:
                self._fresh(parent['source_modules'])
                value.update(rpc_parent_call_hmac_sha256=parent['call'],
                             rpc_parent_object_hmac_sha256=self.identity_observer._name(parent['owner'], context),
                             rpc_parent_frame_verified=True, rpc_parent_worker_verified=True)
            else:
                value['gaps'].append('RANK_RPC_ACTIVE_PARENT_UNVERIFIED')
        fields = self.identity_observer._fields(spec, {'self': owner,
            'rpc_parent': parent['owner'] if value['rpc_parent_frame_verified'] else ipc.MISSING}, context, value['gaps'])
        value.update(fields)
        if spec['kind']=='deferred-result':
            if deferred is None:raise ValueError('RANK_RPC_DEFERRED_ORIGIN_UNAVAILABLE')
            value.update(deferred_rpc=dict(deferred),exception_kind=exception_kind)
        if spec['kind']=='asgi-send':
            if http_send is None:raise ValueError('RANK_HTTP_SEND_UNAVAILABLE')
            value['http_send']=dict(http_send)
        if spec.get('kind') == 'assignment' and event == 'RETURN':
            value['assignment'] = request_identity.assignment(
                result, expected_type=self.profile['assignment_type'], key=self.key)
        elif spec.get('kind') == 'external-id' and event == 'RETURN':
            value['external_request_hmac_sha256'] = request_identity._fingerprints([result], self.key)[0]
        self.emit(value)
        return value

    def _gap(self, point, error, stage):
        # 관측 실패의 원문/사용자 예외를 직렬화하지 않고 고정 진단 코드만 남긴다.
        code = 'RANK_RPC_OBSERVATION_ERROR'
        if type(error) is ValueError and len(error.args) == 1:
            value = error.args[0]
            if (type(value) is str and value.startswith('RANK_') and len(value) <= 80
                    and all(c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789' for c in value)):
                code = value
        try:
            self.emit({'schema': 'guard-rank-rpc-hook-candidate/1', 'point': point,
                       'source_authenticated': False, 'engine_group_verified': False,
                       'producer_registered': False, 'gaps': ['RANK_RPC_CAPTURE_UNAVAILABLE'],
                       'diagnostic_code': code, 'diagnostic_stage': stage})
        except BaseException:
            pass

    def _http_send(self,original,args,kwargs,spec):
        owner=ipc.IPCObserver._argument(original,args,kwargs,'self')
        scope=ipc._path(owner,['scope'])
        if type(scope)is not dict:raise ValueError('RANK_HTTP_SCOPE_UNAVAILABLE')
        if any(type(scope.get(k))is not str for k in('type','method','path')):raise ValueError('RANK_HTTP_SCOPE_UNAVAILABLE')
        if (scope.get('type')!='http'or scope.get('method')!='POST'
                or scope.get('path')not in spec['paths']):return None
        headers=scope.get('headers')
        if type(headers)not in(list,tuple)or len(headers)>64:raise ValueError('RANK_HTTP_HEADER_UNAVAILABLE')
        ids=[]
        for pair in headers:
            if (type(pair)not in(list,tuple)or len(pair)!=2 or type(pair[0])is not bytes or len(pair[0])>256):
                raise ValueError('RANK_HTTP_HEADER_UNAVAILABLE')
            if pair[0].lower()==b'x-request-id':ids.append(pair[1])
        if not ids:return None
        if len(ids)!=1 or type(ids[0])is not bytes or not 1<=len(ids[0])<=512:raise ValueError('RANK_HTTP_HEADER_UNAVAILABLE')
        try:request=request_identity._fingerprints([ids[0].decode('ascii')],self.key)[0]
        except UnicodeError:raise ValueError('RANK_HTTP_HEADER_UNAVAILABLE')from None
        message=ipc.IPCObserver._argument(original,args,kwargs,'message')
        if type(message)is not dict:raise ValueError('RANK_HTTP_MESSAGE_UNAVAILABLE')
        kind=message.get('type')
        if type(kind)is not str:raise ValueError('RANK_HTTP_MESSAGE_UNAVAILABLE')
        if kind not in('http.response.start','http.response.body'):return None
        status=message.get('status')if kind=='http.response.start'else None
        more=message.get('more_body',False)if kind=='http.response.body'else None
        if (kind=='http.response.start'and(type(status)is not int or not 100<=status<=599)
                or kind=='http.response.body'and type(more)is not bool):raise ValueError('RANK_HTTP_MESSAGE_UNAVAILABLE')
        return owner,request,{'schema':'guard-rank-native-http-send/1','type':kind,
            'method':'POST','path':scope['path'],'status':status,'more_body':more,'external_request_hmac_sha256':request}

    def _wrap_http(self,module_name,point,original,spec):
        @functools.wraps(original)
        async def observed(*args,**kwargs):
            bound=None;context=None;call=None;started=time.monotonic_ns()
            try:
                candidate=ipc.IPCObserver._argument(original,args,kwargs,'self')
                if type(candidate)is not vars(self.modules[module_name])[point.split('.')[0]]:
                    raise ValueError('RANK_HTTP_OWNER_UNAVAILABLE')
                bound=self._http_send(original,args,kwargs,spec)
                if bound is not None:
                    owner,request,metadata=bound;context=ipc.base._context();call=self._call(context)
                    self._metadata(module_name,point,spec,context,owner,[request],call,'CALL',started,http_send=metadata)
            except BaseException as error:
                bound=None;self._gap(point,error,'before-original')
            try:
                result=await original(*args,**kwargs)
            except BaseException:
                if bound is not None:
                    try:self._metadata(module_name,point,spec,context,owner,[request],call,'RAISE',started,
                        original_invoked=True,http_send=metadata)
                    except BaseException as error:self._gap(point,error,'after-exception')
                raise
            else:
                if bound is not None:
                    try:
                        after=self._http_send(original,args,kwargs,spec)
                        if after is None or after[0]is not owner or after[1:]!=bound[1:]:
                            raise ValueError('RANK_HTTP_MESSAGE_CHANGED')
                        self._metadata(module_name,point,spec,context,owner,[request],call,'RETURN',started,
                            original_invoked=True,http_send=metadata)
                    except BaseException as error:self._gap(point,error,'after-return')
                return result
        return observed

    def _wrap(self, module_name, point, original, spec):
        if spec['kind']=='asgi-send':return self._wrap_http(module_name,point,original,spec)
        @functools.wraps(original)
        def observed(*args, **kwargs):
            owner = ipc.IPCObserver._argument(original, args, kwargs, 'self')
            context, call, ids, token, parent, started = None, None, [], None, None, time.monotonic_ns()
            armed, proof, original_invoked, idle = False, None, False, False
            deferred=None
            try:
                context = ipc.base._context()
                if spec['kind']=='deferred-result':
                    bound=self._deferred(module_name,point,owner,context)
                    if bound is not None:
                        deferred,ids=bound;call=self._call(context)
                        proof=self._metadata(module_name,point,spec,context,owner,ids,call,'CALL',started,deferred=deferred)
                        armed=True
                elif spec['kind'] == 'rpc-reply':
                    # Response publication receives a model output. Never inspect
                    # that argument: inherit only the parent's already computed
                    # request fingerprints inside the same original RPC frame.
                    parent = self.current.get()
                    # The same queue carries unrelated startup/RPC traffic.
                    # Fall through to the one original invocation below.
                    if type(parent) is dict and parent['point'] == spec['parent']['point']:
                        if (parent['context'] != context
                                or ipc._path(parent['owner'],spec['parent']['worker_path']) is not owner
                                or not self._parent_frame(parent)):
                            raise ValueError('RANK_RPC_REPLY_PARENT_UNVERIFIED')
                        sync_owner = owner if spec.get('synchronous_false_root','self') == 'self' else parent['owner']
                        if ipc._path(sync_owner, spec['synchronous_false_path']) is not False:
                            raise ValueError('RANK_RPC_REPLY_ASYNC_UNSUPPORTED')
                        ids = list(parent['ids'])
                        if not ids:
                            raise ValueError('RANK_RPC_REPLY_REQUEST_UNAVAILABLE')
                        call = self._call(context)
                        proof = self._metadata(module_name, point, spec, context, owner, ids, call,
                            'CALL', started, parent=parent)
                        token = self.current.set({'point': point, 'original': original, 'owner': owner,
                            'context': context, 'ids': ids, 'call': call, 'source_modules': spec['source_modules']})
                        armed = True
                elif spec['kind'] == 'rpc':
                    ids = self._requests(original, args, kwargs, spec)
                    if ids is ipc.MISSING:
                        ids, idle, call = [], True, self._call(context)
                        self._metadata(module_name, point, spec, context, owner, ids, call,
                                       'IDLE_CALL', started, idle=True)
                    if ids is not None:
                        if not idle:
                            call, parent = self._call(context), self.current.get()
                            proof = self._metadata(module_name, point, spec, context, owner, ids, call, 'CALL', started, parent=parent)
                            token = self.current.set({'point': point, 'original': original, 'owner': owner,
                                'context': context, 'ids': ids, 'call': call, 'source_modules': spec['source_modules']})
                            armed = True
                else:
                    call, armed = self._call(context), True
            except BaseException as error:
                self._gap(point, error, 'before-original')
            try:
                if armed and self.control is not None and spec.get('fault_eligible') is True:
                    try:
                        self.control.maybe_hold(proof)
                    except control_code.NativeLabFault:
                        raise
                    except BaseException as error:
                        self._gap(point, error, 'control')
                original_invoked = True
                result = original(*args, **kwargs)
            except BaseException as error:
                if armed or idle:
                    try:
                        event = 'IDLE_RAISE' if idle else 'CONTROL_RAISE' if not original_invoked and type(error) is control_code.NativeLabFault else 'RAISE'
                        self._metadata(module_name, point, spec, context, owner, ids, call, event, started,
                                       parent=parent, original_invoked=original_invoked, idle=idle,deferred=deferred,
                                       exception_kind=('timeout'if type(error)is TimeoutError else'runtime-error'if type(error)is RuntimeError else'other')
                                           if spec['kind']=='deferred-result'else None)
                    except BaseException as observation_error:
                        self._gap(point, observation_error, 'after-exception')
                raise
            else:
                if armed or idle:
                    try:
                        selected = result
                        if spec['kind'] == 'assignment':
                            selected = self._root(original, args, kwargs, spec['assignment'])
                        returned=self._metadata(module_name, point, spec, context, owner, ids, call, 'IDLE_RETURN' if idle else 'RETURN', started,
                                       parent=parent, result=selected, original_invoked=True, idle=idle,deferred=deferred)
                        if armed:self._defer(module_name,point,spec,owner,result,returned)
                    except BaseException as error:
                        self._gap(point, error, 'after-return')
                return result
            finally:
                if token is not None:
                    self.current.reset(token)
        return observed

    def attach(self, module):
        qualified = sources.qualify(module, image=self.image, scope='rpc_sources')
        name = vars(module)['__name__']
        expected, changes = self.sources[name], []
        for point, spec in expected.get('hooks', {}).items():
            if spec['kind'] not in {'rpc', 'rpc-reply', 'assignment', 'external-id','deferred-result','asgi-send'} or spec['phase'] != 'around-original':
                raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            if spec['kind']=='asgi-send'and(type(spec.get('paths'))is not list or not 1<=len(spec['paths'])<=8
                    or any(type(p)is not str or not p.startswith('/v1/')or len(p)>128 for p in spec['paths'])or len(set(spec['paths']))!=len(spec['paths'])
                    or 'requests'in spec or 'parent'in spec or spec.get('fault_eligible')is True):raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            if 'deferred_result'in spec:
                target=spec['deferred_result']
                if (spec['kind']!='rpc'or type(target)is not dict or set(target)!={'class','point'}
                        or type(target['class'])is not str or not target['class']or target['point']!=target['class']+'.result'
                        or expected['hooks'].get(target['point'],{}).get('kind')!='deferred-result'):
                    raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            if spec['kind']=='deferred-result'and('requests'in spec or 'parent'in spec or spec.get('fault_eligible')is True):
                raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            if spec['kind'] == 'rpc-reply' and (type(spec.get('parent', {}).get('worker_path')) is not list
                    or len(spec['parent']['worker_path'])>8
                    or any(type(part) is not str or not part for part in spec['parent']['worker_path'])
                    or type(spec.get('synchronous_false_path')) is not list or not spec['synchronous_false_path']
                    or any(type(part) is not str or not part for part in spec['synchronous_false_path'])
                    or spec.get('synchronous_false_root','self') not in {'self','rpc_parent'}
                    or 'requests' in spec or 'rpc_envelope' in spec):
                raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            original = qualified['functions'][point]
            if inspect.iscoroutinefunction(original)!=(spec['kind']=='asgi-send')or inspect.isasyncgenfunction(original):
                raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            self._class_cell(original,module,point)
            parts = point.split('.')
            owner = module if len(parts) == 1 else vars(module)[parts[0]]
            member = vars(owner)[parts[-1]]
            wrapped = self._wrap(name, point, original, spec)
            replacement = staticmethod(wrapped) if type(member) is staticmethod else classmethod(wrapped) if type(member) is classmethod else wrapped
            changes.append((owner, parts[-1], member, replacement, point))
        applied = []
        try:
            for owner, attribute, member, replacement, point in changes:
                setattr(owner, attribute, replacement)
                applied.append((owner, attribute, member))
                self.installed[name, point] = owner, attribute, replacement
        except Exception:
            for owner, attribute, member in reversed(applied):
                setattr(owner, attribute, member)
            raise
        self.modules[name], self.functions[name] = module, qualified['functions']
        return {'hooks_installed': len(applied), 'engine_group_verified': False, 'producer_registered': False}
