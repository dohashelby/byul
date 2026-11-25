import { useEffect, useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useDocList, useDocRevisions, useEnvironmentRevision, usePolicyRevision } from "../api/hooks";
import { useExecuteUpdate, usePrepareUpdate, useUpdateExecutionSettings, useUpdateRun, useUpdateRuns,
  type UpdatePreparation, type UpdateRun, type UpdateRunRequest } from "../api/updateExecution";
import { Badge } from "../components/Badges";
import { JsonView } from "../components/JsonView";
import { PageHead, Query, StateView } from "../components/Misc";
import { AnyValue, Time } from "../components/Value";
import { useParams } from "../lib/url";
import { reportSearch } from "../lib/reportRef";
import type { Tone } from "../lib/labels";
import s from "./UpdateExecutionPage.module.css";

const STATUS: Record<UpdateRun["status"], { label: string; tone: Tone }> = {
  preparing: { label: "준비 점검 중", tone: "neutral" }, ready: { label: "실행 준비됨", tone: "neutral" },
  blocked: { label: "준비 보완 필요", tone: "caution" }, running: { label: "검증 실행 중", tone: "neutral" },
  completed: { label: "실행 완료", tone: "neutral" }, failed: { label: "실행 오류", tone: "error" },
  interrupted: { label: "중단 · 확인 필요", tone: "caution" },
};

type DraftRef = { id: string; revision: number | null };
type Form = { mode: "validate" | "update"; policy: DraftRef; environment: DraftRef; candidate: DraftRef };
const EMPTY: Form = { mode: "validate", policy: { id: "", revision: null }, environment: { id: "", revision: null }, candidate: { id: "", revision: null } };

function formOf(request: UpdateRun["request"]): Form | null {
  if (!("policy" in request)) return null;
  const environment = request.mode === "update" ? request.baseline : request.environment;
  return { mode: request.mode ?? "validate", policy: request.policy,
    environment: environment ?? EMPTY.environment, candidate: request.candidate ?? EMPTY.candidate };
}

function requestOf(form: Form): UpdateRunRequest | null {
  const complete = (value: DraftRef): value is { id: string; revision: number } => !!value.id && value.revision != null;
  if (!complete(form.policy) || !complete(form.environment) || form.mode === "update" && !complete(form.candidate)) return null;
  return { mode: form.mode, policy: form.policy, ...(form.mode === "validate" ? { environment: form.environment }
    : { baseline: form.environment, candidate: form.candidate as { id: string; revision: number } }) };
}

function Status({ run }: { run: UpdateRun }) {
  return <Badge tone={STATUS[run.status].tone}>{STATUS[run.status].label}</Badge>;
}

function ReportLinks({ run, label }: { run: UpdateRun; label: string }) {
  const refs = run.report_refs?.length ? run.report_refs : run.report_ids ?? [];
  return refs.length ? refs.map((ref) => {
    const query = reportSearch(ref);
    return <Link key={query} className="btn" to={`/update/results${query}`}>{label}</Link>;
  }) : null;
}

