"""SUP-15 owned-record admission through the real read-only executor."""

from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest

from guard import application
from guard.adapters import vllm_file_read_sensor
from guard.adapters.vllm_release_profiles import release_profile
from guard.collectors.lifecycle import load_prepared, prepare_assets
from guard.core.run import _normalize
from guard.executors import registry
from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
from guard.executors.supply_boot_contract import CHECK_PROFILES
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, sha256_hex
from tests.builders import environment, evidence, policy
from tests.test_a1_file_read_sensor import scope_value
from tests.test_b3_supply_records import retained
from tests.test_kubernetes_lifecycle import World
from tests.test_kubernetes_sampling import inputs
from tests.test_lifecycle_capture import Docker, IMAGE, external
from tests.test_supply_boot_record_access import PACK, ROOT, base_values, change, good_packet
from tools import mvp_live_readiness
from tools.j02_run_config import write_config


def _prepare_roles(tmp_path, env, late_owner_change, *, first_owner=1):
    settings, pins, worlds, records = {}, {}, {}, {}
    for index, role in enumerate(("normal", "challenge"), start=first_owner):
        directory = tmp_path / role
        directory.mkdir()
        prepared = prepare_assets(directory / "assets", ROOT.parent)
        engine = inputs()["kubernetes"]["engine"]
        engine.update(namespace=f"guard-{role}", pod=f"engine-{role}", image=env.engine.image_digest,
                      namespace_uid=f"10000000-0000-0000-0000-{index:012d}",
                      pod_uid=f"20000000-0000-0000-0000-{index:012d}",
                      container_id="containerd://" + str(index) * 64,
                      pod_ip=f"127.0.0.{index}", image_id="sha256:" + "c" * 64)
        target = {"schema": "guard-kubernetes-lifecycle-target/1", "lab": True, "run_id": f"guard-{role}",
                  "environment_ref": env.ref().model_dump(mode="json"),
                  "kubernetes": {"context": "fixture-context", "engine": engine}}
        target_path = directory / "target.json"
        target_path.write_bytes(canonical_json(target))
        target_sha256 = sha256_hex(target_path.read_bytes())
        settings[role] = {"target": str(target_path), "prepared": str(directory / "assets" / "manifest.json")}
        pins[f"supply_{role}_target_sha256"] = target_sha256
        pins[f"supply_{role}_prepared_sha256"] = sha256_hex(canonical_json(prepared))

        challenged = role == "challenge"
        packet = good_packet(env.ref().model_dump(mode="json"), target_sha256, "SUP-15")
        packet.update(role=role, change=change(applied=challenged), rows=[{
            "path_sha256": sha256_hex(b"EXAMPLE cache path"), "relevant_change": challenged,
            "reused": True, "compatibility_verified": not challenged,
        }])
        raw = canonical_json(packet)
        world = World(SimpleNamespace(prepared=prepared), target,
                      mode="late-file-owner" if challenged and late_owner_change else None)
        world.docker = Docker(SimpleNamespace(raw=raw))
        worlds[engine["namespace"]] = world
        records[role] = raw
    return settings, pins, worlds, records


def _sup16_roles(root, version):
    env = environment(engine={'name': 'vllm', 'version': version, 'image_digest': release_profile(version)['images'][0]},
        model={'id': 'EXAMPLE-model', 'revision': 'EXAMPLE-revision', 'inventory_sha256': 'a' * 64})
    settings, pins, _, _ = _prepare_roles(root, env, late_owner_change=False)
    return env, settings, pins


@pytest.mark.parametrize('version', ['0.30.0', '0.31.0'])
def test_canonical_supply_prepares_paired_declarations(external, version):
    env, settings, pins = _sup16_roles(external, version)
    check_policy = {
        'LIF-01': {'supply_stage_budget_ms': {'load': 100}, 'supply_stage_budget_bytes': {'load': 1024},
                   'supply_total_budget_ms': 200},
        'LIF-02': {'supply_max_attempts': 2, 'supply_max_fetch_bytes': 1024},
        'PLT-03': {'supply_allowed_objects': ['EXAMPLE-object']},
        'PLT-05': {'supply_allowed_identities': ['EXAMPLE-identity']},
        'SUP-12': {'supply_allowed_readers': ['EXAMPLE-reader']},
    }

    def forbidden(*args, **kwargs):
        pytest.fail('Static role validation dispatched runtime I/O')

    executor = KubernetesSupplyBootExecutor(settings, runner=forbidden)
    for check_id in CHECK_PROFILES:
        values = base_values(env, check_id, {**pins, **check_policy.get(check_id, {})})
        roles = executor.prepared_supply_roles(PACK.checks[check_id], env, values)
        assert set(roles) == {'normal', 'challenge'}, check_id
        assert roles['normal'][1]['session'] != roles['challenge'][1]['session'], check_id


