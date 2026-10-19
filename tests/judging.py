"""판정 테스트 공용."""

from guard.core.judge import judge_evidence
from tests.builders import ROUTES, T0, baseline_pack, policy, req01_obs

PACK = baseline_pack()
REQ01 = PACK.checks["REQ-01"]
OK = req01_obs()


def direct(**per_route):
    """direct_outcomes: 기본 401, 지정한 경로만 바꿈. 키는 경로 이름의 마지막 부분(completions, invocations, models)."""
    names = {"models": "/v1/models", "completions": "/v1/completions", "invocations": "/invocations"}
    out = {r: 401 for r in ROUTES}
    out.update({names[k]: v for k, v in per_route.items()})
    return out


def judge(evd, check=REQ01, pol=None, as_of=T0, origins=None):
    return judge_evidence(check, evd, pol or policy(PACK.ref()), as_of, condition_origins=origins)


def sec(evd, **kw):
    return judge(evd, **kw).verdicts.security
