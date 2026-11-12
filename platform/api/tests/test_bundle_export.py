"""Platform export uses the real runtime builder and preserves its golden policy bytes."""

from __future__ import annotations

import base64
import copy
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import time
from urllib.request import urlopen

import pytest

from byul_platform.contracts import canonical_sha256

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"


@pytest.fixture(autouse=True)
def runtime_cli(monkeypatch, store_client):
    """Exercise runtime source with the test interpreter on laptops and CI."""
    source = str(RUNTIME / "src")
    previous = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", source + (os.pathsep + previous if previous else ""))
    state = store_client.app.state.byul
    monkeypatch.setattr(state, "settings", replace(state.settings, bundle_runtime_python=Path(sys.executable)))


def _inputs():
    summary = json.loads((RUNTIME / "deploy/gpu-lab/platform-approval.aws-lab.json").read_text())
    server = json.loads((RUNTIME / "tests/fixtures/bundle-server-vllm.json").read_text())
    intent = json.loads((RUNTIME / "deploy/gpu-lab/intent.aws-lab.json").read_text()
                        .replace("REPLACE_IMAGE_DIGEST_HEX", "a" * 64).replace("REPLACE_PROFILE", "test"))
    env = json.loads((ROOT / "contracts/examples/environment.json").read_text())
    env["environment_id"] = "bundle-test-env"
    env["target"]["target_ref"] = summary["target_ref"]
    env["target"]["engine"].update({"runtime_kind": "vllm", "version": server["target"]["engine"]["version"],
                                      "image_digest": server["target"]["engine"]["image_digest"]})
    env["target"]["model"]["id"] = server["target"]["model"]["id"]
    env["deployment"] = {
        "kind": "kubernetes", "kubernetes_context": server["target"]["kubernetes"]["context"],
        "namespace": server["target"]["kubernetes"]["namespace"],
        "engine_labels": server["target"]["kubernetes"]["selector"],
        "engine_container": server["target"]["kubernetes"]["container"],
        "gateway_schema_version": "runtime-mvp/1.1", "enforcement_intent": intent,
    }
    policy = {
        "schema_version": "byul-policy/0.1", "policy_id": summary["policy_id"],
        "policy_version": summary["policy_version"], "created_at": "2026-11-03T05:18:02Z",
        "data_origin": "EXAMPLE", "status": "CONFIRMED", "target_ref": summary["target_ref"],
        "environment_ref": {"id": env["environment_id"], "revision": 1, "sha256": canonical_sha256(env)},
        "validity": summary["validity"], "approval": summary["approval"],
        "verification": {"pack": {"id": "PACK-TEST", "revision": 1}, "values": {},
                         "scope": {"include": [], "exclude": []}, "exceptions": []},
        "runtime": summary["runtime"],
    }
    return env, policy


def _save(client, env, policy):
    state = client.app.state.byul
    state.environments.append(env["environment_id"], 1, env, status="DRAFT", label=None)
    state.policies.append(policy["policy_id"], 1, policy, status=policy["status"], label=policy["policy_version"])


def _request(client, policy, **extra):
    return client.post(f"/api/policies/{policy['policy_id']}/revisions/1/bundle", json={
        "environment_id": "bundle-test-env", "environment_revision": 1,
        "requested_by_role": "runtime_operator", "bundle_id": "bundle-test", **extra})


def test_export_real_runtime_golden_and_audit(store_client, tmp_path):
    env, policy = _inputs()
    _save(store_client, env, policy)
    response = _request(store_client, policy)
    assert response.status_code == 200, response.text
    metadata = json.loads(base64.urlsafe_b64decode(response.headers["X-Byul-Bundle-Metadata"]))
    assert metadata["server_values_source"] == "environment_revision"
    assert len(metadata["files"]) == 4
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        members = {member.name: archive.extractfile(member).read() for member in archive.getmembers()}
    manifest = members["bundle-test/bundle.json"]
    assert hashlib.sha256(manifest).hexdigest() == metadata["bundle_sha256"]
    assert members["bundle-test/runtime-policy.json"] == (RUNTIME / "deploy/gpu-lab/policy.aws-lab.json").read_bytes()
    bundle_dir = tmp_path / "bundle-test"
    bundle_dir.mkdir()
    for name, content in members.items():
        (bundle_dir / Path(name).name).write_bytes(content)
    verified = subprocess.run([sys.executable, "-B", "-m", "serving_runtime", "bundle", "verify",
                               "--bundle-dir", str(bundle_dir)], cwd=RUNTIME, capture_output=True, text=True)
    assert verified.returncode == 0, verified.stderr
    assert len(list((tmp_path / "data/bundle_exports").glob("*.json"))) == 1