export function UpdateExecutionPage() {
  const settings = useUpdateExecutionSettings();
  const runs = useUpdateRuns();
  const [params, patch] = useParams();
  const selectedId = params.get("run");
  const selected = useUpdateRun(selectedId);
  const prepare = usePrepareUpdate();
  const execute = useExecuteUpdate();
  const client = useQueryClient();
  const policies = useDocList("policies");
  const environments = useDocList("environments");
  const [form, setForm] = useState<Form>(EMPTY);
  const policy = usePolicyRevision(form.policy.id || null, form.policy.revision);
  const environment = useEnvironmentRevision(form.environment.id || null, form.environment.revision);
  const [message, setMessage] = useState<string | null>(null);
  const run = selected.data;
  // Restore the exact prepared inputs on reload/history selection, without resetting edits on polling.
  const loadedId = run?.id;
  useEffect(() => {
    if (run) { const restored = formOf(run.request); if (restored) setForm(restored); }
    setMessage(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedId]);
  useEffect(() => {
    if (run && ["completed", "failed"].includes(run.status)) {
      void client.invalidateQueries({ queryKey: ["update", "results"] });
      void client.invalidateQueries({ queryKey: ["meta"] });
    }
  }, [client, run?.id, run?.status]);
  useEffect(() => {
    const first = policies.data?.items[0];
    if (!form.policy.id && first) setForm((current) => ({ ...current, policy: { id: first.doc_id, revision: first.revision } }));
  }, [form.policy.id, policies.data]);
  useEffect(() => {
    const ref = policy.data?.document.environment_ref;
    if (ref) setForm((current) => current.environment.id === ref.id && current.environment.revision === ref.revision ? current
      : { ...current, environment: { id: ref.id, revision: ref.revision } });
  }, [policy.data?.meta.doc_id, policy.data?.meta.revision, policy.data?.meta.sha256]);

  const busy = (runs.data?.items ?? []).some((item) => ["preparing", "running"].includes(item.status))
    || !!run && ["preparing", "running"].includes(run.status) || prepare.isPending || execute.isPending;
  const allowed = !!settings.data?.available && !!settings.data.can_execute;
  const selectable = !!settings.data?.can_execute && !busy;
  const restored = run ? formOf(run.request) : null;
  const selection = requestOf(form);
  const sameInputs = !!restored && !!selection && JSON.stringify(selection) === JSON.stringify(requestOf(restored));
  const expected = policy.data?.document.environment_ref;
  const bindingMismatch = !!expected && !!environment.data && (expected.id !== environment.data.meta.doc_id
    || expected.revision !== environment.data.meta.revision || expected.sha256 !== environment.data.meta.sha256);
  const sameSides = form.mode === "update" && form.environment.id === form.candidate.id && form.environment.revision === form.candidate.revision;
  const canPrepare = allowed && !busy && !!selection && !!policy.data && !!environment.data && !bindingMismatch && !sameSides;
  const canRun = allowed && !busy && run?.status === "ready" && sameInputs;

  async function check(event: FormEvent) {
    event.preventDefault();
    if (!selection || !canPrepare) return;
    setMessage(null);
    try {
      const created = await prepare.mutateAsync(selection);
      const restored = formOf(created.request);
      if (restored) setForm(restored);
      patch({ run: created.id });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "준비 점검을 시작하지 못했습니다");
    }
  }

  async function start() {
    if (!run || !canRun) return;
    setMessage(null);
    try { await execute.mutateAsync(run.id); }
    catch (error) { setMessage(error instanceof Error ? error.message : "검증을 시작하지 못했습니다"); }
  }

  return <div className="page">
    <PageHead title="검증 실행"><Link className="btn" to="/update/results">검증 결과 보기</Link></PageHead>
    <p className="notice small">플랫폼에 등록한 환경과 정책의 판본을 선택하세요. 준비 점검 후 검증을 실행하며, 화면을 이동해도 계속됩니다.</p>
    <Query q={settings}>{(config) => <>
      {!config.available && <div className={s.notice} role="alert">{config.unavailable_reason}</div>}
      {!config.can_execute && <div className={s.notice}>관리자 계정으로 검증을 실행할 수 있습니다. 실행 기록은 조회할 수 있습니다.</div>}
      <div className={s.workspace}>
        <form className="card card-pad stack" onSubmit={check} aria-label="검증 입력">
          <div className={s.sectionTitle}><span className={s.number}>1</span><h3>검증 대상 선택</h3></div>
          <div className={s.modes} role="group" aria-label="검증 방식">
            {([ ["validate", "단일 환경 검증", "관리형 엔진 한 개를 기동해 점검"], ["update", "두 판본 비교", "기준·후보 환경을 각각 점검하고 비교"] ] as const).map(([mode, title, description]) =>
              <button key={mode} type="button" className={s.mode} aria-pressed={form.mode === mode} disabled={!selectable}
                onClick={() => setForm({ ...form, mode })}><strong>{title}</strong><span>{description}</span></button>)}
          </div>
          <div className={s.fields}>
            <DocumentPicker kind="policies" label="검증 정책" value={form.policy} disabled={!selectable}
              onChange={(value) => setForm((current) => ({ ...current, policy: value }))} />
            <DocumentPicker kind="environments" label={form.mode === "validate" ? "검증 환경" : "기준 환경"} value={form.environment} disabled={!selectable}
              onChange={(value) => setForm((current) => ({ ...current, environment: value }))} />
            {form.mode === "update" && <DocumentPicker kind="environments" label="후보 환경" value={form.candidate} disabled={!selectable}
              onChange={(value) => setForm((current) => ({ ...current, candidate: value }))} />}
          </div>
          {policy.isError && <div className={s.error}>정책 판본을 불러오지 못했습니다. 등록한 판본을 다시 선택하세요.</div>}
          {environment.isError && <div className={s.error}>환경 판본을 불러오지 못했습니다. 등록한 판본을 다시 선택하세요.</div>}
          {bindingMismatch && <div className={s.notice} role="alert">이 정책의 기준 환경은 {expected!.id} · 판본 {expected!.revision}입니다. 해당 환경 판본을 선택하세요.</div>}
          {sameSides && <div className={s.notice} role="alert">기준과 후보는 서로 다른 환경 판본을 선택하세요.</div>}
          {environment.data && <p className="small muted">{environment.data.document.target.engine.name} · {environment.data.document.target.engine.version ?? "판본 미입력"} · 선택한 판본을 고정해 검증합니다.</p>}
          <p className="small muted">정책의 기준 환경을 기본으로 선택합니다. 검증 기준과 실행 설정은 서버에서 자동으로 적용합니다.
            {form.mode === "update" && " 기준 환경의 정책을 두 환경에 동일하게 적용해 비교합니다."}</p>
          {(!environments.data?.items.length || !policies.data?.items.length) && !environments.isPending && !policies.isPending &&
            <p className="small"><Link to="/environments">환경 등록</Link> · <Link to="/policies">정책 등록</Link> 후 검증할 수 있습니다.</p>}
          {config.temporary && <p className="small muted">현재 임시 저장 모드입니다. 서버 종료 시 입력과 실행 기록이 삭제됩니다.</p>}
          <div className="row wrap">
            <button className="btn btn-primary" type="submit" disabled={!canPrepare}>{prepare.isPending ? "준비 점검 요청 중…" : "준비 점검"}</button>
            <span className="small muted">준비 점검은 엔진을 기동하지 않습니다.</span>
          </div>
          {message && <div className={s.error} role="alert">{message}</div>}
        </form>
        <section className="card card-pad stack" aria-label="실행 상태">
          <div className={s.sectionTitle}><span className={s.number}>2</span><h3>준비 및 실행</h3>{run && <Status run={run} />}</div>
          {!selectedId ? <div className={s.empty}>입력 후 ‘준비 점검’을 누르거나 아래 실행 기록을 선택하세요.</div> :
            <Query q={selected}>{(current) => <>
              <div className={s.runId}>{current.id}</div>
              <div className="small muted">준비 요청 <Time iso={current.created_at} /> · {current.requested_by}</div>
              {current.preparation && <Preparation result={current.preparation} />}
              {!!current.required_checks?.length && <p className="small">모든 측의 필수 확인 범위 {current.required_checks.join(", ")}</p>}
              {current.status === "preparing" && <p role="status">입력·호스트·점검별 지원 상태를 확인하고 있습니다.</p>}
              {current.status === "ready" && restored && <p className="small">준비 조건이 확인되었습니다. ‘검증 실행’을 누르면 선택한 환경에 대한 검증을 시작합니다.</p>}
              {!restored && <p className="small">이전 파일 입력 기록입니다. 등록한 환경·정책을 선택해 준비 점검을 다시 진행하세요.</p>}
              {current.status === "running" && <p role="status">검증을 실행하고 있습니다. 종료와 자원 정리가 끝나면 보고서를 확인할 수 있습니다.</p>}
              {current.status === "completed" && <p className="small">명령 처리가 완료되었습니다. 보안 판정과 미확인 항목은 보고서에서 확인하세요.</p>}
              {current.error && <div className={s.error} role="alert">{current.error}</div>}
              {current.started_at && <div className="small muted">실행 시작 <Time iso={current.started_at} /> · {current.executed_by}</div>}
              {current.finished_at && <div className="small muted">최근 처리 종료 <Time iso={current.finished_at} />{current.exit_code != null && ` · 종료 코드 ${current.exit_code}`}</div>}
              <div className="row wrap">
                <button type="button" className="btn btn-primary" onClick={() => void start()} disabled={!canRun}>검증 실행</button>
                <ReportLinks run={current} label="생성된 보고서 보기" />
              </div>
              {!sameInputs && current.status === "ready" && <span className="small muted">입력이 바뀌었습니다. 준비 점검을 다시 진행하세요.</span>}
              <p className="small muted">준비 완료·명령 종료는 보안 PASS나 기업 승인이 아닙니다. 검증 결과는 update가 생성한 보고서를 따릅니다.</p>
              <details className="small"><summary>선택한 판본 확인</summary><JsonView value={{ selection: current.request, source_refs: current.source_refs, pack: current.pack }} /><p className={s.runId}>입력 SHA-256 {current.input_sha256}</p></details>
            </>}</Query>}
        </section>
      </div>
    </>}</Query>
    {run && <section className="card card-pad stack" aria-label="검증 실행 로그">
      <div className={s.sectionTitle}><h3>실행 로그</h3><span className="small muted">{["preparing", "running"].includes(run.status) ? "자동 갱신 중" : "마지막 기록"}</span></div>
      <pre className={s.log} tabIndex={0}>{run.log_tail || "명령 출력을 기다리고 있습니다."}</pre>
      {run.log_truncated && <span className="small muted">로그가 길어 마지막 64 KiB만 표시합니다.</span>}
    </section>}
    <section className="card card-pad stack" aria-label="검증 실행 기록">
      <div className={s.sectionTitle}><span className={s.number}>3</span><h3>실행 기록</h3><span className="small muted">최근 50건</span></div>
      <Query q={runs}>{(history) => history.items.length ? <div className={s.tableWrap}><table className={s.table}>
        <thead><tr><th>요청 시각</th><th>방식</th><th>실행 ID</th><th>상태</th><th>보고서</th></tr></thead>
        <tbody>{history.items.map((item) => <tr key={item.id} aria-selected={item.id === selectedId}>
          <td><Time iso={item.created_at} /></td><td>{item.request.mode === "update" ? "두 판본 비교" : "단일 환경"}</td>
          <td><button type="button" className={s.historyLink} onClick={() => { patch({ run: item.id }); const restored = formOf(item.request); if (restored) setForm(restored); }}>{item.id}</button></td>
          <td><Status run={item} /></td><td>{item.report_refs?.length || item.report_ids?.length
            ? <ReportLinks run={item} label="결과 보기" /> : "–"}</td>
        </tr>)}</tbody>
      </table></div> : <StateView kind="empty" detail="아직 검증 실행 기록이 없습니다." />}</Query>
    </section>
  </div>;
}

