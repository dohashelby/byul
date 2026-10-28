// 계약 문서를 JSON 그대로 편집하는 폼 부품. 경로는 JSON Pointer(서버 검증 오류와 같은 형식)다.
import { useEffect, useId, useState, type ReactNode } from "react";
import type { FieldIssue } from "../api/types";
import p from "../pages/pages.module.css";

export type Doc = Record<string, unknown>;

export function getIn(doc: unknown, path: string): unknown {
  let cur: unknown = doc;
  for (const key of path.split("/").filter(Boolean)) {
    if (cur == null || typeof cur !== "object") return undefined;
    cur = (cur as Record<string, unknown>)[key];
  }
  return cur;
}

export function setIn<T>(doc: T, path: string, value: unknown): T {
  const keys = path.split("/").filter(Boolean);
  const walk = (node: unknown, i: number): unknown => {
    if (i === keys.length) return value;
    const base = node != null && typeof node === "object" ? (Array.isArray(node) ? [...node] : { ...(node as object) }) : {};
    (base as Record<string, unknown>)[keys[i]] = walk((base as Record<string, unknown>)[keys[i]], i + 1);
    return base;
  };
  return walk(doc, 0) as T;
}

export function deleteIn<T>(doc: T, path: string): T {
  const keys = path.split("/").filter(Boolean);
  const last = keys.pop()!;
  const parent = getIn(doc, keys.join("/"));
  if (parent == null || typeof parent !== "object") return doc;
  const copy = { ...(parent as Record<string, unknown>) };
  delete copy[last];
  return setIn(doc, keys.join("/"), copy);
}

export type FormCtx = {
  doc: Doc;
  set: (path: string, value: unknown) => void;
  issues: FieldIssue[];
  warnings: FieldIssue[];
};

/** 이 경로(또는 그 아래)의 서버 검증 오류. */
export function issuesAt(issues: FieldIssue[], path: string): FieldIssue[] {
  return issues.filter((i) => i.path === path || i.path.startsWith(`${path}/`));
}

export function FieldMessages({ ctx, path, exact }: { ctx: FormCtx; path: string; exact?: boolean }) {
  const pick = (list: FieldIssue[]) => (exact ? list.filter((i) => i.path === path) : issuesAt(list, path));
  const errs = pick(ctx.issues);
  const warns = pick(ctx.warnings);
  return (
    <>
      {errs.map((e, i) => (
        <span key={`e${i}`} className={p.fieldError} role="alert">
          {e.path !== path ? `${e.path}: ` : ""}
          {e.message}
        </span>
      ))}
      {warns.map((e, i) => (
        <span key={`w${i}`} className={p.fieldWarn}>
          {e.message}
        </span>
      ))}
    </>
  );
}

function Field({ label, ctx, path, children, hint }: { label: string; ctx: FormCtx; path: string; children: ReactNode; hint?: string }) {
  return (
    <label className={p.field}>
      {label}
      {children}
      {hint && <span className="muted">{hint}</span>}
      <FieldMessages ctx={ctx} path={path} exact />
    </label>
  );
}

const bad = (ctx: FormCtx, path: string) => (ctx.issues.some((i) => i.path === path) ? p.invalidInput : "");

/** 문자열 칸. nullable이면 빈칸은 null(모름)로 저장한다. 빈 문자열을 만들지 않는다. suggestions는 고를 수 있는 값이고 직접 입력도 받는다. */
export function TextField({
  ctx,
  path,
  label,
  nullable,
  optional,
  placeholder,
  hint,
  suggestions,
}: {
  ctx: FormCtx;
  path: string;
  label: string;
  nullable?: boolean;
  optional?: boolean;
  placeholder?: string;
  hint?: string;
  suggestions?: string[];
}) {
  const v = getIn(ctx.doc, path);
  const listId = useId();
  return (
    <Field label={label} ctx={ctx} path={path} hint={hint}>
      <input
        className={`input ${bad(ctx, path)}`}
        list={suggestions?.length ? listId : undefined}
        value={typeof v === "string" ? v : ""}
        placeholder={nullable ? (placeholder ?? "모르면 비워 둠 (null)") : placeholder}
        onChange={(e) => ctx.set(path, e.target.value === "" && nullable ? null : e.target.value === "" && optional ? undefined : e.target.value)}
      />
      {!!suggestions?.length && (
        <datalist id={listId}>
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
      )}
    </Field>
  );
}

