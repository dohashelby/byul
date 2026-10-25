// 기간 선택과 시간대별 묶기. 묶기는 표시용 집계이며 판정을 다시 계산하지 않는다.

export type PeriodKey = "1h" | "24h" | "7d" | "all" | "custom";

export const PERIODS: { key: PeriodKey; label: string }[] = [
  { key: "1h", label: "1시간" },
  { key: "24h", label: "24시간" },
  { key: "7d", label: "7일" },
  { key: "all", label: "전체" },
  { key: "custom", label: "직접 지정" },
];

const HOUR = 3_600_000;
const DURATION: Partial<Record<PeriodKey, number>> = { "1h": HOUR, "24h": 24 * HOUR, "7d": 7 * 24 * HOUR };

export type Range = { from: number | null; to: number | null };

export function resolveRange(period: PeriodKey, from: string | null, to: string | null, now = Date.now()): Range {
  if (period === "all") return { from: null, to: null };
  if (period === "custom") {
    const f = from ? Date.parse(from) : NaN;
    const t = to ? Date.parse(to) : NaN;
    return { from: Number.isNaN(f) ? null : f, to: Number.isNaN(t) ? null : t };
  }
  return { from: now - (DURATION[period] ?? 24 * HOUR), to: null };
}

export function inRange(iso: string | null | undefined, range: Range): boolean {
  if (range.from == null && range.to == null) return true;
  if (!iso) return false;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return false;
  return (range.from == null || t >= range.from) && (range.to == null || t <= range.to);
}

const STEPS = [60_000, 5 * 60_000, 15 * 60_000, HOUR, 3 * HOUR, 6 * HOUR, 24 * HOUR, 7 * 24 * HOUR];

/** 구간 길이에 맞춰 묶는 간격을 고른다(막대 60개 안팎). */
export function bucketSize(spanMs: number): number {
  const target = spanMs / 60;
  return STEPS.find((s) => s >= target) ?? STEPS[STEPS.length - 1];
}

export type Bucket<K extends string> = { start: number; label: string } & Record<K, number>;

/** 시각별로 묶어 키별 건수를 센다. 시각이 없는 항목은 세지 않고 따로 돌려준다. */
export function bucketize<T, K extends string>(
  items: T[],
  timeOf: (item: T) => string | null | undefined,
  keyOf: (item: T) => K,
  keys: readonly K[],
  range: Range,
): { buckets: Bucket<K>[]; size: number; untimed: number } {
  const times: { t: number; k: K }[] = [];
  let untimed = 0;
  for (const it of items) {
    const iso = timeOf(it);
    const t = iso ? Date.parse(iso) : NaN;
    if (Number.isNaN(t)) {
      untimed += 1;
      continue;
    }
    times.push({ t, k: keyOf(it) });
  }
  if (times.length === 0 && range.from == null) return { buckets: [], size: HOUR, untimed };
  const min = range.from ?? Math.min(...times.map((x) => x.t));
  // 기간을 "전체"로 두면 자료가 있는 구간만 그린다(serving-runtime과 같음). 정한 기간이면 지금까지.
  const dataMax = times.length ? Math.max(...times.map((x) => x.t)) : Date.now();
  const max = range.to ?? (range.from == null ? dataMax : Math.max(Date.now(), dataMax));
  const size = bucketSize(Math.max(max - min, 60_000));
  const first = Math.floor(min / size) * size;
  const count = Math.min(Math.floor((max - first) / size) + 1, 500);
  const buckets: Bucket<K>[] = [];
  for (let i = 0; i < count; i++) {
    const start = first + i * size;
    const b = { start, label: formatBucket(start, size) } as Bucket<K>;
    for (const k of keys) (b as Record<string, number>)[k] = 0;
    buckets.push(b);
  }
  for (const { t, k } of times) {
    const i = Math.floor((t - first) / size);
    if (i >= 0 && i < buckets.length && keys.includes(k)) (buckets[i] as Record<string, number>)[k] += 1;
  }
  return { buckets, size, untimed };
}

function pad(n: number) {
  return String(n).padStart(2, "0");
}

export function formatBucket(ms: number, size: number): string {
  const d = new Date(ms);
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (size >= 24 * HOUR) return `${d.getMonth() + 1}/${d.getDate()}`;
  if (size >= HOUR) return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}시`;
  return hm;
}

export function formatLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** datetime-local 입력값(로컬) ↔ ISO(UTC) */
export function toLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
