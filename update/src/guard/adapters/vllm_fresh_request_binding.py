"""세션 HMAC·fresh 원 호출의 조합. 원본 snapshot 센서를 복사하거나 수정하지 않는다.

설정된 lifecycle composition 뒤에 붙이는 관측 확장이다. 독립 import는 hook을
설치하지 않는다. 새 profile의 준비 자산에서만 키와 정확 원 코드를 확인한다.
"""
import functools
import hmac
import json
import re

_SNAPSHOT_CONFIGURE = globals().get('_configure')
FIELDS = frozenset({'scheme', 'asgi_invocation_sha256', 'asgi_request_sha256', 'selection_call_sha256',
    'native_selection_call_sha256', 'native_call_sha256', 'base_request_sha256', 'native_request_sha256', 'caller_frame_verified',
    'native_owner_verified', 'producer_authenticated', 'worker_connection_verified'})


def validate_sample(value, *, point, phase, spec):
    if (type(value) is not dict or set(value) != FIELDS or value['scheme'] != 'session-hmac-sha256'
            or point not in {spec['selection_point'], spec['native_selection_point'], spec['input_point']}
            or value['caller_frame_verified'] is not True or value['native_owner_verified'] is not True
            or value['producer_authenticated'] is not False or value['worker_connection_verified'] is not False):
        raise ValueError('REQUEST_BINDING_SAMPLE_INVALID')
    required = {'asgi_invocation_sha256', 'selection_call_sha256'}
    if point in {spec['input_point'], spec['native_selection_point']}:
        required |= {'native_selection_call_sha256', 'base_request_sha256', 'native_request_sha256'}
        if point == spec['input_point']:
            required.add('native_call_sha256')
        elif value['native_call_sha256'] is not None:
            raise ValueError('REQUEST_BINDING_SAMPLE_INVALID')
    elif any(value[k] is not None for k in ('native_call_sha256', 'native_selection_call_sha256', 'native_request_sha256')):
        raise ValueError('REQUEST_BINDING_SAMPLE_INVALID')
    elif phase == 'RETURN':
        required.add('base_request_sha256')
    for name in FIELDS:
        if name.endswith('_sha256') and (value[name] is not None or name in required):
            if type(value[name]) is not str or re.fullmatch('[0-9a-f]{64}', value[name]) is None:
                raise ValueError('REQUEST_BINDING_SAMPLE_INVALID')


