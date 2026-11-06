import { useTetragonStatus } from "../../api/hooks";
import type { TetragonVerification } from "../../api/types";
import { Badge, OriginBadge } from "../../components/Badges";
import { PageHead, Query } from "../../components/Misc";
import { Subtabs, TableCard, formatTime, rt } from "./parts";

const METHODS: Record<string, string> = {
  monitor: "감시 규칙",
  override_only: "오류 반환 규칙 (Override)",
  override_lsm: "파일 접근 거부 + 프로세스 종료",
  override_syscall: "파일 열기 거부 + 프로세스 종료",
  sigkill_fd_install: "프로세스 강제 종료",
};
const MODES: Record<string, string> = { monitor: "감시", enforce: "차단", monitor_only: "감시 전용", unknown: "미확인" };
const RECOVERY_REASONS: Record<string, string> = {
  agent_epoch_changed: "에이전트 교체·재시작 감지", agent_or_policy_query_unavailable: "에이전트·정책 조회 대기",
  agent_epoch_first_observed: "초기 에이전트 확인 후 새 관측 대기",
  expected_policy_state_mismatch: "기대 정책·모드 불일치", event_writer_unavailable: "이벤트 수집기 중단·지연",
  process_identity_source_unavailable: "프로세스 대상 확인 자료 미확인", waiting_for_attributed_event: "복구 이후 대상이 확인된 새 이벤트 대기",
  normalized_observations_unavailable: "정규화 이벤트 조회 불가", expected_policies_unavailable: "기대 정책 파일 미설정",
  recovery_check_not_configured: "운영 복구 확인 미설정", recovery_evidence_unavailable: "복구 근거 확인 불가",
  recovery_observer_stale: "복구 확인 수집기의 갱신 공백 감지",
};
const effect = (value: string) => value === "confirmed" ? "확인" : value === "failed" ? "실패" : "미확인";

