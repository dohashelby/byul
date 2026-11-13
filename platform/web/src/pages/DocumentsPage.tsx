import { useEffect, useState } from "react";
import { Link, useNavigate, useParams as useRouteParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  useDocDiff,
  useDocList,
  useDocRevisions,
  useEnvironmentRevision,
  usePolicyRevision,
  useSaveEnvironment,
  useConfirmPolicy,
  useSavePolicy,
  type DocKind,
} from "../api/hooks";
import type { EnvironmentDoc, FieldIssue, PolicyDoc, SaveRejected } from "../api/types";
import { EnumBadge } from "../components/Badges";
import { DataTable } from "../components/DataTable";
import { PageHead, Query, StateView } from "../components/Misc";
import { AnyValue, Short, Time, Value } from "../components/Value";
import { DOC_STATUS } from "../lib/labels";
import { EnvironmentForm, PolicyForm, factWarnings, newEnvironment, newPolicy, nextPolicyVersion } from "./forms";
import p from "./pages.module.css";
import { BundleExport } from "./BundleExport";
import { EditorIntro, EditorSaveBar, InputIssueSummary, useEditorStatus } from "../components/InputEditor";

const TITLE: Record<DocKind, string> = { environments: "환경", policies: "정책" };

export function DocListPage({ kind }: { kind: DocKind }) {
  const q = useDocList(kind);
  const navigate = useNavigate();
  return (
    <div className="page">
      <PageHead title={`${TITLE[kind]} 목록`}>
        <Link className="btn btn-primary" to={`/${kind}/new`}>
          새 {TITLE[kind]}
        </Link>
      </PageHead>
      <div className="row">
        <span className="grow notice">판본은 저장할 때마다 새로 생깁니다. 이번 단계에서는 초안(DRAFT)으로만 저장합니다.</span>
      </div>
      <Query q={q}>
        {(data) => (
          <DataTable
            columns={[
              { id: "doc_id", header: "ID", cell: (r) => <span className="mono">{r.doc_id}</span>, sort: (r) => r.doc_id },
              { id: "revision", header: "최신 판본", cell: (r) => r.revision, sort: (r) => r.revision },
              ...(kind === "policies" ? [{ id: "label", header: "policy_version", cell: (r: typeof data.items[number]) => <Value v={r.label} mono /> }] : []),
              { id: "target", header: "대상", cell: (r) => <Value v={r.target_ref} mono />, sort: (r) => r.target_ref },
              { id: "status", header: "상태", cell: (r) => <EnumBadge table={DOC_STATUS} value={r.status} />, sort: (r) => r.status },
              { id: "saved_at", header: "수정 시각", cell: (r) => <Time iso={r.saved_at} />, sort: (r) => r.saved_at },
              { id: "sha", header: "정규 해시", cell: (r) => <Short value={r.sha256} /> },
            ]}
            rows={data.items}
            rowKey={(r) => r.doc_id}
            onRowClick={(r) => navigate(`/${kind}/${encodeURIComponent(r.doc_id)}`)}
            initialSort={{ id: "saved_at", dir: "desc" }}
            empty={<StateView kind="empty" detail={`저장된 ${TITLE[kind]}이 없습니다.`} />}
          />
        )}
      </Query>
    </div>
  );
}

function rejected(error: unknown): FieldIssue[] {
  if (error instanceof ApiError && error.body && typeof error.body === "object" && "errors" in error.body) {
    return (error.body as SaveRejected).errors;
  }
  if (error instanceof ApiError) return [{ path: "/", message: `${error.code}: ${error.message}` }];
  return [{ path: "/", message: String(error) }];
}

