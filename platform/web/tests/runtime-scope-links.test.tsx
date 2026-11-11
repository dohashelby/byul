import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SOURCE, mockApi, renderAt } from "./helpers";
import { RuntimeObservePage } from "../src/pages/runtime/RuntimeObservePage";
import { RuntimeCheckScope } from "../src/pages/runtime/RuntimeCheckScope";
import { RuntimeEnforcePage } from "../src/pages/runtime/RuntimeEnforcePage";
import { EvidencePage } from "../src/pages/EvidencePage";
import type { RuntimeStatus, RuntimeCheckCoverage } from "../src/api/types";

const STATUS = Object.values(import.meta.glob("../../../contracts/examples/runtime-status.json", { eager: true, import: "default" }))[0] as RuntimeStatus;
const INDEX = Object.values(import.meta.glob("../../../contracts/examples/evidence-index.json", { eager: true, import: "default" }))[0] as { items: object[] };

describe("관측 범위와 증거 연결", () => {
  it("정제 폐기·열린 공백·저장 재시도 상태를 함께 표시한다", async () => {
    const status = structuredClone(STATUS);
    const collector = status.collectors.find((c) => c.detail?.kind === "observation_feed")!;
    if (collector.detail?.kind !== "observation_feed") throw Error("fixture");
    collector.source = "observation_feed_tetragon";
    collector.state = "error";
    Object.assign(collector.detail, { dropped_lines_total: 0, redaction_dropped_lines_total: 10, redaction_loss_detected: true,
      last_redaction_drop_reason: "INVALID_JSON", last_redaction_error_class: "OSError", last_redaction_drop_at: "2026-11-06T00:00:00Z",
      storage_errors_total: 2, last_storage_error_class: "OSError", last_storage_error_at: "2026-11-06T00:00:01Z",
      last_storage_operation: "checkpoint", checkpoint_pending: true });
    Object.assign(collector.detail.writer, { configured: true, state: "reconnecting", open_gap: {
      gap_id: "gap-open1", opened_at: "2026-11-06T00:00:00Z", closed_at: null, attempts: 2, backfill: null } });
    status.collectors = [collector];
    mockApi({ "/api/runtime/status": { item: status, invalid: null, source: SOURCE }, "/api/runtime/events": { items: [], invalid: [], source: SOURCE } });
    renderAt("/runtime/observe/collectors", "/runtime/observe/:sub", <RuntimeObservePage />);
    const row = (await screen.findByText("observation_feed_tetragon")).closest("tr")!;
    expect(row.textContent).toContain("정제 중 폐기 누적 10 · 현재 수집기 0");
    expect(row.textContent).toContain("폐기 원인 INVALID_JSON");
    expect(row.textContent).toContain("체크포인트 저장 재시도 대기");
    expect(row.textContent).toContain("gap-open1");
    expect(row.textContent).toContain("재연결 시도 2회");
  });

  it("관리접근 writer의 오류 시각·원인·누적과 회복 상태를 표시한다", async () => {
    const status = structuredClone(STATUS);
    const collector = status.collectors.find((c) => c.detail?.kind === "observation_feed")!;
    if (collector.detail?.kind !== "observation_feed") throw Error("fixture");
    collector.source = "observation_feed_management_listener";
    collector.state = "ok";
    Object.assign(collector.detail, { writer_write_failure_count: 1, writer_write_failed: false,
      writer_last_write_error_class: "OSError", writer_last_write_error_at: "2026-11-06T00:00:00Z" });
    status.collectors = [collector];
    mockApi({ "/api/runtime/status": { item: status, invalid: null, source: SOURCE }, "/api/runtime/events": { items: [], invalid: [], source: SOURCE } });
    renderAt("/runtime/observe/collectors", "/runtime/observe/:sub", <RuntimeObservePage />);
    const row = (await screen.findByText("observation_feed_management_listener")).closest("tr")!;
    expect(row.textContent).toContain("접근 기록 쓰기 실패 누적 1");
    expect(row.textContent).toContain("OSError");
    expect(row.textContent).toContain("최근 오류");
  });

  it("선정 점검의 미관측·지원 조건과 원증거 링크를 표시한다", async () => {
    const data: RuntimeCheckCoverage = { mapping_version: "test", fixed_sha: "a".repeat(40), pack_revision: 33,
      selected_count: 37, excluded_count: 40, truncated: true, source: { ...SOURCE, mode: "example" },
      items: [{ framework_id: "CHK-OBS-012", question: "보존 기한", devs: ["DEV-12"], sources: ["record_retention"], rules: [],
        condition: "선택 집행", limitation: "관리 JSONL 범위", observation_state: "unobserved", observed_events: 0,
        evidence_refs: [], update: { id: "OBS-12", revision: 4, retired: true, executor: null, sha256: "a".repeat(64) } },
        { framework_id: "CHK-OBS-001", question: "필수 사건", devs: ["DEV-04"], sources: ["gateway"], rules: [],
          condition: "관측 설정", limitation: "생산자 범위", observation_state: "observed", observed_events: 1,
          evidence_refs: [{ evidence_id: "ev1", sha256: "b".repeat(64) }], update: { id: "OBS-01", revision: 4, retired: false, executor: "probe", sha256: "a".repeat(64) } }] };
    mockApi({ "/api/runtime/checks": data });
    renderAt("/runtime/observe/checks", "/runtime/observe/checks", <RuntimeCheckScope />);
    expect(await screen.findByText("CHK-OBS-012")).toBeTruthy();
    const row = screen.getByText("CHK-OBS-012").closest("tr")!;
    expect(within(row).getByText("미관측")).toBeTruthy();
    expect(row.textContent).toContain("관리 JSONL 범위");
    expect(screen.getByRole("link", { name: "ev1" }).getAttribute("href")).toContain("sha256=" + "b".repeat(64));
    expect(screen.getByText(/최근 사건 일부를 조회/)).toBeTruthy();
  });

  it("원증거의 ID와 해시를 함께 대조해 다른 사본을 제외한다", async () => {
    const base = INDEX.items[0] as Record<string, unknown>;
    mockApi({ "/api/evidence": { items: [{ item: { ...base, evidence_id: "ev1", sha256: "a".repeat(64) }, producer: "runtime", index_path: "a" },
      { item: { ...base, evidence_id: "ev1", sha256: "b".repeat(64) }, producer: "runtime", index_path: "b" }], invalid: [], source: SOURCE } });
    renderAt("/evidence?evidence_id=ev1&sha256=" + "b".repeat(64), "/evidence", <EvidencePage />);
    const table = await screen.findByRole("table");
    expect(table.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(table.textContent).toContain("ev1");
  });

  it("L-05b/c의 원판정·증거와 시험 범위를 표시한다", async () => {
    mockApi({ "/api/runtime/lab-report": { item: { state: "ok", report: { cases: [
      { id: "L-05b", title: "secret", status: "passed", summary: "파일 읽기 거부 확인", evidence: ["probe.json"], limitation: "시험 Pod 범위", note: null },
      { id: "L-05c", title: "sigkill", status: "unknown", summary: "종료 미확인", evidence: [], limitation: "GPU 회수 미측정", note: null }] } }, source: SOURCE } });
    renderAt("/runtime/enforce/lab", "/runtime/enforce/:sub", <RuntimeEnforcePage />);
    expect(await screen.findByText("L-05c · sigkill")).toBeTruthy();
    const row = screen.getByText("L-05c · sigkill").closest("tr")!;
    expect(row.textContent).toContain("종료 미확인");
    expect(row.textContent).toContain("GPU 회수 미측정");
    expect(screen.getByText(/probe.json/)).toBeTruthy();
  });
});


it("관측원별 전체·조회·잘림을 구분해 표시한다", async () => {
  const { EventWindowNote } = await import("../src/pages/runtime/EventWindowNote");
  renderAt("/", "/", <EventWindowNote data={{ truncated: true, source_windows: {
    gateway: { returned: 10, total: null, limit: 200, truncated: false },
    hubble: { returned: 50, total: 120, limit: 50, truncated: true },
    tetragon: { returned: 0, total: null, limit: 50, truncated: null },
  } }} />);
  const note = screen.getByLabelText("사건 조회 범위");
  expect(note.textContent).toContain("Hubble 50건 조회 / 원천 전체 120 · 조회 상한 50건 · 일부만 표시");
  expect(note.textContent).toContain("Tetragon 0건 조회 / 원천 전체 미확인 · 조회 상한 50건 · 잘림 여부 미확인");
});

it("봉인 native 근거의 반복 reader 갱신을 현재 감시 정상으로 표시하지 않는다", async () => {
  const status = structuredClone(STATUS);
  const collector = status.collectors[0];
  Object.assign(collector, { source: "native_receipt_observer", state: "error", detail: {
    kind: "native_receipt", capture_state: "closed", producer_updated_at: "2026-11-08T00:00:00Z",
    capture_observed_at: null, producer_fresh: true, capture_fresh: false,
    source_event_freshness: "unknown", future_observation_available: false,
    observed_requests: 1, historical_receipts: 1, source_event_times: [{ pid: 42, start_ticks: 100,
      monotonic_ns: 123456, boot_sha256: "a".repeat(64), pid_namespace: 1, time_namespace: 2, time_offsets_sha256: "b".repeat(64) }] } });
  status.collectors = [collector];
  mockApi({ "/api/runtime/status": { item: status, invalid: null, source: SOURCE }, "/api/runtime/events": { items: [], invalid: [], source: SOURCE } });
  renderAt("/runtime/observe/collectors", "/runtime/observe/:sub", <RuntimeObservePage />);
  const row = (await screen.findByText("native_receipt_observer")).closest("tr")!;
  expect(row.textContent).toContain("봉인 자료(과거)");
  expect(row.textContent).toContain("보존된 요청 근거 1건");
  expect(row.textContent).toContain("native 발생 시각 신선도 미확인");
  expect(row.textContent).toContain("123456ns");
});
