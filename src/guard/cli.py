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
from guard.collectors.manual import ManualEvidenceError, build_template, dump_template, import_evidence, read_document
from guard.collectors.policy_template import write_policy_template
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select as select_plan
from guard.models import Pack, Plan, load_pack, utcnow
from guard.report.html import render_html
from guard.store import Store
from guard.store import IntegrityError
from guard.store.packs import PackNotStoredError, get_pack, put_pack
from guard.application import execute_update, execute_validation, run_config, update_readiness, validation_readiness
from guard.store.bundle import export_bundle, restore_bundle, DEFAULT_MAX_BYTES
from guard.core.preflight import validate_pack
from guard.models import new_id

app = typer.Typer(add_completion=False, no_args_is_help=True, help="서빙엔진 보안 검증")
pack_app = typer.Typer(add_completion=False, no_args_is_help=True, help="팩 보존본")
app.add_typer(pack_app, name="pack")
evidence_app = typer.Typer(add_completion=False, no_args_is_help=True, help="수동 증거 입력·저장소 반출·복원")
app.add_typer(evidence_app, name="evidence")
policy_app = typer.Typer(add_completion=False, no_args_is_help=True, help="기업 정책 입력 양식")
app.add_typer(policy_app, name="policy")

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


@policy_app.command("template")
def policy_template(
    pack: Path = typer.Option(..., "--pack", help="정책 파라미터를 읽을 팩 경로"),
    out: Path = typer.Option(..., "--out", help="새 정책 YAML 경로 (기존 파일 덮어쓰기 금지)"),
) -> None:
    """팩에 선언된 정책 키의 설명과 빈 values를 생성한다. 시험·저장소 생성 없음."""
    try:
        write_policy_template(load_pack(pack), out)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        typer.echo(f"정책 양식 생성 실패: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"DRAFT 정책 양식 생성: {out}")


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
    replace_manual: bool = typer.Option(False, "--replace-manual", help="수동 증거를 새 실행 revision으로 명시적으로 대체"),
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
    rc.replace_manual = replace_manual
    evds = run_plan(st, pk, pl, rc)
    counts: dict[str, int] = {}
    for e in evds:
        counts[e.execution.status.value] = counts.get(e.execution.status.value, 0) + 1
    typer.echo(f"{len(evds)} evidence: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    for e in evds:
        if e.execution.status.value == "COMPLETED" or e.execution.status.value == "ABORTED":
            typer.echo(f"  {e.check_id} {e.id}@{e.revision} {e.execution.status.value} origin={e.data_origin.value}")
    if any(e.execution.cleanup_errors for e in evds):
        typer.echo("probe cleanup incomplete; inspect execution.cleanup_errors and labelled resources")
        raise typer.Exit(1)


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
    """양식·첨부·초안 검증 후 DECLARED 증거 객체를 한 배치로 저장한다."""
    st = _store(store, _config(config))
    try:
        head = read_document(file)
        p = head["plan"]
        pl, pk = _plan_and_pack(st, f"{p['id']}@{p['revision']}", pack, allow_snapshot=True)
        result = import_evidence(st, pk, file, document=head)
    except (ManualEvidenceError, ValueError, KeyError, OSError, IntegrityError) as exc:
        detail = str(exc) if isinstance(exc, ManualEvidenceError) else type(exc).__name__
        typer.echo(f"evidence import failed (no evidence objects imported): {detail}")
        raise typer.Exit(1)
    typer.echo(f"imported {len(result['imported'])}: {', '.join(result['imported']) or '-'}; "
               f"skipped (not filled) {len(result['skipped'])}")


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


@app.command("validate")
def validate(
    pack: Path = typer.Option(..., "--pack"),
    env: Path = typer.Option(..., "--env"),
    policy: Path = typer.Option(..., "--policy"),
    output: Path = typer.Option(Path("out/validations"), "--output"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """단일 managed 엔진 기동 → 실제 점검 → 보고서·정리. canary는 DRAFT 시험용 무해 표식이다."""
    cfg = _config(config)
    pk = load_pack(pack)
    environment = load_environment(env)
    pol = load_policy(policy, pk)
    folder = output / new_id("VALIDATE")
    rpt, failed = execute_validation(_store(store, cfg), pk, environment, pol, cfg, folder, utcnow())
    data = rpt.to_dict()
    (folder / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "report.html").write_text(render_html(data), encoding="utf-8")
    typer.echo(f"{rpt.id} official={rpt.official} → {folder}")
    raise typer.Exit(1 if failed else 0)


@app.command("prepare-validation")
def prepare_validation(
    pack: Path = typer.Option(..., "--pack"),
    env: Path = typer.Option(..., "--env"),
    policy: Path = typer.Option(..., "--policy"),
    config: Path = ConfigOpt,
) -> None:
    """단일 환경의 읽기 전용 준비 검사. 기동·pull·저장을 하지 않는다."""
    try:
        pk = load_pack(pack)
        data = validation_readiness(pk, load_environment(env), load_policy(policy, pk), _config(config))
    except Exception as exc:
        data = {"ready": False, "checks": [{"check": "input-files", "ok": False,
                "detail": f"input loading failed ({type(exc).__name__}); verify file paths, schema and pack hashes"}]}
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    raise typer.Exit(0 if data["ready"] else 1)


@app.command("prepare-update")
def prepare_update(
    pack: Path = typer.Option(..., "--pack"),
    baseline: Path = typer.Option(..., "--baseline"),
    candidate: Path = typer.Option(..., "--candidate"),
    policy: Path = typer.Option(..., "--policy"),
    config: Path = ConfigOpt,
) -> None:
    """입력·Docker·로컬 이미지·모델 사전 검사. 엔진/자원/저장소를 만들지 않는다."""
    try:
        pk = load_pack(pack)
        data = update_readiness(pk, [load_environment(baseline), load_environment(candidate)],
                                load_policy(policy, pk), _config(config))
    except Exception as exc:
        data = {"ready": False, "checks": [{"check": "input-files", "ok": False,
                "detail": f"input loading failed ({type(exc).__name__}); verify file paths, schema and pack hashes"}]}
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    raise typer.Exit(0 if data["ready"] else 1)


@evidence_app.command("export")
def evidence_export(store: Path = typer.Option(..., "--store"), output: Path = typer.Option(..., "--output")) -> None:
    """원본을 변경하지 않고 DB·artifacts·팩 보존본만 검증해 zip으로 반출."""
    try:
        manifest = export_bundle(store, output)
    except Exception as exc:
        typer.echo(f"export failed ({type(exc).__name__}): {exc}")
        raise typer.Exit(1)
    typer.echo(f"exported {len(manifest['files'])} verified files → {output}")


@evidence_app.command("restore")
def evidence_restore(bundle: Path = typer.Option(..., "--bundle"), destination: Path = typer.Option(..., "--destination"),
                     max_bytes: int = typer.Option(DEFAULT_MAX_BYTES, "--max-bytes", min=1)) -> None:
    """새 경로에만 복원. 경로·용량·해시·객체 참조·팩 보존본 검증 후 공개."""
    try:
        manifest = restore_bundle(bundle, destination, max_bytes=max_bytes)
    except Exception as exc:
        typer.echo(f"restore failed ({type(exc).__name__}): {exc}")
        raise typer.Exit(1)
    typer.echo(f"restored {len(manifest['files'])} verified files → {destination}")


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
