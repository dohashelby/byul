"""PLT-06 consumes only the exact Kubernetes process producer's owned metadata."""
import json
from pathlib import Path

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.core.judge import judge_evidence
from guard.executors.kubernetes_ipc_product import KubernetesIPCProductExecutor
from guard.models import ExecStatus, canonical_json, load_pack, sha256_hex
from tests.builders import T0, evidence, policy
from tests.test_kubernetes_process_consumers import ProcessWorld
from tests.test_model_preparation_integration import full_inputs


VALUES = {'worker_isolation_requirement': 'independent-uid', 'worker_trust_group_markers': ['VLLM', 'Worker']}


class WorkerWorld(ProcessWorld):
    def __init__(self, target, case):
        super().__init__(target, mode={'replaced': 'replacement', 'partial-process': 'later-row-gap'}.get(case))
        self.case = case

    def runner(self, argv, **kwargs):
        if 'exec' not in argv:
            result = super().runner(argv, **kwargs)
            if 'get' in argv and 'pod' in argv and self.target['kubernetes']['engine']['pod'] in argv:
                pod = json.loads(result['stdout_bytes'])
                mounts = pod['spec']['containers'][0].setdefault('volumeMounts', [])
                paths = {'one-marker': ['/proca'], 'root-mount': ['/'], 'proc-mount': ['/proc'],
                         'proc-child': ['/proc/sys'], 'duplicate-mount': ['/scratch', '/scratch/'],
                         'mount-changed': ['/scratch'] if self.process_reads else []}.get(self.case, [])
                mounts.extend({'name': f'mount-{index}', 'mountPath': path} for index, path in enumerate(paths))
                result['stdout_bytes'] = canonical_json(pod)
            return result
        assert json.loads(kwargs['input_bytes']) == {'candidate_paths': []}
        result = super().runner(argv, **kwargs)
        packet = json.loads(result['stdout_bytes'])
        first, second = packet['processes']
        first['name'] = 'VLLMWorker' if self.case == 'ambiguous' else 'VLLM::EngineCor'
        if self.case == 'two-markers':
            second.update(name='Worker', mount_namespace_inode=30)
        elif second['name'] is not None:
            second['name'] = 'python3'
        first['environment_names'].append('PRIVATE_SETTING_NAME')
        first['environment_names'].sort()
        if self.case in {'reader-only', 'reader-unstable', 'reader-unexcluded'}:
            packet.update(processes=[], pid_count_before=0, pid_count_after=0, pid_inventory_stable=False,
                          process_metadata_complete=False, diagnostic_codes=['PROCESS_SET_EMPTY'])
            packet['capture_subject']['identity_stable'] = self.case != 'reader-unstable'
            packet['capture_process_excluded'] = self.case != 'reader-unexcluded'
        result['stdout_bytes'] = canonical_json(packet)
        return result