def test_sup06_preflight_checks_only_a_declared_worker_shard_file(external):
    from guard.collectors import worker_shard_producer
    from tests.test_b12_supply_consumers import fixture
    from tests.test_worker_shard import POD, SCOPE

    env, settings, pins = _sup16_roles(external, '0.31.0')
    scope = {**SCOPE, 'environment_ref': env.ref().model_dump(mode='json'),
             'target_sha256': pins['supply_normal_target_sha256'],
             'prepared_sha256': pins['supply_normal_prepared_sha256']}
    witness = fixture('worker_shard_k3d_20261108.json')['cases']['sharded']['rows']
    record, source = worker_shard_producer.record(witness, pod=POD, **scope)
    assert not source['supported'] and not source['producer_authenticated']
    path = external / 'worker-shard-input.json'
    raw = canonical_json(record)
    path.write_bytes(raw)
    settings['normal']['worker_shard_input'] = {'path': str(path), 'sha256': sha256_hex(raw), 'scope': scope}

    def forbidden(*args, **kwargs):
        pytest.fail('Static role validation dispatched runtime I/O')

    executor = KubernetesSupplyBootExecutor(settings, runner=forbidden)
    values = base_values(env, 'SUP-06', pins)
    assert set(executor.prepared_supply_roles(PACK.checks['SUP-06'], env, values)) == {'normal', 'challenge'}
    with pytest.raises(ValueError, match='^SUPPLY_BOOT_INPUT_UNAVAILABLE$'):
        executor.prepared_supply_roles(PACK.checks['SUP-09'], env, values)
    path.write_bytes(raw + b'\n')
    with pytest.raises(ValueError, match='^B_WORKER_SHARD_INPUT_UNAVAILABLE$'):
        executor.prepared_supply_roles(PACK.checks['SUP-06'], env, values)


