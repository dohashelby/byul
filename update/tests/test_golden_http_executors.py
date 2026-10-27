"""HTTP 실행기 골든 비교: 실행기 내부를 나누거나 공통화할 때 EVD 초안(관측·챌린지·정상 대조)과 증거 요약이
바뀌지 않았는지 확인한다. 골든은 분할 전 코드로 기록했다(tests/golden/http_executors.json).

    GUARD_UPDATE_GOLDEN=1 uv run pytest tests/test_golden_http_executors.py   # 의도한 변경일 때만 다시 기록
"""

import json
import os
from pathlib import Path

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors.direct_route import DirectRouteExecutor
from guard.executors.role_route import RoleRouteExecutor
from tests import test_direct_route as dr
from tests import test_method_keys as mk
from tests import test_role_route as rr
from tests.builders import baseline_pack, environment

GOLDEN = Path(__file__).parent / "golden/http_executors.json"
VOLATILE = {"elapsed_ms", "started_at", "finished_at", "path", "id", "collected_at"}
PACK = baseline_pack()


def _norm(v):
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items() if k not in VOLATILE}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _draft_record(draft):
    d = draft.model_dump(mode="json")
    log = [json.loads(l) for a in draft.artifacts for l in Path(a.path).read_text(encoding="utf-8").splitlines()]
    for line in log:  # 실행마다 바뀌는 무작위 키 마스크 결과는 그대로(키 값 자체는 없음)
        line.pop("elapsed_ms", None)
    return _norm({k: d[k] for k in ("status", "data_origin", "normal_control", "challenges", "observations",
                                    "comparability")} | {"log": log})


def _direct(tmp_path, world, exposed, version="1.0.0", host=None):
    target = dr._target(tmp_path, exposed)
    if host is not None:
        t = json.loads(target.read_text())
        t["direct_targets"] = [x for x in t["direct_targets"] if x["origin"] != "host"] + host
        target.write_text(json.dumps(t))
    cfg = {"target": str(target), "roles": {"allowed": "KA"}, "wrong_key_env": "KW"}
    ex = DirectRouteExecutor(cfg, container_probe=world.container_probe, host_probe=world.host_probe,
                             environ={"KA": dr.ALLOWED, "KW": dr.DENIED})
    env = environment({}, engine={"name": "vllm", "version": version}, model={"id": "m"})
    return ex.run(PACK.checks["REQ-01"], env, {"allowed_callers": ["allowed"]}, VllmAdapter(), tmp_path / "w")


def _multi(tmp_path, exposed):
    world = mk.MultiWorld(exposed=exposed)
    target = mk._target(tmp_path, exposed)
    cfg = {"target": str(target), "roles": {"allowed": "KA"}, "wrong_key_env": "KW"}
    ex = DirectRouteExecutor(cfg, container_probe=lambda n, rs, t: [world.handle(n, r) for r in rs],
                             host_probe=lambda r, t: world.handle("host", r), environ={"KA": mk.ALLOWED, "KW": mk.WRONG})
    env = environment({}, engine={"name": "vllm", "version": "0.31.0"}, model={"id": "m"})
    return ex.run(PACK.checks["REQ-01"], env, {"allowed_callers": ["allowed"]}, VllmAdapter(), tmp_path / "w")


def _role(tmp_path, world, version="0.31.0"):
    cfg = {"target": str(rr._target(tmp_path)), "roles": {"general": "KG", "admin": "KD"}, "wrong_key_env": "KW"}
    ex = RoleRouteExecutor(cfg, container_probe=world.container_probe, environ={"KG": rr.GENERAL, "KD": rr.ADMIN, "KW": rr.WRONG})
    env = environment({}, engine={"name": "vllm", "version": version}, model={"id": "facebook/opt-125m"})
    return ex.run(PACK.checks["REQ-03"], env, {"general_allowed_kinds": ["inference"]}, VllmAdapter(), tmp_path / "w")


