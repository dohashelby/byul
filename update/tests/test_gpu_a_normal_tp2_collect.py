"""Public collection with saved, explicitly EXAMPLE Pod and page replies."""

from copy import deepcopy
from functools import partial
import json

import pytest

from guard.models import canonical_json, sha256_hex
from tests.test_callback_paging_runtime import pages
from tests.test_gpu_a_normal_tp2_callbacks import _environment, _owned_pods, _stage, _target
from tools import gpu_a_normal_tp2_callbacks as passive
from tools import gpu_a_producers_collect as collector


@pytest.mark.parametrize(('side', 'drift'), [
    ('baseline', None),
    ('candidate', None),
    ('baseline', 'mounted_source'),
    ('candidate', 'post_read_owner'),
])
def test_owned_normal_tp2_collection_preserves_only_verified_pages(tmp_path, monkeypatch, side, drift):
    mode = 'worker-shards-and-scoped-runner-consumption'
    stage, render, _ = _stage(tmp_path, side, mode)
    namespace, engine, probe = _owned_pods(stage)
    env = _environment(render, namespace, engine)
    target = _target(env, render, namespace, engine, probe)
    env_path, target_path = tmp_path / 'env.json', tmp_path / 'target.json'
    env_path.write_bytes(canonical_json(env.to_dict()))
    target_path.write_bytes(canonical_json(target))
    target_file_sha256 = sha256_hex(target_path.read_bytes())

    prepared = stage / 'engine-callbacks'
    _, files, manifest, _ = passive.read_preparation(prepared, mode)
    source = tmp_path / 'example-journal'
    source.mkdir()
    (source / 'callbacks-11.jsonl').write_bytes(b''.join(
        canonical_json({'EXAMPLE': index, 'padding': 'x' * 100000}) + b'\n' for index in range(6)
    ))
    staged_pages = list(pages(source))
    assert len(staged_pages) > 1

    pods = {pod['metadata']['name']: pod for pod in (engine, probe)}
    exec_replies = []
    owner_checks = []
    mounted = {
        'schema': 'guard-mounted-callback-sources/1',
        'files_sha256': sha256_hex(canonical_json({name: {'sha256': sha256_hex(raw), 'bytes': len(raw)}
                                                 for name, raw in files.items()})),
        'filesystem_sha256': 'f' * 64,
        'file_count': len(files),
        'uid': 2000,
        'gid': 0,
    }

    def runner(argv, *, max_stdout_bytes, **_kwargs):
        assert argv[0:2] == ['kubectl', '--context=offline-fixture']
        args = argv[3:]
        if args == ['get', 'namespaces', 'serving-lab', '-o', 'json']:
            document = namespace
        elif args[0:3] == ['--namespace=serving-lab', 'get', 'pod']:
            assert args[4:] == ['-o', 'json']
            document = deepcopy(pods[args[3]])
            if args[3] == engine['metadata']['name']:
                owner_checks.append(len(exec_replies))
                if drift == 'post_read_owner' and len(exec_replies) == 2:
                    document['metadata']['annotations'].pop(passive.ANNOTATION)
        else:
            assert args[:4] == ['--namespace=serving-lab', 'exec', engine['metadata']['name'],
                                '--container=' + engine['spec']['containers'][0]['name']]
            assert args[4:9] == ['--', 'python3', '-I', '-S', '-c']
            assert max_stdout_bytes == 1024**2
            index = len(exec_replies)
            spec = json.loads(args[-1])
            assert spec['session'] == manifest['session']
            assert spec['snapshot'] == (staged_pages[index - 1]['snapshot'] if index else None)
            assert spec['cursor'] == (staged_pages[index - 1]['cursor'] if index else None)
            document = {**staged_pages[index], 'capture_id': spec['capture_id'],
                        'normal_callback_sources': mounted.copy()}
            if drift == 'mounted_source' and index == 1:
                document['normal_callback_sources']['filesystem_sha256'] = 'e' * 64
            exec_replies.append(document)
        return {'returncode': 0, 'stdout_bytes': canonical_json(document), 'capture': {'complete': True}}

    monkeypatch.setattr(collector, 'KubernetesHTTP', partial(collector.KubernetesHTTP, runner=runner))
    archive = tmp_path / 'archive'
    result = collector.collect(env_path=env_path, target_path=target_path, prepared_dir=prepared,
                               target_file_sha256=target_file_sha256, pages_dir=archive)

    retained = len(staged_pages) if drift is None else 1
    seen = len(staged_pages) if drift is None else 2
    assert len(exec_replies) == seen
    assert owner_checks == [offset for i in range(seen) for offset in (i, i + 1)]
    actual_pages = sorted(archive.iterdir())
    assert len(actual_pages) == len(result['raw_page_refs']) == retained
    for index, path in enumerate(actual_pages):
        raw = path.read_bytes()
        assert raw == canonical_json(exec_replies[index])
        assert result['raw_page_refs'][index] == {'path': path.name, 'sha256': sha256_hex(raw)}

    attestation = result['mounted_source_attestation']
    assert attestation['schema'] == 'guard-normal-tp2-callback-attestation/1'
    assert attestation['data_origin'] == 'EXAMPLE'
    assert attestation['environment_ref'] == env.ref().model_dump(mode='json')
    assert attestation['target_sha256'] == sha256_hex(canonical_json(target))
    assert attestation['target_file_sha256'] == target_file_sha256
    assert attestation['plan_sha256'] == passive.plan_sha256(target[passive.KEY])
    assert attestation['observation'] == mounted
    assert attestation['observed_pages'] == retained
    assert result['truncated'] is attestation['collection_truncated'] is (drift is not None)
    if drift is not None:
        codes = result['page_capture']['diagnostic_codes']
        assert 'NORMAL_TP2_CALLBACK_SOURCE_UNVERIFIED' in codes
        assert 'CALLBACK_PREFIX_INCOMPLETE' in codes


