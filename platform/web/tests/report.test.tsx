import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi, renderAt } from "./helpers";
import { FullReport, markText } from "../src/pages/ReportView";
import type { ReportCheck, ReportView } from "../src/api/types";

const side = (security: string) => ({ result_id: "RES-01M598RQXY78K2P5F8YDVJB902", result_revision: 1, security, functional: "PASS", performance: "UNKNOWN", verification_level: "EXAMPLE", reasons: [] as string[] });
const SUP_REASON = "security: FAIL because code_selection_confirmed_violations nonempty (observed code_selection_confirmed_violations=[{'field': 'mm_processor_kwargs.code_revision'}])";

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
  limitations: ["정책 DRAFT: 공식 아님", "관측 수집 중단으로 일부 점검 미확인"],
  manual_required: ["REQ-03"],
  not_selected: [],
  approval: null,
  inputs_manifest: [],
  checks: [
    check("REQ-01", { headline: "FAIL", candidate: { ...side("FAIL"), reasons: [SUP_REASON] }, comparison: "REGRESSED", marks: [{ kind: "exception", status: "EXPIRED", reason: "임시", expires_at: "2026-10-01T00:00:00Z" }] }),
    check("REQ-02", { headline: "FAIL", baseline: side("FAIL"), candidate: side("FAIL"), comparison: "STILL_FAILING", marks: [{ kind: "manual_declared" }] }),
    check("REQ-03", { headline: "UNKNOWN", baseline: side("UNKNOWN"), candidate: side("UNKNOWN"), comparison: "UNKNOWN", evidence_source: null, marks: [{ kind: "gap", value: "NO_EXECUTOR" }, { kind: "tested_while_undetermined" }] }),
  ],
};

