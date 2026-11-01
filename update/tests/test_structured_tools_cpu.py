"""EXAMPLE algorithm contracts; actual pinned imports run separately."""
from types import SimpleNamespace
import json

from guard.executors.structured_tools_cpu import _run, project, produce_reference_draft
from guard.executors.structured_tools import CASE_IDS
from guard.models import DataOrigin, ExecStatus, Verdict
from tests.builders import environment
from tests.test_structured_tools import judge, ValidationRejected
from tests.test_sampling_validation import check


def test_case_chain_tracks_request_sampling_backend_and_conversion_without_http():
    class Request:
        @staticmethod
        def model_validate(body):
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
    def backend(sampling):
        if sampling.structured_outputs is not None:
            conversion()
    tracked = [('request_validator', Request.model_validate), ('sampling_conversion', Request.to_sampling_params),
               ('backend_validator', backend), ('json_conversion', conversion)]
    rows = _run(Request, backend, ValidationRejected, tracked, 'fixture-model')
    assert [r['case_id'] for r in rows] == list(CASE_IDS)
    assert rows[-1]['outcome'] == 'rejected' and rows[-1]['events'] == ['request_validator']
    assert rows[-2]['schema_present'] is False and 'json_conversion' not in rows[-2]['events']
    obs = project(rows)
    assert set(obs['structured_tools_controls']['value'].values()) == {'pass'}
    assert judge(obs).verdicts['functional'] == Verdict.PASS
    assert judge(obs).verdicts['security'] == Verdict.UNKNOWN
    assert not obs['runner_consumption_verified']['value']
    assert 'Return a location' not in json.dumps(rows)


def test_request_acceptance_violation_survives_later_conversion_error():
    rows = [{'case_id': 'named-json', 'request_accepted': True, 'outcome': 'execution_error', 'events': []}]
    assert judge(project(rows)).verdicts['security'] == Verdict.FAIL


def test_unpinned_source_cannot_supply_json_measurements(tmp_path):
    (tmp_path / 'fake-observations.json').write_text('{"outcomes":{"named-json":"accepted"}}')
    draft = produce_reference_draft(check(), environment(), tmp_path / 'work', source_root=tmp_path, model='fixture-model')
    assert draft.status == ExecStatus.ABORTED
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert not draft.observations and not draft.artifacts
    assert draft.abort_reason == 'STRUCTURED_TOOLS_CPU_CAPTURE_UNAVAILABLE'


def test_local_attachment_failure_preserves_independent_violation(tmp_path, monkeypatch):
    from guard.executors import structured_tools_cpu as module
    record = {'rows': [{'case_id': 'named-json', 'request_accepted': True,
        'outcome': 'execution_error', 'events': []}]}
    monkeypatch.setattr(module, 'capture_reference', lambda *a, **kw: record)
    work = tmp_path / 'work'
    work.mkdir()
    attachment = work / 'structured-tools-reference-cpu.json'
    attachment.write_text('existing append-only bytes')
    draft = module.StructuredToolsCPUExecutor({'source_root': str(tmp_path), 'model': 'fixture-model'}).run(
        check(), environment(), {}, None, work)
    assert draft.status == ExecStatus.ABORTED and draft.abort_reason == 'ARTIFACT_CAPTURE_INCOMPLETE'
    assert not draft.artifacts and attachment.read_text() == 'existing append-only bytes'
    assert draft.observations['structured_tools_validator_violation'].value is True
    assert judge({k: v.model_dump() for k, v in draft.observations.items()}).verdicts['security'] == Verdict.FAIL
