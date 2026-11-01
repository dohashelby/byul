"""B-only observational snapshots of source-qualified OnlineRenderer calls.

The caller owns hook installation, source/process identity and event transport.
This module does not register hooks, write files or decide verdicts.
"""
from __future__ import annotations

import builtins
import hashlib
import inspect
import json
import math
import types

SCHEMA = 'byul-online-input-snapshot/1'
POINTS = frozenset({
    'OnlineRenderer.render_chat', 'OnlineRenderer.preprocess_chat',
    'OnlineRenderer.validate_chat_template', 'online_renderer._reused_prompt_token_ids',
    'protocol.validate_request_mm_kwargs', 'InputProcessor.process_inputs',
    'InputProcessor._validate_model_input', 'InputProcessor._validate_prompt_len',
})
PHASES = frozenset({'CALL', 'RETURN', 'RAISE'})
FIELDS = ('chat_template', 'chat_template_kwargs', 'mm_processor_kwargs',
          'media_io_kwargs', 'echo', 'kv_transfer_params')
BINDING_HASHES = ('asgi_request_id_sha256', 'native_request_id_sha256',
                  'call_id_sha256', 'session_id_sha256', 'process_start_sha256')
# Technical observation bounds only: exceeding them produces unavailable data.
MAX_IDS = 262144
MAX_DOMAIN_IDS = 262144
MAX_FIELD_BYTES = 65536
MAX_FIELD_NODES = 4096
MAX_FIELD_DEPTH = 12
MAX_RENDERED_INPUTS = 8
_MISSING = object()
_EXCEPTIONS = frozenset(name for name, value in vars(builtins).items()
                        if isinstance(value, type) and issubclass(value, Exception)) | {'VLLMValidationError'}


def _get(value, name, default=None):
    """Read fields/slots; do not invoke arbitrary properties or custom repr."""
    if type(value) is dict:
        return value.get(name, default)
    try:
        fields = object.__getattribute__(value, '__dict__')
    except (AttributeError, TypeError):
        fields = {}
    if type(fields) is dict and name in fields:
        return fields[name]
    descriptor = inspect.getattr_static(type(value), name, _MISSING)
    if descriptor is None or type(descriptor) in (bool, int, float, str):
        return descriptor
    if type(descriptor) is types.MemberDescriptorType:
        try:
            return descriptor.__get__(value, type(value))
        except (AttributeError, TypeError):
            pass
    return default


