"""EXAMPLE runner/core contracts. No Kubernetes, HTTP inference or CPU capture."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from guard.executors import registry
from guard.models import DataOrigin, Policy
from guard.store import Store
from tests.test_kubernetes_request_binding import env, target
from tools import b_online_kubernetes_run as runner

PRIVATE = 'PRIVATE_BODY_TOKEN_EXCEPTION_DO_NOT_PERSIST'


@pytest.fixture(autouse=True)
def example_source_contracts(monkeypatch):
    points = ('OnlineRenderer.render_chat', 'OnlineRenderer.validate_chat_template',
              'protocol.validate_request_mm_kwargs', 'InputProcessor.process_inputs',
              'InputProcessor._validate_prompt_len', 'InputProcessor._validate_model_input')
    contracts = {point: {'module': 'EXAMPLE-source-module', 'source_sha256': '9' * 64,
        'code_sha256': '8' * 64, 'default_deny': ('template_nonnull' if point.endswith('validate_chat_template')
            else 'mm_nonempty' if point.endswith('validate_request_mm_kwargs') else 'decoder_length')}
        for point in points}
    monkeypatch.setattr(runner, 'ONLINE_POINT_CONTRACTS', contracts)
    monkeypatch.setattr(runner, 'ONLINE_CALLBACK_CODE_SHA256', '7' * 64)


@pytest.fixture
def inputs(tmp_path):
    environment = env()
    destination = target(environment)
    pack = runner.proposal_pack(tmp_path / 'pack')
    policy = Policy(id='POL-ONLINE-K8S-EXAMPLE', created_at=datetime.now(timezone.utc),
        data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(), scope={'include': list(runner.CHECKS)}).seal()
    prepared = {'session': 'a' * 32}
    body = {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': PRIVATE}], 'max_tokens': 1}
    cases = [{'id': 'case-' + check.lower(), 'check_id': check,
        'body': {**deepcopy(body), 'kv_transfer_params': {'prompt_token_ids': [32]}},
        'after_body': deepcopy(body)} for check in runner.CHECKS]
    manifest = {'schema': 'byul-online-kubernetes-run-input/1',
        'environment_ref': environment.ref().model_dump(mode='json'), 'target_sha256': runner.digest(destination),
        'session': prepared['session'], 'run_id': destination['kubernetes']['run_id'],
        'owner': 'EXAMPLE-owner', 'recovery_owner': 'EXAMPLE-recovery', 'isolated_run': True,
        'domain_config_sha256': 'b' * 64, 'cases': cases}
    return environment, policy, pack, destination, prepared, manifest


def fact(inputs, case):
    _, _, _, destination, prepared, manifest = inputs
    return {'case_id': case['id'], 'check_id': case['check_id'], 'target_sha256': runner.digest(destination),
        'manifest_sha256': runner.digest(manifest), 'session': prepared['session'],
        'source_verified': True, 'generation_verified': True, 'request_join_verified': True,
        'source_field_supported': True, 'native_returned': True, 'after_admitted': True,
        **{k: 'c' * 64 for k in runner._HASH_KEYS},
        'parsed_fields_sha256': runner.digest(runner._case_fields(case)),
        'request_ids_in_domain': False, 'request_ids_within_model_vocab': False,
        'default_deny_bypass': True, 'trust_request_chat_template': False, 'trust_request_mm_kwargs': False,
        'private': PRIVATE}


class ExampleSession:
    def __init__(self, inputs, *, late=None, unqualified=None):
        self.inputs, self.late, self.unqualified = inputs, late, unqualified
        self.calls = []

    def preflight(self):
        self.calls.append('preflight')
        if self.late == 'preflight':
            raise OSError(PRIVATE)
        return True

    def case(self, case, destination):
        self.calls.append(case['id'])
        if self.late == 'case' and case['check_id'] == 'SUP-08':
            raise OSError(PRIVATE)
        raw = fact(self.inputs, case)
        if case['check_id'] == self.unqualified:
            raw['request_join_verified'] = False
        return raw

    def seal(self):
        self.calls.append('seal')
        if self.late == 'seal':
            raise OSError(PRIVATE)
        return {'sealed': True, 'generation_verified': True}

    def close(self):
        self.calls.append('close')
        if self.late == 'cleanup':
            raise OSError(PRIVATE)
        return {'owned_probe_reaped': True, 'owned_session_closed': True}


def run(tmp_path, inputs, **kwargs):
    example = ExampleSession(inputs, **kwargs)
    summary = runner.run_pipeline(*inputs, tmp_path / 'run', execute=True, _example_session=example)
    store = Store(tmp_path / 'run/store')
    return summary, example, list(store.iter_kind('EVD')), list(store.iter_kind('RES'))


def test_one_shared_collection_three_evidence_exact_refs_restore_and_privacy(tmp_path, inputs):
    before = dict(registry.EXECUTORS)
    summary, session, evidence, results = run(tmp_path, inputs)
    assert summary['execution_complete'] and summary['restore_verified']
    assert summary['exact_ref_rejudgment_unchanged'] and summary['store_verify_errors'] == []
    assert session.calls == ['preflight', *[c['id'] for c in inputs[-1]['cases']], 'seal', 'close']
    assert len(evidence) == len(results) == 3
    assert len({e.execution.started_at for e in evidence}) == 1
    assert len({e.execution.finished_at for e in evidence}) == 1
    assert all(e.data_origin == DataOrigin.EXAMPLE and len(e.artifacts) == 1 for e in evidence)
    assert all(r.verdicts.security.value == 'FAIL' for r in results)
    assert registry.EXECUTORS == before
    for path in (tmp_path / 'run/store/artifacts').iterdir():
        assert PRIVATE not in path.read_text()
    assert PRIVATE not in (tmp_path / 'run/summary.json').read_text()


@pytest.mark.parametrize('late', ['case', 'seal', 'cleanup'])
def test_later_errors_preserve_independent_fail_and_cleanup(tmp_path, inputs, late):
    summary, session, evidence, results = run(tmp_path, inputs, late=late)
    assert session.calls[-1] == 'close'
    assert not summary['execution_complete'] and summary['restore_verified']
    assert all(e.execution.status.value == 'ABORTED' for e in evidence)
    assert {r.check_id: r.verdicts.security.value for r in results}['REQ-08'] == 'FAIL'


def test_missing_attribution_stays_unknown_without_erasing_other_fail(tmp_path, inputs):
    _, _, evidence, results = run(tmp_path, inputs, unqualified='REQ-08')
    assert {r.check_id: r.verdicts.security.value for r in results} == {
        'REQ-07': 'FAIL', 'REQ-08': 'UNKNOWN', 'SUP-08': 'FAIL'}
    missing = next(e for e in evidence if e.check_id == 'REQ-08')
    assert missing.observations['online_input_confirmed_violations'].value is None


@pytest.mark.parametrize('execute', [False, True])
def test_actual_entry_stays_closed_without_a_capabilities_and_stores_unknown(tmp_path, inputs, monkeypatch, execute):
    def forbidden(*args, **kwargs):
        raise AssertionError('network must not run')
    monkeypatch.setattr(runner.lifecycle_reader.KubernetesLifecycleReader, 'snapshot', forbidden)
    monkeypatch.setattr(runner.kubernetes_http.KubernetesHTTP, '__call__', forbidden)
    summary = runner.run_pipeline(*inputs, tmp_path / 'closed', execute=execute)
    assert summary['live_support_available'] is False and summary['restore_verified']
    assert summary['started_at'] is None and not summary['execution_complete']
    store = Store(tmp_path / 'closed/store')
    assert len(list(store.iter_kind('EVD'))) == 3
    assert all(r.verdicts.security.value == 'UNKNOWN' for r in store.iter_kind('RES'))


@pytest.mark.parametrize('change', ['envref', 'targethash', 'session', 'owner', 'isolation', 'model', 'duplicate', 'verified'])
def test_bad_inputs_rejected_before_output_or_session(tmp_path, inputs, change):
    args = list(inputs)
    m = args[-1] = deepcopy(args[-1])
    if change == 'envref': m['environment_ref']['sha256'] = 'f' * 64
    elif change == 'targethash': m['target_sha256'] = 'f' * 64
    elif change == 'session': m['session'] = 'f' * 32
    elif change == 'owner': m['owner'] = ''
    elif change == 'isolation': m['isolated_run'] = False
    elif change == 'model': m['cases'][0]['body']['model'] = 'wrong-model'
    elif change == 'duplicate': m['cases'].append(deepcopy(m['cases'][0]))
    else: m['verified'] = True
    with pytest.raises(ValueError):
        runner.run_pipeline(*args, tmp_path / 'bad', execute=True)
    assert not (tmp_path / 'bad').exists()


def test_cannot_inject_example_session_into_declared_environment(tmp_path, inputs):
    args = list(inputs)
    args[0] = args[0].model_copy(update={'data_origin': DataOrigin.DECLARED}).seal()
    args[3] = target(args[0])
    args[-1] = deepcopy(args[-1])
    args[-1].update(environment_ref=args[0].ref().model_dump(mode='json'), target_sha256=runner.digest(args[3]))
    with pytest.raises(ValueError, match='INJECTION_REQUIRES_EXAMPLE'):
        runner.run_pipeline(*args, tmp_path / 'bad', execute=True, _example_session=ExampleSession(inputs))


def test_bad_admission_requires_independent_domain_and_exact_native_hash(inputs):
    case = inputs[-1]['cases'][1]
    raw = fact(inputs, case)
    kwargs = {'target_sha256': runner.digest(inputs[3]), 'manifest_sha256': runner.digest(inputs[-1]), 'session': inputs[4]['session']}
    assert runner._qualify(raw, case, **kwargs)['violation']
    for key, value in [('domain_sha256', None), ('input_ids_sha256', 'd' * 64),
                       ('parsed_fields_sha256', 'e' * 64), ('request_join_verified', False)]:
        bad = {**raw, key: value}
        assert not runner._qualify(bad, case, **kwargs)['violation']


def test_raw_http_status_is_never_native_proof(inputs):
    case = inputs[-1]['cases'][1]
    kwargs = {'target_sha256': runner.digest(inputs[3]), 'manifest_sha256': runner.digest(inputs[-1]), 'session': inputs[4]['session']}
    for status in (200, 400, 422):
        assert not runner._qualify({'outcome': status, 'body': PRIVATE}, case, **kwargs)['qualified']


def test_attachment_failure_does_not_erase_collected_fail(tmp_path, inputs, monkeypatch):
    original = runner._write
    def write(path, value):
        if path.name == 'online-native.json':
            raise OSError(PRIVATE)
        original(path, value)
    monkeypatch.setattr(runner, '_write', write)
    _, _, evidence, results = run(tmp_path, inputs)
    assert all(e.execution.status.value == 'ABORTED' and not e.artifacts for e in evidence)
    assert all(r.verdicts.security.value == 'FAIL' for r in results)


def test_preflight_error_still_closes_session_and_stores_aborted(tmp_path, inputs):
    summary, session, evidence, _ = run(tmp_path, inputs, late='preflight')
    assert session.calls == ['preflight', 'close']
    assert summary['restore_verified'] and all(e.execution.status.value == 'ABORTED' for e in evidence)


def test_new_runs_never_reuse_capture_or_collection_generation(tmp_path, inputs):
    summaries = []
    for name in ('first', 'second'):
        session = ExampleSession(inputs)
        summaries.append(runner.run_pipeline(*inputs, tmp_path / name, execute=True, _example_session=session))
        assert session.calls.count('preflight') == 1 and session.calls.count('seal') == 1
    assert summaries[0]['collection_id'] != summaries[1]['collection_id']


def native_packet(inputs, case, request_hashes):
    """Synthetic output at the existing parse_recording boundary; EXAMPLE only."""
    context = {'pid': 123, 'start_ticks': 456}
    inputs[4]['source_sha256'] = {'EXAMPLE-source-module': '9' * 64}
    records = [{'event': 'START', 'payload': {'context': context}},
               {'event': 'MODULE', 'payload': {'module': 'EXAMPLE-source-module', 'verified': True,
                                              'source_sha256': '9' * 64, 'code_sha256': '8' * 64}}]
    configuration = {'model_vocab_size': 32, 'max_model_len': 100, 'runner_type': 'generate'}
    domain = {'status': 'collected', 'canonical_sha256': runner._native_digest(list(range(32))),
        'request_ids_in_domain': False, 'request_ids_within_model_vocab': False,
        'request_ids_canonical_sha256': runner._native_digest([32])}
    inputs[-1]['domain_config_sha256'] = runner.digest({
        'effective_config': configuration, 'token_id_domain_sha256': domain['canonical_sha256']})
    for index, request_hash in enumerate(request_hashes):
        binding = {'pid': 123, 'asgi_request_id_sha256': request_hash, 'native_request_id_sha256': 'd' * 64,
            'call_id_sha256': 'e' * 64, 'session_id_sha256': runner._native_digest(inputs[4]['session']),
            'process_start_sha256': runner._native_digest(456)}
        actual_ids = [32] if index == 0 else [3]
        actual_domain = {**domain, 'request_ids_in_domain': index == 1, 'request_ids_within_model_vocab': index == 1,
                         'request_ids_canonical_sha256': runner._native_digest(actual_ids)}
        ids = {'status': 'collected', 'count': 1, 'integer_ids': True, 'canonical_sha256': runner._native_digest(actual_ids)}
        metadata = {'native_request_id_sha256': 'd' * 64, 'native_request_binding_matches': True,
            'processor_input': ids, 'admitted': ids, 'token_id_domain': actual_domain,
            'admitted_token_id_domain': actual_domain, 'effective_config': configuration}
        parsed = {'parsed_fields': {k: {'present': True, 'explicitly_set': True, 'status': 'collected',
            'canonical_sha256': runner._native_digest(v)} for k, v in runner._case_fields(case).items()}}
        if index == 1:
            parsed = {'parsed_fields': {field: {'present': True, 'explicitly_set': False, 'status': 'collected',
                'canonical_sha256': runner._native_digest(False if field == 'echo' else None)} for field in
                ('chat_template', 'chat_template_kwargs', 'mm_processor_kwargs', 'media_io_kwargs', 'kv_transfer_params', 'echo')}}
        for point, phase, value in [('OnlineRenderer.render_chat', 'CALL', parsed),
                ('OnlineRenderer.render_chat', 'RETURN', parsed),
                ('InputProcessor.process_inputs', 'CALL', metadata), ('InputProcessor.process_inputs', 'RETURN', metadata)]:
            records.append({'event': 'NATIVE', 'payload': {'point': point, 'source_sha256': '9' * 64, 'metadata': {'online_input': {
                'schema': 'byul-online-input-snapshot/1', 'point': point, 'phase': phase,
                'status': 'collected', 'binding': deepcopy(binding), 'metadata': deepcopy(value)}}}})
    return {'recorded_files_covered': True, 'live_census_stable': True, 'recordings': [{
        'session_owner_match': True, 'listed_file_identity_match': True, 'capture': {
            'process_context_verified': True, 'image_digest_verified': True,
            'online_callback_provenance': {'module': 'guard.adapters.vllm_online_input',
                'bytes_sha256': runner.A_SOURCE_PINS['adapters/vllm_online_input.py'], 'code_sha256': '7' * 64},
            'summary': {'recording_closed': True}, 'records': records}}]}


def test_native_join_uses_callback_fields_and_independent_domain(inputs):
    case = inputs[-1]['cases'][1]
    hashes = ['1' * 64, '2' * 64]
    packet = native_packet(inputs, case, hashes)
    raw = runner._join_native(packet, case, hashes, target=inputs[3], prepared=inputs[4], manifest=inputs[-1])
    assert raw is not None
    safe = runner._qualify(raw, case, target_sha256=runner.digest(inputs[3]),
        manifest_sha256=runner.digest(inputs[-1]), session=inputs[4]['session'])
    assert safe['qualified'] and safe['violation'] and safe['metadata']['after_admitted']
    assert PRIVATE not in json.dumps(safe)


@pytest.mark.parametrize('change', ['process', 'call', 'domain', 'field', 'owner', 'rid'])
def test_native_join_rejects_cross_generation_unqualified_payloads(inputs, change):
    case = inputs[-1]['cases'][1]
    hashes = ['1' * 64, '2' * 64]
    packet = native_packet(inputs, case, hashes)
    capture = packet['recordings'][0]['capture']
    first = capture['records'][2]['payload']['metadata']['online_input']
    returned = capture['records'][5]['payload']['metadata']['online_input']
    if change == 'process': returned['binding']['process_start_sha256'] = '0' * 64
    elif change == 'call': returned['binding']['call_id_sha256'] = '0' * 64
    elif change == 'domain': inputs[-1]['domain_config_sha256'] = '0' * 64
    elif change == 'field': first['metadata']['parsed_fields']['kv_transfer_params']['canonical_sha256'] = '0' * 64
    elif change == 'owner': packet['recordings'][0]['session_owner_match'] = False
    else: returned['metadata']['native_request_id_sha256'] = '0' * 64
    assert runner._join_native(packet, case, hashes, target=inputs[3], prepared=inputs[4], manifest=inputs[-1]) is None


def test_production_adapter_calls_public_http_reader_and_one_capture(tmp_path, inputs, monkeypatch):
    """Future A ABI exercised via EXAMPLE doubles, never an actual K8 result."""
    events = []
    packets = []
    monkeypatch.setattr(runner, '_approved_capabilities', lambda: None)
    monkeypatch.setattr(runner.kubernetes_http, 'ONLINE_INPUT_PROFILE', 'EXAMPLE-online-profile', raising=False)
    real_transport = runner.kubernetes_http.KubernetesHTTP
    class PublicHTTP:
        def __init__(self, destination, environment, **kwargs):
            real = real_transport(destination, environment, **{**kwargs, 'profile': runner.kubernetes_http.REQUEST_BINDING_PROFILE})
            self.api_model, self.target_sha256 = real.api_model, real.target_sha256
        def __call__(self, network, requests, timeout):
            events.append('http')
            assert network == 'kubernetes-probe' and timeout == 30
            packets.extend(deepcopy(requests))
            return [{'outcome': 200, 'body': PRIVATE}]
        def report(self): return {'cleanup_errors': []}
    class PublicReader:
        def __init__(self, destination, environment, prepared, **kwargs):
            self.binding = runner.lifecycle_reader.validate_target(destination, environment)
        def snapshot(self): events.append('snapshot'); return {'EXAMPLE-generation': 1}
        def seal_session(self): events.append('seal'); return {'sealed': True}
        def owner_identity(self, *args, **kwargs): pass
        def read_file(self, *args, **kwargs): pass
        def image_check(self, *args, **kwargs): return False
    def capture(*args, **kwargs):
        events.append('capture')
        # Empty native packet proves that HTTP success cannot produce a FAIL.
        return {'recordings': [], 'live_census_stable': False, 'recorded_files_covered': False}
    monkeypatch.setattr(runner.kubernetes_http, 'KubernetesHTTP', PublicHTTP)
    monkeypatch.setattr(runner.lifecycle_reader, 'KubernetesLifecycleReader', PublicReader)
    monkeypatch.setattr(runner.lifecycle_session, 'capture_session', capture)
    summary = runner.run_pipeline(*inputs, tmp_path / 'public', execute=True)
    assert events == ['snapshot', *['http'] * 6, 'seal', 'capture', 'snapshot']
    assert len(packets) == 6 and all(p['id'].startswith('online-') for p in packets)
    assert all(p['body']['model'] == 'fixture-model' for p in packets)
    assert all(p['headers'].get('X-Request-Id') for p in packets)
    assert not summary['execution_complete'] and summary['restore_verified']
    assert all(r.verdicts.security.value == 'UNKNOWN' for r in Store(tmp_path / 'public/store').iter_kind('RES'))


@pytest.mark.parametrize('change', ['image', 'callback_bytes', 'callback_code', 'source', 'module_code', 'missing_contract'])
def test_diagnostic_recordings_never_promoted_without_provenance(inputs, monkeypatch, change):
    case = inputs[-1]['cases'][1]
    hashes = ['1' * 64, '2' * 64]
    packet = native_packet(inputs, case, hashes)
    capture = packet['recordings'][0]['capture']
    if change == 'image': capture['image_digest_verified'] = False
    elif change == 'callback_bytes': capture['online_callback_provenance']['bytes_sha256'] = '0' * 64
    elif change == 'callback_code': capture['online_callback_provenance']['code_sha256'] = '0' * 64
    elif change == 'source': inputs[4]['source_sha256']['EXAMPLE-source-module'] = '0' * 64
    elif change == 'module_code': capture['records'][1]['payload']['code_sha256'] = '0' * 64
    else: monkeypatch.setattr(runner, 'ONLINE_POINT_CONTRACTS', None)
    assert runner._join_native(packet, case, hashes, target=inputs[3], prepared=inputs[4], manifest=inputs[-1]) is None


@pytest.mark.parametrize('fields,trust,expected', [
    ({'chat_template': 'hello'}, False, True), ({'chat_template': ''}, False, True),
    ({'chat_template_kwargs': {'chat_template': 'hello'}}, False, True),
    ({'chat_template_kwargs': {}}, False, False), ({'chat_template': 'hello'}, True, False),
    ({'mm_processor_kwargs': {'code_revision': 'owned'}}, False, True),
    ({'media_io_kwargs': {'video': {'num_frames': 1}}}, False, True),
    ({'mm_processor_kwargs': {}}, False, False), ({'media_io_kwargs': {}}, False, False),
])
def test_actual_gate_pair_default_deny_join_without_invented_consumption_flags(inputs, fields, trust, expected):
    case = inputs[-1]['cases'][0]
    is_mm = any(k in fields for k in ('mm_processor_kwargs', 'media_io_kwargs'))
    case['check_id'] = 'SUP-08' if is_mm else 'REQ-07'
    case['body'] = {**case['after_body'], **fields}
    hashes = ['1' * 64, '2' * 64]
    packet = native_packet(inputs, case, hashes)
    records = packet['recordings'][0]['capture']['records']
    binding = deepcopy(records[2]['payload']['metadata']['online_input']['binding'])
    point = 'protocol.validate_request_mm_kwargs' if is_mm else 'OnlineRenderer.validate_chat_template'
    trust_key = 'trust_request_mm_kwargs' if is_mm else 'trust_request_chat_template'
    for phase in ('CALL', 'RETURN'):
        records.append({'event': 'NATIVE', 'payload': {'point': point, 'source_sha256': '9' * 64,
            'metadata': {'online_input': {'schema': 'byul-online-input-snapshot/1', 'point': point,
                'phase': phase, 'status': 'collected', 'binding': binding,
                'metadata': {trust_key: trust, 'parsed_fields': {k: {'present': True, 'status': 'collected',
                    'canonical_sha256': runner._native_digest(v)} for k, v in fields.items()}}}}}})
    raw = runner._join_native(packet, case, hashes, target=inputs[3], prepared=inputs[4], manifest=inputs[-1])
    assert raw is not None and raw['default_deny_bypass'] is expected
    safe = runner._qualify(raw, case, target_sha256=runner.digest(inputs[3]),
        manifest_sha256=runner.digest(inputs[-1]), session=inputs[4]['session'])
    assert safe['violation'] is expected


@pytest.mark.parametrize('check', ['REQ-07', 'REQ-08'])
@pytest.mark.parametrize('count,mode,prompt_type,expected', [
    (17, 'generate', 'decoder', True), (16, 'generate', 'decoder', True),
    (16, 'pooling', 'decoder', False), (16, 'unavailable', 'decoder', False),
    (17, 'generate', 'encoder', False), (15, 'generate', 'decoder', False)])
def test_valid_id_overlong_and_equal_generate_boundary(inputs, check, count, mode, prompt_type, expected):
    case = deepcopy(inputs[-1]['cases'][1])
    case['check_id'] = check
    case['body']['kv_transfer_params']['prompt_token_ids'] = [3] * count
    raw = fact(inputs, case)
    raw.update(default_deny_bypass=False, request_ids_in_domain=True, request_ids_within_model_vocab=True,
        length_source_verified=True, prompt_type=prompt_type, runner_type=mode,
        prompt_count=count, effective_max_model_len=16,
        input_ids_sha256=runner._native_digest([3] * count), admitted_ids_sha256=runner._native_digest([3] * count))
    safe = runner._qualify(raw, case, target_sha256=runner.digest(inputs[3]),
        manifest_sha256=runner.digest(inputs[-1]), session=inputs[4]['session'])
    assert safe['violation'] is expected


def test_oov_after_is_not_normal_and_does_not_erase_primary_fail(inputs):
    case = inputs[-1]['cases'][1]
    hashes = ['1' * 64, '2' * 64]
    packet = native_packet(inputs, case, hashes)
    records = packet['recordings'][0]['capture']['records']
    for record in records[6:]:
        snapshot = record['payload']['metadata']['online_input']
        if snapshot['point'] == 'InputProcessor.process_inputs':
            m = snapshot['metadata']
            m['processor_input']['canonical_sha256'] = runner._native_digest([32])
            m['admitted']['canonical_sha256'] = runner._native_digest([32])
            for key in ('token_id_domain', 'admitted_token_id_domain'):
                m[key].update(request_ids_in_domain=False, request_ids_within_model_vocab=False,
                    request_ids_canonical_sha256=runner._native_digest([32]))
    raw = runner._join_native(packet, case, hashes, target=inputs[3], prepared=inputs[4], manifest=inputs[-1])
    assert raw is not None and raw['after_admitted'] is False
    assert runner._qualify(raw, case, target_sha256=runner.digest(inputs[3]),
        manifest_sha256=runner.digest(inputs[-1]), session=inputs[4]['session'])['violation']


@pytest.mark.parametrize('content', [[{'type': 'image_url', 'image_url': {'url': 'http://example.invalid'}}],
    [{'type': 'input_audio', 'input_audio': {'data': 'AAAA'}}], [{'type': 'video_url'}],
    [{'type': 'file'}], 'https://example.invalid/x', 'data:audio/wav;base64,AAAA'])
def test_non_text_or_remote_messages_rejected_before_session(tmp_path, inputs, content):
    inputs[-1]['cases'][0]['body']['messages'][0]['content'] = content
    with pytest.raises(ValueError, match='TEXT_ONLY'):
        runner.run_pipeline(*inputs, tmp_path / 'bad', execute=True)
    assert not (tmp_path / 'bad').exists()


def test_request_body_bound_is_checked_before_session(tmp_path, inputs):
    inputs[-1]['cases'][0]['body']['messages'][0]['content'] = 'x' * 131072
    with pytest.raises(ValueError, match='REQUEST_TOO_LARGE'):
        runner.run_pipeline(*inputs, tmp_path / 'bad', execute=True)
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('mode,prompt_type,expected', [('generate', 'decoder', True),
    ('pooling', 'decoder', False), ('generate', 'encoder', False)])
def test_length_native_validation_point_is_joined_to_real_admission(inputs, mode, prompt_type, expected):
    case = inputs[-1]['cases'][1]
    case['body']['kv_transfer_params']['prompt_token_ids'] = [3] * 16
    hashes = ['1' * 64, '2' * 64]
    packet = native_packet(inputs, case, hashes)
    records = packet['recordings'][0]['capture']['records']
    config = {'model_vocab_size': 32, 'max_model_len': 16, 'runner_type': mode}
    for index, record in enumerate(records[2:], 2):
        snapshot = record['payload']['metadata']['online_input']
        if snapshot['point'] != 'InputProcessor.process_inputs':
            continue
        m = snapshot['metadata']
        m['effective_config'] = config
        if index < 6:
            ids = {'status': 'collected', 'count': 16, 'integer_ids': True,
                   'canonical_sha256': runner._native_digest([3] * 16)}
            m['processor_input'] = m['admitted'] = ids
            for key in ('token_id_domain', 'admitted_token_id_domain'):
                m[key].update(request_ids_in_domain=True, request_ids_within_model_vocab=True,
                    request_ids_canonical_sha256=ids['canonical_sha256'])
    inputs[-1]['domain_config_sha256'] = runner.digest({'effective_config': config,
        'token_id_domain_sha256': runner._native_digest(list(range(32)))})
    binding = deepcopy(records[4]['payload']['metadata']['online_input']['binding'])
    point = 'InputProcessor._validate_prompt_len'
    records.append({'event': 'NATIVE', 'payload': {'point': point, 'source_sha256': '9' * 64,
        'metadata': {'online_input': {'schema': 'byul-online-input-snapshot/1', 'point': point,
            'phase': 'CALL', 'status': 'collected', 'binding': binding,
            'metadata': {'candidate_prompt_count': 16, 'prompt_type': prompt_type, 'effective_config': config}}}}})
    raw = runner._join_native(packet, case, hashes, target=inputs[3], prepared=inputs[4], manifest=inputs[-1])
    assert raw is not None
    safe = runner._qualify(raw, case, target_sha256=runner.digest(inputs[3]),
        manifest_sha256=runner.digest(inputs[-1]), session=inputs[4]['session'])
    assert safe['violation'] is expected
