"""EXAMPLE profile contracts; actual target CPU execution is recorded separately."""
from types import SimpleNamespace
import json
import pytest
from guard.executors import structured_tools_cpu as producer
from guard.models import DataOrigin, ExecStatus, Verdict
from tests.builders import environment
from tests.test_sampling_validation import check
from tests.test_structured_tools import judge, ValidationRejected
from tests.test_structured_tools_cpu_metadata_order import capture_fixture


def target_env(**changes):
    values = {'model': {'id': 'fixture-model'}, 'engine': {'name': 'vllm', 'version': 'reference-' + producer.TARGET031_COMMIT}}
    values.update(changes)
    return environment(**values)


def rows():
    events = ['request_validator', 'sampling_conversion', 'backend_validator', 'json_conversion']
    return [{'case_id': k, 'outcome': 'rejected' if k == 'named-json' else 'accepted',
        'request_accepted': k != 'named-json', 'events': ['request_validator'] if k == 'named-json' else events,
        'after_normal_conversion': {'outcome': 'accepted', 'events': events}} for k in producer.CASE_IDS]


@pytest.mark.parametrize('name,version', [('xgrammar', '0.2.8'), ('torch', '2.15.0'),
    ('transformers', '5.10.3'), ('transformers', '5.18.0'), ('transformers', '5.18.0rc1')])
def test_dependency_mismatch_blocks_before_import(tmp_path, monkeypatch, name, version):
    versions = {'torch': '2.13.0', 'xgrammar': '0.2.7', 'transformers': '5.17.0'}
    versions[name] = version
    seen = []
    monkeypatch.setattr(producer, '_verify_source', lambda root, **kw: seen.append(kw))
    monkeypatch.setattr(producer.importlib.metadata, 'version', versions.__getitem__)
    monkeypatch.setattr(producer.importlib, 'import_module', lambda name: pytest.fail('import after rejected dependency'))
    with pytest.raises(ValueError, match='STRUCTURED_TOOLS_TARGET031_DEPENDENCY_MISMATCH'):
        producer.capture_target031(tmp_path, model='fixture-model')
    assert seen == [{'expected_commit': producer.TARGET031_COMMIT}]


def test_capture_pins_target_profile_dependencies_and_followups(capture_fixture, monkeypatch):
    root, _, _, _ = capture_fixture
    verified, invoked = [], []
    monkeypatch.setattr(producer, '_verify_source', lambda root, **kw: verified.append(kw))
    versions = {'torch': '2.13.0', 'xgrammar': '0.2.7', 'transformers': '5.17.0', 'pydantic': '2.14.1'}
    monkeypatch.setattr(producer.importlib.metadata, 'version', versions.__getitem__)
    monkeypatch.setattr(producer, '_run', lambda *a, **kw: invoked.append(kw) or rows())
    record = producer.capture_target031(root, model='fixture-model')
    assert verified == [{'expected_commit': producer.TARGET031_COMMIT}] * 2
    assert invoked == [{'followups': True}]
    assert record['reference_commit'] == 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
    assert record['profile'] == producer.TARGET031_PROFILE and record['dependencies'] == versions
    assert record['installed_target'] is None and not record['server_preprocessing_verified']
    assert not record['runner_consumption_verified']
    assert producer.SOURCE_COMMIT == '5d35ce3934a7804b298559d2d99eec33792e1eb9'


@pytest.mark.parametrize('changes', [{'model': {'id': 'other-model'}},
    {'engine': {'name': 'other-engine', 'version': 'reference-' + producer.TARGET031_COMMIT}},
    {'engine': {'name': 'vllm', 'version': '0.31.0'}},
    {'engine': {'name': 'vllm', 'version': 'reference-' + producer.SOURCE_COMMIT}}])
