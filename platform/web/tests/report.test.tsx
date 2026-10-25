import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderAt } from "./helpers";
import { FullReport, markText } from "../src/pages/ReportView";
import type { ReportCheck, ReportView } from "../src/api/types";

const side = (security: string) => ({ result_id: "RES-01M598RQXY78K2P5F8YDVJB902", result_revision: 1, security, functional: "PASS", performance: "UNKNOWN", verification_level: "EXAMPLE" });

const check = (id: string, over: Partial<ReportCheck>): ReportCheck => ({
  check_id: id,
  area: "REQ",
  purpose: "요청·대상 접근 권한",
  question: `${id} 질문 <b>굵게</b>?`,
  review_order: "reference_baseline_first",
  impact: null,
  applicability: "APPLICABLE",
  selected: true,
  headline: "PASS",
  single: null,
  baseline: side("PASS"),
  candidate: side("PASS"),
  comparison: "NO_CHANGE",
  functional_comparison: "NO_CHANGE",
  comparison_reasons: [],
  evidence_source: "automated",
  marks: [],
  ...over,
});

const report: ReportView = {
  report_id: "RPT-01M598RR3HPSJNHA1MENFA6P75",
  revision: 1,
  content_sha256: "a".repeat(64),
  created_at: "2026-10-25T00:00:00Z",
  mode: "update",
  data_origin: "EXAMPLE",
  official: false,
  policy_status: "DRAFT",
  pack_review_status: "DRAFT",
  unofficial_reasons: ["POLICY_DRAFT", "PACK_DRAFT", "EXAMPLE_DATA"],
  overall: "FAIL",
  counts: { selected: 3, fail: 2, unknown: 1, pass: 0, not_applicable: 0, manual_required: 1, not_selected: 0, regressed: 1 },
  incomplete_areas: ["OBS"],
  focus: { regressed: ["REQ-01"], fail: ["REQ-02"], unknown: ["REQ-03"] },
  by_area: [{ name: "REQ", security: "FAIL", inventory_complete: true }],
  by_purpose: [{ name: "요청·대상 접근 권한", security: "FAIL", inventory_complete: true }],
  evidence_counts: { REQ: { manual: 1, automated: 2, not_run: 0 } },
  limitations: ["정책 DRAFT: 공식 아님"],
  manual_required: ["REQ-03"],
  not_selected: [],
  approval: null,
  inputs_manifest: [],
  checks: [
    check("REQ-01", { headline: "FAIL", candidate: side("FAIL"), comparison: "REGRESSED", marks: [{ kind: "exception", status: "EXPIRED", reason: "임시", expires_at: "2026-10-01T00:00:00Z" }] }),
    check("REQ-02", { headline: "FAIL", baseline: side("FAIL"), candidate: side("FAIL"), comparison: "STILL_FAILING", marks: [{ kind: "manual_declared" }] }),
    check("REQ-03", { headline: "UNKNOWN", baseline: side("UNKNOWN"), candidate: side("UNKNOWN"), comparison: "UNKNOWN", evidence_source: null, marks: [{ kind: "gap", value: "NO_EXECUTOR" }, { kind: "tested_while_undetermined" }] }),
  ],
};

describe("검증 보고서 화면", () => {
  it("비공식 사유·종합 판정·주요 결과 묶음을 update 보고서와 같은 문구로 보여준다", async () => {
    mockApi({});
    renderAt("/update/results", "/update/results", <FullReport report={report} rawChecks={null} selected={null} onSelect={() => {}} />);
    expect(screen.getByRole("status").textContent).toBe("공식 아님 — 정책 DRAFT, 검토 전 팩, EXAMPLE 자료");
    expect(screen.getByText("후퇴 1건")).toBeTruthy();
    expect(screen.getByText("미충족 1건")).toBeTruthy();
    expect(screen.getByText("미확인 1건")).toBeTruthy();
    expect(screen.getByText("점검 목록 미완결: OBS")).toBeTruthy();
    // 질문은 텍스트로만 들어간다
    expect(document.querySelector("b")).toBeNull();
    expect(screen.getAllByText("REQ-01 질문 <b>굵게</b>?").length).toBeGreaterThan(0);
  });

  it("미확인 점검을 충족처럼 보이게 하지 않는다", () => {
    mockApi({});
    renderAt("/update/results", "/update/results", <FullReport report={report} rawChecks={null} selected={null} onSelect={() => {}} />);
    const unknownGroup = screen.getByText("미확인 1건").closest("section")!;
    expect(within(unknownGroup).queryByText("보안 충족")).toBeNull();
    expect(within(unknownGroup).getAllByText("보안 미확인").length).toBe(2); // 기준·후보
  });

  it("표시 항목 문구 — 예외는 상태대로(만료를 승인됨으로 쓰지 않음)", () => {
    expect(markText({ kind: "exception", status: "EXPIRED", reason: "임시", expires_at: "2026-10-01T00:00:00Z" })).toBe("예외 만료 (만료 2026-10-01T00:00:00Z, 임시)");
    expect(markText({ kind: "exception", status: "APPROVED", reason: "운영", expires_at: null })).toBe("예외 승인됨 (만료 –, 운영)");
    expect(markText({ kind: "manual_declared" })).toBe("수동 입력(DECLARED, 자기 신고)");
    expect(markText({ kind: "gap", value: "NO_EXECUTOR" })).toBe("실행기 없음");
    expect(markText({ kind: "replaced", source: "manual", revision: 2, reason: "자동 실행으로 대체" })).toBe("대체됨: 이전 수동 증거(rev 2) — 자동 실행으로 대체");
    expect(markText({ kind: "tested_while_undetermined" })).toBe("적용 미정 상태로 시험함");
  });
});