@pytest.mark.parametrize(('case', 'version'), [
    ('matched', '0.30.0'), ('matched', '0.31.0'), ('other-manifest', '0.31.0'),
    ('wrong-session', '0.31.0'), ('other-record-owner', '0.31.0'),
    ('other-callback-target', '0.31.0'), ('disabled', '0.31.0'),
])
def test_sup06_boot_role_consumes_callback_bound_worker_shard_prefix(external, monkeypatch, case, version):
    from guard.collectors import engine_callbacks, worker_shard_producer
    from tests.test_b12_supply_consumers import fixture
    from tests.test_kubernetes_request_binding import target as callback_target_fixture

    env, settings, pins = _sup16_roles(external, version)
    callback_root = external / 'callback-assets'
    callback_manifest = engine_callbacks.prepare_assets(callback_root, worker_shards=case != 'disabled')
    manifest_path = callback_root / 'manifest.json'
    callback_digest = sha256_hex(canonical_json(callback_manifest))
    assert callback_digest != pins['supply_normal_prepared_sha256']
    session = callback_manifest['session']
    if case == 'wrong-session':
        session = ('0' if session[0] != '0' else '1') + session[1:]
    if case == 'other-manifest':
        other = external / 'other-callback-assets'
        other_manifest = engine_callbacks.prepare_assets(other, worker_shards=True)
        assert sha256_hex(canonical_json(other_manifest)) != callback_digest
        manifest_path = other / 'manifest.json'

    normal = json.loads(Path(settings['normal']['target']).read_bytes())
    owner = normal['kubernetes']['engine']
    callback_target = callback_target_fixture(env)
    callback_target['observed_version'] = version
    callback_target['kubernetes'].update(context=normal['kubernetes']['context'], run_id=normal['run_id'],
                                         engine=deepcopy(owner))
    callback_target['engine'].update(image_digest=env.engine.image_digest, trusted_url=f"http://{owner['pod_ip']}:8000")
    challenge = json.loads(Path(settings['challenge']['target']).read_bytes())['kubernetes']['engine']
    if case == 'other-callback-target':
        callback_target['kubernetes']['engine']['pod_uid'] = challenge['pod_uid']
        owner = callback_target['kubernetes']['engine']
    elif case == 'other-record-owner':
        owner = challenge
    callback_target_path = external / 'callback-target.json'
    callback_target_bytes = canonical_json(callback_target)
    callback_target_path.write_bytes(callback_target_bytes)
    scope = {'environment_ref': env.ref().model_dump(mode='json'),
             'target_sha256': sha256_hex(callback_target_bytes),
             'prepared_sha256': callback_digest, 'session': session}
    assert scope['target_sha256'] != pins['supply_normal_target_sha256']
    processes = {}
    # Reuse the recorded event shapes as offline examples for either target declaration.
    witness = fixture('worker_shard_k3d_20261108.json')['cases']['sharded']['rows']
    for row in witness:
        fields = row.get('fields')
        if fields is None:
            continue
        pid, ticks = fields['pid'], fields['start_ticks']
        process = processes.setdefault((pid, ticks), {'pid': pid, 'start_ticks': ticks, 'events': []})
        process['events'].append({**row, 'session_sha256': sha256_hex(session.encode())})
    record, source = worker_shard_producer.record_from_processes(list(processes.values()), pod=owner, **scope)
    assert not record['completeness']['source_ended']
    assert not source['supported'] and not source['producer_authenticated']
    required = {row['shard_sha256'] for row in record['events'] if row['event'] == 'shard_required'}
    assigned = {row['shard_sha256'] for row in record['events'] if row['event'] == 'shard_assigned'}
    assert len(required) == 2 and required == assigned
    worker_path = external / 'callback-worker-shard-input.json'
    raw = canonical_json(record)
    worker_path.write_bytes(raw)
    settings['normal']['worker_shard_input'] = {'path': str(worker_path), 'sha256': sha256_hex(raw),
        'scope': scope, 'callback_prepared': str(manifest_path), 'callback_target': str(callback_target_path)}

    def forbidden(*args, **kwargs):
        pytest.fail('Offline callback role fixture attempted runtime I/O')

    executor = KubernetesSupplyBootExecutor(settings, runner=forbidden)
    check = PACK.checks['SUP-06']
    values = base_values(env, check.id, pins)
    if case == 'matched':
        assert set(executor.prepared_supply_roles(check, env, values)) == {'normal', 'challenge'}
    else:
        with pytest.raises(ValueError, match='^B_WORKER_SHARD_INPUT_UNAVAILABLE$'):
            executor.prepared_supply_roles(check, env, values)

    def unavailable():
        raise ValueError('EXAMPLE runtime owner unavailable')

    monkeypatch.setattr(executor, '_role_reader', lambda *args: SimpleNamespace(snapshot=unavailable))
    part = executor._collect_role(check, env, values, 'normal')
    row = part['row']
    assert part['packet'] is None and not row['collected'] and not row['owner_binding_verified']
    assert 'SUPPLY_BOOT_PREFLIGHT_UNAVAILABLE' in row['diagnostic_codes']
    if case != 'matched':
        assert 'B_WORKER_SHARD_INPUT_UNAVAILABLE' in row['diagnostic_codes']
        assert 'worker_shards' not in part and 'worker_shard_input' not in row
        return

    retained_input = row['worker_shard_input']
    assert retained_input['data_origin'] == 'EXAMPLE' and retained_input['record_sha256'] == sha256_hex(raw)
    assert not retained_input['complete'] and not retained_input['violations']
    reported = retained_input['reported_source']
    assert reported['file_sha256'] == sha256_hex(raw) and reported['target_sha256'] == scope['target_sha256']
    assert reported['prepared_sha256'] == callback_digest and reported['session_sha256'] == sha256_hex(session.encode())
    assert reported['environment_ref'] == scope['environment_ref'] and reported['events'] == record['events']
    assert not reported['owner_binding_verified']


