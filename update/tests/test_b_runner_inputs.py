"""원 producer 함수 → 오프라인 B 준비 → 기존 소비자/factory/Store의 새 경계.

콜백과 HTTP 응답은 CPU fixture이며 실제 요청 대응·엔진·GPU 실증이 아니다.
"""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.collectors.engine_callbacks import prepare_assets
from guard.executors import common_consumption as bridge, registry
from guard.executors.http_common import Ctx
from guard.models import canonical_json, sha256_hex, utcnow
from tests.builders import completion_response, environment
from tests.test_b12_runner_consumers import PACK, judged, make_runner
from tools import b_runner_inputs as tool


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    # 원 준비 함수를 한 번만 재사용한다. 엔진 import/설치/기동은 없다.
    return prepare_assets(tmp_path_factory.mktemp("callback-assets") / "prepared", runner_consumption=True)


def pin(path, raw):
    path.write_bytes(raw)
    return {"path": str(path), "file_sha256": sha256_hex(raw)}


def inputs(tmp_path, prepared, *, check="REQ-08", cases=None, damage=None, consumed=False):
    env = environment(model={"id": "test"})
    target = {"lab": False, "engine": {"trusted_url": "http://unused", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    cases = cases or {name: sha256_hex(name.encode()) for name in ("before", "after-0", "reused_input")}
    scope = {"environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target)),
             "prepared_sha256": sha256_hex(canonical_json(prepared)), "session": prepared["session"]}
    record, original = make_runner(scope, cases, rejected=() if consumed else ("reused_input",))
    if damage == "unsupported":
        record["producer"]["source_sha256"] = "f" * 64
    elif damage == "generation":
        record["bindings"][1]["restart_count"] += 1
    elif damage == "incomplete":
        record["completeness"]["source_ended"] = False
    # A 함수가 만든 record를 그대로 렌더링한다. 앞쪽 다중바이트·괄호 문자열도 span 계산에 포함한다.
    record_raw = json.dumps(record, ensure_ascii=False, indent=3).encode()
    prefix = json.dumps({"schema": tool.WRAPPER_SCHEMA, "session": prepared["session"], "truncated": False,
                         "processes": [], "summaries": ["경계 {\"record\": false}"]}, ensure_ascii=False)[:-1].encode()
    wrapper_raw = prefix + b', "records": {"runner-consumption": {"record": ' + record_raw + b', "validation": {"complete": true}}}}'
    values = {"environment": canonical_json(env.to_dict()), "target": canonical_json(target),
              "prepared": canonical_json(prepared), "requests": canonical_json(cases), "wrapper": wrapper_raw}
    spec = {"schema": tool.INPUT_SCHEMA, "check_id": check, "session": prepared["session"],
            "required_cases": list(cases), "data_origin": "EXAMPLE",
            "sources": {name: pin(tmp_path / (name + "-source.json"), raw) for name, raw in values.items()}}
    path = tmp_path / "prepare-inputs.json"
    path.write_bytes(canonical_json(spec))
    return {"path": path, "spec": spec, "env": env, "target": target, "record": record,
            "record_raw": record_raw, "wrapper_raw": wrapper_raw, "scope": scope, "original": original}


def run(case, output):
    case["path"].write_bytes(canonical_json(case["spec"]))
    return tool.prepare(case["path"], sha256_hex(case["path"].read_bytes()), output)


@pytest.mark.parametrize("check", tool.CHECKS)
def test_producer_output_exact_bytes_independent_scope_and_existing_consumer(tmp_path, prepared, check):
    case = inputs(tmp_path, prepared, check=check)
    output = tmp_path / "fixed"
    receipt = run(case, output)
    assert case["original"]["supported"]
    assert receipt["consumer_projection"]["complete"]
    raw = (output / "runner-record.json").read_bytes()
    assert raw == case["record_raw"]
    span = receipt["record_byte_span"]
    assert case["wrapper_raw"][span["start"]:span["end"]] == raw
    assert raw != canonical_json(case["record"])
    assert receipt["scope"] == case["scope"]
    cfg = json.loads((output / "runner-input.json").read_bytes())["runner_input"]
    record, scope = bridge.load_input(cfg, environment_ref=case["env"].ref().model_dump(mode="json"),
        target_sha256=sha256_hex(canonical_json(case["target"])), prepared_sha256=case["scope"]["prepared_sha256"])
    projection = bridge.runner(record, scope, check_id=check, requests=cfg["requests"], required_cases=case["spec"]["required_cases"])
    assert projection == receipt["consumer_projection"]
    for name, value in receipt["files"].items():
        copied = (output / name).read_bytes()
        assert len(copied) == value["bytes"] and sha256_hex(copied) == value["file_sha256"]
    assert not receipt["native_request_mapping_verified"] and not receipt["runtime_link_complete"] and not receipt["gpu_ready"]
    assert not projection["producer_authenticated"] and not projection["token_identity_verified"]


@pytest.mark.parametrize("damage,gap", [("unsupported", "GPU_INPUT_PRODUCER_UNSUPPORTED"),
    ("generation", "B_RUNNER_PROCESS_GENERATION_MISMATCH"), ("incomplete", "GPU_INPUT_SOURCE_INCOMPLETE")])
def test_cached_positive_summary_cannot_hide_original_gaps(tmp_path, prepared, damage, gap):
    case = inputs(tmp_path, prepared, damage=damage)
    receipt = run(case, tmp_path / "fixed")
    assert not receipt["consumer_projection"]["complete"]
    assert gap in receipt["consumer_projection"]["gaps"]
    assert (tmp_path / "fixed/runner-record.json").read_bytes() == case["record_raw"]


@pytest.mark.parametrize("field", ["environment", "target", "prepared", "session"])
def test_scope_is_computed_from_independent_files_and_session(tmp_path, prepared, field):
    case = inputs(tmp_path, prepared)
    if field == "session":
        case["spec"]["session"] = "0" * 32
    else:
        path = Path(case["spec"]["sources"][field]["path"])
        value = json.loads(path.read_bytes())
        if field == "environment":
            value = environment(model={"id": "a-different-model"}).to_dict()
        elif field == "target":
            value["engine"]["container"] = "a-different-container"
        else:
            value["new_independent_field"] = "a-different-preparation"
        case["spec"]["sources"][field] = pin(path, canonical_json(value))
    with pytest.raises(ValueError, match="SCOPE|SESSION"):
        run(case, tmp_path / "fixed")
    assert not (tmp_path / "fixed").exists()


@pytest.mark.parametrize("name", tool.SOURCE_NAMES)
def test_source_file_sha_is_checked_before_publish(tmp_path, prepared, name):
    case = inputs(tmp_path, prepared)
    Path(case["spec"]["sources"][name]["path"]).write_bytes(b"{}")
    with pytest.raises(ValueError, match="BYTES_MISMATCH"):
        run(case, tmp_path / "fixed")
    assert not (tmp_path / "fixed").exists()


@pytest.mark.parametrize("mode", ["missing-case", "unobserved-sha", "duplicate-sha", "swapped-existing"])
def test_mapping_coverage_is_checked_but_semantic_identity_is_not_invented(tmp_path, prepared, mode):
    case = inputs(tmp_path, prepared)
    requests = deepcopy(case["spec"]["required_cases"])
    path = Path(case["spec"]["sources"]["requests"]["path"])
    mapping = json.loads(path.read_bytes())
    if mode == "missing-case":
        del mapping[requests[0]]
    elif mode == "unobserved-sha":
        mapping[requests[0]] = "f" * 64
    elif mode == "duplicate-sha":
        mapping[requests[0]] = mapping[requests[1]]
    else:
        # 두 유효 SHA의 의미상 교환은 record만으로 검출할 수 없다. native 요청 결합을 주장하지 않는다.
        mapping[requests[0]], mapping[requests[-1]] = mapping[requests[-1]], mapping[requests[0]]
    case["spec"]["sources"]["requests"] = pin(path, canonical_json(mapping))
    if mode == "duplicate-sha":
        with pytest.raises(ValueError, match="REQUEST_BINDING_INVALID"):
            run(case, tmp_path / "fixed")
        return
    receipt = run(case, tmp_path / "fixed")
    if mode in {"missing-case", "unobserved-sha"}:
        assert not receipt["consumer_projection"]["complete"]
        assert receipt["consumer_projection"]["gaps"]
    else:
        assert receipt["consumer_projection"]["complete"]  # 형식 완전성은 요청의 의미상 동일성 증명이 아니다.
    assert not receipt["native_request_mapping_verified"] and not receipt["runtime_link_complete"]


@pytest.mark.parametrize("mode", ["duplicate-json-key", "contract-error", "wrapper-session", "truncated-conflict"])
def test_wrapper_rejections_preserve_diagnostics_without_publishing_a_config(tmp_path, prepared, mode):
    case = inputs(tmp_path, prepared)
    path = Path(case["spec"]["sources"]["wrapper"]["path"])
    raw = case["wrapper_raw"]
    if mode == "duplicate-json-key":
        raw = b'{"schema":"duplicate",' + raw[1:]
    else:
        value = json.loads(raw)
        if mode == "contract-error":
            value["records"][tool.KIND] = {"contract_error": "original-producer-error"}
        elif mode == "wrapper-session":
            value["session"] = "0" * 32
        else:
            value["truncated"] = True
        raw = canonical_json(value)
    case["spec"]["sources"]["wrapper"] = pin(path, raw)
    expected = "DUPLICATE_KEY" if mode == "duplicate-json-key" else "original-producer-error" if mode == "contract-error" else "SESSION|COMPLETENESS"
    with pytest.raises(ValueError, match=expected):
        run(case, tmp_path / "fixed")
    assert not (tmp_path / "fixed").exists()


def test_fixed_output_is_not_overwritten_and_extracted_bytes_are_rechecked_by_consumer(tmp_path, prepared):
    case = inputs(tmp_path, prepared)
    output = tmp_path / "fixed"
    receipt = run(case, output)
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(ValueError, match="NEW_B_RUNNER_OUTPUT"):
        run(case, output)
    assert before == {p.name: p.read_bytes() for p in output.iterdir()}
    (output / "runner-record.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="BYTES_MISMATCH"):
        bridge.load_input(receipt["runner_input"], environment_ref=case["scope"]["environment_ref"],
                          target_sha256=case["scope"]["target_sha256"])


@pytest.mark.parametrize("damage,consumed,expected", [(None, False, "PASS"), ("unsupported", False, "UNKNOWN"),
                                                    ("unsupported", True, "FAIL")])
def test_prepared_config_enters_registered_factory_and_stored_evidence(tmp_path, prepared, damage, consumed, expected):
    case = inputs(tmp_path, prepared, damage=damage, consumed=consumed)
    receipt = run(case, tmp_path / "fixed")
    cfg = {"reused_token_ids": [11, 12, 13], "vocab_size": 100, "vocab_source": "EXAMPLE",
           "runner_input": receipt["runner_input"]}
    values = {"reused_token_field": "prompt_token_ids", "reused_token_allowed": False, "rejection_codes": ["400"]}

    def probe(network, batch, timeout):
        result = []
        for request in batch:
            body = request["body"]
            if "prompt_token_ids" in body and not consumed:
                result.append({"outcome": 400, "body": ""})
            else:
                reply = completion_response(request)
                payload = json.loads(reply["body"])
                payload["prompt_token_ids"] = body.get("prompt_token_ids", [5, 6])
                reply["body"] = json.dumps(payload)
                result.append(reply)
        return result

    executor = registry.resolve_executor("input_validation", "real")({"input_validation": cfg})
    executor.validate_policy(PACK.checks["REQ-08"], values)
    ctx = Ctx(check=PACK.checks["REQ-08"], env=case["env"], adapter=VllmAdapter(), target=case["target"], policy_values=values,
        timeout=1, allow_mutating=False, keys={}, secrets=[], cprobe=probe, workdir=tmp_path / "work", started=utcnow())
    draft = executor._finish(ctx)
    evd, result = judged(tmp_path, "REQ-08", draft, case["env"], values)
    assert result.verdicts.security.value == expected
    assert evd.data_origin.value == result.data_origin.value == "EXAMPLE"
    artifact = next(a for a in evd.artifacts if a.name == "b_common_consumption.json")
    assert artifact.sha256 and (tmp_path / "stored" / artifact.path).is_file()
    if damage:
        assert draft.status.value == "ABORTED"
    if consumed:
        assert evd.observations["reused_input_violations"].value
    assert not receipt["runtime_link_complete"]


def test_cli_error_keeps_original_error_on_stderr_and_does_not_claim_ready(tmp_path, prepared, capsys):
    case = inputs(tmp_path, prepared)
    code = tool.main(["--inputs", str(case["path"]), "--inputs-sha256", "f" * 64, "--output", str(tmp_path / "fixed")])
    captured = capsys.readouterr()
    error = json.loads(captured.err)
    assert code == 2 and captured.out == ""
    assert error["error"] == {"type": "ValueError", "message": "B_RUNNER_SOURCE_BYTES_MISMATCH"}
    assert error["gpu_ready"] is False and not (tmp_path / "fixed").exists()


@pytest.mark.parametrize("mode", ["unsealed-env", "measured-origin", "disabled-prepared"])
def test_preparation_does_not_invent_a_seal_origin_or_enabled_sensor(tmp_path, prepared, mode):
    case = inputs(tmp_path, prepared)
    if mode == "measured-origin":
        case["spec"]["data_origin"] = "MEASURED"
    else:
        name = "environment" if mode == "unsealed-env" else "prepared"
        path = Path(case["spec"]["sources"][name]["path"])
        value = json.loads(path.read_bytes())
        if mode == "unsealed-env":
            value["model"]["id"] = "unsealed-change"
        else:
            value["runner_consumption"] = False
        case["spec"]["sources"][name] = pin(path, canonical_json(value))
    with pytest.raises(ValueError):
        run(case, tmp_path / "fixed")
    assert not (tmp_path / "fixed").exists()
