"""REQ20 전용 EXAMPLE 계약 회귀. 실제 CUDA·Pod·HTTP 수집은 0회다."""
from copy import deepcopy
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest
import yaml

from guard.collectors.lifecycle import prepare_assets
from guard.core.select import select
from guard.models import DataOrigin, Environment, Policy, new_id
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, PACK_DIR
from tests.test_kubernetes_request_binding import env as example_env, target as example_target
from tools import b_req20_graph as tool

PRIVATE = 'EXAMPLE_PRIVATE_BODY_RID_TOKEN_VALUES_NOT_STORED'


@pytest.fixture
def case(tmp_path, request):
    temporary = TemporaryDirectory(prefix='byul-req20-example-')
    request.addfinalizer(temporary.cleanup)
    tmp_path = Path(temporary.name).resolve()
    pack = baseline_pack()
    doc = example_env().model_dump(mode='json'); doc['id'] = new_id('ENV'); doc['runtime'].update(gpu_count=1, tp_groups=1)
    doc['model']['inventory_sha256'] = '9' * 64; doc['content_sha256'] = ''
    env = Environment.model_validate(doc).seal()
    policy = Policy(id=new_id('POL'), data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(),
        scope={'include': ['REQ-20']}, values={'REQ-20': {}}).seal()
    source = Store(tmp_path / 'source'); source.put_many([env, policy]); put_pack(source, PACK_DIR)
    plan = select(pack, [env], policy); source.put(plan)
    target = example_target(env)
    prepared = prepare_assets(tmp_path / 'prepared', Path(__file__).resolve().parents[2])
    bodies = {key: {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': PRIVATE + key}],
        'max_tokens': 1, 'temperature': 0} for key in ('A', 'B')}
    manifest = {'schema': 'byul-req20-cohort/1', 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': tool.digest(target), 'session': prepared['session'], 'run_id': target['kubernetes']['run_id'],
        'owner': 'EXAMPLE-owner', 'recovery_owner': 'EXAMPLE-owner', 'model_inventory_sha256': '9' * 64,
        'declared_graph_mode': 'FULL', 'requests': bodies}
    for name, value in [('target', target), ('manifest', manifest)]:
        (tmp_path / (name + '.json')).write_text(json.dumps(value))
    args = dict(store=source.root, plan_id=plan.id, pack=PACK_DIR, target=tmp_path / 'target.json',
        prepared=tmp_path / 'prepared/manifest.json', manifest=tmp_path / 'manifest.json', output=tmp_path / 'output', entered_by='EXAMPLE-operator')
    return dict(args=args, env=env, policy=policy, plan=plan, pack=pack, source=source,
        target=target, prepared=prepared, manifest=manifest)


def snapshot(case, *, point='CUDAGraphWrapper.__call__', phase='RETURN', request='b', call='c', candidate=False, complete=None):
    observer_seq = 10 if point.startswith('GPUModelRunner.') else 20 if point.startswith('CUDAGraphWrapper.') else 40 if point.endswith('__init__') else 50 if phase == 'CALL' else 60
    call_seq = 30 if point.endswith('__init__') else 50 if point.endswith('get_output') else observer_seq - 1
    return {'schema': 'byul-v1-graph-buffer-snapshot/1', 'status': 'metadata_collected', 'point': point, 'phase': phase,
        'binding': {'pid': 20, 'session_id_sha256': tool.digest(case['prepared']['session']),
            'process_start_sha256': tool.digest(100), 'call_id_sha256': call * 64,
            'native_request_id_sha256': request * 64},
        'metadata': {'owner_token': '1' * 64, 'graph_token': '2' * 64, 'event_token': '3' * 64,
            'runtime_mode': 'FULL', 'selected_runtime_mode': 'FULL',
            'forward_context_state': 'available', 'forward_context_source_verified': True,
            'actual_graph_key_token': '5' * 64, 'graph_key_fields_matched': True,
            'graph_key_identity_matched': True,
            'call_transition': {'paired': phase == 'RETURN', 'same_owner': True if phase == 'RETURN' else None, 'call_seq': call_seq if phase == 'RETURN' else None, 'return_seq': observer_seq if phase == 'RETURN' else None},
            'sizes': {'num_reqs': 1, 'total_num_scheduled_tokens': 2},
            'descriptor': {'num_tokens': 2, 'num_reqs': 1},
            'buffers': {'input_ids': {'buffer_token': '4' * 64, 'descriptor': {'shape': [4], 'values_collected': False}, 'mutation_version': 2}},
            'consumer': {'copy_enqueue_boundary_returned': point.endswith('__init__') and phase == 'RETURN', 'paired_init_call_seq': 30, 'pending_output_version_changed': candidate, 'event_query_collected': type(complete) is bool,
                'event_completion_verified': complete, 'value_comparison_verified': True},
            'event_query': {'collected': type(complete) is bool, 'complete': complete,
                'initialized': True, 'capture_active': False, 'device_type': 'cuda', 'device_index': 0},
            'raw': PRIVATE}, 'source_qualified': True, 'actual_worker_observed': True,
        'seq': observer_seq, 'native_record_seq': 1000 + observer_seq, 'generation_sha256': '9' * 64}


