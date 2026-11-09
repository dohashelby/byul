import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import ui from "../components/ui.module.css";
import styles from "./RuntimeOperations.module.css";
import { CHECK_LABELS, SESSION_CREDENTIAL, enforceReady, operationApi, type Actor, type Operation, type AgentState, type Operations, type Readiness } from "../api/operations";

import { useOperationToken } from "../api/operationSession";
import { formatLocal } from "../lib/time";

const ROLE_LABELS: Record<string, string> = { viewer: "조회자", runtime_operator: "운영 요청자", approver: "승인자", agent: "에이전트", admin: "관리자" };
const CAPABILITY_REASONS = {
  static_unavailable: "거부: 사전 점검에서 불가 (실측 근거보다 우선)",
  evidence_required: "미확인: 동일 노드·커널·방식·정책 spec의 실측 시험 필요",
  capability_missing: "미확인: 차단 능력 정보 또는 커널 정보 없음",
};

function LabSettingsBadge({ data }: { data: Operations | null }) {
  if (!data || (data.monitor_seconds >= 86400 && !data.lab_self_approval)) return null;
  return <div><span className={`${ui.badge} ${ui.warn}`}>
    LAB 설정: 모니터 기간 {data.monitor_seconds}초 · 자기 승인 {data.lab_self_approval ? "허용" : "비허용"}
  </span></div>;
}

export function OperationCredentials({ token, setToken, actor, unknownRoleLabel = "현재 역할: 조회 후 확인" }: { token: string; setToken: (v: string) => void; actor?: Actor; unknownRoleLabel?: string }) {
  const [draft, setDraft] = useState("");
  if (token === SESSION_CREDENTIAL) return <div className="row wrap">
    <span>로그인 계정으로 요청 · {actor ? `현재 역할: ${ROLE_LABELS[actor.role] ?? actor.role} (${actor.role}) · ${actor.id}` : unknownRoleLabel}</span>
  </div>;
  return <div className="row wrap">
    {token ? <><span>운영 토큰 저장됨 · {actor ? `현재 역할: ${ROLE_LABELS[actor.role]} (${actor.role}) · ${actor.id}` : unknownRoleLabel}</span>
      <button className="btn" type="button" onClick={() => { setToken(""); setDraft(""); }}>토큰 지우기</button></> : <>
      <label className="small">운영 토큰 <input className="input" type="password" autoComplete="off" value={draft} onChange={e => setDraft(e.target.value)} /></label>
      <button className="btn" type="button" disabled={!draft.trim()} onClick={() => { setToken(draft.trim()); setDraft(""); }}>토큰 저장</button>
    </>}
  </div>;
}

export function BundleApplyRequest({ policyId, revision, environmentId, environmentRevision, confirmed, override }: {
  policyId: string; revision: number | null; environmentId: string; environmentRevision: number | null; confirmed: boolean; override: string;
}) {
  const [token, setToken] = useOperationToken();
  const [server, setServer] = useState("");
  const [expected, setExpected] = useState("");
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  async function request() {
    setPending(true);
    try {
      const item = await operationApi<{ id: string }>(token, "", { kind: "bundle.apply", server,
        expected: { bundle_id: expected, approval: "verified" }, policy_id: policyId, policy_revision: revision,
        environment_id: environmentId, environment_revision: environmentRevision,
        server_values_override: override.trim() ? JSON.parse(override) : undefined });
      setMessage(`적용 요청 등록: ${item.id} · 별도 승인 필요`);
    } catch (error) { setMessage(error instanceof Error ? error.message : "요청 실패"); }
    finally { setPending(false); }
  }
  return <div className="stack">
    <h3>서버 적용 요청</h3>
    <div className="row wrap"><OperationCredentials token={token} setToken={setToken} />
      <label>서버 ID <input className="input" value={server} onChange={e => setServer(e.target.value)} /></label>
      <label>현재 적용 묶음 ID <input className="input" value={expected} onChange={e => setExpected(e.target.value)} /></label></div>
    <button className="btn" type="button" onClick={request} disabled={pending || !confirmed || !token || !server || !expected || !environmentId || !environmentRevision}>적용 요청</button>
    {message && <p role="status">{message}</p>}<Link to="/runtime/operations">운영 요청 목록</Link>
  </div>;
}

