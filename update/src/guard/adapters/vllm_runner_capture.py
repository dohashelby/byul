"""Reviewed vLLM request/callback capture data and response-ID interpretation.

Release additions use data rows; the executor never selects a release or an ID format.
"""
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Mapping
from urllib.parse import urlsplit

from . import vllm_consumption_callback as v1, vllm_consumption_v2_callback as v2
from . import vllm_engine_callback_sensor as sensor
from .vllm_observer_pin_rows import ROWS
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
    private_response_identity: bool = False

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
    # The candidate image's nine serving/input/worker modules are byte-qualified in
    # ma6-pin-compare.json. V1 InputBatch stores the request's temperature/top_p
    # in CPU arrays, and GPUModelRunner._sample passes its sampling_metadata to
    # the sampler. This profile observes those two fields only; structured
    # output and min_tokens still require separate evidence.
    "0.30.0": CaptureProfile(
        source_commit="ced6857afa0ea7b2e3f0846a62e1394e90f15607",
        sources=MappingProxyType({
            "entrypoints/serve/engine/serving.py": "70143311acc2230b925e7f3603b8b9314f029ff5e98d5af990b3bdca691331f1",
            "entrypoints/openai/completion/serving.py": "75d05ddddf303734a078a3e42fcb173fef58ea56da09bc0b5c7525cb9bbbf527",
            "v1/engine/input_processor.py": "c8abd8e4e14d99929d45a7ff3b2a74898269c5e2a7fa30b826623ee85e4ce813",
            "v1/engine/async_llm.py": "52cbf404f1dbc99c6bebf4a50a7eb18ff842a1f53106a45f365523192d816be1",
            "entrypoints/openai/completion/protocol.py": "5f7930635fd3924826015211032fb298206b27c7f04cd6cd2224bb128e4a3a50",
            "entrypoints/openai/chat_completion/serving.py": "ea1f76074a9587c8054f54d30a6ba748b6a5fc4d90dd82c801504505c1da1f92",
            "entrypoints/openai/chat_completion/protocol.py": "51fb06a943201ffdc0a0bbae44b79a996bdd6ae3373593a67666868d996451b2",
            "v1/worker/gpu_model_runner.py": "87c29d08c0bbf66993d8b984811e7325e35e6436242a773feb55ec88f16b2c51",
            "v1/worker/gpu_input_batch.py": "18cf455c9d0eea61cc581c7932f2e6cd09786914e82dd35eef1c1430da359a0f",
        }),
        image="docker.io/vllm/vllm-openai@sha256:5f5e535216848d0c52159c8c13a0af04be5f6fe1a84e79914300610796f76d40",
        callback_root=sensor.ROOT,
        source_root_key="vllm_source_root",
        required_environment=MappingProxyType({
            "VLLM_DISABLE_REQUEST_ID_RANDOMIZATION": "1",
            "VLLM_USE_V2_MODEL_RUNNER": "0",
        }),
        response_rules=MappingProxyType({
            "REQ-10": ("/v1/completions", "cmpl-", "-0"),
        }),
        response_id_pattern=r"(?:chatcmpl|cmpl)-[0-9a-f]{16}",
        private_response_identity=True,
    ),
})


def profile(version):
    return PROFILES.get(version)


# The V2 runner is in a different installed module than the legacy capture.
_SCOPED_V2_SOURCE_PINS = {
    '0.30.0': ROWS[v2.RUNNER]['sha256'],
    '0.31.0': v2.SOURCES[v2.RUNNER],
}


def scoped_record_sources(scope):
    """Exact source files for a reviewed release/image, without runtime qualification."""
    if type(scope) is not dict or scope.get('engine') != 'vllm':
        return None
    version, image = scope.get('engine_version'), scope.get('image_digest')
    if type(version) is not str or version not in _SCOPED_V2_SOURCE_PINS or type(image) is not str:
        return None
    selected = profile(version)
    if image.removeprefix('docker.io/') != selected.image.removeprefix('docker.io/'):
        return None
    return {
        v1.INPUT: selected.sources['v1/engine/input_processor.py'],
        v1.RUNNER: selected.sources.get('v1/worker/gpu_model_runner.py', v1.cache.base.SOURCE_SHA[v1.RUNNER]),
        v2.RUNNER: _SCOPED_V2_SOURCE_PINS[version],
    }