def capture(case, rows=None):
    raw = tool._empty(case['env'], case['target'], case['prepared'], case['manifest'])
    rows = rows if rows is not None else [snapshot(case), snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='CALL', call='d', candidate=True)]
    if not any(r['point'].startswith('GPUModelRunner.') for r in rows):
        rows = [snapshot(case, point='GPUModelRunner._prepare_input_ids', call='e')] + rows
    if any(r['point'].endswith('get_output') for r in rows) and not any(r['point'].endswith('__init__') for r in rows):
        rows = rows + [snapshot(case, point='AsyncGPUModelRunnerOutput.__init__', call='f')]
    # 실제 observer에서는 입력 buffer가 prepare point에만, graph key는 wrapper에만 있다.
    for row in rows:
        if not row['point'].startswith('GPUModelRunner.'):
            row['metadata']['buffers'] = {}
    raw.update(native_rows=rows, input_token_counts=[2, 2],
        request_summaries=[{'id': 'json-distinct_body-A', 'outcome': 200, 'capture_complete': True, 'body': PRIVATE}],
        cleanup={'complete': True}, private=PRIVATE)
    return raw


def project(case, raw):
    return tool.project(raw, case['env'], case['target'], case['prepared'], case['manifest'])


def test_fresh_process_preflight_has_no_callback_torch_network_or_output(case):
    program = '''
import json,sys
from pathlib import Path
from tools import b_req20_graph as tool
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(RuntimeError(event)) if event in ('socket.bind','socket.connect') else None)
r=tool.run(**json.loads(sys.argv[1]))
assert not r['executed'] and r['requests']==0 and not r['native_capability_supported']
assert not Path(json.loads(sys.argv[1])['output']).exists()
assert 'guard.adapters.vllm_graph_buffers' not in sys.modules
assert 'torch' not in sys.modules and 'vllm' not in sys.modules
'''
    args = {k: str(v) if isinstance(v, Path) else v for k, v in case['args'].items()}
    result = subprocess.run([sys.executable, '-c', program, json.dumps(args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_exact_original_pack_and_canonical_refs(case):
    assert case['pack'].checks['REQ-20'].revision == 3
    assert re.fullmatch('ENV-[0-9A-HJKMNP-TV-Z]{26}', case['env'].id)
    assert re.fullmatch('POL-[0-9A-HJKMNP-TV-Z]{26}', case['policy'].id)
    result = tool.run(**case['args'])
    assert result['pack'] == tool.PACK_REF and result['plan'] == case['plan'].ref().model_dump(mode='json')


@pytest.mark.parametrize('field', ['environment_ref', 'target_sha256', 'session', 'run_id', 'model_inventory_sha256', 'owner'])
def test_manifest_binding_rejected_before_io(case, field):
    value = deepcopy(case['manifest']); value[field] = ''
    case['args']['manifest'].write_text(json.dumps(value))
    with pytest.raises(ValueError):
        tool.run(**case['args'], execute=True)
    assert not case['args']['output'].exists()


@pytest.mark.parametrize('change', ['same_input', 'wrong_api_model', 'more_tokens', 'system_prompt'])
def test_existing_safe_request_shape(case, change):
    value = deepcopy(case['manifest'])
    if change == 'same_input': value['requests']['B'] = deepcopy(value['requests']['A'])
    if change == 'wrong_api_model': value['requests']['A']['model'] = case['env'].model.id
    if change == 'more_tokens': value['requests']['A']['max_tokens'] = 2
    if change == 'system_prompt': value['requests']['A']['messages'][0]['role'] = 'system'
    case['args']['manifest'].write_text(json.dumps(value))
    with pytest.raises(ValueError): tool.run(**case['args'])


def test_unavailable_native_contract_closes_before_requests(case, monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('actual HTTP/reader prohibited')
    monkeypatch.setattr('guard.runtime.kubernetes_http.KubernetesHTTP.__call__', forbidden)
    raw = tool._capture_kubernetes(case['env'], case['target'], case['prepared'], case['manifest'], duration=180, binary='kubectl')
    assert raw['gaps'] == ['NATIVE_GRAPH_CAPABILITY_UNAVAILABLE'] and raw['request_summaries'] == []
    assert raw['cleanup'] == {'attempted': False, 'sealed': False, 'complete': True}


def test_tokens_are_count_only_and_partial_response_unusable():
    rows = [{'outcome': 200, 'capture_complete': True, 'body_truncated': False, 'utf8_valid': True,
        'body': json.dumps({'tokens': [3, 4], 'count': 2})} for _ in range(2)]
    assert tool._token_count(rows) == [2, 2]
    rows[0]['capture_complete'] = False
    assert tool._token_count(rows) is None


def test_version_candidate_is_not_failure_or_numerical_comparison(case):
    result = project(case, capture(case))
    assert result['candidate_count'] == 1 and result['rows'][0]['qualified']
    assert not result['confirmed_violations'] and not result['numerical_comparison_verified']
    assert result['rows'][0]['metadata']['consumer']['value_comparison_verified'] is None
    assert PRIVATE not in json.dumps(result)


@pytest.mark.parametrize('which', ['source', 'worker', 'request', 'session', 'mode', 'graph', 'shape', 'valid_length'])
def test_missing_actual_lineage_unqualified(case, which):
    row = snapshot(case, candidate=True)
    if which == 'source': row['source_qualified'] = False
    if which == 'worker': row['actual_worker_observed'] = False
    if which == 'request': row['binding'].pop('native_request_id_sha256')
    if which == 'session': row['binding']['session_id_sha256'] = '0' * 64
    if which == 'mode': row['metadata']['runtime_mode'] = 'NONE'
    if which == 'graph': row['metadata'].pop('graph_token')
    if which == 'valid_length': row['metadata']['descriptor']['num_tokens'] = 5
    raw = capture(case, [row])
    if which == 'shape': raw['native_rows'][0]['metadata']['buffers']['input_ids']['descriptor']['shape'] = []
    result = project(case, raw)
    assert result['candidate_count'] == 0 and not any(r['qualified'] for r in result['rows'])


@pytest.mark.parametrize('complete', [None, False, True])
def test_completion_missing_pending_complete_are_distinct(case, complete):
    rows = [snapshot(case), snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='CALL', call='d', complete=complete),
        snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='RETURN', call='d', complete=complete)]
    result = project(case, capture(case, rows)); pair, = result['sampled_output_copy_completion']
    assert pair['completion_collected'] == (type(complete) is bool)
    assert pair['event_complete'] is complete
    assert not result['confirmed_violations']


def test_different_event_cannot_qualify_completion(case):
    a = snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='CALL', call='d', complete=False)
    b = snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='RETURN', call='d', complete=True)
    b['metadata']['event_token'] = '6' * 64
    result = project(case, capture(case, [snapshot(case), a, b]))
    assert result['sampled_output_copy_completion'][0]['event_complete'] is None


