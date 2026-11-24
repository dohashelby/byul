from __future__ import annotations

import json
from dataclasses import replace
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import stat
import shutil
import sys
import time

import pytest
from fastapi.testclient import TestClient

from byul_platform.accounts import AccountStore
from byul_platform.config import REPO_ROOT, Settings
from byul_platform.main import create_app
from byul_platform.contracts import canonical_json, canonical_sha256

FIXTURE = Path(__file__).parent / "fixtures/update/RPT-01M598RR3HPSJNHA1MENFA6P74.json"
PREPARATION = {"ready": True, "checks": [{"check": "inputs", "ok": True, "detail": "fixture inputs"}],
               "check_support": [{"side": "single", "check_id": "REQ-01", "status": "UNKNOWN", "reasons": ["fixture only"]}],
               "guidance": {"summary": "preparation only", "issues": []}}

# A subprocess fixture, never an engine or a security measurement. Exercise argv,
# pipe streaming, persistence and RPT ingestion through the real platform routes.
CLI = '''\
import json
import os
from pathlib import Path
import sys
import time

args = sys.argv[1:]
flags = json.loads(Path("flags.json").read_text())
with Path("calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\\n")
if args[0].startswith("prepare-"):
    if flags.get("hold"):
        while not Path("release").exists():
            time.sleep(.01)
    if flags.get("malformed"):
        print("not preparation JSON")
    else:
        data = json.loads(Path("preparation.json").read_text())
        data["ready"] = not flags.get("blocked", False)
        print(json.dumps(data))
    sys.exit(1 if flags.get("blocked") else 0)
print("fixture verification starting", flush=True)
if flags.get("split_secret"):
    secret = os.environ["GUARD_TEST_TOKEN"]
    print(secret[:len(secret)//2], end="", flush=True)
    time.sleep(.03)
    print(secret[len(secret)//2:], flush=True)
print("password='literal-private-value'", flush=True)
print("<script>not HTML</script>", flush=True)
if flags.get("long_log"):
    print("로그" * 50000)
if not flags.get("no_report"):
    folder = Path(args[args.index("--output") + 1]) / "fixture-run"
    folder.mkdir(parents=True)
    (folder / "report.json").write_bytes(Path("fixture-rpt.json").read_bytes())
sys.exit(flags.get("exit_code", 0))
'''


@pytest.fixture
def execution(tmp_path):
    update = tmp_path / "update"
    (update / "guard").mkdir(parents=True)
    (update / "guard/__init__.py").touch()
    (update / "guard/cli.py").write_text(CLI)
    shutil.copytree(REPO_ROOT / "update/packs/PACK-BASELINE", update / "packs/PACK-BASELINE")
    (update / "tools").mkdir()
    shutil.copyfile(REPO_ROOT / "update/tools/import_byul_inputs.py", update / "tools/import_byul_inputs.py")
    (update / "guard.toml").write_text("[credentials]\npassword = 'literal-private-value'\n")
    (update / "flags.json").write_text("{}")
    (update / "preparation.json").write_text(json.dumps(PREPARATION))
    (update / "fixture-rpt.json").write_bytes(FIXTURE.read_bytes())
    settings = Settings(update_dir=update, update_python=Path(sys.executable), platform_data_dir=tmp_path / "data",
                        update_config_file=update / "guard.toml", web_dist=tmp_path / "web")
    app = create_app(settings)
    seed_documents(app)
    with TestClient(app) as client:
        try:
            yield client, update, settings
        finally:
            (update / "release").touch()  # Unblock fixture work before the graceful shutdown waits.


def payload(mode="validate"):
    return {"mode": mode, "policy": {"id": "policy-one", "revision": 1},
            **({"environment": {"id": "environment-one", "revision": 1}} if mode == "validate" else
               {"baseline": {"id": "environment-one", "revision": 1}, "candidate": {"id": "environment-one", "revision": 2}})}


