// 선택형 입력 부품. 값이 없으면(undefined) '미입력', null이면 '모름'이다. 둘을 섞지 않는다.
import { useId, useState } from "react";
import type { Choice, InputField, NumberDef } from "../lib/frameworkCatalog";
import p from "../pages/pages.module.css";

type Opt = { value: unknown; label: string };

/** 한 줄 분절 버튼. 이미 고른 칸을 다시 누르면 선택을 지운다(미입력). */
export function Segmented({ value, options, onChange, label }: { value: unknown; options: Opt[]; onChange: (v: unknown) => void; label: string }) {
  return (
    <div className={p.segmented} role="radiogroup" aria-label={label}>
      {options.map((o) => {
        const on = value !== undefined && Object.is(value, o.value);
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={on}
            className={`${p.segBtn} ${on ? p.segOn : ""} ${o.value === null ? p.segUnknown : ""}`}
            onClick={() => onChange(on ? undefined : o.value)}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

const UNKNOWN: Opt = { value: null, label: "모름" };

function Checks({ value, options, onChange }: { value: unknown; options: Choice[]; onChange: (v: unknown) => void }) {
  const list = Array.isArray(value) ? (value as string[]) : [];
  const unknown = value === null;
  const toggle = (v: string) => {
    const next = list.includes(v) ? list.filter((x) => x !== v) : [...list, v];
    onChange(next.length ? next : undefined);
  };
  return (
    <div className={p.checks}>
      {options.map((o) => (
        <label key={o.value} className={`${p.check} ${list.includes(o.value) ? p.checkOn : ""}`}>
          <input type="checkbox" checked={list.includes(o.value)} onChange={() => toggle(o.value)} />
          {o.label}
        </label>
      ))}
      <label className={`${p.check} ${p.checkUnknown} ${unknown ? p.checkOn : ""}`}>
        <input type="checkbox" checked={unknown} onChange={() => onChange(unknown ? undefined : null)} />
        모름
      </label>
      {list.filter((v) => !options.some((o) => o.value === v)).map((v) => (
        <span key={v} className={p.outside} title="카탈로그 목록 밖의 값(이전 판본)">
          {v}
        </span>
      ))}
    </div>
  );
}

/** 목록·표·서술 답의 '없음'·'모름'. 없음은 빈 값(empty)으로 저장한다. */
function EmptyAnswer({ label, none, empty, value, onChange }: { label: string; none?: string; empty: unknown; value: unknown; onChange: (v: unknown) => void }) {
  const isEmpty = value === "" || (Array.isArray(value) && !value.length) || (typeof value === "object" && value !== null && !Object.keys(value).length);
  return (
    <Segmented
      label={`${label} 없음·모름`}
      value={value === null ? null : isEmpty ? "none" : undefined}
      onChange={(v) => onChange(v === "none" ? empty : v)}
      options={[...(none ? [{ value: "none", label: none }] : []), UNKNOWN]}
    />
  );
}

function ListInput({ value, onChange, placeholder }: { value: unknown; onChange: (v: unknown) => void; placeholder?: string }) {
  const list = Array.isArray(value) ? (value as string[]) : [];
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (v && !list.includes(v)) onChange([...list, v]);
    setDraft("");
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className={p.chips}>
        {list.map((v) => (
          <span key={v} className={p.chip}>
            {v}
            <button type="button" className={p.chipX} aria-label={`${v} 지우기`} onClick={() => onChange(list.length > 1 ? list.filter((x) => x !== v) : undefined)}>
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="row" style={{ gap: 6 }}>
        <input
          className="input grow"
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button type="button" className="btn btn-small" onClick={add} disabled={!draft.trim()}>
          추가
        </button>
      </div>
    </div>
  );
}

/** 이름→값 표. blank는 새 줄과 빈 칸의 값이다(null=모름, ""=빈 문자열). */
export function KvInput({
  value,
  onChange,
  keyLabel,
  valueLabel,
  suggestions,
  blank = null,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  keyLabel: string;
  valueLabel: string;
  suggestions?: string[];
  blank?: string | null;
}) {
  const map = value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, string | null>) : {};
  const [name, setName] = useState("");
  const listId = useId();
  const put = (k: string, v: string | null | undefined) => {
    const next = { ...map };
    if (v === undefined) delete next[k];
    else next[k] = v;
    onChange(Object.keys(next).length ? next : undefined);
  };
  return (
    <div className="stack" style={{ gap: 6 }}>
      {Object.entries(map).map(([k, v]) => (
        <div key={k} className={p.kvRow}>
          <span className="mono">{k}</span>
          <input className="input" value={v ?? ""} placeholder={blank === null ? `${valueLabel} (모르면 비워 둠)` : valueLabel} onChange={(e) => put(k, e.target.value === "" ? blank : e.target.value)} />
          <button type="button" className="btn btn-small btn-ghost" onClick={() => put(k, undefined)} aria-label={`${k} 지우기`}>
            삭제
          </button>
        </div>
      ))}
      <div className={p.kvRow}>
        <input className="input" list={listId} value={name} placeholder={`${keyLabel} 이름`} onChange={(e) => setName(e.target.value)} />
        <datalist id={listId}>
          {(suggestions ?? []).filter((s) => !(s in map)).map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <button
          type="button"
          className="btn btn-small"
          disabled={!name.trim() || name.trim() in map}
          onClick={() => {
            put(name.trim(), blank);
            setName("");
          }}
        >
          {keyLabel} 추가
        </button>
      </div>
    </div>
  );
}

/** 카탈로그 항목 하나를 그린다. */
export function InputFieldView({ field, value, onChange, error, required }: { field: InputField; value: unknown; onChange: (v: unknown) => void; error?: string; required?: boolean }) {
  const wide = field.kind === "multi" || field.kind === "kv" || field.kind === "list";
  let control;
  switch (field.kind) {
    case "tri":
      control = <Segmented label={field.label} value={value} onChange={onChange} options={[{ value: true, label: field.yes ?? "예" }, { value: false, label: field.no ?? "아니오" }, UNKNOWN]} />;
      break;
    case "choice": {
      const outside = typeof value === "string" && !field.options.some((o) => o.value === value);
      control =
        field.options.length <= 4 ? (
          <Segmented label={field.label} value={value} onChange={onChange} options={[...field.options, ...(outside ? [{ value, label: `${value} (목록 밖)` }] : []), UNKNOWN]} />
        ) : (
          <select className="select" aria-label={field.label} value={value === null ? "__null" : typeof value === "string" ? value : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value === "__null" ? null : e.target.value)}>
            <option value="">선택 안 함</option>
            {field.options.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
            {outside && <option value={value as string}>{String(value)} (목록 밖)</option>}
            <option value="__null">모름</option>
          </select>
        );
      break;
    }
    case "multi":
      control = <Checks value={value} options={field.options} onChange={onChange} />;
      break;
    case "int":
      control = (
        <div className="row" style={{ gap: 6 }}>
          <input
            className="input"
            type="number"
            min={0}
            step={1}
            style={{ maxWidth: 140 }}
            value={typeof value === "number" ? value : ""}
            onChange={(e) => onChange(e.target.value === "" ? undefined : Math.max(0, Math.trunc(Number(e.target.value))))}
          />
          {field.unit && <span className="muted small">{field.unit}</span>}
          <Segmented label={`${field.label} 모름`} value={value === null ? null : undefined} onChange={(v) => onChange(v === null ? null : undefined)} options={[UNKNOWN]} />
        </div>
      );
      break;
    case "text":
      control = (
        <div className="stack" style={{ gap: 6 }}>
          <input className="input" value={typeof value === "string" ? value : ""} placeholder={field.placeholder} onChange={(e) => onChange(e.target.value === "" ? undefined : e.target.value)} />
          <EmptyAnswer label={field.label} none={field.none} empty="" value={value} onChange={onChange} />
        </div>
      );
      break;
    case "list":
      control = (
        <div className="stack" style={{ gap: 6 }}>
          <ListInput value={value} onChange={onChange} placeholder={field.placeholder} />
          <EmptyAnswer label={field.label} none={field.none} empty={[]} value={value} onChange={onChange} />
        </div>
      );
      break;
    case "kv":
      control = (
        <div className="stack" style={{ gap: 6 }}>
          <KvInput value={value} onChange={onChange} keyLabel={field.keyLabel} valueLabel={field.valueLabel} suggestions={field.suggestions} />
          <EmptyAnswer label={field.label} none={field.none} empty={{}} value={value} onChange={onChange} />
        </div>
      );
      break;
  }
  return (
    <div className={`${p.qField} ${wide ? p.qWide : ""}`}>
      <div className={p.qLabel}>
        <span className={required ? p.req : undefined}>{field.label}</span>
        {value === undefined && <span className={p.qMissing}>미입력</span>}
      </div>
      {control}
      {field.hint && <span className="muted small">{field.hint}</span>}
      {error && <span className={p.fieldError}>{error}</span>}
    </div>
  );
}

export type PolicyNumber = { value: number | null; unit: string | null; scope: string | null; condition: string | null; status: "confirmed" | "after_measurement" | "undecided" };

const STATUS: Opt[] = [
  { value: "confirmed", label: "확정" },
  { value: "after_measurement", label: "측정 후 결정" },
  { value: "undecided", label: "미정" },
];

/** 정책 수치 한 줄: 상태 → 값·단위 → 적용 범위 → 측정 조건. 확정이 아니면 값 칸을 막는다. */
export function NumberEntry({ def, value, onChange }: { def: NumberDef; value: PolicyNumber | undefined; onChange: (v: PolicyNumber | undefined) => void }) {
  const cur: PolicyNumber = value ?? { value: null, unit: def.units[0] ?? null, scope: def.scopes[0] ?? null, condition: null, status: def.measure ? "after_measurement" : "undecided" };
  const set = (patch: Partial<PolicyNumber>) => onChange({ ...cur, ...patch });
  return (
    <div className={p.numRow}>
      <div className={p.qLabel}>
        <span className={value?.status === "confirmed" ? p.req : undefined}>{def.label}</span>
        {value === undefined && <span className={p.qMissing}>미입력</span>}
        {def.measure && <span className={p.measureTag}>측정 필요</span>}
      </div>
      <Segmented
        label={`${def.label} 상태`}
        value={value?.status}
        options={STATUS}
        onChange={(s) => (s === undefined ? onChange(undefined) : set({ status: s as PolicyNumber["status"], value: s === "confirmed" ? cur.value : null }))}
      />
      <div className={p.numInputs}>
        <input
          className="input"
          type="number"
          min={0}
          aria-label={`${def.label} 값`}
          disabled={value?.status !== "confirmed"}
          placeholder={value?.status === "confirmed" ? "값" : "확정 시 입력"}
          value={typeof cur.value === "number" ? cur.value : ""}
          onChange={(e) => set({ value: e.target.value === "" ? null : Number(e.target.value) })}
        />
        {def.units.length > 1 ? (
          <select className="select" aria-label={`${def.label} 단위`} disabled={value === undefined} value={cur.unit ?? ""} onChange={(e) => set({ unit: e.target.value || null })}>
            {def.units.map((u) => (
              <option key={u} value={u}>
                {u}
              </option>
            ))}
          </select>
        ) : (
          <span className="muted small">{def.units[0]}</span>
        )}
        {def.scopes.length > 1 ? (
          <select className="select" aria-label={`${def.label} 적용 범위`} disabled={value === undefined} value={cur.scope ?? ""} onChange={(e) => set({ scope: e.target.value || null })}>
            {def.scopes.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        ) : (
          <span className="muted small">{def.scopes[0]}</span>
        )}
        <input
          className="input"
          aria-label={`${def.label} 측정 조건`}
          disabled={value === undefined}
          placeholder="측정 조건 (선택)"
          value={cur.condition ?? ""}
          onChange={(e) => set({ condition: e.target.value || null })}
        />
      </div>
    </div>
  );
}
