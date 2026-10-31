"""A-2 전용 bytes를 유지하며 정식 registry/CLI 연결을 검증한다."""
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors.registry import resolve_executor
from guard.models import canonical_json
from guard.runtime import kubernetes_http as kube
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_kubernetes_template_supply import inputs, PRIVATE


@pytest.mark.parametrize('mode', [None, 'http-error', 'restart', 'attachment', 'missing-policy'])
def test_shipped_registry_cli_and_saved_pack_without_factory_override(tmp_path, monkeypatch, mode):
    env, _, world, cfg = inputs(tmp_path, mode)
    pack = baseline_pack()
    pol = policy(pack.ref(), scope={'include': ['SUP-03']},
                 values={} if mode == 'missing-policy' else {'SUP-03': {'allowed_template_sha256': []}})
    for name, value in [('environment', env.to_dict()), ('policy', pol.to_dict())]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    config = tmp_path/'guard.toml'
    config.write_text('[executors]\ntemplate_validation="kubernetes"\n[adapter]\nname="vllm"\n'
                      '[template_validation]\ntarget='+json.dumps(cfg['target'])+'\nrequest_timeout=1\n', encoding='utf-8')
    # Only the existing common subprocess boundary is injected. The product
    # registry, executor, collector, judges and Store remain the shipped path.
    monkeypatch.setattr(kube, 'capture_pipes', world.runner)
    def forbidden(*args, **kwargs):
        raise AssertionError('no process/socket outside injected common boundary')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    runner = CliRunner()
    store_path, restored_path = tmp_path/'store', tmp_path/'restored'
    common = ['--config', str(config), '--store', str(store_path)]
    def cli(args):
        r = runner.invoke(app, args)
        assert r.exit_code == 0, (args, r.output, repr(r.exception))
        return r.output
    plan = cli(['select', '--pack', str(pack.path), '--env', str(tmp_path/'environment.json'),
                '--policy', str(tmp_path/'policy.json'), *common]).strip().splitlines()[-1]
    with monkeypatch.context() as capture:
        if mode == 'attachment':
            original, calls = Store.put_artifact, []
            def partial(self, data):
                calls.append(1)
                if len(calls) == 2:
                    raise OSError(PRIVATE)
                return original(self, data)
            capture.setattr(Store, 'put_artifact', partial)
        cli(['run', '--plan', plan, '--pack', str(pack.path), *common])
    cli(['judge', '--plan', plan, '--as-of', T0.isoformat(), *common])
    cli(['report', '--plan', plan, '--as-of', T0.isoformat(), '--json', str(tmp_path/'report.json'), *common])
    cli(['verify', *common])
    store = Store.read_only(store_path)
    evd, = store.iter_kind('EVD')
    res, = store.iter_kind('RES')
    assert evd.data_origin.value == 'EXAMPLE'
    assert all(axis.value == 'UNKNOWN' for axis in res.verdicts.model_dump().values())
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    if mode == 'missing-policy':
        assert evd.execution.status.value == 'NOT_RUN' and evd.execution.executor is None
        assert not world.calls and not world.packets
    else:
        assert evd.execution.executor.name == 'template_validation'
        assert evd.execution.status.value == ('ABORTED' if mode == 'attachment' else 'COMPLETED')
        if mode == 'attachment':
            assert len(evd.artifacts) == 1
            assert evd.observations['selected_template_approved'].value is False
    objects = [x for kind in ('ENV', 'POL', 'PLAN', 'EVD', 'RES', 'RPT') for x in store.iter_kind(kind)]
    bundle = tmp_path/'bundle.zip'
    cli(['evidence', 'export', '--store', str(store_path), '--output', str(bundle)])
    cli(['evidence', 'restore', '--bundle', str(bundle), '--destination', str(restored_path)])
    restored = Store.read_only(restored_path)
    assert all(restored.resolve(x.ref()).to_dict() == x.to_dict() for x in objects)
    cli(['judge', '--plan', plan, '--as-of', T0.isoformat(), '--store', str(restored_path), '--config', str(config)])
    assert [x.ref() for x in Store.read_only(restored_path).iter_kind('RES')] == [res.ref()]


def test_registry_forwards_exact_target_pin_and_rejects_before_transport(tmp_path, monkeypatch):
    env, _, _, cfg = inputs(tmp_path)
    def forbidden(*args, **kwargs):
        pytest.fail('wrong pinned target must be rejected before process/HTTP')
    monkeypatch.setattr(kube, 'capture_pipes', forbidden)
    factory = resolve_executor('template_validation', 'kubernetes')
    executor = factory({'template_validation': cfg, '_runtime_environ': {}, '_kubernetes_target_sha256': '0'*64})
    with pytest.raises(ValueError, match='KUBERNETES_TEMPLATE_CONFIGURATION_INVALID'):
        executor.run(baseline_pack().checks['SUP-03'], env, {'allowed_template_sha256': []},
                     VllmAdapter(), tmp_path/'work')
