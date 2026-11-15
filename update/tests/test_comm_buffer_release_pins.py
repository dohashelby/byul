"""REQ-21 통신 버퍼 관측기의 판본별 고정값(M-A6): 같은 관측 코드가 판본별 원 소스 bytes를 받는다.

`tests/fixtures/vllm-<판본>/custom_all_reduce.py.txt`는 MVP 두 판본 태그 소스의 실제 bytes다.
고정값은 이미지 설치 파일과 대조하기 전까지 후보(`PIN_ROWS`의 `candidate`)다.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_cache_callback as cache
from guard.adapters import vllm_comm_buffer_callback as cb
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.models import sha256_hex

FIXTURES = Path(__file__).parent / 'fixtures'
RELEASES = sorted(p.parent.name for p in FIXTURES.glob('vllm-*/custom_all_reduce.py.txt'))
SECRET = bytes(range(32))


def _module(raw, folder):
    path = folder / 'custom_all_reduce.py'; path.write_bytes(raw)
    module = ModuleType(cb.MODULE); module.__file__ = str(path)
    codes = {}
    def visit(code):
        codes[code.co_qualname] = code
        for child in code.co_consts:
            if type(child) is CodeType: visit(child)
    visit(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    for point in cb.TARGETS[cb.MODULE]:
        owner, method = point.split('.')
        cls = vars(module).setdefault(owner, type(owner, (), {'__module__': cb.MODULE}))
        setattr(cls, method, FunctionType(codes[point], vars(module)))
    return module


class Ops:
    def __init__(self): self.disposed = []
    def get_graph_buffer_ipc_meta(self, ptr): return [1], [0]
    def register_graph_buffers(self, ptr, handles, offsets): pass
    def dispose(self, ptr): self.disposed.append(ptr)


def _globals(ops, world):
    dist = SimpleNamespace(get_world_size=lambda group=None: world, get_process_group_ranks=lambda group: list(range(world)),
                           broadcast_object_list=lambda *a, **k: None)
    logger = SimpleNamespace(info=lambda *a: None, debug=lambda *a: None, warning=lambda *a: None, info_once=lambda *a: None)
    return {'dist': dist, 'ops': ops, 'logger': logger, 'cast': lambda t, v: v}


@pytest.mark.parametrize('release', RELEASES)
def test_fixture_is_a_reviewed_release_row(release):
    folder = FIXTURES / release
    raw = (folder / 'custom_all_reduce.py.txt').read_bytes()
    provenance = json.loads((folder / 'PROVENANCE.json').read_text())
    digest = sha256_hex(raw)
    assert provenance['files']['custom_all_reduce.py.txt']['sha256'] == digest
    assert cb.PIN_ROWS[digest] == 'candidate'  # 이미지 bytes 대조 전
    assert digest in cache._pins(cb.SOURCES, cb.MODULE)
    assert 'Apache License' in (folder / 'LICENSE.txt').read_text()


@pytest.mark.parametrize('release', RELEASES)
def test_same_observer_wraps_each_release_and_records_its_pin(release, tmp_path):
    raw = (FIXTURES / release / 'custom_all_reduce.py.txt').read_bytes()
    module = _module(raw, tmp_path)
    ops = Ops(); vars(module).update(_globals(ops, 2))
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'process': 'p', 'rank': 0},
                                       kernel_checks=True)
    assert callbacks.instrument(module)
    comm = object.__new__(module.CustomAllreduce)
    comm.__dict__.update(disabled=False, _ptr=0x7f0000001000, rank=0, world_size=2, group=object(), _IS_CAPTURING=False,
                         buffer_ptrs=[0x7f0000100000, 0x7f0000200000], meta_ptrs=[1, 2], free_shared_buffer=lambda *a, **k: None)
    comm.register_graph_buffers()
    comm.close()
    assert ops.disposed == [0x7f0000001000]
    points = [(r['point'], r['phase']) for r in rows if r.get('schema') == cb.SCHEMA]
    assert ('CustomAllreduce.register_graph_buffers', 'RETURN') in points and ('CustomAllreduce.close', 'RETURN') in points
    for row in (r for r in rows if r.get('schema') == cb.SCHEMA):
        assert row['source_file_sha256'] == sha256_hex(raw) and row['entry_qualified_before'] is True
    closed = [r for r in rows if r.get('point') == 'CustomAllreduce.close' and r['phase'] == 'CALL'][0]['fields']
    assert closed['live'] is True and closed['rank'] == 0 and closed['world_size'] == 2 and closed['buffer_sha256']
    assert '0x7f' not in str(rows)


def test_bytes_outside_every_release_row_are_not_wrapped(tmp_path):
    raw = (FIXTURES / RELEASES[0] / 'custom_all_reduce.py.txt').read_bytes() + b'\n# changed\n'
    module = _module(raw, tmp_path)
    observer = cb.CommBufferObserver('a' * 32, context_provider=lambda: {'process': 'p', 'rank': 0})
    with pytest.raises(ValueError, match='WORKER_STARTUP_SOURCE_UNAVAILABLE'):
        observer.wrap(module, 'CustomAllreduce.close')


def test_release_rows_do_not_cross_qualify_entry_code(tmp_path):
    """한 판본 파일의 함수 코드를 다른 판본 파일 위에 올리면 감싸지 않는다(파일·함수 지문을 함께 본다)."""
    first, second = (FIXTURES / r / 'custom_all_reduce.py.txt' for r in RELEASES[:2])
    (tmp_path / 'a').mkdir(); (tmp_path / 'b').mkdir()
    module = _module(first.read_bytes(), tmp_path / 'a')
    other = _module(second.read_bytes(), tmp_path / 'b')
    module.CustomAllreduce.close = FunctionType(other.CustomAllreduce.close.__code__, vars(module))
    with pytest.raises(ValueError, match='CACHE_CALLBACK_ENTRY_UNSUPPORTED'):
        cb.CommBufferObserver('a' * 32, context_provider=lambda: {'process': 'p', 'rank': 0}).wrap(module, 'CustomAllreduce.close')


def test_pin_table_shape_is_strict():
    with pytest.raises(ValueError, match='CACHE_CALLBACK_SOURCE_UNSUPPORTED'):
        cache._pins({cb.MODULE: ('x',)}, cb.MODULE)
    with pytest.raises(ValueError, match='CACHE_CALLBACK_SOURCE_UNSUPPORTED'):
        cache._pins({cb.MODULE: ()}, cb.MODULE)
    pin = next(iter(cb.PIN_ROWS))
    with pytest.raises(ValueError, match='CACHE_CALLBACK_SOURCE_UNSUPPORTED'):
        cache._pins({cb.MODULE: (pin, pin)}, cb.MODULE)
    assert cache._pins({cb.MODULE: pin}, cb.MODULE) == (pin,)
