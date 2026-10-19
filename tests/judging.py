"""판정 테스트 공용."""

from guard.core.judge import judge_evidence
from tests.builders import T0, baseline_pack, policy

PACK = baseline_pack()
REQ01 = PACK.checks["REQ-01"]
OK = {"direct_route_status": {"value": 401, "collected": True}, "gateway_route_status": {"value": 200, "collected": True}}


def judge(evd, check=REQ01, pol=None, as_of=T0, origins=None):
    return judge_evidence(check, evd, pol or policy(PACK.ref()), as_of, condition_origins=origins)


def sec(evd, **kw):
    return judge(evd, **kw).verdicts.security
