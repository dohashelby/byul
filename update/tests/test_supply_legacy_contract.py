"""과거 팩24의 SUP-03 수동 계약은 새 관측 프로파일에 소급하지 않는다."""
from hashlib import sha256
import json

import yaml

from guard.judges.declared import DeclaredJudge
from guard.models import CheckDef, Evidence, Verdict
from tests.builders import ROOT, T0, evidence


def test_pack24_manual_template_supply_contract_retains_its_original_pass():
    raw = (ROOT / "tests/fixtures/sup03-pack24.yaml").read_bytes()
    frozen = json.loads((ROOT / "tests/fixtures/pack24-check-hashes.json").read_text(encoding="utf-8"))
    assert sha256(raw).hexdigest() == frozen["checks"]["checks/SUP-03.yaml"]
    check = CheckDef.model_validate(yaml.safe_load(raw))
    assert check.id == "SUP-03" and check.revision == 2
    assert check.executor is None and check.judge is None and not check.policy_params
    value = evidence().to_dict()
    value.update(check_id=check.id, challenges=[
        {"step": step, "attempted": True} for step in check.challenge_steps])
    value["observations"] = {
        "declared_verdicts": {"collected": True, "value": {
            "security": "PASS", "functional": "UNKNOWN", "performance": "UNKNOWN"}},
        "declared_observation_notes": {"collected": True, "value": {
            item: "EXAMPLE historical operator claim" for item in check.required_observations_prose}},
    }
    value["artifacts"] = [{"name": "EXAMPLE", "path": "EXAMPLE", "sha256": "e" * 64,
                           "bytes": 1, "collected_at": T0, "collector": "EXAMPLE"}]
    stored = Evidence.model_validate(value).seal()
    before = stored.to_dict()
    out = DeclaredJudge().judge(check, stored, {}, {})
    assert out.verdicts["security"] == Verdict.PASS
    assert stored.to_dict() == before
