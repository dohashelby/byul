"""Read-only ASGI and source-qualified assignment observation for one owned LIF-05 generation."""

from contextvars import ContextVar
from datetime import datetime, timezone
import hmac
from hashlib import sha256
from inspect import unwrap
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from types import CodeType, FunctionType, MemberDescriptorType


ASSET = 'owned_lif05_request_trace.py'
PRIVATE = 'lif05-request-trace-private.json'
JOURNAL = 'requests.jsonl'
MAX_JOURNAL = 128 * 1024
INPUT = 'v1/engine/input_processor.py'
TRACE_PATHS = {
    'before': '/v1/chat/completions', 'inflight': '/v1/completions',
    'after': '/v1/chat/completions', 'recovered': '/v1/chat/completions',
}
# Selected Linux/amd64 image members reviewed at the approved OCI digests.
PATHS = (
    'entrypoints/openai/chat_completion/api_router.py', 'entrypoints/openai/chat_completion/serving.py',
    'entrypoints/openai/completion/api_router.py', 'entrypoints/openai/completion/serving.py',
    'entrypoints/serve/engine/serving.py', INPUT, 'v1/engine/__init__.py',
)
PINS = {
    'docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40': (
        '77f858fe0079b9dd131c26d22641c79d4ac0f2b22688d033863954a8a669d7d3',
        'ea1f76074a9587c8054f54d30a6ba748b6a5fc4d90dd82c801504505c1da1f92',
        'b93b00298a74a62a3f2d8da1c0a9380a2be5823f5ab7ab99a2c911906280d769',
        '75d05ddddf303734a078a3e42fcb173fef58ea56da09bc0b5c7525cb9bbbf527',
        '70143311acc2230b925e7f3603b8b9314f029ff5e98d5af990b3bdca691331f1',
        'c8abd8e4e14d99929d45a7ff3b2a74898269c5e2a7fa30b826623ee85e4ce813',
        '4e59828787634123026a6b424f48c21d691913dd338b59dd13f25fa260f2284b',
    ),
    'docker.io/vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b': (
        '77f858fe0079b9dd131c26d22641c79d4ac0f2b22688d033863954a8a669d7d3',
        '532dadea845c77a5a79011027b9989987c3e32467614e993f439f29c924de002',
        'b93b00298a74a62a3f2d8da1c0a9380a2be5823f5ab7ab99a2c911906280d769',
        'aac889978efb034b95ab81ec59695d83ba7c879ab48cd5be2448c980f109a3bd',
        'e5239ca4e2119fd019ee0ffcbf6e68f642b7ce94bad1b3fabe22ccdd8cf9eb02',
        'd79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b',
        'e7532e6c9fde4643cf31cd793d4ae4a6d618c71c87208430fb6680105dc1af42',
    ),
}


def digest(key, role, value):
    return hmac.new(key, b'guard-lif05-owned-http/1\0' + role.encode() + b'\0' + value, sha256).hexdigest()


def request_slot(key, case, token):
    raw = token.encode('ascii')
    external = b'cmpl-' + raw + b'-0' if case == 'inflight' else b'chatcmpl-' + raw
    return {'case_id':case, 'path':TRACE_PATHS[case], 'header_hmac':digest(key,'header',raw),
            'external_hmac':digest(key,'external',external)}


def checked_read(path, limit, *, owner=None):
    def identity(info):
        return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit
                or owner is not None and (before.st_uid != owner or stat.S_IMODE(before.st_mode) != 0o600)):
            raise ValueError('trace file identity')
        raw = stream.read(limit + 1)
        if identity(os.fstat(fd)) != identity(before) or len(raw) != before.st_size:
            raise ValueError('trace file changed during read')
        return raw


def code_child(code, name):
    child, = (item for item in code.co_consts if type(item) is CodeType and item.co_name == name)
    return child


