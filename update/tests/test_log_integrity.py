"""OBS-09 execution contracts with explicitly synthetic transports and logs."""
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from guard.adapters.vllm import VllmAdapter
from guard.executors.log_integrity import LogIntegrityExecutor, _ENV
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Verdict
from tests.builders import environment, evidence
from tests.test_log_integrity_parser import native, PREFIX


ROOT = Path(__file__).resolve().parents[1]
IMAGE = "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
VALUES = {"collector_profile": "guard-vllm-native-info-v1", "required_trusted_fields": ["process", "level", "source"],
          "client_request_id_trust": "untrusted_correlation"}


def check():
    value = yaml.safe_load((ROOT / "packs/PACK-BASELINE/checks/OBS-09.yaml").read_text(encoding="utf-8"))
    value.update(yaml.safe_load((ROOT / "tools/overrides/OBS-09.yaml").read_text(encoding="utf-8")))
    return CheckDef.model_validate(value)


def target():
    contract = VllmAdapter().native_log_contract(IMAGE, "0.31.0")
    return {"lab": True, "run_id": "EXAMPLE-log-run", "observed_version": "0.31.0",
            "engine": {"container": "EXAMPLE-log-engine", "trusted_url": "http://engine:8000", "image_digest": IMAGE},
            "origins": {"trusted": {"network": "EXAMPLE-trusted"}},
            "comparison_config": {"launch": {"argv": ["/models", "--enable-log-requests", "--max-log-len", "512"],
                                                "log_profile": contract}}}


