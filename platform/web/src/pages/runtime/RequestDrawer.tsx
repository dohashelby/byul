// 요청 상세 드로어. 삭제된 runtime 대시보드 renderDetail()의 구성을 그대로 옮기고, platform의 증거 목록 탭을 더했다.
import type { ReactNode } from "react";
import { useRequestDetail } from "../../api/hooks";
import type { RuntimeRequestDetail } from "../../api/types";
import { DetailPanel, KeyValue } from "../../components/DetailPanel";
import { JsonView } from "../../components/JsonView";
import { Query, StateView } from "../../components/Misc";
import { OriginBadge, EnumBadge } from "../../components/Badges";
import { Short, Time } from "../../components/Value";
import { AUTH_RESULT, CONTEXT_RESULT, EXTRA_FIELD_MODE, REQUEST_RESULT, TIMELINE_PHASE, label } from "../../lib/labels";
import { ScopeBadges, formatTime, rt } from "./parts";

type Row = [string, ReactNode, { mono?: boolean; problem?: boolean }?];

function Kv({ rows }: { rows: Row[] }) {
  return (
    <KeyValue
      items={rows.map(([k, v, o]) => [
        k,
        <span className={o?.mono ? "mono" : undefined} style={o?.problem ? { color: "var(--error)" } : undefined}>
          {v === null || v === undefined || v === "" ? "–" : v}
        </span>,
      ])}
    />
  );
}

function authText(d: RuntimeRequestDetail): string | null {
  if (!d.auth.state) return null;
  const l = AUTH_RESULT[d.auth.state] ?? d.auth.state;
  return d.auth.principal_id ? `${l} · ${d.auth.principal_id} (${d.auth.roles.join(", ") || "역할 없음"})` : l;
}

