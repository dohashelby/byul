// runtime 현황. 삭제된 runtime 대시보드의 "현황" 화면(renderStatus·renderSummary·renderCharts·renderObservations)을 그대로 옮겼다.
import { useMemo, useState, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useRequestSummary, useRequests, useRuntimeEvents, useRuntimeStatus } from "../../api/hooks";
import type { RuntimeStatus } from "../../api/types";
import { KeyValue } from "../../components/DetailPanel";
import { Dot, Query, StateView, WarningBox, ui } from "../../components/Misc";
import { ChartCard, Donut, HBars, REQUEST_SERIES, TimeBars, TimeLine, type RequestKey } from "../../components/StackedBars";
import { Value } from "../../components/Value";
import { APPROVAL_DETAIL } from "../../lib/labels";
import { bucketize, inRange } from "../../lib/time";
import { carry, useGlobalFilters } from "../../lib/url";
import p from "../pages.module.css";
import { ObservationChip, formatTime, observationState, rt } from "./parts";

type Row = [string, ReactNode, { problem?: boolean; mono?: boolean; title?: string }?];

function Kv({ rows }: { rows: Row[] }) {
  return (
    <KeyValue
      items={rows.map(([k, v, o]) => [
        k,
        <span className={`${o?.mono ? "mono" : ""}`} style={o?.problem ? { color: "var(--error)" } : undefined} title={o?.title}>
          {v === null || v === undefined || v === "" ? "–" : v}
        </span>,
      ])}
    />
  );
}

export function ScopeChips({ status, observeEnv }: { status: RuntimeStatus; observeEnv: string | null }) {
  const external = status.backend.kind !== "mock";
  const name = ({ vllm: "vLLM", sglang: "SGLang" } as Record<string, string>)[status.backend.kind] ?? status.backend.kind;
  return (
    <div className={rt.scopeChips} aria-label="표시 범위">
      <span className={rt.scopeChip}>
        <span className={rt.scopeKey}>요청 환경</span>
        <span className={rt.scopeValue}>{status.environment ?? "미기록"}</span>
      </span>
      <span className={rt.scopeChip}>
        <span className={rt.scopeKey}>관측 환경</span>
        <span className={rt.scopeValue}>{observeEnv ?? "미연결"}</span>
      </span>
      <span className={ui.badge} style={external && !status.backend.healthy ? { color: "var(--error)", background: "var(--error-soft)" } : undefined}>
        {external ? `외부 ${name} 모드 · ${status.backend.healthy ? "연결됨" : "연결 안 됨"} · 정식 GPU 시험(S3B) 전` : "모의 엔진"}
      </span>
    </div>
  );
}

function mostCommon(values: (string | null | undefined)[]): string | null {
  const counts = new Map<string, number>();
  for (const v of values) if (v) counts.set(v, (counts.get(v) ?? 0) + 1);
  let best: string | null = null;
  for (const [v, n] of counts) if (best === null || n > (counts.get(best) ?? 0)) best = v;
  return best;
}

