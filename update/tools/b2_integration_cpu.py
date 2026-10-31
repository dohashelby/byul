"""REQ-13 product-path fixture check on an approved integration snapshot.

Actual reference validation handler + existing executor/EVD/judge/RPT; normal
inference transport is EXAMPLE. No Docker/engine launch or network request.
This validates the fixture path, not registration of a new product executor.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import logging
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

from tools.b2_reference_cpu import reference_scope
from tools.b1_integration_cpu import BASE_SHA as COMMON_BASE_SHA, verify_tool_checkout, verify_snapshot

BASE_SHA = "fc3c4c174ef9dd4b53bc966b75ec4e131bdf1f2a"
REFERENCE_SHA = "db9527a46873454610df6dbedf79a36d6bf1a7f6"
B2_FILE_SHA256 = {
    "update/tools/b1_integration_cpu.py": "858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf",
    "update/src/guard/executors/response_error_contract.py": "2cf7d60d45c19114a17ca37ade0aeb46466e60e246a39076c4e3139ad5ed1a70",
    "update/tools/b2_reference_cpu.py": "45e29826bac697bfe7e357d95fcd7ba871c8a5f150b93fac590ad4ec91de0940",
    "update/tests/builders.py": "bc2274445bc40ea3747a420e0f7ec480270199562466675bdf491d5dab188cd1",
    "update/tests/test_response_content.py": "40450eb1100de2e4170f17f96d7a545db8abc2d36e579ad4df3be6e608b2ca26",
    "update/docs/b2-response/reference-source.json": "4f025a9f416861ebb33d510bf17587cc76ef4f53640ca0fb57bfdb0ccd9c10e4",
}


def verify_consumption_checkout(repo):
    """Consume the approved common/B1 overlay plus fixed B2 source and fixtures."""
    verify_tool_checkout(repo)
    verify_snapshot(repo, BASE_SHA, tuple(B2_FILE_SHA256))
    for relative, expected in B2_FILE_SHA256.items():
        path = repo / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("B2 source/helper bytes mismatch: " + relative)


def verify_reference_source(source):
    if subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() != REFERENCE_SHA:
        raise ValueError("B2 reference HEAD mismatch")
    verify_snapshot(source, REFERENCE_SHA, ("vllm",))


def verify_helper_imports(repo, functions):
    """Use filesystem identity so aliases still identify the same exact file."""
    for function, relative in functions:
        actual = Path(inspect.getfile(function)).resolve()
        expected = repo / relative
        if not actual.is_file() or not expected.is_file() or not actual.samefile(expected):
            raise ValueError("imported product/helper source is outside the pinned checkout")


def run(source, output):
    if sys.flags.optimize:
        raise ValueError("normal Python required for verification assertions")
    import pydantic
    import pytest
    from guard.core.report import build_report
    from guard.core.judge import judge_plan
    from guard.store.packs import get_pack, put_pack
    from guard.executors.response_error_contract import observe_error_response
    from tests.builders import baseline_pack
    from tests.test_response_content import VALUES, response_probe, run_content

    repo = Path(__file__).resolve().parents[2]
    verify_helper_imports(repo, (
        (verify_tool_checkout, "update/tools/b1_integration_cpu.py"),
        (verify_snapshot, "update/tools/b1_integration_cpu.py"),
        (build_report, "update/src/guard/core/report.py"),
        (observe_error_response, "update/src/guard/executors/response_error_contract.py"),
        (baseline_pack, "update/tests/builders.py"),
        (response_probe, "update/tests/test_response_content.py"),
        (run_content, "update/tests/test_response_content.py"),
        (reference_scope, "update/tools/b2_reference_cpu.py"),
    ))
    verify_consumption_checkout(repo)
    verify_reference_source(source)
    output.mkdir(parents=True, exist_ok=False)
    scope, pins = reference_scope(source, logging.getLogger("b2.integration.reference"))
    fields = ["error.message", "error.type", "error.param", "error.code"]
    summaries = []
    for case, violation in (("normal_error_path", False), ("confirmed_field_and_after_failure", True)):
        (output / case).mkdir()
        base = response_probe(extra=violation)
        observed, marks = [], set()

        def probe(network, requests, timeout):
            request = requests[0]
            body = request.get("body", {})
            for message in body.get("messages", []):
                marks.update(re.findall(r"G[0-9a-f]{12}[USE]", message.get("content", "")))
            if violation and request["id"] == "after":
                raise TimeoutError("B2_SYNTHETIC_EXCEPTION_DO_NOT_SAVE")
            if request["id"] != "safe_error":
                return base(network, requests, timeout)
            marker = body["max_tokens"]
            marks.add(marker)
            try:
                pydantic.TypeAdapter(int).validate_python(marker)
            except pydantic.ValidationError as exc:
                error = scope["RequestValidationError"](exc.errors(), body=body)
            req = scope["Request"]({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(
                args=SimpleNamespace(log_error_stack=False)))})
            response = asyncio.run(scope["validation_exception_handler"](req, error))
            result = {"outcome": int(response.status_code), "body": response.body.decode(), "body_bytes": len(response.body)}
            private = body["messages"][0]["content"].split()[0]
            facts = observe_error_response(result, marks={"error_input": marker, "private_system": private},
                allowed_fields=fields, forbidden_contexts=["private_system"])
            assert facts["complete"] and not facts["violations"]
            observed.append(facts)
            return [result]

        with pytest.MonkeyPatch.context() as patch:
            store, evd, res = run_content(output / case, patch, probe,
                                         values={**VALUES, "allowed_error_fields": fields})
        assert len(observed) == 1
        assert evd.data_origin.value == "EXAMPLE"
        assert res.verdicts.security.value == ("FAIL" if violation else "PASS")
        assert evd.normal_control.succeeded is (not violation)
        pack = baseline_pack()
        plan = store.resolve(evd.plan)
        put_pack(store, pack.path)
        replay, = judge_plan(store, get_pack(store, plan.inputs.pack), plan,
                             store.resolve(plan.inputs.environments[0]).created_at)
        assert replay.ref() == res.ref() and replay.evidence == evd.ref()
        rpt = build_report(store, pack, plan, plan.created_at)
        store.put(rpt)
        assert store.verify() == []
        assert not rpt.official
        saved = "\n".join(path.read_text(encoding="utf-8") for path in store.root.rglob("*")
                          if path.is_file() and path.suffix in {".json", ".jsonl", ".txt"})
        # Content-addressed artifact filenames do not have a text suffix.
        saved += "\n" + "\n".join((store.root / item.path).read_text(encoding="utf-8") for item in evd.artifacts)
        assert all(marker not in saved for marker in marks)
        assert "B2_SYNTHETIC_EXCEPTION_DO_NOT_SAVE" not in saved
        summaries.append({"case": case, "data_origin": "EXAMPLE", "evidence": evd.ref().model_dump(mode="json"),
            "result": res.ref().model_dump(mode="json"), "report": rpt.ref().model_dump(mode="json"),
            "security": res.verdicts.security.value, "normal_control": evd.normal_control.succeeded,
            "actual_error_observations": observed, "store_verified": True, "raw_markers_absent": True,
            "exact_ref_rejudge_preserved": True})
    verify_reference_source(source)
    verify_consumption_checkout(repo)
    own_files = [repo / relative for relative in B2_FILE_SHA256] + [Path(__file__).resolve()]
    report = {"schema": "byul-b2-pinned-integration/2", "base_commit": BASE_SHA,
        "common_baseline_commit": COMMON_BASE_SHA, "b2_source_pins_sha256": B2_FILE_SHA256,
        "b2_overlay_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in own_files},
        "reference_commit": pins["commit"], "normal_inference_origin": "EXAMPLE",
        "actual_reference_error_handler_executed": True, "engine_inference_executed": False,
        "common_source_modified": False, "product_registration_changed": False,
        "cases": summaries}
    with (output / "summary.json").open("x") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    print("Approved fixture execution→EVD→RES/RPT passed: EXAMPLE PASS and independent FAIL retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve())