def test_late_cleanup_gap_keeps_existing_qualified_candidate(case):
    raw = capture(case); raw.update(gaps=['CLEANUP_UNVERIFIED', 'AFTER_CONTROL_UNAVAILABLE'], cleanup={'complete': False})
    result = project(case, raw)
    assert result['candidate_count'] == 1 and not result['cleanup']['complete']


def test_original_manual_store_restore_and_privacy(case):
    before = tool._inventory(case['source'].root); calls = []
    def example(*args, **kwargs):
        calls.append(1); return capture(case)
    result = tool.run(**case['args'], execute=True, _example_capture=example)
    assert calls == [1] and result['security'] == 'UNKNOWN' and result['candidate_count'] == 1
    assert result['source_unchanged'] and result['exact_refs_restored'] and not result['real_gpu_execution_verified']
    store = Store.read_only(case['args']['output'] / 'store'); evidence, = store.iter_kind('EVD'); res, = store.iter_kind('RES')
    assert evidence.plan == case['plan'].ref() and evidence.environment == case['env'].ref()
    assert evidence.data_origin.value == 'DECLARED' and len(evidence.artifacts) == 1
    assert all(not c.attempted for c in evidence.challenges) and not evidence.normal_control.attempted
    assert res.verdicts.model_dump(mode='json') == {'security': 'UNKNOWN', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
    assert tool._inventory(case['source'].root) == before
    doc = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text()); entry, = doc['checks']
    check = case['pack'].checks['REQ-20']; assert entry['question'] == check.question
    assert [x['step'] for x in entry['challenges']] == check.challenge_steps and len(entry['observation_notes']) == 1
    assert PRIVATE not in (case['args']['output'] / 'graph-capture.json').read_text()
    assert store.verify() == []


