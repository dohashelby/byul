"""작업지시서 0.1절의 판본 쌍만 읽는다. 판본 번호를 코드에 복제하지 않는다."""
from pathlib import Path
import re

WORK_ORDER = Path(__file__).resolve().parents[1] / 'docs/WORK_ORDER_VLLM_MVP.md'


def versions(path=WORK_ORDER):
    text = Path(path).read_text(encoding='utf-8')
    section = text.split('### 0.1 MVP 판본 표 (데이터)', 1)[1].split('### 0.2', 1)[0]
    rows = re.findall(r'^\| (기준 판본|후보 판본) \| ([^|]+?) \| `([^`]+)`', section, re.M)
    if len(rows) != 2 or [row[0] for row in rows] != ['기준 판본', '후보 판본']:
        raise ValueError('MVP 판본 표의 두 행이 필요합니다.')
    result = {}
    for side, (role, identity, image) in zip(('baseline', 'candidate'), rows):
        engine, version = identity.strip().split(' ', 1)
        if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not re.fullmatch(r'.+@sha256:[0-9a-f]{64}', image):
            raise ValueError('MVP 판본 또는 이미지 digest가 올바르지 않습니다.')
        result[side] = {'role': role, 'engine': engine.lower(), 'version': version, 'image': image}
    if result['baseline'] == result['candidate'] or result['baseline']['image'] == result['candidate']['image']:
        raise ValueError('기준·후보는 별도 이미지여야 합니다.')
    return result
