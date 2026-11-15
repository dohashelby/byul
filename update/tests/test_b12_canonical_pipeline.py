"""실제 등록 factory와 원 팩으로 실행·저장·판정·보고를 확인한다. 전송은 EXAMPLE이다."""
import json
from pathlib import Path

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors.input_validation import InputValidationExecutor
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import get_pack, put_pack
from tests.builders import completion_response, environment, policy
from tests.test_b12_runner_consumers import make_runner, pinned
from tests.test_gpu_input_producers import SCOPE


@pytest.mark.parametrize("unsupported,consumed,expected", [
    (False, False, "PASS"), (True, False, "UNKNOWN"), (True, True, "FAIL"),
])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_common_input_survives_registered_run_stored_pack_judge_and_report(
        tmp_path, monkeypatch, unsupported, consumed, expected):
    pack_dir = Path(__file__).parents[1] / "packs/PACK-BASELINE"
    pack = load_pack(pack_dir)
    cid = "REQ-08"
    env = environment(model={"id": "test"})
    target = {"lab": False, "engine": {"trusted_url": "http://unused", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    target_file = tmp_path / "target.json"
    target_file.write_bytes(canonical_json(target))
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"),
             "target_sha256": sha256_hex(canonical_json(target))}
    requests = {name: sha256_hex(name.encode()) for name in ("before", "after-0", "reused_input")}
    record, _ = make_runner(scope, requests, rejected=() if consumed else ("reused_input",))
    if unsupported:
        record["producer"]["source_sha256"] = "f" * 64
    cfg = {"target": str(target_file), "reused_token_ids": [11, 12, 13],
           "vocab_size": 100, "vocab_source": "EXAMPLE",
           "runner_input": pinned(tmp_path, record, scope, requests)}
    values = {"reused_token_field": "prompt_token_ids", "reused_token_allowed": False,
              "rejection_codes": ["400"]}

    def probe(network, batch, timeout):
        replies = []
        for request in batch:
            body = request["body"]
            if "prompt_token_ids" in body and not consumed:
                replies.append({"outcome": 400, "body": ""})
            else:
                reply = completion_response(request)
                payload = json.loads(reply["body"])
                payload["prompt_token_ids"] = body.get("prompt_token_ids", [5, 6])
                reply["body"] = json.dumps(payload)
                replies.append(reply)
        return replies

    # 등록 factory·run·collect·_finish는 실제 코드다. 외부 전송만 CPU fixture로 주입한다.
    original_init = InputValidationExecutor.__init__
    def init_with_example_transport(self, config):
        original_init(self, config, container_probe=probe)
    monkeypatch.setattr(InputValidationExecutor, "__init__", init_with_example_transport)
    store = Store(tmp_path / "store")
    pol = policy(pack.ref(), values={cid: values}, scope={"include": [cid], "exclude": []})
    store.put_many([env, pol])
    assert put_pack(store, pack_dir) == pack.ref()
    plan = select(pack, [env], pol)
    store.put(plan)
    assert [item.check_id for item in plan.items if item.selected] == [cid]
    evd, = run_plan(store, pack, plan, RunConfig(executors={"input_validation": "real"},
        adapter="vllm", raw={"input_validation": cfg}))
    assert evd.execution.abort_reason != "EXECUTOR_FAILURE"
    assert evd.data_origin == DataOrigin.EXAMPLE
    artifact, = [a for a in evd.artifacts if a.name == "b_common_consumption.json"]
    raw = (store.root / artifact.path).read_bytes()
    assert sha256_hex(raw) == artifact.sha256
    assert json.loads(raw)["record"] == record
    assert evd.observations["reused_input_trials"].collected
    stored_pack = get_pack(store, plan.inputs.pack)
    result, = judge_plan(store, stored_pack, plan, utcnow())
    assert result.verdicts.security.value == expected
    assert result.data_origin == DataOrigin.EXAMPLE
    report = build_report(store, stored_pack, plan, utcnow())
    store.put(report)
    assert not report.official
    row, = [row for row in report.by_check if row.check_id == cid]
    assert row.single.security.value == expected
    assert store.resolve(evd.ref()).ref() == evd.ref()
    assert store.resolve(result.ref()).ref() == result.ref()
    assert store.resolve(report.ref()).ref() == report.ref()