def seed_documents(app):
    from guard.models import load_pack

    state = app.state.byul
    environment = json.loads((REPO_ROOT / "contracts/examples/environment.json").read_bytes())
    environment.update(environment_id="environment-one", revision=1)
    environment["target"]["model"].update(tokenizer_revision="fixture-tokenizer", components=[])
    environment["target"]["engine"].update(runtime_kind="vllm", frontend="engine-server")
    environment["target"]["runtime"].update(tp_size=1, pp_size=1, dp_size=1)
    state.environments.append("environment-one", 1, environment, status="DRAFT", label=None)
    candidate = deepcopy(environment)
    candidate["revision"] = 2
    candidate["target"]["engine"]["version"] = "0.32.0"
    state.environments.append("environment-one", 2, candidate, status="DRAFT", label=None)
    policy = json.loads((REPO_ROOT / "contracts/examples/policy.json").read_bytes())
    policy["policy_id"] = "policy-one"
    policy["environment_ref"] = {"id": "environment-one", "revision": 1, "sha256": canonical_sha256(environment)}
    pack = load_pack(state.settings.update_dir / "packs/PACK-BASELINE")
    policy["verification"]["pack"] = {"id": pack.ref().id, "revision": pack.ref().revision}
    policy["validity"] = {"valid_from": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
                          "valid_until": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    state.policies.append("policy-one", 1, policy, status=policy["status"], label=policy["policy_version"])


def flags(update, **values):
    (update / "flags.json").write_text(json.dumps(values))


def calls(update):
    path = update / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def settled(client, run_id):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/update/runs/{run_id}")
        assert response.status_code == 200, response.text
        result = response.json()
        if result["status"] not in {"preparing", "running"}:
            # The terminal record is saved before the worker releases its lease.
            # Drain the single-worker queue before the next command or restart.
            client.app.state.byul.update_execution._pool.submit(lambda: None).result()
            return result
        time.sleep(.01)
    pytest.fail("fixture command did not finish")


def prepared(client, mode="validate"):
    response = client.post("/api/update/runs/prepare", json=payload(mode))
    assert response.status_code == 202, response.text
    result = settled(client, response.json()["id"])
    assert result["status"] == "ready", result
    return result


@pytest.mark.parametrize("mode,commands", [("validate", ["prepare-validation", "prepare-validation", "validate"]),
                                           ("update", ["prepare-update", "prepare-update", "update"])])
def test_prepare_execute_report_and_immutable_inputs(execution, mode, commands):
    client, update, settings = execution
    before = {path: path.read_bytes() for path in settings.platform_data_dir.rglob("*.json")}
    # GETs cannot start a command.
    assert client.get("/api/update/execution-settings").json()["can_execute"] is True
    assert client.get("/api/update/runs").json()["items"] == []
    assert calls(update) == []
    run = prepared(client, mode)
    assert [argv[0] for argv in calls(update)] == commands[:1]
    assert not (settings.platform_data_dir / "update-runs/objects").exists()
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 202, response.text
    done = settled(client, run["id"])
    assert done["status"] == "completed" and done["exit_code"] == 0, done
    assert done["requested_by"] == done["executed_by"] == "local-development"
    assert done["request"] == {**payload(mode), **({"baseline": None, "candidate": None} if mode == "validate" else {"environment": None})}
    assert done["source_refs"]["policy"]["sha256"] == client.app.state.byul.policies.get("policy-one", 1).sha256
    assert [argv[0] for argv in calls(update)] == commands
    for argv in calls(update):
        for option in ("--pack", "--config", "--policy", "--env" if mode == "validate" else "--baseline"):
            assert "/inputs/" in argv[argv.index(option) + 1]
    assert "literal-private-value" not in done["log_tail"]
    assert "[REDACTED]" in done["log_tail"]
    assert "<script>not HTML</script>" in done["log_tail"]
    assert before == {path: path.read_bytes() for path in before}
    report = client.get(f"/api/update/results/{done['report_ids'][0]}")
    assert report.status_code == 200, report.text
    assert report.json()["item"]["report_ref"]["id"] == json.loads(FIXTURE.read_bytes())["id"]
    assert report.json()["source"]["mode"] == "live"
    assert json.loads(FIXTURE.read_bytes()) == json.loads(Path(report.json()["raw_path"]).read_bytes())
    for path in (settings.platform_data_dir / "update-runs" / run["id"]).rglob("*"):
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # Completion is one-shot. A new run requires preparation.
    assert client.post(f"/api/update/runs/{run['id']}/execute", json={}).status_code == 409


def test_not_ready_cannot_execute_or_create_guard_store(execution):
    client, update, settings = execution
    flags(update, blocked=True)
    response = client.post("/api/update/runs/prepare", json=payload())
    run = settled(client, response.json()["id"])
    assert run["status"] == "blocked" and run["preparation"]["ready"] is False
    assert client.post(f"/api/update/runs/{run['id']}/execute", json={}).status_code == 409
    assert len(calls(update)) == 1
    assert not (settings.platform_data_dir / "update-runs/objects").exists()


def test_host_is_checked_again_before_execution(execution):
    client, update, _ = execution
    run = prepared(client)
    flags(update, blocked=True)
    assert client.post(f"/api/update/runs/{run['id']}/execute", json={}).status_code == 202
    assert settled(client, run["id"])["status"] == "blocked"
    assert [argv[0] for argv in calls(update)] == ["prepare-validation"] * 2


def test_preparation_writes_each_registered_snapshot_file_once(execution, monkeypatch):
    client, _, _ = execution
    generated = {"baseline.json", "candidate.json", "policy.json", "source-baseline.json",
                 "source-candidate.json", "source-policy.json", "provenance.json"}
    writes = []
    write_bytes = Path.write_bytes

    def track_write(path, data):
        if path.parent.name == "inputs" and path.name in generated:
            writes.append(path.name)
        return write_bytes(path, data)

    monkeypatch.setattr(Path, "write_bytes", track_write)
    prepared(client, "update")
    assert sorted(writes) == sorted(generated)


@pytest.mark.parametrize("changed", ["guard.toml", "packs/PACK-BASELINE/manifest.yaml"])
def test_changed_source_requires_new_preparation(execution, changed):
    client, update, _ = execution
    run = prepared(client)
    (update / changed).write_text("changed")
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "INPUT_CHANGED"
    assert len(calls(update)) == 1


def test_changed_snapshot_never_reaches_execution(execution):
    client, update, settings = execution
    run = prepared(client)
    (settings.platform_data_dir / "update-runs" / run["id"] / "inputs/environment.json").write_text("tampered")
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "INPUT_CHANGED"
    assert len(calls(update)) == 1


def test_concurrent_preparations_are_rejected_across_app_workers(execution):
    client, update, settings = execution
    flags(update, hold=True)
    response = client.post("/api/update/runs/prepare", json=payload())
    assert response.status_code == 202
    with TestClient(create_app(settings)) as second:
        assert client.post("/api/update/runs/prepare", json=payload()).status_code == 409
        assert second.post("/api/update/runs/prepare", json=payload()).status_code == 409
    (update / "release").touch()
    assert settled(client, response.json()["id"])["status"] == "ready"


@pytest.mark.parametrize("case,expected_reports", [({"exit_code": 1}, True), ({"no_report": True}, False)])
def test_exit_code_and_report_required_for_command_completion(execution, case, expected_reports):
    client, update, _ = execution
    run = prepared(client)
    flags(update, **case)
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 202, response.text
    done = settled(client, run["id"])
    assert done["status"] == "failed"
    assert bool(done["report_ids"]) is expected_reports


def test_malformed_preparation_output_is_failure_and_releases_lock(execution):
    client, update, _ = execution
    flags(update, malformed=True)
    response = client.post("/api/update/runs/prepare", json=payload())
    assert settled(client, response.json()["id"])["status"] == "failed"
    flags(update)
    assert prepared(client)["status"] == "ready"


@pytest.mark.parametrize("long_log", [False, True])
def test_secrets_split_across_reads_are_redacted_and_logs_are_bounded(execution, monkeypatch, long_log):
    client, update, _ = execution
    monkeypatch.setenv("GUARD_TEST_TOKEN", "never-expose-this-test-value")
    run = prepared(client)
    flags(update, split_secret=True, long_log=long_log)
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 202, response.text
    done = settled(client, run["id"])
    assert done["status"] == "completed" and done["log_truncated"] is long_log
    assert len(done["log_tail"].encode()) <= 64 * 1024
    assert "never-expose" not in done["log_tail"]
    if not long_log:
        assert "[REDACTED]" in done["log_tail"]


def test_history_and_report_survive_a_platform_restart(execution):
    client, _, settings = execution
    run = prepared(client)
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 202, response.text
    done = settled(client, run["id"])
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/update/runs").json()["items"][0]["id"] == run["id"]
        assert restarted.get(f"/api/update/runs/{run['id']}").json()["status"] == "completed"
        assert restarted.get(f"/api/update/results/{done['report_ids'][0]}").status_code == 200


def test_relative_data_dir_keeps_subprocess_paths_absolute(execution, tmp_path, monkeypatch):
    _, update, settings = execution
    monkeypatch.chdir(tmp_path)
    relative_app = create_app(replace(settings, platform_data_dir=Path("relative-data")))
    seed_documents(relative_app)
    with TestClient(relative_app) as client:
        run = prepared(client)
        response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
        assert response.status_code == 202, response.text
        assert settled(client, run["id"])["status"] == "completed"
        for argv in calls(update):
            assert Path(argv[argv.index("--pack") + 1]).is_absolute()


def test_stale_execution_is_interrupted_not_completed(execution):
    client, _, settings = execution
    run = prepared(client)
    path = settings.platform_data_dir / "update-runs" / run["id"] / "run.json"
    record = json.loads(path.read_text())
    record["status"] = "running"
    path.write_text(json.dumps(record))
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get(f"/api/update/runs/{run['id']}").json()["status"] == "interrupted"


def test_only_admin_can_prepare_and_execute_with_csrf(execution):
    _, update, settings = execution
    authenticated = replace(settings, login_enabled=True)
    with TestClient(create_app(authenticated)) as client:
        assert client.post("/api/update/runs/prepare", json=payload()).status_code == 401
        accounts = AccountStore(settings.platform_data_dir / "accounts/accounts.json")
        temporary = accounts.create("readuser", "viewer")
        login = client.post("/api/auth/login", json={"username": "readuser", "password": temporary}).json()
        assert client.get("/api/update/execution-settings").json()["can_execute"] is False
        response = client.post("/api/update/runs/prepare", json=payload(), headers={"X-CSRF-Token": login["csrf_token"]})
        assert response.status_code == 403 and response.json()["detail"]["code"] == "ROLE_FORBIDDEN"
        login = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()
        assert client.post("/api/update/runs/prepare", json=payload()).status_code == 403
        response = client.post("/api/update/runs/prepare", json=payload(), headers={"X-CSRF-Token": login["csrf_token"]})
        assert response.status_code == 202, response.text
        assert settled(client, response.json()["id"])["requested_by"] == "admin"


def test_invalid_selections_client_paths_and_missing_python_do_not_spawn(execution):
    client, update, settings = execution
    assert client.post("/api/update/runs/prepare", json={**payload(), "environment": {"id": "missing", "revision": 1}}).status_code == 422
    assert client.post("/api/update/runs/prepare", json={**payload(), "command": "echo arbitrary"}).status_code == 422
    assert client.post("/api/update/runs/prepare", json={**payload(), "pack_path": "/tmp/override"}).status_code == 422
    assert client.post("/api/update/runs/prepare", json={**payload(), "config_path": "/tmp/override"}).status_code == 422
    assert client.post("/api/update/runs/prepare", json=payload("update") | {"candidate": None}).status_code == 422
    assert client.get("/api/update/runs/bogus").status_code == 404
    unavailable = replace(settings, update_python=update / "missing-python")
    with TestClient(create_app(unavailable)) as other:
        assert other.get("/api/update/execution-settings").json()["available"] is False
        assert other.post("/api/update/runs/prepare", json=payload()).status_code == 503
    assert calls(update) == []


def test_actual_guard_preparation_keeps_unprepared_registered_environment_out_of_engine_execution(tmp_path, monkeypatch):
    # Existing converter + guard CLI, with an unprepared EXAMPLE environment.
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT / "update/src") + os.pathsep + os.environ.get("PYTHONPATH", ""))
    app = create_app(Settings(update_python=Path(sys.executable), platform_data_dir=tmp_path / "data"))
    seed_documents(app)
    with TestClient(app) as client:
        response = client.post("/api/update/runs/prepare", json=payload())
        assert response.status_code == 202, response.text
        run = settled(client, response.json()["id"])
        assert run["status"] == "blocked", run
        assert run["preparation"]["guidance"]["engine_started"] is False
        assert run["report_ids"] == []