def test_export_rejects_draft_hash_and_missing_fields(store_client):
    env, policy = _inputs()
    draft = copy.deepcopy(policy)
    draft["status"] = "DRAFT"
    _save(store_client, env, draft)
    assert _request(store_client, draft).status_code == 409
    state = store_client.app.state.byul
    state.policies.append(policy["policy_id"], 2, policy, status="CONFIRMED", label=policy["policy_version"])
    response = store_client.post(f"/api/policies/{policy['policy_id']}/revisions/2/bundle", json={
        "environment_id": env["environment_id"], "environment_revision": 1, "requested_by_role": "runtime_operator"})
    assert response.status_code == 200, response.text
    stored_path = state.policies.dir / policy["policy_id"] / "000002.json"
    stored = json.loads(stored_path.read_text())
    stored["document"]["runtime"]["gateway"]["output_max_tokens"] = 1
    stored_path.write_text(json.dumps(stored))
    assert store_client.post(f"/api/policies/{policy['policy_id']}/revisions/2/bundle", json={
        "environment_id": env["environment_id"], "environment_revision": 1, "requested_by_role": "runtime_operator"}).status_code == 409

    env2, policy2 = _inputs()
    del env2["deployment"]["engine_container"]
    policy2["policy_id"] = "missing-fields-policy"
    policy2["environment_ref"] = {"id": env2["environment_id"], "revision": 3, "sha256": canonical_sha256(env2)}
    state.environments.append(env2["environment_id"], 3, env2, status="DRAFT", label=None)
    state.policies.append(policy2["policy_id"], 1, policy2, status="CONFIRMED", label=policy2["policy_version"])
    missing = store_client.post(f"/api/policies/{policy2['policy_id']}/revisions/1/bundle", json={
        "environment_id": env2["environment_id"], "environment_revision": 3, "requested_by_role": "runtime_operator"})
    assert missing.status_code == 422
    assert "/deployment/engine_container" in [error["path"] for error in missing.json()["errors"]]


def test_saved_confirmed_export_starts_temporary_gateway(store_client, tmp_path):
    env, policy = _inputs()
    policy["verification"]["pack"] = {"id": "PACK-BASELINE", "revision": 34}
    saved_env = store_client.post("/api/environments", json=env)
    assert saved_env.status_code == 201, saved_env.text
    policy["status"] = "DRAFT"
    policy["approval"] = None
    policy["environment_ref"]["sha256"] = saved_env.json()["saved"]["meta"]["sha256"]
    draft = store_client.post("/api/policies", json=policy)
    assert draft.status_code == 201, draft.text
    policy_id = policy["policy_id"]
    confirmed = store_client.post(f"/api/policies/{policy_id}/confirm", json={"revision": 1, "approved_by": "runtime_operator"})
    assert confirmed.status_code == 201, confirmed.text
    response = store_client.post(f"/api/policies/{policy_id}/revisions/2/bundle", json={
        "environment_id": env["environment_id"], "environment_revision": 1,
        "requested_by_role": "runtime_operator", "bundle_id": "flow-test"})
    assert response.status_code == 200, response.text
    with tarfile.open(fileobj=io.BytesIO(response.content), mode="r:gz") as archive:
        for member in archive.getmembers():
            target = tmp_path / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())
    bundle_dir = tmp_path / "flow-test"
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        gateway_port = port_socket.getsockname()[1]
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        engine_port = port_socket.getsockname()[1]
    process = subprocess.Popen([sys.executable, "-B", "-m", "serving_runtime", "mvp",
                                "--policy-bundle", str(bundle_dir), "--gateway-port", str(gateway_port),
                                "--engine-port", str(engine_port), "--data-dir", str(tmp_path / "gateway-data")],
                               cwd=RUNTIME, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        status = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                with urlopen(f"http://127.0.0.1:{gateway_port}/api/status", timeout=1) as answer:
                    status = json.load(answer)
                break
            except OSError:
                time.sleep(0.2)
        assert status is not None, process.stderr.read() if process.poll() is not None else "Gateway 시작 시간 초과"
        assert status["policy_bundle"]["bundle_id"] == "flow-test"
        assert status["policy"]["gateway_active"] is True
        assert status["policy"]["approval"]["state"] == "verified"
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        process.stderr.close()
