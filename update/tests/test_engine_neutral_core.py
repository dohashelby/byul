"""코어(core/, models/, store/)에 엔진 이름을 쓰지 않는다 (CLAUDE.md 1절, SPEC §7.1)."""

import re
import ast
from pathlib import Path
import pytest

SRC = Path(__file__).resolve().parents[1] / "src/guard"
ENGINE_NAMES = re.compile(r"vllm|sglang|tensorrt|trt-llm", re.IGNORECASE)


def test_no_engine_names_in_core():
    hits = []
    for sub in ("core", "models", "store"):
        for p in (SRC / sub).rglob("*.py"):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if ENGINE_NAMES.search(line):
                    hits.append(f"{p.relative_to(SRC)}:{i}")
    assert hits == []


COMMON_LAYERS = ('core', 'models', 'store', 'executors', 'runtime', 'collectors', 'judges')
RELEASE = re.compile(r'v?\d+\.\d+\.\d+(?:(?:a|b|rc|\.post|\.dev)\d+|[-+][a-zA-Z0-9.-]+)?')


def release_comparisons(source):
    """주석·설명 문자열을 제외하고 비교식의 고정 판본을 찾는다."""
    return sorted({node.lineno for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Compare) and any(
            isinstance(value, ast.Constant) and isinstance(value.value, str)
            and RELEASE.fullmatch(value.value) for value in ast.walk(node))})


def test_no_release_literal_comparisons_in_common_layers():
    hits = [f'{path.relative_to(SRC)}:{line}' for layer in COMMON_LAYERS
        for path in (SRC / layer).rglob('*.py')
        for line in release_comparisons(path.read_text(encoding='utf-8'))]
    assert hits == []


@pytest.mark.parametrize('source', [
    "env.engine.version == '1.2.3'", "'v1.2.3' != version",
    "version in {'1.2.3', '2.0.0rc1'}", "version not in ('1.2.3',)",
    "version == '1.2.3' == configured",
])
def test_release_comparison_detection(source):
    assert release_comparisons(source) == [1]


def test_release_comparison_detection_preserves_contracts_and_comments():
    source = '''\"\"\"version == '1.2.3'\"\"\"
# version != '1.2.3'
version == selected.version
schema == 'guard-gpu-common-input/4'
host == '127.0.0.1'
address != '255.255.255.255'
text = "version == '1.2.3'"
'''
    assert release_comparisons(source) == []