def test_registered_conversion_preserves_sources_refs_and_common_policy(execution):
    from guard.collectors.file import load_environment, load_policy
    from guard.models import load_pack

    client, _, settings = execution
    run = prepared(client, "update")
    folder = settings.platform_data_dir / "update-runs" / run["id"] / "inputs"
    state = client.app.state.byul
    policy = state.policies.get("policy-one", 1)
    for role, ref in run["source_refs"].items():
        store = state.policies if role == "policy" else state.environments
        document = store.get(ref["id"], ref["revision"]).document
        assert (folder / f"source-{role}.json").read_bytes() == canonical_json(document)
        assert ref["sha256"] == canonical_sha256(document)
    pack = load_pack(folder / "pack")
    converted_policy = load_policy(folder / "policy.json", pack)
    assert converted_policy.source_document_sha256 == policy.sha256
    assert converted_policy.status.value == policy.document["status"]
    candidate = load_environment(folder / "candidate.json")
    source = state.environments.get("environment-one", 2).document
    # Use the producer for a test-only candidate context to prove equal ENV mapping.
    comparison_policy = deepcopy(policy.document)
    comparison_policy["environment_ref"] = {"id": source["environment_id"], "revision": source["revision"], "sha256": canonical_sha256(source)}
    boundary = state.update_execution.registered_inputs._boundary()
    expected, _, _ = boundary.convert_documents(source, comparison_policy, pack, state.contracts, as_of=datetime.now(UTC), version=2)
    expected = expected.model_copy(update={"id": candidate.id}).seal()
    assert candidate.to_dict() == expected.to_dict()
    assert not (folder / "candidate-policy.json").exists()
    assert json.loads((folder / "provenance.json").read_bytes())["policy_binding"] == "baseline"


