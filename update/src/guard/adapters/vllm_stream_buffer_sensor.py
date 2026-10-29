"""Opt-in /12 actual ASGI transport boundaries over immutable /8 assets.

Only exact reviewed Uvicorn source/code is hooked. Actual selected TCP transport
buffer and kernel outq observations are distinct from process RSS and body size.
No response/header/body values, socket addresses or exception strings are kept.
"""
from __future__ import annotations
import asyncio,contextvars,functools,hashlib,inspect,os,re,stat,sys,time,types,weakref

PROFILE='vllm-native-lifecycle-metadata/12'
SCHEMA='guard-native-lifecycle-events/12'
STREAM_SCHEMA='guard-native-stream-buffer/1'
COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
BASE8_SHA256='2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd'
UVICORN_COMMIT='3eb9a9ab9af69005c687728097461c1fd2a95db9'
SOURCE_ADDITIONS={'uvicorn.protocols.http.h11_impl':'a3154a6c2cfaaaebe19ac7aa255dfe9b6b7b490de1da95274c033aa093e8c322',
 'uvicorn.protocols.http.httptools_impl':'1d4e1c579df4168433a61a01f10c628b31c1790e72dd9769d2b8633b22214033',
 'uvicorn.protocols.http.flow_control':'d39d16560df512f3ce907c329c2a0c3f5cd7165fef3b753876eae5739bf2a785'}
TARGETS={'uvicorn.protocols.http.h11_impl':{'RequestResponseCycle':('run_asgi','send'),'H11Protocol':('connection_lost',)},
 'uvicorn.protocols.http.httptools_impl':{'RequestResponseCycle':('run_asgi','send'),'HttpToolsProtocol':('connection_lost',)},
 'uvicorn.protocols.http.flow_control':{'FlowControl':('drain',)}}
PHASES=frozenset({'REQUEST_ENTER','RUN_RETURN','RUN_RAISE','SEND_ENTER','SEND_RETURN','SEND_RAISE',
 'DRAIN_ENTER','DRAIN_RETURN','DRAIN_RAISE','CONNECTION_LOST_ENTER','CONNECTION_LOST_RETURN','CONNECTION_LOST_RAISE'})
MAX_CONNECTIONS=256
MAX_ROWS=2048
MAX_BYTES=2*1024**2
CODE_FILENAME='guard/adapters/vllm_stream_buffer_sensor.py'
# Added only after the completed own-source offline code review, before freeze.
RUNTIME_CODE_CONTRACTS={'cpython-3.12.14':'ceec7cda9f19079820d1b9b1051a2325b1260911e880923d54dc1d50e32893ad','cpython-3.12.3':'875b72bd80b035631cba745eb877dce59d2adff2dd3b9a67667296fa0b5c12f0'}
_BASE=None
_SEND=contextvars.ContextVar('guard_stream_buffer_send',default=None)


def attrs(value):
    try:result=object.__getattribute__(value,'__dict__')
    except (AttributeError,TypeError):result={}
    return result if type(result) is dict else {}


def sha(value):return hashlib.sha256(value).hexdigest()


def integer(value):return value if type(value) is int and 0<=value<2**63 else None


def request_hash(scope):
    if type(scope) is not dict or scope.get('type')!='http' or scope.get('path')!='/v1/completions':return None
    headers=scope.get('headers');found=[]
    if type(headers) not in (tuple,list) or len(headers)>128:return None
    for pair in headers:
        if type(pair) not in (tuple,list) or len(pair)!=2 or type(pair[0]) is not bytes or type(pair[1]) is not bytes:continue
        if pair[0].lower()==b'x-request-id':found.append(pair[1])
    if len(found)!=1 or re.fullmatch(rb'byul-lif04-[0-9a-f]{32}-(normal-before|normal|slow|slow-close|normal-after)',found[0]) is None:return None
    return sha(found[0])


