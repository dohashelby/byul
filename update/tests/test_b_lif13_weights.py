"""LIF13 선언 capture 소비 회귀. 실제 daemon/RPC/CUDA/Pod 실행은 없다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import pytest
import yaml

from guard.core.select import select
from guard.models import Environment, Policy, DataOrigin, new_id
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import baseline_pack, PACK_DIR
from tests.test_kubernetes_request_binding import env as example_env, target as example_target
from tools import b_lif13_weights as tool

PRIVATE = 'EXAMPLE_PRIVATE_SOCKET_ENTRY_CACHE_KEY_AND_RAW_WEIGHTS'


@pytest.fixture
def case(request):
    temp = TemporaryDirectory(prefix='byul-lif13-evidence-'); request.addfinalizer(temp.cleanup)
    root = Path(temp.name).resolve(); pack = baseline_pack()
    doc = example_env().model_dump(mode='json'); doc.update(id=new_id('ENV'), content_sha256='')
    doc['runtime'].update(gpu_count=1, tp_groups=1)
    env = Environment.model_validate(doc).seal()
    policy = Policy(id=new_id('POL'), data_origin=DataOrigin.EXAMPLE, status='DRAFT', pack=pack.ref(),
        scope={'include': ['LIF-13']}, values={'LIF-13': {}}).seal()
    source = Store(root / 'source'); source.put_many([env, policy]); put_pack(source, PACK_DIR)
    plan = select(pack, [env], policy); source.put(plan)
    t = example_target(env)
    target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'environment_ref': env.ref().model_dump(mode='json'),
        'run_id': t['kubernetes']['run_id'], 'lab': True, 'kubernetes': {'context': t['kubernetes']['context'], 'engine': t['kubernetes']['engine']}}
    config = {'schema': tool.CONFIG_SCHEMA, 'data_origin': 'EXAMPLE',
        'environment_ref': env.ref().model_dump(mode='json'), 'policy_ref': policy.ref().model_dump(mode='json'),
        'plan_ref': plan.ref().model_dump(mode='json'), 'target_sha256': tool.digest(target),
        'source_commit': tool.SOURCE_COMMIT, 'mode': 'zero_copy', 'owner': 'EXAMPLE-owner', 'recovery_owner': 'EXAMPLE-recovery',
        'allow_global_release': True, 'allow_empty_after': True,
        'daemon': {'socket_path': '/tmp/private.sock', 'pid': 20, 'uid': 1000, 'start_ticks': 100,
            'boot_id_sha256': '1' * 64, 'pid_namespace_inode': 2, 'mount_namespace_inode': 3, 'gpu_uuid': 'GPU-EXAMPLE'},
        'cache_key': {'checkpoint': PRIVATE, 'model_arch': 'EXAMPLE-arch', 'tp_size': 1, 'tp_rank': 0,
            'dtype': 'torch.float32', 'quantization': None, 'quant_config_hash': '', 'revision': env.model.revision,
            'vllm_version': '0.31.0', 'is_draft': False, 'dp_size': 1, 'dp_rank': 0},
        'entry_name': PRIVATE, 'device_index': 0, 'read_elements': 1, 'max_response_bytes': 1048576,
        'duration_seconds': 60, 'rpc_timeout_seconds': 5}
    for name, value in [('target', target), ('config', config)]: (root / (name + '.json')).write_text(json.dumps(value))
    args = dict(store=source.root, plan_id=plan.id, pack=PACK_DIR, target=root / 'target.json', config=root / 'config.json',
        output=root / 'output', entered_by='EXAMPLE-operator', pod_config=Path('/owned/config.json'),
        pod_source_root=Path('/approved/vllm'), pod_output=Path('/owned/capture.json'))
    return dict(root=root, source=source, pack=pack, env=env, policy=policy, plan=plan, target=target, config=config, args=args)


def read_fact(digest='a'):
    return {'collected': True, 'status': 'read', 'cuda_ipc_rebuilt': True, 'is_cuda': True, 'device_index': 0,
        'selected_count': 1, 'digest_sha256': digest * 64, 'read_completion': True, 'shape': [16], 'dtype': 'torch.float32',
        'source_validation': {'pre': 'verified', 'post': 'verified'}, 'peer_generation_verified': True,
        'daemon_generation_sha256': '3' * 64, 'raw_values': PRIVATE, 'pointer': PRIVATE}


def native_capture(case):
    cfg = case['config']
    return {'schema': tool.CAPTURE_SCHEMA, 'config_sha256': tool.digest(cfg), 'environment_ref': cfg['environment_ref'],
        'policy_ref': cfg['policy_ref'], 'plan_ref': cfg['plan_ref'], 'target_sha256': cfg['target_sha256'],
        'source_commit': cfg['source_commit'], 'data_origin': 'EXAMPLE', 'executed': True, 'mode': 'zero_copy',
        'source_file_bytes_sha256': deepcopy(tool.CLIENT_SOURCE_FILES), 'own_producer_sha256': '4' * 64,
        'source_validation': {'pre': 'verified', 'post': {'status': 'verified'}}, 'peer_generation_verified': True,
        'daemon_source_verified': True, 'release_authorization_verified': True,
        'collection_id': 'lif13-' + 'a' * 32, 'started_at': '2026-10-31T00:00:00+00:00', 'finished_at': '2026-10-31T00:00:01+00:00',
        'consumers': [{'slot': i, 'baseline': read_fact(), 'held_after': read_fact(), 'digest_equal': True,
            'followup_get_state': {'attempted': True, 'status': 'error', 'reason': 'weights_released', 'message': PRIVATE},
            'cleanup': {'references_dropped': True, 'device_sync_complete': True},
            'process_cleanup': {'joined': True, 'forced': False, 'exit_ok': True}} for i in (0, 1)],
        'release': {'attempted': True, 'status': 'ok', 'response_received': True, 'actor_uid': 1000},
        'daemon_generation_sha256': '3' * 64, 'cleanup': {'owned_children_complete': True,
            'daemon_recovery_complete': True, 'daemon_left_empty_allowed': True, 'collection_gap': False},
        'gaps': [], 'raw_weight_values': PRIVATE, 'fallback_consumption_verified': True}


def write_capture(case, raw=None):
    path = case['root'] / 'capture.json'; path.write_text(json.dumps(raw if raw is not None else native_capture(case)))
    return path


def test_preflight_fresh_process_has_no_rpc_gpu_native_import_or_output(case):
    code = '''
import json,sys
from pathlib import Path
from tools import b_lif13_weights as tool
sys.addaudithook(lambda event,args: (_ for _ in ()).throw(RuntimeError(event)) if event in ('socket.connect','socket.bind','subprocess.Popen') else None)
result=tool.run(**json.loads(sys.argv[1]))
assert result['rpc_calls']==result['native_runs']==0 and not result['evidence_imported']
assert not Path(json.loads(sys.argv[1])['output']).exists()
assert 'torch' not in sys.modules and 'vllm' not in sys.modules and 'tools.b_lif13_weights_native' not in sys.modules
'''
    args = {k: str(v) if isinstance(v, Path) else v for k, v in case['args'].items()}
    value = subprocess.run([sys.executable, '-c', code, json.dumps(args)], capture_output=True, text=True)
    assert value.returncode == 0, value.stderr


def test_pod_command_is_explicit_and_never_a_host_gpu_exec(case):
    result = tool.run(**case['args']); command = result['pod_commands']
    assert command['preflight_argv'] == ['python', '-m', 'tools.b_lif13_weights_native', '--config', '/owned/config.json',
        '--source-root', '/approved/vllm', '--output', '/owned/capture.json']
    assert command['execution_argv'][-1] == '--execute' and not command['executed']
    assert not command['module_installation_verified'] and not command['generic_kubernetes_exec_added']


@pytest.mark.parametrize('key', ['environment_ref', 'policy_ref', 'plan_ref', 'target_sha256', 'source_commit', 'mode', 'allow_global_release', 'allow_empty_after'])
def test_config_binding_and_selected_scope_close_before_any_io(case, key):
    config = deepcopy(case['config']); config[key] = False if key.startswith('allow_') else 'wrong'
    case['args']['config'].write_text(json.dumps(config))
    with pytest.raises(ValueError): tool.run(**case['args'])
    assert not case['args']['output'].exists()


def test_non_ascii_config_keeps_spec_canonical_utf8(case):
    cfg = deepcopy(case['config']); cfg['owner'] = '운영자'; cfg['recovery_owner'] = '복구 담당'
    cfg['entry_name'] = '선정가중치'; cfg['daemon']['socket_path'] = '/tmp/승인.sock'
    expected = hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    assert tool.digest(cfg) == expected
    case['args']['config'].write_text(json.dumps(cfg, ensure_ascii=False))
    assert tool.run(**case['args'])['config_sha256'] == expected


def test_three_results_are_distinct_and_no_standalone_measurement_promotion(case):
    result = tool.project(native_capture(case), case['config'])
    assert result['release_actor_scope']['release_status'] == 'ok'
    assert result['release_actor_scope']['daemon_uid_declared'] == 1000
    assert not result['release_actor_scope']['authorization_verified']
    assert result['post_acquisition']['consumers'][0]['reason'] == 'weights_released'
    assert not result['post_acquisition']['fallback_collected']
    assert {r['digest_relation'] for r in result['existing_native_references']['consumers']} == {'same'}
    assert result['qualification']['client_source_claim_matches_fixed_files']
    assert not result['qualification']['daemon_source_verified'] and not result['qualification']['measurement_verified']
    assert result['cleanup']['daemon_recovery_complete'] is None and result['cleanup']['daemon_native_cleanup_verified'] is None
    assert PRIVATE not in json.dumps(result)


def test_changed_native_read_is_recorded_not_hidden_by_getstate_or_cleanup_error(case):
    raw = native_capture(case); raw['consumers'][1]['held_after'] = read_fact('b')
    raw['consumers'][1]['digest_equal'] = True  # 신고flag를사실보다우선하지않는다.
    raw['consumers'][1]['followup_get_state'].update(status='unavailable', reason='rpc_error')
    raw['source_validation']['post'] = {'status': 'unavailable'}
    raw['cleanup'].update(owned_children_complete=False, collection_gap=True)
    result = tool.project(raw, case['config'])
    assert result['existing_native_references']['consumers'][1]['digest_relation'] == 'changed'
    assert result['qualification']['client_source_validation_claimed']['post'] == 'unavailable'
    assert result['post_acquisition']['consumers'][1]['status'] == 'unavailable'
    assert not result['cleanup']['owned_children_complete_claimed']


@pytest.mark.parametrize('bad', ['non_cuda', 'by_value', 'incomplete', 'count', 'shape', 'dtype'])
def test_non_native_or_incomplete_read_is_not_zero_copy_relation(case, bad):
    raw = native_capture(case); value = raw['consumers'][0]['held_after']
    if bad == 'non_cuda': value['is_cuda'] = False
    if bad == 'by_value': value['cuda_ipc_rebuilt'] = False
    if bad == 'incomplete': value['read_completion'] = False
    if bad == 'count': value['selected_count'] = 17
    if bad == 'shape': value['shape'] = [15]
    if bad == 'dtype': value['dtype'] = 'not a tensor dtype'
    result = tool.project(raw, case['config'])
    assert result['existing_native_references']['consumers'][0]['digest_relation'] == 'unavailable'


@pytest.mark.parametrize('field', ['config_sha256', 'environment_ref', 'policy_ref', 'plan_ref', 'target_sha256', 'source_commit', 'data_origin'])
def test_capture_exact_binding_cannot_be_replaced(case, field):
    raw = native_capture(case); raw[field] = 'wrong'
    path = write_capture(case, raw)
    with pytest.raises(ValueError): tool.run(**case['args'], capture=path, import_capture=True)
    assert not case['args']['output'].exists()


def test_duplicate_slots_and_capture_symlink_are_rejected(case):
    raw = native_capture(case); raw['consumers'][1]['slot'] = 0
    with pytest.raises(ValueError): tool.project(raw, case['config'])
    real = write_capture(case); link = case['root'] / 'link.json'; link.symlink_to(real)
    with pytest.raises(ValueError): tool.run(**case['args'], capture=link, import_capture=True)


def test_partial_handoff_even_without_release_does_not_claim_daemon_cleanup(case):
    raw = native_capture(case); raw['release'] = {'attempted': False, 'status': 'unavailable', 'response_received': False}
    raw['consumers'] = []; raw['cleanup']['owned_children_complete'] = True
    raw['gaps'] = ['BASELINE_COHORT_UNAVAILABLE']
    result = tool.project(raw, case['config'])
    assert not result['release_actor_scope']['release_attempted']
    assert result['cleanup']['exported_counters_reclaimed_verified'] is None
    assert result['cleanup']['allocator_global_reclamation_verified'] is None
    assert 'PARTIAL_HANDOFF_CAN_REQUIRE_EXTERNAL_RECOVERY' in result['gaps']


def test_original_manual_exact_refs_restore_and_whole_unknown(case):
    path = write_capture(case); before = tool._inventory(case['source'].root); original = path.read_bytes()
    result = tool.run(**case['args'], capture=path, import_capture=True)
    assert result['native_runs'] == result['rpc_calls'] == 0 and result['exact_refs_restored'] and result['source_unchanged']
    assert tool._inventory(case['source'].root) == before and path.read_bytes() == original
    store = Store.read_only(case['args']['output'] / 'store'); evidence, = store.iter_kind('EVD'); res, = store.iter_kind('RES')
    assert evidence.data_origin.value == 'DECLARED' and evidence.plan == case['plan'].ref() and evidence.environment == case['env'].ref()
    assert res.verdicts.model_dump(mode='json') == {'security': 'UNKNOWN', 'functional': 'UNKNOWN', 'performance': 'UNKNOWN'}
    assert all(not c.attempted for c in evidence.challenges) and not evidence.normal_control.attempted
    doc = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text()); entry, = doc['checks']
    check = case['pack'].checks['LIF-13']; assert entry['question'] == check.question
    assert [c['step'] for c in entry['challenges']] == check.challenge_steps and len(entry['observation_notes']) == 1
    assert PRIVATE not in (case['args']['output'] / 'weights-lifetime.json').read_text()
    assert store.verify() == []


def test_artifact_write_failure_preserves_all_three_safe_results_in_manual(case, monkeypatch):
    path = write_capture(case); original = tool._write
    def write(p, value):
        if p.name == 'weights-lifetime.json': raise OSError(PRIVATE)
        return original(p, value)
    monkeypatch.setattr(tool, '_write', write)
    result = tool.run(**case['args'], capture=path, import_capture=True)
    assert result['facts_storage'] == 'manual_notes' and result['issues'] == ['ARTIFACT_WRITE_UNAVAILABLE']
    doc = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text())
    recovered = json.loads(doc['checks'][0]['observation_notes'][0]['answer'].split('LIF13_SAFE_FACTS=', 1)[1])
    assert recovered == result['safe_facts'] and recovered['existing_native_references']['consumers'][0]['digest_relation'] == 'same'
    assert PRIVATE not in json.dumps(recovered)


def test_store_failure_is_explicit_and_retains_safe_facts_without_recapture(case, monkeypatch):
    path = write_capture(case)
    monkeypatch.setattr(tool, 'import_evidence', lambda *a, **kw: (_ for _ in ()).throw(OSError(PRIVATE)))
    with pytest.raises(tool.EvidencePreservationError) as error:
        tool.run(**case['args'], capture=path, import_capture=True)
    assert error.value.recovery['status'] == 'failed' and not error.value.recovery['success']
    assert not error.value.recovery['capture_rerun_required']
    assert error.value.safe_facts['existing_native_references']['consumers'][0]['digest_relation'] == 'same'
    assert PRIVATE not in json.dumps(error.value.safe_facts)


def test_existing_output_cannot_be_overwritten(case):
    case['args']['output'].mkdir(); (case['args']['output'] / 'keep').write_text('original')
    with pytest.raises(ValueError): tool.run(**case['args'])
    assert (case['args']['output'] / 'keep').read_text() == 'original'


def test_final_native_exports_runtime_actor_fields_survive_as_separate_claims(case):
    raw = native_capture(case)
    baseline = raw['consumers'][0]['baseline']
    baseline['get_state'] = {'attempted': True, 'response_received': True, 'status': 'ok', 'peer_generation_verified': True,
        'daemon_generation_sha256': '3' * 64, 'message': PRIVATE}
    baseline['exports'] = {'received_cuda_entries': 4, 'rebuilt_cuda_entries': 4, 'cpu_by_value_entries': 1, 'complete': True}
    baseline['runtime'] = {'torch_version': '2.13.0+cu130', 'torch_reductions_file_sha256': '8' * 64, 'path': PRIVATE}
    follow = raw['consumers'][0]['followup_get_state']; follow.update(status='ok', reason='ok',
        exports={'received_cuda_entries': 4, 'rebuilt_cuda_entries': 4, 'cpu_by_value_entries': 1, 'complete': True})
    raw['release'].update(actor_real_uid=1000, actor_effective_uid=1001)
    result = tool.project(raw, case['config'])
    fact = result['existing_native_references']['consumers'][0]['baseline']
    assert fact['exports_claimed']['all_received_cuda_rebuilt_claimed'] and fact['exports_claimed']['received_cuda_entries'] == 4
    assert fact['runtime_claimed'] == {'torch_version': '2.13.0+cu130', 'torch_reductions_file_sha256': '8' * 64}
    assert result['post_acquisition']['consumers'][0]['exports_claimed']['rebuilt_cuda_entries'] == 4
    assert result['release_actor_scope']['actor_real_uid_claimed'] == 1000
    assert result['release_actor_scope']['actor_effective_uid_claimed'] == 1001
    assert result['release_actor_scope']['daemon_uid_declared'] == 1000
    assert not result['release_actor_scope']['authorization_verified'] and result['cleanup']['daemon_native_cleanup_verified'] is None
    assert PRIVATE not in json.dumps(result)


def test_partial_or_cpu_only_exports_cannot_claim_native_cleanup(case):
    raw = native_capture(case)
    raw['consumers'][0]['baseline']['exports'] = {'received_cuda_entries': 4, 'rebuilt_cuda_entries': 2,
        'cpu_by_value_entries': 1, 'complete': True}
    raw['consumers'][1]['baseline']['exports'] = {'received_cuda_entries': 0, 'rebuilt_cuda_entries': 0,
        'cpu_by_value_entries': 2, 'complete': True}
    result = tool.project(raw, case['config']); first, second = result['existing_native_references']['consumers']
    assert not first['baseline']['exports_claimed']['all_received_cuda_rebuilt_claimed']
    assert not second['baseline']['exports_claimed']['cuda_handoff_seen_claimed']
    assert result['cleanup']['exported_counters_reclaimed_verified'] is None


@pytest.mark.parametrize('value,expected', [(True, True), (False, False), (None, None), (1, None)])
def test_owned_socket_cleanup_retains_boolean_or_uncollected(case, value, expected):
    raw = native_capture(case); raw['consumers'][0]['cleanup']['owned_sockets_closed'] = value
    result = tool.project(raw, case['config'])
    assert result['existing_native_references']['consumers'][0]['cleanup_claimed']['owned_sockets_closed'] is expected
    assert result['existing_native_references']['consumers'][1]['cleanup_claimed']['owned_sockets_closed'] is None
    assert result['cleanup']['daemon_native_cleanup_verified'] is None


@pytest.mark.parametrize('status', ['daemon_epoch_changed', 'daemon_epoch_unavailable'])
def test_pre_read_epoch_status_survives_without_claiming_cuda_read(case, status):
    raw = native_capture(case); raw['consumers'][0]['held_after'] = {'collected': False, 'status': status,
        'peer_generation_verified': False, 'daemon_generation_sha256': None}
    result = tool.project(raw, case['config']); fact = result['existing_native_references']['consumers'][0]
    assert fact['held_after']['status'] == status and not fact['held_after']['collected']
    assert not fact['held_after']['peer_generation_verified_claimed'] and fact['digest_relation'] == 'unavailable'


def test_post_read_epoch_failure_preserves_observed_digest_without_promotion(case):
    raw = native_capture(case); raw['consumers'][0]['held_after'].update(peer_generation_verified=False)
    result = tool.project(raw, case['config']); held = result['existing_native_references']['consumers'][0]['held_after']
    assert held['collected'] and held['digest_sha256'] == 'a' * 64 and held['selected_count'] == 1
    assert not held['peer_generation_verified_claimed']
    assert not result['existing_native_references']['consumers'][0]['native_measurement_verified']


@pytest.mark.parametrize('mode', ['zero_copy', 'copy'])
def test_capture_mode_must_equal_original_selected_zero_copy(case, mode):
    raw = native_capture(case); raw['mode'] = mode
    if mode == 'copy':
        with pytest.raises(ValueError, match='LIF13_CAPTURE_CONFIG_OR_REF_MISMATCH'):
            tool.project(raw, case['config'])
    else:
        result = tool.project(raw, case['config'])
        assert result['mode'] == 'zero_copy'
        assert result['existing_native_references']['consumers'][0]['digest_relation'] == 'same'
