"""M-A6 실행기 단위 판본표: 카탈로그 기능으로만 열고, 막힌 실행기는 판본 데이터에 원인이 있다."""
import pytest

from guard.adapters import engine_profiles
from guard.adapters.engine_provider import get_provider
from guard.executors.registry import EXECUTORS, _PORTABLE_KUBERNETES, resolve_executor

KUBERNETES = sorted(k.split(':')[0] for k in EXECUTORS if k.endswith(':kubernetes'))
ENGINE_BOUND = [e for e in KUBERNETES if e not in _PORTABLE_KUBERNETES]
# 엔진 안 관측기를 설치하지 않는 실행기(소유 probe Pod·Kubernetes API·CRI 로그·/proc).
EXTERNAL = {'boundary_egress', 'forwarded_identity', 'log_integrity', 'record_access', 'required_event_record',
            'response_content', 'route_record', 'stored_responses', 'template_validation', 'unix_ipc'}


def test_every_engine_bound_executor_has_a_catalog_feature_and_the_reference_release_is_unchanged():
    native = next(p for p in engine_profiles.PROFILES if p.id == 'vllm-native-source/1')
    for executor in ENGINE_BOUND:
        assert executor in engine_profiles.EXECUTOR_FEATURES
        assert engine_profiles.executor_feature(executor) in native.features
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.31.0') is not None


@pytest.mark.parametrize('version', ['0.7.0', '0.30.0'])
def test_external_observation_executors_open_on_both_mvp_releases(version):
    for executor in sorted(EXTERNAL):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version=version) is not None, executor
        support = engine_profiles.resolve('vllm', version, engine_profiles.executor_feature(executor))
        assert support.profile == 'vllm-external-observation/1' and not support.runtime_qualified


@pytest.mark.parametrize('version', ['0.7.0', '0.30.0'])
def test_each_closed_engine_bound_executor_records_its_cause(version):
    provider = get_provider('vllm')
    closed = [e for e in ENGINE_BOUND if resolve_executor(e, 'kubernetes', engine='vllm', version=version) is None]
    assert closed and not set(closed) & EXTERNAL
    for executor in closed:
        gap = provider.executor_gap(version, executor)
        assert gap and gap['cause'] in {'OBSERVER_PIN_ROWS_ABSENT', 'V0_OBSERVER_NOT_IMPLEMENTED', 'V0_TARGET_TABLE_ABSENT',
                                        'APPROVED_UNKNOWN', 'APPROVED_UNKNOWN_V1', 'REQUEST_FIELD_MAPPING_NOT_CONSUMED'}, executor
        assert gap['observers'] and gap['basis']
    # 열린 실행기에는 원인을 적지 않는다(열림과 공백이 섞이지 않게).
    for executor, _ in (provider.release_profile(version)['executor_gaps'] or {}).items():
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version=version) is None, executor


def test_engine_internal_observers_stay_closed_without_release_pins():
    # V1 선택은 실행 자격이 아니다. 기준은 정확 대상표/행이 없으므로 계속 닫혀 있다.
    assert resolve_executor('engine_sensor', 'kubernetes', engine='vllm', version='0.30.0') is not None
    assert resolve_executor('engine_sensor', 'kubernetes', engine='vllm', version='0.7.0') is None
    assert resolve_executor('sampling_validation', 'kubernetes', engine='vllm', version='0.7.0') is None
    assert resolve_executor('sampling_validation', 'kubernetes', engine='vllm', version='0.30.0') is not None
    for executor in ('stream_buffer', 'profiler_observation'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.30.0') is not None
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.7.0') is None
    for executor in ('request_termination', 'observation_state', 'initialization'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.30.0') is not None
    for executor in ('request_termination', 'observation_state'):
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='0.7.0') is not None
    assert resolve_executor('initialization', 'kubernetes', engine='vllm', version='0.7.0') is None
    # M-A6-V6: 기반 /8만 쓰는 실행기는 후보의 정확 행 profile(/25)로 열린다.
    assert resolve_executor('model_authorization', 'kubernetes', engine='vllm', version='0.30.0') is not None
    assert resolve_executor('model_authorization', 'kubernetes', engine='vllm', version='0.7.0') is None
    # 이전 V0 profiler UNKNOWN 승인을 V1 기동에 이월하지 않는다.
    assert get_provider('vllm').executor_gap('0.7.0', 'profiler_observation')['cause'] == 'OBSERVER_PIN_ROWS_ABSENT'
    assert get_provider('vllm').executor_gap('EXAMPLE-future', 'stream_buffer') is None


def test_unreviewed_release_and_other_engines_gain_nothing():
    for executor in ENGINE_BOUND:
        assert resolve_executor(executor, 'kubernetes', engine='vllm', version='EXAMPLE-future') is None
        assert resolve_executor(executor, 'kubernetes', engine='sglang', version='0.30.0') is None