def transport_metrics(transport):
    value={'transport_kind':'unsupported','write_buffer_bytes':None,'kernel_outq_bytes':None,'socket_inode':None,
        'socket_identity_stable':False,'transport_closing':None,'process_rss_bytes':None}
    try:
        with open('/proc/self/statm','rb') as stream:parts=stream.read(256).split()
        if len(parts)>=2 and parts[1].isdigit():value['process_rss_bytes']=integer(int(parts[1])*os.sysconf('SC_PAGE_SIZE'))
    except (OSError,ValueError,AttributeError):pass
    try:
        from asyncio.selector_events import _SelectorSocketTransport
        import socket
        if type(transport) is not _SelectorSocketTransport:return value
        data=attrs(transport);buffer=data.get('_buffer');sock=data.get('_sock')
        if type(buffer) is not bytearray:return value
        value.update(transport_kind='asyncio-selector-tcp',write_buffer_bytes=integer(len(buffer)),
            transport_closing=data.get('_closing') if type(data.get('_closing')) is bool else None)
        if sys.platform!='linux' or type(sock) is not socket.socket:return value
        import array,fcntl
        fd=sock.fileno();before=os.fstat(fd);link=os.readlink('/proc/self/fd/'+str(fd))
        if not stat.S_ISSOCK(before.st_mode) or re.fullmatch(r'socket:\[[0-9]+\]',link) is None:return value
        out=array.array('i',[0]);fcntl.ioctl(fd,0x5411,out,True)
        after=os.fstat(fd)
        if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):return value
        value.update(kernel_outq_bytes=integer(out[0]),socket_inode=integer(before.st_ino),socket_identity_stable=True)
    except (OSError,ValueError,TypeError,ImportError,AttributeError):pass
    return value


def expected_codes(module,base):
    name=module.__name__;path=vars(module).get('__file__')
    path_before=os.stat(path,follow_symlinks=False)
    if not stat.S_ISREG(path_before.st_mode):raise ValueError
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=131072:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(131073)
        after=os.fstat(fd)
        path_after=os.stat(path,follow_symlinks=False)
        identity=lambda v:(v.st_dev,v.st_ino,v.st_mode,v.st_size,v.st_mtime_ns,v.st_ctime_ns)
        if len(raw)!=before.st_size or len({identity(v) for v in (path_before,before,after,path_after)})!=1 or sha(raw)!=SOURCE_ADDITIONS[name]:raise ValueError
    finally:os.close(fd)
    root=compile(raw,path,'exec',dont_inherit=True,optimize=0);result={}
    def visit(code):
        result[code.co_qualname]=base.code_digest(code)
        for item in code.co_consts:
            if isinstance(item,types.CodeType):visit(item)
    visit(root);return result


