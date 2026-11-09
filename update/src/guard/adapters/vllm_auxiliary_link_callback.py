"""Tie real auxiliary FD reads to config/tokenizer loader returns during engine boot."""
from __future__ import annotations

import contextvars
import functools
import types
import uuid

from . import vllm_auxiliary_read as read
from . import vllm_cache_callback as cache
from .vllm_execution_link_callback import config_digest

SCHEMA = 'guard-vllm-auxiliary-links/1'
CONFIG = 'vllm.transformers_utils.config'
CONFIG_SHA = 'bfa2d8ed9fc0971694e7873351a06dc9d5948775984f120148ad8a8e38a5acbc'
TOKENIZER = 'vllm.tokenizers.hf'
TOKENIZER_SHA = '36314bc67ab94552f6d99acb55cdf66045ce5d92553b06f52b4b3ebc7de6f641'
MODULES = (*read.SOURCES, CONFIG, TOKENIZER)
_ACTIVE = contextvars.ContextVar('guard_auxiliary_loader_call', default=None)


class AuxiliaryLinkObserver(read.AuxiliaryReadObserver):
    def __init__(self, roots, *, session, link, sink):
        super().__init__(roots, session=session)
        self.link, self.sink = link, sink
        self.loaders = []

    def _qualify(self, module, code):
        name = vars(module).get('__name__')
        if name in (CONFIG, TOKENIZER):
            try:
                point = 'get_config' if name == CONFIG else 'get_cached_tokenizer'
                expected = cache._codes(module, {name: (point,)}, {CONFIG: CONFIG_SHA, TOKENIZER: TOKENIZER_SHA}).get(point)
                return cache._digest(code, name) == expected
            except Exception:
                return False
        return super()._qualify(module, code)

    def _emit(self, row):
        active = _ACTIVE.get()
        try:
            self.sink({'schema': SCHEMA, 'session': self.session, 'pid': self.pid, 'start_ticks': self.start_ticks,
                       'process_sha256': self.link.process_sha256,
                       'loader_call': active['call'] if active and active['observer'] is self else None, **row})
        except BaseException:
            self.gaps.add('AUXILIARY_LINK_SINK_UNAVAILABLE')

    def _wrap(self, module, original, role):
        observer = self
        if type(original) is not types.FunctionType or not self._qualify(module, original.__code__):
            raise ValueError('AUXILIARY_LINK_LOADER_UNSUPPORTED')
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            call = uuid.uuid4().hex
            before = observer._qualify(module, original.__code__)
            input_tokenizer = observer.link.token(args[0] if args else kwargs.get('tokenizer')) if role == 'tokenizer_cache' else None
            token = _ACTIVE.set({'observer': observer, 'call': call})
            try:
                result = original(*args, **kwargs)
                after = observer._qualify(module, original.__code__)
                try:
                    fields = {'role': role, 'source_sha256': {**read.SOURCES, CONFIG: CONFIG_SHA, TOKENIZER: TOKENIZER_SHA}[vars(module)['__name__']],
                              'point': original.__qualname__, 'qualified_before': before, 'qualified_after': after,
                              'config_sha256': config_digest(result) if role == 'config' else None,
                              'tokenizer_sha256': observer.link.token(result) if role in ('tokenizer', 'tokenizer_cache') else None}
                    if role == 'tokenizer_cache':
                        fields['input_tokenizer_sha256'] = input_tokenizer
                    observer._emit({'phase': 'LOADER_RETURN', **fields})
                except Exception:
                    observer.gaps.add('AUXILIARY_LINK_TERMINAL_UNAVAILABLE')
                return result
            except BaseException:
                try:
                    observer._emit({'phase': 'LOADER_RAISE', 'role': role})
                except BaseException:
                    observer.gaps.add('AUXILIARY_LINK_TERMINAL_UNAVAILABLE')
                raise
            finally:
                _ACTIVE.reset(token)
        return wrapped

    def install(self, module):
        name = vars(module).get('__name__')
        if name in (CONFIG, TOKENIZER):
            point, role = ('get_config', 'config') if name == CONFIG else ('get_cached_tokenizer', 'tokenizer_cache')
            original = vars(module).get(point)
            wrapped = self._wrap(module, original, role)
            setattr(module, point, wrapped)
            self.loaders.append((module, point, original, wrapped))
        else:
            super().install(module)
            if name == 'transformers.tokenization_utils_base':
                cls = module.PreTrainedTokenizerBase
                descriptor = vars(cls)['_from_pretrained']
                if type(descriptor) is not classmethod:
                    raise ValueError('AUXILIARY_LINK_TOKENIZER_UNSUPPORTED')
                wrapped = classmethod(self._wrap(module, descriptor.__func__, 'tokenizer'))
                cls._from_pretrained = wrapped
                self.loaders.append((cls, '_from_pretrained', descriptor, wrapped))
        return True
