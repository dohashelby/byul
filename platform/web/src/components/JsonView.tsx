import { useMemo, useState, type ReactElement } from "react";
import s from "./ui.module.css";

type Line = {
  indent: number;
  key: string | null;
  text: string; // 값 또는 여는/닫는 괄호
  kind: "str" | "num" | "lit" | "punct";
  openEnd?: number; // 이 줄이 { 또는 [ 로 열면, 닫는 줄 번호
  comma: boolean;
};

function build(value: unknown): Line[] {
  const lines: Line[] = [];
  const walk = (v: unknown, indent: number, key: string | null, comma: boolean) => {
    if (v !== null && typeof v === "object") {
      const isArr = Array.isArray(v);
      const entries = isArr ? (v as unknown[]).map((x, i) => [String(i), x] as const) : Object.entries(v as object);
      if (entries.length === 0) {
        lines.push({ indent, key, text: isArr ? "[]" : "{}", kind: "punct", comma });
        return;
      }
      const at = lines.length;
      lines.push({ indent, key, text: isArr ? "[" : "{", kind: "punct", comma: false });
      entries.forEach(([k, x], i) => walk(x, indent + 1, isArr ? null : k, i < entries.length - 1));
      lines.push({ indent, key: null, text: isArr ? "]" : "}", kind: "punct", comma });
      lines[at].openEnd = lines.length - 1;
      return;
    }
    const kind = typeof v === "string" ? "str" : typeof v === "number" ? "num" : "lit";
    lines.push({ indent, key, text: JSON.stringify(v) ?? "null", kind, comma });
  };
  walk(value, 0, null, false);
  return lines;
}

/** 접고 펼치는 JSON 보기. 줄 번호·복사 버튼. 값은 텍스트로만 넣는다(HTML로 해석하지 않는다). */
export function JsonView({ value, maxHeight = 480 }: { value: unknown; maxHeight?: number }) {
  const lines = useMemo(() => build(value), [value]);
  const [folded, setFolded] = useState<Set<number>>(new Set());
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(value, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      setCopied(false);
    }
  };

  const rows: ReactElement[] = [];
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    const isFolded = l.openEnd != null && folded.has(i);
    const cls = l.kind === "str" ? s.jsonStr : l.kind === "num" ? s.jsonNum : l.kind === "lit" ? s.jsonLit : undefined;
    rows.push(
      <div className={s.jsonLine} key={i}>
        <span className={s.jsonNo}>{i + 1}</span>
        {l.openEnd != null ? (
          <button
            type="button"
            className={s.jsonFold}
            aria-label={isFolded ? "펼치기" : "접기"}
            onClick={() =>
              setFolded((f) => {
                const n = new Set(f);
                if (n.has(i)) n.delete(i);
                else n.add(i);
                return n;
              })
            }
          >
            {isFolded ? "▸" : "▾"}
          </button>
        ) : (
          <span className={s.jsonFold} />
        )}
        <span>
          {"  ".repeat(l.indent)}
          {l.key != null && (
            <>
              <span className={s.jsonKey}>{JSON.stringify(l.key)}</span>
              {": "}
            </>
          )}
          <span className={cls}>{l.text}</span>
          {isFolded && (
            <>
              <span className="muted"> … {lines[l.openEnd!].text}</span>
              {lines[l.openEnd!].comma ? "," : ""}
            </>
          )}
          {!isFolded && l.comma ? "," : ""}
        </span>
      </div>,
    );
    if (isFolded) i = l.openEnd!;
  }

  return (
    <div className={s.json}>
      <div className={s.jsonBar}>
        <button type="button" className="btn btn-small" onClick={() => setFolded(new Set())}>
          모두 펼치기
        </button>
        <button type="button" className="btn btn-small" onClick={copy}>
          {copied ? "복사됨" : "복사"}
        </button>
      </div>
      <div style={{ maxHeight, overflow: "auto", padding: "4px 0" }}>{rows}</div>
    </div>
  );
}
