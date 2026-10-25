import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import "./helpers";
import { OutcomeBadge, OriginBadge, VerdictBadge } from "../src/components/Badges";
import { JsonView } from "../src/components/JsonView";
import { Value } from "../src/components/Value";
import { OUTCOME, VERDICT } from "../src/lib/labels";
import { bucketize, inRange, resolveRange } from "../src/lib/time";
import { factWarnings, newEnvironment } from "../src/pages/forms";
import { isUnofficial } from "../src/pages/ResultsPage";
import type { VerificationResult } from "../src/api/types";

const exampleVR = Object.values(
  import.meta.glob("../../../contracts/examples/verification-result.json", { eager: true, import: "default" }),
)[0] as VerificationResult;

describe("결과 배지 (3.9)", () => {
  it("탐지·집행 시도를 차단으로 표시하지 않는다", () => {
    for (const outcome of ["detected", "enforce_attempted", "observed"]) {
      const { container, unmount } = render(<OutcomeBadge outcome={outcome} />);
      expect(container.textContent).not.toMatch(/차단|거부 확인/);
      unmount();
    }
    expect(OUTCOME.detected).toEqual({ label: "탐지", tone: "warn" });
    expect(OUTCOME.enforce_attempted).toEqual({ label: "집행 시도", tone: "caution" });
    expect(OUTCOME.denied.tone).toBe("accent");
    expect(OUTCOME.allowed.tone).toBe("neutral");
    expect(OUTCOME.error.tone).toBe("error");
    expect(OUTCOME.unknown.tone).toBe("muted");
    // 탐지·집행 시도·거부 확인은 서로 다른 톤이다
    expect(new Set([OUTCOME.detected.tone, OUTCOME.enforce_attempted.tone, OUTCOME.denied.tone]).size).toBe(3);
  });

  it("거부 확인만 '거부 확인'으로 보인다", () => {
    render(<OutcomeBadge outcome="denied" />);
    expect(screen.getByText("거부 확인")).toBeTruthy();
  });

  it("UNKNOWN 판정은 PASS처럼 보이지 않는다", () => {
    expect(VERDICT.UNKNOWN.tone).not.toBe(VERDICT.PASS.tone);
    const { container } = render(<VerdictBadge verdict="UNKNOWN" />);
    expect(container.textContent).toBe("미확인");
    expect(container.innerHTML).not.toMatch(/accent/);
  });

  it("EXAMPLE 등급은 눈에 띄는 배지로 표시한다", () => {
    const { container } = render(<OriginBadge origin="EXAMPLE" detail="synthetic" />);
    expect(container.textContent).toBe("예시");
    expect(container.querySelector("[title='EXAMPLE (synthetic)']")).toBeTruthy();
  });
});

describe("값 없음 표시", () => {
  it("null은 –, 0이나 빈칸으로 바꾸지 않는다", () => {
    const { container: a } = render(<Value v={null} />);
    expect(a.textContent).toBe("–");
    const { container: b } = render(<Value v={undefined} />);
    expect(b.textContent).toBe("–");
    const { container: c } = render(<Value v={0} />);
    expect(c.textContent).toBe("0");
    const { container: d } = render(<Value v={null} notCollected />);
    expect(d.textContent).toBe("미수집");
    const { container: e } = render(<Value v={false} />);
    expect(e.textContent).toBe("아니오");
  });
});

describe("JSON 보기", () => {
  it("값을 HTML로 해석하지 않는다", () => {
    const { container } = render(<JsonView value={{ x: "<img src=x onerror=alert(1)>", n: null }} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
    expect(container.textContent).toContain("null");
  });

  it("줄 번호와 접기", () => {
    const { container } = render(<JsonView value={{ a: { b: 1 } }} />);
    const fold = within(container).getAllByRole("button", { name: "접기" });
    expect(fold.length).toBe(2);
  });
});

describe("기간과 묶기", () => {
  it("범위 밖과 시각 없는 기록을 거른다", () => {
    const r = resolveRange("1h", null, null, Date.parse("2026-10-25T02:00:00Z"));
    expect(inRange("2026-10-25T01:30:00Z", r)).toBe(true);
    expect(inRange("2026-10-25T00:30:00Z", r)).toBe(false);
    expect(inRange(null, r)).toBe(false);
    expect(inRange(null, resolveRange("all", null, null))).toBe(true);
  });

  it("시각별로 결과를 센다", () => {
    const items = [
      { t: "2026-10-25T01:00:10Z", k: "allowed" as const },
      { t: "2026-10-25T01:00:20Z", k: "denied" as const },
      { t: null, k: "denied" as const },
    ];
    const range = { from: Date.parse("2026-10-25T01:00:00Z"), to: Date.parse("2026-10-25T01:30:00Z") };
    const { buckets, untimed } = bucketize(items, (x) => x.t, (x) => x.k, ["allowed", "denied"] as const, range);
    expect(untimed).toBe(1);
    expect(buckets.reduce((a, b) => a + b.allowed + b.denied, 0)).toBe(2);
  });
});

describe("환경 사실 경고", () => {
  it("false인데 비사용 근거가 없으면 경고한다", () => {
    const env = newEnvironment();
    env.facts = {
      prefix_cache: { value: false, origin: "DECLARED", source: "x" },
      gateway: { value: false, origin: "DECLARED", source: "x", absence_evidence: "배치 문서 3절" },
      dynamic_import: { value: null, origin: null, source: null },
    };
    expect(factWarnings(env).map((w) => w.path)).toEqual(["/facts/prefix_cache/absence_evidence"]);
  });
});

describe("공식 결과 아님 띠", () => {
  it("팩 DRAFT나 EXAMPLE 자료면 공식 결과가 아니다", () => {
    expect(isUnofficial(exampleVR)).toBe(true);
    expect(isUnofficial({ ...exampleVR, data_origin: "MEASURED", pack: { ...exampleVR.pack, review_status: "REVIEWED" } })).toBe(false);
    expect(isUnofficial({ ...exampleVR, data_origin: "MEASURED" })).toBe(true);
  });
});