def test_policy_binding_and_server_pack_revision_are_not_silently_rewritten(execution):
    client, update, _ = execution
    mismatch = payload() | {"environment": {"id": "environment-one", "revision": 2}}
    response = client.post("/api/update/runs/prepare", json=mismatch)
    assert response.status_code == 422 and "환경 판본" in response.json()["detail"]["message"]
    policy = deepcopy(client.app.state.byul.policies.get("policy-one", 1).document)
    policy["verification"]["pack"]["revision"] -= 1
    client.app.state.byul.policies.append("policy-one", 2, policy, status=policy["status"], label=policy["policy_version"])
    response = client.post("/api/update/runs/prepare", json=payload() | {"policy": {"id": "policy-one", "revision": 2}})
    assert response.status_code == 422 and "기준 판본" in response.json()["detail"]["message"]
    assert calls(update) == []


def test_registered_revision_tampering_blocks_execution(execution):
    client, update, settings = execution
    run = prepared(client)
    path = settings.platform_data_dir / "environments/environment-one/000001.json"
    record = json.loads(path.read_bytes())
    record["document"]["target"]["engine"]["version"] = "tampered"
    path.write_text(json.dumps(record))
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "REGISTERED_INPUT_CHANGED"
    assert len(calls(update)) == 1


def test_candidate_cannot_target_another_registered_system(execution):
    client, update, _ = execution
    store = client.app.state.byul.environments
    other = deepcopy(store.get("environment-one", 2).document)
    other.update(environment_id="other-environment", revision=1)
    other["target"]["target_ref"] = "other-system"
    store.append("other-environment", 1, other, status="DRAFT", label=None)
    response = client.post("/api/update/runs/prepare", json=payload("update") | {"candidate": {"id": "other-environment", "revision": 1}})
    assert response.status_code == 422 and "적용 대상" in response.json()["detail"]["message"]
    assert calls(update) == []


