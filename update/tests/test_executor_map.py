"""사용자가 확정한 77개 실행기 연결표와 현행 팩·registry의 대조."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guard.executors import registry
from guard.models import load_pack

ROOT = Path(__file__).resolve().parents[1]
MAP = yaml.safe_load((ROOT / "docs/pack_baseline_executor_map.yaml").read_text(encoding="utf-8"))
CHECKS = MAP["checks"]
EXCLUDED = {"SUP-01", "SUP-02", "SUP-14", "LIF-15", "OBS-07", "OBS-08", "OBS-10", "OBS-12"}
OWNER2_PENDING = "owner2"


def _yaml_keys():
    """dict 변환이 중복 키를 지우므로 원문에서 다시 센다."""
    lines = (ROOT / "docs/pack_baseline_executor_map.yaml").read_text(encoding="utf-8").splitlines()
    return [line.strip().split(":")[0] for line in lines if line.startswith("  ") and "-" in line and not line.strip().startswith("#")]


def _implementations(name):
    return [impl for impl in ("real", "kubernetes") if f"{name}:{impl}" in registry.EXECUTORS
            and registry.resolve_executor(name, impl) is not None]


def test_map_has_77_unique_checks_with_69_connected_and_8_manual():
    keys = _yaml_keys()
    assert len(keys) == len(set(keys)) == 77 == len(CHECKS)
    assert set(keys) == set(load_pack(ROOT / "packs/PACK-BASELINE").checks)
    assert sum(v is not None for v in CHECKS.values()) == 69
    assert {k for k, v in CHECKS.items() if v is None} == EXCLUDED


def test_exactly_four_new_executor_names():
    assert MAP["new_executors"] == ["supply_boot", "record_access", "engine_sensor", "lifecycle_transition"]
    planned = {v for v in CHECKS.values() if v and v != OWNER2_PENDING}
    registered = {key.split(":")[0] for key in registry.EXECUTORS}
    assert planned - registered <= set(MAP["new_executors"])
    assert set(MAP["new_executors"]) <= planned


def test_current_pack_never_disagrees_with_the_map():
    pack = load_pack(ROOT / "packs/PACK-BASELINE")
    for check_id, check in pack.checks.items():
        planned = CHECKS[check_id]
        if planned is None:
            assert check.executor is None, check_id
        elif check.executor is not None and planned != OWNER2_PENDING:
            assert check.executor == planned, check_id


@pytest.mark.parametrize("name", sorted({v for v in CHECKS.values() if v and v != OWNER2_PENDING} - {"engine_sensor", "lifecycle_transition"}))
def test_connected_executor_has_a_supported_non_fake_implementation(name):
    if name in ("supply_boot", "record_access") and not _implementations(name):
        pytest.xfail("구현 전: 담당 1 순서상 뒤에 등록")
    assert _implementations(name), name
