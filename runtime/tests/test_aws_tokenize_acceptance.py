"""D3 command generation and fake execution; never contacts a cluster."""
import errno
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

import pytest

from tools.aws_lab_acceptance import (
    AWSAcceptance, CASE_IDS, CASE_TITLES, CASE_RT_IDS, REPORT_CASES, CommandResult,
    TOKENIZE_PROBE_CODE, TCP_PROBE_CODE, _parse_cases, _report_summary, evaluate_case, publish_report,
)
from test_aws_lab_acceptance import FakeHTTP, FakeRunner, arguments, detail, response


@pytest.mark.parametrize("error", sorted({0, errno.EAGAIN, errno.EWOULDBLOCK, errno.ETIMEDOUT, errno.ECONNREFUSED}))
def test_tokenize_probe_renders_http_body_without_real_socket(monkeypatch, capsys, error):
    sent = []

    class FakeSocket:
        def settimeout(self, seconds):
            assert seconds == 3

        def connect_ex(self, address):
            assert address == ("serving-engine", 8000)
            return error

        def sendall(self, value):
            sent.append(value)

        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "socket", SimpleNamespace(socket=FakeSocket))
    monkeypatch.setattr(sys, "argv", ["probe", "serving-engine", "8000"])
    with pytest.raises(SystemExit) as stopped:
        exec(compile(TOKENIZE_PROBE_CODE, "<tokenize-probe>", "exec"), {})
    output = capsys.readouterr().out
    assert "BYUL_SYNTHETIC_" not in output
    if error:
        assert stopped.value.code == 0 and not sent
        assert f"errno={error} name={errno.errorcode[error]}" in output
    else:
        assert stopped.value.code == 1 and output.strip() == "CONNECTED"
        headers, body = sent[0].split(b"\r\n\r\n", 1)
        assert headers.startswith(b"POST /tokenize HTTP/1.1\r\n")
        assert f"Content-Length: {len(body)}".encode() in headers
        payload = json.loads(body)
        assert payload["mm_processor_kwargs"]["code_revision"].startswith("BYUL_SYNTHETIC_")
        assert payload["model"] == "test-model" and payload["max_tokens"] == 8


