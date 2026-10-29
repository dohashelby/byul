"""Closed Online /1 snapshot validation. No raw strings, tokens or verdicts."""
from __future__ import annotations

import builtins
import re
from . import vllm_online_sensor as online

FIELDS = frozenset({"chat_template", "chat_template_kwargs", "mm_processor_kwargs", "media_io_kwargs", "echo", "kv_transfer_params"})
EXCEPTIONS = {name for name, value in vars(builtins).items()
    if isinstance(value, type) and issubclass(value, Exception)} | {"VLLMValidationError", "WindowsError"}


def sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def integer(value, maximum=2**63-1):
    return type(value) is int and 0 <= value <= maximum


def nullable_sha(value):return value is None or sha(value)
def nullable_bool(value):return value is None or type(value) is bool
def nullable_int(value):return value is None or integer(value)


def require(value):
    if not value:raise ValueError("ONLINE_NATIVE_METADATA_INVALID")


def ids(value):
    require(type(value) is dict)
    if value == {"status": "unavailable"}:return
    require(set(value) == {"status", "count", "canonical_sha256", "integer_ids", "min", "max"}
        and value["status"] == "collected" and integer(value["count"], 262144)
        and sha(value["canonical_sha256"]) and type(value["integer_ids"]) is bool)
    if not value["integer_ids"] or value["count"] == 0:
        require(value["min"] is None and value["max"] is None)
    else:
        require(all(type(value[k]) is int and value[k].bit_length() <= 256 for k in ("min", "max"))
            and value["min"] <= value["max"])


def domain(value):
    require(type(value) is dict)
    if value == {"status": "unavailable"}:return
    require(set(value) == {"status", "model_vocab_size", "tokenizer_id_count", "min", "max", "canonical_sha256",
        "contiguous_from_zero", "request_ids_in_domain", "request_ids_within_model_vocab", "request_ids_canonical_sha256"})
    require(value["status"] == "collected" and integer(value["model_vocab_size"]) and value["model_vocab_size"] > 0
        and integer(value["tokenizer_id_count"], 262144) and value["tokenizer_id_count"] > 0
        and integer(value["min"]) and integer(value["max"]) and value["min"] <= value["max"]
        and sha(value["canonical_sha256"]) and type(value["contiguous_from_zero"]) is bool
        and nullable_bool(value["request_ids_in_domain"]) and nullable_bool(value["request_ids_within_model_vocab"])
        and nullable_sha(value["request_ids_canonical_sha256"]))
    if value["contiguous_from_zero"]:
        require(value["min"] == 0 and value["max"] + 1 == value["tokenizer_id_count"])


def fields(value, point):
    expected = ({"chat_template", "chat_template_kwargs"} if point == "OnlineRenderer.validate_chat_template" else
        {"mm_processor_kwargs", "media_io_kwargs"} if point == "protocol.validate_request_mm_kwargs" else FIELDS)
    require(type(value) is dict and set(value) <= expected)
    for row in value.values():
        require(type(row) is dict and {"present", "explicitly_set", "kind", "status"} <= set(row)
            and set(row) <= {"present", "explicitly_set", "kind", "status", "canonical_sha256"}
            and type(row["present"]) is bool and nullable_bool(row["explicitly_set"])
            and row["kind"] in {"null", "bool", "int", "float", "str", "list", "dict", "unsupported"}
            and row["status"] in {"collected", "unavailable"})
        if row["status"] == "collected":require(row["present"] and sha(row.get("canonical_sha256")))
        else:require("canonical_sha256" not in row)