function downloadEvidence(report: TetragonVerification) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `${report.id}.verification.json`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function TetragonPolicyPage() {
  const query = useTetragonStatus();
  return <div className="page">
    <PageHead title="파일·프로세스 정책 (Tetragon)" />
    <Subtabs tabs={[
      { to: "/runtime/enforce/cilium", label: "네트워크 정책 (Cilium)", active: false },
      { to: "/runtime/enforce/tetragon", label: "파일·프로세스 정책 (Tetragon)", active: true },
      { to: "/runtime/enforce/lab", label: "검증 시험 (Lab)", active: false },
    ]} />
    <Query q={query}>{(data) => {
      const state = data.item;
      const current = state.fresh && state.state === "ok";
      const policies = state.policies ?? [];
      const capabilities = state.capabilities ?? [];
      const transitions = state.transitions ?? [];
      const verifications = state.verifications ?? [];
      const monitoring = state.monitoring;
      return <>
        <p className="notice">정책 모드는 서버에서 조회한 값입니다. 실제 차단 효과는 아래 시험 결과에서 파일 읽기 거부와 프로세스 종료를 각각 확인합니다.</p>
        <div className="row wrap">
          <Badge tone={current ? "accent" : "caution"}>{current ? "정책 상태 수집 정상" : state.state === "not_configured" ? "정책 상태 미연결" : "현재 정책 상태 미확인"}</Badge>
          <span>환경 {state.context ?? "미확인"} · 마지막 수집 {formatTime(state.generated_at)}</span>
        </div>
        {state.error && <p role="alert" className="notice">{state.error}</p>}
        <TableCard title="운영 감시 중단·복구 확인">
          <div className={rt.pad}>
            <Badge tone={current && monitoring?.state === "healthy" ? "accent" : "caution"}>
              {!current ? "복구 상태 수집 미확인" : monitoring?.state === "healthy" ? "복구 조건 확인됨" : monitoring?.state === "recovering" ? "중단·복구 확인 중" : "운영 복구 확인 미설정·오류"}
            </Badge>
            <p>마지막 확인 {formatTime(monitoring?.verified_at)} · 기대 정책 {monitoring?.policies_verified ? "확인" : "미확인"} · 수집기 {monitoring?.writer_verified ? "확인" : "미확인"} · 대상 식별 {monitoring?.identities_verified ? "확인" : "미확인"} · 복구 후 새 이벤트 {monitoring?.events_verified ? "확인" : "미확인"}</p>
            {(monitoring?.reasons ?? []).map((reason) => <div key={reason}>{RECOVERY_REASONS[reason] ?? "복구 근거 확인 중"}</div>)}
            {monitoring?.open_gap && <p role="alert">중단·미확인 구간: 이전 정상 확인 {formatTime(monitoring.open_gap.last_verified_at)} · 감지 {formatTime(monitoring.open_gap.opened_at)}</p>}
            <p className="small muted">정책·수집기·대상 확인과 새 실제 이벤트를 함께 확인합니다. 복구 확인은 중단 구간의 이벤트가 모두 복원됐다는 뜻이 아닙니다.</p>
            {(monitoring?.history ?? []).slice().reverse().map((gap, i) => <div className="small" key={`${gap.opened_at}/${i}`}>{formatTime(gap.opened_at)} ~ {formatTime(gap.closed_at)} · 복구 확인 · 구간 이벤트 완전성 미확인</div>)}
          </div>
        </TableCard>
        {(state.operation_checks ?? []).length > 0 && <TableCard title="운영 대상 확인·재시작 복구 실측">
          <div className={rt.pad}>{(state.operation_checks ?? []).map((check) => <div key={check.id} className="notice">
            {formatTime(check.finished_at)} · {check.namespace} · {check.status === "passed" ? "시험 통과" : "시험 미통과"}
            <div>주 프로세스 {check.facts?.main_process_identified ? "확인" : "미확인"} · 자식 프로세스 {check.facts?.child_process_identified ? "확인" : "미확인"} · 두 Pod 구분 {check.facts?.distinct_pods_identified ? "확인" : "미확인"}{check.facts?.threaded_child_identified && " · 스레드의 파일 접근 확인"}</div>
            <div>{check.restart_requested ? <>중단 감지 {check.facts?.gap_detected ? "확인" : "미확인"} · 복구 후 새 이벤트 {check.facts?.recovery_verified ? "확인" : "미확인"}</> : "에이전트 재시작 시험 안 함"}</div>
            <span className="small muted">시험 대상·기록된 구간의 실측이며 중단 구간 이벤트의 완전한 복원을 뜻하지 않습니다.</span>
          </div>)}</div>
        </TableCard>}
        {(state.agents ?? []).filter((a) => a.last_exit_summary).map((a) => <p className="notice" key={a.node}>에이전트 종료 이력: {a.node} · {formatTime(a.last_exit_at)} · {a.last_exit_summary} · 재시작 {a.restart_count ?? "미확인"}회</p>)}
        {!current && policies.length > 0 && <p role="alert" className="notice">수집이 지연되거나 실패했습니다. 아래 모드는 마지막 관측값이며 현재 모드로 확정하지 않습니다.</p>}
        <TableCard title={current ? "현재 정책 모드" : "마지막 관측 정책 모드"}>
          <table className={rt.statusTable}><thead><tr><th>정책·네임스페이스</th><th>노드·정책 ID</th><th>모드</th><th>방식</th><th>로드 상태</th></tr></thead><tbody>
            {policies.map((p) => <tr key={`${p.node}/${p.namespace}/${p.name}`}>
              <td>{p.name}<div className="small muted">{p.namespace ?? "클러스터 범위"}</div></td>
              <td>{p.node ?? "미확인"} · {p.id}</td>
              <td>{MODES[p.mode]}{!current && " (현재 미확인)"}</td>
              <td>{p.method ? METHODS[p.method] ?? p.method : "별도 방식 미선언"}</td>
              <td>{p.state === "enabled" ? "로드됨" : p.state}{p.load_error && <div className="small">{p.load_error}</div>}</td>
            </tr>)}
          </tbody></table>
          {!policies.length && <p className={rt.empty}>조회된 정책이 없습니다. 사건이 없다는 뜻이나 차단 해제 확인을 뜻하지 않습니다.</p>}
        </TableCard>
        <TableCard title="서버 기능 사전 점검">
          <p className={rt.pad}>이 표는 기능 조건 점검입니다. 규칙 로드와 실제 차단 성공 여부는 아래 실측 결과로 확인합니다.</p>
          <table className={rt.statusTable}><thead><tr><th>노드·커널</th><th>점검 시각</th><th>사전 조건 충족 후보</th><th>확인 필요</th><th>권장 후보</th></tr></thead><tbody>
            {capabilities.map((c) => <tr key={c.node ?? "node"}>
              <td>{c.node ?? "미확인"}<div className="small">{c.kernel ?? "커널 미확인"} · AppArmor {c.apparmor === true ? "활성" : c.apparmor === false ? "비활성" : "미확인"}</div></td>
              <td>{formatTime(c.checked_at)}</td>
              <td>{(c.available ?? []).map((m) => METHODS[m] ?? m).join(", ") || "없음"}</td>
              <td>{(c.unknown ?? []).map((m) => METHODS[m] ?? m).join(", ") || "없음"}<div className="small muted">조건 미충족: {(c.unavailable ?? []).map((m) => METHODS[m] ?? m).join(", ") || "없음"}</div></td>
              <td>{c.recommended ? METHODS[c.recommended] ?? c.recommended : "실측 후 결정"}</td>
            </tr>)}
          </tbody></table>
          {!capabilities.length && <p className={rt.empty}>서버 기능 점검 결과가 없습니다.</p>}
        </TableCard>
        <TableCard title="모드 전환·되돌림 기록">
          <table className={rt.statusTable}><thead><tr><th>시각·정책</th><th>요청</th><th>결과</th><th>전후 정책 ID</th><th>되돌림</th></tr></thead><tbody>
            {[...transitions].reverse().map((t, i) => <tr key={`${t.at}/${i}`}>
              <td>{formatTime(t.at)}<div className="small">{t.namespace} / {t.policy}</div></td>
              <td>{MODES[t.requested_mode] ?? t.requested_mode}</td>
              <td>{t.ok ? "전환 확인" : "전환 미확인"}<div className="small muted">{t.reason}</div></td>
              <td>{t.before?.id ?? "–"} → {t.after?.id ?? "–"}</td>
              <td>{t.rollback_ok === true ? "복귀 확인" : t.rollback_ok === false ? "복귀 미확인" : "해당 없음"}</td>
            </tr>)}
          </tbody></table>
          {!transitions.length && <p className={rt.empty}>기록된 전환이 없습니다. 현재 모드는 위의 서버 조회 결과로 확인하세요.</p>}
        </TableCard>
        <TableCard title="실제 시험 결과·근거">
          <p className={rt.pad}>시험용 대상과 기록된 시간 구간의 결과입니다. 프로세스 종료 확인만으로 파일 읽기 거부를 확정하지 않습니다.</p>
          <table className={rt.statusTable}><thead><tr><th>시험·자료</th><th>방식·검증 목표</th><th>파일 읽기 거부</th><th>프로세스 종료</th><th>재시작·복구</th><th>감시 복귀·근거</th></tr></thead><tbody>
            {verifications.map((v) => <tr key={v.id}>
              <td>{formatTime(v.finished_at)}<div className="small">{v.namespace}</div><OriginBadge origin={v.data_origin === "real" ? "MEASURED" : "EXAMPLE"} detail={v.data_origin === "real" ? "real" : "synthetic"} /></td>
              <td>{METHODS[v.method] ?? v.method}<div className="small muted">목표: {v.verification.expected_effect === "file_read_denial" ? "읽기 거부" : "프로세스 종료"}</div>{v.variant === "override_only_diagnostic" && <Badge tone="caution">Override 단독 진단 · 시험 전용</Badge>}</td>
              <td>{effect(v.verification.file_read_denial)}{v.verification.file_denial_basis && <div className="small muted">{v.verification.file_denial_basis === "userspace_errno" ? "읽기 오류 반환" : "커널 읽기 검사 거부"}</div>}</td>
              <td>{effect(v.verification.process_termination)}</td>
              <td>{effect(v.verification.service_recovery)}</td>
              <td>{v.verification.status === "unsupported" ? "규칙 로드 미지원" : v.verification.monitor_restored ? "감시 복귀 확인" : "감시 복귀 미확인"}
                <details><summary>시험 근거</summary>
                  <div className="small">Pod UID: {v.pod_uid ?? "미확인"}<br />정책 ID: {v.policy_id ?? "미확인"}<br />종료 코드: {v.termination?.exitCode ?? "미확인"}<br />시작: {formatTime(v.started_at)}</div>
                  {v.task_identity && <p className="small">대상 확인: 커널 task의 PID namespace·시작 시각을 CRI 컨테이너 주 프로세스와 대조했습니다.</p>}
                  <ul>{(v.events ?? []).map((e, i) => <li key={i}>{e.action ?? "행위 미확인"} · PID {e.pid ?? "미확인"}{e.return_value != null && ` · 커널 반환값 ${e.return_value}`} · {formatTime(e.time)}{e.identity_source === "kernel_task" && " · 커널 task 대조"}</li>)}</ul>
                  {v.error && <p className="small">{v.error}</p>}
                  {v.load?.state && <p className="small">정책 로드: {v.load.state}</p>}
                  {v.load?.error && <p className="small">{v.load.error}</p>}
                  {v.reassessment_note && <p className="small muted">{v.reassessment_note} · 재판정 {formatTime(v.reassessed_at)}</p>}
                  {v.policy_spec_sha256 && <p className="small mono">정책 spec SHA-256: {v.policy_spec_sha256}</p>}
                  <button className="btn" onClick={() => downloadEvidence(v)}>시험 근거 다운로드</button>
                </details>
              </td>
            </tr>)}
          </tbody></table>
          {!verifications.length && <p className={rt.empty}>연결된 시험 근거가 없습니다. 이벤트만으로 차단 성공을 확정하지 않습니다.</p>}
        </TableCard>
      </>;
    }}</Query>
  </div>;
}