@pytest.mark.parametrize(('check_id', 'version'), [('SUP-09', '0.30.0'), ('SUP-11', '0.31.0')])
def test_dynamic_supply_prepares_distinct_declared_challenge_roles(external, check_id, version):
    env, settings, pins = _sup16_roles(external, version)
    settings['challenge']['target'] = settings['normal']['target']
    pins['supply_challenge_target_sha256'] = pins['supply_normal_target_sha256']

    def forbidden(*args, **kwargs):
        pytest.fail('Static role validation dispatched runtime I/O')

    check = PACK.checks[check_id]
    executor = KubernetesSupplyBootExecutor(settings, runner=forbidden)
    roles = executor.prepared_supply_roles(check, env, base_values(env, check_id, pins))
    assert roles['normal'][0] == roles['challenge'][0]
    assert roles['normal'][1]['session'] != roles['challenge'][1]['session']

    settings['challenge']['prepared'] = settings['normal']['prepared']
    pins['supply_challenge_prepared_sha256'] = pins['supply_normal_prepared_sha256']
    alias = KubernetesSupplyBootExecutor(settings, runner=forbidden)
    with pytest.raises(ValueError, match='^SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED$'):
        alias.prepared_supply_roles(check, env, base_values(env, check_id, pins))


@pytest.mark.parametrize(('version', 'mode', 'error'), [
    ('0.30.0', 'ordinary', None), ('0.31.0', 'same-owner-distinct-sessions', None),
    ('0.31.0', 'same-location', 'SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED'),
    ('0.31.0', 'native-scope', None), ('0.31.0', 'foreign-native-scope', 'MODEL_READ_SCOPE_TARGET_MISMATCH'),
    ('0.30.0', 'foreign-owner', 'SUPPLY_BOOT_INPUT_UNAVAILABLE'),
    ('0.31.0', 'missing-prepared', 'SUPPLY_BOOT_INPUT_UNAVAILABLE'),
])
def test_sup16_prepares_both_native_role_declarations_without_runtime(external, version, mode, error):
    env, settings, pins = _sup16_roles(external, version)
    if mode == 'same-location':
        settings['challenge'] = dict(settings['normal'])
        for kind in ('target', 'prepared'):
            pins[f'supply_challenge_{kind}_sha256'] = pins[f'supply_normal_{kind}_sha256']
    if mode == 'same-owner-distinct-sessions':
        settings['challenge']['target'] = settings['normal']['target']
        pins['supply_challenge_target_sha256'] = pins['supply_normal_target_sha256']
    if mode == 'foreign-owner':
        path = Path(settings['challenge']['target'])
        target = json.loads(path.read_bytes())
        target['environment_ref']['sha256'] = '0' * 64
        path.write_bytes(canonical_json(target))
        pins['supply_challenge_target_sha256'] = sha256_hex(path.read_bytes())
    if mode in {'native-scope', 'foreign-native-scope'}:
        target = json.loads(Path(settings['challenge']['target']).read_bytes())
        scope = scope_value(external / 'example.safetensors', env=env, target=target)
        if mode == 'foreign-native-scope':
            scope['target_sha256'] = '0' * 64
        scope_path = external / 'scope.json'
        scope_path.write_bytes(canonical_json(scope))
        assets = external / 'native-assets'
        prepared = prepare_assets(assets, ROOT.parent, profile=vllm_file_read_sensor.PROFILE, model_read_scope=scope_path)
        settings['challenge']['prepared'] = str(assets / 'manifest.json')
        pins['supply_challenge_prepared_sha256'] = sha256_hex(canonical_json(prepared))
    if mode == 'missing-prepared':
        settings['challenge']['prepared'] = str(external / 'unavailable.json')

    def forbidden(*args, **kwargs):
        pytest.fail('Static role validation dispatched runtime I/O')

    check = PACK.checks['SUP-16']
    executor = KubernetesSupplyBootExecutor(settings, runner=forbidden)
    values = base_values(env, check.id, pins)
    if error is not None:
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.prepared_supply_roles(check, env, values)
    else:
        roles = executor.prepared_supply_roles(check, env, values)
        assert set(roles) == {'normal', 'challenge'}
        assert all(row[0]['environment_ref'] == env.ref().model_dump(mode='json') for row in roles.values())
        assert all(not row[1]['engine_started'] and not row[1]['image_contents_verified'] for row in roles.values())
        if mode == 'native-scope':
            assert roles['challenge'][1]['profile'] == vllm_file_read_sensor.PROFILE
        if mode == 'same-owner-distinct-sessions':
            assert roles['normal'][0] == roles['challenge'][0]
            assert roles['normal'][1]['session'] != roles['challenge'][1]['session']