def qualify(image, module):
    if sys.implementation.name != 'cpython' or sys.version_info[:2] != (3,12) or sys.flags.optimize:
        raise ValueError('assignment interpreter unverified')
    source = Path(module.__spec__.origin)
    root = source.parents[2]
    if source != root / INPUT:
        raise ValueError('assignment source path unverified')
    found = {}
    for path, expected in zip(PATHS, PINS[image], strict=True):
        raw = checked_read(root / path, 512 * 1024)
        found[path] = sha256(raw).hexdigest()
        if found[path] != expected:
            raise ValueError('installed source pin mismatch')
        if path == INPUT:
            code = compile(raw, str(source), 'exec', dont_inherit=True, optimize=0)
    expected = code_child(code_child(code, 'InputProcessor'), 'assign_request_id')
    cls = vars(module)['InputProcessor']
    descriptor = vars(cls)['assign_request_id']
    if type(descriptor) is not staticmethod:
        raise ValueError('assignment descriptor unverified')
    original = unwrap(descriptor.__func__)
    if (type(original) is not FunctionType or original.__globals__ is not vars(module)
            or original.__code__.co_filename != str(source) or original.__code__ != expected
            or original.__code__.co_qualname != 'InputProcessor.assign_request_id'
            or original.__code__.co_varnames[0] != 'request'):
        raise ValueError('installed assignment code unverified')
    return original.__code__, found


