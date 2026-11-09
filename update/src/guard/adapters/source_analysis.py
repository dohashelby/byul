"""Engine-owned static analysis profiles; unknown coverage stays unmapped."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SourceAnalysis:
    id: str
    engine: str
    url: str
    rules: Path
    entrypoints: tuple[tuple[str, str], ...]
    reviewed: bool

    def entrypoint(self, path):
        for prefix, kind in self.entrypoints:
            if path.startswith(prefix):
                return 'grpc' if kind == 'http' and 'grpc' in path else kind
        return None


_ASSETS = Path(__file__).with_name('assets')
PROVIDERS = {
    'vllm': SourceAnalysis('vllm-path-rules/3', 'vllm', 'https://github.com/vllm-project/vllm.git',
        _ASSETS / 'vllm_change_rules.yaml', (('rust/src/server/', 'native'), ('vllm/entrypoints/', 'http')), True),
    'sglang': SourceAnalysis('conservative-unmapped/1', 'sglang', 'https://github.com/sgl-project/sglang.git',
        _ASSETS / 'unmapped_change_rules.yaml', (), False),
    'tensorrt-llm': SourceAnalysis('conservative-unmapped/1', 'tensorrt-llm', 'https://github.com/NVIDIA/TensorRT-LLM.git',
        _ASSETS / 'unmapped_change_rules.yaml', (), False),
}


def get_source_analysis(engine):
    from .engine_provider import PROVIDERS as engines, load_installed_providers
    load_installed_providers()
    if engine in engines and engines[engine].source_analysis is not None:
        return engines[engine].source_analysis
    try:
        return PROVIDERS[engine]
    except KeyError:
        raise ValueError('ENGINE_SOURCE_ANALYSIS_UNAVAILABLE:' + str(engine)) from None
