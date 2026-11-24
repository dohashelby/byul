"""Unregistered passive input-source observer; no engine imports or judgement.

All source pins are caller-reviewed candidate data. A synthetic test profile is
never a runtime qualification or a registration. Response/input contents are
not inspected. Missing async/thread lineage supplies no validator proof.
"""
from contextvars import ContextVar
from abc import ABCMeta
import dis,functools,hashlib,hmac,inspect,json,os,sys,threading,time,types,weakref
from . import vllm_rank_ipc_hooks as ipc,vllm_rank_ipc_sources as source


class NativeInputObserver:
    def __init__(self,profile,key,emit):
        if type(key)is not bytes or len(key)!=32 or not callable(emit)or sys.flags.optimize!=0:
            raise ValueError('NATIVE_INPUT_SETTINGS_UNAVAILABLE')
        runtime=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
        if profile['runtime']!=runtime:raise ValueError('NATIVE_INPUT_RUNTIME_UNAVAILABLE')
        self.profile,self.key,self.emit=profile,key,emit
        self.modules,self.originals,self.installed,self.owners={},{},{},{}
        self.current=ContextVar('owned_native_input_root',default=None)
        self.pid,self.serial,self.lock=os.getpid(),0,threading.RLock()

    def fingerprint(self,domain,value):
        raw=json.dumps([domain,value],sort_keys=True,separators=(',',':')).encode()
        return hmac.new(self.key,raw,hashlib.sha256).hexdigest()

    def _call(self,context):
        if self.pid!=os.getpid():
            self.pid,self.serial,self.lock=os.getpid(),0,threading.RLock()
            self.current=ContextVar('owned_native_input_root',default=None)
        with self.lock:
            self.serial+=1
            if self.serial>4096:raise ValueError('NATIVE_INPUT_CALL_BOUND')
            return self.fingerprint('call',[context,self.serial])

    @staticmethod
    def _function(module,point):
        owner,member=NativeInputObserver._function_owner(module,point)
        member=vars(owner)[member]
        if not inspect.isfunction(member):raise ValueError('NATIVE_INPUT_FUNCTION_UNAVAILABLE')
        return owner,member

    def _checked(self,module,point,original):
        name=vars(module)['__name__'];pin=self.profile['sources'][name]
        owner,_=self._function_owner(module,point)
        if self.owners.get((name,point))is not owner:raise ValueError('NATIVE_INPUT_OWNER_CHANGED')
        if (not inspect.isfunction(original)or original.__globals__ is not vars(module)or original.__qualname__!=point
                or source.code_digest(source.relative_code(original.__code__,pin['path']))!=pin['body_code_sha256'][point]):
            raise ValueError('NATIVE_INPUT_SOURCE_UNAVAILABLE')
        if original.__code__.co_freevars:
            if (original.__code__.co_freevars!=('__class__',)or original.__closure__ is None
                    or len(original.__closure__)!=1 or original.__closure__[0].cell_contents is not owner):
                raise ValueError('NATIVE_INPUT_CLASS_CELL_UNAVAILABLE')
        elif original.__closure__ is not None:raise ValueError('NATIVE_INPUT_CLASS_CELL_UNAVAILABLE')

    @staticmethod
    def _function_owner(module,point):
        parts=point.split('.')
        if len(parts)==1:return module,parts[0]
        if len(parts)!=2:raise ValueError('NATIVE_INPUT_OWNER_UNAVAILABLE')
        cls,member=parts;owner=vars(module)[cls]
        if type(owner)not in(type,ABCMeta):raise ValueError('NATIVE_INPUT_OWNER_UNAVAILABLE')
        return owner,member

    def _fresh(self,names):
        for name in names:
            module=self.modules[name];pin=self.profile['sources'][name]
            if sys.modules.get(name)is not module:raise ValueError('NATIVE_INPUT_MODULE_CHANGED')
            source._bytes(vars(module)['__file__'],pin)
            for point in pin['functions']:
                original=self.originals[name,point];self._checked(module,point,original)
                owner,member=self._function_owner(module,point)
                if vars(owner)[member]is not self.installed.get((name,point),original):
                    raise ValueError('NATIVE_INPUT_BINDING_CHANGED')

    def _root(self,original,args,kwargs,spec):
        argument=spec.get('scope_owner_argument',spec.get('raw_request_argument'))
        if type(argument)is not str:raise ValueError('NATIVE_INPUT_HTTP_SCOPE_UNAVAILABLE')
        raw=ipc.IPCObserver._argument(original,args,kwargs,argument)
        scope=ipc._plain(raw,'scope')
        if type(scope)is dict and(type(scope.get('method'))is str and type(scope.get('path'))is str
                and(scope['method']!='POST'or scope['path']not in spec['paths'])):
            return None
        if (type(scope)is not dict or scope.get('type')!='http'or scope.get('method')!='POST'
                or scope.get('path')not in spec['paths']or type(scope.get('headers'))is not list or len(scope['headers'])>128):
            raise ValueError('NATIVE_INPUT_HTTP_SCOPE_UNAVAILABLE')
        ids=[]
        for pair in scope['headers']:
            if type(pair)not in(tuple,list)or len(pair)!=2 or type(pair[0])is not bytes:
                raise ValueError('NATIVE_INPUT_HTTP_SCOPE_UNAVAILABLE')
            if pair[0].lower()==b'x-request-id':
                value=pair[1]
                if type(value)is not bytes or not 1<=len(value)<=256 or any(c<33 or c>126 for c in value):
                    raise ValueError('NATIVE_INPUT_REQUEST_ID_UNAVAILABLE')
                ids.append(value.decode('ascii'))
        if len(ids)!=1:raise ValueError('NATIVE_INPUT_REQUEST_ID_UNAVAILABLE')
        return {'method':'POST','path':scope['path'],'request_hmac_sha256':self.fingerprint('request',ids[0])}

    def _row(self,name,point,owner,event,call,started,root,*,exception_kind=None,exception_site=None,original_invoked=False):
        if root['active']is not True or root['pid']!=os.getpid():raise ValueError('NATIVE_INPUT_ROOT_INACTIVE')
        self._fresh([name,*root['source_modules']])
        context=ipc.base._context()
        row={'schema':'guard-native-input-hook-candidate/1','event':event,'point':point,'source_module':name,
            'source_sha256':self.profile['sources'][name]['sha256'],'body_code_sha256':self.profile['sources'][name]['body_code_sha256'][point],
            'context':context,'call_hmac_sha256':call,'root_call_hmac_sha256':root['call'],
            'parent_root_call_hmac_sha256':root.get('parent_call'),
            'object_hmac_sha256':self.fingerprint('object',[context,id(owner)]),'request_hmac_sha256':root['request_hmac_sha256'],
            'method':root['method'],'path':root['path'],'operation_started_ns':started,'observation_ns':time.monotonic_ns(),
            'exception_kind':exception_kind,'exception_site':exception_site,'original_invoked':original_invoked,'source_authenticated':False,
            'producer_registered':False,'scope_complete':False}
        self.emit(row);return row

    def _gap(self):
        try:self.emit({'schema':'guard-native-input-hook-gap/1','diagnostic':'NATIVE_INPUT_PROOF_UNAVAILABLE',
            'source_authenticated':False,'producer_registered':False,'scope_complete':False})
        except BaseException:pass

    def _validation_error(self,error):
        selected=self.profile['validation_exception'];name=selected['module']
        self._fresh([name]);module=self.modules[name]
        return 'native-validation-error'if type(error)is vars(module)[selected['class']]else'other-error'

    @staticmethod
    def _exception_site(error,original):
        # Read interpreter-owned traceback slots directly. Never access the
        # engine exception's args, text, parameters or custom attributes.
        trace=vars(BaseException)['__traceback__'].__get__(error,type(error));matches=[];count=0
        while trace is not None:
            if type(trace)is not types.TracebackType or count>=64:raise ValueError('NATIVE_INPUT_TRACE_BOUND')
            if trace.tb_frame.f_code is original.__code__ and trace.tb_frame.f_globals is original.__globals__:
                matches.append((trace.tb_lasti,trace.tb_lineno))
            trace=trace.tb_next;count+=1
        if len(matches)!=1:return None
        offset,line=matches[0]
        instruction=next((i for i in dis.get_instructions(original.__code__,show_caches=True)if i.offset==offset),None)
        if instruction is None:raise ValueError('NATIVE_INPUT_TRACE_UNAVAILABLE')
        return {'code_offset':offset,'source_line':line,'direct_raise':instruction.opname=='RAISE_VARARGS'}

    def _wrap_handler(self,name,point,original,spec):
        @functools.wraps(original)
        async def wrapped(*args,**kwargs):
            token=None;root=None;owner=None;started=time.monotonic_ns()
            try:
                owner=ipc.IPCObserver._argument(original,args,kwargs,'self');context=ipc.base._context()
                cls,_=self._function_owner(self.modules[name],point)
                if type(owner)is not cls:raise ValueError('NATIVE_INPUT_OWNER_UNAVAILABLE')
                selected=self._root(original,args,kwargs,spec)
                if selected is None:token=self.current.set(None)
                else:
                    root={**selected,'call':self._call(context),'active':True,'owner':weakref.ref(owner),
                        'source_modules':spec.get('source_modules',[name]),'context':context,'pid':os.getpid()}
                    parent=self.current.get()
                    if parent is not None:
                        if (parent['active']is not True or parent['pid']!=root['pid']or parent['context']!=context
                                or any(root[k]!=parent[k]for k in('request_hmac_sha256','method','path'))):
                            raise ValueError('NATIVE_INPUT_PARENT_ROOT_UNAVAILABLE')
                        root['parent_call']=parent['call'];root['source_modules']=list(dict.fromkeys([*parent['source_modules'],*root['source_modules']]))
                    self._row(name,point,owner,'CALL',root['call'],started,root)
                    token=self.current.set(root)
            except BaseException:
                root=None
                try:token=self.current.set(None)
                except BaseException:
                    parent=self.current.get()
                    if type(parent)is dict:parent['active']=False
                self._gap()
            try:
                result=await original(*args,**kwargs)
            except BaseException:
                if root is not None:
                    try:self._row(name,point,owner,'RAISE',root['call'],started,root,original_invoked=True)
                    except BaseException:self._gap()
                raise
            else:
                if root is not None:
                    try:self._row(name,point,owner,'RETURN',root['call'],started,root,original_invoked=True)
                    except BaseException:self._gap()
                return result
            finally:
                if root is not None:root['active']=False
                if token is not None:
                    try:self.current.reset(token)
                    except BaseException:self._gap()
        return wrapped

    def _wrap_validator(self,name,point,original,spec):
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            root=self.current.get();owner=None;call=None;started=time.monotonic_ns();bound=False
            if root is not None:
                try:
                    if root['active']is not True or root['pid']!=os.getpid()or root['owner']()is None:
                        raise ValueError('NATIVE_INPUT_ROOT_INACTIVE')
                    owner=ipc.IPCObserver._argument(original,args,kwargs,'self');cls,_=self._function_owner(self.modules[name],point)
                    if type(owner)is not cls:raise ValueError('NATIVE_INPUT_OWNER_UNAVAILABLE')
                    call=self._call(ipc.base._context());self._row(name,point,owner,'CALL',call,started,root);bound=True
                except BaseException:self._gap()
            try:result=original(*args,**kwargs)
            except BaseException as error:
                if bound:
                    try:
                        kind=self._validation_error(error)
                        site=self._exception_site(error,original)if kind=='native-validation-error'else None
                        self._row(name,point,owner,'RAISE',call,started,root,exception_kind=kind,exception_site=site,original_invoked=True)
                    except BaseException:self._gap()
                raise
            else:
                if bound:
                    try:self._row(name,point,owner,'RETURN',call,started,root,original_invoked=True)
                    except BaseException:self._gap()
                return result
        return wrapped

    def attach(self,module):
        name=vars(module)['__name__'];pin=self.profile['sources'][name]
        source._bytes(vars(module)['__file__'],pin)
        changes=[]
        for point in pin['functions']:
            cls,original=self._function(module,point);self.owners[name,point]=cls;self._checked(module,point,original)
            self.originals[name,point]=original
            spec=pin.get('hooks',{}).get(point)
            if spec is None:continue
            if (spec['kind']=='http-handler'and inspect.iscoroutinefunction(original)
                    and not inspect.isasyncgenfunction(original)):
                wrapper=self._wrap_handler(name,point,original,spec)
            elif spec['kind']=='input-validator'and not inspect.iscoroutinefunction(original)and not inspect.isasyncgenfunction(original):
                wrapper=self._wrap_validator(name,point,original,spec)
            else:raise ValueError('NATIVE_INPUT_HOOK_UNAVAILABLE')
            changes.append((cls,point.split('.')[-1],original,wrapper,point))
        self.modules[name]=module;applied=[]
        try:
            for cls,member,original,wrapper,point in changes:
                setattr(cls,member,wrapper);applied.append((cls,member,original));self.installed[name,point]=wrapper
        except BaseException:
            for cls,member,original in reversed(applied):setattr(cls,member,original)
            self.modules.pop(name,None)
            for _,_,_,_,point in changes:self.installed.pop((name,point),None)
            raise
