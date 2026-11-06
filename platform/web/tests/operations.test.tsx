import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { CHECK_LABELS, enforceReady, type Operations } from "../src/api/operations";
import { BundleApplyRequest, ModeRequests, RuntimeOperationsPage } from "../src/pages/RuntimeOperations";
import { setOperationToken } from "../src/api/operationSession";
import { mockApi, renderAt } from "./helpers";

beforeEach(() => { setOperationToken(""); });
function enterToken(value = "test-only") {
  fireEvent.change(screen.getByLabelText("운영 토큰"), { target: { value } });
  fireEvent.click(screen.getByRole("button", { name: "토큰 저장" }));
}
function data(): Operations {
  return { actor: { id: "approver-01", role: "approver" }, items: [{ id: "op-01", server: "server-01", kind: "bundle.apply", state: "requested", requested_by: "operator-01", created_at: new Date().toISOString(), approval_required: true, approvals: [] }],
    agents: [{ server: "server-01", at: new Date().toISOString(), last_poll_at: new Date().toISOString(), current: { bundle_id: "old", approval: "verified" }, bundles: [{ bundle_id: "old", sha256: "b".repeat(64) }, { bundle_id: "previous", sha256: "c".repeat(64) }], policies: [{ name: "runtime-tg-secret-01", method: "override_lsm", expected: { bundle_id: "old", approval: "verified", policy_id: "7", mode: "monitor", spec_sha256: "a".repeat(64) }, checks: Object.fromEntries(Object.keys(CHECK_LABELS).map(k => [k, true])), evidence_sha256: [] }] }],
    lab_self_approval: false, monitor_seconds: 86400 };
}

it("모든 체크와 최신 에이전트 보고를 요구한다", () => {
  const agent = data().agents[0];
  const row = agent.policies[0];
  expect(enforceReady(agent, row)).toBe(true);
  for (const key of Object.keys(CHECK_LABELS)) expect(enforceReady(agent, { ...row, checks: { ...row.checks, [key]: false } })).toBe(false);
  expect(enforceReady({ ...agent, at: new Date(Date.now() - 31_000).toISOString() }, row)).toBe(false);
});