def test_environment_mismatch_prevents_capture(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(producer, 'capture_target031', lambda *a, **kw: pytest.fail('capture for wrong ENV'))
    executor = producer.StructuredToolsTarget031CPUExecutor({'source_root': str(tmp_path), 'model': 'fixture-model'})
    with pytest.raises(ValueError, match='STRUCTURED_TOOLS_TARGET031_ENVIRONMENT_MISMATCH'):
        executor.run(check(), target_env(**changes), {}, None, tmp_path)


def test_chain_executes_five_cases_and_five_normal_followups():
    calls = []
    class Request:
        @staticmethod
        def model_validate(body):
            calls.append(body)
            if isinstance(body['tool_choice'], dict) and 'structured_outputs' in body:
                raise ValidationRejected()
            return Request('structured_outputs' in body)
        def __init__(self, schema):
            self.schema = schema
        def to_sampling_params(self, **kwargs):
            assert kwargs == {'max_tokens': 2, 'default_sampling_params': {}}
            return SimpleNamespace(structured_outputs=SimpleNamespace(json={}) if self.schema else None)
    def conversion():
        pass
    def backend(params):
        if params.structured_outputs is not None:
            conversion()
    tracked = [('request_validator', Request.model_validate), ('sampling_conversion', Request.to_sampling_params),
        ('backend_validator', backend), ('json_conversion', conversion)]
    result = producer._run(Request, backend, ValidationRejected, tracked, 'fixture-model', followups=True)
    assert len(calls) == 10 and all(b['tool_choice'] == 'auto' for b in calls[1::2])
    assert result[-1]['outcome'] == 'rejected' and result[-1]['events'] == ['request_validator']
    assert all(r['after_normal_conversion']['outcome'] == 'accepted' and
        'json_conversion' in r['after_normal_conversion']['events'] for r in result)
    obs = producer.project(result, require_followups=True)
    assert len(obs['structured_tools_controls']['value']) == len(obs['structured_tools_controls_planned']['value']) == 9
    assert judge(obs).verdicts['security'] == Verdict.UNKNOWN and judge(obs).verdicts['functional'] == Verdict.PASS
    assert 'Return a location' not in json.dumps(result)


@pytest.mark.parametrize('failure', ['missing', 'execution_error'])
def test_followup_failure_blocks_functional_pass_and_preserves_violation(failure):
    result = rows()
    result[-1].update(request_accepted=True, outcome='execution_error')
    if failure == 'missing':
        result[-1].pop('after_normal_conversion')
    else:
        result[-1]['after_normal_conversion']['outcome'] = 'execution_error'
    obs = producer.project(result, require_followups=True)
    assert judge(obs).verdicts['functional'] == Verdict.UNKNOWN and judge(obs).verdicts['security'] == Verdict.FAIL


@pytest.mark.parametrize('attachment_failure', [False, True])
def test_draft_provenance_and_append_failure_keep_facts(tmp_path, monkeypatch, attachment_failure):
    result = rows()
    if attachment_failure:
        result[-1].update(request_accepted=True, outcome='execution_error')
    monkeypatch.setattr(producer, 'capture_target031', lambda *a, **kw: {'rows': result})
    path = tmp_path / 'structured-tools-vllm-031-cpu.json'
    if attachment_failure:
        path.write_text('existing append-only evidence')
    draft = producer.StructuredToolsTarget031CPUExecutor({'source_root': str(tmp_path), 'model': 'fixture-model'}).run(
        check(), target_env(origin=DataOrigin.DECLARED), {}, None, tmp_path)
    assert draft.data_origin == DataOrigin.DECLARED and draft.config['reference_commit'] == producer.TARGET031_COMMIT
    assert draft.config['profile'] == producer.TARGET031_PROFILE
    obs = {k: v.model_dump() for k, v in draft.observations.items()}
    if attachment_failure:
        assert draft.status == ExecStatus.ABORTED and draft.abort_reason == 'ARTIFACT_CAPTURE_INCOMPLETE'
        assert not draft.artifacts and path.read_text() == 'existing append-only evidence'
        assert judge(obs).verdicts['security'] == Verdict.FAIL
    else:
        assert draft.status == ExecStatus.COMPLETED and draft.normal_control.succeeded and len(draft.artifacts) == 1
        assert judge(obs).verdicts['security'] == Verdict.UNKNOWN
