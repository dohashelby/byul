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
from guard.collectors.change import read_change, resolve_plan, save_change, extracted_input
from guard.collectors.updates import update_object
from guard.core.change_selection import build_context
from guard.collectors.manual import ManualEvidenceError, build_template, dump_template, import_evidence, read_document
from guard.collectors.policy_template import write_policy_template
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select as select_plan
from guard.models import Pack, Plan, load_pack, utcnow
from guard.store import Store
from guard.store import IntegrityError
from guard.store.packs import PackNotStoredError, get_pack, put_pack
from guard.application import execute_update, execute_validation, run_config, update_readiness, validation_readiness
from guard.store.bundle import export_bundle, restore_bundle, DEFAULT_MAX_BYTES
from guard.core.preflight import validate_pack
from guard.models import new_id
from guard.preparation_guidance import annotate, input_failure, read_inputs

app = typer.Typer(add_completion=False, no_args_is_help=True, help="서빙엔진 보안 검증")
pack_app = typer.Typer(add_completion=False, no_args_is_help=True, help="팩 보존본")
app.add_typer(pack_app, name="pack")
evidence_app = typer.Typer(add_completion=False, no_args_is_help=True, help="수동 증거 입력·저장소 반출·복원")
app.add_typer(evidence_app, name="evidence")
policy_app = typer.Typer(add_completion=False, no_args_is_help=True, help="기업 정책 입력 양식")
app.add_typer(policy_app, name="policy")
env_app = typer.Typer(add_completion=False, no_args_is_help=True, help="환경 입력 갱신")
app.add_typer(env_app, name="env")

ConfigOpt = typer.Option(Path("guard.toml"), "--config", help="플러그인 설정 파일")
StoreOpt = typer.Option(None, "--store", help="저장소 경로 (기본: guard.toml의 store 또는 .guard)")


