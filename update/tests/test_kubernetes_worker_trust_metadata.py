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
        super().__init__(target, mode='replacement' if case == 'replaced' else None)
        self.case = case

    def runner(self, argv, **kwargs):
        if 'exec' not in argv:
            return super().runner(argv, **kwargs)
        assert json.loads(kwargs['input_bytes']) == {'candidate_paths': []}
        result = super().runner(argv, **kwargs)
        packet = json.loads(result['stdout_bytes'])
        first, second = packet['processes']
        first['name'] = 'VLLMWorker' if self.case == 'ambiguous' else 'VLLM::EngineCor'
        second['name'] = 'Worker' if self.case == 'two-markers' else 'python3'
        first['environment_names'].append('PRIVATE_SETTING_NAME')
        first['environment_names'].sort()
        result['stdout_bytes'] = canonical_json(packet)
        return result


@pytest.mark.parametrize('case,completed,markers', [
    ('one-marker', True, ['VLLM']), ('two-markers', True, ['VLLM', 'Worker']),
    ('ambiguous', False, []), ('replaced', False, []),
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
    assert world.process_reads == 1 and not world.packets
    assert draft.status == (ExecStatus.COMPLETED if completed else ExecStatus.ABORTED)
    assert draft.data_origin.value == 'EXAMPLE'
    assert not draft.normal_control.attempted and all(not row.attempted for row in draft.challenges)
    observations = draft.observations
    mapping = observations['worker_trust_group_map'].value or []
    assert [group['marker'] for group in mapping] == markers
    if mapping:
        assert all(item['effective_uid'] == 2000 for group in mapping for item in group['processes'])
        assert observations['worker_trust_groups_missing'].value == sorted(set(VALUES['worker_trust_group_markers']) - set(markers))
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