function DocumentPicker({ kind, label, value, disabled, onChange }: { kind: "environments" | "policies"; label: string;
  value: DraftRef; disabled: boolean; onChange: (value: DraftRef) => void }) {
  const documents = useDocList(kind);
  const revisions = useDocRevisions(kind, value.id || null);
  useEffect(() => {
    if (value.id && value.revision == null && revisions.data?.items.length) {
      onChange({ id: value.id, revision: revisions.data.items.at(-1)!.revision });
    }
  }, [value.id, value.revision, revisions.data, onChange]);
  return <div className={s.picker}>
    <label className={s.field}>{label}<select className="select" value={value.id} required disabled={disabled || documents.isPending || documents.isError}
      onChange={(event) => onChange({ id: event.target.value, revision: null })}>
      <option value="">{documents.isPending ? "불러오는 중…" : "등록한 항목 선택"}</option>
      {(documents.data?.items ?? []).map((item) => <option key={item.doc_id} value={item.doc_id}>{item.doc_id}{item.target_ref ? ` · ${item.target_ref}` : ""}</option>)}
    </select></label>
    <label className={s.field}>{label} 판본<select className="select" value={value.revision ?? ""} required disabled={disabled || !value.id || revisions.isPending || revisions.isError}
      onChange={(event) => onChange({ id: value.id, revision: Number(event.target.value) || null })}>
      <option value="">{revisions.isPending && value.id ? "불러오는 중…" : "판본 선택"}</option>
      {(revisions.data?.items ?? []).map((item) => <option key={item.revision} value={item.revision}>판본 {item.revision} · {item.status}{item.label ? ` · ${item.label}` : ""}</option>)}
    </select></label>
    {(documents.isError || revisions.isError) && <span className={s.error}>등록 목록을 불러오지 못했습니다. 새로고침 후 다시 선택하세요.</span>}
  </div>;
}