SLEEP = {**dr.ENGINE_ROUTES, "/sleep": "post"}
SCENARIOS = {
    "direct_exposed": lambda p: _direct(p, dr.World(exposed=True), True),
    "direct_isolated_timeout": lambda p: _direct(p, dr.World(exposed=False), False),
    "direct_isolated_refused": lambda p: _direct(p, dr.World(exposed=False, blocked="refused"), False),
    "direct_isolated_dns_error": lambda p: _direct(p, dr.World(exposed=False, blocked="dns_error"), False),
    "direct_isolated_no_positive_control": lambda p: _direct(p, dr.World(exposed=False, trusted_reachable=False), False),
    "direct_exposed_mutating": lambda p: _direct(p, dr.World(exposed=True, engine_routes=SLEEP), True),
    "direct_isolated_mutating": lambda p: _direct(p, dr.World(exposed=False, engine_routes=SLEEP), False),
    "direct_exposed_engine_key": lambda p: _direct(p, dr.World(exposed=True, engine_key=dr.ALLOWED), True),
    "direct_extra_route": lambda p: _direct(p, dr.World(exposed=False, engine_routes={**dr.ENGINE_ROUTES, "/tokenize": "post"}), False),
    "multi_exposed": lambda p: _multi(p, True),
    "multi_isolated": lambda p: _multi(p, False),
    "role_enforced": lambda p: _role(p, rr.RoleWorld(enforced=True)),
    "role_open": lambda p: _role(p, rr.RoleWorld(enforced=False)),
    "role_unverified_version": lambda p: _role(p, rr.RoleWorld(enforced=True), version="0.30.0"),
}


def _all(tmp_path):
    out = {}
    for name, fn in SCENARIOS.items():
        d = tmp_path / name
        d.mkdir()
        out[name] = _draft_record(fn(d))
    return out


def test_http_executor_drafts_match_golden(tmp_path, monkeypatch):
    # The immutable PACK27 golden still checks the complete original payload.
    monkeypatch.setitem(globals(), "PACK", _pack27())
    current = _all(tmp_path)
    if os.environ.get("GUARD_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
        pytest.skip("golden rewritten")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert sorted(current) == sorted(golden)
    for name in golden:
        assert current[name] == golden[name], name


def test_explicit_comparison_conditions_produce_config_hash(tmp_path):
    from guard.models import canonical_json, sha256_hex
    target = dr._target(tmp_path, False)
    data = json.loads(target.read_text(encoding="utf-8"))
    data["comparison_config"] = {"profile":"synthetic explicit launch conditions"}
    target.write_text(json.dumps(data), encoding="utf-8")
    world = dr.World(exposed=False)
    executor = DirectRouteExecutor({"target":str(target), "roles":{"allowed":"KA"}},
        container_probe=world.container_probe, host_probe=world.host_probe, environ={"KA":dr.ALLOWED})
    draft = executor.run(PACK.checks["REQ-01"], environment(model={"id":"m"}),
                         {"allowed_callers":["allowed"]}, VllmAdapter(), tmp_path / "work")
    assert draft.comparability.config_sha256 == sha256_hex(canonical_json(draft.config))


def _pack27():
    import zipfile
    import yaml
    from guard.models.checkdef import build_pack
    archive_path = Path(__file__).parent / "fixtures/pack27-checks/source.zip"
    with zipfile.ZipFile(archive_path) as archive:
        manifest = yaml.safe_load(archive.read("pack.yaml"))
        files = {row["path"]: archive.read(row["path"]) for row in manifest["files_sha256"]}
    return build_pack(manifest, files)


def test_current_http_draft_changes_only_canonical_procedure_words(tmp_path):
    from copy import deepcopy
    prior = _pack27()
    old_steps = prior.checks["REQ-01"].challenge_steps
    new_steps = PACK.checks["REQ-01"].challenge_steps
    assert len(old_steps) == len(new_steps) == 3
    expected = deepcopy(json.loads(GOLDEN.read_text(encoding="utf-8")))
    expected_changes = 0
    for name, payload in expected.items():
        if name.startswith("role_"):
            continue
        for row in payload["challenges"]:
            assert row["step"] in old_steps
            row["step"] = new_steps[old_steps.index(row["step"])]
            expected_changes += 1
    assert expected_changes == 33
    current = _all(tmp_path)
    assert current == expected