class StreamObservation:
    def stream_entry(self,transport,request=None):
        if self.stream_pid!=os.getpid():
            self.stream_pid=os.getpid();self.stream_connections=weakref.WeakKeyDictionary();self.stream_serial=0
        if transport is None:return None
        try:
            entry=self.stream_connections.get(transport)
            if entry is None:
                if request is None or len(self.stream_connections)>=MAX_CONNECTIONS:return None
                self.stream_serial+=1
                context=self._writer().context
                entry={'connection':sha((self.config['session']+':'+str(context['pid'])+':'+str(context['start_ticks'])+':'+str(self.stream_serial)).encode()),
                    'requests':set(),'serial':0}
                self.stream_connections[transport]=entry
            if request is not None and len(entry['requests'])<8:entry['requests'].add(request)
            return entry
        except (TypeError,RuntimeError):return None

    def stream_emit(self,module,phase,transport,entry,request,call,*,body_bytes=None,flow=None,cycle=None,started=None,finished=None,parent=None):
        if entry is None:return
        try:
            context=self.stream_base._ROUTE_REQUEST.get();context=context if type(context) is dict else {}
            source=self.modules.get(module,{})
            payload={'schema':STREAM_SCHEMA,'phase':phase,'source_module':module,'source_sha256':SOURCE_ADDITIONS[module],
                'source_verified':source.get('source_sha256')==SOURCE_ADDITIONS[module],
                'request_sha256':request,'asgi_invocation_sha256':context.get('invocation') if context.get('request')==request else None,
                'asgi_context_live':context.get('pid')==os.getpid() and context.get('request')==request and context.get('closed') is False,
                'connection_sha256':entry['connection'],'associated_requests':sorted(entry['requests']),'call_sha256':call,'parent_call_sha256':parent,
                'body_bytes':integer(body_bytes),'operation_started_ns':integer(started),'operation_finished_ns':integer(finished),
                'flow_write_paused':attrs(flow).get('write_paused') if type(attrs(flow).get('write_paused')) is bool else None,
                'cycle_disconnected':attrs(cycle).get('disconnected') if type(attrs(cycle).get('disconnected')) is bool else None,
                'observer_runtime':self.stream_runtime,'observer_code_sha256':self.stream_code,**transport_metrics(transport)}
            self.emit('STREAM_BUFFER',payload)
        except BaseException:
            self.disabled_pid=os.getpid()

    def stream_wrap(self,module,point,original,expected):
        observer=self
        def selected(args):
            owner=args[0] if args else None;data=attrs(owner)
            if point.endswith('connection_lost'):
                transport=data.get('transport');return owner,transport,observer.stream_entry(transport),None,None
            if point=='FlowControl.drain':
                binding=_SEND.get()
                if type(binding) is not tuple or len(binding)!=3:return owner,None,None,None,None
                transport=data.get('_transport');entry=observer.stream_entry(transport)
                if entry is None or entry['connection']!=binding[0]:return owner,None,None,None,None
                return owner,transport,entry,binding[1],binding[2]
            request=request_hash(data.get('scope'));transport=data.get('transport')
            return owner,transport,observer.stream_entry(transport,request),request,None
        def begin(args):
            if not observer.active():return None
            if observer.stream_base.code_digest(original.__code__)!=expected:
                observer.modules.pop(module,None);observer.emit('MODULE',{'module':module,'verified':False});return None
            owner,transport,entry,request,parent=selected(args)
            if entry is None or request is None and not point.endswith('connection_lost'):return None
            entry['serial']+=1;call=sha((entry['connection']+':'+str(entry['serial'])).encode())
            return owner,transport,entry,request,call,parent
        def guarded_begin(args):
            try:return begin(args)
            except BaseException:
                observer.disabled_pid=os.getpid();return None
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def wrapped(*args,**kwargs):
                value=guarded_begin(args)
                if value is None:return await original(*args,**kwargs)
                owner,transport,entry,request,call,parent=value;flow=owner if point=='FlowControl.drain' else attrs(owner).get('flow')
                prefix='DRAIN' if point=='FlowControl.drain' else 'SEND' if point=='RequestResponseCycle.send' else 'REQUEST'
                message=args[1] if len(args)>1 and point=='RequestResponseCycle.send' else kwargs.get('message') if point=='RequestResponseCycle.send' else None
                body=message.get('body') if type(message) is dict and message.get('type')=='http.response.body' else None
                size=len(body) if type(body) is bytes else None
                observer.stream_emit(module,prefix+'_ENTER',transport,entry,request,call,body_bytes=size,flow=flow,cycle=owner,parent=parent)
                token=_SEND.set((entry['connection'],request,call)) if prefix=='SEND' else None
                returned=False;started=time.monotonic_ns()
                try:
                    result=await original(*args,**kwargs);returned=True;return result
                finally:
                    finished=time.monotonic_ns()
                    observer.stream_emit(module,('RUN_' if prefix=='REQUEST' else prefix+'_')+('RETURN' if returned else 'RAISE'),
                        transport,entry,request,call,body_bytes=size,flow=flow,cycle=owner,started=started,finished=finished,parent=parent)
                    if token is not None:
                        try:_SEND.reset(token)
                        except BaseException:observer.disabled_pid=os.getpid()
            return wrapped
        @functools.wraps(original)
        def wrapped(*args,**kwargs):
            value=guarded_begin(args)
            if value is None:return original(*args,**kwargs)
            owner,transport,entry,request,call,parent=value
            observer.stream_emit(module,'CONNECTION_LOST_ENTER',transport,entry,None,call)
            returned=False;started=time.monotonic_ns()
            try:result=original(*args,**kwargs);returned=True;return result
            finally:observer.stream_emit(module,'CONNECTION_LOST_RETURN' if returned else 'CONNECTION_LOST_RAISE',transport,entry,None,call,started=started,finished=time.monotonic_ns())
        return wrapped