function EnforcementApplyRequest({ data, token, refresh }: { data: Operations; token: string; refresh: () => Promise<void> }) {
  const [server, setServer] = useState("");
  const [cilium, setCilium] = useState(false);
  const [applyCilium, setApplyCilium] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const agent = data.agents.find(a => a.server === server);
  const bundle = agent?.bundles?.find(b => b.bundle_id === agent.current?.bundle_id);
  async function submit() {
    if (!agent?.current || !bundle || !agentFresh(agent)) return;
    setPending(true);
    try {
      await operationApi(token, "", { kind: "enforcement.apply", server, expected: agent.current,
        bundle_id: bundle.bundle_id, bundle_sha256: bundle.sha256,
        components: cilium ? ["tetragon", "cilium"] : ["tetragon"], cilium_apply: cilium && applyCilium });
      setMessage("규칙 적용 요청 등록 · 별도 approver 승인 필요");
      await refresh();
    } catch (e) { setMessage(e instanceof Error ? e.message : "요청 실패"); }
    finally { setPending(false); }
  }
  return <section className="card card-pad stack" aria-label="외부 규칙 적용"><h2>외부 규칙 처음 적용·동기화</h2>
    <label>규칙 대상 서버 <select className="input" value={server} onChange={e => setServer(e.target.value)}>
      <option value="">서버 선택</option>{data.agents.map(a => <option key={a.server} value={a.server}>{a.server}</option>)}
    </select></label>
    <p>현재 묶음: {bundle?.bundle_id ?? "미확인"} · SHA-256: {bundle?.sha256 ?? "미확인"}</p>
    <p>Tetragon은 항상 monitor로 생성·교체합니다. enforce 정책 변경은 먼저 기존 경로로 monitor 복귀가 필요합니다.</p>
    <label><input type="checkbox" checked disabled /> Tetragon</label>
    <label><input type="checkbox" checked={cilium} onChange={e => { setCilium(e.target.checked); setApplyCilium(false); }} /> Cilium 포함</label>
    {cilium && <><p className="notice">Cilium은 monitor 모드가 없어 즉시 트래픽이 차단될 수 있습니다. 기본은 dry-run·diff이며, CRD가 없으면 건너뜁니다.</p>
      <label><input type="checkbox" checked={applyCilium} onChange={e => setApplyCilium(e.target.checked)} /> Cilium 실제 적용</label></>}
    <p>실행 에이전트의 --execute가 필요하며 별도 approver만 승인할 수 있습니다. 자기 승인은 허용하지 않습니다.</p>
    <button className="btn" disabled={pending || !token || !bundle || !agentFresh(agent)} onClick={submit}>규칙 적용 요청</button>
    {message && <p role="status">{message}</p>}
  </section>;
}

function MonitorWindows({ row }: { row: Readiness }) {
  if (!row.monitor_windows?.length) return <p>현재 모니터 창 없음 · 관측·상태 갱신 필요</p>;
  return <div>{row.monitor_windows.map((window, index) => <p key={`${window.node}-${index}`}>
    현재 모니터 창 · {window.node} · 창 시작 {timeLabel(window.since)} · 마지막 확인 {timeLabel(window.until)} · {window.continuous ? "연속 관측" : "관측 공백"}
    {` · 이벤트 ${window.event_count}건 · 검토 ${window.reviewed_event_count}/${window.event_count}건 · 오탐 ${window.false_positive_count}건`}
    {window.reviewed_event_count < window.event_count ? " · 미검토 있음" : " · 미검토 없음"}
  </p>)}</div>;
}

function PolicyChecks({ row, reportedAt }: { row: Readiness; reportedAt?: string }) {
  const reported = timeLabel(reportedAt);
  return <div>
    <p title={reported}>에이전트 판단(보고 시각 {reported === "미확인" ? reported : reported.slice(-8)} 기준)</p>
    <ul>{Object.entries(CHECK_LABELS).map(([key, label]) => <li key={key}>{row.checks[key] ? "충족" : "미충족"} · {label}</li>)}</ul>
    <MonitorWindows row={row} />
  </div>;
}

