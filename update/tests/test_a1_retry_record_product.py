"""LIF12 existing registry/owned file/CLI/exact Ref; fake Pod and producer only."""
import base64
from copy import deepcopy
from datetime import timedelta, timezone
import json
from pathlib import Path
import socket
import subprocess
import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.executors import kubernetes_retry_records as retry_executor
from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
from guard.executors.owned_capture import _FILE_READER, MAX_BYTES
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_a1_required_event_product import World as OriginalWorld, PRIVATE
from tests.test_a1_retry_record_contract import example, bind, automatic, rows_of, append_row
from tests.test_kubernetes_request_binding import env
from tools.build_kubernetes_retry_record_pack import derive


class World(OriginalWorld):
    def _packet(self):
        packet=deepcopy(self.retry_packet);packet['recorded_at']=utcnow().isoformat()
        if self.mode=='declared-forbidden':
            pass  # fixed EXAMPLE policy/config was selected before collection
        elif self.mode=='declared-cost':rows_of(packet,'COST_SAMPLE_CLAIM')[-1]['data']['cumulative_amount']=101
        elif self.mode=='manual-only':
            for row in packet['events']:
                if row['initiator']=='AUTOMATIC':row['initiator']='MANUAL'
        elif self.mode=='missing-event':packet['events']=[r for r in packet['events'] if r['type']!='ATTEMPT_END_CLAIM']
        elif self.mode=='duplicate-event':packet['events'].append(deepcopy(automatic(packet)))
        elif self.mode=='event-conflict':
            row=deepcopy(automatic(packet));row['initiator']='MANUAL';packet['events'].append(row)
        elif self.mode=='seq-gap':packet['events'][-1]['seq']+=1
        elif self.mode=='wrong-actor':automatic(packet)['actor_id']='f'*32
        elif self.mode=='wrong-generation':automatic(packet)['worker_generation']='f'*32
        elif self.mode=='wrong-source':packet['source_sha256']='f'*64
        elif self.mode=='raw-field':automatic(packet)['data']['prompt']=PRIVATE
        elif self.mode=='trust-claim':packet['producer_authenticated']=True
        elif self.mode=='partial':packet['declared_complete']=False
        elif self.mode=='empty':packet['events']=[]
        elif self.mode=='after-invalid' and self.reads>1:packet['schema']='unsupported'
        elif self.mode=='future-snapshot' and self.reads==2:
            packet['recorded_at']=(utcnow()+timedelta(seconds=30)).isoformat()
        elif self.mode=='producer-changed' and self.reads>1:packet['producer_id']='f'*32
        elif self.mode=='snapshot-changed' and self.reads>1:packet['snapshot_generation']='f'*32
        if self.mode in {'restart','pod-replaced'} and self.reads>1:self.after_exec=True
        return packet

    def runner(self,argv,**kwargs):
        if 'input_bytes' in kwargs:raise AssertionError('read-only retry consumer sent HTTP, retry or fault injection')
        if 'exec' in argv:
            self.reads+=1;command=argv[argv.index('--')+1:]
            assert command[:5]==['python3','-I','-S','-B','-c'] and command[5]==supervised_source(_FILE_READER)
            assert command[7:]==['/guard-observation/data/'+self.values['retry_session']+'/required-events.json',str(MAX_BYTES)]
            assert '--container=engine' in argv and '-i' not in argv and '-t' not in argv
            if self.mode=='missing' or self.mode=='recovered' and self.reads==1 or self.mode=='final-missing' and self.reads==2:raise OSError(PRIVATE)
            raw=canonical_json(self._packet());identity={'device':1,'inode':2,'size':len(raw),'mtime_ns':3,'ctime_ns':4}
            if self.mode == 'rotated-record' and self.reads == 2:
                identity['inode'] = 3
            value={'schema':'guard-container-file/2','source_complete':True,'file_identity_stable':True,'truncated':False,
                'nofollow_components':True,'regular_file':True,'raw_base64':base64.b64encode(raw).decode(),
                'raw_retained_bytes':len(raw),'raw_retained_sha256':sha256_hex(raw),'file_before':identity,'file_after':identity,'path_after':identity}
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='record-cleanup':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        return super().runner(argv,**kwargs)


