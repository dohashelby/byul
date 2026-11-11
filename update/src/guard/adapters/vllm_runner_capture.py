"""Reviewed vLLM request/callback capture data and response-ID interpretation.

Release additions use data rows; the executor never selects a release or an ID format.
"""
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from . import vllm_engine_callback_sensor as sensor
from .vllm import REQUEST_BINDING_GPU_IMAGE

SOURCE_COMMIT = "db9527a46873454610df6dbedf79a36d6bf1a7f6"
SOURCES = {
    "entrypoints/serve/engine/serving.py": "e5239ca4e2119fd019ee0ffcbf6e68f642b7ce94bad1b3fabe22ccdd8cf9eb02",
    "entrypoints/openai/completion/serving.py": "aac889978efb034b95ab81ec59695d83ba7c879ab48cd5be2448c980f109a3bd",
    "v1/engine/input_processor.py": "d79601e4a053a7bacb0d6a23e273348435e4ab70c6a315dcccb2d428ed57d96b",
    "v1/engine/async_llm.py": "ff933ed78aa3195782cf1c6d18c53897adfa5bf920421bd2bcc330f2adc84d1e",
    "entrypoints/openai/completion/protocol.py": "4616cd6887ef0a23fd8a77468e82f2668e3d0b53075112f2d97f3de960c22c81",
    "entrypoints/openai/chat_completion/serving.py": "532dadea845c77a5a79011027b9989987c3e32467614e993f439f29c924de002",
    "entrypoints/openai/chat_completion/protocol.py": "4bdc71f668e4820d8ae5bae2a153fed0296b430cd6353ace8c0c27cfb982ebc1",
}


@dataclass(frozen=True)
class CaptureProfile:
    source_commit: str
    sources: Mapping[str, str]
    image: str
    callback_root: str
    source_root_key: str
    required_environment: Mapping[str, str]
    response_rules: Mapping[str, tuple[str, str, str]]
    response_id_pattern: str

    def fingerprinter(self, secret):
        # Reuse the official fingerprint without initializing a Linux observer.
        observer = object.__new__(sensor.ConsumptionObserver)
        observer.secret = secret
        return observer

    def request_id(self, check_id, response_id):
        _, prefix, suffix = self.response_rules[check_id]
        if (type(response_id) is str and response_id.startswith(prefix)
                and re.fullmatch(self.response_id_pattern, response_id)):
            return response_id + suffix
        return None

    def validate_request(self, request, check_id):
        body = request.get("body")
        url = urlsplit(request.get("url", ""))
        expected_path = self.response_rules[check_id][0]
        if (request.get("method") != "POST" or url.path != expected_path or url.query or url.fragment
                or request.get("headers", {}) != {} or type(body) is not dict
                or body.get("n", 1) != 1 or type(body.get("n", 1)) is not int
                or body.get("stream", False) is not False or body.get("use_beam_search", False) is not False
                or type(body.get("max_tokens")) is not int or not 1 <= body["max_tokens"] <= 8
                or "request_id" in body):
            raise ValueError("B_REQ_NATIVE_REQUEST_OUTSIDE_SELECTED_SCOPE")
        if check_id == "REQ-08":
            messages = body.get("messages")
            if (type(messages) is not list or len(messages) != 1 or type(messages[0]) is not dict
                    or set(messages[0]) != {"role", "content"} or messages[0]["role"] != "user"
                    or type(messages[0]["content"]) is not str or not 0 < len(messages[0]["content"].encode("utf-8")) <= 512):
                raise ValueError("B_REQ_SINGLE_SMALL_PROMPT_REQUIRED")
            return
        prompt = body.get("prompt")
        if not (type(prompt) is str and 0 < len(prompt.encode("utf-8")) <= 512 or type(prompt) is list
                and 0 < len(prompt) <= 64 and all(type(token) is int and token >= 0 for token in prompt)):
            raise ValueError("B_REQ_SINGLE_SMALL_PROMPT_REQUIRED")


# db9527a random_uuid masks UUID4 to 64 bits and formats 016x. Custom IDs
# remain excluded by validate_request; the completion child has fixed suffix -0.
PROFILES = MappingProxyType({
    "0.31.0": CaptureProfile(
        source_commit=SOURCE_COMMIT,
        sources=MappingProxyType(SOURCES),
        image=REQUEST_BINDING_GPU_IMAGE,
        callback_root=sensor.ROOT,
        source_root_key="vllm_source_root",
        required_environment=MappingProxyType({
            "VLLM_DISABLE_REQUEST_ID_RANDOMIZATION": "1",
            "VLLM_USE_V2_MODEL_RUNNER": "0",
        }),
        response_rules=MappingProxyType({
            "REQ-08": ("/v1/chat/completions", "chatcmpl-", ""),
            "REQ-09": ("/v1/completions", "cmpl-", "-0"),
            "REQ-10": ("/v1/completions", "cmpl-", "-0"),
        }),
        response_id_pattern=r"(?:chatcmpl|cmpl)-[0-9a-f]{16}",
    ),
})


def profile(version):
    return PROFILES.get(version)
