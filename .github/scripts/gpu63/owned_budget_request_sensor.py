"""Link the fixed own HTTP cases to exact installed scheduling and KV calls."""

import contextvars
import hashlib
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import weakref


SOURCE = Path('/guard-owned-budget')
OUT = Path('/guard-owned-budget-data')
SCOPE = contextvars.ContextVar('byul_owned_budget_case', default=None)
CASE = re.compile(rb'byul-lif04-[0-9a-f]{32}-(normal-before|normal|slow|slow-close|normal-after)')
HEX = re.compile('[0-9a-f]{64}')
REQUEST = {'module': 'vllm.v1.request', 'name': 'Request'}
CORE_REQUEST = {'module': 'vllm.v1.engine', 'name': 'EngineCoreRequest'}
SCHEDULER_OUTPUT = {'module': 'vllm.v1.core.sched.output', 'name': 'SchedulerOutput'}
POINTS = {
    'vllm.v1.engine.input_processor': ('InputProcessor', ('assign_request_id',)),
    'vllm.v1.core.sched.scheduler': ('Scheduler', ('add_request', '_enqueue_waiting_request', 'schedule', 'update_from_output')),
    'vllm.v1.core.kv_cache_manager': ('KVCacheManager', ('allocate_slots', 'free', 'pop_blocks_for_free')),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def internal_digest(key, raw):
    if type(raw) is not str or not 1 <= len(raw) <= 512:
        raise ValueError('invalid reviewed native request identifier')
    return hmac.new(key, b'guard-rank-request-id/1\0' + raw.encode('utf-8'), hashlib.sha256).hexdigest()


def sample_group(blocks):
    rows = []
    for block in blocks:
        if block.is_null is True:
            continue
        if type(block.block_id) is not int or type(block.ref_cnt) is not int or block.ref_cnt < 0:
            raise ValueError('invalid installed KV reference counter')
        rows.append(block)
    return rows


def snapshot(manager, raw):
    # Reviewed SingleTypeKVCacheManager maps may be defaultdicts. dict.get
    # is deliberate so an observation after release creates no entries.
    managers = vars(manager.coordinator)['single_type_managers']
    groups = []
    for group in managers:
        mapping = vars(group)['req_to_blocks']
        groups.append(sample_group(dict.get(mapping, raw, ())))
    return groups


def owned_block_token(key, pool_id, block_id, object_id):
    value = f'{pool_id}:{block_id}:{object_id}'.encode('ascii')
    return hmac.new(key, b'guard-owned-budget-block/1\0' + value, hashlib.sha256).hexdigest()


def weak_block_counts(groups, key):
    result = []
    seen = set()
    for blocks in groups:
        for block in blocks:
            pool, block_id = block.pool, block.block_id
            identity = (id(pool), block_id, id(block))
            if identity in seen:
                continue
            seen.add(identity)
            try:
                watch = weakref.ref(pool)
            except TypeError:
                watch = None
            registered = watch is not None and type(pool.blocks) is list and 0 <= block_id < len(pool.blocks) and pool.blocks[block_id] is block
            result.append((watch if registered else None, block_id, id(block), block.ref_cnt,
                           owned_block_token(key, *identity)))
    return result


def block_changes(watches):
    changes = []
    for watch, index, identity, before, token in watches:
        pool = watch() if watch is not None else None
        block = pool.blocks[index] if pool is not None and 0 <= index < len(pool.blocks) else None
        after = block.ref_cnt if block is not None and id(block) == identity else None
        changes.append({'before_refcount': before, 'after_refcount': after, 'block_hmac_sha256': token})
    return changes


class PendingCalls:
    """Keep entry metadata without retaining the frame, receiver or request."""

    def __init__(self):
        self.entries = {}

    @staticmethod
    def key(frame):
        return threading.get_ident(), id(frame)

    def reset(self, frame):
        self.entries.pop(self.key(frame), None)

    def record(self, code, frame, identity, details=None):
        self.entries[self.key(frame)] = (code, identity, time.monotonic_ns(), details)

    def take(self, code, frame):
        key = self.key(frame)
        entry = self.entries.get(key)
        if entry is not None and entry[0] is code:
            del self.entries[key]
            return entry[1:]
        return None


def call_identity(data, binding=None):
    return id(data.get('self')), id(data.get('request')), binding


class Observer:
    def __init__(self, product):
        from byul_native.vllm_rank_request_identity import assignment, member

        self.assignment, self.member = assignment, member
        self.product = product
        self.config = json.loads((SOURCE/'settings.json').read_bytes())
        self.key = (SOURCE/'request-observation.key').read_bytes()
        if len(self.key) != 32:
            raise ValueError('invalid per-audit observing key')
        self.uid = os.environ['BYUL_BUDGET_POD_UID']
        self.pid = os.getpid()
        self.start_ticks = int(Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()[19])
        self.clock = {'inode': os.stat('/proc/self/ns/time').st_ino,
                      'offsets_sha256': sha(Path('/proc/self/timens_offsets').read_bytes())}
        self.mon = sys.monitoring
        self.tool = next((i for i in (5, 4, 3) if self.mon.get_tool(i) is None), None)
        if self.tool is None:
            raise ValueError('local monitoring slot unavailable')
        self.installer = product.instrument.__code__
        self.emitter = product.stream_emit.__code__
        self.enabled = {}
        self.methods = {}
        self.active = {}
        self.calls = PendingCalls()
        self.target = None
        self.errors = set()

    def event(self, point, **data):
        record = {'point': point, 'session': self.config['session'], 'pod_uid': self.uid, 'pid': self.pid,
                  'process_start_ticks': self.start_ticks, 'clock': self.clock, 'monotonic_ns': time.monotonic_ns(),
                  'utc_ns': time.time_ns(), **data}
        path = OUT/f'budget-{self.pid}.jsonl'
        fd = os.open(path, os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, (json.dumps(record, sort_keys=True, separators=(',', ':'))+'\n').encode())
        finally:
            os.close(fd)

    def install(self):
        mon, ev = self.mon, self.mon.events
        mon.use_tool_id(self.tool, 'byul-owned-budget-request')
        mon.register_callback(self.tool, ev.PY_START, self.enter)
        mon.register_callback(self.tool, ev.PY_RETURN, self.returned)
        mon.register_callback(self.tool, ev.PY_UNWIND, self.unwound)
        mon.set_events(self.tool, ev.PY_UNWIND)
        mon.set_local_events(self.tool, self.emitter, ev.PY_START)
        mon.set_local_events(self.tool, self.installer, ev.PY_RETURN)
        for n in POINTS:
            if n in sys.modules:
                self.bind(sys.modules[n])
        self.event('loader', observer_sha256=sha(Path(__file__).read_bytes()))

    def bind(self, module):
        module_name = getattr(module, '__name__', None)
        if module_name not in POINTS or module_name in self.enabled:
            return
        source = Path(module.__file__).resolve()
        digest = sha(source.read_bytes())
        if digest != self.config['sources'][module_name]:
            self.event('installed_source_mismatch', module=module_name, source_sha256=digest)
            return
        cls_name, methods = POINTS[module_name]
        cls = vars(module)[cls_name]
        selected = {}
        for name in methods:
            code = inspect.unwrap(getattr(cls, name)).__code__
            if Path(code.co_filename).resolve() != source or code.co_qualname != f'{cls_name}.{name}':
                raise ValueError('installed budget operation differs from reviewed source')
            selected[code] = name
        for code, name in selected.items():
            self.methods[code] = name
            ev = self.mon.events
            self.mon.set_local_events(self.tool, code, ev.PY_START|ev.PY_RETURN)
        self.enabled[module_name] = digest
        self.event('installed_source_verified', module=module_name, source_sha256=digest,
                   methods={name: code.co_firstlineno for code, name in selected.items()})

    def own_http(self, f):
        if f['phase'] in ('RUN_RETURN', 'RUN_RAISE'):
            SCOPE.set(None)
        if f['phase'] != 'REQUEST_ENTER' or type(f.get('entry')) is not dict:
            return
        connection, call = f['entry'].get('connection'), f.get('call')
        if any(type(value) is not str or not HEX.fullmatch(value) for value in (connection, call)):
            return
        if self.target is None:
            self.target = json.loads((SOURCE/'live.json').read_bytes())
        if self.target['engine_pod_uid'] != self.uid or self.target['session'] != self.config['session']:
            return
        scope = vars(f['cycle'])['scope']
        if scope.get('path') != '/v1/completions' or scope.get('client', (None,))[0] != self.target['probe_ip']:
            return
        headers = [value for name, value in scope['headers'] if name.lower() == b'x-request-id']
        slot = CASE.fullmatch(headers[0]) if len(headers) == 1 else None
        if slot is None:
            return
        SCOPE.set({'case_id': 'lif04-'+slot[1].decode('ascii'), 'product_connection_sha256': connection,
                   'product_call_sha256': call, 'claimed': False})

    def tag_assignment(self, data):
        case = SCOPE.get()
        if not case or case['claimed']:
            return
        values = self.assignment(data['request'], expected_type=CORE_REQUEST, key=self.key)
        token = values['internal_hmac_sha256']
        stored = {k:v for k,v in case.items() if k != 'claimed'} | {'native_request_hmac_sha256': token,
                  'external_engine_hmac_sha256': values['external_hmac_sha256'], 'session': self.config['session'], 'pod_uid': self.uid}
        case['claimed'] = True
        p = OUT/f'assigned-{token}.json'
        with p.open('x', encoding='utf-8') as file:
            json.dump(stored, file, sort_keys=True)
        self.event('assign_request_id', **{k:v for k,v in stored.items() if k not in ('session','pod_uid')})

    def admitted(self, raw):
        if raw in self.active:
            return self.active[raw]
        tag = internal_digest(self.key, raw)
        p = OUT/f'assigned-{tag}.json'
        if not p.is_file() or len(self.active) >= 5:
            return None
        doc = json.loads(p.read_bytes())
        if (doc['session'], doc['pod_uid'], doc['native_request_hmac_sha256']) != (self.config['session'], self.uid, tag):
            return None
        value = {'native_request_hmac_sha256': tag, 'case_id': doc['case_id']}
        self.active[raw] = value
        return value

    def enter(self, code, _offset):
        if self.pid != os.getpid():
            return
        try:
            self.observe_entry(code, sys._getframe(1))
        except (AttributeError, OSError, TypeError, RuntimeError, ValueError, KeyError) as e:
            self.error(code, e)

    def observe_entry(self, code, frame):
        data = frame.f_locals
        if code is self.emitter:
            self.own_http(data)
            return
        self.calls.reset(frame)
        name = self.methods[code]
        if name == 'schedule':
            eligible = frozenset(row['native_request_hmac_sha256'] for row in tuple(self.active.values()))
            if eligible:
                self.calls.record(code, frame, id(data['self']), eligible)
            return
        if name == 'update_from_output':
            self.scheduled(data['scheduler_output'], name)
            return
        if name == 'assign_request_id':
            case = SCOPE.get()
            if case and not case['claimed']:
                self.calls.record(code, frame, call_identity(data, id(case)))
            return
        raw = self.member(data['request'], 'request_id', REQUEST)
        case = self.admitted(raw) if name == 'add_request' else self.active.get(raw)
        if case is None:
            return
        identity = call_identity(data, case['native_request_hmac_sha256'])
        if name in ('allocate_slots', 'free', 'pop_blocks_for_free'):
            groups = snapshot(data['self'], raw)
            self.calls.record(code, frame, identity, ([len(x) for x in groups], weak_block_counts(groups, self.key)))
        else:
            self.event(name+'.enter', **case)
            self.calls.record(code, frame, identity)

    def scheduled(self, output, point, started=None, eligible=None):
        if not self.active:
            return
        tokens = self.member(output, 'num_scheduled_tokens', SCHEDULER_OUTPUT)
        if type(tokens) is not dict:
            raise ValueError('reviewed schedule return layout differs')
        for raw, case in tuple(self.active.items()):
            selected = tokens.get(raw)
            if (type(selected) is int and selected > 0
                    and (eligible is None or case['native_request_hmac_sha256'] in eligible)):
                self.event(point, **case, num_scheduled_tokens=selected, call_start_monotonic_ns=started)

    def unwound(self, code, _offset, _exception):
        if self.calls.entries and code in self.methods and self.pid == os.getpid():
            self.calls.take(code, sys._getframe(1))

    def returned(self, code, _offset, result):
        if self.pid != os.getpid():
            return
        try:
            self.observe_return(code, sys._getframe(1), result)
        except (AttributeError, OSError, TypeError, RuntimeError, ValueError, KeyError) as e:
            self.error(code, e)

    def observe_return(self, code, frame, result):
        data = frame.f_locals
        if code is self.installer:
            self.bind(data.get('module'))
            return
        entry = self.calls.take(code, frame)
        if entry is None:
            return
        identity, stamp, details = entry
        name = self.methods[code]
        if name == 'assign_request_id':
            case = SCOPE.get()
            if case and identity == call_identity(data, id(case)):
                self.tag_assignment(data)
            return
        if name == 'schedule':
            if identity == id(data['self']):
                self.scheduled(result, name, stamp, details)
            return
        raw = self.member(data['request'], 'request_id', REQUEST)
        case = self.active.get(raw)
        if case is None or identity != call_identity(data, case['native_request_hmac_sha256']):
            return
        if name in ('allocate_slots', 'free', 'pop_blocks_for_free'):
            before, watches = details
            after = [len(x) for x in snapshot(data['self'], raw)]
            allocated = [sample_group(x) for x in result.blocks] if name == 'allocate_slots' and result is not None else None
            tokens = sorted({owned_block_token(self.key, id(b.pool), b.block_id, id(b)) for group in allocated or () for b in group})
            self.event(name, **case, before_group_references=before, after_group_references=after,
                       returned_allocation_by_group=[len(x) for x in allocated] if allocated is not None else None,
                       allocated_owned_block_tokens=tokens, before_reference_counters=block_changes(watches),
                       call_start_monotonic_ns=stamp)
        else:
            registered = data['self'].requests.get(raw) is data['request'] if name == 'add_request' else None
            self.event(name+'.return', **case, registered=registered)

    def error(self, code, error):
        token = (code.co_qualname, type(error).__name__)
        if token not in self.errors:
            self.errors.add(token)
            try:
                self.event('observer_error', symbol=token[0], error_type=token[1])
            except OSError:
                pass


def install():
    product = sys.modules.get('guard_lifecycle_sensor')
    cls = getattr(getattr(product, '_BASE', None), 'Observer', None)
    if os.environ.get('BYUL_BUDGET_POD_UID') and cls is not None and OUT.is_dir():
        Observer(cls).install()
