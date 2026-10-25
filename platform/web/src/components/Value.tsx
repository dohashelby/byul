import { formatLocal } from "../lib/time";
import s from "./ui.module.css";

type Scalar = string | number | boolean | null | undefined;

/** 값 없음은 "–", 미수집은 "미수집"으로 보여준다. 0이나 빈칸으로 바꾸지 않는다. */
export function Value({ v, notCollected, mono }: { v: Scalar; notCollected?: boolean; mono?: boolean }) {
  if (notCollected) return <span className={s.notCollected}>미수집</span>;
  if (v === null || v === undefined) return <span className={s.na}>–</span>;
  if (v === "") return <span className={s.na}>(빈 문자열)</span>;
  const text = typeof v === "boolean" ? (v ? "예" : "아니오") : String(v);
  return <span className={mono ? "mono" : undefined}>{text}</span>;
}

/** 사용자 시간대로 보여주고, 마우스를 올리면 원래 UTC 값을 보여준다. */
export function Time({ iso }: { iso: string | null | undefined }) {
  if (!iso) return <span className={s.na}>–</span>;
  return (
    <time dateTime={iso} title={`${iso} (원본)\n${new Date(iso).toISOString()} (UTC)`} className="nowrap">
      {formatLocal(iso)}
    </time>
  );
}

export function Short({ value, n = 12 }: { value: string | null | undefined; n?: number }) {
  if (!value) return <span className={s.na}>–</span>;
  return (
    <span className="mono" title={value}>
      {value.length > n ? `${value.slice(0, n)}…` : value}
    </span>
  );
}

/** 값이 무엇이든 텍스트로만 보여준다(객체는 JSON 문자열). */
export function AnyValue({ v }: { v: unknown }) {
  if (v === null || v === undefined) return <span className={s.na}>–</span>;
  if (typeof v === "object") return <span className="mono">{JSON.stringify(v)}</span>;
  return <Value v={v as Scalar} mono={typeof v !== "boolean"} />;
}