export function ModeRequests() {
  const [token, setToken] = useOperationToken();
  const [data, setData] = useState<Operations | null>(null);
  const [confirmation, setConfirmation] = useState("");
  const [selection, setSelection] = useState<{ server: string; name: string; mode: string } | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  useEffect(() => { setData(null); setSelection(null); setConfirmation(""); setMessage(""); }, [token]);
  async function load() {
    setSelection(null); setConfirmation("");
    try { setData(await operationApi<Operations>(token)); setMessage(""); }
    catch (e) { setMessage(e instanceof Error ? e.message : "조회 실패"); }
  }
  async function request(agent: AgentState, name: string, mode: string) {
    const row = agent.policies.find(p => p.name === name);
    if (!row) return;
    setPending(true);
    try {
      const item = await operationApi<{id: string}>(token, "", { kind: "tetragon.mode", server: agent.server,
        expected: row.expected, policy_name: name, mode, confirmation: mode === "enforce" ? "ENGINE_RESTART" : undefined });
      setMessage(`전환 요청 등록: ${item.id}`);
      setSelection(null);
      setConfirmation("");
    } catch (e) { setMessage(e instanceof Error ? e.message : "요청 실패"); }
    finally { setPending(false); }
  }
  return <section className="card card-pad stack"><h2>정책 모드 전환 요청</h2>
    <LabSettingsBadge data={data} />
    <div className="row wrap"><OperationCredentials token={token} setToken={setToken} actor={data?.actor} unknownRoleLabel="역할 미확인(전환 조건 조회 시 확인)" /><button className="btn" disabled={!token} onClick={load}>전환 조건 조회</button></div>
    {data?.agents.map(agent => <div key={agent.server} className="stack"><strong>{agent.server} · 마지막 조회 {timeLabel(agent.last_poll_at)}</strong>
      {agent.policies.map(row => {
        const mode = selection?.server === agent.server && selection.name === row.name ? selection.mode : "";
        const containsSigkill = row.contains_sigkill ?? ["sigkill_fd_install", "override_lsm", "override_syscall"].includes(row.method ?? "");
        const needsConfirmation = mode === "enforce" && containsSigkill;
        return <div key={row.name} className="notice"><strong>{row.name} · {row.expected.mode}</strong>
        <PolicyChecks row={row} reportedAt={agent.at} />
        {row.checks.capability && row.capability_basis && <p>확인: {row.capability_basis === "live_trial" ? "실측 시험으로 확인" : "사전 점검으로 확인"}</p>}
        {!row.checks.capability && row.capability_reason && <p>{CAPABILITY_REASONS[row.capability_reason]}</p>}
        {!enforceReady(agent, row) && <p>차단 전환 조건 미충족 또는 상태 갱신 필요</p>}
        <label>전환 모드 <select className="input" aria-label={`${agent.server} · ${row.name} 전환 모드`} value={mode} disabled={pending} onChange={e => {
          setSelection({ server: agent.server, name: row.name, mode: e.target.value }); setConfirmation("");
        }}><option value="">전환 요청 선택</option><option value="enforce">enforce (차단)</option><option value="monitor">monitor (탐지)</option></select></label>
        {["lsm_file_open", "override_syscall"].includes(row.method ?? "") && !containsSigkill && <p>파일 열기만 거부, 프로세스 유지</p>}
        {needsConfirmation && <>
          <p>Sigkill 계열: 엔진 종료 → Gateway 503 → 재시작</p>
          <label>확인 입력: ENGINE_RESTART <input aria-label="Sigkill 확인 입력" className="input" value={confirmation} onChange={e => setConfirmation(e.target.value)} /></label>
        </>}
        <button className="btn" disabled={pending || !token || !mode || (mode === "enforce" && !enforceReady(agent, row)) || (needsConfirmation && confirmation !== "ENGINE_RESTART")}
          onClick={() => request(agent, row.name, mode)}>{mode === "enforce" ? "차단으로 전환" : mode === "monitor" ? "탐지로 전환" : "전환 요청"}</button>
      </div>; })}
    </div>)}
    {message && <p role="status">{message}</p>}<Link to="/runtime/operations">운영 요청 목록</Link>
  </section>;
}

