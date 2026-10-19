import json
from pathlib import Path
import pytest
from guard.executors import registry
from guard.executors.credential_exposure import CredentialExposureExecutor, ENV_CMD
from guard.executors.input_validation import InputValidationExecutor
from guard.core.select import select
from guard.core.run import run_plan, RunConfig
from guard.core.judge import judge_plan
from guard.runtime.docker import CmdResult
from guard.store import Store
from tests.builders import baseline_pack, environment, policy


def pipeline(tmp_path, cid, factory, values):
    target = {"lab": True, "engine": {"trusted_url": "http://engine:8000", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    store = Store(tmp_path / "store")
    pack = baseline_pack()
    env = environment(model={"id": "test"})
    pol = policy(pack.ref(), scope={"include": [cid]}, values={cid: values})
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    name = pack.checks[cid].executor
    old = registry.EXECUTORS.get(name + ":sim")
    registry.EXECUTORS[name + ":sim"] = lambda cfg: factory({"target": str(path), "vocab_size":100,
                                                           "vocab_source":"synthetic model fixture"})
    try:
        evd, = run_plan(store, pack, plan, RunConfig(executors={name:"sim"}, adapter="vllm"))
        result, = judge_plan(store, pack, plan, env.created_at)
        assert evd.data_origin.value == "EXAMPLE"
        return evd, result
    finally:
        if old is None: registry.EXECUTORS.pop(name + ":sim")
        else: registry.EXECUTORS[name + ":sim"] = old


@pytest.mark.parametrize("outcome, expected", [(400,"PASS"),(200,"FAIL"),(206,"FAIL"),("timeout","UNKNOWN"),(500,"UNKNOWN")])
def test_token_boundary_contract(tmp_path, outcome, expected):
    def probe(net, requests, timeout):
        return [{"outcome": 200 if r["body"]["prompt"] == [0] else outcome} for r in requests]
    _, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                         {"vocab_size": 100, "max_input_tokens": 8, "rejection_codes":["400","422"]})
    assert result.verdicts.security.value == expected


def test_input_budget_aborts_before_requests(tmp_path):
    def probe(*args): raise AssertionError("must not send a request")
    evd, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                          {"vocab_size": 100, "max_input_tokens": 1000000, "rejection_codes":["400"]})
    assert evd.execution.status.value == "ABORTED"
    assert result.verdicts.security.value == "UNKNOWN"


def test_same_response_changes_with_explicit_rejection_policy(tmp_path):
    probe = lambda n,rs,t: [{"outcome":200 if r["body"]["prompt"] == [0] else 400} for r in rs]
    _, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                         {"vocab_size":100,"max_input_tokens":8,"rejection_codes":["422"]})
    assert result.verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("leak, unreadable, expected", [(False,False,"PASS"),(True,False,"FAIL"),(False,True,"UNKNOWN"),(True,True,"FAIL")])
def test_credential_exposure_contract(tmp_path, leak, unreadable, expected):
    class Docker:
        def inspect_json(self, container, *, timeout): return {"Mounts":[]}
        def exec(self, container, argv, *, timeout):
            assert argv == ["sh","-c",ENV_CMD]
            return CmdResult(0, "PID 1\nPATH\n" + ("GUARD_TEST_CREDENTIAL\n" if leak else "") +
                             ("GUARD_READ_ERROR\n" if unreadable else "GUARD_READ_OK\n"), "")
    probe = lambda n,rs,t: [{"outcome":200} for r in rs]
    _, result = pipeline(tmp_path, "PLT-04",
                         lambda c: CredentialExposureExecutor(c, container_probe=probe, docker=Docker()),
                         {"forbidden_env_names":["GUARD_TEST_CREDENTIAL"],"forbidden_mount_paths":["/test-creds"]})
    assert result.verdicts.security.value == expected