describe("검증 보고서 화면", () => {
  it("보고서 자료 등급과 비공식 상태, 원본 한계 및 자동 실행 건수를 표시한다", async () => {
    mockApi({});
    const { container } = renderAt("/update/results", "/update/results", <FullReport report={report} rawChecks={null} selected="REQ-01" onSelect={() => {}} />);
    expect(screen.getByText("예시")).toBeTruthy();
    expect(screen.getByText("비공식 보고서")).toBeTruthy();
    expect(screen.getByText("정책 DRAFT: 공식 아님")).toBeTruthy();
    expect(container.textContent).toContain("자동 실행 2");
    expect(container.textContent).not.toContain("자동 측정");
    expect(screen.getByText("위험 1건")).toBeTruthy();
    expect(screen.getByText("관측 수집 중단으로 일부 점검 미확인")).toBeTruthy();
    expect(screen.getByText("미충족 1건")).toBeTruthy();
    expect(screen.getByText("미확인 1건")).toBeTruthy();
    expect(screen.getByText("점검 목록 미완결: OBS")).toBeTruthy();
    // 질문은 텍스트로만 들어간다
    expect(document.querySelector("b")).toBeNull();
    expect(screen.getAllByText("REQ-01 질문 <b>굵게</b>?").length).toBeGreaterThan(0);
  });

  it("과거 판정은 유지하고 근거 부족을 안내하며 기술 사유와 원본 행을 열 수 있다", () => {
    mockApi({});
    renderAt("/update/results", "/update/results", <FullReport report={report} rawChecks={{ "REQ-01": { original: "raw-value" } }} selected="REQ-01" onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    expect(drawer.getByText("후보의 세부 판정 근거를 설명할 수 있는 관측 기록이 없습니다.")).toBeTruthy();
    const details = drawer.getByText("상세 근거").closest("details")!;
    expect(details.open).toBe(false);
    fireEvent.click(drawer.getByText("상세 근거"));
    expect(details.textContent).toContain(SUP_REASON);
    fireEvent.click(drawer.getByRole("tab", { name: "RPT 원본 행" }));
    expect(drawer.getByRole("tabpanel").textContent).toContain("raw-value");
  });

  it.each(["REGRESSED", "NOT_COMPARABLE"] as const)("SUP-08의 로드 근거와 비교 상태를 표시한다 (%s)", (comparison) => {
    mockApi({});
    const row = check("SUP-08", {
      headline: "FAIL", comparison,
      candidate: { ...side("FAIL"), data_origin: "MEASURED", finding: {
        code: "forbidden_code_revision_imported", field: "mm_processor_kwargs.code_revision", revision: "recorded-revision",
      } },
    });
    const update = { ...report, checks: [row], targets: {
      baseline: { name: "vllm", version: "0.7.0" },
      candidate: { name: "vllm", version: "0.30.0" },
    } };
    renderAt("/update/results", "/update/results", <FullReport report={update} rawChecks={null} selected="SUP-08" onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    expect(within(drawer.getByText("기준 · vLLM 0.7.0").parentElement!).getByText("보안 충족")).toBeTruthy();
    expect(within(drawer.getByText("후보 · vLLM 0.30.0").parentElement!).getByText("보안 미충족")).toBeTruthy();
    const heading = comparison === "REGRESSED"
      ? "기준 판정은 보안 충족, 후보 실측에서 요청에 지정한 코드가 로드됨"
      : "후보 vLLM 0.30.0 · 요청에 지정한 코드가 실제로 로드됨";
    expect(drawer.getByRole("heading", { name: heading })).toBeTruthy();
    if (comparison === "NOT_COMPARABLE") expect(drawer.getByText(/비교 조건을 충족하지 못했으며/)).toBeTruthy();
    const details = drawer.getByText("상세 근거").closest("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("recorded-revision");
    expect(drawer.getByText(/기준 vLLM 0.7.0은 보안 충족으로 기록됐습니다/)).toBeTruthy();
    expect(screen.getByText("두 판본에 모두 기록된 보안 판정 중 1건이 달라졌습니다.")).toBeTruthy();
    expect(drawer.getByText(/로드 기록과 저장된 모듈의 해시가 일치했습니다/)).toBeTruthy();
  });

  it("단일 보고서에서도 누락과 미확인 관측을 표시한다", () => {
    mockApi({});
    const row = check("SUP-03", {
      headline: "UNKNOWN", baseline: null, candidate: null,
      single: { ...side("UNKNOWN"), functional: "UNKNOWN", missing_observations: ["selected_template_sha256"],
        unverified_observations: ["template_consumer_verified"], decisions: (["security", "functional"] as const).map((axis) => ({
          axis, verdict: "UNKNOWN", observations: [], unmet: [],
          missing: ["selected_template_sha256"], policy_missing: ["internal_policy_key"], limits: ["no_pass_rule"],
        })) },
    });
    const audit = { ...report, mode: "audit", checks: [row], targets: { single: { name: "sglang", version: "0.5.1" } } };
    renderAt("/update/results", "/update/results", <FullReport report={audit} rawChecks={null} selected="SUP-03" onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    expect(drawer.getByText("검증 대상 · sglang 0.5.1")).toBeTruthy();
    expect(drawer.getByText("보안 미확인")).toBeTruthy();
    expect(drawer.getByText(/수집 기록이 빠진 관측은 선택된 템플릿의 해시입니다/)).toBeTruthy();
    expect(drawer.getByText(/템플릿의 실제 소비 확인입니다/)).toBeTruthy();
    expect(drawer.getAllByText(/선택된 템플릿의 해시입니다/)).toHaveLength(1);
    expect(drawer.getAllByText(/보안·기능 — 이 축의 충족을 확인할 판정 규칙이 없습니다/)).toHaveLength(1);
    expect(drawer.getByText(/필요한 정책 설정값 1개/)).toBeTruthy();
    expect(drawer.getByText("상세 근거").closest("details")?.textContent).toContain("internal_policy_key");
  });

  it("평문 API 요청의 관측과 미확인 범위를 함께 표시한다", () => {
    mockApi({});
    const row = check("PLT-08", {
      headline: "FAIL", baseline: null, candidate: null,
      single: { ...side("FAIL"), data_origin: "MEASURED", finding: { code: "forbidden_plaintext_succeeded" },
        unverified_observations: ["api_transport_scope_complete"] },
    });
    const audit = { ...report, mode: "audit", checks: [row] };
    renderAt("/update/results", "/update/results", <FullReport report={audit} rawChecks={null} selected="PLT-08" onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    expect(drawer.getByRole("heading", { name: "평문 연결이 금지된 구간에서 API 요청이 성공함" })).toBeTruthy();
    expect(drawer.getByText(/평문 HTTP 요청을 보냈고, API가 정상 응답했습니다/)).toBeTruthy();
    expect(drawer.getByText(/대상 API 전송 구간 전체의 관측입니다/)).toBeTruthy();
  });

  it("예시 측의 판정은 표시하고 실측 확인 문구는 표시하지 않는다", () => {
    mockApi({});
    const row = check("PLT-08", { headline: "FAIL",
      baseline: { ...side("PASS"), data_origin: "MEASURED" },
      candidate: { ...side("FAIL"), data_origin: "EXAMPLE", finding: { code: "forbidden_plaintext_succeeded" } },
    });
    renderAt("/update/results", "/update/results", <FullReport report={{ ...report, checks: [row] }}
      rawChecks={null} selected="PLT-08" onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    expect(drawer.getByTitle("MEASURED")).toBeTruthy();
    expect(drawer.getByText("예시")).toBeTruthy();
    expect(drawer.getByText("보안 미충족")).toBeTruthy();
    expect(drawer.queryByRole("heading", { name: /평문 연결이 금지된 구간에서 API 요청이 성공함/ })).toBeNull();
  });

  it("기존 전용 문구가 없는 점검도 축별 관측과 미확인 조건을 표시한다", () => {
    mockApi({});
    const row = check("REQ-01", {
      baseline: null, candidate: null, single: { ...side("PASS"), decisions: [
        { axis: "security", verdict: "PASS", criterion: "시험 대상에 보호 정책이 적용된다.",
          observations: [{ key: "routes_tested", kind: "count", value: 2, operators: ["nonempty"] },
            { key: "unknown_technical_key", kind: "recorded" }],
          missing: [], unmet: [], policy_missing: [], limits: [] },
        { axis: "performance", verdict: "UNKNOWN", observations: [], missing: [], unmet: [],
          policy_missing: [], limits: ["cpu_performance_unverified"] },
      ] },
    });
    renderAt("/update/results", "/update/results", <FullReport report={{ ...report, mode: "audit", checks: [row] }} rawChecks={null} selected="REQ-01" onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    expect(drawer.getByRole("heading", { name: "검증 대상 · 보안 충족 근거" })).toBeTruthy();
    expect(drawer.getByText(/실제로 시험한 요청 경로 · 2건 기록 · 항목이 기록됨/)).toBeTruthy();
    expect(drawer.getByText("성능 — 이 CPU 비교에서는 성능을 검증하지 않았습니다.")).toBeTruthy();
    const details = drawer.getByText("상세 근거").closest("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("unknown_technical_key");
    expect(Array.from(drawer.getByText(/실제로 시험한 요청 경로/).closest("ul")!.children)
      .every((item) => !item.textContent?.includes("unknown_technical_key"))).toBe(true);
  });

  it.each([
    ["REQ-03", "nonadmin_outcomes", "관리자 권한이 없는 사용자로 작업을 요청한 결과", "PASS"],
    ["PLT-06", "worker_isolation_violations", "작업 프로세스 사이에서 확인된 격리 위반", "FAIL"],
    ["OBS-06", "record_copy_inventory", "확인 대상 기록 복사본 목록", "UNKNOWN"],
  ] as const)("새 한국어 관측명을 기본 화면에 표시하고 키는 상세 근거에 둔다 (%s)", (id, key, name, verdict) => {
    mockApi({});
    const row = check(id, {
      headline: verdict, baseline: null, candidate: null,
      single: { ...side(verdict), decisions: [{
        axis: "security", verdict, observations: verdict === "UNKNOWN" ? [] : [{ key, kind: "count", value: 1 }],
        missing: verdict === "UNKNOWN" ? [key] : [], unmet: [], policy_missing: [], limits: [],
      }] },
    });
    renderAt("/update/results", "/update/results", <FullReport report={{ ...report, mode: "audit", checks: [row] }} rawChecks={null} selected={id} onSelect={() => {}} />);
    const drawer = within(screen.getByLabelText("상세"));
    const basic = drawer.getByText(name, { exact: false, selector: "li" });
    expect(basic.textContent).not.toContain(key);
    const details = drawer.getByText("상세 근거").closest("details")!;
    expect(details.open).toBe(false);
    expect(details.textContent).toContain(key);
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