it("차단은 Sigkill 확인 입력을 요구하고 탐지 복귀는 조건과 독립이다", async () => {
  mockApi({ "/api/runtime/operations": data() });
  renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <ModeRequests />);
  enterToken();
  fireEvent.click(screen.getByText("전환 조건 조회"));
  const enforce = await screen.findByRole("button", { name: "차단으로 전환" });
  expect((enforce as HTMLButtonElement).disabled).toBe(true);
  expect((screen.getByRole("button", { name: "탐지로 전환" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.change(screen.getByLabelText("Sigkill 확인 입력"), { target: { value: "ENGINE_RESTART" } });
  expect((enforce as HTMLButtonElement).disabled).toBe(false);
  expect(screen.getByText(/엔진 종료 → Gateway 503 → 재시작/)).toBeTruthy();
  fireEvent.click(enforce);
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const call = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string).confirmation).toBe("ENGINE_RESTART");
  expect(JSON.parse(call[1]!.body as string).requested_by_role).toBeUndefined();
});

it("근거가 없으면 차단 버튼을 비활성화한다", async () => {
  const result = data();
  result.agents[0].policies[0].checks.evidence = false;
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <ModeRequests />);
  enterToken();
  fireEvent.change(screen.getByLabelText("Sigkill 확인 입력"), { target: { value: "ENGINE_RESTART" } });
  fireEvent.click(screen.getByText("전환 조건 조회"));
  expect((await screen.findByRole("button", { name: "차단으로 전환" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/미충족 · 방식·spec 해시·커널/)).toBeTruthy();
});

it.each(["정책", "운영"])("%s 화면에 모니터 창 시작·건수·미검토·오탐을 표시한다", async (page) => {
  const result = data();
  const row = result.agents[0].policies[0];
  row.checks.monitor_period = false;
  row.monitor_windows = [{ node: "node-01", since: "2026-11-06T00:00:00Z", until: "2026-11-06T00:00:10Z",
    continuous: true, event_count: 3, reviewed_event_count: 2, false_positive_count: 1 }];
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", page === "정책" ? <ModeRequests /> : <RuntimeOperationsPage />);
  enterToken();
  if (page === "정책") fireEvent.click(screen.getByText("전환 조건 조회"));
  expect(await screen.findByText(/node-01 · 창 시작/)).toBeTruthy();
  expect(screen.getByText(/이벤트 3건 · 검토 2\/3건 · 오탐 1건 · 미검토 있음/)).toBeTruthy();
  expect(screen.getByText(/미충족 · 모니터 기간/)).toBeTruthy();
  if (page === "정책") expect((screen.getByRole("button", { name: "차단으로 전환" }) as HTMLButtonElement).disabled).toBe(true);
});

it.each([
  ["static", null, true, true, "확인: 사전 점검으로 확인"],
  ["static", null, true, false, "확인: 사전 점검으로 확인"],
  ["live_trial", null, true, true, "확인: 실측 시험으로 확인"],
  [null, "evidence_required", false, false, "미확인: 동일 노드·커널·방식·정책 spec의 실측 시험 필요"],
  [null, "static_unavailable", false, true, "거부: 사전 점검에서 불가 (실측 근거보다 우선)"],
] as const)("능력 근거 %s / %s에 따라 체크리스트와 차단 버튼을 표시한다", async (basis, reason, capability, evidence, label) => {
  const result = data();
  const agent = result.agents[0];
  const row = agent.policies[0];
  row.method = "sigkill_fd_install";
  row.capability_basis = basis;
  row.capability_reason = reason;
  row.checks.capability = capability;
  row.checks.evidence = evidence;
  expect(enforceReady(agent, row)).toBe(capability && evidence);
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/enforce/tetragon", "/runtime/enforce/tetragon", <ModeRequests />);
  enterToken();
  fireEvent.change(screen.getByLabelText("Sigkill 확인 입력"), { target: { value: "ENGINE_RESTART" } });
  fireEvent.click(screen.getByText("전환 조건 조회"));
  expect(await screen.findByText(label)).toBeTruthy();
  expect((screen.getByRole("button", { name: "차단으로 전환" }) as HTMLButtonElement).disabled).toBe(!(capability && evidence));
});

it("요청 목록에 승인과 에이전트 조회 시각을 표시한다", async () => {
  mockApi({ "/api/runtime/operations": data() });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  fireEvent.click(screen.getByText("운영 요청 조회"));
  expect(await screen.findByRole("button", { name: "새 묶음 적용 · server-01" })).toBeTruthy();
  expect(screen.getByText(/에이전트 마지막 조회/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "승인" })).toBeTruthy();
});

it("정책 적용 요청은 토큰과 기대 current를 전송한다", async () => {
  mockApi({ "/api/runtime/operations": { id: "op-01" } });
  renderAt("/policies/test", "/policies/:id", <BundleApplyRequest policyId="policy-01" revision={2} environmentId="env-01" environmentRevision={1} confirmed override="" />);
  enterToken();
  fireEvent.change(screen.getByLabelText("서버 ID"), { target: { value: "server-01" } });
  fireEvent.change(screen.getByLabelText("현재 적용 묶음 ID"), { target: { value: "old" } });
  fireEvent.click(screen.getByText("적용 요청"));
  expect(await screen.findByText(/적용 요청 등록: op-01/)).toBeTruthy();
  const [, init] = vi.mocked(fetch).mock.calls[0];
  expect((init!.headers as Record<string, string>).Authorization).toBe("Bearer test-only");
  expect(JSON.parse(init!.body as string).expected.bundle_id).toBe("old");
});

it("목록 우선 표시·상태 필터·행 상세·세부 사유와 증거를 지원한다", async () => {
  const result = data();
  result.items.push({ ...result.items[0], id: "failed-01", state: "failed", approvals: [], result: {
    code: "VALIDATION_FAILED", reason_code: "BUNDLE_ROLES_MISSING", dry_run: true, evidence_sha256: ["e".repeat(64)], steps: [{ stage: "validation", state: "failed" }],
  } });
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  const list = screen.getByRole("region", { name: "운영 요청 목록" });
  expect(await within(list).findByText(/BUNDLE_ROLES_MISSING/)).toBeTruthy();
  expect(within(list).getByText("e".repeat(64))).toBeTruthy();
  expect(within(list).getByText(/요청됨 ✓ → 승인 생략 → 가져감 ✓ → 실행 생략 → 결과 ✓/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("상태 필터"), { target: { value: "failed" } });
  expect(within(list).getAllByRole("button", { name: /새 묶음 적용/ })).toHaveLength(1);
  fireEvent.click(within(list).getByRole("button", { name: /새 묶음 적용/ }));
  expect(screen.getByRole("region", { name: "운영 요청 상세" })).toBeTruthy();
  expect(screen.getByText("검증: 실패")).toBeTruthy();
  expect(screen.queryByRole("region", { name: "묶음 되돌리기" })).toBeNull();
});

it("dry-run 검증 통과는 별도 종결 상태로 표시하고 실패 필터에서 제외한다", async () => {
  const result = data();
  result.items = [
    { ...result.items[0], id: "checked-01", state: "checked", result: {
      code: "DRY_RUN", dry_run: true, evidence_sha256: [], steps: [{ stage: "validation", state: "passed" }],
    } },
    { ...result.items[0], id: "failed-01", state: "failed", result: {
      code: "VALIDATION_FAILED", dry_run: true, evidence_sha256: [], steps: [{ stage: "validation", state: "failed" }],
    } },
  ];
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  const list = screen.getByRole("region", { name: "운영 요청 목록" });
  expect(await within(list).findByText("실행 없이 확인 완료", { selector: "strong" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("상태 필터"), { target: { value: "checked" } });
  expect(within(list).getAllByRole("button", { name: /새 묶음 적용/ })).toHaveLength(1);
  expect(within(list).getByText(/가져감 ✓ → 실행 생략 → 결과 ✓/)).toBeTruthy();
  expect(within(list).queryByText(/VALIDATION_FAILED/)).toBeNull();
  fireEvent.click(within(list).getByRole("button", { name: /새 묶음 적용/ }));
  const detail = screen.getByRole("region", { name: "운영 요청 상세" });
  expect(within(detail).getByText(/실행 없이 확인 완료/)).toBeTruthy();
  expect(within(detail).getByText("검증: 통과")).toBeTruthy();
  expect(within(detail).getByText(/실행 생략 → 결과 ✓/)).toBeTruthy();
  fireEvent.change(screen.getByLabelText("상태 필터"), { target: { value: "failed" } });
  expect(within(list).getAllByRole("button", { name: /새 묶음 적용/ })).toHaveLength(1);
  expect(within(list).getByText("실패", { selector: "strong" })).toBeTruthy();
  expect(within(list).queryByText(/DRY_RUN/)).toBeNull();
});

it.each(["viewer", "runtime_operator"] as const)("%s는 다른 사람 요청의 승인 버튼을 받지 않는다", async role => {
  const result = data(); result.actor = { id: "other-01", role };
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  await screen.findByText(new RegExp(`\\(${role}\\)`));
  expect(screen.queryByRole("button", { name: "승인" })).toBeNull();
});

it("자기 요청 승인은 기본 비활성이고 LAB 설정에서만 가능하다", async () => {
  const result = data(); result.actor = { id: "operator-01", role: "approver" };
  mockApi({ "/api/runtime/operations": result, "/api/runtime/operations/op-01/approve": {} });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  expect((await screen.findByRole("button", { name: "승인" }) as HTMLButtonElement).disabled).toBe(true);
  result.lab_self_approval = true;
  fireEvent.click(screen.getByRole("button", { name: "운영 요청 조회" }));
  await screen.findByText(/LAB 자기 승인 허용/);
  expect((screen.getByRole("button", { name: "승인" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "승인" }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).endsWith("/op-01/approve"))).toBe(true));
});

it("운영 요청자는 LAB에서 자기 요청만 승인한다", async () => {
  const result = data(); result.actor = { id: "operator-01", role: "runtime_operator" }; result.lab_self_approval = true;
  result.items.push({ ...result.items[0], id: "other-op", requested_by: "another-operator" });
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  await screen.findByText(/LAB 자기 승인 허용/);
  expect(screen.getAllByRole("button", { name: "승인" })).toHaveLength(1);
});

it("보고된 서버·현재 적용 묶음과 목록 선택으로 되돌리기를 요청한다", async () => {
  const result = data(); result.actor = { id: "operator-01", role: "runtime_operator" };
  result.agents.push({ ...result.agents[0], server: "server-02", current: { bundle_id: "second-current", approval: "verified" }, bundles: [{ bundle_id: "second-previous", sha256: "d".repeat(64) }] });
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  fireEvent.click(await screen.findByRole("button", { name: "이전 묶음으로 되돌리기" }));
  const form = screen.getByRole("region", { name: "묶음 되돌리기" });
  expect(within(form).getByText("old")).toBeTruthy();
  fireEvent.change(within(form).getByLabelText("서버"), { target: { value: "server-02" } });
  expect(within(form).getByText("second-current")).toBeTruthy();
  fireEvent.change(within(form).getByLabelText("되돌릴 묶음"), { target: { value: "second-previous" } });
  fireEvent.click(within(form).getByRole("button", { name: "되돌리기 요청" }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const call = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string)).toEqual({ kind: "bundle.switch", server: "server-02", bundle_id: "second-previous", expected: { bundle_id: "second-current", approval: "verified" } });
});

it("구형 에이전트의 묶음 목록 누락은 안전하게 안내한다", async () => {
  const result = data(); result.actor = { id: "operator-01", role: "runtime_operator" }; delete result.agents[0].bundles;
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  fireEvent.click(await screen.findByRole("button", { name: "이전 묶음으로 되돌리기" }));
  expect(screen.getByText(/에이전트가 보고한 묶음 목록이 없습니다/)).toBeTruthy();
  expect((screen.getByRole("button", { name: "되돌리기 요청" }) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText("고급 · 직접 입력")).toBeTruthy();
});

it("세션 토큰을 화면 재진입에서도 사용하고 지우면 인증 상태를 비운다", async () => {
  mockApi({ "/api/runtime/operations": data() });
  const first = renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken("session-secret");
  await screen.findByText(/현재 역할: 승인자/);
  expect(sessionStorage.getItem("byul.runtime.operation-token")).toBe("session-secret");
  expect(screen.queryByDisplayValue("session-secret")).toBeNull();
  expect(screen.queryByText("session-secret")).toBeNull();
  first.unmount();
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  await screen.findByText(/현재 역할: 승인자/);
  expect(screen.queryByLabelText("운영 토큰")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "토큰 지우기" }));
  expect(screen.getByLabelText("운영 토큰")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /새 묶음 적용/ })).toBeNull();
  expect(sessionStorage.getItem("byul.runtime.operation-token")).toBeNull();
});

