"""guard CLI (SPEC §10)."""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from guard.collectors.file import load_environment, load_policy
from guard.collectors.manual import ManualEvidenceError, build_template, dump_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select as select_plan
from guard.models import Pack, Plan, load_pack, utcnow
from guard.report.html import render_html
from guard.store import Store
from guard.store.packs import PackNotStoredError, get_pack, put_pack

app = typer.Typer(add_completion=False, no_args_is_help=True, help="서빙엔진 보안 검증")
pack_app = typer.Typer(add_completion=False, no_args_is_help=True, help="팩 보존본")
app.add_typer(pack_app, name="pack")
evidence_app = typer.Typer(add_completion=False, no_args_is_help=True, help="수동 증거 입력(DECLARED)")
app.add_typer(evidence_app, name="evidence")

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
    pack = load_pack(pack_dir or Path("packs") / plan.inputs.pack.id)
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
    rc = RunConfig(executors=cfg.get("executors", {}), adapter=(cfg.get("adapter") or {}).get("name"), raw=cfg)
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


@evidence_app.command("template")
def evidence_template(
    plan: str = typer.Option(..., "--plan"),
    out: Path = typer.Option(Path("manual-evidence.yaml"), "--out"),
    include_automated: bool = typer.Option(False, "--include-automated", help="자동 실행기가 있는 점검도(override_reason 필요)"),
    pack: Optional[Path] = typer.Option(None, "--pack"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """수동 입력이 필요한 점검의 빈 YAML 양식을 만든다."""
    st = _store(store, _config(config))
    pl, pk = _plan_and_pack(st, plan, pack, allow_snapshot=True)
    doc = build_template(st, pk, pl, include_automated=include_automated)
    out.write_text(dump_template(doc), encoding="utf-8")
    typer.echo(f"{len(doc['checks'])} entries → {out}")


@evidence_app.command("import")
def evidence_import(
    file: Path = typer.Argument(..., help="채운 양식 YAML (첨부 경로는 이 파일 기준)"),
    pack: Optional[Path] = typer.Option(None, "--pack"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """채운 양식을 전부 검증한 뒤 DECLARED 증거로 넣는다(하나라도 틀리면 아무것도 넣지 않음)."""
    import yaml as _yaml

    st = _store(store, _config(config))
    head = _yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    p = head.get("plan") or {}
    pl, pk = _plan_and_pack(st, f"{p.get('id')}@{p.get('revision')}", pack, allow_snapshot=True)
    try:
        result = import_evidence(st, pk, file)
    except ManualEvidenceError as exc:
        typer.echo(f"evidence import failed (nothing written): {exc}")
        raise typer.Exit(1)
    typer.echo(f"imported {len(result['imported'])}: {', '.join(result['imported']) or '-'}; "
               f"skipped (not filled) {len(result['skipped'])}")


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
