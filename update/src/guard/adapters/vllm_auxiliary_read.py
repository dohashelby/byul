"""SUP-03 exact Python auxiliary loader reads, with no native/worker admission claim.

Only the module-local open of pinned loader code is observed. The original read
value and original exceptions are returned unchanged. File bytes and parser
input bytes have separate hashes (text newline translation can change them).
No global builtins hook, alternate parser, or supply denial is installed.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import marshal
import os
from pathlib import Path
import stat
import sys
import types
import uuid

SCHEMA = 'guard-auxiliary-read/1'
SOURCES = {
    'transformers.configuration_utils': '02826ac9e987487b645135d8f1d747f60a7e567d1b8f72327de8514d02b6f15d',
    'transformers.tokenization_utils_base': 'e7eb23b77be31b251570936ca74b2a3f2c04f1a44a13aa079ac4957535916a4b',
    'vllm.entrypoints.chat_utils': '4d442ec6bc7783f2ed37e7400390631a5f39c27e3a48add89991d1669fef13bd',
}
POINTS = {
    'transformers.configuration_utils': {'PreTrainedConfig._dict_from_json_file': 'config'},
    'transformers.tokenization_utils_base': {'PreTrainedTokenizerBase._from_pretrained': 'tokenizer'},
    'vllm.entrypoints.chat_utils': {'_load_chat_template': 'template'},
}
MAX_READ_BYTES = 2 * 1024**2
MAX_EVENTS = 256


def sha(value):
    return hashlib.sha256(value).hexdigest()


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _start_ticks():
    try:
        raw = Path('/proc/self/stat').read_text()
        return int(raw[raw.rfind(')') + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


class AuxiliaryReadObserver:
    def __init__(self, roots, *, session):
        if (type(roots) is not list or not roots or len(roots) > 8
                or any(type(p) is not str or not p.startswith('/') or os.path.normpath(p) != p for p in roots)):
            raise ValueError('AUXILIARY_ROOTS_INVALID')
        if type(session) is not str or len(session) != 32 or any(c not in '0123456789abcdef' for c in session):
            raise ValueError('AUXILIARY_SESSION_INVALID')
        self.roots, self.session = list(roots), session
        self.events, self.gaps, self.installed = [], set(), []
        self.pid, self.start_ticks = os.getpid(), _start_ticks()

    def _emit(self, row):
        if len(self.events) >= MAX_EVENTS:
            self.gaps.add('AUXILIARY_EVENT_BOUND'); return
        self.events.append({'schema': SCHEMA, 'session': self.session, 'pid': self.pid, 'start_ticks': self.start_ticks, **row})

    def _qualify(self, module, code):
        name = vars(module).get('__name__')
        if sys.version_info[:2] != (3, 12) or sys.flags.optimize or name not in SOURCES:
            return False
        try:
            path = vars(module)['__file__']
            with builtins.open(path, 'rb') as stream:
                raw = stream.read(1024**2 + 1)
            if sha(raw) != SOURCES[name]:
                return False
            expected = {}
            def visit(item):
                expected[item.co_qualname] = item
                for child in item.co_consts:
                    if type(child) is types.CodeType:
                        visit(child)
            visit(compile(raw, path, 'exec', dont_inherit=True, optimize=0))
            return code == expected.get(code.co_qualname) and code.co_qualname in POINTS[name]
        except (OSError, ValueError, KeyError, TypeError, SyntaxError):
            return False

    def install(self, module):
        name = vars(module).get('__name__')
        if name not in SOURCES or vars(module).get('open', builtins.open) is not builtins.open:
            raise ValueError('AUXILIARY_MODULE_UNSUPPORTED')
        observer = self
        def observed_open(*args, **kwargs):
            frame = sys._getframe(1)
            point = frame.f_code.co_qualname
            if point not in POINTS[name]:
                return builtins.open(*args, **kwargs)
            if frame.f_globals is not vars(module) or not observer._qualify(module, frame.f_code):
                observer.gaps.add('AUXILIARY_SOURCE_UNQUALIFIED')
                return builtins.open(*args, **kwargs)
            try:
                path = os.fspath(args[0]) if args else os.fspath(kwargs.get('file'))
            except TypeError:
                return builtins.open(*args, **kwargs)
            if type(path) is not str or not path.startswith('/'):
                return builtins.open(*args, **kwargs)
            path = os.path.normpath(path)
            root = next((i for i, base in enumerate(observer.roots)
                         if path == base or path.startswith(base.rstrip('/') + '/')), None)
            if root is None:
                return builtins.open(*args, **kwargs)
            common = {'call': uuid.uuid4().hex, 'root': root, 'path_sha256': sha(path.encode()),
                      'role': POINTS[name][point], 'point': point, 'source_sha256': SOURCES[name],
                      'entry_sha256': sha(marshal.dumps(frame.f_code))}
            try:
                stream = builtins.open(*args, **kwargs)
            except BaseException:
                observer._emit({**common, 'phase': 'OPEN_RAISE'}); raise
            if not stream.readable():
                return stream
            return _ReadProxy(observer, module, frame.f_code, path, stream, common)
        module.open = observed_open
        self.installed.append((module, observed_open))

    def restore(self):
        for module, wrapped in reversed(self.installed):
            if vars(module).get('open') is wrapped:
                del module.open
        self.installed.clear()


class _ReadProxy:
    def __init__(self, observer, module, code, path, stream, common):
        self.observer, self.module, self.code = observer, module, code
        self.path, self.stream, self.common = path, stream, common

    def __getattr__(self, key):
        return getattr(self.stream, key)

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, *args, **kwargs):
        try:
            before = os.fstat(self.stream.fileno())
        except Exception:
            self.observer.gaps.add('AUXILIARY_OBSERVATION_UNAVAILABLE')
            return self.stream.read(*args, **kwargs)
        try:
            result = self.stream.read(*args, **kwargs)
        except BaseException:
            self.observer._emit({**self.common, 'phase': 'READ_RAISE'}); raise
        # Observation errors never replace an original successful read.
        try:
            after = os.fstat(self.stream.fileno())
            path = os.stat(self.path, follow_symlinks=False)
            qualified = self.observer._qualify(self.module, self.code)
            stable = stat.S_ISREG(before.st_mode) and _stamp(before) == _stamp(after) == _stamp(path)
            raw = os.pread(self.stream.fileno(), MAX_READ_BYTES + 1, 0) if stable and before.st_size <= MAX_READ_BYTES else None
            returned = result.encode('utf-8') if type(result) is str else result if type(result) is bytes else None
            stable = stable and _stamp(after) == _stamp(os.fstat(self.stream.fileno()))
            complete = qualified and stable and raw is not None and len(raw) == before.st_size and not args and not kwargs
            self.observer._emit({**self.common, 'phase': 'READ_RETURN', 'stable': stable, 'qualified': qualified,
                                 'complete': complete, 'file_bytes': before.st_size,
                                 'file_sha256': sha(raw) if raw is not None else None,
                                 'parser_input_bytes': len(returned) if returned is not None else None,
                                 'parser_input_sha256': sha(returned) if returned is not None else None})
            if not complete:
                self.observer.gaps.add('AUXILIARY_READ_INCOMPLETE')
        except BaseException:
            self.observer.gaps.add('AUXILIARY_OBSERVATION_UNAVAILABLE')
        return result