def journal(rows, spec):
    selections, choices, native, invocations = {}, {}, {}, {}
    for row in rows:
        payload = row['payload']
        sample = payload.get('metadata', {}).get('request_binding') if row['event'] == 'NATIVE' else None
        if sample is not None:
            validate_sample(sample, point=payload['point'], phase=payload['phase'], spec=spec)
            invocation = invocations.setdefault(sample['asgi_invocation_sha256'],
                {'request': sample['asgi_request_sha256'], 'closed': False})
            if invocation['closed'] or invocation['request'] != sample['asgi_request_sha256']:
                raise ValueError('REQUEST_BINDING_INVOCATION_INVALID')
            selected = sample['selection_call_sha256']
            identity = (sample['asgi_invocation_sha256'], sample['asgi_request_sha256'])
            phase = payload['phase']
            if payload['point'] == spec['selection_point']:
                if phase == 'CALL':
                    if selected in selections:
                        raise ValueError('REQUEST_BINDING_SELECTION_REUSED')
                    selections[selected] = {'identity': identity, 'closed': False, 'base': None}
                else:
                    current = selections.get(selected)
                    if current is None or current['closed'] or current['identity'] != identity:
                        raise ValueError('REQUEST_BINDING_SELECTION_INVALID')
                    current.update(closed=True, base=sample['base_request_sha256'] if phase == 'RETURN' else None)
            else:
                current = selections.get(selected)
                if (current is None or not current['closed'] or current['base'] is None
                        or current['identity'] != identity or current['base'] != sample['base_request_sha256']
                        or [r['request_sha256'] for r in payload['requests']] != [sample['native_request_sha256']]):
                    raise ValueError('REQUEST_BINDING_NATIVE_SELECTION_INVALID')
                choice = sample['native_selection_call_sha256']
                if payload['point'] == spec['native_selection_point']:
                    if phase == 'CALL':
                        if choice in choices:
                            raise ValueError('REQUEST_BINDING_NATIVE_SELECTION_REUSED')
                        choices[choice] = {'sample': sample, 'closed': False, 'returned': False}
                    elif choice not in choices or choices[choice]['closed'] or choices[choice]['sample'] != sample:
                        raise ValueError('REQUEST_BINDING_NATIVE_SELECTION_INVALID')
                    else:
                        choices[choice].update(closed=True, returned=phase == 'RETURN')
                    continue
                chosen = choices.get(choice)
                identity_fields = ('asgi_invocation_sha256', 'asgi_request_sha256', 'selection_call_sha256',
                                   'base_request_sha256', 'native_request_sha256')
                if (chosen is None or not chosen['returned']
                        or any(chosen['sample'][k] != sample[k] for k in identity_fields)):
                    raise ValueError('REQUEST_BINDING_NATIVE_SELECTION_INVALID')
                call = sample['native_call_sha256']
                if phase == 'CALL':
                    if call in native:
                        raise ValueError('REQUEST_BINDING_NATIVE_CALL_REUSED')
                    native[call] = {'sample': sample, 'closed': False}
                elif call not in native or native[call]['closed'] or native[call]['sample'] != sample:
                    raise ValueError('REQUEST_BINDING_NATIVE_CALL_INVALID')
                else:
                    native[call]['closed'] = True
        if row['event'] == 'ASGI' and payload.get('asgi_invocation_sha256') is not None:
            invocation = invocations.setdefault(payload['asgi_invocation_sha256'],
                {'request': payload['request_sha256'], 'closed': False})
            if invocation['closed'] or invocation['request'] != payload['request_sha256']:
                raise ValueError('REQUEST_BINDING_ASGI_INVALID')
            if payload['phase'] in {'APP_RETURN', 'APP_RAISE'}:
                invocation['closed'] = True
    missing = []
    if not native: missing.append('REQUEST_BINDING_NATIVE_CALL_UNOBSERVED')
    if any(not row['closed'] for row in selections.values()): missing.append('REQUEST_BINDING_SELECTION_INCOMPLETE')
    if any(not row['closed'] for row in choices.values()): missing.append('REQUEST_BINDING_NATIVE_SELECTION_INCOMPLETE')
    if any(not row['closed'] for row in native.values()): missing.append('REQUEST_BINDING_NATIVE_CALL_INCOMPLETE')
    if any(not row['closed'] for row in invocations.values()): missing.append('REQUEST_BINDING_ASGI_END_UNOBSERVED')
    selected_ids = [row['sample']['native_selection_call_sha256'] for row in native.values()]
    if len(selected_ids) != len(set(selected_ids)): missing.append('REQUEST_BINDING_SELECTED_INPUT_REUSED')
    return {'selection_callbacks': len(selections), 'native_selections': len(choices), 'native_calls': len(native),
        'closed_native_calls': sum(row['closed'] for row in native.values()),
        'closed_asgi_invocations': sum(row['closed'] for row in invocations.values()), 'missing': missing,
        'producer_authenticated': False, 'worker_connection_verified': False, 'question_scope_complete': False}


