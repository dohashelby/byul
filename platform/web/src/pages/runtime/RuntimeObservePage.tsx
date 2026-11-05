// 통신·행위 관측. 삭제된 runtime 대시보드 renderCollectors()·collectorSummary()·renderObservations()를 옮겼다.
import type { ReactNode } from "react";
import { useParams as useRouteParams } from "react-router-dom";
import { useRuntimeEvents, useRuntimeStatus } from "../../api/hooks";
import type { Collector, RuntimeEvent } from "../../api/types";
import { Badge } from "../../components/Badges";
import { Pagination, usePagination } from "../../components/Pagination";
import { PageHead, Query, StateView } from "../../components/Misc";
import { Value } from "../../components/Value";
import { CACHE_GENERATION, EXEC_VERDICT, METRIC_STATE, PARENT_ATTRIBUTION, PARENT_CHECK, PHASE, SUSPECT_CONTEXT, label } from "../../lib/labels";
import { isTetragonBlockAttempt, isTetragonSigkill } from "../../lib/tetragon";
import { useRuntimeProblems } from "./problems";
import { ObservationOriginBadge, ScopeBadges, StateBadge, Subtabs, TableCard, formatTime, rt } from "./parts";

const FLAGGED = ["outside_allowlist", "suspect_binary", "exec_from_cache", "phase_unknown", "phase_signal_unverified"];
const gib = (bytes: number | null) => `${((bytes ?? 0) / 1024 ** 3).toFixed(2)}GiB`;

function CollectorSummary({ c }: { c: Collector }) {
  const d = c.detail;
  if (!d) return null;
  const lines: [ReactNode, boolean][] = [];
  if (d.kind === "asset_integrity") {
    lines.push([
      <>
        일치 {d.match}/{d.total ?? "–"} · 변경 {d.changed} · 누락 {d.missing} · 읽기 불가 {d.unreadable} · 링크 {d.symlink} · 목록 밖 파일 <Value v={d.extra_files} /> · 목록 밖 링크 <Value v={d.extra_symlinks} /> · 순회 오류 <Value v={d.walk_errors} />
      </>,
      d.intact === false,
    ]);
  }
  if (d.kind === "observation_feed") {
    lines.push([
      `최근 처리 ${d.processed} · 저장 ${d.accepted} · 미지원 건너뜀 누적 ${d.skipped_unsupported_total} · 중복 ${d.duplicates} · 거부 ${d.rejected} · 손상 누적 ${d.corrupt_lines_total} · 실행 전 이벤트 누적 ${d.out_of_window_total}`,
      d.rejected > 0 || d.corrupt_lines_total > 0 || d.out_of_window_total > 0,
    ]);
    if (c.source === "observation_feed_hubble") {
      const bySource = d.lost_events_by_source
        ? Object.entries(d.lost_events_by_source).map(([source, count]) => `${source} ${count}`).join(" · ")
        : null;
      lines.push([
        <>
          네트워크 이벤트 (Hubble) 손실 누적 <Value v={d.lost_events_total} /> · 원인별 <Value v={bySource} /> · 최근 손실 <Value v={d.last_lost_at ? formatTime(d.last_lost_at) : null} />
          {d.lost_events_total === 0 ? " (처리한 줄에서 손실 기록 미관측; 무손실 확인 아님)" : ""}
        </>,
        d.loss_detected === true,
      ]);
    }
    if (d.rejected_details.length) lines.push([d.rejected_details.map((x) => `#${x.line}: ${x.reason}`).join(" · "), true]);
    lines.push([
      d.writer.configured
        ? `수집기 ${d.writer.state} · 생존 신호 ${formatTime(d.writer.updated_at)}${d.idle ? " · 신규 이벤트 없음" : ""}`
        : "수집기 생존 신호 미설정 · 수집기 상태 확인 불가",
      !d.writer.configured,
    ]);
    if (d.writer.last_gap)
      lines.push([
        `재연결 ${d.writer.reconnect_count ?? 0}회 · 최근 연결 단절 구간 ${formatTime(d.writer.last_gap.opened_at)} ~ ${formatTime(d.writer.last_gap.closed_at)} · 구간 이벤트 복구 요청(완전성 미확인)`,
        true,
      ]);
  }
  if (d.kind === "record_retention") {
    lines.push([
      `${d.mode === "enforce" ? "삭제 적용" : "보고만(삭제 안 함)"} · ${d.max_age_days ?? "–"}일/${gib(d.max_bytes)} · 현재 ${gib(d.total_bytes)} · 한도 초과 대상 ${d.removable_lines}줄`,
      d.over_size_limit || d.removable_lines > 0,
    ]);
    if (d.holds_state === "error") lines.push([`보존 목록 오류: ${d.holds_error} — 삭제하지 않음`, true]);
    if (d.errors.length) lines.push([`정리 오류: ${d.errors.join(" · ")}`, true]);
    if (d.last_deletion)
      lines.push([
        `최근 정리 ${formatTime(d.last_deletion.ran_at)}: ${d.last_deletion.files
          .map((f) => `${f.name} ${f.removed_lines}줄 (${formatTime(f.from)}~${formatTime(f.to)})${f.durability === "uncertain" ? " · 교체됨, 디렉터리 fsync 실패로 내구성 불확실" : ""}`)
          .join(" · ")} — 이 구간 기록은 조회되지 않음`,
        true,
      ]);
  }
  if (d.kind === "metrics") {
    for (const f of d.fields) {
      const parts = [`${f.name}: ${METRIC_STATE[f.state] ?? f.state}`, `선언 형식 ${f.declared_type ?? "없음"}`];
      if (f.counter_reset_now) parts.push("counter 감소 감지(엔진 재시작 가능성)");
      else if (f.last_counter_reset_at) parts.push(`최근 counter 감소 ${formatTime(f.last_counter_reset_at)}`);
      lines.push([parts.join(" · "), f.state !== "ok" || f.counter_reset_now]);
    }
  }
  if (d.kind === "cilium_policy") lines.push([`정책 객체 ${d.object_count}개 · 객체 존재는 실제 차단 증거가 아님`, false]);
  return (
    <>
      {lines.map(([text, problem], i) => (
        <div key={i} className={problem ? rt.problem : rt.sub}>
          {text}
        </div>
      ))}
    </>
  );
}

