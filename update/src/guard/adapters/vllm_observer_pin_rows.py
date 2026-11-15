"""판본별 정확 bytes 행. 태그 소스 후보는 이미지 검토·생산자 등록이 아니다."""
import json
from pathlib import Path

ROWS = json.loads((Path(__file__).with_name('assets') / 'vllm_observer_pin_rows.json').read_bytes())['modules']
SOURCE_BYTES = {row['sha256']: row['size'] for row in ROWS.values()}
# The 0.7.0 V1 image's Python files match clean tag 5204ff5c. These are
# separate exact rows for the two reviewed cache/slot points, not a release
# wildcard and not producer/GPU registration.
BASELINE_ROWS = {
    'vllm.v1.core.kv_cache_manager': {
        'sha256': '39f087609c2049d6383d45ae098d5b5a54749d32f14ab6d4cf779d3f54390355',
        'size': 22785,
    },
    'vllm.v1.worker.gpu_input_batch': {
        'sha256': '89770d22d9398a061d96e4aa547a8afbacaf0eeff589e1dcf3df45503a4ab40c',
        'size': 18246,
    },
}


def candidate_sources(previous, modules):
    """선택한 관측 대상에만 정확 후보 SHA 행을 더한다."""
    result = dict(previous)
    for name in modules:
        if name not in ROWS or name not in result:
            continue
        pins = result[name] if type(result[name]) is tuple else (result[name],)
        result[name] = tuple(dict.fromkeys((*pins, ROWS[name]['sha256'])))
    return result


def baseline_sources(previous, modules):
    """Add only the exact 0.7.0 V1 file rows selected by this observer."""
    result = dict(previous)
    for name in modules:
        if name not in BASELINE_ROWS or name not in result:
            continue
        pins = result[name] if type(result[name]) is tuple else (result[name],)
        result[name] = tuple(dict.fromkeys((*pins, BASELINE_ROWS[name]['sha256'])))
    return result


def layout(observer, module):
    row = ROWS.get(module)
    if row and getattr(observer, '_source_pins', {}).get(module) == row['sha256']:
        return row.get('layout', {})
    return {}