def _binding_key(spec):
    def pairs(items):
        value = {}
        for name, item in items:
            if name in value:
                raise ValueError('REQUEST_BINDING_KEY_INVALID')
            value[name] = item
        return value
    directory = os.open('/guard-lifecycle', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        fd = os.open('request-binding-key.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or not 0 < before.st_size <= 256):
            raise ValueError('REQUEST_BINDING_KEY_INVALID')
        raw = os.read(fd, 257)
        after = os.fstat(fd)
        current = os.stat('request-binding-key.json', dir_fd=directory, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) != before.st_size or len({identity(s) for s in (before, after, current)}) != 1
                or hashlib.sha256(raw).hexdigest() != spec['request_key_sha256']):
            raise ValueError('REQUEST_BINDING_KEY_INVALID')
        value = json.loads(raw, object_pairs_hook=pairs)
        if (type(value) is not dict or set(value) != {'schema', 'session', 'key_hex'}
                or value['schema'] != 'guard-lifecycle-request-key/1'
                or type(value['session']) is not str or len(value['session']) != 32
                or type(value['key_hex']) is not str or len(value['key_hex']) != 64
                or any(c not in '0123456789abcdef' for c in value['session'] + value['key_hex'])):
            raise ValueError('REQUEST_BINDING_KEY_INVALID')
        return value['session'], bytes.fromhex(value['key_hex'])
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _configure(base):
    base = _SNAPSHOT_CONFIGURE(base)
    specification = SPEC['request_binding']
    session, key = _binding_key(SPEC)
    parent = base.Observer
    calls = base.contextvars.ContextVar('guard_fresh_request_binding_call', default=None)
    points = {point: name for name, row in SPEC['sources'].items()
              for cls, methods in row['classes'].items() for point in (cls + '.' + method for method in methods)}
    originals = {}
    def digest(domain, value):
        if type(value) is not str or not 0 < len(value) <= 512:
            return None
        return hmac.new(key, ('guard-native-request-binding/1\0' + domain + '\0' + value).encode(
            'utf-8', 'surrogatepass'), hashlib.sha256).hexdigest()
    # 요청 ID만 바꾼다. source/코드/모델/경로 SHA의 기존 의미는 유지한다.
    base.id_sha = lambda value: digest('native-request', value)

    class FreshObserver(parent):
        def __init__(self, config, config_sha):
            if config['session'] != session:
                raise ValueError('REQUEST_BINDING_KEY_SESSION_MISMATCH')
            super().__init__(config, config_sha)

        def route_scope(self, scope):
            context = super().route_scope(scope)
            if (context is None or scope.get('path') != specification['entry_path']
                    or scope.get('method') != specification['entry_method']):
                return None
            context['_binding_scope_id'] = id(scope)
            context['_binding_selections'] = []
            context['_binding_native_selections'] = []
            context['invocation'] = digest('asgi-invocation', context['invocation'])
            headers = scope.get('headers', ())
            values = [pair[1] for pair in headers if type(pair) in (list, tuple) and len(pair) == 2
                      and type(pair[0]) is bytes and pair[0].lower() == b'x-request-id'] if (
                          type(headers) in (list, tuple) and len(headers) <= 64) else []
            context['request'] = (hmac.new(key, b'guard-native-request-binding/1\0asgi-header\0' + values[0],
                hashlib.sha256).hexdigest() if len(values) == 1 and type(values[0]) is bytes
                and 0 < len(values[0]) <= 512 else None)
            return context

        def emit(self, event, payload):
            if event == 'ASGI':
                context = self.selection_context()
                # /8의 임시 SHA ContextVar를 새 profile의 공개 기록에 내보내지 않는다.
                payload = {**payload, 'request_sha256': context.get('request') if context is not None else None,
                    'asgi_invocation_sha256': context.get('invocation') if context is not None else None}
            return super().emit(event, payload)

        def _known(self, point):
            name = points[point]
            row = self.modules.get(name, {})
            return (row.get('source_sha256') == base.SOURCE_SHA[name] and type(row.get('code_sha256')) is str
                    and re.fullmatch('[0-9a-f]{64}', row['code_sha256']) is not None)

        def _fresh(self, point, args, kwargs, caller):
            context = self.selection_context()
            if context is None or any(not self._known(p) for p in (
                    specification['handler_point'], specification['selection_point'],
                    specification['native_selection_point'], specification['caller_point'], specification['input_point'])):
                return None
            selection = point == specification['selection_point']
            choice = point == specification['native_selection_point']
            parent_point = specification['handler_point'] if selection or choice else specification['caller_point']
            native = originals.get(parent_point)
            if (native is None or caller.f_code is not native.__code__ or caller.f_globals is not native.__globals__):
                return None
            owner = caller.f_locals.get('self')
            if selection or choice:
                request = (args[0] if args else kwargs.get('raw_request')) if selection else caller.f_locals.get('raw_request')
                scope = base.attrs(request).get('scope')
                engine = base.attrs(owner).get(specification['engine_attribute'])
                if (request is not caller.f_locals.get('raw_request') or type(scope) is not dict
                        or id(scope) != context['_binding_scope_id'] or engine is None):
                    return None
                if selection:
                    return {'context': context, 'engine_id': id(engine), 'kind': 'base',
                        'selection_call': digest('selection-call', self.route_nonce('binding-selection'))}
                request_id = args[1] if len(args) > 1 else kwargs.get('request_id')
                if ((args[0] if args else kwargs.get('self')) is not owner
                        or request_id is not caller.f_locals.get('request_id_item')
                        or digest('native-request', request_id) is None):
                    return None
                prefix = specification['native_prefix']
                if not request_id.startswith(prefix):
                    return None
                base_id, separator, index = request_id[len(prefix):].rpartition('-')
                if not separator or not index or len(index) > 10 or any(c not in '0123456789' for c in index):
                    return None
                matches = [row for row in context['_binding_selections'] if row['engine_id'] == id(engine)
                           and row['base_request_sha256'] == digest('selected-base', base_id)]
                if len(matches) != 1:
                    return None
                return {'context': context, 'engine_id': id(engine), 'kind': 'choice', 'selected': matches[0],
                    'native_request_sha256': digest('native-request', request_id),
                    'native_selection_call': digest('native-selection-call', self.route_nonce('binding-native-selection'))}
            processor = args[0] if args else kwargs.get('self')
            request_id = args[1] if len(args) > 1 else kwargs.get('request_id')
            if (base.attrs(owner).get(specification['processor_attribute']) is not processor
                    or caller.f_locals.get('request_id') != request_id or digest('native-request', request_id) is None):
                return None
            matches = [row for row in context['_binding_native_selections'] if row['engine_id'] == id(owner)
                       and row['native_request_sha256'] == digest('native-request', request_id)]
            if len(matches) != 1:
                return None
            return {'context': context, 'kind': 'input', 'selected': matches[0],
                'native_request_sha256': digest('native-request', request_id),
                'native_call': digest('native-call', self.route_nonce('binding-native'))}

        def snapshot(self, point, args, kwargs, result=None, returned=False):
            output = super().snapshot(point, args, kwargs, result, returned)
            call = calls.get()
            if point not in {specification['selection_point'], specification['native_selection_point'], specification['input_point']} or call is None:
                return output
            context = call['context']
            if self.selection_context() is not context:
                return output
            selected = call.get('selected')
            if call['kind'] == 'base' and returned:
                value = digest('selected-base', result)
                if value is None or len(context['_binding_selections']) >= base.MAX_IDS:
                    return output
                selected = {'engine_id': call['engine_id'], 'base_request_sha256': value,
                            'selection_call': call['selection_call']}
                context['_binding_selections'].append(selected)
            if call['kind'] == 'choice' and returned:
                if len(context['_binding_native_selections']) >= base.MAX_IDS:
                    return output
                context['_binding_native_selections'].append({**selected, 'engine_id': call['engine_id'],
                    'native_selection_call': call['native_selection_call'],
                    'native_request_sha256': call['native_request_sha256']})
            metadata = {'scheme': 'session-hmac-sha256', 'asgi_invocation_sha256': context['invocation'],
                'asgi_request_sha256': context['request'],
                'selection_call_sha256': call['selection_call'] if call['kind'] == 'base' else selected['selection_call'],
                'native_selection_call_sha256': (call.get('native_selection_call') if call['kind'] != 'input'
                    else selected['native_selection_call']),
                'native_call_sha256': call.get('native_call'),
                'base_request_sha256': selected['base_request_sha256'] if selected is not None else None,
                'native_request_sha256': call.get('native_request_sha256'),
                'caller_frame_verified': True, 'native_owner_verified': True,
                'producer_authenticated': False, 'worker_connection_verified': False}
            return {**output, 'metadata': {**output['metadata'], 'request_binding': metadata}}

        def wanted(self, point, snapshot):
            return True if 'request_binding' in snapshot['metadata'] else super().wanted(point, snapshot)

        def wrap(self, module_name, point, original):
            if point in {specification['handler_point'], specification['caller_point']}:
                originals[point] = original
            wrapped = super().wrap(module_name, point, original)
            if point not in {specification['selection_point'], specification['native_selection_point'], specification['input_point']}:
                return wrapped
            @functools.wraps(original)
            def observed(*args, **kwargs):
                try:
                    current = self._fresh(point, args, kwargs, sys._getframe(1)) if self.active() else None
                except BaseException:
                    current = None
                token = calls.set(current)
                try:
                    return wrapped(*args, **kwargs)
                finally:
                    calls.reset(token)
            return observed

    base.Observer = FreshObserver
    return base
