"""INV-03: 시도가 없으면 차단 성공이 아니다. 응답 미수신만으로 PASS가 되지 않는다.

refused·timeout·unreachable은 같은 시각 신뢰 출발점에서 대상에 도달함(양성 대조)이 관측된 경우에만 차단 근거.
dns_error·error는 차단 근거가 아니다.
"""

import socket
import sys

import pytest

from guard.executors.http_probe import probe
from guard.models import Verdict
from tests.builders import ROUTES, evidence, req01_obs
from tests.judging import OK, direct, sec


def test_not_attempted_but_rejected_looking_is_not_pass():
    assert sec(evidence(attempted=False, observations=OK)) == Verdict.UNKNOWN


def test_partially_attempted_is_not_pass():
    assert sec(evidence(attempted=[True, False, True], observations=OK)) == Verdict.UNKNOWN


@pytest.mark.parametrize("outcome", ["refused", "timeout", "unreachable"])
def test_no_response_with_positive_control_is_pass(outcome):
    obs = req01_obs(direct={r: outcome for r in ROUTES}, trusted=True)
    assert sec(evidence(observations=obs)) == Verdict.PASS


@pytest.mark.parametrize("outcome", ["refused", "timeout", "unreachable"])
def test_no_response_without_positive_control_is_not_pass(outcome):
    obs = req01_obs(direct={r: outcome for r in ROUTES}, trusted=False)
    assert sec(evidence(observations=obs)) == Verdict.UNKNOWN


def test_no_response_with_uncollected_positive_control_is_not_pass():
    obs = req01_obs(direct={r: "refused" for r in ROUTES}, missing=("trusted_origin_reachable",))
    assert sec(evidence(observations=obs)) == Verdict.UNKNOWN


def test_mixed_401_and_refused_needs_positive_control():
    obs = req01_obs(direct=direct(invocations="refused"), trusted=False)
    assert sec(evidence(observations=obs)) == Verdict.UNKNOWN


def test_401_only_does_not_need_positive_control():
    assert sec(evidence(observations=req01_obs(trusted=False))) == Verdict.PASS


@pytest.mark.parametrize("outcome", ["dns_error", "error"])
def test_dns_and_other_errors_are_never_block_evidence(outcome):
    obs = req01_obs(direct={r: outcome for r in ROUTES}, trusted=True)
    assert sec(evidence(observations=obs)) == Verdict.UNKNOWN


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 알려진 제한: 닫힌 루프백 포트가 연결 거부 대신 timeout으로 보고됨 (지원 플랫폼 macOS·Linux, DECISIONS 2026-10-19)")
def test_real_connection_refused_is_classified_and_not_pass_alone():
    """루프백에서 실제 연결 거부를 만들어 분류하고, 양성 대조 없이는 PASS가 아님을 확인 (Docker 불필요)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # 소켓을 닫았으므로 그 포트는 연결 거부
    out = probe({"method": "GET", "url": f"http://127.0.0.1:{port}/v1/models"}, timeout=1.0)
    assert out["outcome"] == "refused"
    obs = req01_obs(direct={r: out["outcome"] for r in ROUTES}, trusted=False)
    assert sec(evidence(observations=obs)) == Verdict.UNKNOWN
    obs = req01_obs(direct={r: out["outcome"] for r in ROUTES}, trusted=True)
    assert sec(evidence(observations=obs)) == Verdict.PASS
