"""REQ-13 product-path fixture check on a pinned A integration checkout.

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
from types import SimpleNamespace

from tools.b2_reference_cpu import reference_scope

A_SHA = "7acd3d02f01cda6910a9e7c062f36611dcac5d37"


def run(source, output):
    import pydantic
    import pytest
    from guard.core.report import build_report
    from guard.executors.response_error_contract import observe_error_response
    from tests.builders import baseline_pack
    from tests.test_response_content import VALUES, response_probe, run_content

    repo = Path(__file__).resolve().parents[2]
    if subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() != A_SHA:
        raise ValueError("run in the dedicated pinned A checkout with B-2-only overlay")
    # HEAD alone cannot prove which checkout PYTHONPATH imported.
    for function in (build_report, run_content, reference_scope, observe_error_response):
        if not Path(inspect.getfile(function)).resolve().is_relative_to(repo / "update"):
            raise ValueError("imported product/helper source is outside the pinned checkout")
    if subprocess.check_output(["git", "-C", str(repo), "diff", "--name-only", "HEAD"], text=True).strip():
        raise ValueError("tracked A source files must remain unchanged")
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
            "actual_error_observations": observed, "store_verified": True, "raw_markers_absent": True})
    own_files = [p for p in repo.rglob("*") if p.is_file() and p.suffix in {".py", ".json", ".md", ".yaml"}
                 and ("b2-response" in str(p) or p.name in {"b2_integration_cpu.py", "b2_reference_cpu.py", "response_error_contract.py"})]
    report = {"schema": "byul-b2-pinned-a-integration/1", "a_commit": A_SHA,
        "b2_overlay_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest() for p in own_files},
        "reference_commit": pins["commit"], "normal_inference_origin": "EXAMPLE",
        "actual_reference_error_handler_executed": True, "engine_inference_executed": False,
        "common_source_modified": False, "product_registration_changed": False,
        "cases": summaries}
    with (output / "summary.json").open("x") as stream:
        json.dump(report, stream, indent=2); stream.write("\n")
    print("Pinned A fixture execution→EVD→RES/RPT passed: EXAMPLE PASS and independent FAIL retained")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.source.resolve(), args.output.resolve())