def test_artifact_write_failure_preserves_metadata_in_manual_notes(case, monkeypatch):
    original = tool._write
    def write(path, value):
        if path.name == 'graph-capture.json': raise OSError(PRIVATE)
        return original(path, value)
    monkeypatch.setattr(tool, '_write', write)
    result = tool.run(**case['args'], execute=True, _example_capture=lambda *a, **kw: capture(case))
    assert result['candidate_count'] == 1 and result['issues'] == ['ARTIFACT_WRITE_UNAVAILABLE']
    evidence, = Store.read_only(case['args']['output'] / 'store').iter_kind('EVD')
    assert not evidence.artifacts
    assert PRIVATE not in (case['args']['output'] / 'manual-evidence.yaml').read_text()


def test_reusing_output_rejected_append_only(case):
    case['args']['output'].mkdir(); (case['args']['output'] / 'keep').write_text('original')
    with pytest.raises(ValueError): tool.run(**case['args'], execute=True)
    assert (case['args']['output'] / 'keep').read_text() == 'original'


def native_packet(case):
    point = {'module': 'EXAMPLE.worker', 'source_sha256': '5' * 64, 'code_sha256': '6' * 64}
    contract = {'profile': 'EXAMPLE-only-graph', 'callback_sha256': '7' * 64, 'callback_code_sha256': '8' * 64,
        'points': {p: deepcopy(point) for p in tool.POINTS | {'InputProcessor.assign_request_id'}}}
    prepared = deepcopy(case['prepared']); prepared['source_sha256']['EXAMPLE.worker'] = '5' * 64
    records = [
        {'event': 'CONTEXT', 'seq': 1, 'payload': {'context': {'pid': 20, 'start_ticks': 100}}},
        {'event': 'MODULE', 'seq': 2, 'payload': {'module': 'EXAMPLE.worker', 'verified': True,
            'source_sha256': '5' * 64, 'code_sha256': '6' * 64}},
        {'event': 'NATIVE', 'seq': 3, 'payload': {'point': 'InputProcessor.assign_request_id', 'phase': 'RETURN', 'source_sha256': '5' * 64,
            'metadata': {'request_selection': {'asgi_request_sha256': 'a' * 64,
                'selected_request_id_sha256': 'b' * 64, 'request_id_observed': True}}}},
        {'event': 'NATIVE', 'seq': 4, 'payload': {'point': 'CUDAGraphWrapper.__call__', 'phase': 'RETURN', 'source_sha256': '5' * 64,
            'metadata': {'graph_buffers': snapshot(case, candidate=True)}}}]
    cap = {'image_digest_verified': True, 'process_context_verified': True, 'provenance': {'owner': {'EXAMPLE-generation': 'owned-pod-1'}},
        'graph_callback_provenance': {'module': 'guard.adapters.vllm_graph_buffers', 'bytes_sha256': '7' * 64, 'code_sha256': '8' * 64},
        'records': records}
    packet = {'recordings': [{'session_owner_match': True, 'listed_file_identity_match': True, 'capture': cap}]}
    return contract, prepared, packet