const STATE_LABELS: Record<string, string> = {
  requested: "승인 대기", approved: "승인됨", claimed: "가져감", running: "실행 중", succeeded: "완료",
  checked: "실행 없이 확인 완료", failed: "실패", rolled_back: "되돌림", unknown: "확인 필요", expired: "만료",
};
const KIND_LABELS: Record<string, string> = { "enforcement.apply": "외부 규칙 적용", "bundle.apply": "새 묶음 적용", "bundle.switch": "이전 묶음으로 되돌리기", "tetragon.mode": "정책 모드 전환" };
const STEP_LABELS: Record<string, string> = { render: "렌더", "dry-run": "서버 dry-run·비교", apply: "규칙 적용", verify: "적용 확인", validation: "검증", link: "묶음 연결", restart: "재시작", status: "상태 확인", "set-mode": "모드 전환", rollback: "되돌림" };
const STEP_STATES: Record<string, string> = { passed: "통과", failed: "실패", unknown: "미확인", skipped: "생략" };
const REASON_LABELS: Record<string, string> = {
  BUNDLE_ROLES_MISSING: "필수 4역할이 없는 묶음", BUNDLE_INVALID: "묶음 검증 실패", CURRENT_LINK_INVALID: "현재 적용 묶음 링크 오류",
  STATUS_UNREACHABLE: "Gateway 상태 조회 불가", STATUS_MISMATCH: "Gateway와 현재 적용 묶음 불일치",
};
function timeLabel(value?: string) {
  if (!value) return "미확인";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "미확인" : formatLocal(value);
}
function evidenceHashes(hashes: string[] = []) {
  return hashes.filter(hash => !/^0{64}$/.test(hash));
}
function EvidenceSummary({ hashes }: { hashes?: string[] }) {
  const evidence = evidenceHashes(hashes);
  if (!evidence.length) return <>없음</>;
  return <span className={`mono ${styles.evidence}`} title={evidence.join("\n")}>
    {evidence[0].slice(0, 12)}…{evidence.length > 1 && ` 외 ${evidence.length - 1}개`}
  </span>;
}
function stateLabel(op: Operation) {
  return STATE_LABELS[op.state] ?? op.state;
}
function agentFresh(agent?: AgentState) {
  const age = Date.now() - Date.parse(agent?.at ?? "");
  return age >= 0 && age < 30_000;
}
function progress(op: Operation) {
  const approved = !op.approval_required || op.approvals.length > 0;
  const claimed = !!op.claimed_by || ["claimed", "running", "checked", "succeeded", "failed", "rolled_back", "unknown"].includes(op.state);
  const running = !!op.running_at || (!!op.result && !op.result.dry_run && ["succeeded", "rolled_back"].includes(op.state));
  const done = ["checked", "succeeded", "failed", "rolled_back", "unknown", "expired"].includes(op.state);
  return [["요청됨", true], ["승인", approved], ["가져감", claimed], ["실행", running], ["결과", done]]
    .map(([label, reached]) => `${label} ${reached ? "✓" : done ? "생략" : "대기"}`).join(" → ");
}
function approvalAllowed(op: Operation, data: Operations) {
  const actor = data.actor;
  const self = actor?.id === op.requested_by;
  if (op.kind === "enforcement.apply") return actor?.role === "approver" && !self;
  return actor?.role === "approver" ? (!self || data.lab_self_approval) :
    actor?.role === "runtime_operator" && self && data.lab_self_approval;
}
function ResultSummary({ op }: { op: Operation }) {
  return <>{op.result?.code ?? op.error_code ?? "결과 대기"}
    {op.result?.dry_run && " · 실행 없이 확인"}
    {op.result?.reason_code && <div>{op.result.reason_code} · {REASON_LABELS[op.result.reason_code] ?? "세부 사유"}</div>}
  </>;
}

