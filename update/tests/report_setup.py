"""보고서 테스트 공용: 저장소에 select→run→judge까지 채운다."""

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy
from tests.prepared_fixtures import manual_setup_seed

D = {"origin": "DECLARED", "source": "env.yaml"}


def pipeline(tmp_path, scenario="pass", include=None, pack=None, **pol_kw):
    use_seed = pack is None
    pack = pack or baseline_pack()
    env = environment({"gateway": {"value": True, **D}})
    pol = policy(pack.ref(), scope={"include": include or [], "exclude": []}, **pol_kw)
    config = RunConfig(executors={"direct_route": "fake"}, adapter="fake", raw={"fake": {"scenario": scenario}})

    def create(folder):
        store = Store(folder / ".guard")
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        run_plan(store, pack, plan, config)
        judge_plan(store, pack, plan, as_of=T0)
        return store, plan, {}

    if use_seed:
        store, plan, _ = manual_setup_seed(
            family="report-pipeline",
            parameters={"pack": pack.model_dump(mode="json"),
                        "environment": env.to_dict(), "policy": pol.to_dict(),
                        "mode": "audit", "scenario": scenario,
                        "run_config": {"executors": config.executors, "adapter": config.adapter, "raw": config.raw},
                        "judge_as_of": T0.isoformat()},
            folder=tmp_path, create=create, store_directory=".guard")
    else:
        # A caller-owned pack may be mutated between calls; execute it afresh.
        store, plan, _ = create(tmp_path)
    return store, pack, plan
