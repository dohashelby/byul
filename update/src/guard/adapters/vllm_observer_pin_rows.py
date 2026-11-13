"""판본별 정확 bytes 행. 태그 소스 후보는 이미지 검토·생산자 등록이 아니다."""
import json
from pathlib import Path

ROWS = json.loads((Path(__file__).with_name('assets') / 'vllm_observer_pin_rows.json').read_bytes())['modules']
SOURCE_BYTES = {row['sha256']: row['size'] for row in ROWS.values()}


def candidate_sources(previous, modules):
    """선택한 관측 대상에만 정확 후보 SHA 행을 더한다."""
    result = dict(previous)
    for name in modules:
        if name not in ROWS or name not in result:
            continue
        pins = result[name] if type(result[name]) is tuple else (result[name],)
        result[name] = tuple(dict.fromkeys((*pins, ROWS[name]['sha256'])))
    return result


def layout(observer, module):
    row = ROWS.get(module)
    if row and getattr(observer, '_source_pins', {}).get(module) == row['sha256']:
        return row.get('layout', {})
    return {}