@pytest.mark.parametrize(('side', 'damage'), [
    ('baseline', None),
    ('candidate', 'nonempty-cut'),
    ('baseline', 'missing-cut'),
    ('baseline', 'incomplete-seek'),
    ('candidate', 'source-changed'),
    ('candidate', 'writer-restart'),
    ('baseline', 'cursor-reset'),
    ('baseline', 'missing-start-page'),
])
def test_normal_callback_trial_binds_the_acknowledged_writer_cut(tmp_path, side, damage):
    from pathlib import Path

    from guard.adapters.observer_runtime import JournalSink
    from guard.runtime import engine_http

    stage, render, _ = _stage(tmp_path, side, 'worker-shards-and-scoped-runner-consumption')
    namespace, engine, probe = _owned_pods(stage)
    environment = _environment(render, namespace, engine)
    target = _target(environment, render, namespace, engine, probe)
    _, files, manifest, _ = passive.read_preparation(stage / 'engine-callbacks', target[passive.KEY]['mode'])
    session = manifest['session']
    assets = {'target_file_sha256': sha256_hex(canonical_json(target)),
              'plan_sha256': passive.plan_sha256(target[passive.KEY]),
              'files': {name: {'bytes': len(raw), 'sha256': sha256_hex(raw)} for name, raw in files.items()}}
    mounted = {'schema': engine_http.NORMAL_CALLBACK_SOURCE_SCHEMA,
               'files_sha256': sha256_hex(canonical_json(assets['files'])), 'filesystem_sha256': 'f' * 64,
               'file_count': len(files), 'uid': 2000, 'gid': 0}
    journal, archive = tmp_path / 'journal', tmp_path / 'start-pages'
    journal.mkdir(mode=0o700)
    source_root = Path(engine_http.__file__).parent / 'assets'
    source = '\n'.join((source_root / name).read_text()
                       for name in ('callback_page.py.txt', 'normal_callback_cut.py.txt'))
    local = {'__name__': 'guard_callback_page'}
    exec(compile(source, 'example-callback-cut', 'exec'), local)
    replies, specifications, owner_checks = [], [], []
    pods = {pod['metadata']['name']: pod for pod in (engine, probe)}

    def check_owner(pod):
        passive.verify_mounted_pod(target, environment, pod)
        owner_checks.append(len(replies))

    def runner(argv, *, max_stdout_bytes, **_kwargs):
        assert argv[:2] == ['kubectl', '--context=offline-fixture']
        args = argv[3:]
        if args == ['get', 'namespaces', 'serving-lab', '-o', 'json']:
            document = namespace
        elif args[:3] == ['--namespace=serving-lab', 'get', 'pod']:
            document = pods[args[3]]
        else:
            assert args[:4] == ['--namespace=serving-lab', 'exec', engine['metadata']['name'], '--container=engine']
            assert args[4:9] == ['--', 'python3', '-I', '-S', '-c'] and max_stdout_bytes == 1024**2
            assert args[9] == engine_http._callback_page_source(attest=True, trial=True)
            spec = json.loads(args[-1])
            assert spec['session'] == session and spec['normal_callback_assets'] == assets['files']
            document = local['page'](journal, spec['snapshot'], spec['cursor'], spec['capture_id'], spec['seek_end'],
                                     session=session)
            document.update(normal_callback_sources=deepcopy(mounted), container_start_monotonic_ns=1)
            if spec['seek_end'] and damage == 'incomplete-seek':
                document['gaps'] = ['CALLBACK_CHECKPOINT_UNAVAILABLE']
            if not spec['seek_end'] and damage == 'source-changed':
                document['normal_callback_sources']['filesystem_sha256'] = 'e' * 64
            if not spec['seek_end'] and damage == 'writer-restart':
                document['normal_callback_writers']['writers'][str(sink.pid)] += 1
                for row in document['snapshot'].values():
                    row['start_ticks'] += 1
            if not spec['seek_end'] and damage == 'cursor-reset':
                document['start_cursor'] = {}
            specifications.append(spec)
            replies.append(document)
        return {'returncode': 0, 'stdout_bytes': canonical_json(document), 'capture': {'complete': True}}

    transport = engine_http.KubernetesHTTP(target, environment, duration=15, profile=engine_http.ENGINE_CALLBACK_PROFILE,
                                           runner=runner)
    options = {'normal_callback_assets': assets, 'normal_pod_check': check_owner}
    following = tmp_path / 'following'
    if damage == 'missing-cut':
        with pytest.raises(engine_http.KubernetesBindingError):
            transport.engine_attempt_events(session, archive=following, normal_callback_cut=None, **options)
        assert not replies and not following.exists()
        return

    sink = JournalSink(journal)
    prehistory = damage in {'nonempty-cut', 'cursor-reset'}
    try:
        if prehistory:
            sink({'EXAMPLE': 'before'})
        if damage == 'incomplete-seek':
            with pytest.raises(engine_http.KubernetesBindingError):
                transport.begin_callback_trial(session, archive=archive, **options)
            assert len(replies) == 1 and not archive.exists()
            return
        cut = transport.begin_callback_trial(session, archive=archive, **options)
        start, = replies
        assert specifications[0]['seek_end'] is True and specifications[0]['cursor'] is None
        assert cut['schema'] == engine_http.NORMAL_CALLBACK_CUT_SCHEMA and cut['data_origin'] == 'EXAMPLE'
        assert cut['session_sha256'] == sha256_hex(session.encode())
        assert cut['target_file_sha256'] == assets['target_file_sha256']
        assert cut['plan_sha256'] == assets['plan_sha256']
        assert cut['source_sha256'] == sha256_hex(engine_http._callback_page_source(attest=True, trial=True).encode())
        assert cut['cursor_sha256'] == sha256_hex(canonical_json(start['cursor']))
        assert cut['writer_scope_sha256'] == sha256_hex(canonical_json(start['normal_callback_writers']))
        assert cut['source_observation_sha256'] == sha256_hex(canonical_json(mounted))
        assert start['start_cursor'] == {} and not start['rows'] and bool(start['snapshot']) == prehistory
        assert start['normal_callback_writers']['writers'].keys() == {str(sink.pid)}
        start_path = archive / cut['start_page']['path']
        assert start_path.read_bytes() == canonical_json(start)
        assert cut['start_page']['sha256'] == sha256_hex(start_path.read_bytes())
        assert transport.callback_cursors == {}

        if damage == 'missing-start-page':
            start_path.unlink()
            with pytest.raises(engine_http.KubernetesBindingError):
                transport.engine_attempt_events(session, archive=following, normal_callback_cut=cut, **options)
            assert len(replies) == 1 and not following.exists()
            return
        sink({'EXAMPLE': 'after'})
        captured = transport.engine_attempt_events(session, archive=following, normal_callback_cut=cut, **options)
        assert specifications[1]['cursor'] == start['cursor']
        assert specifications[1]['seek_end'] is False and specifications[1]['capture_id'] != specifications[0]['capture_id']
        assert captured['normal_callback_cut'] == cut and owner_checks == [0, 1, 1, 2]
        assert captured['mounted_source_attestation']['data_origin'] == 'EXAMPLE'
        if damage in {'source-changed', 'writer-restart', 'cursor-reset'}:
            assert captured['truncated'] and not captured['normal_callback_cut_verified']
            assert engine_http.NORMAL_CALLBACK_CUT_GAP in captured['diagnostic_codes']
            assert not captured['page_refs'] and not captured['processes']
        else:
            assert not captured['truncated'] and captured['normal_callback_cut_verified']
            assert captured['processes'][0]['events'] == [{'EXAMPLE': 'after'}]
            next_page, = captured['page_refs']
            assert (following / next_page['path']).read_bytes() == canonical_json(replies[1])
            assert next_page['sha256'] == sha256_hex(canonical_json(replies[1]))
        with pytest.raises(engine_http.KubernetesBindingError):
            transport.engine_attempt_events(session, archive=following, normal_callback_cut=cut, **options)
        assert len(replies) == 2
    finally:
        sink.close()