export function RuntimeOperationsPage() {
  const [token, setToken] = useOperationToken();
  const [data, setData] = useState<Operations | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [filter, setFilter] = useState("");
  const [selected, setSelected] = useState("");
  const [showSwitch, setShowSwitch] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [server, setServer] = useState("");
  const [current, setCurrent] = useState("");
  const [bundle, setBundle] = useState("");
  const tokenRef = useRef(token);
  tokenRef.current = token;
  useEffect(() => {
    let active = true;
    setData(null); setSelected(""); setMessage(""); setShowSwitch(false);
    if (token) operationApi<Operations>(token).then(value => { if (active) setData(value); })
      .catch(e => { if (active) setMessage(e instanceof Error ? e.message : "조회 실패"); });
    return () => { active = false; };
  }, [token]);
  useEffect(() => {
    if (!showSwitch || advanced) return;
    const agent = data?.agents.find(a => a.server === server);
    const reported = agent?.current?.bundle_id ?? "";
    setCurrent(reported);
    setBundle(previous => previous !== reported && agent?.bundles?.some(b => b.bundle_id === previous) ? previous : "");
  }, [data, server, showSwitch, advanced]);
  async function load() {
    const credential = token;
    try {
      const value = await operationApi<Operations>(credential);
      if (tokenRef.current === credential) { setData(value); setMessage(""); }
    } catch (e) { if (tokenRef.current === credential) setMessage(e instanceof Error ? e.message : "조회 실패"); }
  }
  async function approve(id: string) {
    setPending(true);
    try { await operationApi(token, `/${id}/approve`, {}); await load(); }
    catch (e) { setMessage(e instanceof Error ? e.message : "승인 실패"); }
    finally { setPending(false); }
  }
  function chooseServer(value: string) {
    const agent = data?.agents.find(a => a.server === value);
    setServer(value); setCurrent(agent?.current?.bundle_id ?? ""); setBundle("");
  }
  function openSwitch() {
    setAdvanced(false); chooseServer(data?.agents.find(a => a.current)?.server ?? data?.agents[0]?.server ?? "");
    setShowSwitch(true);
  }
  async function switchBundle() {
    if (!advanced && !agentFresh(data?.agents.find(a => a.server === server))) {
      setMessage("상태 보고가 오래되었습니다. 운영 요청 조회로 갱신하세요.");
      return;
    }
    setPending(true);
    try {
      await operationApi(token, "", { kind: "bundle.switch", server, bundle_id: bundle,
        expected: { bundle_id: current, approval: "verified" } });
      setShowSwitch(false); await load();
    } catch (e) { setMessage(e instanceof Error ? e.message : "요청 실패"); }
    finally { setPending(false); }
  }
  const chosenAgent = data?.agents.find(a => a.server === server);
  const fresh = agentFresh(chosenAgent);
  const canSwitch = advanced || (fresh && chosenAgent?.current?.bundle_id === current && chosenAgent?.bundles?.some(b => b.bundle_id === bundle));
  const detail = data?.items.find(op => op.id === selected);
  const rows = (data?.items ?? []).filter(op => !filter || op.state === filter).slice().sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  return <div className="page stack"><h1>운영 요청</h1>
    <LabSettingsBadge data={data} />
    <div className="row wrap"><OperationCredentials token={token} setToken={setToken} actor={data?.actor} />
      <button className="btn" disabled={!token || pending} onClick={load}>운영 요청 조회</button></div>
    {message && <p role="status">{message}</p>}
    <section className="card card-pad stack" aria-label="운영 요청 목록"><h2>운영 요청 목록</h2>
      <label>상태 필터 <select className="input" value={filter} onChange={e => setFilter(e.target.value)}><option value="">전체 상태</option>
        {Object.entries(STATE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select></label>
      {!data ? <p>{token ? "요청 목록 조회 중 또는 조회 실패" : "토큰을 저장하면 운영 요청을 조회합니다."}</p> : <>
        {rows.length === 0 ? <p>표시할 운영 요청이 없습니다.</p> : <div className={ui.tableWrap}><table className={`${ui.table} ${styles.operations}`}><thead><tr>
          <th>요청 종류 / 대상 서버</th><th>상태 / 단계</th><th>요청자 / 승인자 (역할 ID)</th><th>시각</th><th>결과 코드 / 세부 사유</th><th>증거 해시</th><th>승인</th>
        </tr></thead><tbody>{rows.map(op => <tr key={op.id} className={selected === op.id ? ui.selected : undefined} onClick={() => setSelected(op.id)} aria-selected={selected === op.id} style={{ cursor: "pointer" }}>
          <td><button className="btn" onClick={() => setSelected(op.id)}>{KIND_LABELS[op.kind] ?? op.kind} · {op.server}</button></td>
          <td><strong>{stateLabel(op)}</strong><div>{progress(op)}</div></td>
          <td>요청: {op.requested_by}<div>승인: {op.approvals.map(a => `${a.token_id}${a.self_approval ? " (자기 승인)" : ""}`).join(", ") || (op.approval_required ? "승인 대기" : "별도 승인 없음")}</div></td>
          <td>요청: {timeLabel(op.created_at)}<div>결과: {timeLabel(op.result?.finished_at)}</div></td>
          <td><ResultSummary op={op} /></td>
          <td><EvidenceSummary hashes={op.result?.evidence_sha256} /></td>
          <td>{op.state === "requested" && (data.actor?.role === "approver" || (data.lab_self_approval && data.actor?.id === op.requested_by)) &&
            <button className="btn" disabled={pending || !approvalAllowed(op, data)} title={!approvalAllowed(op, data) ? "자기 요청은 승인할 수 없습니다" : undefined}
              onClick={e => { e.stopPropagation(); void approve(op.id); }}>승인</button>}</td>
        </tr>)}</tbody></table></div>}
      </>}
    </section>
    {detail && <section className="card card-pad stack" aria-label="운영 요청 상세"><div className="row wrap"><h2>요청 상세 · {detail.id}</h2><button className="btn" onClick={() => setSelected("")}>상세 닫기</button></div>
      <p>{KIND_LABELS[detail.kind] ?? detail.kind} · {detail.server} · {stateLabel(detail)}</p>
      <p>{progress(detail)}</p><p>요청자: {detail.requested_by} ({ROLE_LABELS[detail.requested_role ?? "runtime_operator"]})</p>
      <p>승인자: {detail.approvals.map(a => `${a.token_id} (${ROLE_LABELS[a.role ?? "approver"]}) · ${timeLabel(a.at)}${a.self_approval ? " · LAB 자기 승인" : ""}`).join(", ") || "없음"}</p>
      <p>요청 {timeLabel(detail.created_at)} · 가져감 {timeLabel(detail.claimed_at)} · 실행 {timeLabel(detail.running_at)} · 결과 {timeLabel(detail.result?.finished_at)}</p>
      <p>요청 시 현재 적용 묶음: {detail.expected?.bundle_id ?? "미확인"} · 대상 묶음 ID: {String(detail.payload?.bundle_id ?? "해당 없음")}</p>
      <p>실행 전 현재 적용 묶음: {detail.result?.before?.bundle_id ?? "미확인"} · 실행 후 현재 적용 묶음: {detail.result?.after?.bundle_id ?? "미확인"}</p>
      <div>결과: <ResultSummary op={detail} /></div>
      {detail.result?.steps.map((s, i) => <div key={i}>{STEP_LABELS[s.stage] ?? s.stage}: {STEP_STATES[s.state] ?? s.state}</div>)}
      {detail.result?.components?.map(c => <p key={c.component}>{c.component}: {c.state === "not_installed" ? "not_installed · 미설치 건너뜀" : c.state}</p>)}
      {!!detail.result?.policies?.length && <div className={ui.tableWrap}><table className={ui.table} aria-label="정책별 적용 결과"><thead><tr>
        <th>정책</th><th>구성요소</th><th>동작</th><th>모드</th><th>spec SHA-256</th>
      </tr></thead><tbody>{detail.result.policies.map(p => <tr key={`${p.component}-${p.name}`}>
        <td>{p.name}</td><td>{p.component}</td><td>{p.action}</td><td>{p.mode ?? "해당 없음"}</td><td title={p.spec_sha256}>{p.spec_sha256.slice(0, 12)}…</td>
      </tr>)}</tbody></table></div>}
      {evidenceHashes(detail.result?.evidence_sha256).map(hash => <div className="mono" key={hash} style={{ overflowWrap: "anywhere" }}>증거 SHA-256: {hash}</div>)}
      {!evidenceHashes(detail.result?.evidence_sha256).length && <p>증거 SHA-256: 없음</p>}
      {detail.state === "unknown" && <p className="notice">서버 상태를 수동 확인하세요. 자동으로 다시 실행하지 않습니다.</p>}
    </section>}
    <section className="card card-pad stack"><h2>에이전트 서버 상태</h2>
      {data?.agents.map(a => <div key={a.server}><strong>{a.server}</strong> · 에이전트 마지막 조회 {timeLabel(a.last_poll_at)} · 상태 보고 {timeLabel(a.at)} · 현재 적용 묶음 {a.current?.bundle_id ?? "미확인"}
        {a.policies.map(row => <div key={row.name}><strong>{row.name}</strong><PolicyChecks row={row} reportedAt={a.at} /></div>)}
      </div>)}
      {data?.agents.length === 0 && <p>등록된 에이전트가 없습니다.</p>}
    </section>
    {data?.actor?.role === "runtime_operator" && <button className="btn" onClick={openSwitch}>이전 묶음으로 되돌리기</button>}
    {showSwitch && <section className="card card-pad stack" aria-label="묶음 되돌리기"><h2>이전 묶음으로 되돌리기</h2>
      <p>에이전트가 보고한 서버와 현재 적용 묶음을 사용합니다. 요청을 가져갈 때 서버에서 다시 검증합니다.</p>
      <label>서버 <select className="input" value={server} onChange={e => chooseServer(e.target.value)}><option value="">서버 선택</option>
        {data?.agents.map(a => <option key={a.server} value={a.server}>{a.server}</option>)}
      </select></label>
      <p>현재 적용 묶음: <strong>{current || "미확인"}</strong> · 상태 보고 {timeLabel(chosenAgent?.at)}</p>
      <label>되돌릴 묶음 <select className="input" value={bundle} onChange={e => setBundle(e.target.value)}><option value="">묶음 선택</option>
        {chosenAgent?.bundles?.filter(b => b.bundle_id !== current).map(b => <option key={b.bundle_id} value={b.bundle_id}>{b.bundle_id}</option>)}
      </select></label>
      {!fresh && <p className="notice">상태 보고가 없거나 오래되었습니다. 운영 요청 조회로 갱신하세요.</p>}
      {!chosenAgent?.bundles?.length && <p>에이전트가 보고한 묶음 목록이 없습니다. 에이전트 갱신이 필요합니다.</p>}
      <details onToggle={e => { setAdvanced(e.currentTarget.open); setBundle(""); if (!e.currentTarget.open) chooseServer(server); }}><summary>고급 · 직접 입력</summary>
        <label>서버 ID <input className="input" value={server} onChange={e => { setServer(e.target.value); setBundle(""); }} /></label>
        <label>현재 적용 묶음 ID <input className="input" value={current} onChange={e => setCurrent(e.target.value)} /></label>
        <label>되돌릴 묶음 ID <input className="input" value={bundle} onChange={e => setBundle(e.target.value)} /></label>
      </details>
      <div className="row wrap"><button className="btn" disabled={pending || !token || !server || !current || !bundle || bundle === current || !canSwitch} onClick={switchBundle}>되돌리기 요청</button>
        <button className="btn" disabled={pending} onClick={() => setShowSwitch(false)}>취소</button></div>
    </section>}
    {data?.actor?.role === "runtime_operator" && <EnforcementApplyRequest data={data} token={token} refresh={load} />}
    <p>새 묶음 적용·차단 전환·외부 규칙 적용은 별도 승인이 필요합니다. 기본 에이전트는 실행 없이 확인만 수행합니다.</p>
  </div>;
}
