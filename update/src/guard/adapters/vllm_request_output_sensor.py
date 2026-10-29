"""Explicit /11 collector/output observations over immutable /10 and /8 assets.

Collector identity and native request identity must be observed together.
An external RequestOutput.request_id never supplies a native attempt ID.
No engine import, hook installation, generator consumption or output retention
occurs on module import.
"""
from __future__ import annotations
import hashlib
import os
import re
import threading
import weakref
import functools
import inspect
import stat
import sys
import types

PROFILE='vllm-native-lifecycle-metadata/11'
SCHEMA='guard-native-lifecycle-events/11'
OUTPUT_SCHEMA='guard-native-request-output-link/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
MODULE='vllm.v1.engine.output_processor'
SOURCE_SHA256='c72d0149e18d6da71d53e23b6550d09310af6917523fef6e282b0907d36b8d54'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
INVOCATION10_SHA256='071763bfd2553ea20986c0b9a946d4017d167ea4d6cc06f25363bc6cf4abbca3'
MAX_IDENTITIES=4096
MAX_OUTPUTS=8
TEXT_HASH_LIMIT=65536
# Filled from completed, fixed own-source offline code review before admission.
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14':'4a9b7ce7ec7b106061d6579caaf7f11e8df8ced600f24f17001d4e692868f610','cpython-3.12.3':'e1876cc5a37e78b95a533bef0f5cc197170505f7242ced14311df49f27b0f15a'}
CODE_FILENAME='guard/adapters/vllm_request_output_sensor.py'
MAX_ROWS=2048
MAX_BYTES=2*1024**2
_BASE=None
POINTS=frozenset({'OutputProcessor.add_request','RequestState.make_request_output',
    'RequestOutputCollector.put','RequestOutputCollector.get','RequestOutputCollector.get_nowait','RequestOutputCollector.close'})


def attributes(value):
    try:
        result=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):result={}
    result=result if type(result) is dict else {}
    fields={}
    for name in ('request_id','external_req_id','request_states','queue','output','outputs','text'):
        if name in result:fields[name]=result[name];continue
        descriptor=inspect.getattr_static(type(value),name,None)
        if type(descriptor) is types.MemberDescriptorType:
            try:fields[name]=descriptor.__get__(value,type(value))
            except (AttributeError,TypeError):pass
    return fields


def string_sha(value):
    if type(value) is not str or not 0<len(value)<=512:return None
    return hashlib.sha256(value.encode('utf-8','surrogatepass')).hexdigest()


class WeakIdentities:
    """Bounded process-scoped nonces, with no strong reference to native outputs.

    The weak reference's identity is checked on lookup and expiry. A reused
    address or inherited parent registry cannot resurrect an earlier identity.
    These are opaque observation nonces, not file/content or approval hashes.
    """
    def __init__(self,nonce,*,pid=None):
        self.nonce=nonce;self.pid=pid or os.getpid;self.owner=self.pid();self.generation=0
        self.lock=threading.RLock();self.entries={}

    def identify(self,value,kind):
        if value is None or type(kind) is not str or kind not in {'collector','output','state'}:return None
        current=self.pid()
        # A child must not acquire a lock held by a vanished parent thread.
        if current!=self.owner:
            self.lock=threading.RLock();self.entries={};self.owner=current;self.generation+=1
        with self.lock:
            key=(kind,id(value));entry=self.entries.get(key)
            if entry is not None and entry[0]() is value:return entry[1]
            if entry is not None:self.entries.pop(key,None)
            if len(self.entries)>=MAX_IDENTITIES:return None
            generation=self.generation
            def expired(ref):
                with self.lock:
                    previous=self.entries.get(key)
                    if self.generation==generation and previous is not None and previous[0] is ref:
                        self.entries.pop(key,None)
            try:ref=weakref.ref(value,expired)
            except TypeError:return None
            token=self.nonce('request-output-'+kind)
            if type(token) is not str or re.fullmatch(r'[0-9a-f]{64}',token) is None:return None
            self.entries[key]=(ref,token)
            return token