export function EnvironmentEditorPage() {
  const { id } = useRouteParams();
  const isNew = id === undefined;
  const revs = useDocRevisions("environments", id ?? null);
  const latest = revs.data?.items.at(-1)?.revision ?? null;
  const current = useEnvironmentRevision(id ?? null, latest);
  const save = useSaveEnvironment();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<EnvironmentDoc | null>(isNew ? newEnvironment() : null);
  const editor = useEditorStatus(doc, isNew);
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const [serverWarnings, setServerWarnings] = useState<FieldIssue[]>([]);
  const [savedRev, setSavedRev] = useState<number | null>(null);

  useEffect(() => {
    if (!isNew && current.data && !doc) {
      setDoc(current.data.document);
      setSavedRev(current.data.meta.revision);
    }
  }, [current.data, doc, isNew]);

  const onSave = () => {
    if (!doc || editor.localIssues.length) return;
    save.mutate(doc, {
      onSuccess: (res) => {
        setIssues([]);
        setServerWarnings(res.warnings);
        setSavedRev(res.saved.meta.revision);
        setDoc(res.saved.document);
        editor.markSaved(res.saved.document);
        if (isNew) navigate(`/environments/${encodeURIComponent(res.saved.meta.doc_id)}`, { replace: true });
      },
      onError: (e) => setIssues(rejected(e)),
    });
  };

  if (!isNew && (revs.isError || current.isError)) return <InputLoadError retry={() => { void revs.refetch(); void current.refetch(); }} />;
  if (!isNew && revs.isSuccess && latest === null) return <StateView kind="empty" detail="저장된 환경 판본이 없습니다." />;
  if (!doc) return <StateView kind="loading" />;
  const warnings = [...factWarnings(doc), ...serverWarnings.filter((w) => !factWarnings(doc).some((x) => x.path === w.path))];
  return (
    <div className={`page ${p.inputPage}`}>
      <EditorIntro kind="환경" isNew={isNew} origin={doc.data_origin} context={[doc.target.engine.name || "엔진 미선택", doc.target.engine.version || "판본 미확인", doc.target.model.id || "모델 미입력"]} />
      <EditorSaveBar onSave={onSave} pending={save.isPending} failed={save.isError} dirty={editor.dirty} issues={[...issues, ...editor.localIssues]} localError={editor.localIssues.length > 0} savedRev={savedRev} />
      <InputIssueSummary issues={[...issues, ...editor.localIssues]} />
      <fieldset className={p.editorFields} disabled={save.isPending} aria-label="환경 입력">
        <EnvironmentForm doc={doc} setDoc={(d) => { setDoc(d); setIssues([]); }} issues={[...issues, ...editor.localIssues]} warnings={warnings} isNew={isNew} onLocalIssue={editor.reportIssue} />
      </fieldset>
      {!isNew && id && <History kind="environments" id={id} />}
    </div>
  );
}