@pytest.mark.parametrize('sealed', [False, True])
def test_sup16_only_sealed_runtime_rejects_same_location_before_reads(external, tmp_path, monkeypatch, sealed):
    env, settings, pins = _sup16_roles(external, '0.31.0')
    challenge = Path(settings['challenge']['target'])
    target = json.loads(Path(settings['normal']['target']).read_bytes())
    target['kubernetes']['context'] = 'EXAMPLE-context-alias'
    challenge.write_bytes(canonical_json(target))
    settings['challenge']['prepared'] = settings['normal']['prepared']
    pins['supply_challenge_target_sha256'] = sha256_hex(challenge.read_bytes())
    pins['supply_challenge_prepared_sha256'] = pins['supply_normal_prepared_sha256']
    raw_pins = {role: sha256_hex(Path(entry['prepared']).read_bytes()) for role, entry in settings.items()}
    executor = KubernetesSupplyBootExecutor(settings, expected_prepared_sha256_by_role=raw_pins if sealed else None)
    check = PACK.checks['SUP-16']
    values = base_values(env, check.id, pins)
    attempted = []

    def unavailable(target, selected_env, prepared):
        attempted.append(target['kubernetes']['context'])
        raise ValueError('EXAMPLE no runtime')

    monkeypatch.setattr(executor, '_role_reader', unavailable)
    if sealed:
        with pytest.raises(ValueError, match='^SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED$'):
            executor.run(check, env, values, None, tmp_path / 'evidence')
        assert attempted == []
    else:
        draft = executor.run(check, env, values, None, tmp_path / 'evidence')
        assert attempted == ['fixture-context', 'EXAMPLE-context-alias']
        assert draft.status is ExecStatus.ABORTED and draft.data_origin is DataOrigin.EXAMPLE


def test_sup16_raw_prepared_pin_checks_exact_read_and_preserves_other_role(external, tmp_path, monkeypatch):
    env, settings, pins = _sup16_roles(external, '0.31.0')
    raw_pins = {role: sha256_hex(Path(entry['prepared']).read_bytes()) for role, entry in settings.items()}
    executor = KubernetesSupplyBootExecutor(settings, expected_prepared_sha256_by_role=raw_pins)
    check = PACK.checks['SUP-16']
    values = base_values(env, check.id, pins)
    roles = executor.prepared_supply_roles(check, env, values)
    path = Path(settings['challenge']['prepared'])
    path.write_bytes(path.read_bytes() + b'\n')
    assert load_prepared(path) == roles['challenge'][1]
    with pytest.raises(ValueError, match='^SUPPLY_BOOT_INPUT_UNAVAILABLE$'):
        executor.prepared_supply_roles(check, env, values)

    seen = []
    def unavailable(target, selected_env, prepared):
        seen.append((target, selected_env, prepared))
        raise ValueError('EXAMPLE no runtime')
    monkeypatch.setattr(executor, '_role_reader', unavailable)
    draft = executor.run(check, env, values, None, tmp_path / 'evidence')
    normal, challenge = draft.observations['supply_boot_sessions'].value
    assert seen == [(roles['normal'][0], env, roles['normal'][1])]
    assert normal['diagnostic_codes'] == ['SUPPLY_BOOT_PREFLIGHT_UNAVAILABLE']
    assert challenge['diagnostic_codes'] == ['SUPPLY_BOOT_INPUT_UNAVAILABLE']
    assert draft.status is ExecStatus.ABORTED and draft.data_origin is DataOrigin.EXAMPLE


