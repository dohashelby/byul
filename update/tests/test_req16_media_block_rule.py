"""REQ-16 이미지 문맥(사용자 결정 2026-11-05): 이미지 토큰을 담은 블록까지 적중했을 때만 위반이다."""
from types import SimpleNamespace

from guard.adapters.vllm_cache_callback import CacheCallbackObserver
from guard.executors import engine_callback_projection as projection

POLICY = {'required_cache_contexts': ['media_identity']}
IMG_X = [{'offset': 64, 'length': 16, 'identifier_sha256': 'x' * 64}]
IMG_Y = [{'offset': 64, 'length': 16, 'identifier_sha256': 'y' * 64}]


def cases():
    media = {'seed': 1}
    return [{'case_id': 'prime', 'role': 'prime', 'prefix': 'p', 'context': 'media_identity', 'prompt': 'x', 'media': media},
            {'case_id': 'control', 'role': 'control', 'prefix': 'p', 'context': 'media_identity', 'prompt': 'x', 'media': media},
            {'case_id': 'probe', 'role': 'probe', 'prefix': 'p', 'context': 'media_identity', 'prompt': 'x', 'media': {'seed': 2}},
            {'case_id': 'post', 'role': 'post', 'prompt': 'x'}]


def rows(probe_tokens, *, control_tokens=96, probe_media=IMG_Y, control_media=IMG_X):
    row = lambda tokens, media, first='h0': [{'tokens': tokens, 'hashes': [first], 'salt': None, 'media': media}]
    return {'prime': row(0, IMG_X), 'control': row(control_tokens, control_media), 'probe': row(probe_tokens, probe_media),
            'post': row(32, [])}


def test_shared_text_prefix_before_the_image_is_normal_sharing():
    result = projection.project_cache_context(cases(), rows(64), POLICY)
    assert result['violation'] is False and result['full_scope'] is True
    assert result['contexts'][0]['text_prefix_hit'] is True and result['contexts'][0]['changed_hit'] is False


def test_hit_reaching_image_tokens_of_a_different_image_is_a_violation():
    result = projection.project_cache_context(cases(), rows(80), POLICY)
    assert result['violation'] is True and result['contexts'][0]['basis'] == 'media_block_reach'


def test_without_image_positions_or_control_power_the_media_context_is_unmeasured():
    assert projection.project_cache_context(cases(), rows(80, probe_media=None), POLICY)['violation'] is None
    # 같은 이미지를 다시 보낸 대조가 이미지 블록까지 적중하지 않으면 측정력이 없다.
    assert projection.project_cache_context(cases(), rows(80, control_tokens=64), POLICY)['violation'] is None


def test_cache_sensor_records_media_positions_and_only_an_identifier_fingerprint():
    observer = CacheCallbackObserver.__new__(CacheCallbackObserver)
    observer.gaps = set(); observer.fingerprint = lambda value: 'f' * 64 if value is not None else None
    feature = SimpleNamespace(mm_position=SimpleNamespace(offset=64, length=16), identifier='raw-image-id', data=None)
    assert observer._media([feature]) == [{'offset': 64, 'length': 16, 'identifier_sha256': 'f' * 64}]
    assert observer._media(None) == [] and observer._media([]) == []
    assert observer._media([SimpleNamespace(mm_position=SimpleNamespace(offset=None, length=0), identifier='x')]) is None
    assert 'CACHE_CALLBACK_MEDIA_SHAPE_UNAVAILABLE' in observer.gaps