def _validate_snapshot(value, point, phase, context=None, session=None):
    require(type(value) is dict and {"schema", "point", "phase", "status", "metadata", "binding"} <= set(value)
        and set(value) <= {"schema", "point", "phase", "status", "metadata", "binding", "diagnostic"}
        and value["schema"] == "byul-online-input-snapshot/1" and point in online.POINT_CONTRACTS
        and value["point"] == point and value["phase"] == phase and phase in {"CALL", "RETURN", "RAISE"}
        and value["status"] in {"collected", "partial", "unavailable"}
        and ("diagnostic" not in value or value["diagnostic"] == "SNAPSHOT_UNAVAILABLE"))
    binding = value["binding"]
    hashes = {"asgi_request_id_sha256", "call_id_sha256", "session_id_sha256", "process_start_sha256"}
    require(type(binding) is dict and hashes | {"pid"} <= set(binding)
        and set(binding) <= hashes | {"pid", "native_request_id_sha256"}
        and all(sha(binding[k]) for k in hashes) and integer(binding["pid"]) and binding["pid"] > 0
        and ("native_request_id_sha256" not in binding or sha(binding["native_request_id_sha256"])))
    if context is not None:
        require(binding["pid"] == context["pid"] and binding["process_start_sha256"] == online.digest(context["start_ticks"])
            and binding["session_id_sha256"] == online.digest(session))
    metadata = value["metadata"]
    allowed = {"effective_config", "reported_cache_max_token_id", "trust_request_chat_template", "trust_request_mm_kwargs",
        "parsed_fields", "processor_input", "admitted", "reused", "rendered_inputs", "token_id_domain",
        "admitted_token_id_domain", "native_request_id_sha256", "native_request_binding_matches", "return_kind",
        "native_error_code", "exception_type", "candidate_prompt_count", "prompt_type"}
    require(type(metadata) is dict and set(metadata) <= allowed and (value["status"] != "collected" or bool(metadata)))
    common = {"effective_config", "reported_cache_max_token_id", "trust_request_chat_template",
        "trust_request_mm_kwargs", "token_id_domain"}
    specific = {
        "OnlineRenderer.render_chat": {"parsed_fields", "rendered_inputs"},
        "OnlineRenderer.preprocess_chat": {"parsed_fields", "rendered_inputs"},
        "OnlineRenderer.validate_chat_template": {"parsed_fields"},
        "online_renderer._reused_prompt_token_ids": {"parsed_fields", "reused"},
        "protocol.validate_request_mm_kwargs": {"parsed_fields"},
        "InputProcessor.process_inputs": {"processor_input", "native_request_id_sha256", "native_request_binding_matches", "admitted", "admitted_token_id_domain"},
        "InputProcessor._validate_model_input": {"processor_input", "prompt_type"},
        "InputProcessor._validate_prompt_len": {"candidate_prompt_count", "prompt_type"},
    }
    require(set(metadata) <= common | specific[point] | {"return_kind", "native_error_code", "exception_type"})
    if phase != "RETURN":require(not set(metadata) & {"rendered_inputs", "reused", "admitted", "admitted_token_id_domain", "return_kind", "native_error_code"})
    if phase != "RAISE":require("exception_type" not in metadata)
    for key, item in metadata.items():
        if key == "effective_config":
            require(type(item) is dict and set(item) == {"max_model_len", "model_vocab_size", "runner_type"}
                and nullable_int(item["max_model_len"]) and nullable_int(item["model_vocab_size"])
                and item["runner_type"] in {"generate", "pooling", "draft", "unavailable"})
        elif key == "parsed_fields":fields(item, point)
        elif key in {"processor_input", "admitted", "reused"}:ids(item)
        elif key == "rendered_inputs":
            require(type(item) is list and len(item) <= 8)
            for row in item:ids(row)
        elif key in {"token_id_domain", "admitted_token_id_domain"}:domain(item)
        elif key in {"trust_request_chat_template", "trust_request_mm_kwargs", "native_request_binding_matches"}:require(nullable_bool(item))
        elif key in {"reported_cache_max_token_id", "candidate_prompt_count"}:require(nullable_int(item))
        elif key == "native_request_id_sha256":require(nullable_sha(item))
        elif key == "return_kind":require(item in {"none", "other", "error_response", "rendered_inputs", "ids", "admitted"})
        elif key == "native_error_code":require(integer(item))
        elif key == "exception_type":require(type(item) is str and item in EXCEPTIONS)
        elif key == "prompt_type":require(item in {"encoder", "decoder", "unavailable"})
    if "native_request_id_sha256" in metadata and metadata.get("native_request_binding_matches") is True:
        require(metadata["native_request_id_sha256"] == binding.get("native_request_id_sha256") and sha(metadata["native_request_id_sha256"]))
    return value


def validate_snapshot(value, point, phase, context=None, session=None):
    try:return _validate_snapshot(value, point, phase, context, session)
    except (KeyError, TypeError, ValueError, OverflowError):
        raise ValueError("ONLINE_NATIVE_METADATA_INVALID") from None
