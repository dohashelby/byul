import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { FieldIssue, InputCatalog } from "../api/types";
import { focusInputIssue, inputIssueLabel, openInputSection } from "../lib/inputUx";
import s from "./inputEditor.module.css";

export function useEditorStatus(doc: unknown, isNew: boolean) {
  const signature = (value: unknown) => {
    if (!value || typeof value !== "object") return JSON.stringify(value);
    const snapshot = { ...value } as Record<string, unknown>;
    delete snapshot.created_at;
    delete snapshot.revision;
    if (snapshot.verification && typeof snapshot.verification === "object") snapshot.verification = { ...snapshot.verification, pack: undefined };
    return JSON.stringify(snapshot);
  };
  const [baseline, setBaseline] = useState<string | null>(() => isNew ? signature(doc) : null);
  const [localIssues, setLocalIssues] = useState<Record<string, string>>({});
  const dirty = baseline !== null && (signature(doc) !== baseline || Object.keys(localIssues).length > 0);
  useEffect(() => {
    if (!doc || baseline !== null) return;
    setBaseline(signature(doc));
  }, [doc, baseline]);
  useEffect(() => {
    if (!dirty) return;
    const prevent = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", prevent);
    return () => window.removeEventListener("beforeunload", prevent);
  }, [dirty]);
  const reportIssue = useCallback((path: string, message: string | null) => setLocalIssues((current) => {
    const next = { ...current };
    if (message) next[path] = message;
    else delete next[path];
    return next;
  }), []);
  const markSaved = (saved: unknown) => { setBaseline(signature(saved)); setLocalIssues({}); };
  return { dirty, markSaved, reportIssue, localIssues: Object.entries(localIssues).map(([path, message]) => ({ path, message })) };
}

export function EditorIntro({ kind, isNew, context, origin }: { kind: "환경" | "정책"; isNew: boolean; context: string[]; origin: string }) {
  return <header className={s.hero}>
    <div><span className={s.eyebrow}>UPDATE · {kind === "환경" ? "시험 대상" : "검증 기준"}</span>
      <h1>{kind} {isNew ? "등록" : "편집"}</h1>
      <p>{kind === "환경" ? "저장된 자료를 재사용하고, 바뀐 환경만 확인하세요." : "적용할 기준을 정하고, 실행에 연결되는 값을 확인하세요."}</p>
    </div>
    <div className={s.context} aria-label="현재 입력 대상">
      {context.map((item, i) => <span key={i} className={s.contextItem}>{item}</span>)}
      <span className={s.origin}>{origin === "EXAMPLE" ? "예시 자료 · 공식 판정 근거 제외" : "입력 기준과 실제 관측은 별도"}</span>
    </div>
  </header>;
}

export function EditorSaveBar({ onSave, pending, dirty, issues, localError, savedRev, failed = false }: {
  onSave: () => void; pending: boolean; dirty: boolean; issues: FieldIssue[]; localError: boolean; savedRev: number | null; failed?: boolean;
}) {
  const state = pending ? "저장 중" : failed && issues.length ? "저장 실패 · 입력 확인 필요" : issues.length ? "저장 전 확인 필요" : dirty ? "미저장 변경 있음" : savedRev !== null ? "저장됨" : "아직 저장하지 않음";
  return <div className={s.saveBar}>
    <div className={s.saveInfo} role="status" aria-live="polite"><span className={s.status} data-tone={issues.length ? "error" : dirty ? "warn" : "neutral"}>{state}</span>
      <span className={s.saveHint}>{savedRev !== null && !dirty && !issues.length ? `판본 ${savedRev}로 저장했습니다.` : "저장하면 새 초안(DRAFT)이 생깁니다. 시험은 실행되지 않습니다."}</span>
    </div>
    <button className={`btn btn-primary ${s.saveButton}`} aria-label={pending ? "저장 중" : "새 판본으로 저장 (DRAFT)"} type="button" onClick={onSave} disabled={pending || localError}>
      {pending ? "저장 중…" : issues.length ? "확인 후 다시 저장" : "초안 저장"}<span aria-hidden="true"> ↗</span>
    </button>
  </div>;
}

export function InputIssueSummary({ issues, title = "입력 내용을 확인해 주세요", catalog }: { issues: FieldIssue[]; title?: string; catalog?: InputCatalog }) {
  if (!issues.length) return null;
  return <section className={s.errorSummary} aria-label={title} role="alert">
    <strong>{title} <span>{issues.length}건</span></strong><p>항목을 선택하면 해당 입력 위치로 이동합니다. 작성한 값은 유지됩니다.</p>
    <ul>{issues.map((issue, i) => <li key={`${issue.path}-${i}`}><button type="button" onClick={() => focusInputIssue(issue.path, catalog)}><span>{inputIssueLabel(issue.path)}</span><span>{issue.message}</span><span aria-hidden="true">→</span></button></li>)}</ul>
  </section>;
}

export function InputNavigation({ label, items }: { label: string; items: { id: string; title: string; state?: string }[] }) {
  return <div className={s.navigation}><div className={s.navHeading}><span>{label}</span><span>필요한 구역부터 작성하세요</span></div>
    <nav aria-label={label}>{items.map((item, i) => <a key={item.id} href={`#${item.id}`} onClick={(event) => { event.preventDefault(); openInputSection(item.id); }}><span className={s.navNumber}>{String(i + 1).padStart(2, "0")}</span><span>{item.title}</span>{item.state && <small>{item.state}</small>}</a>)}</nav>
  </div>;
}

export function PreparationGroup({ title, note, issues, catalog, open = false }: { title: string; note: string; issues: FieldIssue[]; catalog?: InputCatalog; open?: boolean }) {
  if (!issues.length) return null;
  return <details className={s.issueGroup} open={open}>
    <summary><span>{title}</span><span className={s.count}>{issues.length}건</span></summary><p>{note}</p>
    <ul>{issues.map((issue, i) => <li key={`${issue.path}-${i}`}><button type="button" onClick={() => focusInputIssue(issue.path, catalog)}><strong>{inputIssueLabel(issue.path)}</strong><span>{issue.message}</span></button></li>)}</ul>
  </details>;
}

export function StateCard({ label, title, children }: { label: string; title: ReactNode; children?: ReactNode }) {
  return <article className={s.stateCard}><span className={s.eyebrow}>{label}</span><strong>{title}</strong>{children && <div>{children}</div>}</article>;
}
