"""EXAMPLE proposal integration; actual request class runs separately."""
import pytest
from tools.b1_integration_cpu import run_case,SCENARIOS


@pytest.mark.parametrize('scenario',SCENARIOS)
def test_req10_observations_survive_product_normalization_judgment_and_report(tmp_path,scenario):
    outcomes={'none':'accepted','auto':'accepted','required':'accepted','named-only':'accepted','named-json':'rejected'}
    result=run_case(tmp_path/scenario,scenario,outcomes)
    assert result['exact_ref_rejudge_preserved'] and result['origin']=='EXAMPLE'