def test_native_source_callback_and_selected_worker_request_binding(case, monkeypatch):
    contract, prepared, packet = native_packet(case); monkeypatch.setattr(tool, 'NATIVE_CONTRACT', contract)
    rows = tool._native_rows(packet, prepared, {'a' * 64})
    assert len(rows) == 1 and rows[0]['source_qualified'] and rows[0]['actual_worker_observed']
    assert rows[0]['metadata']['buffers']['input_ids']['descriptor']['shape'] == [4]
    assert not tool._native_rows(packet, prepared, {'f' * 64})
    packet['recordings'][0]['capture']['records'][1]['payload']['source_sha256'] = '0' * 64
    assert not tool._native_rows(packet, prepared, {'a' * 64})


@pytest.mark.parametrize('gate', ['session_owner_match', 'listed_file_identity_match', 'image_digest_verified', 'process_context_verified', 'callback'])
def test_unqualified_source_packet_is_not_actual_worker(case, monkeypatch, gate):
    contract, prepared, packet = native_packet(case); monkeypatch.setattr(tool, 'NATIVE_CONTRACT', contract)
    entry = packet['recordings'][0]
    if gate in entry: entry[gate] = False
    elif gate == 'callback': entry['capture']['graph_callback_provenance']['code_sha256'] = '0' * 64
    else: entry['capture'][gate] = False
    assert tool._native_rows(packet, prepared, {'a' * 64}) == []


@pytest.mark.parametrize('missing', ['input_prepare', 'graph_key', 'forward_context', 'source_context', 'init_lineage', 'event_capture_active'])
def test_actual_crosspoint_producer_boundaries(case, missing):
    rows = [snapshot(case), snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='CALL', call='d', complete=False),
        snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='RETURN', call='d', complete=True)]
    raw = capture(case, rows)
    if missing == 'input_prepare': raw['native_rows'] = [r for r in raw['native_rows'] if not r['point'].startswith('GPUModelRunner.')]
    if missing == 'graph_key': raw['native_rows'][1]['metadata']['actual_graph_key_token'] = None
    if missing == 'forward_context': raw['native_rows'][1]['metadata']['forward_context_state'] = 'absent'
    if missing == 'source_context': raw['native_rows'][1]['metadata']['forward_context_source_verified'] = False
    if missing == 'init_lineage': raw['native_rows'] = [r for r in raw['native_rows'] if not r['point'].endswith('__init__')]
    if missing == 'event_capture_active':
        for row in raw['native_rows']: row['metadata']['event_query']['capture_active'] = True
    result = project(case, raw)
    if missing in {'input_prepare', 'graph_key', 'forward_context', 'source_context'}:
        assert not result['graph_contexts']
    if missing in {'init_lineage', 'event_capture_active'}:
        assert not any(p['completion_collected'] for p in result['sampled_output_copy_completion'])
    else:
        assert result['sampled_output_copy_completion'][0]['event_complete'] is True
    assert result['graph_output_consumption']['verified'] is None


def test_wrapper_has_no_fake_buffer_and_input_shape_comes_from_prepare_point(case):
    result = project(case, capture(case))
    assert result['graph_contexts'][0]['input_shapes'] == [[4]]
    assert result['graph_contexts'][0]['valid_length'] == 2 and result['graph_contexts'][0]['replay_verified'] is None
    assert not next(r for r in result['rows'] if r['point'].startswith('CUDAGraphWrapper.'))['metadata']['buffers']


def test_actual_observer_raise_phase_and_binding_keys_are_preserved(case):
    raw = snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='RAISE', call='d')
    safe = tool._safe_snapshot(raw)
    assert safe['phase'] == 'RAISE' and safe['binding']['native_request_id_sha256'] == 'b' * 64
    assert 'selected_request_id_sha256' not in safe['binding']


def test_frontend_request_assignment_can_bind_separate_source_qualified_worker(case, monkeypatch):
    contract, prepared, packet = native_packet(case); monkeypatch.setattr(tool, 'NATIVE_CONTRACT', contract)
    frontend = packet['recordings'][0]; worker = deepcopy(frontend)
    frontend['capture']['records'] = frontend['capture']['records'][:3]
    frontend['capture']['records'][0]['payload']['context'] = {'pid': 10, 'start_ticks': 50}
    worker['capture']['records'] = [r for r in worker['capture']['records'] if r.get('payload', {}).get('point') != 'InputProcessor.assign_request_id']
    frontend['capture'].pop('graph_callback_provenance')
    packet['recordings'] = [frontend, worker]
    rows = tool._native_rows(packet, prepared, {'a' * 64})
    assert len(rows) == 1 and rows[0]['binding']['pid'] == 20
    # HTTP200 alone and an unqualified frontend cannot establish a worker ID.
    frontend['capture']['process_context_verified'] = False
    assert not tool._native_rows(packet, prepared, {'a' * 64})