class World:
    def __init__(self, **changes):
        self.changes, self.requests, self.lines = changes, [], []
        self.inspections = 0

    def inspect_json(self, container, *, timeout):
        assert container == "EXAMPLE-log-engine" and 0 < timeout <= 5
        self.inspections += 1
        result = {"Id": "EXAMPLE-owned-id", "Image": IMAGE.split("@")[-1],
                  "State": {"Running": True, "StartedAt": "EXAMPLE-start"},
                  "Config": {"Labels": {"guard.run": "EXAMPLE-log-run"}, "Image": IMAGE,
                             "Cmd": target()["comparison_config"]["launch"]["argv"],
                             "Env": [f"{key}={value}" for key, value in _ENV.items()] + ["API_KEY=EXAMPLE-inspect-secret"]}}
        if self.changes.get("wrong_owner"):
            result["Config"]["Labels"]["guard.run"] = "other-owner"
        if self.changes.get("wrong_env"):
            result["Config"]["Env"] = ["VLLM_LOGGING_LEVEL=DEBUG"]
        if self.changes.get("custom_config"):
            result["Config"]["Env"].append("VLLM_LOGGING_CONFIG_PATH=/private/custom-config.json")
        if self.changes.get("wrong_argv"):
            result["Config"]["Cmd"] = ["unexpected"]
        if self.changes.get("wrong_image_id"):
            result["Image"] = "sha256:" + "f" * 64
        if self.changes.get("restart") and self.inspections > 1:
            result["State"]["StartedAt"] = "EXAMPLE-new-start"
        return result

    def probe(self, network, requests, timeout):
        assert network == "EXAMPLE-trusted" and len(requests) == 1 and 0 < timeout <= 1
        request = deepcopy(requests[0])
        self.requests.append(request)
        assert not any(key.lower() == "x-request-id" for key in request["headers"])
        label, body = request["id"], request["body"]
        rid = body["request_id"]
        assert body["prompt"] == "guard log control" and body["max_tokens"] == 1
        message = "Received request cmpl-" + rid + "-0: params: SamplingParams(max_tokens=1), lora_request: None."
        prefix = PREFIX.replace("INFO", "ERROR") if self.changes.get("forged_metadata") and label == "reserved" else PREFIX
        if not self.changes.get("no_logs") and label not in self.changes.get("missing_log", []):
            raw = native(message, prefix)
            if self.changes.get("strip_cr"):
                raw = raw.replace("\r", "")
            self.lines.append(raw)
            if self.changes.get("duplicate") and label == "reserved":
                self.lines.append(raw)
        if label in self.changes.get("raises", []):
            raise RuntimeError("API_KEY=EXAMPLE-transport-secret")
        response = {"id": "cmpl-" + rid, "model": "fixture-model",
                    "choices": [{"text": "EXAMPLE", "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}
        if self.changes.get("wrong_response_id"):
            response["id"] = "cmpl-unrelated"
        raw = json.dumps(response)
        return [{"id": label, "outcome": 200, "body": raw, "body_bytes": len(raw.encode())}]

    def logs_window(self, container, *, since, until, timeout):
        assert container == "EXAMPLE-log-engine" and since and until and 0 < timeout <= 30
        if self.changes.get("capture_raises"):
            raise RuntimeError("API_KEY=EXAMPLE-capture-secret")
        text = "".join(self.lines)
        # Synthetic native timestamps belong to this synthetic capture window;
        # they are never measurements of the fixed historical engine profile.
        text = re.sub(r"(?m)^2026-10-23T03:00:00\.123456789Z ", since.replace("+00:00", "Z") + " ", text)
        if self.changes.get("replacement"):
            text += "\ufffd\n"
        if self.changes.get("oversize"):
            text += "x" * 131072
        return SimpleNamespace(stdout=text, stderr="EXAMPLE-daemon-diagnostic" if self.changes.get("stderr") else "",
                               returncode=1 if self.changes.get("reader_failure") else 0)


def run(tmp_path, changes=None, values=None, cfg=None, mutate_target=None):
    world = World(**(changes or {}))
    data = target()
    if mutate_target:
        mutate_target(data)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    executor = LogIntegrityExecutor({"target": str(path), "request_timeout": 1, **(cfg or {})},
                                    docker=world, container_probe=world.probe, environ={})
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "fixture-model"})
    draft = executor.run(check(), env, deepcopy(VALUES if values is None else values), VllmAdapter(), tmp_path / "work")
    return draft, world


def judge(draft, values=None):
    definition = check()
    evd = evidence(check_id="OBS-09", normal=draft.normal_control.model_dump(),
                   observations={key: value.model_dump() for key, value in draft.observations.items()})
    return RuleTableJudge().judge(definition, evd, definition.judge.params, VALUES if values is None else values)


def test_actual_reference_consumer_and_display_contracts_with_synthetic_transport(tmp_path):
    draft, world = run(tmp_path)
    assert draft.data_origin == "EXAMPLE" and draft.normal_control.succeeded
    assert all(step.attempted for step in draft.challenges) and len(world.requests) == 7
    assert judge(draft).verdicts == {"security": Verdict.PASS, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert {item.name for item in draft.artifacts} == {"probe_log.jsonl", "log_integrity_samples.json", "log_integrity_events.json", "log_integrity_display.html"}
    assert draft.observations["log_trials_reached"].value == ["before", "multiline", "cr", "crlf", "reserved", "html", "after"]
    files = "\n".join(item.path.read_text(encoding="utf-8") for item in draft.artifacts)
    assert "EXAMPLE-inspect-secret" not in files and "Config.Env" not in files
    display = next(item.path for item in draft.artifacts if item.name.endswith(".html")).read_text(encoding="utf-8")
    assert "<script>guard_probe" not in display and display.count('data-event-index="') == 7


def test_display_digest_identifies_exact_persisted_utf8_bytes_on_every_platform(tmp_path):
    draft, _ = run(tmp_path)
    paths = {item.name: item.path for item in draft.artifacts}
    raw = paths["log_integrity_display.html"].read_bytes()
    samples = json.loads(paths["log_integrity_samples.json"].read_text(encoding="utf-8"))["capture"]
    assert hashlib.sha256(raw).hexdigest() == samples["display_sha256"]
    assert b"\r\n" not in raw
    assert raw.decode("utf-8").encode("utf-8") == raw


@pytest.mark.parametrize("changes", [{"no_logs": True}, {"missing_log": ["crlf"]}, {"strip_cr": True},
    {"replacement": True}, {"oversize": True}, {"stderr": True}, {"reader_failure": True},
    {"capture_raises": True}, {"wrong_response_id": True}, {"restart": True}])
def test_missing_or_ambiguous_source_evidence_is_unknown_not_pass(tmp_path, changes):
    draft, _ = run(tmp_path, changes)
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert draft.observations["log_coverage_gaps"].value


@pytest.mark.parametrize("changes", [{"wrong_owner": True}, {"wrong_env": True}, {"custom_config": True}, {"wrong_argv": True}, {"wrong_image_id": True}])
def test_unverified_logging_configuration_dispatches_no_request(tmp_path, changes):
    draft, world = run(tmp_path, changes)
    assert not world.requests and not draft.normal_control.succeeded
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("changes", [{"duplicate": True}, {"forged_metadata": True}])
def test_confirmed_duplicate_or_changed_native_event_metadata_fails(tmp_path, changes):
    draft, _ = run(tmp_path, changes)
    assert judge(draft).verdicts["security"] == Verdict.FAIL
    assert draft.observations["log_security_violations"].value


def test_independent_duplicate_failure_survives_later_inference_failure(tmp_path):
    draft, _ = run(tmp_path, {"duplicate": True, "raises": ["after"]})
    assert judge(draft).verdicts["security"] == Verdict.FAIL
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN
    files = "".join(item.path.read_text(encoding="utf-8") for item in draft.artifacts)
    assert "EXAMPLE-transport-secret" not in files


@pytest.mark.parametrize("values", [
    {**VALUES, "collector_profile": "enterprise-selected-profile"},
    {**VALUES, "client_request_id_trust": "authenticated_principal"},
    {**VALUES, "required_trusted_fields": ["process", "principal"]},
])
def test_unprovided_enterprise_contract_is_valid_but_unknown(tmp_path, values):
    draft, _ = run(tmp_path, values=values)
    assert judge(draft, values).verdicts["security"] == Verdict.UNKNOWN


def test_policy_requirement_order_and_subset_match_real_available_fields(tmp_path):
    values = {**VALUES, "required_trusted_fields": ["source", "level"]}
    draft, _ = run(tmp_path, values=values)
    assert draft.observations["log_required_trusted_fields"].value == ["source", "level"]
    assert judge(draft, values).verdicts["security"] == Verdict.PASS


def test_dispatch_budget_keeps_all_declared_challenges_and_records_unattempted_groups(tmp_path):
    draft, world = run(tmp_path, cfg={"max_total_requests": 2})
    assert len(world.requests) == 2 and len(draft.challenges) == 3
    assert [step.attempted for step in draft.challenges] == [True, False, False]
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("cfg", [{"request_timeout": 121}, {"request_timeout": float("nan")},
    {"max_total_requests": 13}, {"max_id_bytes": 513}, {"max_events": True},
    {"max_duration_seconds": 301}, {"endpoint": "/arbitrary"}])
def test_invalid_execution_bounds_fail_before_dispatch(tmp_path, cfg):
    with pytest.raises(ValueError):
        run(tmp_path, cfg=cfg)


def test_unsupported_metadata_prevents_engine_dispatch(tmp_path):
    draft, world = run(tmp_path, mutate_target=lambda data: data["comparison_config"]["launch"]["log_profile"].update(level="DEBUG"))
    assert not world.requests and judge(draft).verdicts["security"] == Verdict.UNKNOWN