def inputs(tmp_path,mode='normal'):
    environment=env();environment=environment.model_copy(update={'model':environment.model.model_copy(update={'inventory_sha256':'c'*64})}).seal()
    world=World(environment,mode);scope,values,packet,_=example()
    values.update(retry_environment_sha256=environment.ref().sha256,retry_target_sha256=sha256_hex(canonical_json(world.target)))
    scope.update(run_id=world.target['kubernetes']['run_id'],image_digest=world.target['kubernetes']['engine']['image'].rsplit('@',1)[1])
    if mode=='declared-forbidden':
        values['retry_policy_mode']='FORBID'
        for row in rows_of(packet,'CONFIGURATION_CLAIM'):row['data']['mode']='FORBID'
    elif mode=='unresolved':
        values['retry_policy_mode']='UNRESOLVED'
        for row in rows_of(packet,'CONFIGURATION_CLAIM'):row['data']['mode']='UNRESOLVED'
    packet.update(environment_ref=environment.ref().model_dump(mode='json'),target_sha256=values['retry_target_sha256'],run_id=scope['run_id'])
    bind(scope,values,packet)
    path=tmp_path/'retry-scope.json';path.write_bytes(canonical_json(scope))
    assert sha256_hex(path.read_bytes())==values['retry_scope_sha256']
    world.values=values;world.retry_packet=packet
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    return environment,world,scope,values,pack,receipt,path