def test_callback_phase_and_source_phase_must_match(case, monkeypatch):
    contract, prepared, packet = native_packet(case); monkeypatch.setattr(tool, 'NATIVE_CONTRACT', contract)
    packet['recordings'][0]['capture']['records'][3]['payload']['phase'] = 'CALL'
    assert tool._native_rows(packet, prepared, {'a' * 64}) == []


@pytest.mark.parametrize('missing', ['profile', 'point', 'source', 'callback_module'])
def test_partial_native_capability_closes_without_remote_io(case, monkeypatch, missing):
    contract, prepared, _ = native_packet(case)
    prepared['profile'] = contract['profile']
    if missing == 'profile': prepared['profile'] = 'EXAMPLE-unapproved-profile'
    if missing == 'point': del contract['points']['AsyncGPUModelRunnerOutput.get_output']
    if missing == 'source': prepared['source_sha256']['EXAMPLE.worker'] = '0' * 64
    calls = []
    def unavailable(name):
        calls.append(name); raise ModuleNotFoundError('EXAMPLE callback not integrated')
    monkeypatch.setattr(tool, 'NATIVE_CONTRACT', contract)
    monkeypatch.setattr(tool.importlib, 'import_module', unavailable)
    assert tool._capability(prepared) is False
    assert bool(calls) == (missing == 'callback_module')


def consumer_rows(case):
    return [snapshot(case), snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='CALL', call='d', complete=False),
        snapshot(case, point='AsyncGPUModelRunnerOutput.get_output', phase='RETURN', call='d', complete=True)]


@pytest.mark.parametrize('problem', ['init_after_call', 'return_before_call', 'owner', 'event', 'generation', 'worker', 'returned_new_init', 'inner_reversed', 'unpaired_return'])
def test_consumer_strict_original_sequence_owner_event_generation(case, problem):
    raw = capture(case, consumer_rows(case)); rows = raw['native_rows']
    call = next(r for r in rows if r['point'].endswith('get_output') and r['phase'] == 'CALL')
    returned = next(r for r in rows if r['point'].endswith('get_output') and r['phase'] == 'RETURN')
    init = next(r for r in rows if r['point'].endswith('__init__'))
    if problem == 'init_after_call': init['native_record_seq'] = call['native_record_seq'] + 1
    if problem == 'return_before_call': returned['native_record_seq'] = call['native_record_seq'] - 1
    if problem == 'owner': returned['metadata']['owner_token'] = '6' * 64
    if problem == 'event': returned['metadata']['event_token'] = '6' * 64
    if problem == 'generation': returned['generation_sha256'] = '6' * 64
    if problem == 'worker': returned['binding']['pid'] = 21
    if problem == 'returned_new_init': returned['metadata']['consumer']['paired_init_call_seq'] = 55
    if problem == 'inner_reversed': returned['seq'] = init['seq'] - 1
    if problem == 'unpaired_return': returned['metadata']['call_transition']['paired'] = False
    result = project(case, raw)
    assert not any(p['completion_collected'] for p in result['sampled_output_copy_completion'])


def test_reinit_between_get_output_call_return_is_explicit_gap(case):
    raw = capture(case, consumer_rows(case)); original = next(r for r in raw['native_rows'] if r['point'].endswith('__init__'))
    other = deepcopy(original); other['binding']['call_id_sha256'] = '7' * 64
    other['seq'] = 55; other['native_record_seq'] = 1055
    other['metadata']['call_transition'] = {'paired': True, 'same_owner': True, 'call_seq': 54, 'return_seq': 55}
    raw['native_rows'].append(other)
    result = project(case, raw)
    assert result['sampled_output_copy_completion'] and not result['sampled_output_copy_completion'][0]['init_lineage_matched']
    assert 'CONSUMER_LINEAGE_UNVERIFIED' in result['gaps']


