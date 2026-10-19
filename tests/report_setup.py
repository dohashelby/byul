"""보고서 테스트 공용: 저장소에 select→run→judge까지 채운다."""

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


def pipeline(tmp_path, scenario="pass", include=None, pack=None, **pol_kw):
    store = Store(tmp_path / ".guard")
    pack = pack or baseline_pack()
    env = environment({"gateway": {"value": True, **D}})
    pol = policy(pack.ref(), scope={"include": include or [], "exclude": []}, **pol_kw)
    store.put(env); store.put(pol)
    plan = select(pack, [env], pol); store.put(plan)
    run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}, adapter="fake", raw={"fake": {"scenario": scenario}}))
    judge_plan(store, pack, plan, as_of=T0)
    return store, pack, plan
