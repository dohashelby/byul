"""EXAMPLE 원 함수·서명 파일·시험 소유 자식의 실제 신호로 부분 공통 입력을 검증한다."""
from copy import deepcopy
from hashlib import sha256
import hmac
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from guard.adapters import vllm_rank_rpc_control as control
from guard.adapters import vllm_rank_rpc_input as adapter
from guard.collectors import rank_rpc_fault_producer as producer, gpu_common_inputs as common
from tests.test_rank_rpc_runtime import _rpc_prepared
from tests.test_rank_rpc_product_binding import example
from tools import rank_rpc_fault_inputs as tool


SCRIPT = '''import sys,os,json,time,signal,importlib.util,importlib
from pathlib import Path
from multiprocessing import Pipe
root=Path(sys.argv[1]);kind=sys.argv[2];sys.path.insert(0,str(root))
spec=importlib.util.spec_from_file_location('_byul_rank_ipc',root/'guard/__init__.py',submodule_search_locations=[str(root/'guard')])
package=importlib.util.module_from_spec(spec);sys.modules['_byul_rank_ipc']=package;spec.loader.exec_module(package)
runtime=importlib.import_module('_byul_rank_ipc.adapters.vllm_rank_ipc_runtime');runtime.ROOT=root
assert runtime.install()
actor=runtime.rpc_actor;code=runtime.rpc_control
import rank_rpc_fixture as fixture
outer,inner,scheduled=fixture.Outer(),fixture.Inner(),fixture.Scheduled()
outer.worker,outer.replace_ids,inner.error,inner.result=inner,False,None,object()
inner.rank,inner.tp=0,2
controller=runtime._INSTALLED.observer.rpc.control
binding=json.loads((root/'EXAMPLE-binding.json').read_bytes())
os.environ.update(GUARD_POD_UID=binding['pod_uid'],GUARD_CONTAINER_ID=binding['container_id'],GUARD_RESTART_COUNT=str(binding['restart_count']))
controller.write('control-product-binding.json',binding)
parent,end=Pipe();pid=os.fork()
if pid==0:
 parent.close()
 try:
  while True:
   request=end.recv();scheduled.num_scheduled_tokens={request:1}
   try:outer.execute(scheduled);end.send(200)
   except code.NativeLabFault:end.send(500)
 except (EOFError,OSError):pass
 finally:end.close();os._exit(0)
end.close()
def client(model,header,key):
 started=time.monotonic_ns();fixture.External.header(header);parent.send('cmpl-'+header+'-0')
 try:status=parent.recv()
 except (EOFError,OSError):status=None
 return dict(context=code.base._context(),header_hmac_sha256=actor._id(header,key),request_started_ns=started,
  response_finished_ns=time.monotonic_ns(),status=status,complete=status is not None,
  response_sha256=code.digest('EXAMPLE-response') if status is not None else None,response_id_hmac_sha256=None)
actor.post=client
try:
 assert client('EXAMPLE-model','EXAMPLE-normal',controller.key)['status']==200
 records=controller._records()
 proof,=[p for p,_,_ in records if p['point']=='Inner.execute' and p['event']=='CALL' and p['context']['pid']==pid]
 header,=[p for p,_,_ in records if p['point']=='External.header' and p['event']=='RETURN']
 calibration=dict(proof=proof,header_context=header['context'],assignment_context=None,initial_capture_sha256=code.digest(sorted(raw for _,raw,_ in records)))
 owner=dict(pod_uid=binding['pod_uid'],model_receipt_sha256='9'*64)
 result=actor.exercise(controller,calibration,'EXAMPLE-model',kind,owner,hold_ms=10,product_binding=binding)
 assert result['scope_complete'] is result['producer_registered'] is False
finally:
 parent.close()
 if kind!='death':
  try:os.kill(pid,signal.SIGKILL)
  except ProcessLookupError:pass
 os.waitpid(pid,0)
'''