function Overview({ d }: { d: RuntimeRequestDetail }) {
  const ctx = d.request.context;
  return (
    <div className="stack" style={{ gap: 0 }}>
      <div className="row wrap" style={{ gap: 8 }}>
        <EnumBadge table={REQUEST_RESULT} value={d.outcome} />
        <OriginBadge origin={d.data_origin} detail={d.origin_detail} />
      </div>
      <p className="mono" style={{ margin: "8px 0" }}>{d.request_id}</p>
      <ScopeBadges environment={d.environment} runId={d.run_id} />
      <div className={`${rt.evidenceBox} ${d.evidence_state ? rt[`ev_${d.evidence_state}`] : ""}`} style={{ marginTop: 12 }}>
        {d.evidence_summary ?? "–"}
      </div>
      <h4 className={rt.h4}>판정</h4>
      <Kv
        rows={[
          ["결과", label(Object.fromEntries(Object.entries(REQUEST_RESULT).map(([k, v]) => [k, v.label])), d.outcome)],
          ["HTTP", d.decision.http_status],
          ["HTTP 시작 상태", d.http_start_status],
          ["최종 결과", d.final_outcome, { mono: true }],
          ["스트림", d.stream === true ? "예" : d.stream === false ? "아니요" : null],
          ["사유 코드", d.decision.reason_code, { mono: true }],
          ["설명", d.decision.message],
          ["적용 규칙", d.decision.rule_id, { mono: true }],
          ["엔진 HTTP", d.decision.engine_http_status],
          ["모델", d.request.model],
          ["max_tokens", d.request.max_tokens],
          ["n", d.request.n],
          ["캐시 분리", d.request.cache_isolation.applied === true ? "적용" : d.request.cache_isolation.applied === false ? "미적용" : "모름"],
          ["추가 필드", d.request.extra_fields.length ? d.request.extra_fields.map((x) => `${x.name}(${label(EXTRA_FIELD_MODE, x.mode)})`).join(", ") : null],
          ["요청 주체", authText(d)],
          ["context 검사", ctx ? `${label(CONTEXT_RESULT, ctx.result)} · 입력 ${ctx.input_tokens ?? "–"}토큰 / 한도 ${ctx.limit ?? "–"}` : "설정 안 됨"],
        ]}
      />
      <h4 className={rt.h4}>적용 정책</h4>
      <Kv
        rows={[
          ["정책", `${d.policy.policy_id ?? "–"} · 판본 ${d.policy.policy_version ?? "–"}`],
          ["프로필", d.policy.profile === "zeroday-lab" ? "zeroday-lab (시험 전용)" : d.policy.profile, { problem: d.policy.profile === "zeroday-lab" }],
          ["SHA-256", d.policy.sha256, { mono: true }],
        ]}
      />
      <h4 className={rt.h4}>전달</h4>
      <Kv
        rows={[
          ["전달 시도", d.delivery.attempted ? "예" : d.delivery.attempted === false ? "아니요 (엔진 미호출)" : null],
          ["기록 시점 수신 확인", d.delivery.backend_receipt],
          ["응답", d.delivery.response],
          ["오류 코드", d.delivery.error_code, { mono: true }],
          ["backend 취소", d.backend_cancel === "mock_engine_recorded" ? "모의 엔진 기록 (실제 엔진 취소 확인 아님)" : d.backend_cancel],
        ]}
      />
      <h4 className={rt.h4}>Gateway 기록</h4>
      <ol className={rt.timeline}>
        {d.timeline.map((t, i) => (
          <li key={i}>
            <div className={rt.phase}>{label(TIMELINE_PHASE, t.phase)}</div>
            <div className="muted small">{formatTime(t.recorded_at)}</div>
          </li>
        ))}
      </ol>
      <h4 className={rt.h4}>엔진 수신 증거</h4>
      <Kv
        rows={[
          ["수신 기록 파일", d.engine_receipts.count ? `${d.engine_receipts.count}건 (${formatTime(d.engine_receipts.first_received_at)})` : "없음"],
          ["실시간 조회", `${d.engine_live.status ?? "–"} · ${formatTime(d.engine_live.queried_at)}`],
          ["같은 관측 세션", d.same_observation_session ? "예" : d.same_observation_session === false ? "아니요" : null],
          ["손상 줄", `Gateway ${d.corrupt_lines.gateway ?? "–"} · 엔진 ${d.corrupt_lines.engine ?? "–"}`],
        ]}
      />
      <p style={{ marginTop: 16 }}>
        <a className={rt.download} href={`/api/runtime/requests/${encodeURIComponent(d.request_id)}/evidence`} download={`evidence-${d.request_id}.json`}>
          증거 JSON 내려받기
        </a>
      </p>
    </div>
  );
}

export function RequestDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useRequestDetail(id);
  return (
    <Query q={q}>
      {(res) => {
        const evidence = res.evidence ?? [];
        return (
        <DetailPanel
          title="요청 상세"
          subtitle={<span className="mono">{id}</span>}
          onClose={onClose}
          tabs={[
            {
              id: "detail",
              label: "상세",
              content: res.detail ? <Overview d={res.detail} /> : <StateView kind="invalid" detail={res.detail_invalid?.errors.join(" / ")} />,
            },
            { id: "raw", label: "원본 기록", content: <JsonView value={res.detail?.native ?? res.detail_invalid?.raw ?? res.event?.native ?? null} /> },
            {
              id: "evidence",
              label: `증거 목록 ${evidence.length}`,
              content:
                evidence.length === 0 ? (
                  <StateView kind="empty" detail="이 요청을 가리키는 증거 목록 항목이 없습니다." />
                ) : (
                  <div className="stack">
                    {evidence.map((ev) => (
                      <KeyValue
                        key={ev.evidence_id}
                        items={[
                          ["증거 ID", <span className="mono">{ev.evidence_id}</span>],
                          ["경로", <span className="mono">{ev.path}</span>],
                          ["SHA-256", <Short value={ev.sha256} n={16} />],
                          ["수집", <Time iso={ev.collected_at} />],
                          ["자료 등급", <OriginBadge origin={ev.data_origin} />],
                        ]}
                      />
                    ))}
                  </div>
                ),
            },
          ]}
        />
        );
      }}
    </Query>
  );
}
