"""Existing real executor contracts with synthetic I/O, portable evidence and rejudging.

These are EXAMPLE fixtures. They do not count as real-engine measurements.
"""
import json

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.container_config import ContainerConfigExecutor, PROC_CMD
from guard.executors.credential_exposure import CredentialExposureExecutor, ENV_CMD
from guard.executors.direct_route import DirectRouteExecutor
from guard.executors.input_validation import InputValidationExecutor
from guard.executors.role_route import RoleRouteExecutor
from guard.models import canonical_json
from guard.runtime.docker import CmdResult
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from tests import test_direct_route as direct
from tests import test_role_route as role
from tests.test_container_config import FakeDocker, LAB_POLICY, _probe, IMAGE as PERMISSION_IMAGE, RUN as PERMISSION_RUN
from tests.test_credential_mount_semantics import FakeDocker as CredentialFixture, IMAGE as CREDENTIAL_IMAGE, RUN as CREDENTIAL_RUN
from tests.builders import PACK_DIR, baseline_pack, environment, policy
from tests.prepared_fixtures import manual_setup_seed, put_baseline_snapshot
from tests.response_semantics_support import materialize_pack21
from guard.store.packs import put_pack


VALUES = {
    'REQ-01': {'allowed_callers': ['allowed']},
    'REQ-03': {'general_allowed_kinds': ['inference']},
    'PLT-01': LAB_POLICY,
    'PLT-04': {'forbidden_env_names': ['GUARD_TEST_CREDENTIAL'], 'forbidden_mount_paths': ['/test-creds']},
    'REQ-09': {'max_input_tokens': 8, 'rejection_codes': ['400', '422'],
               'allowed_endpoints': ['POST /v1/completions'], 'allowed_input_formats': ['token_ids']},
}


def _executor(tmp_path, cid, expected, *, violation_and_missing=False, env=None):
    """Choose the fixture situation, never inject the expected verdict into a judge."""
    if cid == 'REQ-01':
        world = direct.World(exposed=expected == 'FAIL', openapi_ok=expected != 'UNKNOWN')
        target = direct._target(tmp_path, exposed=expected == 'FAIL')
        ex = DirectRouteExecutor({'target': str(target), 'roles': {'allowed': 'KA'}, 'wrong_key_env': 'KW'},
            container_probe=world.container_probe, host_probe=world.host_probe,
            environ={'KA': direct.ALLOWED, 'KW': direct.DENIED})
    elif cid == 'REQ-03':
        world = role.RoleWorld(enforced=expected != 'FAIL')
        target = role._target(tmp_path)
        # A mutating route with no source-verified safe probe is not sent.
        if expected == 'UNKNOWN':
            world.routes = {**world.routes, '/sleep': 'post'}
        ex = RoleRouteExecutor({'target': str(target), 'roles': {'general': 'KG', 'admin': 'KA'}, 'wrong_key_env': 'KW'},
            container_probe=world.container_probe,
            environ={'KG': role.GENERAL, 'KA': role.ADMIN, 'KW': role.WRONG})
    else:
        target = tmp_path / 'target.json'
        target.write_text(json.dumps({'lab': True,
            'engine': {'trusted_url': 'http://engine:8000', 'container': 'engine'},
            'origins': {'trusted': {'network': 'inside'}}}), encoding='utf-8')
        cfg = {'target': str(target), 'sentinel_paths': ['/models', '/guard-sentinel']}
        if cid == 'PLT-01':
            class ProcFixture(FakeDocker):
                def exec(self, container, argv, *, timeout):
                    result = super().exec(container, argv, timeout=timeout)
                    if argv == ['sh', '-c', PROC_CMD] and (expected == 'UNKNOWN' or violation_and_missing):
                        return CmdResult(0, result.stdout + 'PID 998\nGUARD_READ_ERROR\n', '')
                    return result
            docker = ProcFixture('default' if expected == 'FAIL' else 'hardened')
            docker.owner_name = 'engine'
            ex = ContainerConfigExecutor(cfg, container_probe=_probe, docker=docker)
        elif cid == 'PLT-04':
            class EnvFixture(CredentialFixture):
                def exec(self, container, argv, *, timeout):
                    assert argv == ['sh', '-c', ENV_CMD]
                    text = 'PID 1\nPATH\n'
                    if expected == 'FAIL':
                        text += 'GUARD_TEST_CREDENTIAL\n'
                    text += 'GUARD_READ_ERROR\n' if expected == 'UNKNOWN' or violation_and_missing else 'GUARD_READ_OK\n'
                    return CmdResult(0, text, '')
            ex = CredentialExposureExecutor(cfg, container_probe=_probe, docker=EnvFixture())
        else:
            def probe(network, requests, timeout):
                from tests.builders import completion_response
                return [completion_response(request, 200 if request['body']['prompt'] == [0] else
                    {'PASS': 400, 'FAIL': 200, 'UNKNOWN': 500}[expected]) for request in requests]
            ex = InputValidationExecutor({**cfg, 'vocab_size': 100, 'vocab_source': 'synthetic fixture vocabulary'},
                container_probe=probe)
    data = json.loads(target.read_text(encoding='utf-8'))
    data['lab'] = True
    if cid in {'PLT-01', 'PLT-04'}:
        assert env is not None
        data.update(environment_ref=env.ref().model_dump(mode='json'),
                    run_id=PERMISSION_RUN if cid == 'PLT-01' else CREDENTIAL_RUN)
    target.write_text(json.dumps(data), encoding='utf-8')
    return ex