@pytest.mark.parametrize("fault,expected", [
    (None, "passed"), ("refused", "unknown"), ("connected", "failed"),
    ("exec_error", "unknown"), ("exec_raise", "unknown"), ("missing_python", "unknown"),
    ("hubble_missing", "unknown"), ("collection_gap", "unknown"),
    ("gateway_http", "failed"), ("gateway_tcp", "unknown"),
    ("primary_refused", "unknown"), ("primary_exec", "unknown"),
    ("primary_connected", "failed"), ("primary_connected+refused", "failed"),
    ("primary_connected+exec_raise", "failed"), ("primary_connected+missing_python", "failed"),
    ("connected+primary_refused", "failed"), ("connected+primary_missing_python", "failed"),
    ("connected+gateway_missing_python", "failed"), ("connected+collection_gap", "failed"),
    ("connected_send_error", "failed"),
])
def test_aws_04_runs_tokenize_as_auxiliary_with_shared_flow_controls(tmp_path, fault, expected):
    events = []
    faults = set(fault.split("+")) if fault else set()
    connected = CommandResult(1, "CONNECTED\n")
    timeout = CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n")
    refused = CommandResult(0, "CONNECT_FAILED errno=111 name=ECONNREFUSED\n")
    missing_python = CommandResult(127, stderr="python3: not found")
    primary = (connected if "primary_connected" in faults else
               refused if "primary_refused" in faults else
               missing_python if "primary_missing_python" in faults else
               CommandResult(124) if "primary_exec" in faults else timeout)
    auxiliary = (connected if "connected" in faults else
                 CommandResult(2, "CONNECTED\n", "sendall error") if "connected_send_error" in faults else
                 refused if "refused" in faults else
                 missing_python if "missing_python" in faults else
                 CommandResult(124) if "exec_error" in faults else timeout)

    class Runner(FakeRunner):
        def run(self, argv, **kwargs):
            events.append("tokenize" if TOKENIZE_PROBE_CODE in argv else "tcp")
            if TOKENIZE_PROBE_CODE in argv and "exec_raise" in faults:
                raise OSError("synthetic exec failure")
            return super().run(argv, **kwargs)

    class HTTP(FakeHTTP):
        def request(self, method, url, **kwargs):
            events.append(method)
            return super().request(method, url, **kwargs)

    results = [connected, primary]
    if "exec_raise" not in faults:
        results.append(auxiliary)
    results.append(missing_python if "gateway_missing_python" in faults else
                   timeout if "gateway_tcp" in faults else connected)
    runner = Runner(results)
    http = HTTP([response(500 if "gateway_http" in faults else 200,
                          {"choices": [{"message": {"content": "ok"}}]}, "control"),
                 response(200, detail("ALLOWED", attempted=True))])
    checker = AWSAcceptance(arguments(tmp_path, cases=("AWS-04",)), runner=runner, http=http)
    observation_calls = []

    def observations(kind, since, predicate):
        observation_calls.append((kind, since))
        assert kind == "hubble" and since
        flow = {"src": {"pod": "other-client"}, "dst": {"pod": "serving-engine-abc", "port": 8000}, "verdict": "dropped"}
        assert predicate(flow)
        assert not predicate({**flow, "verdict": "forwarded"})
        assert not predicate({**flow, "dst": {"pod": "serving-engine-abc", "port": 8080}})
        assert not predicate({**flow, "src": {"pod": "another-client"}})
        return "collection_gap" not in faults, [] if faults & {"hubble_missing", "collection_gap"} else ["flow-ref"]

    checker._wait_observation_refs = observations
    checker._run_case("AWS-04", checker.aws_04)
    case = checker.cases[0]
    assert case["result"] == expected
    assert events == ["tcp", "tcp", "tokenize", "tcp", "POST", "GET"]
    assert len(observation_calls) == 1  # Same source/destination/port; share AWS-04's Hubble control.
    assert runner.history[1]["argv"][5:8] == ["exec", "pod/other-client", "-c"]
    assert runner.history[1]["argv"][-3:] == [TCP_PROBE_CODE, "serving-engine", "8000"]
    if "exec_raise" not in faults:
        assert runner.history[2]["argv"][-3:] == [TOKENIZE_PROBE_CODE, "serving-engine", "8000"]
        assert runner.history[2]["argv"][:-3] == runner.history[1]["argv"][:-3]
    assert not runner.results and not http.responses
    assert case["evidence"]["tokenize_direct_probe"]["path"] == "/tokenize"
    assert case["evidence"]["tokenize_direct_probe"]["method"] == "POST"
    assert "tokenize_direct_failed" in case["facts"]
    assert "직통 POST /tokenize(코드 선택 본문) 연결 errno " in _report_summary(case)
    if faults & {"connected", "connected_send_error"}:
        assert case["facts"]["tokenize_direct_failed"] is False
    elif faults & {"refused", "exec_error", "exec_raise", "missing_python"}:
        assert case["facts"]["tokenize_direct_failed"] is None
    else:
        assert case["facts"]["tokenize_direct_failed"] is True
    if fault is None:
        raw, markdown = checker.write_report()
        saved = json.loads(raw.read_text())["cases"][0]
        assert saved["facts"]["tokenize_direct_failed"] is True
        assert saved["evidence"]["tokenize_direct_probe"]["errno"] == errno.ETIMEDOUT
        assert "직통 POST /tokenize(코드 선택 본문) 연결 errno 110 (ETIMEDOUT)" in markdown.read_text()


@pytest.mark.parametrize("fault", ["gateway_exec", "gateway_request", "hubble"])
@pytest.mark.parametrize("primary_connected,tokenize_connected,expected", [
    (False, False, "unknown"), (True, False, "failed"), (False, True, "failed"),
])
def test_aws_04_preserves_direct_success_when_later_control_raises(
    tmp_path, fault, primary_connected, tokenize_connected, expected,
):
    connected = CommandResult(1, "CONNECTED\n")
    timeout = CommandResult(0, "CONNECT_FAILED errno=110 name=ETIMEDOUT\n")

    class Runner(FakeRunner):
        def run(self, argv, **kwargs):
            if fault == "gateway_exec" and len(self.history) == 3:
                raise OSError("synthetic Gateway exec error")
            return super().run(argv, **kwargs)

    class HTTP(FakeHTTP):
        def request(self, method, url, **kwargs):
            if fault == "gateway_request":
                raise OSError("synthetic Gateway request error")
            return super().request(method, url, **kwargs)

    runner = Runner([connected, connected if primary_connected else timeout,
                     connected if tokenize_connected else timeout, connected])
    http = HTTP([response(200, {"choices": [{"message": {"content": "ok"}}]}, "control"),
                 response(200, detail("ALLOWED", attempted=True))])
    checker = AWSAcceptance(arguments(tmp_path, cases=("AWS-04",)), runner=runner, http=http)

    def observations(*args):
        assert fault == "hubble"
        raise OSError("synthetic Hubble collection error")

    checker._wait_observation_refs = observations
    checker._run_case("AWS-04", checker.aws_04)
    case = checker.cases[0]
    assert case["result"] == expected
    assert case["evidence"]["direct_probe"]["marker"] == ("CONNECTED" if primary_connected else "CONNECT_FAILED")
    assert case["facts"]["tokenize_direct_failed"] is (not tokenize_connected)
    assert case["evidence"]["control_error"]