def _arg(args, kwargs, index, name, default=None):
    return kwargs[name] if name in kwargs else args[index] if len(args) > index else default


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _canonical(value):
    remaining = [MAX_FIELD_NODES]
    byte_budget = [MAX_FIELD_BYTES]
    def walk(item, depth):
        remaining[0] -= 1
        if depth > MAX_FIELD_DEPTH or remaining[0] < 0:
            raise ValueError
        if item is None or type(item) in (bool, int) or type(item) is float and math.isfinite(item):
            if type(item) is int and item.bit_length() > MAX_FIELD_BYTES:
                raise ValueError
            byte_budget[0] -= len(json.dumps(item, allow_nan=False))
            if byte_budget[0] < 0:raise ValueError
            return item
        if type(item) is str and len(item) <= MAX_FIELD_BYTES:
            byte_budget[0] -= len(json.dumps(item, ensure_ascii=True))
            if byte_budget[0] < 0:raise ValueError
            return item
        if type(item) in (list, tuple) and len(item) <= MAX_FIELD_NODES:
            return [walk(child, depth + 1) for child in item]
        if type(item) is dict and len(item) <= MAX_FIELD_NODES and all(type(key) is str for key in item):
            return {walk(key, depth + 1): walk(child, depth + 1) for key, child in item.items()}
        raise ValueError
    converted = walk(value, 0)
    raw = json.dumps(converted, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > MAX_FIELD_BYTES:
        raise ValueError
    return hashlib.sha256(raw).hexdigest()


def _field(value, present, explicit=None):
    kinds = {type(None): 'null', bool: 'bool', int: 'int', float: 'float', str: 'str',
             list: 'list', tuple: 'list', dict: 'dict'}
    result = {'present': present, 'explicitly_set': explicit,
              'kind': kinds.get(type(value), 'unsupported'), 'status': 'unavailable'}
    if present:
        try:
            result.update(status='collected', canonical_sha256=_canonical(value))
        except Exception:
            pass
    return result


def _fields(request):
    explicit = _get(request, '__pydantic_fields_set__', None)
    return {name: _field(value if value is not _MISSING else None, value is not _MISSING,
                         name in explicit if type(explicit) is set else None)
            for name in FIELDS for value in [_get(request, name, _MISSING)]}


def _ids(value):
    # Do not consume generators, tensors, custom sequences or subclasses.
    if type(value) not in (list, tuple) or len(value) > MAX_IDS:
        return {'status': 'unavailable'}
    try:
        if any(type(item) not in (int, bool) or type(item) is int and item.bit_length()>256 for item in value):
            return {'status': 'unavailable'}
        integers = all(type(item) is int for item in value)
        return {'status': 'collected', 'count': len(value), 'canonical_sha256': _hash(list(value)),
                'integer_ids': integers, 'min': min(value) if value and integers else None,
                'max': max(value) if value and integers else None}
    except Exception:
        return {'status': 'unavailable'}


def _small_int(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def _bool(value):
    return value if type(value) is bool else None


def _sha(value):
    return type(value) is str and len(value) == 64 and all(char in '0123456789abcdef' for char in value)


def _binding(value):
    if type(value) is not dict:
        return {}
    result = {key: value[key] for key in BINDING_HASHES if _sha(value.get(key))}
    if type(value.get('pid')) is int and 0 < value['pid'] < 2**63:
        result['pid'] = value['pid']
    return result


def _request_id(value):
    if type(value) is str and 0 < len(value) <= 512:
        return hashlib.sha256(value.encode('utf-8', 'surrogatepass')).hexdigest()
    return None


def _context(owner):
    model = _get(owner, 'model_config')
    renderer = _get(owner, 'renderer')
    tokenizer = _get(renderer, 'tokenizer')
    runner_type = _get(model, 'runner_type')
    config = {'max_model_len': _small_int(_get(model, 'max_model_len')), 'model_vocab_size': None,
              'runner_type': runner_type if type(runner_type) is str and runner_type in ('generate', 'pooling', 'draft') else 'unavailable'}
    try:
        size = model.get_vocab_size()
        config['model_vocab_size'] = _small_int(size) if size > 0 else None
    except Exception:
        pass
    reported = None
    try:
        reported = _small_int(tokenizer.max_token_id)
    except Exception:
        pass
    return model, tokenizer, config, reported


def _domain(tokenizer, model_size, request_ids=None):
    unavailable = {'status': 'unavailable'}
    if model_size is None or tokenizer is None:
        return unavailable
    try:
        # get_vocab() includes actual added-token IDs. Token strings never leave
        # this function. The cached max_token_id is deliberately not consulted.
        vocab = tokenizer.get_vocab()
        if type(vocab) is not dict or not 0 < len(vocab) <= MAX_DOMAIN_IDS:
            return unavailable
        values = vocab.values()
        if any(type(item) is not int or item < 0 or item >= 2**63 for item in values):
            return unavailable
        ids = sorted(set(values))
        valid_request = type(request_ids) in (list, tuple) and len(request_ids) <= MAX_IDS
        members = set(ids)
        return {'status': 'collected', 'model_vocab_size': model_size,
                'tokenizer_id_count': len(ids), 'min': ids[0], 'max': ids[-1],
                'canonical_sha256': _hash(ids),
                'contiguous_from_zero': ids[0] == 0 and ids[-1] + 1 == len(ids),
                'request_ids_in_domain': all(type(v) is int and v in members for v in request_ids) if valid_request else None,
                'request_ids_within_model_vocab': all(type(v) is int and 0 <= v < model_size for v in request_ids) if valid_request else None,
                'request_ids_canonical_sha256': _ids(request_ids).get('canonical_sha256') if valid_request else None}
    except Exception:
        return unavailable


def _prompt_ids(prompt):
    return prompt.get('prompt_token_ids') if type(prompt) is dict else None


def _result_inputs(result):
    if type(result) not in (tuple, list) or len(result) != 2:
        return None
    inputs = result[1]
    if type(inputs) not in (tuple, list) or len(inputs) > MAX_RENDERED_INPUTS:
        return None
    return [_ids(_prompt_ids(prompt)) for prompt in inputs]


def snapshot_online_input(point, args, kwargs, *, phase, result=None, exception=None, binding=None):
    """Return only B metadata. Never mutate arguments, install hooks or raise.

    ``binding`` contains already hashed identities supplied by the A hook. Their
    authenticity and source/process qualification remain the caller's job.
    """
    output = {'schema': SCHEMA, 'point': point if type(point) is str and point in POINTS else 'unsupported',
              'phase': phase if type(phase) is str and phase in PHASES else 'unsupported',
              'status': 'unavailable', 'metadata': {}, 'binding': _binding(binding)}
    try:
        if point not in POINTS or phase not in PHASES or type(args) not in (tuple, list) or type(kwargs) is not dict:
            return output
        metadata = output['metadata']
        owner = args[0] if args else kwargs.get('self')
        model, tokenizer, config, reported = _context(owner)
        if model is not None:
            metadata.update(effective_config=config, reported_cache_max_token_id=reported,
                trust_request_chat_template=_bool(_get(owner, 'trust_request_chat_template')),
                trust_request_mm_kwargs=_bool(_get(owner, 'trust_request_mm_kwargs')))
        request_ids = None
        if point in {'OnlineRenderer.render_chat', 'OnlineRenderer.preprocess_chat'}:
            metadata['parsed_fields'] = _fields(_arg(args, kwargs, 1, 'request'))
        elif point == 'online_renderer._reused_prompt_token_ids':
            metadata['parsed_fields'] = _fields(_arg(args, kwargs, 0, 'request'))
        elif point == 'OnlineRenderer.validate_chat_template':
            values = {'chat_template': _arg(args, kwargs, 1, 'request_chat_template', _MISSING),
                      'chat_template_kwargs': _arg(args, kwargs, 2, 'chat_template_kwargs', _MISSING)}
            metadata['parsed_fields'] = {name: _field(value if value is not _MISSING else None, value is not _MISSING)
                                         for name, value in values.items()}
            metadata['trust_request_chat_template'] = _bool(_arg(args, kwargs, 3, 'trust_request_chat_template'))
        elif point == 'protocol.validate_request_mm_kwargs':
            metadata['parsed_fields'] = {name: _field(kwargs.get(name), name in kwargs)
                                         for name in ('mm_processor_kwargs', 'media_io_kwargs')}
            metadata['trust_request_mm_kwargs'] = _bool(kwargs.get('trust_request_mm_kwargs'))
        elif point == 'InputProcessor.process_inputs':
            prompt = _arg(args, kwargs, 2, 'prompt')
            request_ids = _prompt_ids(prompt)
            metadata['processor_input'] = _ids(request_ids)
            native_id = _request_id(_arg(args, kwargs, 1, 'request_id'))
            metadata['native_request_id_sha256'] = native_id
            provided = output['binding'].get('native_request_id_sha256')
            metadata['native_request_binding_matches'] = native_id == provided if native_id and provided else None
        elif point == 'InputProcessor._validate_model_input':
            request_ids = _prompt_ids(_arg(args, kwargs, 1, 'prompt_input'))
            metadata['processor_input'] = _ids(request_ids)
        elif point == 'InputProcessor._validate_prompt_len':
            metadata['candidate_prompt_count'] = _small_int(_arg(args, kwargs, 1, 'prompt_len'))
        if point in {'InputProcessor._validate_prompt_len', 'InputProcessor._validate_model_input'}:
            prompt_type = _arg(args, kwargs, 2, 'prompt_type')
            metadata['prompt_type'] = prompt_type if type(prompt_type) is str and prompt_type in ('encoder', 'decoder') else 'unavailable'
        if model is not None:
            metadata['token_id_domain'] = _domain(tokenizer, config['model_vocab_size'], request_ids)
        if phase == 'RETURN':
            metadata['return_kind'] = 'none' if result is None else 'other'
            code = _small_int(_get(_get(result, 'error'), 'code'))
            if code is not None:
                metadata.update(return_kind='error_response', native_error_code=code)
            elif point in {'OnlineRenderer.render_chat', 'OnlineRenderer.preprocess_chat'}:
                inputs = _result_inputs(result)
                if inputs is not None:
                    metadata.update(return_kind='rendered_inputs', rendered_inputs=inputs)
            elif point == 'online_renderer._reused_prompt_token_ids':
                metadata.update(return_kind='ids' if result is not None else 'none', reused=_ids(result))
            elif point == 'InputProcessor.process_inputs':
                admitted = _get(result, 'prompt_token_ids')
                metadata.update(return_kind='admitted', admitted=_ids(admitted))
                metadata['admitted_token_id_domain'] = _domain(tokenizer, config['model_vocab_size'], admitted)
        elif phase == 'RAISE':
            name = type(exception).__name__
            metadata['exception_type'] = name if name in _EXCEPTIONS else 'Exception'
        output['status'] = 'collected' if metadata else 'unavailable'
        if any(type(value) is dict and value.get('status') == 'unavailable' for value in metadata.values()):
            output['status'] = 'partial'
        return output
    except Exception:
        # Observation failure must never replace the serving call's exception.
        output['status'] = 'partial' if output['metadata'] else 'unavailable'
        output['diagnostic'] = 'SNAPSHOT_UNAVAILABLE'
        return output
