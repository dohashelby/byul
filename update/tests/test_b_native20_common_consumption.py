"""B-owned current394 source admission and public HTTP product consumption."""
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import socket

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.models import canonical_json, utcnow
from guard.runtime import kubernetes_http
from guard.store import Store
from tests.test_b_lif07_readiness import case, PRIVATE
from tests.test_b_lif07_http_readiness import World, A_PRIVATE
from tools import b_lif07_native_capture as helper, b_lif07_readiness as tool


@pytest.mark.parametrize('mode', ['unready', 'cleanup-after-canary'])
def test_current394_real_gate_HTTP_main_and_exact_saved_pack(case, monkeypatch, capsys, mode):
    proof = helper.verify_source()
    assert proof['files_verified'] == 394 and proof['paths_excluded'] == []
    assert proof['verified_manifest_sha256'] == '0ee4b9da536307f9f5d68b55f3b938c591260da3386dc401d6a01a51a9ebc3c2'
    assert proof['reviewed_common_commit'] == 'adcde856375a4de43d4a5d789c00de875e43e321'
    assert not proof['runtime_hash_approval'] and not proof['original_B_default_pin_changed']
    case['manifest']['request']['messages'] = [{'role': 'user', 'content': 'hello'}]
    case['args']['manifest'].write_bytes(canonical_json(case['manifest']))
    world = World(case['target'], mode)
    monkeypatch.setattr(kubernetes_http, 'capture_pipes', world.runner)
    def forbidden(*args, **kwargs): raise AssertionError('actual target socket forbidden')
    monkeypatch.setattr(socket, 'socket', forbidden)
    now = utcnow()
    args = {**case['args'], 'http_validity_start': (now - timedelta(seconds=30)).isoformat(),
            'http_validity_end': (now + timedelta(minutes=3)).isoformat()}
    argv = ['--collect-http']
    for key, value in args.items():
        argv += ['--' + ('plan' if key == 'plan_id' else key.replace('_', '-')), str(value)]
    before = tool._inventory(case['source'].root)
    tool.main(argv)
    summary = json.loads(capsys.readouterr().out)
    out = case['args']['output']
    safe = json.loads((out / 'worker-readiness.json').read_text(encoding='utf-8'))
    observation = safe['http_observation']
    assert observation['source_validation'] == {'reviewed_common_commit': proof['reviewed_common_commit'],
        'runtime_hash_approval': False, 'producer_authenticated': False}
    assert observation['report_validated']
    assert len(world.execs) == summary['requests'] == (3 if mode == 'unready' else 2)
    assert summary['capture_origin'] == 'EXAMPLE' and summary['actual_target_executed'] is False
    assert summary['exact_refs_restored'] and tool._inventory(case['source'].root) == before
    assert summary['verdicts'] == {key: 'UNKNOWN' for key in ('security', 'functional', 'performance')}
    assert summary['source_authentication_verified'] is False and summary['full_scope_coverage'] is False
    if mode == 'cleanup-after-canary':
        assert observation['snapshot']['cleanup_errors'] and observation['snapshot']['requests'][1]['outcome'] == 200
    assert PRIVATE not in json.dumps(safe) and A_PRIVATE not in json.dumps(safe)
    store, restored = Store.read_only(out / 'store'), Store.read_only(out / 'restored')
    objects = [o for kind in ('ENV', 'POL', 'PLAN', 'EVD', 'RES', 'RPT') for o in store.iter_kind(kind)]
    assert len(objects) == 6 and store.verify() == restored.verify() == []
    assert all(restored.resolve(o.ref()).to_dict() == o.to_dict() for o in objects)
    plan, = store.iter_kind('PLAN'); report, = store.iter_kind('RPT'); result, = store.iter_kind('RES')
    cli = CliRunner()
    checked = cli.invoke(app, ['judge', '--plan', plan.id, '--as-of', report.created_at.isoformat(),
                              '--store', str(restored.root)])
    assert checked.exit_code == 0, checked.output
    checked = cli.invoke(app, ['verify', '--store', str(restored.root)])
    assert checked.exit_code == 0, checked.output
    assert [o.ref() for o in Store.read_only(restored.root).iter_kind('RES')] == [result.ref()]
    durable = os.environ.get('BYUL_NATIVE20_CONTRACT_EVIDENCE')
    if durable:
        destination = Path(durable) / mode
        assert not destination.exists()
        shutil.copytree(out, destination)
        (destination / 'receipt.json').write_bytes(canonical_json({'argv': argv, 'source_gate': proof,
            'exact_refs': [o.ref().model_dump(mode='json') for o in objects], 'saved_pack_rejudge_equal': True,
            'mode': mode, 'transport': 'EXAMPLE; actual target/worker/GPU 0', 'safe_facts': safe}))

