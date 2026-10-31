"""EXAMPLE /9 common writer/parser consumed by the existing native file CLI."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_sensor as legacy, vllm_online_sensor as online
from guard.cli import app
from guard.collectors import native_weight_files as collector
from guard.executors import lifecycle_capture as capture, weight_file_capture as files
from guard.models import canonical_json
from tests.test_lifecycle_capture import case, external, revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_native_weight_files import inputs, Reader, ROOT, WEIGHTS, UTILS, POINT, Model, CANARY
from tests.test_a1_online_native_hook import world as common_world, wrapped, run_chat, packet
from tests.test_kubernetes_sampling import inputs as sampling_inputs


def seal(rows):
    rows = deepcopy(rows)
    stamp = max(row['monotonic_ns'] for row in rows)
    for index, row in enumerate(rows):
        row['monotonic_ns'] = stamp + index
    return revised(b''.join(map(capture.canonical_row, rows)), lambda value: None)


def save_native(value, native):
    value.paths[5].write_bytes(canonical_json(native))
    context = json.loads(value.paths[3].read_bytes())
    context.update(session=value.prepared['session'], transport='kubernetes',
        native_session_sha256=hashlib.sha256(value.paths[5].read_bytes()).hexdigest())
    value.paths[3].write_bytes(canonical_json(context))


def online_inputs(original, monkeypatch, extent='small'):
    # Reuse the frozen common EXAMPLE writer/callback fixture in a distinct owned
    # directory. No common/B fixture or source is edited.
    directory = original.case.root/'online-source'
    directory.mkdir()
    world = common_world.__wrapped__(directory, monkeypatch)
    world.fake.getcwd = lambda: ROOT
    for module in (WEIGHTS, UTILS):
        world.writer.write('MODULE', {'module': module, 'verified': True,
            'source_sha256': legacy.SOURCE_SHA[module], 'code_sha256': 'c'*64})
    factory = world.observer.wrap(UTILS, POINT, lambda paths, *args: (p for p in paths))
    loader = world.observer.wrap(WEIGHTS, 'DefaultModelLoader.load_weights',
        lambda owner, model, config: factory([ROOT+'/a.bin', ROOT+'/b.bin'], False))
    generator = loader(SimpleNamespace(), Model(), SimpleNamespace())
    assert generator.gi_frame is not None  # This test does not consume model tensors.
    reuse = wrapped(world, 'online_renderer._reused_prompt_token_ids')
    returned, _ = run_chat(world, lambda: reuse(SimpleNamespace(kv_transfer_params={'prompt_token_ids': [-1, 32]})))
    assert returned == [-1, 32]
    _, rows, _ = packet(world)
    if extent != 'small':
        filler = next(row for row in rows if row['event'] == ('MODULE' if extent == 'rows' else 'NATIVE')
            and (row['payload'].get('module') == WEIGHTS if extent == 'rows'
                 else row['payload'].get('point') == POINT and row['payload']['phase'] == 'CALL'))
        rows[-1:-1] = [deepcopy(filler) for _ in range(600 if extent == 'rows' else 350)]
    raw = seal(rows)
    rows, summary = capture.parse_recording(raw, world.prepared)
    assert summary['recording_closed'] and not summary['online_incomplete_calls']
    if extent == 'rows':
        assert legacy.MAX_ROWS < len(rows) <= online.MAX_ROWS and len(raw) <= legacy.MAX_BYTES
    if extent == 'bytes':
        assert legacy.MAX_BYTES < len(raw) <= online.MAX_BYTES and len(rows) <= legacy.MAX_ROWS
    target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': True, 'run_id': 'guard-fixture',
        'environment_ref': original.value['env'].ref().model_dump(mode='json'),
        'kubernetes': {'context': 'fixture-context', 'engine': deepcopy(sampling_inputs()['kubernetes']['engine'])}}
    role = target['kubernetes']['engine']
    role['image'] = original.value['env'].engine.image_digest
    role['image_id'] = 'sha256:'+'c'*64
    original.paths[1].write_bytes(canonical_json(target))
    native = json.loads(original.paths[5].read_bytes())
    native.update(session=world.prepared['session'], recordings=[{'capture': {'records': rows,
        'provenance': {'owner': deepcopy(role)}}}])
    paths = list(original.paths)
    paths[2] = directory/'assets/manifest.json'
    value = SimpleNamespace(paths=paths, prepared=world.prepared, owner=deepcopy(role), native=native, raw=raw)
    save_native(value, native)
    return value


@pytest.mark.parametrize('extent', ['small', 'rows', 'bytes'])
def test_valid_profile9_inputs_keep_weight_selections(inputs, monkeypatch, extent):
    value = online_inputs(inputs, monkeypatch, extent)
    loaded = collector.load_inputs(*value.paths)
    assert not loaded['native_gap'] and len(loaded['selections']) == 1
    result = collector.collect(loaded, reader=Reader(value.owner), max_file_bytes=1024)
    assert len(result['files']) == 2 and not result['collection_gap']
    assert all(row['capture_verified'] and row['content_matches_reference'] for row in result['files'])
    assert result['security'] == 'UNKNOWN' and result['data_origin'] == 'EXAMPLE'
    assert not any(result[k] for k in ('producer_authenticated', 'final_model_verified',
        'actual_consumption_verified', 'weights_revision_verified', 'whole_run_coverage_verified'))


@pytest.mark.parametrize('damage', ['callback-code', 'module-code', 'binding', 'raw', 'unknown-runtime'])
def test_profile9_shared_provenance_and_metadata_gates_remain_closed(inputs, monkeypatch, damage):
    value = online_inputs(inputs, monkeypatch)
    rows = deepcopy(value.native['recordings'][0]['capture']['records'])
    if damage in {'callback-code', 'unknown-runtime'}:
        row = next(r for r in rows if r['event'] == 'MODULE' and r['payload']['module'] == online.CALLBACK_MODULE)
        row['payload']['code_sha256' if damage == 'callback-code' else 'online_runtime'] = '0'*64 if damage == 'callback-code' else 'cpython-3.12.999'
    elif damage == 'module-code':
        next(r for r in rows if r['event'] == 'MODULE' and 'online_code_sha256' in r['payload'])['payload']['online_code_sha256'] = '0'*64
    else:
        row = next(r for r in rows if r['event'] == 'NATIVE' and 'online_input' in r['payload']['metadata'])
        if damage == 'raw':
            row['payload']['metadata']['body'] = CANARY
        else:
            row['payload']['metadata']['online_input']['binding']['session'] = '0'*32
    raw = seal(rows)
    with pytest.raises(ValueError):
        capture.parse_recording(raw, value.prepared)
    value.native['recordings'][0]['capture']['records'] = [json.loads(line) for line in raw.splitlines()]
    save_native(value, value.native)
    loaded = collector.load_inputs(*value.paths)
    assert loaded['native_gap'] and not loaded['selections']


def test_later_invalid_recording_keeps_prior_profile9_file_mismatch_facts(inputs, monkeypatch):
    value = online_inputs(inputs, monkeypatch)
    bad = deepcopy(value.native['recordings'][0])
    bad['capture']['records'][0]['payload']['session'] = '0'*32
    value.native['recordings'].append(bad)
    save_native(value, value.native)
    loaded = collector.load_inputs(*value.paths)
    assert loaded['native_gap'] and len(loaded['selections']) == 1
    result = collector.collect(loaded, reader=Reader(value.owner, 'mismatch'), max_file_bytes=1024)
    assert result['collection_gap'] and len(result['files']) == 2
    assert all(row['capture_verified'] and row['content_matches_reference'] is False for row in result['files'])
    assert result['security'] == 'UNKNOWN' and CANARY not in json.dumps(result) and ROOT not in json.dumps(result)


@pytest.mark.parametrize('extent', ['rows', 'bytes'])
def test_legacy_profile_keeps_its_original_recording_limits(inputs, extent):
    native = json.loads(inputs.paths[5].read_bytes())
    rows = native['recordings'][0]['capture']['records']
    filler = next(row for row in rows if row['event'] == ('MODULE' if extent == 'rows' else 'NATIVE')
        and (row['payload'].get('module') == WEIGHTS if extent == 'rows'
             else row['payload'].get('point') == POINT and row['payload']['phase'] == 'CALL'))
    rows[-1:-1] = [deepcopy(filler) for _ in range(600 if extent == 'rows' else 350)]
    raw = seal(rows)
    assert len(rows) > legacy.MAX_ROWS if extent == 'rows' else len(raw) > legacy.MAX_BYTES
    native['recordings'][0]['capture']['records'] = [json.loads(line) for line in raw.splitlines()]
    inputs.paths[5].write_bytes(canonical_json(native))
    context = json.loads(inputs.paths[3].read_bytes())
    context['native_session_sha256'] = hashlib.sha256(inputs.paths[5].read_bytes()).hexdigest()
    inputs.paths[3].write_bytes(canonical_json(context))
    loaded = collector.load_inputs(*inputs.paths)
    assert loaded['native_gap'] and not loaded['selections']


@pytest.mark.parametrize('extent', ['rows', 'bytes', 'one-row'])
def test_profile9_limits_are_not_removed(inputs, monkeypatch, extent):
    value = online_inputs(inputs, monkeypatch)
    rows = value.native['recordings'][0]['capture']['records']
    if extent == 'one-row':
        rows[0]['payload']['unexpected'] = 'a'*16384
    else:
        filler = deepcopy(next(row for row in rows if row['event'] == 'MODULE'
            and row['payload'].get('module') == WEIGHTS))
        if extent == 'bytes':
            filler = deepcopy(next(row for row in rows if row['event'] == 'NATIVE'
                and row['payload'].get('point') == POINT and row['payload']['phase'] == 'CALL'))
            metadata = filler['payload']['metadata']['weight_selection']
            metadata.update(path_count=64, normalized_path_sha256=[hashlib.sha256(str(i).encode()).hexdigest() for i in range(64)])
        rows[-1:-1] = [deepcopy(filler) for _ in range(online.MAX_ROWS if extent == 'rows' else 450)]
    raw = seal(rows)
    if extent == 'rows':
        assert len(rows) > online.MAX_ROWS
    if extent == 'bytes':
        assert len(raw) > online.MAX_BYTES and len(rows) < online.MAX_ROWS
    with pytest.raises(ValueError):
        capture.parse_recording(raw, value.prepared)
    value.native['recordings'][0]['capture']['records'] = [json.loads(line) for line in raw.splitlines()]
    save_native(value, value.native)
    loaded = collector.load_inputs(*value.paths)
    assert loaded['native_gap'] and not loaded['selections']


@pytest.mark.parametrize('mode', ['normal', 'mismatch-partial', 'attachment'])
def test_existing_capture_CLI_consumes_profile9_via_kubernetes_boundary(inputs, monkeypatch, tmp_path, mode):
    value = online_inputs(inputs, monkeypatch, 'bytes')
    if mode == 'mismatch-partial':
        bad = deepcopy(value.native['recordings'][0])
        bad['capture']['records'][0]['payload']['session'] = '0'*32
        value.native['recordings'].append(bad)
        save_native(value, value.native)
    reader = Reader(value.owner, 'mismatch' if mode != 'normal' else None)
    monkeypatch.setattr(files, 'KubernetesWeightReader', lambda target, env, prepared, *a, **kw:
        SimpleNamespace(read=reader.read, report=lambda: {'profile': 'kubernetes-owned-weight-file-read-v1', 'collection_gap': False}))
    output = tmp_path/'capture.json'
    if mode == 'attachment':
        original_open = Path.open
        def open_file(path, *args, **kwargs):
            if path == output and args and args[0] == 'x':
                raise OSError(CANARY)
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', open_file)
    flags = ('--env', '--target', '--prepared', '--context', '--model-manifest', '--native-session')
    argv = ['capture-native-weight-files', *[s for pair in zip(flags, map(str, value.paths)) for s in pair],
        '--output', str(output), '--max-file-bytes', '1024', '--execute']
    result = CliRunner().invoke(app, argv)
    assert result.exit_code == (1 if mode == 'attachment' else 0), result.stdout
    receipt = json.loads(result.stdout)
    captured = receipt['capture'] if mode == 'attachment' else json.loads(output.read_bytes())
    assert len(captured['files']) == 2 and len(reader.calls) == 2
    assert all(row['capture_verified'] and row['content_matches_reference'] is (mode == 'normal') for row in captured['files'])
    assert captured['collection_gap'] is (mode == 'mismatch-partial') and captured['security'] == 'UNKNOWN'
    assert captured['data_origin'] == 'EXAMPLE' and not captured['actual_consumption_verified']
    assert ROOT not in result.stdout and CANARY not in result.stdout
    if mode == 'attachment':
        assert receipt['artifact_saved'] is False and not output.exists()
    if mode == 'normal':
        portable = tmp_path/'installed-inputs'
        portable.mkdir()
        for index, path in enumerate(value.paths):
            if index == 2:
                shutil.copytree(path.parent, portable/'assets')
            else:
                shutil.copyfile(path, portable/('input-'+str(index)+'.json'))
        (portable/'owner.json').write_bytes(canonical_json(value.owner))
