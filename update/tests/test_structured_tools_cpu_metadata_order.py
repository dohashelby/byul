"""Capture metadata must be available before executing reference cases."""
from types import SimpleNamespace
import importlib.metadata
import subprocess

import pytest

from guard.executors import structured_tools_cpu as producer


@pytest.fixture
def capture_fixture(tmp_path, monkeypatch):
    root = tmp_path / 'reference'
    package = root / 'vllm'
    package.mkdir(parents=True)
    events = []
    modules = {}
    names = ('vllm.entrypoints.openai.chat_completion.protocol', 'vllm.v1.structured_output.backend_xgrammar',
             'vllm.sampling_params', 'vllm.exceptions')
    for index, name in enumerate(names):
        path = package / f'module_{index}.py'
        path.write_text('EXAMPLE_MODULE = True\n')
        modules[name] = SimpleNamespace(__file__=str(path))
    def fixture_function():
        pass
    modules[names[0]].ChatCompletionRequest = SimpleNamespace(
        check_structured_outputs_count=fixture_function, to_sampling_params=fixture_function)
    modules[names[1]].validate_xgrammar_grammar = fixture_function
    modules[names[3]].VLLMValidationError = ValueError
    modules['xgrammar'] = SimpleNamespace(Grammar=SimpleNamespace(from_json_schema=fixture_function))
    modules['torch'] = SimpleNamespace(version=SimpleNamespace(cuda=None))
    monkeypatch.setattr(producer.importlib, 'import_module', lambda name: modules[name])
    monkeypatch.setattr(producer.inspect, 'getsourcefile', lambda function: str(package / 'module_0.py'))
    monkeypatch.setattr(producer, '_verify_source', lambda source: events.append('source_check'))
    def describe(*args, **kwargs):
        events.append('describe')
        return 'EXAMPLE-reference\n'
    monkeypatch.setattr(producer.subprocess, 'check_output', describe)
    def version(name):
        events.append('version:' + name)
        return 'EXAMPLE-1'
    monkeypatch.setattr(producer.importlib.metadata, 'version', version)
    rows = [{'case_id': 'named-json', 'request_accepted': True, 'outcome': 'execution_error', 'events': []}]
    def run(*args):
        events.append('run')
        return rows
    monkeypatch.setattr(producer, '_run', run)
    return root, modules, events, rows


@pytest.mark.parametrize('failure', ['describe', 'dependency_version', 'producer_hash', 'source_hash'])
def test_required_metadata_failure_prevents_reference_execution(capture_fixture, monkeypatch, failure):
    root, modules, events, _ = capture_fixture
    if failure == 'describe':
        def unavailable(*args, **kwargs):
            raise subprocess.CalledProcessError(1, ['git', 'describe'])
        monkeypatch.setattr(producer.subprocess, 'check_output', unavailable)
        error = subprocess.CalledProcessError
    elif failure == 'dependency_version':
        def unavailable(name):
            raise importlib.metadata.PackageNotFoundError(name)
        monkeypatch.setattr(producer.importlib.metadata, 'version', unavailable)
        error = importlib.metadata.PackageNotFoundError
    elif failure == 'producer_hash':
        monkeypatch.setattr(producer, '__file__', str(root / 'missing_producer.py'))
        error = FileNotFoundError
    else:
        next(iter(modules.values())).__file__ = str(root / 'vllm/missing_module.py')
        error = FileNotFoundError
    with pytest.raises(error):
        producer.capture_reference(root, model='EXAMPLE-model')
    assert 'run' not in events
    assert events.count('source_check') == 1


def test_metadata_is_complete_before_run_and_source_recheck_preserves_rows(capture_fixture):
    root, _, events, rows = capture_fixture
    record = producer.capture_reference(root, model='EXAMPLE-model')
    assert events == ['source_check', 'describe', 'version:torch', 'version:xgrammar', 'version:pydantic', 'run', 'source_check']
    assert record['rows'] is rows
    assert record['dependencies'] == {'torch': 'EXAMPLE-1', 'xgrammar': 'EXAMPLE-1', 'pydantic': 'EXAMPLE-1'}
    assert len(record['source_file_bytes_sha256']) == 4
    assert record['source_clean_verified'] is True


def test_postrun_source_mismatch_still_rejects_capture(capture_fixture, monkeypatch):
    root, _, events, _ = capture_fixture
    def verify(source):
        events.append('source_check')
        if 'run' in events:
            raise ValueError('STRUCTURED_TOOLS_REFERENCE_MISMATCH')
    monkeypatch.setattr(producer, '_verify_source', verify)
    with pytest.raises(ValueError, match='^STRUCTURED_TOOLS_REFERENCE_MISMATCH$'):
        producer.capture_reference(root, model='EXAMPLE-model')
    assert events[-2:] == ['run', 'source_check']