@pytest.mark.parametrize('case,completed,markers', [
    ('one-marker', True, ['VLLM']), ('two-markers', True, ['VLLM', 'Worker']),
    ('partial-process', True, ['VLLM']), ('ambiguous', False, []), ('replaced', False, []),
    ('reader-only', False, []), ('reader-unstable', False, []), ('reader-unexcluded', False, []),
    ('mount-changed', False, []), ('root-mount', False, []), ('proc-mount', False, []),
    ('proc-child', False, []), ('duplicate-mount', False, []),
])
def test_worker_marker_map_uses_owned_read_only_metadata(tmp_path, case, completed, markers):
    env, target = full_inputs()
    target['lab'] = False
    world = WorkerWorld(target, case)
    path = tmp_path / 'target.json'
    path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['PLT-06']
    cfg = {'target': str(path), 'worker_trust_metadata_only': True}
    executor = KubernetesIPCProductExecutor(cfg, runner=world.runner, expected_target_sha256=sha256_hex(canonical_json(target)))
    draft = executor.run(check, env, VALUES, VllmAdapter(), tmp_path / 'work')
    shadowed = case in {'root-mount', 'proc-mount', 'proc-child'}
    assert world.process_reads == (0 if shadowed or case == 'duplicate-mount' else 1) and not world.packets
    assert draft.status == (ExecStatus.COMPLETED if completed else ExecStatus.ABORTED)
    assert draft.data_origin.value == 'EXAMPLE'
    assert not draft.normal_control.attempted and all(not row.attempted for row in draft.challenges)
    observations = draft.observations
    mapping = observations['worker_trust_group_map'].value or []
    assert [group['marker'] for group in mapping] == markers
    summary = json.loads(draft.artifacts[0].path.read_text().splitlines()[-1])
    assert summary['worker_role_authenticated'] is False and summary['process_pid_namespace_membership_verified'] is False
    stable_reader = bool(mapping) or case in {'reader-only', 'ambiguous'}
    assert summary['reader_pid_namespace_sha256'] == (sha256_hex(canonical_json(20)) if stable_reader else None)
    assert summary['reader_mount_namespace_sha256'] == (sha256_hex(canonical_json(10)) if stable_reader else None)
    if case.startswith('reader-'):
        assert summary['stable_process_rows'] == 0 and summary['process_inventory_stable'] is False
        assert summary['binding_verified'] is (case != 'reader-unexcluded')
        assert not observations['worker_trust_group_map'].collected
        assert not observations['worker_trust_groups_missing'].collected
        assert summary['marker_mount_namespace_relations'] == []
    if case == 'replaced':
        assert summary['binding_verified'] is False
    if mapping:
        assert all(item['effective_uid'] == 2000 for group in mapping for item in group['processes'])
        assert all(set(item) == {'pid', 'start_ticks', 'effective_uid', 'mount_namespace_sha256'}
                   for group in mapping for item in group['processes'])
        assert [(item['pid'], item['start_ticks'], item['shares_reader_mount_namespace'])
                for item in summary['marker_mount_namespace_relations']] == (
                    [(1, 101, True), (2, 102, False)] if case == 'two-markers' else [(1, 101, True)])
        assert observations['worker_trust_groups_missing'].value == sorted(set(VALUES['worker_trust_group_markers']) - set(markers))
    if shadowed:
        assert 'WORKER_TRUST_PROC_VIEW_SHADOWED' in summary['diagnostic_codes']
    if case in {'duplicate-mount', 'mount-changed'}:
        assert 'WORKER_TRUST_PROC_VIEW_UNVERIFIED' in summary['diagnostic_codes']
    if shadowed or case in {'duplicate-mount', 'mount-changed'}:
        assert summary['reader_pid_namespace_sha256'] is None and not observations['worker_trust_group_map'].collected
    assert not observations['worker_isolation_violations'].collected
    assert not observations['worker_shared_resources'].collected
    assert observations['worker_trust_map_complete'].value is False
    assert observations['worker_command_impact_verified'].value is False
    result = judge_evidence(check, evidence(status=draft.status, check_id='PLT-06', normal=draft.normal_control.model_dump(),
                                          observations={key: value.model_dump() for key, value in observations.items()}),
                            policy(pack.ref(), values={'PLT-06': VALUES}), T0)
    assert result.verdicts.security.value == 'UNKNOWN'
    assert all(b'PRIVATE_SETTING_NAME' not in item.path.read_bytes() for item in draft.artifacts)


@pytest.mark.parametrize('change', ['actors', 'sibling', 'missing-hash', 'wrong-hash', 'lab'])
def test_worker_metadata_opt_in_never_activates_ipc_actor_scope(tmp_path, change):
    env, target = full_inputs()
    target['lab'] = change == 'lab'
    path = tmp_path / 'target.json'
    path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    check = pack.checks['PLT-02' if change == 'sibling' else 'PLT-06']
    cfg = {'worker_trust_metadata_only': True, 'target': str(path)}
    if change == 'actors':
        cfg['actor_specs'] = []
    digest = None if change == 'missing-hash' else '0' * 64 if change == 'wrong-hash' else sha256_hex(canonical_json(target))
    calls = []
    executor = KubernetesIPCProductExecutor(cfg, runner=lambda *a, **kw: calls.append(a), expected_target_sha256=digest)
    with pytest.raises(ValueError):
        executor.run(check, env, VALUES, VllmAdapter(), tmp_path / 'work')
    assert not calls


@pytest.mark.parametrize('case,error', [
    ('ordinary', None),
    ('foreign-model-revision', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('over-budget', 'KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID'),
    ('captured-target-changed', 'KUBERNETES_IPC_PRODUCT_CONFIGURATION_INVALID'),
])
def test_worker_preparation_checks_saved_target_without_collecting(tmp_path, monkeypatch, case, error):
    from guard.runtime.engine_http import KubernetesHTTP

    def forbidden(*args, **kwargs):
        pytest.fail('PLT-06 preparation must not contact the target or read processes')

    monkeypatch.setattr(KubernetesHTTP, '_verify', forbidden)
    monkeypatch.setattr(KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(KubernetesHTTP, 'process_snapshot', forbidden)
    env, target = full_inputs()
    target['lab'] = False
    if case == 'foreign-model-revision':
        target['model_mapping']['revision'] = ('1' if env.model.revision == '0' * 40 else '0') * 40
    raw = canonical_json(target)
    path = tmp_path / 'saved-target.json'
    path.write_bytes(raw)
    digest = sha256_hex(raw)
    cfg = {'target': str(path), 'worker_trust_metadata_only': True,
           'max_duration_seconds': 301 if case == 'over-budget' else 30}
    executor = KubernetesIPCProductExecutor(cfg, runner=forbidden, expected_target_sha256=digest)
    expected = 'f' * 64 if case == 'captured-target-changed' else digest
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['PLT-06']
    if error is None:
        executor.prepared_worker_trust(check, env, VALUES, expected_target_sha256=expected)
    else:
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.prepared_worker_trust(check, env, VALUES, expected_target_sha256=expected)