class Trace:
    def __init__(self, config, path):
        self.scope = config['scope']
        self.slots = {row['header_hmac']:row for row in config['slots']}
        self.key = bytes.fromhex(config['key'])
        self.path = path
        self.context = ContextVar('lif05_owned_http', default=None)
        self.calls = {}
        self.invocation = 0
        self.source = None
        self.assignment_class = None
        self.sequence = 0
        self.lock = threading.Lock()
        self.failed = False
        self.pid = os.getpid()
        self.file_identity = None
        clock = os.stat('/proc/self/ns/time')
        proc = Path('/proc/self/stat').read_text().rsplit(')',1)[1].split()
        self.emit('observer_opened', scope=self.scope, pid=self.pid, start_ticks=int(proc[19]),
            time_namespace=[clock.st_dev,clock.st_ino], sensor_sha256=sha256(Path(__file__).read_bytes()).hexdigest())

    def emit(self, event, slot=None, **values):
        if self.failed or self.pid != os.getpid():
            return
        with self.lock:
            try:
                flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_APPEND
                if self.sequence == 0:
                    flags |= os.O_CREAT | os.O_EXCL
                fd = os.open(self.path, flags, 0o600)
                try:
                    info = os.fstat(fd)
                    identity = (info.st_dev, info.st_ino)
                    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                            or stat.S_IMODE(info.st_mode) != 0o600
                            or self.file_identity not in (None, identity) or self.sequence >= 96):
                        raise ValueError
                    row = {'index': self.sequence, 'event':event,
                           'utc':datetime.now(timezone.utc).isoformat(), 'monotonic_ns':time.monotonic_ns(),
                           **values}
                    if slot is not None:
                        row.update(case_id=slot['case_id'], header_hmac=slot['header_hmac'], invocation=slot['invocation'])
                    encoded = (json.dumps(row, separators=(',', ':'), allow_nan=False) + '\n').encode()
                    if len(encoded) >= 4096 or len(encoded) + info.st_size > MAX_JOURNAL or os.write(fd, encoded) != len(encoded):
                        raise ValueError
                    self.file_identity = identity
                    self.sequence += 1
                finally:
                    os.close(fd)
            except (ValueError, OSError):
                self.failed = True

    def arm(self):
        if self.source is not None:
            return
        self.source = False
        tool = None
        try:
            module = sys.modules['vllm.v1.engine.input_processor']
            code, pins = qualify(self.scope['image'], module)
            self.assignment_class = vars(sys.modules['vllm.v1.engine'])['EngineCoreRequest']
            mon = sys.monitoring
            tool = next(number for number in (3,4) if mon.get_tool(number) is None)
            mon.use_tool_id(tool, 'owned-lif05-request-assignment')
            events = mon.events
            for kind, callback in ((events.PY_START,self.enter), (events.PY_RETURN,self.finish)):
                mon.register_callback(tool,kind,callback)
            mon.set_local_events(tool,code,events.PY_START | events.PY_RETURN)
            self.source = True
            self.emit('installed_assignment_verified', sources=pins, line=code.co_firstlineno)
        except (KeyError, AttributeError, OSError, RuntimeError, StopIteration, TypeError, ValueError):
            if tool is not None and mon.get_tool(tool) == 'owned-lif05-request-assignment':
                for event in (mon.events.PY_START,mon.events.PY_RETURN):
                    mon.register_callback(tool,event,None)
                mon.free_tool_id(tool)
            self.emit('installed_assignment_unverified')

    def member(self, request, name):
        cls = self.assignment_class
        if type(request) is not cls:
            raise ValueError
        field = vars(cls).get(name)
        if type(field) is not MemberDescriptorType or field.__objclass__ is not cls:
            raise ValueError
        return field.__get__(request, cls)

    def enter(self, _code, _offset):
        slot = self.context.get()
        if not slot or slot['closed'] or self.pid != os.getpid() or self.failed:
            return
        frame = sys._getframe(1)
        edge = (threading.get_ident(), id(frame))
        self.calls.pop(edge,None)
        try:
            request = frame.f_locals['request']
            value = self.member(request,'request_id')
            if (type(value) is not str or not 1 <= len(value) <= 512
                    or digest(self.key,'external',value.encode()) != slot['external_hmac']):
                return
            self.calls[edge] = (slot, id(request))
            self.emit('assignment_invoked',slot)
        except (KeyError, AttributeError, ValueError):
            return

    def finish(self, _code, _offset, _result):
        frame = sys._getframe(1)
        entry = self.calls.pop((threading.get_ident(), id(frame)), None)
        if entry is None:
            return
        slot, request_object = entry
        try:
            request = frame.f_locals['request']
            external = self.member(request,'external_req_id')
            internal = self.member(request,'request_id')
            if (slot is not self.context.get() or slot['closed'] or id(request) != request_object
                    or type(external) is not str or type(internal) is not str or not 1 <= len(internal) <= 512
                    or digest(self.key,'external',external.encode()) != slot['external_hmac']):
                return
            self.emit('assignment_returned',slot, external_hmac=slot['external_hmac'],
                      internal_hmac=digest(self.key,'internal',internal.encode()), identifier_changed=internal != external)
        except (KeyError, AttributeError, ValueError):
            return

    def registered(self, scope):
        client = scope.get('client')
        headers = scope.get('headers')
        if (scope.get('type') != 'http' or scope.get('method') != 'POST'
                or not client or client[0] != self.scope['probe_ip'] or not isinstance(headers, (tuple,list))
                or len(headers) > 64):
            return None
        request = [value for name,value in headers if name.lower() == b'x-request-id']
        if len(request) != 1 or re.fullmatch(rb'[0-9a-f]{32}',request[0]) is None:
            return None
        slot = self.slots.get(digest(self.key,'header',request[0]))
        if slot is not None and scope.get('path') == slot['path']:
            return slot
        return None

    async def observe(self, app, scope, receive, send):
        selected = self.registered(scope)
        if selected is None:
            return await app(scope,receive,send)
        self.invocation += 1
        slot = {**selected,'invocation':self.invocation,'closed':False}
        token = self.context.set(slot)
        self.emit('asgi_entered',slot)
        self.arm()

        async def received():
            message = await receive()
            if message.get('type') == 'http.disconnect':
                self.emit('asgi_disconnect_received',slot)
            return message

        async def sent(message):
            result = await send(message)
            if message.get('type') == 'http.response.start':
                self.emit('asgi_response_start_returned',slot,status=message['status'])
            if message.get('type') == 'http.response.body' and not message.get('more_body',False):
                self.emit('asgi_terminal_send_returned',slot)
            return result

        returned = False
        try:
            result = await app(scope,received,sent)
            returned = True
            return result
        finally:
            slot['closed'] = True
            for edge, entry in tuple(self.calls.items()):
                if entry[0] is slot:
                    self.calls.pop(edge,None)
            self.context.reset(token)
            self.emit('asgi_app_returned' if returned else 'asgi_app_raised',slot)


class Middleware:
    def __init__(self, app):
        self.app = app
        self.trace = None
        runtime = Path('/guard-lifecycle')
        try:
            config = json.loads(checked_read(runtime / PRIVATE, 8192, owner=os.getuid()))
            session = config['scope']['session']
            if re.fullmatch(r'[0-9a-f]{32}',session) and len(bytes.fromhex(config['key'])) == 32:
                self.trace = Trace(config, runtime / 'data' / ('lif05-http-' + session) / JOURNAL)
        except (KeyError, TypeError, AttributeError, OSError, ValueError):
            pass

    async def __call__(self, scope, receive, send):
        if self.trace is None:
            return await self.app(scope,receive,send)
        return await self.trace.observe(self.app,scope,receive,send)