function pod(ns: string | null, name: string | null) {
  if (!name) return "–";
  return ns ? `${ns}/${name}` : name;
}

function tetragonKind(e: RuntimeEvent) {
  const o = e.observation!;
  let kind = o.kind === "process_exec" ? "프로세스" : `파일 ${o.access ?? "other"}`;
  if (o.cache_generation) kind += ` · ${CACHE_GENERATION[o.cache_generation] ?? o.cache_generation}`;
  const attr = [];
  if (o.pod_attribution === "unavailable") attr.push("pod 미확인");
  if (o.process_attribution === "unavailable" || (o.kind === "file_access" && !o.binary)) attr.push("프로세스 미확인");
  return { kind: attr.length ? `${kind} · ${attr.join(" · ")}` : kind, attr: attr.join(" · ") };
}

// 계약 observation에는 부모 필드가 없지만 native.record에는 runtime 정규화 원본이 보존된다.
function parentInfo(e: RuntimeEvent) {
  const record: Record<string, unknown> = e.native.record;
  const field = (key: string) => typeof record[key] === "string" ? record[key] as string : null;
  const check = field("parent_check");
  const attribution = field("parent_attribution");
  const context = field("suspect_context");
  return [
    check ? label(PARENT_CHECK, check) : null,
    attribution && (check !== "unavailable" || attribution !== "unavailable") ? label(PARENT_ATTRIBUTION, attribution) : null,
    context ? label(SUSPECT_CONTEXT, context) : null,
  ].filter(Boolean).join(" · ") || "–";
}