@pytest.mark.parametrize('case,error', [
    ('ordinary', None),
    ('foreign-run', 'RETRY_CONFIGURATION_INVALID'),
    ('foreign-source', 'RETRY_CONFIGURATION_INVALID'),
    ('malformed-scope', 'RETRY_CONFIGURATION_INVALID'),
    ('target-pin', 'RETRY_CONFIGURATION_INVALID'),
    ('over-budget', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('legacy-stage', 'RETRY_PROFILE_UNSUPPORTED'),
])
def test_retry_readiness_uses_the_exact_owned_scope_without_collecting(tmp_path, monkeypatch, case, error):
    from guard.models.checkdef import PolicyParam

    def forbidden(*args, **kwargs):
        pytest.fail('LIF-12 readiness must not contact the target or read retry records')

    monkeypatch.setattr(http.KubernetesHTTP, '_verify', forbidden)
    monkeypatch.setattr(http.KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(retry_executor.RequiredEventReader, 'read_file', forbidden)
    environment, world, scope, values, _, _, scope_path = inputs(tmp_path)
    decision_key = retry_executor.contract.DECISION_KEY
    values[decision_key] = deepcopy(scope['limits'])
    policy_row = {key: values[key] for key in (decision_key, 'retry_policy_mode', 'retry_max_record_delay_ms')}
    scope['policy_sha256'] = values['retry_policy_sha256'] = sha256_hex(canonical_json(policy_row))
    if case == 'foreign-run':
        scope['run_id'] = 'another-example-run'
    elif case == 'foreign-source':
        scope['producer_source_sha256'] = 'f' * 64
    raw_scope = b'{' if case == 'malformed-scope' else canonical_json(scope)
    scope_path.write_bytes(raw_scope)
    values['retry_scope_sha256'] = sha256_hex(raw_scope)

    target_path = tmp_path / 'saved-target.json'
    target_path.write_bytes(canonical_json(world.target))
    digest = sha256_hex(target_path.read_bytes())
    cfg = {'target': str(target_path), 'retry_scope': str(scope_path),
           'prepared': str(tmp_path / 'inert-prepared-declaration.json'),
           'max_duration_seconds': 301 if case == 'over-budget' else 30}
    executor = KubernetesRequestTerminationExecutor(
        cfg, runner=forbidden, expected_target_sha256='0' * 64 if case == 'target-pin' else None)
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['LIF-12']
    if case == 'legacy-stage':
        check = check.model_copy(update={'policy_params': [
            *check.policy_params, PolicyParam(key='retry_session', type='str')]})
    if error is None:
        executor.prepared_retry_records(check, environment, values, expected_target_sha256=digest)
    else:
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.prepared_retry_records(check, environment, values, expected_target_sha256=digest)


def test_retry_snapshot_scope_keeps_sibling_executors_out_of_the_option():
    from guard.configuration import RETRY_RECORD_SNAPSHOT_OPTION, bounded_metadata_scopes, for_check

    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    allowed = {"LIF-10", "LIF-12", "REQ-12"}
    cfg = {"executors": {"request_termination": "kubernetes"},
           "check_config": {"LIF-12": {RETRY_RECORD_SNAPSHOT_OPTION: True}}}
    scopes = bounded_metadata_scopes([cfg, deepcopy(cfg)], pack, allowed)
    assert all(set(scope) == {"LIF-12"} for scope in scopes)
    assert "request_termination" not in for_check(cfg, "request_termination", "LIF-10")

    for unsafe in ({"request_termination": {RETRY_RECORD_SNAPSHOT_OPTION: True}},
                   {**cfg, "lab_activation": {"EXAMPLE": True}},
                   {**cfg, "update": {"kubernetes_candidate_binding": "staged"}}):
        with pytest.raises(ValueError, match="^RETRY_RECORD_SNAPSHOT_SCOPE_INVALID$"):
            bounded_metadata_scopes([{**cfg, **unsafe}, cfg], pack, allowed)


@pytest.mark.parametrize("pin,target_problem", [
    ("exact", None), ("missing", None), ("different", None),
    ("exact", "missing"), ("exact", "changed"), ("exact", "symlink"),
    ("exact", "prepared-owner"), ("exact", "prepared-environment"),
    ("raw-exact", None), ("raw-exact", "raw-rewrite"),
])
def test_normal_retry_snapshot_reads_only_the_independently_pinned_owner(tmp_path, pin, target_problem):
    from guard.adapters.vllm import VllmAdapter
    from guard.configuration import RETRY_RECORD_SNAPSHOT_OPTION
    from guard.models import DataOrigin

    environment, world, scope, values, _, _, scope_path = inputs(tmp_path)
    world.target["lab"] = False
    values[retry_executor.contract.DECISION_KEY] = deepcopy(scope["limits"])
    digest = sha256_hex(canonical_json(world.target))
    values["retry_target_sha256"] = world.retry_packet["target_sha256"] = digest
    target_path = tmp_path / "normal-target.json"
    target_bytes = canonical_json(world.target)
    raw_pin = None
    if pin == "raw-exact":
        target_bytes += b"\n"
        raw_pin = sha256_hex(target_bytes)
        assert raw_pin != digest
    target_path.write_bytes(target_bytes)
    cfg = {"target": str(target_path), "retry_scope": str(scope_path), RETRY_RECORD_SNAPSHOT_OPTION: True}
    prepared_binding = retry_executor.contract.TARGET_BINDING_KEY
    if target_problem in {"prepared-owner", "prepared-environment"}:
        cfg[prepared_binding] = {"environment_ref": environment.ref().model_dump(mode="json"), "target_sha256": digest}
    selected_pin = {"exact": digest, "raw-exact": digest, "missing": None, "different": "0" * 64}[pin]
    pins = {"expected_target_sha256": selected_pin, "expected_target_file_sha256": raw_pin}
    executor = KubernetesRequestTerminationExecutor(cfg, runner=world.runner, **pins)
    check = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE").checks["LIF-12"]

    if target_problem is not None:
        executor.prepared_retry_records(check, environment, values)
        if target_problem == "missing":
            target_path.unlink()
        elif target_problem == "changed":
            target_path.write_bytes(canonical_json({**world.target, "observed_version": "changed"}))
        elif target_problem == "prepared-owner":
            cfg[prepared_binding]["target_sha256"] = "0" * 64
        elif target_problem == "prepared-environment":
            cfg[prepared_binding]["environment_ref"]["sha256"] = "0" * 64
        elif target_problem == "raw-rewrite":
            target_path.write_bytes(canonical_json(world.target))
        else:
            original = target_path.rename(tmp_path / "same-target-bytes.json")
            target_path.symlink_to(original.name)

    if selected_pin != digest or target_problem is not None:
        def forbidden(*args, **kwargs):
            pytest.fail("An invalid retry snapshot target must be rejected before transport dispatch")

        executor = KubernetesRequestTerminationExecutor(cfg, runner=forbidden, **pins)
        with pytest.raises(ValueError, match="^RETRY_CONFIGURATION_INVALID$"):
            executor.prepared_retry_records(check, environment, values)
        with pytest.raises(ValueError, match="^RETRY_CONFIGURATION_INVALID$"):
            executor.run(check, environment, values, VllmAdapter(), tmp_path / "work")
        assert world.reads == 0 and world.sent == []
        return

    executor.prepared_retry_records(check, environment, values)
    assert world.reads == 0
    draft = executor.run(check, environment, values, VllmAdapter(), tmp_path / "work")
    assert world.reads == 2 and world.sent == []
    assert draft.data_origin is DataOrigin.EXAMPLE and not draft.normal_control.attempted
    assert draft.observations["retry_records_collected"].value is True
    assert draft.observations["automatic_retry_verified"].value is False
    assert draft.observations["retry_scope_complete"].value is False


@pytest.mark.parametrize('mode', ['future-snapshot', 'record-cleanup', 'same-second-record', 'rotated-record', 'after-read',
    'after-policy-window', 'just-before-read-ns', 'exact-read-ns', 'just-after-read-ns'])
def test_retry_snapshot_normalizes_only_qualified_owner_bound_source_receipts(tmp_path, monkeypatch, mode):
    from guard.adapters.vllm import VllmAdapter
    from guard.configuration import RETRY_RECORD_SNAPSHOT_OPTION
    from guard.core.run import _normalize

    environment, world, scope, values, _, _, scope_path = inputs(tmp_path, mode)
    ns_boundary = mode in {'just-before-read-ns', 'exact-read-ns', 'just-after-read-ns'}
    if mode in {'same-second-record', 'rotated-record', 'after-read', 'after-policy-window'} or ns_boundary:
        second = retry_executor.contract.stamp(world.retry_packet['recorded_at'])
        instants = [second + timedelta(microseconds=offset)
                    for offset in (100000, 200000, 500000, 750000, 850000)]
        clock = iter(instants)
        monkeypatch.setattr(retry_executor, '_snapshot_time', lambda: next(clock))
        monkeypatch.setattr(retry_executor, 'utcnow', lambda: second)
        if mode == 'after-policy-window':
            values['retry_validity_end'] = (instants[-1] - timedelta(microseconds=1)).isoformat()
        original_packet = world._packet

        def timestamped_packet():
            packet = original_packet()
            offset = 250000 if world.reads == 1 else 900000 if mode == 'after-read' else 700000
            packet['recorded_at'] = (second + timedelta(microseconds=offset)).isoformat()
            if ns_boundary and world.reads == 2:
                finish = instants[-1].astimezone(timezone(timedelta(hours=5, minutes=30)))
                if mode == 'just-before-read-ns':
                    finish -= timedelta(microseconds=1)
                wall, zone = finish.isoformat(timespec='microseconds').rsplit('+', 1)
                fraction = {'just-before-read-ns': '999', 'exact-read-ns': '000', 'just-after-read-ns': '001'}
                packet['recorded_at'] = wall + fraction[mode] + '+' + zone
            return packet

        monkeypatch.setattr(world, '_packet', timestamped_packet)
    world.target["lab"] = False
    values[retry_executor.contract.DECISION_KEY] = deepcopy(scope["limits"])
    digest = sha256_hex(canonical_json(world.target))
    values["retry_target_sha256"] = world.retry_packet["target_sha256"] = digest
    target_path = tmp_path / "normal-target.json"
    target_path.write_bytes(canonical_json(world.target))
    cfg = {"target": str(target_path), "retry_scope": str(scope_path), RETRY_RECORD_SNAPSHOT_OPTION: True}
    executor = KubernetesRequestTerminationExecutor(cfg, runner=world.runner, expected_target_sha256=digest)
    check = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE").checks["LIF-12"]

    draft = executor.run(check, environment, values, VllmAdapter(), tmp_path / "work")
    assert world.reads == 2 and world.sent == []
    completed = mode in {'same-second-record', 'rotated-record', 'just-before-read-ns', 'exact-read-ns'}
    assert draft.status.value == ("COMPLETED" if completed else "ABORTED")
    assert draft.data_origin.value == "EXAMPLE" and not draft.normal_control.attempted
    first, second = draft.observations["retry_collection"].value
    observations = _normalize(check, draft)["observations"]
    assert observations["activation_scope_complete"] == {"value": False, "collected": True}
    assert observations["activation_violations"] == {"value": None, "collected": False}
    assert draft.observations["retry_nonoccurrence_verified"].value is False
    if completed:
        snapshots = draft.observations["retry_record_snapshots"].value
        steps = observations["activation_steps"]["value"]
        assert [step["stage"] for step in steps] == ["before", "after"]
        binding = world.target['kubernetes']['engine']
        owner = {key: binding[key] for key in ('namespace_uid', 'pod_uid', 'container', 'container_id',
                                               'node', 'restart_count', 'started_at')}
        owner.update(transport='kubernetes', image_reference=binding['image'], reported_image_id=binding['image_id'])
        owner_sha256 = sha256_hex(canonical_json(owner))
        for index, (step, snapshot) in enumerate(zip(steps, snapshots, strict=True), 1):
            raw = canonical_json(snapshot['packet'])
            identity = {'device': 1, 'inode': 3 if mode == 'rotated-record' and index == 2 else 2,
                        'size': len(raw), 'mtime_ns': 3, 'ctime_ns': 4}
            assert step["started_at"] == instants[2 * index - 1].isoformat()
            assert step["finished_at"] == instants[2 * index].isoformat()
            assert step['file_bytes_sha256'] == sha256_hex(raw)
            assert step['owner_sha256'] == owner_sha256
            assert step['file_identity_sha256'] == sha256_hex(canonical_json(identity))
            receipt = (first, second)[index - 1]
            assert receipt['owner_sha256'] == owner_sha256
            assert receipt['file_identity_sha256'] == step['file_identity_sha256']
        if mode == 'rotated-record':
            assert steps[0]['file_identity_sha256'] != steps[1]['file_identity_sha256']
        return
    if mode == 'record-cleanup':
        assert not first["collected"] and not second["collected"]
        assert all({'owner_sha256', 'file_identity_sha256'}.isdisjoint(row) for row in (first, second))
        assert observations["activation_steps"] == {"value": None, "collected": False}
        assert "RETRY_RECORD_PARENT_CLEANUP_UNVERIFIED" in observations["activation_missing"]["value"]
        assert "RETRY_RECORD_PARENT_CLEANUP_UNVERIFIED" in draft.cleanup_errors
        return
    snapshot, = draft.observations["retry_record_snapshots"].value
    step, = observations["activation_steps"]["value"]
    assert step["step"] == "owned-retry-record-read" and step["stage"] == "before"
    assert step["file_bytes_sha256"] == first["file_bytes_sha256"]
    assert step["finished_at"] == first["source_read_finished_at"]
    expected_gap = "RETRY_RECORD_UNAVAILABLE" if mode == 'after-policy-window' else "RETRY_RECORD_SOURCE_TIME_UNVERIFIED"
    assert expected_gap in observations["activation_missing"]["value"]
    assert first["stage"] == snapshot["stage"] == "before" and first["owner_binding_verified"]
    assert first["collected_at"] <= first["source_read_finished_at"]
    assert snapshot["packet"]["recorded_at"] <= first["source_read_finished_at"]
    assert first["file_bytes_sha256"] == sha256_hex(canonical_json(snapshot["packet"]))
    assert second["stage"] == "after" and not second["collected"] and second["file_bytes_sha256"] is None
    assert {'owner_sha256', 'file_identity_sha256'}.isdisjoint(second)
    assert draft.observations["retry_event_declarations"].value


@pytest.mark.parametrize('publication_ns,allowed', [
    pytest.param(100_000_000, False, id='lower-minus-1ns'),
    pytest.param(100_000_001, True, id='lower-equality'),
    pytest.param(100_000_002, True, id='lower-plus-1ns'),
])
def test_retry_owned_read_uses_the_exact_fractional_policy_start(tmp_path, monkeypatch, publication_ns, allowed):
    from guard.adapters.vllm import VllmAdapter
    from guard.configuration import RETRY_RECORD_SNAPSHOT_OPTION
    from guard.core.run import _normalize

    contract = retry_executor.contract
    environment, world, scope, values, _, _, scope_path = inputs(tmp_path)
    second = contract.stamp(world.retry_packet['recorded_at']).astimezone(timezone.utc).replace(microsecond=0)
    instants = iter(second + timedelta(microseconds=offset)
                    for offset in (200000, 300000, 400000, 500000, 600000))
    monkeypatch.setattr(retry_executor, '_snapshot_time', lambda: next(instants))
    monkeypatch.setattr(retry_executor, 'utcnow', lambda: second + timedelta(microseconds=200000))

    def stamp_at(nanoseconds):
        return f'{second:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z'

    values['retry_validity_start'] = stamp_at(100_000_001)
    for row in world.retry_packet['events']:
        row['at'] = stamp_at(100_000_001)
    original = world._packet
    monkeypatch.setattr(world, '_packet', lambda: {
        **original(), 'recorded_at': stamp_at(publication_ns if world.reads == 1 else 150_000_000)})
    world.target['lab'] = False
    values[contract.DECISION_KEY] = deepcopy(scope['limits'])
    digest = sha256_hex(canonical_json(world.target))
    values['retry_target_sha256'] = world.retry_packet['target_sha256'] = digest
    target_path = tmp_path / 'normal-target.json'
    target_path.write_bytes(canonical_json(world.target))
    cfg = {'target': str(target_path), 'retry_scope': str(scope_path), RETRY_RECORD_SNAPSHOT_OPTION: True}
    executor = KubernetesRequestTerminationExecutor(cfg, runner=world.runner, expected_target_sha256=digest)
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['LIF-12']

    draft = executor.run(check, environment, values, VllmAdapter(), tmp_path / 'work')
    assert world.reads == 2 and world.sent == []
    assert draft.data_origin.value == 'EXAMPLE' and not draft.normal_control.attempted
    observations = _normalize(check, draft)['observations']
    expected = ['before', 'after'] if allowed else ['after']
    assert [row['stage'] for row in draft.observations['retry_record_snapshots'].value] == expected
    assert [row['stage'] for row in observations['activation_steps']['value']] == expected
    assert ('RETRY_RECORD_SOURCE_TIME_UNVERIFIED' in observations['activation_missing']['value']) is not allowed
    assert observations['activation_scope_complete'] == {'value': False, 'collected': True}
    assert observations['activation_violations'] == {'value': None, 'collected': False}


@pytest.mark.parametrize('terminal_ns,complete', [
    pytest.param(250_000_001, True, id='publication-equality'),
    pytest.param(250_000_002, False, id='publication-plus-1ns'),
    pytest.param(225_000_001, True, id='maximum-delay-equality'),
    pytest.param(225_000_000, False, id='maximum-delay-plus-1ns'),
])
def test_retry_snapshot_checks_exact_normal_terminal_publication_delay(tmp_path, monkeypatch, terminal_ns, complete):
    from guard.adapters.vllm import VllmAdapter
    from guard.configuration import RETRY_RECORD_SNAPSHOT_OPTION
    from guard.core.run import _normalize

    environment, world, scope, values, _, _, scope_path = inputs(tmp_path)
    second = retry_executor.contract.stamp(world.retry_packet['recorded_at']).replace(microsecond=0)
    instants = [second + timedelta(microseconds=offset)
                for offset in (100000, 200000, 500000, 750000, 850000)]
    clock = iter(instants)
    monkeypatch.setattr(retry_executor, '_snapshot_time', lambda: next(clock))
    monkeypatch.setattr(retry_executor, 'utcnow', lambda: second)

    def stamp_at(nanoseconds):
        return f'{second:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z'

    family = next(subject['request_family_id'] for subject in scope['subjects'] if subject['scenario'] == 'NORMAL')
    terminal = next(row for row in rows_of(world.retry_packet, 'ATTEMPT_END_CLAIM') if row['request_family_id'] == family)
    for row in world.retry_packet['events']:
        row['at'] = stamp_at(250_000_001)
    terminal['at'] = stamp_at(terminal_ns)
    monkeypatch.setattr(world, '_packet', lambda: {**world.retry_packet, 'recorded_at': stamp_at(250_000_001)})
    values['retry_max_record_delay_ms'] = 25
    world.target['lab'] = False
    values[retry_executor.contract.DECISION_KEY] = deepcopy(scope['limits'])
    digest = sha256_hex(canonical_json(world.target))
    values['retry_target_sha256'] = world.retry_packet['target_sha256'] = digest
    target_path = tmp_path / 'normal-target.json'
    target_path.write_bytes(canonical_json(world.target))
    cfg = {'target': str(target_path), 'retry_scope': str(scope_path), RETRY_RECORD_SNAPSHOT_OPTION: True}
    executor = KubernetesRequestTerminationExecutor(cfg, runner=world.runner, expected_target_sha256=digest)
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['LIF-12']

    draft = executor.run(check, environment, values, VllmAdapter(), tmp_path / 'work')
    observations = _normalize(check, draft)['observations']
    assert draft.status.value == ('COMPLETED' if complete else 'ABORTED')
    assert draft.data_origin.value == 'EXAMPLE' and world.reads == 2 and world.sent == []
    assert observations['activation_scope_complete'] == {'value': False, 'collected': True}
    assert observations['activation_violations'] == {'value': None, 'collected': False}
    assert ('record_clock_or_delay_unverified' in observations['activation_missing']['value']) is not complete
    snapshots = draft.observations['retry_record_snapshots'].value
    steps = observations['activation_steps']['value']
    assert [step['stage'] for step in steps] == ['before', 'after']
    for step, snapshot in zip(steps, snapshots, strict=True):
        assert step['file_bytes_sha256'] == sha256_hex(canonical_json(snapshot['packet']))
    assert terminal in draft.observations['retry_event_declarations'].value


@pytest.mark.parametrize('terminal_ns,publication_ns,outside', [
    pytest.param(100_000_001, 180_000_000, False, id='lower-equality'),
    pytest.param(100_000_000, 180_000_000, True, id='lower-minus-1ns'),
    pytest.param(200_000_001, 200_000_001, False, id='upper-equality'),
    pytest.param(200_000_002, 200_000_002, True, id='upper-plus-1ns'),
])
def test_retry_projection_preserves_exact_policy_bounds(terminal_ns, publication_ns, outside):
    contract = retry_executor.contract
    scope, values, packet, _ = example()
    second = contract.stamp(packet['recorded_at']).astimezone(timezone.utc).replace(microsecond=0)

    def stamp_at(nanoseconds):
        return f'{second:%Y-%m-%dT%H:%M:%S}.{nanoseconds:09d}Z'

    values['retry_validity_start'] = stamp_at(100_000_001)
    values['retry_validity_end'] = stamp_at(200_000_001)
    packet['recorded_at'] = stamp_at(publication_ns)
    for row in packet['events']:
        row['at'] = stamp_at(150_000_000)
    terminal = rows_of(packet, 'ATTEMPT_END_CLAIM')[0]
    terminal['at'] = stamp_at(terminal_ns)
    snapshots = [{'stage': 'before', 'packet': packet}]

    exact = contract.project(scope, snapshots, values, exact_source_clock=True)
    legacy = contract.project(scope, snapshots, values)
    assert ('event_outside_policy_window' in exact['missing']) is outside
    assert ('record_outside_policy_window' in exact['missing']) is (publication_ns > 200_000_001)
    assert 'event_outside_policy_window' not in legacy['missing']
    assert 'record_outside_policy_window' not in legacy['missing']
    assert exact['events'] == legacy['events']


def test_retry_projection_preserves_rows_on_unsupported_exact_publication_format():
    contract = retry_executor.contract
    scope, values, packet, _ = example()
    start = contract.stamp(values['retry_validity_start']).astimezone(timezone.utc)
    values['retry_validity_start'] = start.strftime('%Y%m%dT%H%M%S.%fZ')
    published = contract.stamp(packet['recorded_at']).astimezone(timezone.utc) + timedelta(seconds=1)
    packet['recorded_at'] = published.strftime('%Y%m%dT%H%M%SZ')
    assert contract.stamp(packet['recorded_at']) == published.replace(microsecond=0)
    snapshots = [{'stage': 'before', 'packet': packet}]

    exact = contract.project(scope, snapshots, values, exact_source_clock=True)
    legacy = contract.project(scope, snapshots, values)
    assert 'record_clock_or_delay_unverified' in exact['missing']
    assert 'record_clock_or_delay_unverified' not in legacy['missing']
    assert 'event_outside_policy_window' not in exact['missing']
    assert exact['events'] == legacy['events'] == packet['events']


@pytest.mark.parametrize('mode',['normal','unresolved','declared-forbidden','declared-cost','manual-only','missing-event',
    'duplicate-event','event-conflict','seq-gap','wrong-actor','wrong-generation','wrong-source','raw-field','trust-claim',
    'partial','empty','after-invalid','producer-changed','snapshot-changed','missing','recovered','final-missing',
    'restart','pod-replaced','record-cleanup','owner-unbound','attachment'])
def test_existing_request_termination_retry_records_cli_exact_refs(tmp_path,monkeypatch,mode):
    environment,world,scope,values,pack,receipt,scope_path=inputs(tmp_path,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr(retry_executor,'capture_pipes',world.runner)
    if mode=='owner-unbound':
        original=retry_executor.RequiredEventReader.read_file
        def unbound(*args,**kwargs):
            raw,provenance=original(*args,**kwargs);return raw,{**provenance,'owner_binding_verified':False}
        monkeypatch.setattr(retry_executor.RequiredEventReader,'read_file',unbound)
    def forbidden(*args,**kwargs):raise AssertionError('unexpected subprocess or network outside selected owned reader')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def opened(path,*args,**kwargs):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kwargs)
        monkeypatch.setattr(Path,'open',opened)
    pol=policy(pack.ref(),scope={'include':['LIF-12']},values={'LIF-12':values})
    for name,value in [('environment',environment.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrequest_termination="kubernetes"\n[adapter]\nname="vllm"\n[request_termination]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nretry_scope='+json.dumps(str(scope_path))+'\nprepared="EXAMPLE_NOT_READ.json"\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');assert next(i for i in plan.items if i.check_id=='LIF-12').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='record-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');result,=store.iter_kind('RES')
    assert evd.data_origin.value=='EXAMPLE' and evd.normal_control.attempted is False and evd.normal_control.succeeded is None
    assert result.verdicts.model_dump(mode='json')=={k:'UNKNOWN' for k in ('security','functional','performance')}
    assert all(evd.observations[k].value is False for k in ('retry_producer_authenticated','automatic_retry_verified','retry_nonoccurrence_verified',
        'retry_previous_work_termination_verified','retry_cost_enforcement_verified','retry_scope_complete'))
    assert world.sent==[] and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF12_complete']
    rows=evd.observations['retry_event_declarations'].value
    if mode in {'normal','unresolved','declared-forbidden','declared-cost','duplicate-event'}:
        assert len(rows)==len(world.retry_packet['events']) and evd.execution.status.value=='COMPLETED'
    if mode=='unresolved':assert 'enterprise_retry_policy_unresolved' in evd.observations['retry_unverified'].value
    if mode=='declared-forbidden':assert any(r['declared_forbidden_automatic_attempt'] for r in evd.observations['retry_family_candidates'].value)
    if mode=='declared-cost':assert any(r['declared_limit_exceeded'] for r in evd.observations['retry_cost_candidates'].value)
    if mode=='manual-only':assert not evd.observations['retry_attempt_candidates'].value
    if mode in {'after-invalid','producer-changed','snapshot-changed','final-missing','restart','pod-replaced','record-cleanup','attachment'}:assert rows
    if mode not in {'normal','unresolved','declared-forbidden','declared-cost','duplicate-event'}:assert evd.execution.status.value=='ABORTED'
    assert bool(evd.execution.cleanup_errors)==(mode=='record-cleanup')
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    for artifact in evd.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/artifact.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[result.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_original_LIF12_criteria_and_other76_definitions_preserved(tmp_path):
    _,_,_,_,pack,receipt,_=inputs(tmp_path)
    seed=baseline_pack().path
    for path in seed.rglob('*'):
        if path.is_file() and path.name!='pack.yaml' and path.relative_to(seed).as_posix()!='checks/LIF-12.yaml':
            assert (pack.path/path.relative_to(seed)).read_bytes()==path.read_bytes()
    import yaml
    old=yaml.safe_load((seed/'checks/LIF-12.yaml').read_text(encoding='utf-8'))
    new=yaml.safe_load((pack.path/'checks/LIF-12.yaml').read_text(encoding='utf-8'))
    for key in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','method_and_evidence'):
        assert new[key]==old[key]
    assert receipt['unchanged_definition_bytes']==76


@pytest.mark.parametrize('change',['env','target','scope','scope-raw','model','inventory','image','run','window','session',
    'source','policy-mode','delay-bool','unknown','actual-lab','version032','profile'])
def test_retry_configuration_closed_before_any_remote_IO(tmp_path,change):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    environment,world,scope,values,pack,_,path=inputs(tmp_path)
    if change=='env':values['retry_environment_sha256']='f'*64
    elif change=='target':values['retry_target_sha256']='f'*64
    elif change=='scope':values['retry_scope_sha256']='f'*64
    elif change in {'scope-raw','model','inventory','image','run'}:
        if change=='scope-raw':scope['argv']=['EXAMPLE_PRIVATE']
        elif change=='model':scope['model_revision']='other-revision'
        elif change=='inventory':scope['model_inventory_sha256']='f'*64
        elif change=='image':scope['image_digest']='sha256:'+'f'*64
        elif change=='run':scope['run_id']='other-run'
        path.write_bytes(canonical_json(scope));values['retry_scope_sha256']=sha256_hex(path.read_bytes())
    elif change=='window':values['retry_validity_end']=values['retry_validity_start']
    elif change=='session':values['retry_session']='../unsupported'
    elif change=='source':values['retry_record_source_sha256']=True
    elif change=='policy-mode':values['retry_policy_mode']='implicit-allow'
    elif change=='delay-bool':values['retry_max_record_delay_ms']=True
    elif change=='unknown':values['new_policy']=None
    elif change=='actual-lab':world.target['lab']=False;values['retry_target_sha256']=sha256_hex(canonical_json(world.target))
    elif change=='version032':environment=environment.model_copy(update={'engine':environment.engine.model_copy(update={'version':'0.32.0'})}).seal();values['retry_environment_sha256']=environment.ref().sha256
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    def forbidden(*args,**kwargs):raise AssertionError('retry configuration caused remote IO')
    executor=KubernetesRequestTerminationExecutor({'target':str(target),'retry_scope':str(path)},runner=forbidden,environ={})
    check=pack.checks['LIF-12']
    if change=='profile':check=check.model_copy(update={'collection_profile':'kubernetes-request-attempt-observation-v1'})
    with pytest.raises(ValueError,match='RETRY_'):executor.run(check,environment,values,VllmAdapter(),tmp_path/'work')


def test_retry_read_only_consumer_does_not_read_credentials_or_old_prepared_path(tmp_path,monkeypatch):
    from guard.adapters.vllm import VllmAdapter
    from guard.executors.kubernetes_request_termination import KubernetesRequestTerminationExecutor
    environment,world,_,values,pack,_,scope_path=inputs(tmp_path)
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.target))
    class NoCredentials(dict):
        def get(self,*args,**kwargs):raise AssertionError('retry consumer accessed a credential')
        def __getitem__(self,key):raise AssertionError('retry consumer accessed a credential')
    original=Path.open
    def opened(path,*args,**kwargs):
        if path.name=='EXAMPLE_NOT_READ.json':raise AssertionError('retry consumer opened LIF10/REQ12 prepared file')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',opened)
    executor=KubernetesRequestTerminationExecutor({'target':str(target),'retry_scope':str(scope_path),
        'prepared':'EXAMPLE_NOT_READ.json'},runner=world.runner,environ=NoCredentials())
    draft=executor.run(pack.checks['LIF-12'],environment,values,VllmAdapter(),tmp_path/'work')
    assert draft.normal_control.attempted is False and world.sent==[]
    assert draft.observations['automatic_retry_verified'].value is False
