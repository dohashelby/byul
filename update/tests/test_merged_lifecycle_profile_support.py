"""Merged exact SG runtime support does not open generic/native features."""
import pytest
from guard.adapters import engine_profiles
from guard.adapters import sglang_request_termination_sensor as sg


def test_existing_sg_runtime_generation_uses_exact_catalog_feature():
    result=engine_profiles.resolve('sglang','0.5.22',sg.PROFILE)
    assert result.available and not result.runtime_qualified
    assert not engine_profiles.supports('sglang','0.5.22')
    assert not engine_profiles.supports('sglang','0.5.22','termination')
    assert engine_profiles.supports('vllm','0.31.0')


@pytest.mark.parametrize('name,version',[('sglang','0.5.21'),('sglang','0.5.23'),('vllm','0.31.0'),('unreviewed','0.5.22')])
def test_sg_runtime_profile_does_not_admit_other_engine_or_generation(name,version):
    assert not engine_profiles.supports(name,version,sg.PROFILE)
    with pytest.raises(ValueError):engine_profiles.require(name,version,sg.PROFILE)
