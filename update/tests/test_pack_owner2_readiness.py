"""The owner's fixed mapping must never turn registry presence into a connection."""
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from tests.builders import baseline_pack
from tools import pack_owner2_readiness as tool


def test_exact_user_assignment_and_manual_exclusions():
    assert set(tool.MAPPING) == {
        'LIF-03','LIF-04','LIF-05','LIF-06','LIF-07','LIF-08','LIF-09','LIF-10','LIF-11','LIF-12','LIF-13','LIF-14','LIF-16',
        'REQ-12','REQ-14','REQ-15','REQ-16','REQ-17','REQ-18','REQ-19','REQ-20','REQ-21','OBS-02'}
    result = tool.inspect(baseline_pack())
    assert result['assigned'] == 23 and result['excluded_manual_paths_preserved']
    assert all(not row['whole_check_experiment_complete'] and not row['gpu_measured'] for row in result['checks'])
    assert {row['check_id'] for row in result['checks'] if row['unimplemented_reason']} == {
        'LIF-13','REQ-14','REQ-15','REQ-16','REQ-17','REQ-18','REQ-19','REQ-20','REQ-21'}


def test_fake_factory_does_not_count_as_support(monkeypatch):
    monkeypatch.setitem(registry.EXECUTORS, 'engine_sensor:kubernetes', lambda cfg: FakeExecutor())
    result = tool.inspect(baseline_pack())
    selected = [row for row in result['checks'] if row['executor'] == 'engine_sensor']
    assert selected and all(not row['registered_factory_available'] and not row['baseline_connected'] for row in selected)


def test_registry_presence_without_pack_profile_and_judge_is_never_a_connection():
    pack = baseline_pack()
    for check_id in tool.MAPPING:
        pack.checks[check_id].executor = None
    result = tool.inspect(pack)
    assert result['registered_factory_available'] == 23
    assert result['baseline_connected'] == 0
