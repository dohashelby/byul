"""정확 소스 후보 대조의 실행 금지·본문/신원/형식 거부를 검증한다."""
from hashlib import sha256
import json
from pathlib import Path
import sys
import types

import pytest

from guard.adapters import vllm_rank_ipc_sources as sources

RAW = b'''calls = []
class Worker:
    def bind(self, value):
        calls.append(value)
        return value + 1
    @staticmethod
    def attach(value):
        return value
'''


def _fixture(tmp_path, monkeypatch, raw=RAW):
    path = tmp_path / 'fixture.py'
    path.write_bytes(raw)
    module = types.ModuleType('fixture')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), vars(module))
    profiles = tmp_path / 'profiles.json'
    names = ['Worker.bind', 'Worker.attach']
    codes = sources._codes(compile(raw, 'fixture.py', 'exec', dont_inherit=True, optimize=0))
    expected = {'path': 'fixture.py', 'bytes': len(raw), 'sha256': sha256(raw).hexdigest(),
                'functions': names,
                'body_code_sha256': {name: sources.code_digest(codes[name]) for name in names}}
    profiles.write_text(json.dumps({'schema': 'guard-rank-ipc-source-candidates/1',
        'profiles': [{'id': 'EXAMPLE', 'image': 'fixture-image',
                      'runtime': sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3])),
                      'sources': {'fixture': expected}}]}))
    monkeypatch.setattr(sources, 'PROFILES', profiles)
    return module, path


def test_exact_source_and_body_are_candidates_without_calling_or_wrapping(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    before = vars(module.Worker)['bind']
    result = sources.qualify(module, image='fixture-image')
    assert module.calls == [] and vars(module.Worker)['bind'] is before
    assert result['functions']['Worker.bind'] is before
    assert result['pin_status'] == 'candidate_pin'
    assert result['engine_group_verified'] is result['observer_installed'] is result['producer_registered'] is False
    assert len(result['body_code_sha256']) == 2


def test_standard_abstract_class_metaclass_keeps_exact_body_checks(tmp_path, monkeypatch):
    raw = b'from abc import ABC\n' + RAW.replace(b'class Worker:', b'class Worker(ABC):')
    module, _ = _fixture(tmp_path, monkeypatch, raw)
    result = sources.qualify(module, image='fixture-image')
    assert result['functions']['Worker.bind'] is vars(module.Worker)['bind']
    assert module.calls == [] and result['engine_group_verified'] is False


def test_custom_metaclass_dictionary_property_is_not_executed(tmp_path, monkeypatch):
    module, _ = _fixture(tmp_path, monkeypatch)
    class TrapMeta(type):
        @property
        def __dict__(cls):
            raise AssertionError('metaclass property를 호출했다')
    module.Worker = TrapMeta('Worker', (), {})
    with pytest.raises(ValueError, match='RANK_IPC_SOURCE_UNAVAILABLE'):
        sources.qualify(module, image='fixture-image')
    assert module.calls == []


@pytest.mark.parametrize('change', ['bytes', 'function', 'namespace', 'property', 'image', 'symlink',
                                   'runtime', 'body_pin', 'module_getattr'])
def test_changed_source_body_or_binding_is_rejected(tmp_path, monkeypatch, change):
    module, path = _fixture(tmp_path, monkeypatch)
    image = 'fixture-image'
    if change == 'bytes':
        path.write_bytes(RAW.replace(b'value + 1', b'value + 2'))
    elif change == 'function':
        exec(compile('def altered(self, value): return value + 2', str(path), 'exec'), vars(module))
        module.Worker.bind = module.altered
    elif change == 'namespace':
        other = types.ModuleType('other')
        exec(compile(RAW, str(path), 'exec'), vars(other))
        module.Worker = other.Worker
    elif change == 'property':
        def forbidden(self):
            raise AssertionError('property를 호출했다')
        module.Worker.bind = property(forbidden)
    elif change == 'image':
        image = 'another-image'
    elif change == 'module_getattr':
        del module.__name__
        def forbidden(name):
            raise AssertionError('module __getattr__를 호출했다')
        module.__getattr__ = forbidden
    elif change in ('runtime', 'body_pin'):
        data = json.loads(sources.PROFILES.read_bytes())
        if change == 'runtime':
            data['profiles'][0]['runtime'] = 'unsupported-runtime'
        else:
            data['profiles'][0]['sources']['fixture']['body_code_sha256']['Worker.bind'] = '0' * 64
        sources.PROFILES.write_text(json.dumps(data))
    else:
        alias = tmp_path / 'alias.py'
        alias.symlink_to(path)
        module.__file__ = str(alias)
    with pytest.raises(ValueError, match='RANK_IPC_SOURCE_UNAVAILABLE'):
        sources.qualify(module, image=image)
    assert module.calls == []


def test_profile_covers_each_exact_mvp_image_without_registering_it():
    from tools.mvp_versions import versions

    data = json.loads(sources.PROFILES.read_bytes())
    assert data['schema'] == 'guard-rank-ipc-source-candidates/1'
    assert len(data['profiles']) == 2
    assert {row['image'] for row in data['profiles']} == {row['image'] for row in versions().values()}
    assert {row['id'] for row in data['profiles']} == {'v0-task-queue', 'v1-shm-broadcast'}
    for row in data['profiles']:
        assert row['pin_status'] == 'candidate_pin'
        assert row['engine_group_verified'] is row['observer_installed'] is row['producer_registered'] is False
        for source in row['sources'].values():
            assert source['bytes'] > 0 and len(source['sha256']) == 64 and source['functions']
            assert set(source['body_code_sha256']) == set(source['functions'])