def text_samples(value):
    """Only bounded generation text hashes; never token/logprob/raw output data."""
    values=attributes(value).get('outputs')
    if type(values) is not list:return {'output_count':None,'outputs_complete':False,'items':[]}
    items=[];complete=0<len(values)<=MAX_OUTPUTS
    for index,output in enumerate(values[:MAX_OUTPUTS]):
        text=attributes(output).get('text');chars=len(text) if type(text) is str else None
        raw=text.encode('utf-8','surrogatepass') if chars is not None and chars<=TEXT_HASH_LIMIT else None
        hashed=raw is not None and len(raw)<=TEXT_HASH_LIMIT
        items.append({'index':index,'text_chars':chars,'text_bytes':len(raw) if raw is not None else None,
            'text_sha256':hashlib.sha256(raw).hexdigest() if hashed else None,'text_hash_complete':hashed})
        complete=complete and hashed
    return {'output_count':len(values),'outputs_complete':complete,'items':items}


def points(base):
    result={module:dict(classes) for module,classes in base.POINTS.items()}
    classes=result[MODULE]
    classes['RequestState']=('make_request_output',)
    classes['RequestOutputCollector']=('put','get','get_nowait','close')
    return result


class OutputObservation:
    """Mixed into a private base.Observer; no strong native-object registry."""
    def output_snapshot(self,point,args,kwargs,result,phase,call):
        owner=args[0] if args else None;data=attributes(owner)
        context=self.output_base._ROUTE_REQUEST.get()
        context=context if type(context) is dict else {}
        collector=owner if point.startswith('RequestOutputCollector.') else data.get('queue')
        native=data.get('request_id') if point.startswith('RequestState.') else None
        external=data.get('request_id') if point.startswith('RequestOutputCollector.') else data.get('external_req_id')
        state=owner if point.startswith('RequestState.') else None
        if point=='OutputProcessor.add_request':
            request=args[1] if len(args)>1 else kwargs.get('request')
            native=attributes(request).get('request_id')
            collector=args[5] if len(args)>5 else kwargs.get('queue')
            states=data.get('request_states')
            state=states.get(native) if type(states) is dict and type(native) is str else None
            external=attributes(state).get('external_req_id')
        provided=(args[1] if len(args)>1 else kwargs.get('output')) if point=='RequestOutputCollector.put' else None
        returned=result if phase=='RETURN' and point in {'RequestState.make_request_output','RequestOutputCollector.get','RequestOutputCollector.get_nowait'} else None
        stored=attributes(collector).get('output') if point.startswith('RequestOutputCollector.') else None
        return {'schema':OUTPUT_SCHEMA,'point':point,'phase':phase,'call_sha256':call,'source_sha256':SOURCE_SHA256,
            'asgi_request_sha256':context.get('request'),'asgi_invocation_sha256':context.get('invocation'),
            'asgi_context_live':context.get('pid')==self.output_base.os.getpid() and context.get('closed') is False,
            'collector_sha256':self.output_ids.identify(collector,'collector'),'state_sha256':self.output_ids.identify(state,'state'),
            'native_request_sha256':string_sha(native),'external_request_sha256':string_sha(external),
            'provided_output_object_sha256':self.output_ids.identify(provided,'output'),
            'output_object_sha256':self.output_ids.identify(returned,'output'),
            'stored_output_object_sha256':self.output_ids.identify(stored,'output'),
            'provided_text':text_samples(provided),'returned_text':text_samples(returned),'stored_text':text_samples(stored),
            'output_present':returned is not None,'observer_runtime':self.output_runtime,'observer_code_sha256':self.output_code}

    def output_wrap(self,point,original):
        observer=self
        def begin():
            try:return observer.route_nonce('output-call') if observer.active() else None
            except BaseException:return None
        def emit(args,kwargs,result,phase,call):
            if call is None:return
            try:observer.emit('OUTPUT',observer.output_snapshot(point,args,kwargs,result,phase,call))
            except BaseException:pass  # Observation cannot replace native errors/cancellation.
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def asynchronous(*args,**kwargs):
                call=begin();emit(args,kwargs,None,'CALL',call)
                try:result=await original(*args,**kwargs)
                except BaseException:
                    emit(args,kwargs,None,'RAISE',call);raise
                emit(args,kwargs,result,'RETURN',call);return result
            return asynchronous
        @functools.wraps(original)
        def synchronous(*args,**kwargs):
            call=begin();emit(args,kwargs,None,'CALL',call)
            try:result=original(*args,**kwargs)
            except BaseException:
                emit(args,kwargs,None,'RAISE',call);raise
            emit(args,kwargs,result,'RETURN',call);return result
        return synchronous


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    codes=(attributes,string_sha,WeakIdentities.identify,text_samples,OutputObservation.output_snapshot,OutputObservation.output_wrap)
    return hashlib.sha256(''.join(base.code_digest(relative_code(fn.__code__)) for fn in codes).encode('ascii')).hexdigest()