export function PolicyEditorPage() {
  const { id } = useRouteParams();
  const isNew = id === undefined;
  const revs = useDocRevisions("policies", id ?? null);
  const latest = revs.data?.items.at(-1)?.revision ?? null;
  const current = usePolicyRevision(id ?? null, latest);
  const save = useSavePolicy();
  const navigate = useNavigate();
  const [doc, setDoc] = useState<PolicyDoc | null>(isNew ? newPolicy() : null);
  const editor = useEditorStatus(doc, isNew);
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  const [savedRev, setSavedRev] = useState<number | null>(null);
  const [serverWarnings, setServerWarnings] = useState<FieldIssue[]>([]);

  useEffect(() => {
    if (!isNew && current.data && !doc) {
      // 확정·예시 판본을 고쳐도 새 판본은 DRAFT로 저장된다.
      setDoc({ ...current.data.document, status: "DRAFT" });
      setSavedRev(current.data.meta.revision);
    }
  }, [current.data, doc, isNew]);

  const onSave = () => {
    if (!doc || editor.localIssues.length) return;
    save.mutate(isNew ? doc : { ...doc, policy_version: nextPolicyVersion(doc.policy_version) }, {
      onSuccess: (res) => {
        setIssues([]);
        setServerWarnings(res.warnings);
        setSavedRev(res.saved.meta.revision);
        setDoc(res.saved.document);
        editor.markSaved(res.saved.document);
        if (isNew) navigate(`/policies/${encodeURIComponent(res.saved.meta.doc_id)}`, { replace: true });
      },
      onError: (e) => setIssues(rejected(e)),
    });
  };

  if (!isNew && (revs.isError || current.isError)) return <InputLoadError retry={() => { void revs.refetch(); void current.refetch(); }} />;
  if (!isNew && revs.isSuccess && latest === null) return <StateView kind="empty" detail="저장된 정책 판본이 없습니다." />;
  if (!doc) return <StateView kind="loading" />;
  return (
    <div className={`page ${p.inputPage}`}>
      <EditorIntro kind="정책" isNew={isNew} origin={doc.data_origin} context={[doc.policy_id || "새 정책", doc.environment_ref ? `${doc.environment_ref.id} · 판본 ${doc.environment_ref.revision}` : "기준 환경 미선택"]} />
      <EditorSaveBar onSave={onSave} pending={save.isPending} failed={save.isError} dirty={editor.dirty} issues={[...issues, ...editor.localIssues]} localError={editor.localIssues.length > 0} savedRev={savedRev} />
      <InputIssueSummary issues={[...issues, ...editor.localIssues]} />
      {serverWarnings.length > 0 && <div className="notice">저장했지만 실행 준비는 완료되지 않았습니다. 입력 연결·준비 상태에서 부족한 값을 확인하세요.</div>}
      <fieldset className={p.editorFields} disabled={save.isPending} aria-label="정책 입력">
        <PolicyForm doc={doc} setDoc={(d) => { setDoc(d); setIssues([]); }} issues={[...issues, ...editor.localIssues]} warnings={serverWarnings} isNew={isNew} onLocalIssue={editor.reportIssue} />
      </fieldset>
      {!isNew && id && latest !== null && current.data && (
        <ConfirmPolicy id={id} revision={latest} stored={current.data.document} edited={doc} onConfirmed={(d) => setDoc({ ...d, status: "DRAFT" })} />
      )}
      {!isNew && id && <History kind="policies" id={id} />}
      {!isNew && id && <BundleExport policyId={id} revisions={revs.data?.items ?? []} />}
    </div>
  );
}

function InputLoadError({ retry }: { retry: () => void }) {
  return <div className={`page ${p.inputPage}`}><StateView kind="error" detail="저장된 입력을 불러오지 못했습니다. 연결 상태와 문서 접근 권한을 확인하세요." /><button type="button" className="btn" onClick={retry}>다시 불러오기</button></div>;
}

/** 저장된 최신 DRAFT 판본을 승인한다. 승인은 그 내용 그대로 새 CONFIRMED 판본을 만든다. */
function ConfirmPolicy({ id, revision, stored, edited, onConfirmed }: { id: string; revision: number; stored: PolicyDoc; edited: PolicyDoc; onConfirmed: (d: PolicyDoc) => void }) {
  const confirm = useConfirmPolicy(id);
  const [approvedBy, setApprovedBy] = useState("");
  const [issues, setIssues] = useState<FieldIssue[]>([]);
  if (stored.status === "CONFIRMED") {
    return (
      <section className="card card-pad stack">
        <h2>판본 승인</h2>
        <span className="small">
          판본 {revision} 승인됨 · {stored.approval?.approval_ref} · {stored.approval?.approved_by} · <Time iso={stored.approval?.approved_at ?? null} />
        </span>
      </section>
    );
  }
  const dirty = JSON.stringify({ ...edited, status: "DRAFT" }) !== JSON.stringify(stored);
  const onConfirm = () =>
    confirm.mutate(
      { revision, approved_by: approvedBy.trim() },
      {
        onSuccess: (res) => {
          setIssues([]);
          onConfirmed(res.saved.document);
        },
        onError: (e) => setIssues(rejected(e)),
      },
    );
  return (
    <section className="card card-pad stack">
      <h2>판본 승인</h2>
      <div className="small muted">저장된 판본 {revision}을 그대로 승인합니다. 승인 번호와 시각은 자동으로 기록합니다. 자기 신고 기록이며 서명이 아닙니다.</div>
      <div className="row wrap">
        <label className="small">
          <span className={p.req}>승인자</span>
          <input className="input" value={approvedBy} onChange={(e) => setApprovedBy(e.target.value)} />
        </label>
        <button type="button" className="btn" disabled={dirty || !approvedBy.trim() || confirm.isPending} onClick={onConfirm}>
          이 판본 승인
        </button>
      </div>
      {dirty && <span className="small muted">바뀐 내용을 먼저 저장해야 승인할 수 있습니다.</span>}
      {issues.map((i, k) => (
        <span key={k} className="small" style={{ color: "var(--error)" }}>
          {i.path}: {i.message}
        </span>
      ))}
    </section>
  );
}

