"""Engine-specific HTTP behavior behind a stable gateway client interface.

Registration is implementation availability, never engine qualification. New
drivers can be installed with the byul.runtime_drivers entry point group.
"""
from dataclasses import dataclass
from importlib.metadata import entry_points
import re


@dataclass(frozen=True)
class BackendDriver:
    id: str
    label: str
    health_path: str = '/health'
    version_path: str | None = None
    tokenize_path: str | None = None
    chat_output_field: str = 'max_tokens'
    add_generation_prompt: bool = False
    prefer_mapped_model: bool = False
    unknown_model_length: bool = False

    def make_client(self, backend, timeout_s):
        """Override for protocols whose payload/response semantics differ."""
        from .engine_client import EngineClient
        return EngineClient(backend, timeout_s)

    def select_model(self, entries, mapped):
        if self.prefer_mapped_model:
            item = next((v for v in entries if isinstance(v, dict) and v.get('id') in mapped), None)
            return item if item is not None else next((v for v in entries if isinstance(v, dict) and v.get('max_model_len') is not None), None)
        return entries[0] if entries and isinstance(entries[0], dict) else None


DRIVERS = {
    'vllm': BackendDriver('vllm', 'vLLM', version_path='/version', tokenize_path='/tokenize', add_generation_prompt=True),
    'sglang': BackendDriver('sglang', 'SGLang', health_path='/ready', tokenize_path='/tokenize',
        chat_output_field='max_completion_tokens', prefer_mapped_model=True, unknown_model_length=True),
    # HTTP metadata only until a tokenizer/semantic profile has been reviewed.
    'tensorrt-llm': BackendDriver('tensorrt-llm', 'TensorRT-LLM'),
}


def register_driver(driver, *, replace_metadata_only=False):
    previous = DRIVERS.get(driver.id) if isinstance(driver, BackendDriver) else None
    replaceable = replace_metadata_only and previous is not None and previous.tokenize_path is None
    if not isinstance(driver, BackendDriver) or driver.id == 'mock' or re.fullmatch('[a-z][a-z0-9_.-]{0,63}', driver.id) is None or previous is not None and not replaceable:
        raise ValueError('BACKEND_DRIVER_DUPLICATE_OR_INVALID')
    DRIVERS[driver.id] = driver


for _entry in entry_points(group='byul.runtime_drivers'):
    register_driver(_entry.load()(), replace_metadata_only=True)


def driver(kind):
    if kind == 'mock': return BackendDriver('mock', 'Mock', tokenize_path='/tokenize')
    try: return DRIVERS[kind]
    except KeyError: raise ValueError('BACKEND_DRIVER_UNAVAILABLE') from None
