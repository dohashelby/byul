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
from tests.builders import baseline_pack, environment, policy, completion_response
from tests.response_semantics_support import with_legacy_check
from tests.test_credential_mount_semantics import FakeDocker as CredentialFixture, IMAGE as CREDENTIAL_IMAGE, RUN as CREDENTIAL_RUN


def pipeline(tmp_path, cid, factory, values):
    target = {"lab": True, "engine": {"trusted_url": "http://engine:8000", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    store = Store(tmp_path / "store")
    # Historical status-only PACK21 component contracts; new semantics have
    # independent regression tests, including challenge-only HTTP200 errors.
    pack = with_legacy_check(baseline_pack(), cid)
    if cid == "REQ-09":
        values = {"allowed_endpoints": [pack.checks[cid].functional_probes[0].id],
                  "allowed_input_formats": ["token_ids"], **values}  # Explicit EXAMPLE test fixture, not defaults.
    env = environment(model={"id": "test"})
    if cid == "PLT-04":
        env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": CREDENTIAL_IMAGE}, model={"id": "test"})
        target.update(environment_ref=env.ref().model_dump(mode="json"), run_id=CREDENTIAL_RUN)
        path.write_text(json.dumps(target), encoding="utf-8")
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
        return [completion_response(r, 200 if r["body"]["prompt"] == [0] else outcome) for r in requests]
    _, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                         {"vocab_size": 100, "max_input_tokens": 8, "rejection_codes":["400","422"]})
    assert result.verdicts.security.value == expected


def test_input_budget_aborts_before_requests(tmp_path):
    def probe(*args): raise AssertionError("must not send a request")
    evd, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                          {"vocab_size": 100, "max_input_tokens": 1000000, "rejection_codes":["400"]})
    assert evd.execution.status.value == "ABORTED"
    assert result.verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("budget", [8, 1, "9", True])
def test_boundary_request_must_fit_strict_token_budget(tmp_path, budget):
    def probe(*args): raise AssertionError("must reject before any request")
    evd, _ = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor({**c, "max_probe_tokens":budget}, container_probe=probe),
                      {"max_input_tokens":8, "rejection_codes":["400"]})
    assert evd.execution.status.value == "ABORTED"


def test_same_response_changes_with_explicit_rejection_policy(tmp_path):
    probe = lambda n,rs,t: [completion_response(r, 200 if r["body"]["prompt"] == [0] else 400) for r in rs]
    _, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                         {"vocab_size":100,"max_input_tokens":8,"rejection_codes":["422"]})
    assert result.verdicts.security.value == "UNKNOWN"


@pytest.mark.parametrize("leak, unreadable, expected", [(False,False,"PASS"),(True,False,"FAIL"),(False,True,"UNKNOWN"),(True,True,"FAIL")])
def test_credential_exposure_contract(tmp_path, leak, unreadable, expected):
    class Docker(CredentialFixture):
        def exec(self, container, argv, *, timeout):
            assert argv == ["sh","-c",ENV_CMD]
            return CmdResult(0, "PID 1\nPATH\n" + ("GUARD_TEST_CREDENTIAL\n" if leak else "") +
                             ("GUARD_READ_ERROR\n" if unreadable else "GUARD_READ_OK\n"), "")
    probe = lambda n,rs,t: [completion_response(r) for r in rs]
    _, result = pipeline(tmp_path, "PLT-04",
                         lambda c: CredentialExposureExecutor(c, container_probe=probe, docker=Docker()),
                         {"forbidden_env_names":["GUARD_TEST_CREDENTIAL"],"forbidden_mount_paths":["/test-creds"]})
    assert result.verdicts.security.value == expected


@pytest.mark.parametrize("raw", ["{}", '{"error":"generation failed"}', "<html>not inference</html>"])
def test_req09_http_200_error_body_does_not_establish_normal_inference(tmp_path, raw):
    sent = []
    def probe(net, requests, timeout):
        sent.extend(requests)
        return [{"outcome": 200, "body": raw} for _ in requests]
    evd, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                           {"max_input_tokens": 8, "rejection_codes": ["400"]})
    assert evd.execution.status.value == "COMPLETED" and evd.normal_control.succeeded is False
    assert result.verdicts.security.value == result.verdicts.functional.value == "UNKNOWN"
    assert len(sent) == 1


def test_req09_confirmed_boundary_acceptance_survives_later_probe_exception(tmp_path):
    calls = 0
    def probe(net, requests, timeout):
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise TimeoutError("private transport details")
        return [completion_response(request) for request in requests]
    evd, result = pipeline(tmp_path, "REQ-09", lambda c: InputValidationExecutor(c, container_probe=probe),
                           {"max_input_tokens": 8, "rejection_codes": ["400"]})
    assert evd.execution.status.value == "COMPLETED"
    assert result.verdicts.security.value == "FAIL"
    assert "private transport details" not in evd.model_dump_json()
