import type { Meta } from "../api/types";
import { Link, useSearchParams } from "react-router-dom";
import { carry } from "../lib/url";
import { Banner } from "./Misc";

/** 출처 연결 모드와 개별 자료의 EXAMPLE 등급은 각각 표시한다. */
export function SourceExampleBanner({ meta }: { meta: Meta | undefined }) {
  const [params] = useSearchParams();
  if (!meta?.example_mode) return null;
  const allExample = meta.sources.length > 0 && meta.sources.every((source) => source.mode === "example");
  return (
    <Banner kind="example">
      <strong>{allExample ? "예시 화면" : "일부 예시 자료 포함"}</strong>
      {" · 예시 자료는 검증 결과에 포함되지 않습니다. "}
      <Link to={`/runtime/settings${carry(params)}#system-info`}>자료 출처 보기</Link>
    </Banner>
  );
}