def test_native_record_and_observer_sequence_domains_are_compared_separately(case):
    raw = capture(case, consumer_rows(case))
    # outer seq는inner보다작아도 각자 실제 순서가맞으면 허용한다.
    for row in raw['native_rows']:
        row['native_record_seq'] = row['seq'] // 10
    result = project(case, raw)
    assert result['sampled_output_copy_completion'][0]['completion_collected']
    assert result['sampled_output_copy_completion'][0]['event_complete'] is True


@pytest.mark.parametrize('kind', ['empty', 'bound', 'unsupported', 'false_complete'])
def test_output_bound_or_empty_never_promoted_to_complete(case, kind):
    row = snapshot(case)
    item = {'tensor_token': '4' * 64, 'descriptor': {'status': 'collected', 'shape': [2]}}
    items = [deepcopy(item)]
    if kind == 'empty': items = []
    if kind == 'bound': items = [deepcopy(item)] * 9; row['gaps'] = ['OUTPUT_BOUND']
    if kind == 'unsupported': items[0]['descriptor']['status'] = 'unavailable'
    row['metadata']['output'] = {'tensor_items': items, 'complete': kind != 'false_complete'}
    assert not tool._safe_snapshot(row)['metadata']['output_metadata']['complete']


def test_supported_bounded_output_metadata_is_collected_without_values(case):
    row = snapshot(case); row['metadata']['output'] = {'complete': True, 'tensor_items': [
        {'tensor_token': '4' * 64, 'descriptor': {'status': 'collected', 'shape': [2], 'raw': PRIVATE}}]}
    safe = tool._safe_snapshot(row)
    assert safe['metadata']['output_metadata'] == {'complete': True, 'tensor_count': 1, 'shapes': [[2]], 'tensor_tokens': ['4' * 64]}
    assert PRIVATE not in json.dumps(safe)


def test_real_observer_call_transition_is_unpaired_until_return(case):
    rows = consumer_rows(case); call = next(r for r in rows if r['point'].endswith('get_output') and r['phase'] == 'CALL')
    assert call['metadata']['call_transition'] == {'paired': False, 'same_owner': None, 'call_seq': None, 'return_seq': None}
    result = project(case, capture(case, rows))
    assert result['sampled_output_copy_completion'][0]['init_lineage_matched']
    assert result['sampled_output_copy_completion'][0]['event_complete'] is True


def test_sealed_session_does_not_claim_remote_observer_close(case):
    raw = capture(case); raw['cleanup'] = {'complete': False, 'sealed': True,
        'owned_probe_cleanup_complete': True, 'worker_observer_close_verified': True}
    result = project(case, raw)
    assert result['cleanup']['sealed'] and result['cleanup']['owned_probe_cleanup_complete']
    assert result['cleanup']['worker_observer_close_verified'] is None and not result['cleanup']['complete']


def test_sampled_copy_completion_does_not_establish_graph_hidden_output_consumption(case):
    rows = consumer_rows(case); wrapper = rows[0]
    wrapper['metadata']['output'] = {'complete': True, 'tensor_items': [{'tensor_token': '4' * 64,
        'descriptor': {'status': 'collected', 'shape': [2, 64]}}]}
    for row in rows[1:]:
        row['metadata']['retained_device_output_token'] = '6' * 64
        row['metadata']['copy_host_output'] = {'tensor_token': '8' * 64,
            'descriptor': {'status': 'collected', 'shape': [1, 1]}}
    result = project(case, capture(case, rows))
    assert result['sampled_output_copy_completion'][0]['event_complete'] is True
    graph = next(r for r in result['rows'] if r['point'] == 'CUDAGraphWrapper.__call__')
    consumer = next(r for r in result['rows'] if r['point'].endswith('get_output'))
    assert graph['metadata']['output_metadata']['tensor_tokens'] == ['4' * 64]
    assert consumer['metadata']['retained_device_output_token'] == '6' * 64
    assert result['graph_output_consumption'] == {'collected': False, 'verified': None,
        'sampled_output_is_graph_output_verified': None}
    assert 'LOGITS_SAMPLER_LINEAGE_UNOBSERVED' in result['gaps'] and not result['confirmed_violations']