export function NumberField({ ctx, path, label, nullable, optional }: { ctx: FormCtx; path: string; label: string; nullable?: boolean; optional?: boolean }) {
  const v = getIn(ctx.doc, path);
  return (
    <Field label={label} ctx={ctx} path={path} hint={nullable ? "모르면 비워 둠 (null)" : optional ? "비우면 필드 없음" : undefined}>
      <input
        className={`input ${bad(ctx, path)}`}
        type="number"
        value={typeof v === "number" ? v : ""}
        onChange={(e) => {
          const raw = e.target.value;
          if (raw === "") {
            if (nullable) ctx.set(path, null);
            else if (optional) ctx.set(path, undefined);
            else ctx.set(path, undefined);
            return;
          }
          ctx.set(path, Number(raw));
        }}
      />
    </Field>
  );
}

export function SelectField({
  ctx,
  path,
  label,
  options,
  nullable,
}: {
  ctx: FormCtx;
  path: string;
  label: string;
  options: { value: string; label: string }[];
  nullable?: boolean;
}) {
  const v = getIn(ctx.doc, path);
  // 이전 판본에 목록 밖 값이 있으면 지우지 않고 그대로 보여준다.
  const outside = typeof v === "string" && v !== "" && !options.some((o) => o.value === v);
  return (
    <Field label={label} ctx={ctx} path={path}>
      <select className={`select ${bad(ctx, path)}`} value={typeof v === "string" ? v : ""} onChange={(e) => ctx.set(path, e.target.value === "" ? (nullable ? null : undefined) : e.target.value)}>
        <option value="">{nullable ? "– (null)" : "선택"}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
        {outside && <option value={v}>{v} (목록 밖)</option>}
      </select>
    </Field>
  );
}

/** 쉼표로 나눈 문자열 목록. */
export function ListField({ ctx, path, label }: { ctx: FormCtx; path: string; label: string }) {
  const v = getIn(ctx.doc, path);
  const [text, setText] = useState(Array.isArray(v) ? v.join(", ") : "");
  useEffect(() => setText(Array.isArray(v) ? v.join(", ") : ""), [JSON.stringify(v)]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Field label={label} ctx={ctx} path={path} hint="쉼표로 구분">
      <input
        className={`input ${bad(ctx, path)}`}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          ctx.set(
            path,
            e.target.value
              .split(",")
              .map((x) => x.trim())
              .filter(Boolean),
          );
        }}
      />
    </Field>
  );
}

export function DateTimeField({ ctx, path, label }: { ctx: FormCtx; path: string; label: string }) {
  const v = getIn(ctx.doc, path);
  const local = typeof v === "string" ? toLocal(v) : "";
  return (
    <Field label={label} ctx={ctx} path={path} hint={typeof v === "string" ? `저장 값: ${v}` : "비우면 null"}>
      <input
        className={`input ${bad(ctx, path)}`}
        type="datetime-local"
        value={local}
        onChange={(e) => ctx.set(path, e.target.value ? new Date(e.target.value).toISOString().replace(/\.\d{3}Z$/, "Z") : null)}
      />
    </Field>
  );
}

function toLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** JSON 편집기. 파싱에 실패하면 값을 바꾸지 않고 오류를 보여준다. */
export function JsonField({ ctx, path, label, rows = 8, hint }: { ctx: FormCtx; path: string; label: string; rows?: number; hint?: string }) {
  const v = getIn(ctx.doc, path);
  const [text, setText] = useState(() => JSON.stringify(v ?? null, null, 2));
  const [parseError, setParseError] = useState<string | null>(null);
  useEffect(() => {
    try {
      if (JSON.stringify(JSON.parse(text)) === JSON.stringify(v ?? null)) return;
    } catch {
      return;
    }
    setText(JSON.stringify(v ?? null, null, 2));
  }, [JSON.stringify(v)]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <label className={p.field} style={{ gridColumn: "1 / -1" }}>
      {label}
      <textarea
        className={`input ${parseError || ctx.issues.some((i) => i.path === path) ? p.invalidInput : ""}`}
        rows={rows}
        spellCheck={false}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          try {
            ctx.set(path, JSON.parse(e.target.value));
            setParseError(null);
          } catch (err) {
            setParseError(`JSON 형식 오류: ${(err as Error).message}`);
          }
        }}
      />
      {hint && <span className="muted">{hint}</span>}
      {parseError && <span className={p.fieldError}>{parseError}</span>}
      <FieldMessages ctx={ctx} path={path} />
    </label>
  );
}

export function Section({ title, children, note }: { title: string; children: ReactNode; note?: ReactNode }) {
  return (
    <section className="card card-pad stack">
      <h2>{title}</h2>
      {note && <div className="small muted">{note}</div>}
      <div className={p.formGrid}>{children}</div>
    </section>
  );
}