def _config(path: Path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _store(store: Optional[Path], cfg: dict) -> Store:
    return Store(store or Path(cfg.get("store", ".guard")))


def _change_inputs(value, previous_plan, pack, cfg, store):
    """검증 실패 시 새 저장소·실행 출력 없이 반환한다."""
    root = store or Path(cfg.get("store", ".guard"))
    needs_store = previous_plan is not None or (value is not None and not Path(value).is_file())
    source = Store.read_only(root) if needs_store else None
    return read_change(value, pack, source), resolve_plan(source, previous_plan), source


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


def _update_input(kind, base, patch, config, store, out):
    if out is not None and (out.exists() or not out.parent.is_dir()):
        raise typer.BadParameter("--out must be a new file in an existing directory")
    cfg = _config(config)
    obj = update_object(_store(store, cfg), base, patch, kind=kind)
    if out is not None:
        with out.open("x", encoding="utf-8") as stream:
            yaml.safe_dump(obj.to_dict(), stream, allow_unicode=True, sort_keys=False)
    typer.echo(f"{obj.id}@{obj.revision} sha256={obj.content_sha256}")


@env_app.command("update")
def env_update(base: str = typer.Option(..., "--base"), patch: Path = typer.Option(..., "--patch"),
               out: Optional[Path] = typer.Option(None, "--out"), config: Path = ConfigOpt,
               store: Optional[Path] = StoreOpt):
    """최신 ENV에 부분 갱신을 적용해 같은 ID의 새 revision을 저장한다."""
    _update_input("ENV", base, patch, config, store, out)


@policy_app.command("update")
def policy_update(base: str = typer.Option(..., "--base"), patch: Path = typer.Option(..., "--patch"),
                  out: Optional[Path] = typer.Option(None, "--out"), config: Path = ConfigOpt,
                  store: Optional[Path] = StoreOpt):
    """최신 POL에 부분 갱신을 적용해 같은 ID의 새 revision을 저장한다."""
    _update_input("POL", base, patch, config, store, out)


@app.command("diff")
def engine_diff(baseline: str = typer.Option(..., "--from"), candidate: str = typer.Option(..., "--to"),
                repository: Optional[Path] = typer.Option(None, "--repository", help="기존 git 저장소; 지정 시 fetch하지 않음"),
                pack: Path = typer.Option(Path("packs/PACK-BASELINE"), "--pack"),
                config: Path = ConfigOpt, store: Optional[Path] = StoreOpt):
    """엔진 소스의 원본 바이트와 경로 규칙에서 CHG를 만든다. 엔진을 실행하지 않는다."""
    from guard.adapters.git_diff import extract_git_change, prepare_source_repository
    cfg = _config(config)
    pk = load_pack(pack)
    options = cfg.get("engine_source", {})
    if repository is None:
        repository = prepare_source_repository(options)
    extracted = extract_git_change(repository, baseline, candidate, pack=pk)
    saved = save_change(_store(store, cfg), extracted_input(extracted, pk))
    typer.echo(f"{saved.id}@{saved.revision} sha256={saved.content_sha256}")
    typer.echo(json.dumps(saved.analysis or {}, ensure_ascii=False, sort_keys=True))


@app.command("model-diff")
def model_diff(baseline: Path = typer.Option(..., "--from"), candidate: Path = typer.Option(..., "--to"),
               baseline_id: Optional[str] = typer.Option(None, "--baseline-id"),
               candidate_id: Optional[str] = typer.Option(None, "--candidate-id"),
               pack: Path = typer.Option(Path("packs/PACK-BASELINE"), "--pack"),
               config: Path = ConfigOpt, store: Optional[Path] = StoreOpt):
    """로컬 snapshot/manifest의 변경을 CHG로 보존한다. 모델 코드를 실행하지 않는다."""
    from guard.collectors.model_manifest import extract_model_change
    cfg, pk = _config(config), load_pack(pack)
    extracted = extract_model_change(baseline, candidate, baseline_id=baseline_id, candidate_id=candidate_id, pack=pk)
    saved = save_change(_store(store, cfg), extracted_input(extracted, pk))
    typer.echo(f"{saved.id}@{saved.revision} sha256={saved.content_sha256}")
    typer.echo(json.dumps(saved.analysis or {}, ensure_ascii=False, sort_keys=True))


@app.command()
def select(
    pack: Path = typer.Option(..., "--pack"),
    env: Path = typer.Option(..., "--env"),
    policy: Path = typer.Option(..., "--policy"),
    change: Optional[str] = typer.Option(None, "--change", help="CHG YAML/JSON 또는 저장 CHG Ref"),
    previous_plan: Optional[str] = typer.Option(None, "--previous-plan"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """ENV + POL + 팩 → PLAN"""
    cfg = _config(config)
    p = load_pack(pack)
    validate_pack(p)
    e, pol = load_environment(env), load_policy(policy, p)
    change_input, previous, source = _change_inputs(change, previous_plan, p, cfg, store)
    if change_input is not None or previous is not None:
        build_context(source, previous, p, [e], pol, cfg, change_input.change if change_input else None, read_only=True)
    st = _store(store, cfg)
    put_pack(st, pack)  # select 시점 팩 보존본
    st.put(e)
    st.put(pol)
    chg = save_change(st, change_input) if change_input is not None else None
    context = build_context(st, previous, p, [e], pol, cfg, chg) if chg is not None or previous is not None else None
    plan = select_plan(p, [e], pol, change=chg, context=context) if context is not None else select_plan(p, [e], pol)
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
    as_of: Optional[str] = typer.Option(None, "--as-of"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """RES → RPT(JSON). 화면은 byul platform이 RPT JSON을 읽어 보여준다."""
    cfg = _config(config)
    st = _store(store, cfg)
    pl, pk = _plan_and_pack(st, plan, pack, allow_snapshot=True)
    rpt = build_report(st, pk, pl, _as_of(as_of))
    st.put(rpt)
    data = rpt.to_dict()
    json_out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"{rpt.id}@{rpt.revision} official={rpt.official} → {json_out}")


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
    change: Optional[str] = typer.Option(None, "--change"),
    previous_plan: Optional[str] = typer.Option(None, "--previous-plan"),
    output: Path = typer.Option(Path("out/updates"), "--output"),
    as_of: Optional[str] = typer.Option(None, "--as-of", help="예외 만료 판단 시각 (RFC3339, 기본 현재)"),
    config: Path = ConfigOpt,
    store: Optional[Path] = StoreOpt,
) -> None:
    """Two environments → side-specific execution → JSON comparison."""
    cfg = _config(config)
    pk = load_pack(pack)
    validate_pack(pk)
    envs = [load_environment(baseline), load_environment(candidate)]
    pol = load_policy(policy, pk)
    change_input, previous, source = _change_inputs(change, previous_plan, pk, cfg, store)
    if change_input is not None or previous is not None:
        build_context(source, previous, pk, envs, pol, cfg, change_input.change if change_input else None,
                      mode="update", read_only=True)
    folder = output / new_id("UPDATE")
    judged_at = _as_of(as_of)
    extra = {"change": change_input, "previous_plan": previous} if change_input is not None or previous is not None else {}
    rpt, failed = execute_update(_store(store, cfg), pk, envs, pol, cfg, folder, judged_at, **extra)
    data = rpt.to_dict()
    (folder / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
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
    loaded, issues = read_inputs({"pack": pack, "single": env, "policy": policy, "config": config},
        pack_loader=load_pack, environment_loader=load_environment, policy_loader=load_policy, config_loader=_config)
    try:
        data = input_failure(issues) if issues else annotate(validation_readiness(
            loaded["pack"], loaded["single"], loaded["policy"], loaded["config"]))
    except Exception as exc:
        data = annotate({"ready": False, "checks": [{"check": "input-files", "ok": False,
                "detail": "preparation failed; verify file paths, schema and pack hashes"}]})
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))
    raise typer.Exit(0 if data["ready"] else 1)


@app.command("prepare-update")
def prepare_update(
    pack: Path = typer.Option(..., "--pack"),
    baseline: Path = typer.Option(..., "--baseline"),
    candidate: Path = typer.Option(..., "--candidate"),
    policy: Path = typer.Option(..., "--policy"),
    change: Optional[str] = typer.Option(None, "--change"),
    previous_plan: Optional[str] = typer.Option(None, "--previous-plan"),
    store: Optional[Path] = StoreOpt,
    config: Path = ConfigOpt,
) -> None:
    """입력·Docker·로컬 이미지·모델 사전 검사. 엔진/자원/저장소를 만들지 않는다."""
    loaded, issues = read_inputs({"pack": pack, "baseline": baseline, "candidate": candidate,
        "policy": policy, "config": config}, pack_loader=load_pack, environment_loader=load_environment,
        policy_loader=load_policy, config_loader=_config)
    try:
        extra = {}
        if not issues and (change is not None or previous_plan is not None):
            ci, previous, source = _change_inputs(change, previous_plan, loaded["pack"], loaded["config"], store)
            extra = {"change": ci, "previous_plan": previous, "source_store": source}
        data = input_failure(issues) if issues else annotate(update_readiness(loaded["pack"],
            [loaded["baseline"], loaded["candidate"]], loaded["policy"], loaded["config"], **extra))
    except Exception as exc:
        data = annotate({"ready": False, "checks": [{"check": "input-files", "ok": False,
                "detail": "preparation failed; verify file paths, schema and pack hashes"}]})
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


@app.command("capture-model-authorization")
def capture_model_authorization(
    pack: Path = typer.Option(..., "--pack"),
    env: Path = typer.Option(..., "--env"),
    policy: Path = typer.Option(..., "--policy"),
    context: Path = typer.Option(..., "--context"),
    target: Path = typer.Option(..., "--target"),
    gateway_container: str = typer.Option(..., "--gateway-container"),
    gateway_image: str = typer.Option(..., "--gateway-image"),
    gateway_path: str = typer.Option(..., "--gateway-path"),
    output: Path = typer.Option(..., "--output"),
    execute: bool = typer.Option(False, "--execute", help="소유 파일 읽기만 수행; 엔진 기동·요청·판정 없음"),
) -> None:
    """REQ-05 고정 모델의 Gateway 기록을 수집한다. 최종 모델 생산자 미확인으로 판정은 UNKNOWN."""
    from guard.collectors.model_authorization import capture, load_inputs
    from guard.runtime.docker import Docker, DockerError
    source = {"container": gateway_container, "image": gateway_image, "path": gateway_path}
    try:
        environment, pol, ledger, topology = load_inputs(pack, env, policy, context, target, source)
        repo = Path(__file__).resolve().parents[3]
        if (output.exists() or output.is_symlink() or not output.parent.is_dir()
                or output.resolve().is_relative_to(repo)):
            raise ValueError("MODEL_AUTHORIZATION_OUTPUT_INVALID")
        if not execute:
            typer.echo(json.dumps({"capture_executed": False, "engine_started": False,
                "ready_to_execute": False, "product_connected": False, "security": "UNKNOWN",
                "pending": ["OWNED_CAPTURE_NOT_EXECUTED", "FINAL_ENGINE_MODEL_PRODUCER_UNAVAILABLE"]}))
            return
        result = capture(Docker(topology["run_id"]), environment, pol, ledger, topology, source)
        raw = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        with output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError, DockerError):
        typer.echo("MODEL_AUTHORIZATION_CAPTURE_FAILED")
        raise typer.Exit(2)
    typer.echo(json.dumps({"capture_executed": True, "engine_started": False,
                          "product_connected": False, "security": "UNKNOWN"}))


if __name__ == "__main__":
    app()
