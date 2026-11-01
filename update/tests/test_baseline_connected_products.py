"""현행 PACK-BASELINE을 기존 제품 왕복 시험에 넣어 연결 8개의 선정→실행→EVD→RES→RPT→재판정을 확인한다.

기존 시험 함수를 그대로 쓰고 파생 팩 대신 현행 팩만 넣는다. kubectl과 기록은 EXAMPLE이며 실제 엔진은 없다.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from guard.executors import registry
from guard.models import load_pack
import tests.test_a1_credential_copy_product as copy_product
import tests.test_a1_credential_revocation_product as credential
import tests.test_a1_forwarded_identity_product as forwarded
import tests.test_a1_kubernetes_model_authorization_product as model_auth
import tests.test_a1_required_event_product as required_event
import tests.test_a1_session_revocation_product as session
import tests.test_a1_stored_responses_product as stored

from tests.test_a1_credential_revocation_product import serving_loop  # noqa: F401
from tests.test_lifecycle_sensor import writer_factory  # noqa: F401
from tests.test_kubernetes_lifecycle import group, target as native_target  # noqa: F401
from tests.test_lifecycle_capture import case, external, revised  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
CURRENT = ROOT / "packs/PACK-BASELINE"


class _Receipt(dict):
    """파생 빌더 영수증 자리. 전체 완료 표시는 없는 값(False)으로 읽힌다."""

    def __missing__(self, key):
        return False


def _current(_seed, output):
    shutil.copytree(CURRENT, output)
    return _Receipt(pack_ref=load_pack(output).ref().model_dump(mode="json"), checks=77, unchanged_definition_bytes=76)


@pytest.fixture
def current_pack(monkeypatch):
    for module in (copy_product, credential, forwarded, model_auth, required_event, session, stored):
        monkeypatch.setattr(module, "derive", _current)


CONNECTED = {"PLT-10": "credential_revocation", "PLT-11": "session_revocation", "PLT-12": "required_event_record",
             "OBS-01": "required_event_record", "REQ-02": "route_record", "REQ-04": "forwarded_identity",
             "REQ-05": "model_authorization", "REQ-06": "stored_responses"}


@pytest.mark.parametrize("check_id,name", sorted(CONNECTED.items()))
def test_connected_check_reaches_the_kubernetes_factory_and_has_real_rules(check_id, name):
    check = load_pack(CURRENT).checks[check_id]
    assert check.executor == name and check.judge.plugin == "rule_table"
    assert check.judge.params["pass_when_all"] and check.judge.params["fail_when_any"]
    executor = registry.resolve_executor(name, "kubernetes")({name: {}})
    assert executor.name == name
    assert registry.EXECUTORS.get(f"{name}:real") is None  # kubernetes를 real로 세지 않는다


@pytest.mark.parametrize("mode", ["denied", "accepted", "native-partial", "http-cleanup"])
def test_plt10_round_trip_on_current_pack(tmp_path, group, native_target, writer_factory, monkeypatch, serving_loop, current_pack, mode):
    credential.test_registered_cli_auth_samples_absolute_constraint_and_exact_refs(
        tmp_path, group, native_target, writer_factory, monkeypatch, serving_loop, mode)


@pytest.mark.parametrize("mode", ["denied", "accepted", "reconnected"])
def test_plt11_round_trip_on_current_pack(tmp_path, group, native_target, writer_factory, monkeypatch, serving_loop, current_pack, mode):
    session.test_registered_cli_existing_connection_constraints_and_exact_refs(
        tmp_path, group, native_target, writer_factory, monkeypatch, serving_loop, mode)


@pytest.mark.parametrize("mode", ["normal", "missing-event", "event-conflict"])
def test_plt12_round_trip_on_current_pack(tmp_path, monkeypatch, current_pack, mode):
    copy_product.test_existing_registry_cli_copy_records_and_exact_refs(tmp_path, monkeypatch, mode)


@pytest.mark.parametrize("mode", ["normal", "missing-field", "missing", "recovered"])
def test_obs01_round_trip_on_current_pack(tmp_path, monkeypatch, current_pack, mode):
    required_event.test_existing_cli_records_and_exact_refs_preserve_unknown_and_followup_facts(tmp_path, monkeypatch, mode)


@pytest.mark.parametrize("mode", ["normal", "claim-leak", "missing-engine"])
def test_req04_round_trip_on_current_pack(tmp_path, monkeypatch, current_pack, mode):
    forwarded.test_registered_cli_issuer_claims_errors_and_exact_ref_roundtrip(tmp_path, monkeypatch, mode)


@pytest.mark.parametrize("mode", ["normal", "retired", "native-one-missing"])
def test_req05_round_trip_on_current_pack(tmp_path, group, native_target, monkeypatch, current_pack, mode):
    model_auth.test_registered_cli_owned_probe_native_and_exact_refs(tmp_path, group, native_target, monkeypatch, mode)


@pytest.mark.parametrize("mode", ["enforced", "vulnerable", "partial"])
def test_req06_round_trip_on_current_pack(tmp_path, monkeypatch, current_pack, mode):
    stored.test_existing_registry_cli_all_operations_exact_refs_and_no_raw_objects(tmp_path, monkeypatch, mode)