def test_aws_04_keeps_original_required_facts_and_default_selection():
    required = ("direct_failed", "gateway_tcp_control", "gateway_control_200", "hubble_dropped")
    assert REPORT_CASES["AWS-04"].required == required
    assert evaluate_case("AWS-04", dict.fromkeys(required, True))[0] == "passed"
    assert evaluate_case("AWS-04", {})[0] == "unknown"
    assert "AWS-04" in _parse_cases(None)
    assert _parse_cases(["AWS-04"]) == ("AWS-04",)
    assert set(CASE_IDS) == set(CASE_TITLES) == set(CASE_RT_IDS) == set(REPORT_CASES)


def test_all_aws_case_ids_match_platform_contract_pattern():
    from serving_runtime.mvp.lab_report import AWS_CASE_IDS

    root = Path(__file__).resolve().parents[2]
    schema = json.loads((root / "contracts/schemas/runtime-lab-report.schema.json").read_text())
    report_schema = next(item for item in schema["properties"]["report"]["oneOf"] if item["type"] == "object")
    pattern = report_schema["properties"]["cases"]["items"]["properties"]["id"]["pattern"]
    assert CASE_IDS == AWS_CASE_IDS == tuple(f"AWS-{i:02d}" for i in range(1, 21))
    assert all(re.fullmatch(pattern, case_id) for case_id in CASE_IDS)


@pytest.mark.parametrize("marker,number,name,expected", [
    ("CONNECT_FAILED", 110, "ETIMEDOUT", "110 (ETIMEDOUT)"),
    ("CONNECTED", None, None, "0 (CONNECTED)"),
    (None, None, None, "확인 못 함 (확인 못 함)"),
])
def test_aws_report_with_tokenize_evidence_passes_platform_contract(tmp_path, monkeypatch, marker, number, name, expected):
    from serving_runtime.mvp.lab_report import LabReportSource

    pytest.importorskip("jsonschema")
    pytest.importorskip("httpx")
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "platform/api/src"))
    from byul_platform.contracts import Contracts
    from byul_platform.sources.runtime import lab_report_doc

    required = REPORT_CASES["AWS-04"].required
    report = {"run_id": "aws-acceptance-20261111T000000Z-1", "started_at": "2026-11-11T00:00:00Z",
              "ended_at": "2026-11-11T00:01:00Z", "kube_context": "byul-aws-lab", "namespace": "serving-lab", "engine": "vllm",
              "cases": [{"case_id": case_id, "result": "passed", "reason": "synthetic unit test",
                         "facts": dict.fromkeys(required, True)} for case_id in CASE_IDS]}
    case = report["cases"][3]
    case["result"] = "failed" if marker == "CONNECTED" else "passed" if number else "unknown"
    case["evidence"] = {"tokenize_direct_probe": {"marker": marker, "errno": number, "errno_name": name,
                                               "unrelated_text": "private-prompt-and-response-body"}}
    publish_report(report, tmp_path, ())
    result = LabReportSource(tmp_path).read()
    assert result["state"] == "ok"
    assert [case["id"] for case in result["report"]["cases"]] == list(CASE_IDS)
    published = result["report"]["cases"][3]
    assert published["id"] == "AWS-04" and published["status"] == case["result"]
    line = "직통 POST /tokenize(코드 선택 본문) 연결 errno " + expected
    assert line in _report_summary(case)
    assert published["evidence"] == [line]
    assert "private-prompt-and-response-body" not in json.dumps(result)
    assert Contracts(root / "contracts").errors("runtime-lab-report", lab_report_doc(result)) == []