function History({ kind, id }: { kind: DocKind; id: string }) {
  const revs = useDocRevisions(kind, id);
  const items = revs.data?.items ?? [];
  const [from, setFrom] = useState<number | null>(null);
  const [to, setTo] = useState<number | null>(null);
  useEffect(() => {
    if (items.length >= 2) {
      setFrom(items[items.length - 2].revision);
      setTo(items[items.length - 1].revision);
    }
  }, [items.length]); // eslint-disable-line react-hooks/exhaustive-deps
  const diff = useDocDiff(kind, id, from, to);
  return (
    <section className="card card-pad stack">
      <h2>판본 이력</h2>
      <DataTable
        columns={[
          { id: "revision", header: "판본", cell: (r) => r.revision, sort: (r) => r.revision },
          { id: "status", header: "상태", cell: (r) => <EnumBadge table={DOC_STATUS} value={r.status} /> },
          { id: "label", header: "policy_version", cell: (r) => <Value v={r.label} mono />, hidden: kind === "environments" },
          { id: "saved_at", header: "저장 시각", cell: (r) => <Time iso={r.saved_at} />, sort: (r) => r.saved_at },
          { id: "sha", header: "정규 해시", cell: (r) => <Short value={r.sha256} n={16} /> },
        ]}
        rows={items}
        rowKey={(r) => String(r.revision)}
        initialSort={{ id: "revision", dir: "desc" }}
      />
      {items.length < 2 ? (
        <span className="small muted">판본이 하나뿐이라 비교할 것이 없습니다.</span>
      ) : (
        <div className="stack">
          <div className="row small">
            차이 보기
            <select className="select" value={from ?? ""} onChange={(e) => setFrom(Number(e.target.value))} aria-label="이전 판본">
              {items.map((r) => (
                <option key={r.revision} value={r.revision}>
                  판본 {r.revision}
                </option>
              ))}
            </select>
            →
            <select className="select" value={to ?? ""} onChange={(e) => setTo(Number(e.target.value))} aria-label="이후 판본">
              {items.map((r) => (
                <option key={r.revision} value={r.revision}>
                  판본 {r.revision}
                </option>
              ))}
            </select>
          </div>
          {from === to ? (
            <span className="small muted">같은 판본입니다.</span>
          ) : (
            <Query q={diff}>
              {(d) => (
                <DataTable
                  columns={[
                    { id: "path", header: "필드", cell: (e) => <span className="mono">{e.path}</span>, sort: (e) => e.path },
                    {
                      id: "change",
                      header: "변경",
                      cell: (e) => (
                        <span className={e.change === "added" ? p.diffAdded : e.change === "removed" ? p.diffRemoved : undefined}>
                          {e.change === "added" ? "추가" : e.change === "removed" ? "삭제" : "변경"}
                        </span>
                      ),
                    },
                    { id: "before", header: `판본 ${from}`, cell: (e) => (e.change === "added" ? <span className="muted">(없음)</span> : <AnyValue v={e.before} />) },
                    { id: "after", header: `판본 ${to}`, cell: (e) => (e.change === "removed" ? <span className="muted">(없음)</span> : <AnyValue v={e.after} />) },
                  ]}
                  rows={d.entries}
                  rowKey={(e) => e.path}
                  pageParam="page_diff"
                  empty={<span className="muted">차이가 없습니다</span>}
                />
              )}
            </Query>
          )}
        </div>
      )}
    </section>
  );
}