export function RuntimeObservePage() {
  const { sub = "collectors" } = useRouteParams();
  const status = useRuntimeStatus();
  const events = useRuntimeEvents();
  const problems = useRuntimeProblems();
  const observations = (events.data?.items ?? []).filter((e) => e.observation);
  // Hubble은 DROPPED를 먼저 보여준다.
  const hubbleAll = observations.filter((e) => e.source === "hubble");
  const hubble = [...hubbleAll.filter((e) => e.observation!.verdict?.toLowerCase() === "dropped"), ...hubbleAll.filter((e) => e.observation!.verdict?.toLowerCase() !== "dropped")];
  const tetragon = observations.filter((e) => e.source === "tetragon");
  const item = status.data?.item;
  const pagination = usePagination(sub === "hubble" ? hubble.length : sub === "tetragon" ? tetragon.length : item?.collectors.length ?? 0);
  return (
    <div className="page">
      <PageHead title={sub === "hubble" ? "네트워크 이벤트 (Hubble)" : sub === "tetragon" ? "프로세스·파일 이벤트 (Tetragon)" : "수집 상태"}>
        {item && (
          <span className="row small muted" style={{ gap: 12 }}>
            <span>네트워크 이벤트 {item.observations.by_source.hubble ?? 0}건 · 프로세스·파일 이벤트 {item.observations.by_source.tetragon ?? 0}건</span>
            <span>손상 줄 {item.observations.corrupt_lines}</span>
          </span>
        )}
      </PageHead>
      <p className="notice">관측 자료의 환경(예: kind lab)은 행마다 표시됩니다. 요청 화면의 엔진과 다른 환경일 수 있습니다.</p>
      <Subtabs
        tabs={[
          { to: "/runtime/observe/collectors", label: "수집 상태", badge: problems?.observe, title: problems?.collectorProblems.join(" · "), active: sub === "collectors" },
          { to: "/runtime/observe/hubble", label: "네트워크 이벤트 (Hubble)", count: hubble.length, active: sub === "hubble" },
          { to: "/runtime/observe/tetragon", label: "프로세스·파일 이벤트 (Tetragon)", count: tetragon.length, badge: problems?.tetragonFlagged, title: problems?.tetragonFlagged ? `허용 목록 외 실행·의심·확인 필요 실행 ${problems.tetragonFlagged}건` : undefined, active: sub === "tetragon" },
          { to: "/runtime/observe/phases", label: "엔진 실행 단계", active: sub === "phases" },
        ]}
      />
      {sub === "collectors" && (
        <Query q={status}>
          {(res) =>
            !res.item ? (
              <StateView kind="invalid" />
            ) : (
              <TableCard title="수집 상태">
                <table className={rt.statusTable}>
                  <thead>
                    <tr><th>관측 연결</th><th>상태</th><th>최근 성공</th><th>오류</th><th>출처</th></tr>
                  </thead>
                  <tbody>
                    {res.item.collectors.slice(pagination.start, pagination.end).map((c) => (
                      <tr key={c.source}>
                        <td>
                          <span className="mono">{c.source}</span>
                          <CollectorSummary c={c} />
                        </td>
                        <td><StateBadge value={c.state} /></td>
                        <td>{formatTime(c.last_success_at)}</td>
                        <td>{c.last_error ?? "–"}</td>
                        <td>
                          <Badge tone={c.origin_detail === "mock" ? "muted" : "neutral"}>{c.origin_detail === "mock" ? "모의" : c.origin_detail ?? "–"}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <Pagination state={pagination} />
              </TableCard>
            )
          }
        </Query>
      )}
      {sub === "hubble" && (
        <TableCard title="네트워크 이벤트 (Hubble)">
          <table className={rt.statusTable}>
            <thead>
              <tr><th>시각</th><th>환경·실행</th><th>출발</th><th>도착</th><th>판정</th><th>출처</th></tr>
            </thead>
            <tbody>
              {hubble.slice(pagination.start, pagination.end).map((e) => {
                const o = e.observation!;
                return (
                  <tr key={e.event_id} className={o.verdict?.toLowerCase() === "dropped" ? rt.rowDropped : undefined}>
                    <td>{formatTime(e.occurred_at)}</td>
                    <td><ScopeBadges environment={e.scope.environment} runId={e.scope.run_id} /></td>
                    <td>{pod(o.src_namespace, o.src_pod)}</td>
                    <td className="mono">{`${pod(o.dst_namespace, o.dst_pod)} · ${o.dst_ip ?? "–"}:${o.dst_port ?? "–"}/${o.protocol ?? "–"}`}</td>
                    <td title={o.reason ?? ""}>{o.verdict ?? "–"}</td>
                    <td><ObservationOriginBadge origin={e.origin_detail} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pagination state={pagination} />
          {hubble.length === 0 && <p className={rt.empty}>네트워크 이벤트 미수집. 수집 상태와 기간을 확인하세요.</p>}
          <p className="notice" style={{ padding: "8px 16px 12px" }}>Hubble DROPPED는 관측이며, 출발·목적지·정책 귀속과 수신 대조 전에는 거부 확인이 아닙니다.</p>
        </TableCard>
      )}
      {sub === "tetragon" && (
        <TableCard title="프로세스·파일 이벤트 (Tetragon)">
          <table className={rt.statusTable}>
            <thead>
              <tr><th>시각</th><th>환경·실행</th><th>종류</th><th>실행 파일·경로</th><th>판정</th><th>단계</th><th>부모 확인</th><th>규칙·행위</th><th>출처</th></tr>
            </thead>
            <tbody>
              {tetragon.slice(pagination.start, pagination.end).map((e) => {
                const o = e.observation!;
                const flagged = o.kind === "process_exec" && (o.exec_verdict ? FLAGGED.includes(o.exec_verdict) : o.allowed_binary === false);
                const { kind, attr } = tetragonKind(e);
                const blocked = isTetragonBlockAttempt(e);
                const mode = o.action_mode === "enforce" ? "차단 시도 기록(enforce)" : o.action_mode === "monitor" ? "감시만(monitor)" : "알 수 없음";
                return (
                  <tr key={e.event_id} className={flagged ? rt.rowFlagged : undefined}>
                    <td>{formatTime(e.occurred_at)}</td>
                    <td><ScopeBadges environment={e.scope.environment} runId={e.scope.run_id} /></td>
                    <td>{kind}</td>
                    <td className="mono" title={attr || pod(o.pod_namespace, o.pod_name)}>{o.path ?? o.binary ?? "–"}</td>
                    <td>{blocked ? <><Badge tone="caution" title="원시 차단 시도 기록; 실제 결과는 Lab 시험에서 확인">차단 시도</Badge> {isTetragonSigkill(e) ? "Sigkill · 프로세스 종료" : "차단 (Override)"}</> : o.exec_verdict ? `${label(EXEC_VERDICT, o.exec_verdict)}${o.exec_verdict === "exception" && o.exception_id ? ` · ${o.exception_id}` : ""}` : "–"}</td>
                    <td>{o.phases.length ? o.phases.map((phase) => label(PHASE, phase)).join(" + ") : "–"}</td>
                    <td>{parentInfo(e)}</td>
                    <td className="mono" title={o.action_raw ?? "원본 action 미기록"}>{`${o.rule_id ?? "–"} · ${mode}`}</td>
                    <td><ObservationOriginBadge origin={e.origin_detail} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <Pagination state={pagination} />
          {tetragon.length === 0 && <p className={rt.empty}>프로세스·파일 이벤트 미수집. 수집 상태와 기간을 확인하세요.</p>}
          <p className="notice" style={{ padding: "8px 16px 12px" }}>
            프로세스·파일 이벤트 (Tetragon)의 Override는 차단 시도 기록입니다. 실제 EPERM 결과는 「검증 시험 (Lab)」의 L-05 결과에서 확인합니다.
          </p>
        </TableCard>
      )}
    </div>
  );
}