def test_new_revision_does_not_replace_the_selected_immutable_revision(execution):
    client, _, _ = execution
    run = prepared(client)
    store = client.app.state.byul.environments
    document = deepcopy(store.get("environment-one", 2).document)
    document["revision"] = 3
    store.append("environment-one", 3, document, status="DRAFT", label=None)
    assert client.post(f"/api/update/runs/{run['id']}/execute", json={}).status_code == 202
    done = settled(client, run["id"])
    assert done["status"] == "completed" and done["source_refs"]["environment"]["revision"] == 1


def test_policy_validity_is_rechecked_at_execution(execution, monkeypatch):
    from byul_platform.sources import registered_update_inputs

    client, update, _ = execution
    run = prepared(client)
    class AfterExpiry(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(UTC) + timedelta(days=2)
    monkeypatch.setattr(registered_update_inputs, "datetime", AfterExpiry)
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 409 and "유효 기간" in response.json()["detail"]["message"]
    assert len(calls(update)) == 1


def test_old_file_runs_remain_readable_without_accepting_file_based_execution(execution):
    client, update, settings = execution
    run = prepared(client)
    path = settings.platform_data_dir / "update-runs" / run["id"] / "run.json"
    record = json.loads(path.read_bytes())
    record["request"] = record.pop("file_inputs")
    path.write_text(json.dumps(record))
    assert client.get(f"/api/update/runs/{run['id']}").status_code == 200
    response = client.post(f"/api/update/runs/{run['id']}/execute", json={})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "REGISTERED_INPUT_REQUIRED"
    assert len(calls(update)) == 1
