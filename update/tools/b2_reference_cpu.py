"""Opt-in, pinned handler/logger CPU execution; no server, model or network.

AST-selected definitions execute verbatim. Heavy engine imports are omitted;
FastAPI exceptions, Pydantic models and Starlette JSONResponse are real. The
request body/schema and logging configuration are synthetic dependency inputs.
Only hashes, marker categories and branch metadata leave memory.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
from http import HTTPStatus
import importlib.metadata
import io
import json
import logging
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import ClassVar

from guard.executors.response_error_contract import observe_error_log, observe_error_response
from guard.executors.log_integrity_parser import parse_native_info

PINS = Path(__file__).resolve().parents[1] / "docs/b2-response/reference-source.json"
MARKS = {"caller_input": "B2_SYNTHETIC_CALLER", "error_input": "B2_SYNTHETIC_INVALID",
         "private_system": "B2_SYNTHETIC_PRIVATE", "internal_path": "/srv/B2_SYNTHETIC/internal.py",
         "internal_address": " at 0x1234ABCD>"}
FIELDS = ["error.message", "error.type", "error.param", "error.code"]


def verify_source(root):
    pins = json.loads(PINS.read_text())
    head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    if head != pins["commit"]:
        raise ValueError("reference HEAD differs from the reviewed commit")
    for relative, expected in pins["files"].items():
        if hashlib.sha256((root / relative).read_bytes()).hexdigest() != expected:
            raise ValueError("reference file bytes differ from the reviewed manifest")
    return pins


def selected(root, relative, names, scope):
    """Compile original nodes with original filenames/line numbers, no rewrites."""
    path = root / relative
    tree = ast.parse(path.read_bytes(), filename=str(path))
    found = []
    for node in tree.body:
        declared = {node.name} if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) else set()
        if isinstance(node, ast.Assign):
            declared = {target.id for target in node.targets if isinstance(target, ast.Name)}
        if declared & set(names):
            found.append(node)
    actual = set()
    for node in found:
        actual.update({node.name} if hasattr(node, "name") else
                      {target.id for target in node.targets if isinstance(target, ast.Name)})
    if actual != set(names):
        raise ValueError("reviewed definition inventory differs")
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    tree.body = [future, *found]
    ast.fix_missing_locations(tree)
    scope["__file__"] = str(path)
    exec(compile(tree, str(path), "exec"), scope)


async def run(root):
    import regex
    import pydantic
    from fastapi import HTTPException, Request
    from fastapi.exceptions import RequestValidationError
    from starlette.responses import JSONResponse

    pins = verify_source(root)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    logger = logging.getLogger("b2.reference")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    logger.propagate = False
    scope = {"__name__": __name__, "re": regex, "HTTPStatus": HTTPStatus, "logger": logger, "Request": Request,
             "RequestValidationError": RequestValidationError, "JSONResponse": JSONResponse,
             "HTTPException": HTTPException, "BaseModel": pydantic.BaseModel,
             "ConfigDict": pydantic.ConfigDict, "model_validator": pydantic.model_validator, "ClassVar": ClassVar}
    # exceptions.py has only standard-library imports and class definitions.
    path = root / "vllm/exceptions.py"
    exec(compile(path.read_bytes(), str(path), "exec"), scope)
    selected(root, "vllm/entrypoints/serve/engine/protocol.py",
             ["OpenAIBaseModel", "ErrorInfo", "ErrorResponse", "_REMOVED_GUIDED_FIELDS"], scope)
    # Selected definitions share an explicit namespace rather than importing
    # vLLM's heavyweight package. Resolve its real model forward references.
    scope["ErrorResponse"].model_rebuild(_types_namespace=scope)
    selected(root, "vllm/entrypoints/serve/exception_handling/utils.py", ["sanitize_message"], scope)
    path = "vllm/entrypoints/serve/exception_handling/handlers/validation.py"
    selected(root, path, ["_BRACKETED_INTERNAL_RE", "_INTERNAL_LOC_MARKERS", "_MAX_REPORTED_ERRORS",
             "_MAX_ERROR_INPUT_CHARS", "_MAX_ERROR_CHARS", "_summarize_error_input", "_format_error",
             "_is_internal_loc_segment", "clean_loc_for_param", "validation_exception_handler"], scope)
    selected(root, "vllm/entrypoints/serve/exception_handling/handlers/http.py", ["http_exception_handler"], scope)
    responses = []
    for stack in (False, True):
        req = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(
            args=SimpleNamespace(log_error_stack=stack)))})
        for case in ("bad_max_tokens", "http_internal_path"):
            output.seek(0); output.truncate(0)
            if case == "bad_max_tokens":
                try:
                    pydantic.TypeAdapter(int).validate_python(MARKS["error_input"])
                except pydantic.ValidationError as exc:
                    error = RequestValidationError(exc.errors(), body={"max_tokens": MARKS["error_input"],
                        "messages": [{"role": "system", "content": MARKS["private_system"]}]})
                method = scope["validation_exception_handler"]
            else:
                error = HTTPException(400, detail="failure " + MARKS["internal_path"] + MARKS["internal_address"])
                method = scope["http_exception_handler"]
            # Run inside the active exception context, as the framework does.
            try:
                raise error
            except (RequestValidationError, HTTPException) as caught:
                response = await method(req, caught)
            # Starlette retains an HTTPStatus IntEnum here; the HTTP transport
            # exposes its numeric code to the existing collector contract.
            result = {"outcome": int(response.status_code), "body": response.body.decode(), "body_bytes": len(response.body)}
            responses.append({"case": case, "log_error_stack": stack,
                "response": observe_error_response(result, marks=MARKS, allowed_fields=FIELDS,
                    forbidden_contexts=["private_system", "internal_path", "internal_address"]),
                "logger": observe_error_log(output.getvalue(), marks=MARKS,
                    forbidden_contexts=None, source_complete=True)})
    # Actual request logger methods + formatter; init_logger and engine objects
    # are outside this source-slice experiment. No torch import or inference.
    selected(root, "vllm/entrypoints/serve/utils/request_logger.py", ["RequestLogger"], scope)
    scope.update(logging=logging, Path=Path)
    selected(root, "vllm/logging_utils/formatter.py", ["NewLineFormatter"], scope)
    formatter = scope["NewLineFormatter"]("(CPU pid=1) %(levelname)s %(asctime)s [%(fileinfo)s:%(lineno)d] %(message)s",
                                           datefmt="%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    request_logger = scope["RequestLogger"].__new__(scope["RequestLogger"])
    request_logger.max_log_len = 512
    logs = []
    for level in (logging.INFO, logging.DEBUG):
        logger.setLevel(level)
        output.seek(0); output.truncate(0)
        request_logger.log_inputs("B2-SYNTHETIC", MARKS["private_system"], [1], None, None, None)
        logs.append({"level": logging.getLevelName(level), "capture": observe_error_log(output.getvalue(), marks=MARKS,
                     forbidden_contexts=None, source_complete=True)})
    logger.setLevel(logging.INFO)
    integrity = []
    for case, identifier in (("normal", "B2-normal"), ("multiline", "B2-first\nFORGED-SOURCE"),
                             ("carriage_return", "B2-first\rFORGED-SOURCE")):
        output.seek(0); output.truncate(0)
        request_logger.log_inputs(identifier, None, None, None, None, None)
        # Docker timestamp framing is synthetic, not a Docker capture claim.
        framed = "".join("2026-10-28T00:00:00Z " + line + "\n" for line in output.getvalue().split("\n")[:-1])
        parsed = parse_native_info(framed, markers=(identifier,))
        integrity.append({"case": case, "event_count": len(parsed["events"]), "complete": parsed["complete"],
                          "coverage_gaps": parsed["gaps"], "capture_sha256": parsed["decoded_capture_sha256"],
                          "sources": sorted({event["source"] for event in parsed["events"]})})
    return {"schema": "byul-b2-reference-cpu/1", "source": pins,
            "scope": "verbatim selected handler/model/logger/formatter definitions; synthetic dependency inputs",
            "engine_inference_executed": False, "enterprise_policy_status": "UNSET",
            "response_marker_rule": "EXAMPLE fixed synthetic system/path/address exclusion; not enterprise approval",
            "dependencies": {name: importlib.metadata.version(name) for name in ("pydantic", "fastapi", "starlette", "regex")},
            "python": sys.version.split()[0], "response_trials": responses,
            "request_logger_trials": logs, "reference_collector_trials": integrity}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(run(args.source.resolve()))
    # Exclusive creation keeps prior evidence immutable.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print("Pinned reference handler/logger CPU observations saved; no engine verdict.")


if __name__ == "__main__":
    main()