@pytest.mark.parametrize(("candidate_input", "prepare_gap", "audit_gap"), [
    ("distinct", None, None),
    ("alias", "SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED", "SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED"),
    ("foreign-target", "SUPPLY_BOOT_INPUT_UNAVAILABLE", "SUPPLY_BOOT_INPUT_UNAVAILABLE"),
    ("missing-preparation", "RUN_BINDING_UNAVAILABLE", "MA7_INPUT_INVALID"),
    ("changed-snapshot", None, "MVP_INPUT_FILE_CHANGED"),
])
def test_sup15_offline_readiness_binds_both_declared_roles(
        external, monkeypatch, candidate_input, prepare_gap, audit_gap):
    def no_runtime(*args, **kwargs):
        pytest.fail("SUP-15 file readiness must not contact or run a target")

    monkeypatch.setattr(subprocess, "Popen", no_runtime)
    monkeypatch.setattr(socket, "socket", no_runtime)
    producer = external / "producer.py"
    producer.write_bytes(b"# EXAMPLE boot-record source\n")
    check_id = "SUP-15"
    pol = policy(PACK.ref(), scope={"include": [check_id]}, values={})
    policy_path = external / "policy.json"
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    cfg = {"adapter": {"name": "vllm"}, "executors": {"supply_boot": "kubernetes"},
           "update": {"mode": "external", "allowed_checks": [check_id]},
           "run_binding": {check_id: {"record_producer": str(producer)}}, "sides": {}}
    envs, entries = [], []
    config_path = external / "guard.toml"
    for side, release in mvp_live_readiness.versions().items():
        root = external / side
        root.mkdir()
        env = environment(id=f"ENV-EXAMPLE-SUP15-{side.upper()}",
            engine={"name": release["engine"], "version": release["version"], "image_digest": release["image"]},
            runtime={"gpu_count": 1, "gpu_model": "EXAMPLE GPU", "driver": "EXAMPLE driver"})
        env_path = root / "env.json"
        env_path.write_bytes(canonical_json(env.to_dict()))
        settings, _, _, _ = _prepare_roles(root, env, late_owner_change=False, first_owner=1 + 2 * len(envs))
        if side == "candidate":
            target_path = Path(settings["challenge"]["target"])
            if candidate_input == "alias":
                alias = json.loads(Path(settings["normal"]["target"]).read_bytes())
                alias["kubernetes"]["context"] = "fixture-alias-context"
                target_path.write_bytes(canonical_json(alias))
                settings["challenge"]["prepared"] = settings["normal"]["prepared"]
            elif candidate_input == "foreign-target":
                target = json.loads(target_path.read_bytes())
                target["environment_ref"] = envs[0].ref().model_dump(mode="json")
                target_path.write_bytes(canonical_json(target))
            elif candidate_input == "missing-preparation":
                settings["challenge"]["prepared"] = str(root / "missing-manifest.json")
        cfg["sides"][side] = {"supply_boot": settings}
        envs.append(env)
        entries.append((side, env_path))

    prepared = application.update_readiness(PACK, envs, pol, cfg)
    inputs = next(item for item in prepared["checks"] if item["check"] == "inputs")
    assert inputs["ok"] is (prepare_gap is None), inputs
    if prepare_gap is not None:
        assert inputs["detail"] == prepare_gap

    write_config(cfg, config_path)
    index = mvp_live_readiness.template(PACK)
    index["inputs"] = [{"check_id": check_id, "side": side, **{
        role: {"path": str(path), "sha256": sha256_hex(path.read_bytes())}
        for role, path in {"environment": env, "policy": policy_path, "configuration": config_path}.items()
    }} for side, env in entries]
    index_path = external / "index.json"
    index_path.write_bytes(canonical_json(index))
    if candidate_input == "changed-snapshot":
        prepared_target = Path(cfg["sides"]["candidate"]["supply_boot"]["challenge"]["target"])
        read = mvp_live_readiness._read

        def replace_after_snapshot(path):
            raw, identity = read(path)
            if Path(path) == prepared_target:
                target = json.loads(raw)
                target["kubernetes"]["context"] = "fixture-new-context"
                prepared_target.write_bytes(canonical_json(target))
            return raw, identity

        monkeypatch.setattr(mvp_live_readiness, "_read", replace_after_snapshot)
    report = mvp_live_readiness.audit(index_path, pack=PACK)
    assert report["sides"] == 138 and report["active_checks"] == 69
    assert report["file_binding_valid_sides"] == (2 if audit_gap is None else 1)
    for row in report["rows"]:
        if row["check_id"] != check_id:
            continue
        assert row["ready_for_execution"] is row["live_target_verified"] is row["engine_executed"] is False
        if row["side"] == "candidate" and audit_gap is not None:
            assert row["blocking_codes"] == [audit_gap]
        else:
            assert row["binding_schema"] == "guard-run-binding/2"
            assert set(row["blocking_codes"]) == {"EXAMPLE_ENV_NOT_ACTUAL", "LIVE_EXECUTOR_PREFLIGHT_REQUIRED"}