@pytest.fixture(scope='module')
def examples(tmp_path_factory):
    results = {}
    for kind in ('delay', 'exception', 'death'):
        parent = tmp_path_factory.mktemp('EXAMPLE-rpc-' + kind)
        prepared, settings = _rpc_prepared(parent)
        path = prepared / 'guard/adapters/assets/vllm_rank_ipc_profiles.json'
        document = json.loads(path.read_bytes()); profile = document['profiles'][0]
        profile['request_binding'] = {'kind': 'direct', 'external_prefix': 'cmpl-', 'external_suffix': '-0'}
        hook = profile['rpc_sources']['rank_rpc_fixture']['hooks']['Inner.execute']
        hook.update(fault_eligible=True, scalars={'gpu_rank': {'root': 'self', 'path': ['rank']},
            'configured_tp_size': {'root': 'self', 'path': ['tp']}})
        raw = control.canonical(document); path.write_bytes(raw)
        settings['assets']['guard/adapters/assets/vllm_rank_ipc_profiles.json'] = {'bytes': len(raw), 'sha256': sha256(raw).hexdigest()}
        (prepared / 'settings.json').write_bytes(control.canonical(settings))
        manifest = json.loads((prepared / 'manifest.json').read_bytes())
        manifest.update(assets=settings['assets'], settings_sha256=sha256(control.canonical(settings)).hexdigest())
        (prepared / 'manifest.json').write_bytes(control.canonical(manifest))
        binding = example(settings['session']); binding['prepared_sha256'] = sha256(control.canonical(manifest)).hexdigest()
        (prepared / 'EXAMPLE-binding.json').write_bytes(control.canonical(binding))
        run = subprocess.run([sys.executable, '-I', '-S', '-c', SCRIPT, str(prepared), kind],
            text=True, capture_output=True)
        assert run.returncode == 0, run.stderr
        capture = parent / 'capture'
        (capture / 'snapshots').mkdir(parents=True); (capture / 'controls').mkdir()
        for path in (prepared / 'data' / settings['session']).iterdir():
            shutil.copy2(path, capture / ('snapshots' if path.name.startswith('pid-') else 'controls') / path.name)
        results[kind] = prepared, capture, binding
    return results


def make(data, *, wire_schema='guard-gpu-common-input/4'):
    prepared, capture, binding = data
    pod = {k: binding[k] for k in ('pod_uid', 'container_id', 'restart_count')}
    return producer.record(prepared, capture, pod=pod,
        **{k: binding[k] for k in ('environment_ref', 'target_sha256', 'prepared_sha256', 'session')}, wire_schema=wire_schema)


@pytest.mark.parametrize('kind', ['delay', 'exception', 'death'])
@pytest.mark.parametrize('wire', ['guard-gpu-common-input/1', 'guard-gpu-common-input/4'])
def test_real_owned_child_facts_become_partial_unregistered_common_input(examples, kind, wire):
    value, validation, projection = make(examples[kind], wire_schema=wire)
    assert value['schema'] == wire
    assert [p['event'] for p in value['events']] == (['fault_applied', 'fault_released'] if kind == 'delay' else ['fault_applied'])
    assert all(p['ack'] is True and p['fault_kind'] == kind for p in value['events'])
    assert kind in {'delay', 'death', 'exception'}
    assert len(value['bindings']) == 1 and value['bindings'][0]['worker_pid'] > 0 and value['bindings'][0]['rank'] == 0
    assert not value['completeness']['source_ended']
    assert validation['supported'] is validation['complete'] is validation['producer_authenticated'] is False
    assert projection['scope_complete'] is projection['engine_group_verified'] is False
    assert 'RANK_RPC_DETECTION_UNOBSERVED' in projection['diagnostic_codes']
    assert 'RANK_RPC_RECOVERY_UNOBSERVED' in projection['diagnostic_codes']
    assert not any(p['event'] in {'detected', 'client_delivered', 'recovered'} for p in value['events'])
    assert projection['client_observation']['upper_delivery_verified'] is False
    if kind == 'death':
        assert projection['client_observation']['status'] is None and not projection['client_observation']['complete']
    assert projection['source_snapshots_verified'] > 0 and len(projection['source_set_sha256']) == 64
    assert (producer.NAME, producer.VERSION) not in common.SUPPORTED_PRODUCERS
    assert 'EXAMPLE-normal' not in json.dumps(projection) and 'EXAMPLE-model' not in json.dumps(value)


