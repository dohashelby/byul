"""guard CLI (SPEC §10)."""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
import yaml

from guard.collectors.file import load_environment, load_policy
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select as select_plan
from guard.models import Pack, Plan, load_pack, utcnow
from guard.report.html import render_html
from guard.store import Store
from guard.store.packs import PackNotStoredError, get_pack, put_pack
from guard.application import execute_update, run_config
from guard.core.preflight import validate_pack
from guard.models import new_id

app = typer.Typer(add_completion=False, no_args_is_help=True, help="서빙엔진 보안 검증")
pack_app = typer.Typer(add_completion=False, no_args_is_help=True, help="팩 보존본")
app.add_typer(pack_app, name="pack")

ConfigOpt = typer.Option(Path("guard.toml"), "--config", help="플러그인 설정 파일")
StoreOpt = typer.Option(None, "--store", help="저장소 경로 (기본: guard.toml의 store 또는 .guard)")


def _config(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _store(store: Optional[Path], cfg: dict) -> Store:
    return Store(store or Path(cfg.get("store", ".guard")))


def _plan_and_pack(st: Store, plan_id: str, pack_dir: Optional[Path], allow_snapshot: bool = False) -> tuple[Plan, Pack]:
    """PLAN과 그 PLAN이 쓴 팩. 디스크의 팩이 PLAN의 팩 Ref와 다르면 run은 중단하고, judge·report는
    (allow_snapshot) 저장소의 팩 보존본을 읽는다. 보존본도 없으면 중단한다(팩 해시 검증은 양쪽 모두)."""
    pid, _, rev = plan_id.partition("@")
    plan = st.get(pid, int(rev)) if rev else st.latest(pid)
    if plan.kind != "PLAN":
        raise typer.BadParameter(f"{plan_id} is not a PLAN")
    try:
        pack = load_pack(pack_dir or Path("packs") / plan.inputs.pack.id)
    except (OSError, ValueError, yaml.YAMLError):
        if allow_snapshot:
            return plan, get_pack(st, plan.inputs.pack)
        raise
    if pack.ref() == plan.inputs.pack:
        return plan, pack
    msg = f"pack on disk differs from the one the plan used ({plan.inputs.pack.id} rev {plan.inputs.pack.revision})"
    if not allow_snapshot:
        raise typer.BadParameter(msg)
    try:
        return plan, get_pack(st, plan.inputs.pack)
    except PackNotStoredError as exc:
        raise typer.BadParameter(f"{msg}; no stored pack snapshot either ({exc})") from exc


def _as_of(value: Optional[str]) -> datetime:
    if not value:
        return utcnow()
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@app.command()
def select(
    pack: Path = typer.Option(..., "--pack"),
    env: Path = typer.Option(..., "--env"),
    policy: Path = typer.Option(..., "--policy"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """ENV + POL + 팩 → PLAN"""
    cfg = _config(config)
    st = _store(store, cfg)
    p = load_pack(pack)
    validate_pack(p)
    put_pack(st, pack)  # select 시점 팩 보존본(judge·report가 나중에 이 revision을 읽을 수 있게)
    e, pol = load_environment(env), load_policy(policy, p)
    st.put(e)
    st.put(pol)
    plan = select_plan(p, [e], pol)
    st.put(plan)
    selected = [i for i in plan.items if i.selected]
    auto = [i for i in selected if not i.gaps]
    typer.echo(f"{plan.id}@{plan.revision}  selected {len(selected)}/{len(plan.items)}, automatable {len(auto)}")
    typer.echo(plan.id)


@app.command()
def run(
    plan: str = typer.Option(..., "--plan", help="PLAN id (또는 id@revision)"),
    pack: Optional[Path] = typer.Option(None, "--pack"),
    fake_scenario: Optional[str] = typer.Option(None, "--fake-scenario", help="FakeExecutor 시나리오 덮어쓰기"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """PLAN → EVD (판정하지 않는다)"""
    cfg = _config(config)
    if fake_scenario:
        cfg.setdefault("fake", {})["scenario"] = fake_scenario
    st = _store(store, cfg)
    pl, pk = _plan_and_pack(st, plan, pack)
    rc = run_config(cfg)
    evds = run_plan(st, pk, pl, rc)
    counts: dict[str, int] = {}
    for e in evds:
        counts[e.execution.status.value] = counts.get(e.execution.status.value, 0) + 1
    typer.echo(f"{len(evds)} evidence: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for e in evds:
        if e.execution.status.value == "COMPLETED" or e.execution.status.value == "ABORTED":
            typer.echo(f"  {e.check_id} {e.id}@{e.revision} {e.execution.status.value} origin={e.data_origin.value}")


@app.command()
def judge(
    plan: str = typer.Option(..., "--plan"),
    pack: Optional[Path] = typer.Option(None, "--pack"),
    as_of: Optional[str] = typer.Option(None, "--as-of", help="예외 만료 판단 시각 (RFC3339, 기본 현재)"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """EVD → RES (재실행 = 재판정)"""
    cfg = _config(config)
    st = _store(store, cfg)
    pl, pk = _plan_and_pack(st, plan, pack, allow_snapshot=True)
    results = judge_plan(st, pk, pl, _as_of(as_of))
    counts: dict[str, int] = {}
    for r in results:
        counts[r.verdicts.security.value] = counts.get(r.verdicts.security.value, 0) + 1
    typer.echo(f"{len(results)} results (security): " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for r in results:
        if r.judge.name != "none":
            typer.echo(f"  {r.check_id} {r.id}@{r.revision} security={r.verdicts.security.value} "
                       f"functional={r.verdicts.functional.value} level={r.verification_level}")


@app.command()
def report(
    plan: str = typer.Option(..., "--plan"),
    pack: Optional[Path] = typer.Option(None, "--pack"),
    json_out: Path = typer.Option(Path("report.json"), "--json"),
    html_out: Optional[Path] = typer.Option(None, "--html"),
    as_of: Optional[str] = typer.Option(None, "--as-of"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """RES → RPT(JSON) → HTML"""
    cfg = _config(config)
    st = _store(store, cfg)
    pl, pk = _plan_and_pack(st, plan, pack, allow_snapshot=True)
    rpt = build_report(st, pk, pl, _as_of(as_of))
    st.put(rpt)
    data = rpt.to_dict()
    json_out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"{rpt.id}@{rpt.revision} official={rpt.official} → {json_out}")
    if html_out:
        html_out.write_text(render_html(json.loads(json_out.read_text(encoding="utf-8"))), encoding="utf-8")
        typer.echo(f"  html → {html_out}")


@app.command()
def update(
    pack: Path = typer.Option(..., "--pack"),
    baseline: Path = typer.Option(..., "--baseline"),
    candidate: Path = typer.Option(..., "--candidate"),
    policy: Path = typer.Option(..., "--policy"),
    output: Path = typer.Option(Path("out/updates"), "--output"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """Two environments → side-specific execution → JSON/HTML comparison."""
    cfg = _config(config)
    pk = load_pack(pack)
    validate_pack(pk)
    envs = [load_environment(baseline), load_environment(candidate)]
    pol = load_policy(policy, pk)
    folder = output / new_id("UPDATE")
    rpt, failed = execute_update(_store(store, cfg), pk, envs, pol, cfg, folder, utcnow())
    data = rpt.to_dict()
    (folder / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "report.html").write_text(render_html(data), encoding="utf-8")
    typer.echo(f"{rpt.id} official={rpt.official} → {folder}")
    raise typer.Exit(1 if failed else 0)


@pack_app.command("import")
def pack_import(
    pack: Path = typer.Option(..., "--pack", help="팩 폴더(예: git archive로 꺼낸 과거 revision)"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """팩 폴더를 해시 검증한 뒤 저장소에 보존본으로 넣는다(과거 PLAN 재판정용)."""
    st = _store(store, _config(config))
    try:
        ref = put_pack(st, pack)
    except Exception as exc:  # noqa: BLE001 - 사용자에게 검증 실패를 보여준다
        typer.echo(f"pack import failed: {exc}")
        raise typer.Exit(1)
    typer.echo(f"stored {ref.id} rev {ref.revision} sha256 {ref.sha256[:16]}… in {st.root}")


@app.command()
def verify(config: Path = ConfigOpt, store: Optional[Path] = StoreOpt) -> None:
    """저장소 해시·참조·증거 파일 일괄 검증"""
    st = _store(store, _config(config))
    problems = st.verify()
    for p in problems:
        typer.echo(p)
    typer.echo("OK" if not problems else f"{len(problems)} problem(s)")
    raise typer.Exit(1 if problems else 0)


if __name__ == "__main__":
    app()
