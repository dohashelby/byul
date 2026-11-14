"""Execute the first-install documentation snippets against real loaders."""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from serving_runtime.gateway.auth import load_auth
from serving_runtime.policy.policy_bundle import verify_policy_bundle

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "deploy/install/첫_설치_준비.md"


def snippet(marker):
    document = DOC.read_text()
    blocks = [block.split("```", 1)[0] for block in document.split("```python\n")[1:]]
    return next(block for block in blocks if marker in block)


def test_new_authentication_instructions_create_private_distinct_tokens(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    before = os.umask(0o077)
    try:
        exec(compile(snippet("TOKEN_SETUP"), str(DOC), "exec"), {})
    finally:
        os.umask(before)
    folder = tmp_path / "private-install"
    assert folder.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in folder.iterdir())
    auth = load_auth(folder / "auth.json")
    assert auth.state == "ok"
    pairs = dict(line.split("=", 1) for line in (folder / "runtime-acceptance.env").read_text().splitlines())
    assert len(set(pairs.values())) == 2
    for name, token in pairs.items():
        result = auth.authenticate("Bearer " + token)
        assert result.state == "authenticated"
        expected = "inference" if name == "RUNTIME_INFERENCE_TOKEN" else "viewer"
        assert expected in result.principal.roles
        assert token not in (folder / "auth.json").read_text()
    with pytest.raises(FileExistsError):
        exec(snippet("TOKEN_SETUP"), {})


def test_first_lab_bundle_instructions_need_no_platform_or_previous_bundle(tmp_path, monkeypatch):
    intent = json.loads((ROOT / "deploy/gpu-lab/intent.aws-lab.json").read_text())
    intent["assets"]["cache_generation"].update(image_id="a" * 64, compile_profile="reviewed-profile-1")
    target = json.loads((ROOT / "deploy/gpu-lab/target.aws-lab.json").read_text())
    target["engine"]["image_digest"] = "sha256:" + "a" * 64
    (tmp_path / "intent.json").write_text(json.dumps(intent))
    (tmp_path / "target.json").write_text(json.dumps(target))
    cfg = dict(
        intent_file=str(tmp_path / "intent.json"),
        target_file=str(tmp_path / "target.json"),
        bundle_id="fresh-lab-1",
        approved_by="fixture-reviewer",
        approval_ref="LAB-LOCAL-REVIEW",
        valid_until=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    )
    (tmp_path / "lab-input.json").write_text(json.dumps(cfg))
    policy_folder = tmp_path / "deploy/gpu-lab"
    policy_folder.mkdir(parents=True)
    (policy_folder / "policy.aws-lab.json").write_bytes((ROOT / "deploy/gpu-lab/policy.aws-lab.json").read_bytes())
    monkeypatch.chdir(tmp_path)
    exec(compile(snippet("LAB_BUNDLE_SETUP"), str(DOC), "exec"), {})
    bundle = verify_policy_bundle(tmp_path / "lab-first-bundles/fresh-lab-1")
    assert bundle.target_ref == target["target_id"]
    assert {entry.role for entry in bundle.files} == {
        "gateway_policy",
        "policy_approvals",
        "enforcement_intent",
        "target",
    }
    assert not (tmp_path / "current").exists()
    with pytest.raises(FileExistsError):
        exec(snippet("LAB_BUNDLE_SETUP"), {})