def configure(base,invocation):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None:raise ValueError('OUTPUT_RUNTIME_UNAVAILABLE')
    if code_contract(base)!=expected:raise ValueError('OUTPUT_CODE_UNAVAILABLE')
    invocation.configure(base)
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__
    base.POINTS=points(base)
    class OutputObserver(OutputObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.output_base=base;self.output_runtime=runtime;self.output_code=expected
            self.output_ids=WeakIdentities(self.route_nonce,pid=base.os.getpid)
        def wrap(self,module_name,point,original):
            if module_name==MODULE and point in POINTS:
                wrapped=super().wrap(module_name,point,original) if point=='OutputProcessor.add_request' else original
                return self.output_wrap(point,wrapped)
            return super().wrap(module_name,point,original)
    base.Observer=OutputObserver
    return base


def _dependency(name,expected):
    directory=os.open('/guard-lifecycle',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    finally:os.close(directory)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=262144:raise ValueError('OUTPUT_DEPENDENCY_UNAVAILABLE')
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(262145)
        after=os.fstat(fd)
        if (any(getattr(before,k)!=getattr(after,k) for k in ('st_dev','st_ino','st_mode','st_size','st_mtime_ns','st_ctime_ns'))
                or len(raw)!=before.st_size or hashlib.sha256(raw).hexdigest()!=expected):raise ValueError('OUTPUT_DEPENDENCY_UNAVAILABLE')
        return raw
    finally:os.close(fd)


def _private_base():
    modules=[]
    for name,expected in (('guard_lifecycle_base.py',BASE8_SHA256),('guard_attempt_invocation.py',INVOCATION10_SHA256)):
        raw=_dependency(name,expected);module=types.ModuleType('_guard_output_'+name.removesuffix('.py'))
        module.__file__='/guard-lifecycle/'+name
        exec(compile(raw,module.__file__,'exec',dont_inherit=True,optimize=0),module.__dict__);modules.append(module)
    return configure(*modules)


def install(path=None):
    global _BASE
    if _BASE is not None:return False
    try:
        base=_private_base()
        if not base.install(path):return False
        _BASE=base;return True
    except Exception:return False


class Middleware:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        if _BASE is None:return await self.app(scope,receive,send)
        return await _BASE.Middleware(self.app)(scope,receive,send)


def seal(path='/guard-lifecycle/settings.json'):
    return (_BASE if _BASE is not None else _private_base()).seal(path)


if __name__=='__main__':
    if sys.argv[1:]!=['--seal']:raise SystemExit('NATIVE_SENSOR_SEAL_ARGUMENTS_REQUIRED')
    try:seal()
    except Exception:raise SystemExit('NATIVE_SENSOR_SEAL_UNAVAILABLE') from None