function Preparation({ result }: { result: UpdatePreparation }) {
  const checks = result.checks ?? [];
  return <div className="stack">
    {result.guidance?.summary && <p className="small">{result.guidance.summary}</p>}
    <div className={s.checks}>{checks.map((check, index) => <div className={s.check} key={`${check.check}-${index}`}>
      <Badge tone={check.ok ? "neutral" : "caution"}>{check.ok ? "준비 확인" : "보완 필요"}</Badge>
      <span className="small"><strong>{check.check}</strong><span className={s.detail}>{check.detail}</span></span>
    </div>)}</div>
    {!!result.guidance?.issues?.length && <details className="small" open={!result.ready}>
      <summary>원인 및 다음 조치 ({result.guidance.issues.length})</summary>
      <div className={s.issues}>{result.guidance.issues.map((issue, index) => <div key={index}>
        <strong>{issue.check_id || issue.side || issue.code}{issue.blocking === false && " · 참고"}</strong>
        <p>{issue.cause}</p><p className="muted">{issue.action}</p>
      </div>)}</div>
    </details>}
    {!!result.check_support?.length && <details className="small" open>
      <summary>점검별 지원 범위 ({result.check_support.length})</summary>
      <p className="muted">지원 상태는 수집 자격을 나타냅니다. 실측 범위는 실행 보고서에서 확인하세요.</p>
      <div className={s.tableWrap}><table className={s.table}><thead><tr><th>대상</th><th>점검</th><th>지원 상태</th><th>근거</th></tr></thead>
        <tbody>{result.check_support.map((row, index) => <tr key={index}><td><AnyValue v={row.side} /></td><td><AnyValue v={row.check_id} /></td>
          <td><AnyValue v={row.status} /></td><td><AnyValue v={row.reasons} /></td></tr>)}</tbody></table></div>
    </details>}
    <details className="small"><summary>준비 점검 원본</summary><JsonView value={result} /></details>
  </div>;
}
