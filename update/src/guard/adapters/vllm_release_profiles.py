"""기동 CLI 차이는 판본 데이터로 고른다. 관측 자격·이미지 검토는 별개다."""
from copy import deepcopy
import json
from pathlib import Path


def release_profile(version):
    catalog = json.loads((Path(__file__).with_name('assets') / 'vllm_release_profiles.json').read_bytes())
    if catalog.get('schema') != 'guard-release-profiles/1':
        raise ValueError('RELEASE_PROFILE_CATALOG_INVALID')
    rows = [row for row in catalog['profiles'] if row['version'] == version]
    if len(rows) > 1:
        raise ValueError('RELEASE_PROFILE_AMBIGUOUS')
    return deepcopy(rows[0]) if rows else None


def http_profile(version, image):
    """정확한 판본·이미지 쌍의 HTTP 검토 데이터. 후보는 실행·거부 증명이 아니다."""
    row = release_profile(version)
    if row is None or image not in row['images'] or 'http' not in row:
        return None
    return {**row['http'], 'pin_status': row['pin_status']}


def usage_report_profile(env):
    """Select the stock local-report source candidates for the exact image."""
    engine = env.engine
    row = release_profile(engine.version)
    if engine.name != 'vllm' or row is None or engine.image_digest not in row['images']:
        return None
    source = row.get('usage_report_source')
    if source is None or engine.source_commit not in {None, source['source_commit']}:
        return None
    return {key: source[key] for key in ('source_root', 'file_sha256', 'filename')}