export function RuntimeOverviewPage() {
  const statusQ = useRuntimeStatus();
  const summaryQ = useRequestSummary();
  const requests = useRequests();
  const events = useRuntimeEvents();
  const { range } = useGlobalFilters();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [timeMode, setTimeMode] = useState("total");
  const [mixMode, setMixMode] = useState("results");

  const reqRows = useMemo(() => (requests.data?.items ?? []).filter((x) => inRange(x.recorded_at, range)), [requests.data, range]);
  const observations = useMemo(() => (events.data?.items ?? []).filter((e) => e.observation), [events.data]);
  const chart = bucketize(reqRows, (x) => x.recorded_at, (x) => x.outcome as RequestKey, REQUEST_SERIES.map((x) => x.key), range);

  const reasons = new Map<string, number>();
  for (const x of reqRows) if (x.outcome !== "allowed" && x.outcome !== "unknown" && x.reason_code) reasons.set(x.reason_code, (reasons.get(x.reason_code) ?? 0) + 1);
  const topReasons = [...reasons.entries()].sort((a, b) => b[1] - a[1]);
  // runtime observationRules: Hubble DROPPED 건수 + 규칙별 건수
  const rules = new Map<string, number>();
  const dropped = observations.filter((e) => e.source === "hubble" && e.observation?.verdict?.toLowerCase() === "dropped").length;
  for (const e of observations) if (e.observation?.rule_id) rules.set(e.observation.rule_id, (rules.get(e.observation.rule_id) ?? 0) + 1);
  const ruleRows: [string, number][] = [...(dropped ? ([["Hubble DROPPED", dropped]] as [string, number][]) : []), ...[...rules.entries()].sort((a, b) => b[1] - a[1])];

  return (
    <div className="page">
      <Query q={statusQ}>
        {(res) => {
          if (!res.item) return <StateView kind="invalid" detail={res.invalid?.errors.join(" / ")} />;
          const s = res.item;
          const summary = summaryQ.data;
          const pol = s.policy;
          const eng = s.backend;
          const external = eng.kind !== "mock";
          const counts = summary?.counts;
          const max = counts ? Math.max(1, ...Object.values(counts)) : 1;
          const store = s.store;
          const corruptTotal = store.corrupt_lines.gateway + store.corrupt_lines.engine;
          const col = Object.fromEntries(s.collectors.map((c) => [c.source, c]));
          const origins = s.observations.origins_by_kind;
          const observeEnv = mostCommon(observations.map((e) => e.scope.environment));
          const policyRows: Row[] = [
            ["정책", pol.policy_ref?.policy_id],
            ["판본", pol.policy_ref?.policy_version],
            ["프로필", pol.test_only ? `${pol.profile} (시험 전용)` : pol.profile || "default", { problem: pol.test_only }],
            ["대상", s.target.target_id],
            ["SHA-256", pol.policy_ref ? `${pol.policy_ref.sha256.slice(0, 12)}…` : null, { mono: true, title: pol.policy_ref?.sha256 }],
            ["로딩", formatTime(pol.loaded_at)],
            ["로딩 검증", pol.load_valid ? "유효" : "무효 — 추론 요청은 503", { problem: !pol.load_valid }],
            ["승인 확인", APPROVAL_DETAIL[pol.approval_state] ?? pol.approval_state, { problem: !["not_configured", "verified"].includes(pol.approval_state) }],
            ["Gateway 활성", pol.gateway_active ? "활성" : "비활성 — 추론 요청은 503", { problem: !pol.gateway_active }],
            ...(!pol.load_valid ? ([["사유", `${pol.error_code ?? ""} ${pol.error_message ?? ""}`.trim(), { problem: true }]] as Row[]) : []),
            [
              "API 인증",
              s.auth.state === "not_configured"
                ? "미적용 (누구나 추론 요청 가능)"
                : s.auth.state === "ok"
                  ? `적용 · 주체 ${s.auth.principal_count ?? 0}명 · 보호 경로 ${s.auth.protected_routes.length}개`
                  : `설정 오류 — 추론 요청은 503 (${s.auth.error_code ?? "unknown"})`,
              { problem: s.auth.state !== "ok" },
            ],
          ];
          const engineRows: Row[] = [
            ["주소", eng.url, { mono: true }],
            ["연결", eng.healthy ? "정상" : "응답 없음", { problem: !eng.healthy }],
            ["점검", formatTime(eng.checked_at)],
            ["동시 요청", `${s.concurrency.in_flight ?? "–"} / ${s.concurrency.limit ?? "–"}`],
            ["클라이언트 연결", `${s.connections.active ?? "–"} / ${s.connections.limit ?? "–"} · 거절 ${s.connections.rejected ?? 0}`],
            ["요청 읽기 timeout", `헤더 ${s.connections.request_header_timeout_s ?? "–"}초 · 본문 ${s.connections.request_body_timeout_s ?? "–"}초`],
            [
              "수신 기록",
              eng.receipts_store.state === "error" ? `저장 오류: ${eng.receipts_store.last_error}` : eng.receipts_store.state,
              { problem: eng.receipts_store.state === "error" },
            ],
            ...(external
              ? ([
                  ["자격정보", eng.credential, { problem: eng.credential !== "configured" }],
                  ["metrics 자격정보", eng.metrics_credential ?? "미설정", { problem: ["missing", "invalid", "insecure_file"].includes(eng.metrics_credential ?? "") }],
                  ["추론 검증", eng.inference_verified ? "확인됨" : "미확인 (health는 연결 가능 여부만 뜻함)"],
                ] as Row[])
              : []),
            ...(eng.error ? ([["오류", eng.error, { problem: true }]] as Row[]) : []),
            ["의미", "연결 점검이며 추론·관측 동작 검증이 아님"],
          ];
          const storeRows: Row[] = [
            ["경로", store.data_dir, { mono: true }],
            ["상태", { ok: "정상", error: "오류", unknown: "미확인" }[store.state], { problem: store.state === "error" }],
            ["손상 줄", `Gateway ${store.corrupt_lines.gateway} · 엔진 ${store.corrupt_lines.engine}`, { problem: corruptTotal > 0 }],
            ["전체 요청", summary?.total],
            ...(store.last_error ? ([["마지막 오류", `${formatTime(store.last_error_at)} ${store.last_error}`, { problem: true }]] as Row[]) : []),
            ...(["gateway", "engine"] as const)
              .map((src) => [src, store.corrupt_details.filter((d) => d.source === src)] as const)
              .filter(([, items]) => items.length)
              .map(([src, items]): Row => [src === "gateway" ? "Gateway 손상" : "엔진 손상", items.map((d) => `#${d.line}: ${d.reason}`).join(" · "), { problem: true }]),
          ];
          const go = (extra: Record<string, string>) => navigate(`/runtime/requests${carry(params, extra)}`);
          return (
            <>
              <div className="row wrap" style={{ justifyContent: "space-between" }}>
                <ScopeChips status={s} observeEnv={observeEnv} />
                <span className={rt.sessionLine}>
                  관측 세션 {s.observation_session_id?.slice(0, 8) ?? "–"} · {s.environment ?? "환경 미기록"} · {s.run_id ?? "실행 미기록"}
                </span>
              </div>

              <div className={ui.cards}>
                <article className={ui.metric}>
                  <span className={ui.metricTitle}>요청</span>
                  <span className={ui.bigNumber}>
                    <span>{summary?.total ?? "–"}</span>
                    <span className={ui.bigCaption}>전체 요청 기록</span>
                  </span>
                  <div className={ui.barList} role="group" aria-label="결과별 요청 수">
                    {REQUEST_SERIES.map((x) => (
                      <button key={x.key} type="button" className={`${ui.barRow} ${x.key === "error" ? ui.isError : ""}`} onClick={() => go({ outcome: x.key })}>
                        <span className={ui.barLabel}>{x.label}</span>
                        <span className={ui.barTrack}>
                          <span className={ui.barFill} style={{ width: `${((counts?.[x.key] ?? 0) / max) * 100}%` }} />
                        </span>
                        <span className={ui.barCount}>{counts?.[x.key] ?? "–"}</span>
                      </button>
                    ))}
                  </div>
                </article>
                <article className={ui.metric}>
                  <span className={ui.metricTitle}>거부·오류 사유</span>
                  <span className={ui.bigNumber}>
                    <span>{topReasons.reduce((a, [, n]) => a + n, 0)}</span>
                    <span className={ui.bigCaption}>최근 {reqRows.length}건 중 거부·오류</span>
                  </span>
                  <table className={ui.miniTable}>
                    <thead>
                      <tr>
                        <th>사유</th>
                        <th className={ui.num}>건수</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topReasons.length === 0 ? (
                        <tr>
                          <td className="muted">거부·오류 없음</td>
                          <td />
                        </tr>
                      ) : (
                        topReasons.slice(0, 5).map(([reason, n]) => (
                          <tr key={reason} style={{ cursor: "pointer" }} onClick={() => go({ reason })}>
                            <td className="mono">{reason}</td>
                            <td className={ui.num}>{n}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </article>
                <article className={ui.metric}>
                  <span className={ui.metricTitle}>관측 사건</span>
                  <span className={ui.bigNumber}>
                    <span>{Object.values(s.observations.by_source).reduce((a, b) => a + b, 0)}</span>
                    <span className={ui.bigCaption}>누적 관측 사건</span>
                  </span>
                  <table className={ui.miniTable}>
                    <thead>
                      <tr>
                        <th>규칙·판정</th>
                        <th className={ui.num}>건수</th>
                      </tr>
                    </thead>
                    <tbody>
                      {ruleRows.length === 0 ? (
                        <tr>
                          <td className="muted">관측 사건 없음</td>
                          <td />
                        </tr>
                      ) : (
                        ruleRows.slice(0, 5).map(([rule, n]) => (
                          <tr key={rule}>
                            <td className="mono">{rule}</td>
                            <td className={ui.num}>{n}</td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </article>
              </div>

              <div className={p.chartRow}>
                <ChartCard
                  title="시간별 요청"
                  tabs={[
                    { id: "total", label: "전체" },
                    { id: "results", label: "결과별" },
                  ]}
                  active={timeMode}
                  onTab={setTimeMode}
                  total={reqRows.length}
                  note="최근 요청"
                >
                  {timeMode === "total" ? (
                    <TimeLine buckets={chart.buckets} size={chart.size} keys={REQUEST_SERIES.map((x) => x.key)} />
                  ) : (
                    <TimeBars buckets={chart.buckets} size={chart.size} series={[...REQUEST_SERIES]} />
                  )}
                </ChartCard>
                <ChartCard
                  title="구성 비율"
                  tabs={[
                    { id: "results", label: "요청 결과" },
                    { id: "rules", label: "관측 규칙" },
                  ]}
                  active={mixMode}
                  onTab={setMixMode}
                  total={mixMode === "results" ? (summary?.total ?? "–") : ruleRows.slice(0, 8).reduce((a, [, n]) => a + n, 0)}
                  note={mixMode === "results" ? "누적 요청 결과" : "누적 관측 사건(규칙별)"}
                >
                  {mixMode === "results" ? (
                    <Donut items={REQUEST_SERIES.map((x) => ({ key: x.key, label: x.label, value: counts?.[x.key] ?? 0, color: x.color }))} />
                  ) : (
                    <HBars rows={ruleRows.slice(0, 8)} />
                  )}
                </ChartCard>
              </div>

              {pol.test_only && (
                <WarningBox>
                  시험 전용 프로필({pol.profile}): 검사하지 않고 엔진에 전달하는 필드 {pol.passthrough_fields.length}개
                  {pol.passthrough_fields.length ? ` (${pol.passthrough_fields.join(", ")})` : ""} · 검사 후 전달 <Value v={pol.validate_count} />개 · 허용 n 최대 {pol.max_n ?? 1}. 승인된 운영 정책이
                  아닙니다.
                </WarningBox>
              )}
              {s.revalidation.open_count > 0 && (
                <WarningBox>
                  열린 재검증 요청 {s.revalidation.open_count}건 · 현재 근거 {s.revalidation.current_condition_count}건 — 자동 승인·해제되지 않습니다.
                </WarningBox>
              )}

              <h2 className={ui.blockTitle}>구성요소 상태</h2>
              <div className={rt.cardGrid3}>
                <article className="card card-pad stack" style={{ gap: 10 }}>
                  <h3 className="row" style={{ gap: 8 }}>
                    <Dot state={pol.gateway_active ? "ok" : "bad"} />
                    정책
                  </h3>
                  <Kv rows={policyRows} />
                </article>
                <article className="card card-pad stack" style={{ gap: 10 }}>
                  <h3 className="row" style={{ gap: 8 }}>
                    <Dot state={eng.healthy ? "ok" : "bad"} />
                    {external ? "외부 backend" : "모의 엔진"}
                  </h3>
                  <Kv rows={engineRows} />
                </article>
                <article className="card card-pad stack" style={{ gap: 10 }}>
                  <h3 className="row" style={{ gap: 8 }}>
                    <Dot state={store.state === "ok" && corruptTotal === 0 ? "ok" : store.state === "unknown" ? "none" : "bad"} />
                    기록 저장
                  </h3>
                  <Kv rows={storeRows} />
                </article>
              </div>

              <div className={rt.chipRow} aria-label="관측 연결 상태">
                <ObservationChip label="네트워크" state={observationState(origins.network_flow, col.observation_feed_hubble)} />
                <ObservationChip label="프로세스" state={observationState(origins.process_exec, col.observation_feed_tetragon)} />
                <ObservationChip label="파일" state={observationState(origins.file_access, col.observation_feed_tetragon)} />
                <ObservationChip label="GPU" state={["미연결", "off"]} />
              </div>
            </>
          );
        }}
      </Query>
    </div>
  );
}