@pytest.mark.parametrize(('same_owner', 'same_session'), [(True, True), (True, False), (False, True)])
def test_sup15_rejects_only_role_aliases_at_the_same_owned_record_location(
        tmp_path, external, monkeypatch, same_owner, same_session):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE})
    settings, pins, _, _ = _prepare_roles(external, env, late_owner_change=False)
    _, _, archived_roles = retained('SUP-15', tmp_path)
    if same_owner:
        normal_target = json.loads(Path(settings['normal']['target']).read_bytes())
        # Context aliases can address the same exact namespace, Pod and container.
        normal_target['kubernetes']['context'] = 'fixture-alias-context'
        challenge_target = Path(settings['challenge']['target'])
        challenge_target.write_bytes(canonical_json(normal_target))
        pins['supply_challenge_target_sha256'] = sha256_hex(challenge_target.read_bytes())
    if same_session:
        settings['challenge']['prepared'] = settings['normal']['prepared']
        pins['supply_challenge_prepared_sha256'] = pins['supply_normal_prepared_sha256']

    check = PACK.checks['SUP-15']
    executor = registry.build_executor('supply_boot', 'kubernetes', {'supply_boot': settings})
    calls = []
    def replay(check, environment, values, role, *, role_inputs):
        assert role_inputs[role] is not None
        calls.append(role)
        return deepcopy(archived_roles[role])
    monkeypatch.setattr(executor, '_collect_role', replay)
    values = base_values(env, check.id, pins)
    if same_owner and same_session:
        with pytest.raises(ValueError, match='SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED'):
            executor.run(check, env, values, None, tmp_path / 'evidence')
        assert calls == []
    else:
        draft = executor.run(check, env, values, None, tmp_path / 'evidence')
        assert calls == ['normal', 'challenge'] and draft.data_origin is DataOrigin.EXAMPLE
        assert not draft.observations['supply_boot_complete'].value


@pytest.mark.parametrize("late_owner_change", [False, True], ids=["stable-owner", "late-owner-change"])
def test_sup15_requires_owner_after_record_read_and_keeps_earlier_role(tmp_path, external, late_owner_change):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE})
    settings, pins, worlds, records = _prepare_roles(external, env, late_owner_change)

    def runner(argv, **kwargs):
        namespace = argv[5] if argv[3:5] == ["get", "namespace"] else argv[3].removeprefix("--namespace=")
        return worlds[namespace].runner(argv, **kwargs)

    executor = registry.build_executor("supply_boot", "kubernetes", {"supply_boot": settings})
    executor.runner = runner
    check = PACK.checks["SUP-15"]
    values = base_values(env, check.id, pins)
    draft = executor.run(check, env, values, None, tmp_path / "evidence")
    normal, challenge = draft.observations["supply_boot_sessions"].value
    artifacts = {artifact.name: artifact.path for artifact in draft.artifacts}
    assert draft.data_origin is DataOrigin.EXAMPLE
    assert [world.files for world in worlds.values()] == [1, 1]
    assert worlds["guard-challenge"].bad_owner is late_owner_change
    assert normal["collected"] and normal["owner_binding_verified"]
    assert normal["record_sha256"] == sha256_hex(records["normal"])
    assert artifacts["supply_boot_normal_record.json"].read_bytes() == records["normal"]
    assert draft.observations["supply_boot_record_authenticated"].value is False
    projected = _normalize(check, draft)
    evd = evidence(check_id=check.id, status=draft.status, data_origin=draft.data_origin,
                   normal=projected["normal_control"], observations=projected["observations"])
    verdict = RuleTableJudge().judge(check, evd, check.judge.params, values).verdicts["security"]

    if late_owner_change:
        assert draft.status is ExecStatus.ABORTED
        assert not challenge["collected"] and not challenge["owner_binding_verified"]
        assert challenge["record_sha256"] is None
        assert "reported_boot_record" not in challenge
        assert "supply_boot_challenge_record.json" not in artifacts
        assert draft.observations["supply_boot_violations"].value == []
        assert verdict is Verdict.UNKNOWN
    else:
        assert draft.status is ExecStatus.COMPLETED
        assert challenge["collected"] and challenge["owner_binding_verified"]
        assert artifacts["supply_boot_challenge_record.json"].read_bytes() == records["challenge"]
        assert verdict is Verdict.FAIL
