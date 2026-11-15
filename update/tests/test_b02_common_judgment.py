"""New B judgment delta only; real A transforms, EXAMPLE transport, no registration."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.collectors import actor_consumption, gpu_common_inputs, scoped_consumption
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import common_consumption as subject
from guard.executors.input_validation import InputValidationExecutor
from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import put_pack
from tests import test_b12_runner_consumers as old
from tests.builders import completion_response, environment, policy
from tests.test_gpu_input_producers import api_rows, POD, R1, SCOPE
from tests.test_runner_consumption import worker_rows
from tests.test_scoped_consumption import native, EXAMPLE_SCOPE


def inject_record(monkeypatch, damage=None, *, contradiction=True):
    original = old.make_runner
    def produce(*args, **kwargs):
        if contradiction:
            kwargs['received_rejections'] = ('reused_input',)
        record, validation = original(*args, **kwargs)
        if damage == 'generation':
            record['bindings'][-1]['restart_count'] += 1
        elif damage == 'identity':
            record['bindings'][-1]['start_ticks'] = None
        elif damage == 'order':
            api = next(r for r in record['events'] if r['event'] == 'api_rejected')
            received = next(r for r in record['events'] if r['event'] == 'runner_received'
                            and r['request_sha256'] == api['request_sha256'])
            record['events'][api['seq']], record['events'][received['seq']] = received, api
            for seq, row in enumerate(record['events']): row['seq'] = seq
        return record, validation
    monkeypatch.setattr(old, 'make_runner', produce)
    if damage == 'missing':
        pinned = old.pinned
        def absent(*args, **kwargs):
            cfg = pinned(*args, **kwargs)
            Path(cfg['path']).unlink()
            return cfg
        monkeypatch.setattr(old, 'pinned', absent)


@pytest.mark.usefixtures("example_registered_callback_producers")
def test_registered_rejection_consumption_reaches_original_FAIL_with_late_gap(tmp_path, monkeypatch):
    inject_record(monkeypatch)
    draft, evd, result = old.input_finish(tmp_path)
    assert evd.observations['reused_input_state'].value == 'rejected'
    assert evd.observations['reused_input_violations'].value == ['native_rejected_reused_input_consumed']
    assert draft.status.value == 'ABORTED' and result.verdicts.security.value == 'FAIL'
    assert evd.data_origin == DataOrigin.EXAMPLE


@pytest.mark.usefixtures("example_registered_callback_producers")
def test_supported_normal_control_still_uses_original_PASS_rule(tmp_path):
    draft, evd, result = old.input_finish(tmp_path)
    assert draft.status.value == 'COMPLETED'
    assert evd.observations['reused_input_violations'].value == []
    assert result.verdicts.security.value == 'PASS' and evd.data_origin == DataOrigin.EXAMPLE


@pytest.mark.usefixtures("example_registered_callback_producers")
def test_null_start_ticks_is_original_A_contract_rejection_not_a_new_B_field_gate(tmp_path, monkeypatch):
    # Correct the first-run test premise: /1 _binding already requires int
    # start_ticks. Keep that original rejection, not a new permissive input.
    record, _ = old.make_runner()
    record['bindings'][-1]['start_ticks'] = None
    with pytest.raises(gpu_common_inputs.GpuInputError, match='GPU_INPUT_BINDING_INVALID'):
        gpu_common_inputs.validate(record, kind='runner-consumption', **SCOPE)
    inject_record(monkeypatch, 'identity', contradiction=False)
    draft, evd, result = old.input_finish(tmp_path)
    assert draft.status.value == 'ABORTED' and result.verdicts.security.value == 'UNKNOWN'
    assert evd.observations['reused_input_violations'].value == []
    raw = json.loads(next(a.path for a in draft.artifacts if a.name == 'b_common_consumption.json').read_bytes())
    assert raw['projection']['input_error']['message'] == 'GPU_INPUT_BINDING_INVALID'


@pytest.mark.parametrize('damage', ['missing', 'generation', 'identity', 'order', 'unsupported'])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_missing_unregistered_or_unbound_fact_cannot_create_native_FAIL_or_PASS(tmp_path, monkeypatch, damage):
    inject_record(monkeypatch, damage)
    draft, evd, result = old.input_finish(tmp_path, unsupported=damage == 'unsupported')
    assert evd.observations['reused_input_violations'].value == []
    assert draft.status.value == 'ABORTED' and result.verdicts.security.value == 'UNKNOWN'


@pytest.mark.parametrize('damage', ['missing', 'generation', 'unsupported'])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_original_HTTP_consumption_FAIL_survives_followup_input_gap(tmp_path, monkeypatch, damage):
    inject_record(monkeypatch, damage, contradiction=False)
    draft, evd, result = old.input_finish(tmp_path, consumed=True, unsupported=damage == 'unsupported')
    assert evd.observations['reused_input_violations'].value == ['prompt_token_ids']
    assert draft.status.value == 'ABORTED' and result.verdicts.security.value == 'FAIL'


@pytest.mark.parametrize('writer', [scoped_consumption, actor_consumption])
def test_real_unqualified_schema2_schema3_seq_candidate_is_preserved_without_fabricated_receipt(writer):
    before = deepcopy(gpu_common_inputs.SUPPORTED_PRODUCERS)
    processes = native([{'pid': 1, 'start_ticks': 1, 'events': api_rows(reject=True)},
        {'pid': 112, 'start_ticks': 2, 'rank': 0, 'events': worker_rows(logit_bias=0)}])
    record, validation = writer.record(processes, pod=POD, expected_stage_ranks={
        'runner_received': [0], 'sampler_applied': [0]}, expected_requests=[R1],
        qualification_scope={**EXAMPLE_SCOPE, 'tp_size': 1}, **SCOPE)
    assert not validation['supported']
    original, = [v for v in validation['violation_candidates'] if v['reason'] == 'rejected_input_consumed']
    assert 'request_sha256' not in original
    result = subject.runner(record, SCOPE, check_id='REQ-08', requests={'reused_input': R1}, required_cases=['reused_input'])
    candidate, = result['selected_candidates']
    assert candidate == {**original, 'request_sha256': record['events'][original['seq']]['request_sha256']}
    assert candidate['request_sha256'] == R1 and not result['supported'] and not result['complete']
    assert result['consumed_rejections'] == [] and result['gaps']
    assert gpu_common_inputs.SUPPORTED_PRODUCERS == before


@pytest.mark.parametrize('schema', ['guard-gpu-common-input/2', 'guard-gpu-common-input/3'])
def test_source_support_alone_never_qualifies_a_runner_fact_in_an_unreviewed_scope(schema):
    # Negative predicate input only: do not invent a registered record or
    # patch A's producers/qualification registry to simulate positive support.
    assert not subject._qualified_runner_fact({'schema': schema},
        {'supported': True, 'runtime_qualified': False})


@pytest.mark.usefixtures("example_registered_callback_producers")
def test_existing_factory_run_select_judge_report_preserves_new_FAIL_exact_artifacts(tmp_path, monkeypatch):
    pack = old.PACK
    env = environment(model={'id': 'test'}, engine={'name': 'vllm', 'version': '0.31.0'})
    target = {'lab': False, 'engine': {'trusted_url': 'http://unused', 'container': 'engine'},
              'origins': {'trusted': {'network': 'inside'}}}
    scope = {**SCOPE, 'environment_ref': env.ref().model_dump(mode='json'),
             'target_sha256': sha256_hex(canonical_json(target))}
    requests = {name: sha256_hex(name.encode()) for name in ('before', 'after-0', 'reused_input')}
    record, _ = old.make_runner(scope, requests, rejected=('reused_input',), received_rejections=('reused_input',))
    source = tmp_path/'target.json'; source.write_bytes(canonical_json(target))
    cfg = {'target': str(source), 'reused_token_ids': [11, 12, 13], 'vocab_size': 100,
           'vocab_source': 'EXAMPLE', 'runner_input': old.pinned(tmp_path, record, scope, requests)}
    values = {'reused_token_field': 'prompt_token_ids', 'reused_token_allowed': False, 'rejection_codes': ['400']}
    def probe(network, batch, timeout):
        out = []
        for request in batch:
            if request['method'] == 'GET' or 'prompt_token_ids' in request.get('body', {}):
                out.append({'outcome': 200 if request['method'] == 'GET' else 400, 'body': ''})
            else:
                reply = completion_response(request); payload = json.loads(reply['body'])
                payload['prompt_token_ids'] = [5, 6]; reply['body'] = json.dumps(payload); out.append(reply)
        return out
    original_init = InputValidationExecutor.__init__
    def initialize(self, config, *args, **kwargs):
        original_init(self, config, *args, container_probe=probe, **kwargs)
    monkeypatch.setattr(InputValidationExecutor, '__init__', initialize)
    pol = policy(pack.ref(), scope={'include': ['REQ-08']}, values={'REQ-08': values})
    store = Store(tmp_path/'store'); put_pack(store, pack.path); store.put_many([env, pol])
    plan = select(pack, [env], pol); store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={'input_validation': 'real'},
        adapter='vllm', raw={'input_validation': cfg}))
    at = utcnow(); result, = judge_plan(store, pack, plan, as_of=at)
    report = build_report(store, pack, plan, as_of=at); store.put(report)
    assert evd.execution.status.value == 'ABORTED' and result.verdicts.security.value == 'FAIL'
    assert evd.observations['reused_input_state'].value == 'rejected'
    assert evd.observations['reused_input_violations'].value == ['native_rejected_reused_input_consumed']
    artifact, = [a for a in evd.artifacts if a.name == 'b_common_consumption.json']
    saved = json.loads((store.root/artifact.path).read_bytes())
    assert saved['record'] == record and saved['projection']['supported']
    assert not saved['projection']['producer_authenticated']
    assert not report.official and evd.data_origin == DataOrigin.EXAMPLE
    (tmp_path/'exact-b02-refs.json').write_bytes(canonical_json({'refs': [
        obj.ref().model_dump(mode='json') for obj in (env, pol, plan, evd, result, report)],
        'scope': 'EXAMPLE A transform/B native-rejection consumption judgment; no worker/GPU execution'}))