def _run(tmp_path, monkeypatch, cid, expected, *, violation_and_missing=False):
    # Phase 1 is a historical contract: replay its exact stored PACK21 rather
    # than apply the new response/credential meaning to old status-only EVDs.
    pack = materialize_pack21(tmp_path / 'historical-pack21')
    env = environment({'gateway': {'value': True, 'origin': 'EXAMPLE', 'source': 'synthetic topology'}},
        engine={'name': 'vllm', 'version': '0.31.0'}, model={'id': 'test-model'})
    if cid in {'PLT-01', 'PLT-04'}:
        env.engine.image_digest = PERMISSION_IMAGE if cid == 'PLT-01' else CREDENTIAL_IMAGE
        env = env.seal()
    pol = policy(pack.ref(), scope={'include': [cid]}, values={cid: VALUES[cid]})
    def create(folder):
        st = Store(folder / 'store')
        put_pack(st, pack.path)
        st.put(env)
        st.put(pol)
        plan = select(pack, [env], pol)
        st.put(plan)
        return st, plan, None

    st, plan, _ = manual_setup_seed('automation-phase1-inputs', {
        'pack': pack.model_dump(mode='json'), 'environment': env.to_dict(), 'policy': pol.to_dict(),
    }, tmp_path, create)
    executor = _executor(tmp_path, cid, expected, violation_and_missing=violation_and_missing, env=env)
    name = pack.checks[cid].executor
    monkeypatch.setitem(registry.EXECUTORS, f'{name}:contract', lambda raw: executor)
    evd, = run_plan(st, pack, plan, RunConfig(executors={name: 'contract'}, adapter='vllm'))
    res, = judge_plan(st, pack, plan, env.created_at)
    return st, pack, env, plan, evd, res


@pytest.mark.parametrize('cid', list(VALUES))
@pytest.mark.parametrize('expected', ['PASS', 'FAIL', 'UNKNOWN'])
def test_five_executor_cases_survive_export_restore_and_rejudge(tmp_path, monkeypatch, cid, expected):
    st, pack, env, plan, evd, res = _run(tmp_path, monkeypatch, cid, expected)
    assert evd.execution.status.value == 'COMPLETED'
    assert evd.data_origin.value == 'EXAMPLE'
    assert res.verdicts.security.value == expected
    assert evd.normal_control.succeeded is True
    assert evd.artifacts
    original_bytes = canonical_json(evd.to_dict())
    report = build_report(st, pack, plan, env.created_at)
    assert report.official is False
    st.put(report)
    export_bundle(st.root, tmp_path / 'evidence.zip')
    restore_bundle(tmp_path / 'evidence.zip', tmp_path / 'restored')
    restored = Store(tmp_path / 'restored')
    assert restored.verify() == []
    assert canonical_json(restored.resolve(evd.ref()).to_dict()) == original_bytes
    again, = judge_plan(restored, pack, restored.resolve(plan.ref()), env.created_at)
    assert again.ref() == res.ref()


@pytest.mark.parametrize('cid', ['PLT-01', 'PLT-04'])
def test_confirmed_violation_is_preserved_when_another_process_is_unreadable(tmp_path, monkeypatch, cid):
    st, pack, env, plan, evd, res = _run(tmp_path, monkeypatch, cid, 'FAIL', violation_and_missing=True)
    assert evd.execution.status.value == 'COMPLETED'
    assert evd.observations['process_scan_complete'].value is False
    assert res.verdicts.security.value == 'FAIL'
    assert st.verify() == []