@pytest.fixture
def data(examples, tmp_path):
    prepared, capture, binding = examples['delay']
    root = tmp_path / 'prepared'; observed = tmp_path / 'capture'
    shutil.copytree(prepared, root); shutil.copytree(capture, observed)
    return root, observed, deepcopy(binding)


def resign(data, name, change):
    prepared, capture, _ = data
    path = capture / 'controls' / name
    envelope = json.loads(path.read_bytes()); change(envelope['payload'])
    key = bytes.fromhex(json.loads((prepared / 'settings.json').read_bytes())['key'])
    envelope['hmac_sha256'] = hmac.new(key, control.DOMAIN + control.canonical(envelope['payload']), sha256).hexdigest()
    path.write_bytes(control.canonical(envelope))


@pytest.mark.parametrize('damage', ['env', 'target', 'prepared', 'session', 'pod', 'container', 'restart',
    'legacy', 'hmac', 'missing', 'symlink', 'claim', 'ready_ref', 'signal', 'resume_state', 'resume_time',
    'client_clock', 'client_namespace', 'client_uid', 'nonce', 'source_hmac', 'source_body', 'source_frame', 'source_time'])
def test_wrong_even_resigned_metadata_never_creates_common_input(data, damage):
    prepared, capture, expected = data
    if damage in {'env', 'target', 'prepared', 'session', 'pod', 'container', 'restart'}:
        if damage == 'env': expected['environment_ref']['sha256'] = '0' * 64
        elif damage == 'target': expected['target_sha256'] = '0' * 64
        elif damage == 'prepared': expected['prepared_sha256'] = '0' * 64
        elif damage == 'session': expected['session'] = '0' * 32
        elif damage == 'pod': expected['pod_uid'] = 'wrong-pod'
        elif damage == 'container': expected['container_id'] = '0' * 64
        else: expected['restart_count'] += 1
    elif damage == 'legacy':
        resign(data, 'control-case-result.json', lambda v: v['arm'].update(schema='guard-rank-rpc-arm/1'))
    elif damage == 'hmac':
        path = capture / 'controls/control-case-result.json'
        v = json.loads(path.read_bytes()); v['hmac_sha256'] = '0' * 64; path.write_bytes(control.canonical(v))
    elif damage == 'missing': (capture / 'controls/control-product-binding.json').unlink()
    elif damage == 'symlink':
        path = capture / 'controls/control-case-result.json'; raw = path.read_bytes(); path.unlink()
        other = capture / 'original.json'; other.write_bytes(raw); path.symlink_to(other)
    elif damage == 'claim':
        name = next((capture / 'controls').glob('control-claimed-*.json')).name
        resign(data, name, lambda v: v.update(call_hmac_sha256='0' * 64))
    elif damage in {'ready_ref', 'signal', 'resume_state', 'resume_time', 'client_clock', 'client_namespace', 'client_uid', 'nonce'}:
        def change(v):
            if damage == 'ready_ref': v['ready']['source_refs']['call_capture_sha256'] = '0' * 64
            elif damage == 'signal': v['physical_action']['signal'] = 18
            elif damage == 'resume_state': v['physical_resume']['before_state'] = 'R'
            elif damage == 'resume_time': v['physical_resume']['after_observed_ns'] = 0
            elif damage == 'client_clock': v['client']['context']['boot_sha256'] = '0' * 64
            elif damage == 'client_namespace': v['client']['context']['pid_namespace'] += 1
            elif damage == 'client_uid': v['client']['context']['uid'] += 1
            else: v['arm']['nonce'] = '0' * 32
        resign(data, 'control-case-result.json', change)
    else:
        path = next(p for p in (capture / 'snapshots').glob('pid-*.json')
            if json.loads(p.read_bytes())['snapshot']['payload'].get('event') == 'CALL')
        value = json.loads(path.read_bytes()); payload = value['snapshot']['payload']
        if damage == 'source_hmac': value['hmac_sha256'] = '0' * 64
        else:
            if damage == 'source_body': payload['body_code_sha256'] = '0' * 64
            elif damage == 'source_frame': payload['rpc_parent_frame_verified'] = not payload['rpc_parent_frame_verified']
            else: payload['observation_ns'] = 0
            key = bytes.fromhex(json.loads((prepared / 'settings.json').read_bytes())['key'])
            value['hmac_sha256'] = hmac.new(key, control.canonical(value['snapshot']), sha256).hexdigest()
        path.write_bytes(control.canonical(value))
    with pytest.raises((ValueError, OSError)):
        make(data)


