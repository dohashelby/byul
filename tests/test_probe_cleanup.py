"""Cleanup failure must survive rejudging without destroying confirmed violations."""
import json

import pytest

from guard.executors import http_common, registry
from guard.executors.input_validation import InputValidationExecutor
from guard.core.run import RunConfig, run_plan
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.select import select
from guard.models import ExecStatus
from guard.models.objects import Execution
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, T0


@pytest.mark.parametrize("cleanup_raises", [False, True])
def test_probe_cleanup_preserves_completed_failure(tmp_path, monkeypatch, cleanup_raises):
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab":True, "probe_image":"local", "engine":{"trusted_url":"http://engine:8000"},
                                  "origins":{"trusted":{"network":"sandbox"}}}), encoding="utf-8")
    class Docker:
        def __init__(self, run_id): self.run_id = run_id
        def cleanup(self, **kw):
            if cleanup_raises: raise RuntimeError("secret daemon error")
            return ["secret daemon error"]
    def probe(net, requests, timeout): return [{"outcome":200} for _ in requests]
    monkeypatch.setattr(http_common, "Docker", Docker)
    monkeypatch.setattr(http_common, "docker_container_probe", lambda *a: probe)
    cfg = {"input_validation":{"target":str(target), "vocab_size":100, "vocab_source":"synthetic"}}
    monkeypatch.setitem(registry.EXECUTORS, "input_validation:cleanup", lambda c: InputValidationExecutor(c["input_validation"]))
    pack, env = baseline_pack(), environment(model={"id":"test"})
    pol = policy(pack.ref(), scope={"include":["REQ-09"]},
                 values={"REQ-09":{"max_input_tokens":8,"rejection_codes":["400"],
                    "allowed_endpoints":[pack.checks["REQ-09"].functional_probes[0].id],
                    "allowed_input_formats":["token_ids"]}})
    store = Store(tmp_path / "store")
    for obj in (env, pol): store.put(obj)
    plan = select(pack, [env], pol); store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"input_validation":"cleanup"}, adapter="vllm", raw=cfg))
    assert evd.execution.status == ExecStatus.COMPLETED and evd.execution.cleanup_errors
    assert "secret daemon error" not in json.dumps(evd.to_dict())
    for _ in range(2):
        result, = judge_plan(store, pack, plan, T0)
        assert result.verdicts.security.value == "FAIL"
        assert any("probe cleanup incomplete" in s for s in build_report(store, pack, plan, T0).limitations)


def test_empty_cleanup_field_does_not_change_legacy_serialization():
    original = {"status":"COMPLETED", "executor":None, "adapter":None, "started_at":None,
                "finished_at":None, "abort_reason":None}
    assert Execution.model_validate(original).model_dump(mode="json") == original