def relative_code(code):
    return code.replace(co_filename=CODE_FILENAME,co_consts=tuple(relative_code(v) if isinstance(v,types.CodeType) else v for v in code.co_consts))


def code_contract(base):
    functions=(attrs,sha,integer,request_hash,transport_metrics,expected_codes,StreamObservation.stream_entry,StreamObservation.stream_emit,StreamObservation.stream_wrap)
    return sha(''.join(base.code_digest(relative_code(fn.__code__)) for fn in functions).encode())


def configure(base):
    runtime='cpython-'+'.'.join(map(str,sys.version_info[:3]));expected=RUNTIME_CODE_CONTRACTS.get(runtime)
    if sys.implementation.name!='cpython' or sys.flags.optimize!=0 or expected is None or code_contract(base)!=expected:raise ValueError('STREAM_BUFFER_OBSERVER_CODE_UNAVAILABLE')
    base.PROFILE,base.SCHEMA=PROFILE,SCHEMA;base.__file__=__file__;base.MAX_ROWS,base.MAX_BYTES=MAX_ROWS,MAX_BYTES
    base.SOURCE_SHA={**base.SOURCE_SHA,**SOURCE_ADDITIONS}
    class StreamObserver(StreamObservation,base.Observer):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs);self.stream_base=base;self.stream_runtime=runtime;self.stream_code=expected
            self.stream_pid=os.getpid();self.stream_serial=0;self.stream_connections=weakref.WeakKeyDictionary()
        def instrument(self,module):
            name=module.__name__
            if name not in TARGETS:return super().instrument(module)
            codes=expected_codes(module,base);changes=[]
            for cls_name,methods in TARGETS[name].items():
                cls=vars(module).get(cls_name)
                if not isinstance(cls,type):raise ValueError('STREAM_BUFFER_CLASS_UNAVAILABLE')
                for method in methods:
                    original=vars(cls).get(method);point=cls_name+'.'+method
                    if not inspect.isfunction(original) or base.code_digest(original.__code__)!=codes.get(point):raise ValueError('STREAM_BUFFER_TARGET_CODE_UNAVAILABLE')
                    changes.append((cls,method,self.stream_wrap(name,point,original,codes[point])))
            for cls,method,wrapped in changes:setattr(cls,method,wrapped)
    base.Observer=StreamObserver;return base


def _private_base():
    directory=os.open('/guard-lifecycle',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:fd=os.open('guard_lifecycle_base.py',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
    finally:os.close(directory)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=262144:raise ValueError
        with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(262145)
        after=os.fstat(fd)
        if len(raw)!=before.st_size or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or sha(raw)!=BASE8_SHA256:raise ValueError
    finally:os.close(fd)
    base=types.ModuleType('_guard_stream_buffer_base');base.__file__='/guard-lifecycle/guard_lifecycle_base.py'
    exec(compile(raw,base.__file__,'exec',dont_inherit=True,optimize=0),base.__dict__);return configure(base)


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
    if sys.argv[1:]!=['--seal']:raise SystemExit('STREAM_BUFFER_SEAL_ARGUMENTS_REQUIRED')
    try:seal()
    except Exception:raise SystemExit('STREAM_BUFFER_SEAL_UNAVAILABLE') from None