def test_registered_identity_alone_cannot_complete_partial_observations(examples, monkeypatch):
    value, _, _ = make(examples['delay'])
    source = value['producer']; monkeypatch.setitem(common.SUPPORTED_PRODUCERS, (source['name'], source['version']), source['source_sha256'])
    scope = {k: value[k] for k in ('environment_ref', 'target_sha256', 'prepared_sha256', 'session')}
    result = common.validate(value, kind='rank-fault', **scope)
    assert not result['complete'] and 'GPU_INPUT_SOURCE_INCOMPLETE' in result['gaps']
    # 상위 관측이 빠져도 계약이 가진 독립 FAIL을 지우지 않는다.
    value['events'][0]['ack'] = False
    result = common.validate(value, kind='rank-fault', **scope)
    assert any(v['reason'] == 'fault_ack_missing' for v in result['violation_candidates'])


def test_missing_source_release_keeps_applied_fact_without_inventing_barrier_release(data):
    _, capture, _ = data
    for p in (capture / 'snapshots').glob('pid-*.json'):
        if json.loads(p.read_bytes())['snapshot']['payload'].get('event') == 'CONTROL_RELEASE':
            p.unlink()
    value, validation, projection = make(data)
    assert [e['event'] for e in value['events']] == ['fault_applied']
    assert 'RANK_RPC_RELEASE_UNOBSERVED' in projection['diagnostic_codes']
    assert not validation['complete']


def test_consistently_resigned_wrong_physical_signal_still_fails(data):
    resign(data, 'control-physical-action.json', lambda v: v.update(signal=18))
    resign(data, 'control-case-result.json', lambda v: v['physical_action'].update(signal=18))
    with pytest.raises(ValueError, match='RANK_RPC_INPUT_PROOF_INVALID'):
        make(data)


def test_changed_case_during_source_validation_is_rejected(data, monkeypatch):
    verify = adapter.capture_code.verify_capture
    def changed(*args):
        result = verify(*args)
        resign(data, 'control-case-result.json', lambda v: v['client'].update(status=422))
        return result
    monkeypatch.setattr(adapter.capture_code, 'verify_capture', changed)
    with pytest.raises(ValueError, match='RANK_RPC_INPUT_CHANGED'):
        make(data)


def test_input_tool_keeps_example_origin_and_partial_status(examples, tmp_path, monkeypatch):
    prepared, capture, binding = examples['exception']
    monkeypatch.setattr(tool, 'binding_for', lambda *a: (binding, {}, 'EXAMPLE'))
    output = tmp_path / 'output'
    result = tool.prepare(prepared, capture, 'EXAMPLE-env', 'EXAMPLE-target', output)
    raw = (output / 'common-input.json').read_bytes()
    assert result == json.loads((output / 'manifest.json').read_bytes())
    assert result['common_input_sha256'] == sha256(raw).hexdigest() and result['data_origin'] == 'EXAMPLE'
    assert result['validation']['supported'] is result['validation']['complete'] is False
    assert result['engine_executed'] is result['live_target_verified'] is result['input_preflight_passed'] is False
    assert result['actual_results'] is None and not result['ready_for_execution']
    with pytest.raises(FileExistsError): tool.prepare(prepared, capture, 'EXAMPLE-env', 'EXAMPLE-target', output)


def test_source_identity_includes_rpc_sdk_capture_projection_and_common_wrapper(monkeypatch):
    before = producer.producer(); read = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read(p) + (b'\n' if p.name == 'vllm_rank_ipc_capture.py' else b''))
    assert producer.producer()['source_sha256'] != before['source_sha256']
