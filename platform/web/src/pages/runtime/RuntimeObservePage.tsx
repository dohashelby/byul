// 통신·행위 관측. 삭제된 runtime 대시보드 renderCollectors()·collectorSummary()·renderObservations()를 옮겼다.
import type { ReactNode } from "react";
import { useParams as useRouteParams } from "react-router-dom";
import { useRuntimeEvents, useRuntimeStatus } from "../../api/hooks";
import type { Collector, RuntimeEvent } from "../../api/types";
import { Badge } from "../../components/Badges";
import { PageHead, Query, StateView } from "../../components/Misc";
import { Value } from "../../components/Value";
import { CACHE_GENERATION, EXEC_VERDICT, METRIC_STATE } from "../../lib/labels";
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
      `최근 처리 ${d.processed} · 저장 ${d.accepted} · 중복 ${d.duplicates} · 거부 ${d.rejected} · 손상 누적 ${d.corrupt_lines_total} · 실행 전 이벤트 누적 ${d.out_of_window_total}`,
      d.rejected > 0 || d.corrupt_lines_total > 0 || d.out_of_window_total > 0,
    ]);
    if (d.rejected_details.length) lines.push([d.rejected_details.map((x) => `#${x.line}: ${x.reason}`).join(" · "), true]);
    lines.push([
      d.writer.configured
        ? `writer ${d.writer.state} · heartbeat ${formatTime(d.writer.updated_at)}${d.idle ? " · 새 이벤트 없음" : ""}`
        : "writer heartbeat 미설정 · 이벤트가 없으면 생존 여부를 확인할 수 없음",
      !d.writer.configured,
    ]);
    if (d.writer.last_gap)
      lines.push([
        `재연결 ${d.writer.reconnect_count ?? 0}회 · 최근 끊긴 구간 ${formatTime(d.writer.last_gap.opened_at)} ~ ${formatTime(d.writer.last_gap.closed_at)} · 그 사이 이벤트 복구 요청함(완전성 미확인)`,
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
  if (o.exec_verdict) {
    const verdict = o.exec_verdict === "exception" ? `승인 예외 ${o.exception_id ?? ""}`.trim() : (EXEC_VERDICT[o.exec_verdict] ?? o.exec_verdict);
    kind += ` · ${o.phases.join("+")} · ${verdict}`;
  }
  if (o.cache_generation) kind += ` · ${CACHE_GENERATION[o.cache_generation] ?? o.cache_generation}`;
  const attr = [];
  if (o.pod_attribution === "unavailable") attr.push("pod 미확인");
  if (o.process_attribution === "unavailable" || (o.kind === "file_access" && !o.binary)) attr.push("프로세스 미확인");
  return { kind: attr.length ? `${kind} · ${attr.join(" · ")}` : kind, attr: attr.join(" · ") };
}

export function RuntimeObservePage() {
  const { sub = "collectors" } = useRouteParams();
  const status = useRuntimeStatus();
  const events = useRuntimeEvents();
  const problems = useRuntimeProblems();
  const observations = (events.data?.items ?? []).filter((e) => e.observation);
  // runtime처럼 Hubble은 DROPPED를 먼저(최대 25) 보여준다.
  const hubbleAll = observations.filter((e) => e.source === "hubble");
  const hubble = [...hubbleAll.filter((e) => e.observation!.verdict?.toLowerCase() === "dropped").slice(0, 25), ...hubbleAll.filter((e) => e.observation!.verdict?.toLowerCase() !== "dropped")];
  const tetragon = observations.filter((e) => e.source === "tetragon");
  const item = status.data?.item;
  return (
    <div className="page">
      <PageHead title="통신·행위 관측">
        {item && (
          <span className="row small muted" style={{ gap: 12 }}>
            <span>Hubble {item.observations.by_source.hubble ?? 0}건 · Tetragon {item.observations.by_source.tetragon ?? 0}건</span>
            <span>손상 줄 {item.observations.corrupt_lines}</span>
          </span>
        )}
      </PageHead>
      <p className="notice">관측 자료의 환경(예: kind lab)은 행마다 표시됩니다. 요청 화면의 엔진과 다른 환경일 수 있습니다.</p>
      <Subtabs
        tabs={[
          { to: "/runtime/observe/collectors", label: "수집원", badge: problems?.observe, title: problems?.collectorProblems.join(" · "), active: sub === "collectors" },
          { to: "/runtime/observe/hubble", label: "Hubble 통신", count: hubble.length, active: sub === "hubble" },
          { to: "/runtime/observe/tetragon", label: "Tetragon 프로세스·파일", count: tetragon.length, badge: problems?.tetragonFlagged, title: problems?.tetragonFlagged ? `목록 밖·의심·확인 필요 실행 ${problems.tetragonFlagged}건` : undefined, active: sub === "tetragon" },
        ]}
      />
      {sub === "collectors" && (
        <Query q={status}>
          {(res) =>
            !res.item ? (
              <StateView kind="invalid" />
            ) : (
              <TableCard title="수집원">
                <table className={rt.statusTable}>
                  <thead>
                    <tr><th>수집원</th><th>상태</th><th>최근 성공</th><th>오류</th><th>출처</th></tr>
                  </thead>
                  <tbody>
                    {res.item.collectors.map((c) => (
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
              </TableCard>
            )
          }
        </Query>
      )}
      {sub === "hubble" && (
        <TableCard title="Hubble 통신">
          <table className={rt.statusTable}>
            <thead>
              <tr><th>시각</th><th>환경·실행</th><th>출발</th><th>도착</th><th>판정</th><th>출처</th></tr>
            </thead>
            <tbody>
              {hubble.map((e) => {
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
          {hubble.length === 0 && <p className={rt.empty}>아직 Hubble 관측이 없습니다.</p>}
          <p className="notice" style={{ padding: "8px 16px 12px" }}>Hubble DROPPED는 관측이며, 출발·목적지·정책 귀속과 수신 대조 전에는 거부 확인이 아닙니다.</p>
        </TableCard>
      )}
      {sub === "tetragon" && (
        <TableCard title="Tetragon 프로세스·파일">
          <table className={rt.statusTable}>
            <thead>
              <tr><th>시각</th><th>환경·실행</th><th>종류</th><th>실행 파일·경로</th><th>규칙·행위</th><th>출처</th></tr>
            </thead>
            <tbody>
              {tetragon.map((e) => {
                const o = e.observation!;
                const flagged = o.kind === "process_exec" && (o.exec_verdict ? FLAGGED.includes(o.exec_verdict) : o.allowed_binary === false);
                const { kind, attr } = tetragonKind(e);
                const mode = o.action_mode === "enforce" ? "차단 시도 기록(enforce)" : o.action_mode === "monitor" ? "감시만(monitor)" : "알 수 없음";
                return (
                  <tr key={e.event_id} className={flagged ? rt.rowFlagged : undefined}>
                    <td>{formatTime(e.occurred_at)}</td>
                    <td><ScopeBadges environment={e.scope.environment} runId={e.scope.run_id} /></td>
                    <td>{kind}</td>
                    <td className="mono" title={attr || pod(o.pod_namespace, o.pod_name)}>{o.path ?? o.binary ?? "–"}</td>
                    <td className="mono" title={o.action_raw ?? "원본 action 미기록"}>{`${o.rule_id ?? "–"} · ${mode}`}</td>
                    <td><ObservationOriginBadge origin={e.origin_detail} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {tetragon.length === 0 && <p className={rt.empty}>아직 Tetragon 관측이 없습니다.</p>}
          <p className="notice" style={{ padding: "8px 16px 12px" }}>
            Tetragon Override는 차단 시도가 기록됐다는 뜻입니다. 실제 EPERM 결과는 「통제 적용」의 lab L-05 결과로 따로 확인합니다.
          </p>
        </TableCard>
      )}
    </div>
  );
}
