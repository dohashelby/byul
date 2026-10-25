import type { ReactNode } from "react";
import {
  DATA_ORIGIN,
  OUTCOME,
  VERDICT,
  entry,
  type Tone,
} from "../lib/labels";
import s from "./ui.module.css";

export function Badge({ tone, children, title, plain }: { tone: Tone; children: ReactNode; title?: string; plain?: boolean }) {
  return (
    <span className={`${s.badge} ${s[tone]} ${plain ? s.plain : ""}`} title={title}>
      {children}
    </span>
  );
}

/** 표 하나로 영문 enum을 한국어 배지로 바꾼다. 원래 값은 마우스를 올리면 보인다. */
export function EnumBadge({ table, value }: { table: Record<string, { label: string; tone: Tone }>; value: string | null | undefined }) {
  if (value == null) return <span className={s.na}>–</span>;
  const e = entry(table, value);
  return (
    <Badge tone={e.tone} title={value}>
      {e.label}
    </Badge>
  );
}

/** 사건 결과. 탐지(detected)와 집행 시도(enforce_attempted)는 차단으로 표시하지 않는다. */
export function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  return <EnumBadge table={OUTCOME} value={outcome} />;
}

/** 판정. UNKNOWN은 회색이고 PASS처럼 보이지 않는다. */
export function VerdictBadge({ verdict }: { verdict: string | null | undefined }) {
  return <EnumBadge table={VERDICT} value={verdict} />;
}

/** 자료 등급. EXAMPLE은 항상 눈에 띄게 표시한다. */
export function OriginBadge({ origin, detail }: { origin: string | null | undefined; detail?: string | null }) {
  if (origin == null) return <span className={s.na}>–</span>;
  const e = entry(DATA_ORIGIN, origin);
  const title = detail ? `${origin} (${detail})` : origin;
  if (origin === "EXAMPLE") {
    return (
      <span className={`${s.badge} ${s.example}`} title={title}>
        예시
      </span>
    );
  }
  return (
    <Badge tone={e.tone} title={title} plain>
      {e.label}
    </Badge>
  );
}
