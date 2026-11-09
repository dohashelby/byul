"""Real local orchestration/storage with EXAMPLE execution and launch recorders."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard import application
from guard.adapters.engine_provider import get_provider
from guard.adapters.model_inventory import inventory
from guard.collectors.model_mapping import validate_model_mapping, request_model_name
from guard.core.change_selection import input_diff
from guard.executors import registry
from guard.executors.base import EvidenceDraft
from guard.models import canonical_json, sha256_hex
from guard.models.artifacts import model_closure_sha256
from guard.runtime.docker import Docker, CmdResult, DockerError
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, req01_obs, T0


class ExampleExecutor:
    name, version = 'EXAMPLE-recovery', '1'
    def run(self, check, *args):
        return EvidenceDraft(status='COMPLETED', data_origin='EXAMPLE',
            normal_control={'attempted': True, 'succeeded': True},
            challenges=[{'step': s, 'attempted': True} for s in check.challenge_steps],
            observations=req01_obs(direct={'GET /v1/models': 200}) if check.id == 'REQ-01' else {}, config={'EXAMPLE': True})


def setup(monkeypatch, tmp_path, checks=('REQ-01',)):
    pack = baseline_pack()
    env = environment({'gateway': {'value': True, 'origin': 'EXAMPLE', 'source': 'EXAMPLE'}},
        engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': 'example/engine@sha256:' + 'a' * 64})
    pol = policy(pack.ref(), scope={'include': list(checks), 'exclude': []},
        values={'REQ-01': {'allowed_callers': ['allowed']}, 'REQ-03': {'general_allowed_kinds': ['inference']}})
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {pack.checks[c].executor: 'EXAMPLE' for c in checks}}
    for executor in cfg['executors']:
        monkeypatch.setitem(registry.EXECUTORS, executor + ':EXAMPLE', lambda cfg: ExampleExecutor())
    class ExampleDocker:
        def __init__(self, run_id): self.run_id = run_id
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(application, 'Docker', ExampleDocker)
    monkeypatch.setattr(application, '_credentials', lambda c, p: (deepcopy(c), {}))
    monkeypatch.setattr(application, '_managed_sentinel_dir', lambda *a, **k: None)
    monkeypatch.setattr(application, '_managed_diagnostic_reference', lambda *a, **k: 'EXAMPLE diagnostic')
    return pack, env, pol, cfg, Store(tmp_path / 'store'), ExampleDocker


def test_later_metadata_write_failure_preserves_completed_fail_and_recovers_exact_fragment(tmp_path, monkeypatch):
    pack, env, pol, cfg, store, _ = setup(monkeypatch, tmp_path, ('REQ-01', 'REQ-03'))
    monkeypatch.setattr(application, '_validation_inputs', lambda e, c: (deepcopy(c), {'image': e.engine.image_digest}))
    monkeypatch.setattr(application.sandbox, 'up', lambda *a, **k: {'observed_version': env.engine.version, 'images': {env.engine.image_digest: env.engine.image_digest}})
    put = store.put; failed = []
    def transient(obj):
        if str(obj.kind) == 'EVD' and obj.check_id == 'REQ-03' and not failed:
            failed.append(True); raise OSError('EXAMPLE transient index failure')
        return put(obj)
    monkeypatch.setattr(store, 'put', transient)
    report, execution_failed = application.execute_validation(store, pack, env, pol, cfg, tmp_path / 'run', T0)
    row = next(r for r in report.by_check if r.check_id == 'REQ-01')
    assert failed and execution_failed and row.single.security.value == 'FAIL'
    evidence = list(store.iter_kind('EVD'))
    assert len(evidence) == 2 and all(e.execution.status.value == 'COMPLETED' and e.revision == 1 for e in evidence)
    assert len({e.execution.attempt_id for e in evidence}) == 1 and store.verify() == []
    pending, = (store.root / 'pending-evidence').glob('*/*.json')
    recovered = next(e for e in evidence if e.check_id == 'REQ-03')
    assert pending.read_bytes() == canonical_json(recovered.to_dict())


def test_baseline_cleanup_failure_blocks_dependent_candidate_but_preserves_baseline_fail(tmp_path, monkeypatch):
    pack, baseline, pol, cfg, store, docker = setup(monkeypatch, tmp_path)
    candidate = baseline.model_copy(update={'id': 'ENV-EXAMPLE-CANDIDATE', 'engine': baseline.engine.model_copy(update={'version': '0.32.0', 'image_digest': 'example/engine@sha256:' + 'b' * 64})}).seal()
    side_cfgs = {s: deepcopy(cfg) for s in ('baseline', 'candidate')}
    managed = {s: {'image': e.engine.image_digest} for s, e in zip(side_cfgs, (baseline, candidate))}
    monkeypatch.setattr(application, 'update_inputs', lambda *a: ('managed', side_cfgs, managed))
    started = []
    def up(obj, topology, path, *a, **k):
        started.append(path.name); obj.side = path.name
        e = baseline if path.name == 'baseline' else candidate
        return {'observed_version': e.engine.version, 'images': {e.engine.image_digest: e.engine.image_digest}}
    def cleanup(obj, *args): raise DockerError('EXAMPLE resource release unverified')
    monkeypatch.setattr(docker, '__exit__', cleanup)
    monkeypatch.setattr(application.sandbox, 'up', up)
    report, failed = application.execute_update(store, pack, [baseline, candidate], pol, cfg, tmp_path / 'run', T0)
    row = next(r for r in report.by_check if r.check_id == 'REQ-01')
    assert started == ['baseline'] and failed and row.baseline.security.value == 'FAIL'
    candidate_evd = next(e for e in store.iter_kind('EVD') if e.side.value == 'candidate')
    assert candidate_evd.execution.abort_reason == 'BASELINE_RESOURCE_RELEASE_UNVERIFIED'


def model_cache(tmp_path, *, gpu_count, digest_only=False):
    snapshot = tmp_path / ('hub/models--EXAMPLE--model/snapshots/' + 'a' * 40)
    snapshot.mkdir(parents=True); (snapshot / 'EXAMPLE.txt').write_text('EXAMPLE only, not a model')
    env = environment(engine={'name': 'vllm', 'version': 'EXAMPLE', 'image_digest': ('sha256:' + 'b' * 64) if digest_only else ('example/engine@sha256:' + 'b' * 64)},
        runtime={'gpu_count': gpu_count}, model={'id': 'EXAMPLE/model', 'revision': 'a' * 40, 'inventory_sha256': inventory(snapshot)['inventory_sha256']})
    return env, {'vllm': {'models_dir': str(tmp_path), 'image': 'example/engine@sha256:' + 'b' * 64}}


def test_digest_identity_and_launch_locator_are_bound_without_editing_env(tmp_path):
    env, cfg = model_cache(tmp_path, gpu_count=0, digest_only=True)
    ref = env.ref(); options = get_provider('vllm').prepare_managed(env, cfg)
    assert options['image'] == cfg['vllm']['image'] and env.ref() == ref
    cfg['vllm']['image'] = 'example/engine@sha256:' + 'c' * 64
    with pytest.raises(ValueError, match='LOCATOR'): get_provider('vllm').prepare_managed(env, cfg)


def test_gpu_count_requires_actual_device_assignment_and_bounded_launch(tmp_path):
    env, cfg = model_cache(tmp_path, gpu_count=2)
    with pytest.raises(ValueError, match='GPU_ASSIGNMENT'): get_provider('vllm').prepare_managed(env, cfg)
    cfg['vllm'].update(gpu_devices=['0', '1'], resources={'cpus': 2, 'memory_bytes': 1024**3, 'pids_limit': 128})
    plan = get_provider('vllm').launch_plan(get_provider('vllm').prepare_managed(env, cfg))
    commands = []
    def record(argv, **kwargs): commands.append(argv); return CmdResult(0, 'EXAMPLE-container', '')
    Docker('example-gpu-allocation', runner=record).run_detached(plan.image, 'engine', networks=[], timeout=1,
        command=plan.argv, gpus=plan.gpus, gpu_devices=plan.gpu_devices, limits=plan.limits)
    assert commands[0][commands[0].index('--gpus') + 1] == '"device=0,1"'
    assert '--memory' in commands[0] and plan.gpu_devices == ('0', '1')


def test_opaque_artifact_mapping_and_tokenizer_change_have_explicit_identity():
    env = environment(model={'id': 'registry.internal/customer/compiled-artifact', 'revision': 'build-2026.11.08', 'inventory_sha256': 'a' * 64})
    mapping = {'schema': 'guard.model-request-mapping/2', 'environment_ref': env.ref().model_dump(mode='json'),
        'artifact': {'kind': 'opaque-model-build', 'identity': env.model.id, 'revision': env.model.revision, 'sha256': env.model.inventory_sha256},
        'served_model_name': 'customer-model', 'host_snapshot': '/models/customer', 'container_mount': '/model'}
    assert validate_model_mapping(env, mapping) == mapping and request_model_name(env, mapping) == 'customer-model'
    newer = env.model_copy(update={'id': 'ENV-NEW', 'model': env.model.model_copy(update={'tokenizer_revision': 'tokenizer-build-2'})}).seal()
    pol = policy(baseline_pack().ref())
    assert input_diff(env, newer, pol, pol).model_changed
    assert model_closure_sha256(env.model) == env.model.inventory_sha256
    assert model_closure_sha256(newer.model) != model_closure_sha256(env.model)


@pytest.mark.parametrize('mutation', ['unbound', 'cycle', 'duplicate'])
def test_model_component_graph_cannot_claim_an_ambiguous_or_open_closure(mutation):
    component = {'role': 'compiled-engine', 'kind': 'EXAMPLE-build', 'identity': 'EXAMPLE', 'sha256': 'b' * 64, 'inputs': ['a' * 64]}
    components = [component]
    if mutation == 'unbound': component['inputs'] = ['c' * 64]
    if mutation == 'cycle': component['inputs'] = ['b' * 64]
    if mutation == 'duplicate': components.append(deepcopy(component))
    with pytest.raises(ValueError, match='MODEL_COMPONENT'):
        environment(model={'inventory_sha256': 'a' * 64, 'components': components})


def test_driver_and_validity_changes_are_selection_inputs():
    env = environment(); pol = policy(baseline_pack().ref())
    changed = env.model_copy(update={'engine': env.engine.model_copy(update={'runtime_kind': 'example-next-driver'})}).seal()
    newer = pol.model_copy(update={'valid_from': T0}).seal()
    diff = input_diff(env, changed, pol, newer)
    assert 'engine.runtime_kind' in diff.runtime and diff.policy_metadata_changed


def test_regenerating_inventory_does_not_generate_review_approval(tmp_path):
    from tools.verify_req10_consumption import approved_manifest
    manifest = tmp_path / 'update/release/reviewed-source.json'; manifest.parent.mkdir(parents=True)
    receipt = tmp_path / 'receipts.json'; receipt.write_text('{"schema":"byul-source-review-receipts/1","receipts":[]}')
    manifest.write_text('{"EXAMPLE":"before"}')
    assert approved_manifest(tmp_path, receipt) is None
    digest = sha256_hex(manifest.read_bytes())
    receipt.write_text(json.dumps({'schema': 'byul-source-review-receipts/1', 'receipts': [{'manifest_sha256': digest, 'reviewed_by': 'EXAMPLE fixture review', 'reviewed_at': T0.isoformat()}]}))
    assert approved_manifest(tmp_path, receipt) == digest
    manifest.write_text('{"EXAMPLE":"after regeneration"}')
    assert approved_manifest(tmp_path, receipt) is None


def test_resource_lease_keeps_release_uncertainty_until_verified_recovery(tmp_path):
    from guard.runtime.resource_lease import ResourceLease
    identity = {'kind': 'EXAMPLE-only', 'id': 'EXAMPLE-shared-device'}
    lease = ResourceLease(tmp_path, identity)
    with pytest.raises(RuntimeError):
        with lease.acquire(): raise RuntimeError('EXAMPLE interrupted owner')
    with pytest.raises(ValueError, match='RECOVERY_REQUIRED'):
        with ResourceLease(tmp_path, identity).acquire(): pass
    with pytest.raises(ValueError, match='STILL_UNVERIFIED'): lease.recover(lambda owner: False)
    assert lease.fence.is_file()
    lease.recover(lambda owner: owner == 'interrupted-coordinator')
    assert not lease.fence.exists() and len(list((tmp_path / 'recovered').iterdir())) == 2
    with lease.acquire(): pass


def test_coordinator_hard_exit_cannot_silently_release_the_resource_lease(tmp_path):
    import subprocess, sys
    from guard.runtime.resource_lease import ResourceLease
    identity = {'kind': 'EXAMPLE-no-device', 'id': 'EXAMPLE-hard-exit'}
    source = ('from guard.runtime.resource_lease import ResourceLease\nimport os,sys\n'
        'with ResourceLease(sys.argv[1], ' + repr(identity) + ').acquire() as lease:\n'
        ' lease.note_owner("EXAMPLE-owned-label")\n os._exit(17)\n')
    assert subprocess.run([sys.executable, '-c', source, str(tmp_path)]).returncode == 17
    lease = ResourceLease(tmp_path, identity)
    assert lease.active.exists()
    with pytest.raises(ValueError, match='RECOVERY_REQUIRED'):
        with lease.acquire(): pass
    lease.recover(lambda owner: owner == 'EXAMPLE-owned-label')
    assert not lease.active.exists() and not lease.fence.exists()
    with lease.acquire(): pass
