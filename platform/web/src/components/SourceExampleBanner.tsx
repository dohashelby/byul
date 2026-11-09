import type { Meta } from "../api/types";
import { Banner } from "./Misc";

/** 출처 연결 모드와 개별 자료의 EXAMPLE 등급은 각각 표시한다. */
export function SourceExampleBanner({ meta }: { meta: Meta | undefined }) {
  if (!meta?.example_mode) return null;
  const examples = meta.sources.filter((source) => source.mode === "example");
  return (
    <Banner kind="example">
      {meta.sources.some((source) => source.mode !== "example") ? "예시 자료" : "예시 데이터"} · {examples.map((source) => source.name).join(", ")} · 해당 자료는 공식 판정 근거 제외
    </Banner>
  );
}