it("sessionStorage가 차단돼도 메모리 토큰으로 요청한다", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  mockApi({ "/api/runtime/operations": data() });
  const first = renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken("memory-secret");
  await screen.findByText(/현재 역할: 승인자/);
  first.unmount();
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  await screen.findByText(/현재 역할: 승인자/);
  expect((vi.mocked(fetch).mock.calls.at(-1)![1]!.headers as Record<string, string>).Authorization).toBe("Bearer memory-secret");
});

it("열린 되돌리기 양식도 새 상태 보고의 현재 적용 묶음을 반영한다", async () => {
  const result = data(); result.actor = { id: "operator-01", role: "runtime_operator" };
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  fireEvent.click(await screen.findByRole("button", { name: "이전 묶음으로 되돌리기" }));
  const form = screen.getByRole("region", { name: "묶음 되돌리기" });
  expect(within(form).getByText("old")).toBeTruthy();
  result.agents[0].current!.bundle_id = "new-current";
  fireEvent.click(screen.getByRole("button", { name: "운영 요청 조회" }));
  expect(await within(form).findByText("new-current")).toBeTruthy();
});

it("오래된 상태 보고에서는 기본 되돌리기를 막고 고급 입력은 별도로 연다", async () => {
  const result = data(); result.actor = { id: "operator-01", role: "runtime_operator" };
  result.agents[0].at = new Date(Date.now() - 31_000).toISOString();
  mockApi({ "/api/runtime/operations": result });
  renderAt("/runtime/operations", "/runtime/operations", <RuntimeOperationsPage />);
  enterToken();
  fireEvent.click(await screen.findByRole("button", { name: "이전 묶음으로 되돌리기" }));
  const form = screen.getByRole("region", { name: "묶음 되돌리기" });
  fireEvent.change(within(form).getByLabelText("되돌릴 묶음"), { target: { value: "previous" } });
  expect((within(form).getByRole("button", { name: "되돌리기 요청" }) as HTMLButtonElement).disabled).toBe(true);
  const details = within(form).getByText("고급 · 직접 입력").closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  fireEvent.change(within(form).getByLabelText("되돌릴 묶음 ID"), { target: { value: "manual-previous" } });
  expect((within(form).getByRole("button", { name: "되돌리기 요청" }) as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(within(form).getByRole("button", { name: "되돌리기 요청" }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const call = vi.mocked(fetch).mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(call[1]!.body as string).bundle_id).toBe("manual-previous");
});
