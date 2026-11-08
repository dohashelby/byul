import { BundleApplyRequest } from "./RuntimeOperations";
import { useEffect, useState } from "react";
import { useDocList, useDocRevisions, useEnvironmentRevision, usePolicyRevision } from "../api/hooks";
import type { RevisionMeta } from "../api/types";

type Metadata = {
  bundle_id: string;
  bundle_sha256: string;
  policy_sha256: string;
  files: { role: string; sha256: string; bytes: number }[];
  server_values_source: "human_input" | "environment_revision";
  human_input_paths: string[];
};

function shellQuote(value: string): string {
  return `'${value.replaceAll("'", `'"'"'`)}'`;
}

export function BundleExport({ policyId, revisions }: { policyId: string; revisions: RevisionMeta[] }) {
  const [policyRevision, setPolicyRevision] = useState<number | null>(revisions.filter((r) => r.status === "CONFIRMED").at(-1)?.revision ?? revisions.at(-1)?.revision ?? null);
  const policy = usePolicyRevision(policyId, policyRevision);
  const envs = useDocList("environments");
  const [envId, setEnvId] = useState("");
  const envRevisions = useDocRevisions("environments", envId || null);
  const [envRevision, setEnvRevision] = useState<number | null>(null);
  const environment = useEnvironmentRevision(envId || null, envRevision);
  const [override, setOverride] = useState("");
  const [bundleId, setBundleId] = useState("");
  const [role, setRole] = useState<"runtime_operator" | "policy_operator">("runtime_operator");
  const [pending, setPending] = useState(false);
  const [errors, setErrors] = useState<{ path: string; message: string }[]>([]);
  const [result, setResult] = useState<Metadata | null>(null);

  useEffect(() => {
    if (policyRevision == null && revisions.length) {
      setPolicyRevision(revisions.filter((r) => r.status === "CONFIRMED").at(-1)?.revision ?? revisions.at(-1)!.revision);
    }
  }, [policyRevision, revisions]);
  useEffect(() => {
    const ref = policy.data?.document.environment_ref;
    if (ref?.id) {
      setEnvId(ref.id);
      setEnvRevision(ref.revision);
    }
  }, [policy.data]);
  useEffect(() => {
    if (!envId && envs.data?.items.length) setEnvId(envs.data.items[0].doc_id);
  }, [envId, envs.data]);
  useEffect(() => {
    if (envRevision == null && envRevisions.data?.items.length) setEnvRevision(envRevisions.data.items.at(-1)!.revision);
  }, [envRevision, envRevisions.data]);

  const confirmed = policy.data?.meta.status === "CONFIRMED";
  const context = environment.data?.document.deployment?.kubernetes_context ?? "<ctx>";
  const namespace = environment.data?.document.deployment?.namespace ?? "<ns>";
  const command = result ? `sudo -E env KUBECONFIG="$HOME/.kube/config" PATH="$PATH" .venv/bin/python -B -m serving_runtime bundle apply \\\n  --bundle-dir '<받은-묶음-폴더>' --bundle-root /srv/serving/bundles --context ${shellQuote(context)} --namespace ${shellQuote(namespace)} \\\n  --kubeconfig "$HOME/.kube/config" --status-url http://127.0.0.1:8080 --token-env-file '<토큰파일>' --dry-run` : "";

  async function generate() {
    setErrors([]);
    setResult(null);
    let serverValuesOverride: object | undefined;
    try {
      if (override.trim()) {
        serverValuesOverride = JSON.parse(override);
        if (!serverValuesOverride || Array.isArray(serverValuesOverride) || typeof serverValuesOverride !== "object") throw Error("JSON 객체가 필요합니다");
      }
    } catch {
      setErrors([{ path: "/server_values_override", message: "서버 값 덮어쓰기는 JSON 객체여야 합니다" }]);
      return;
    }
    setPending(true);
    try {
      const response = await fetch(`/api/policies/${encodeURIComponent(policyId)}/revisions/${policyRevision}/bundle`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ environment_id: envId, environment_revision: envRevision,
          requested_by_role: role, server_values_override: serverValuesOverride, bundle_id: bundleId.trim() || undefined }),
      });
      if (!response.ok) {
        const body = await response.json();
        setErrors(Array.isArray(body.errors) ? body.errors : [{ path: "/", message: `생성 실패 (${response.status})` }]);
        return;
      }
      const raw = response.headers.get("X-Byul-Bundle-Metadata");
      if (!raw) throw Error("묶음 메타데이터가 없습니다");
      const metadata = JSON.parse(atob(raw.replace(/-/g, "+").replace(/_/g, "/"))) as Metadata;
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `${metadata.bundle_id}.tar.gz`;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setResult(metadata);
    } catch (error) {
      setErrors([{ path: "/", message: error instanceof Error ? error.message : "묶음 요청 실패" }]);
    } finally {
      setPending(false);
    }
  }

  return <section className="card card-pad stack">
    <h2>정책 묶음 내보내기</h2>
    <BundleApplyRequest policyId={policyId} revision={policyRevision} environmentId={envId} environmentRevision={envRevision} confirmed={confirmed} override={override} />
    <span className="small muted">CONFIRMED 판본과 환경 판본을 선택합니다. 받은 파일은 서버에서 직접 적용합니다.</span>
    <div className="row wrap">
      <label className="small">정책 판본 <select className="select" aria-label="묶음 정책 판본" value={policyRevision ?? ""} onChange={(e) => setPolicyRevision(Number(e.target.value))}>
        {revisions.map((r) => <option key={r.revision} value={r.revision}>{r.revision} · {r.status}</option>)}
      </select></label>
      <label className="small">환경 <select className="select" aria-label="묶음 환경" value={envId} onChange={(e) => { setEnvId(e.target.value); setEnvRevision(null); }}>
        {(envs.data?.items ?? []).map((r) => <option key={r.doc_id} value={r.doc_id}>{r.doc_id}</option>)}
      </select></label>
      <label className="small">환경 판본 <select className="select" aria-label="묶음 환경 판본" value={envRevision ?? ""} onChange={(e) => setEnvRevision(Number(e.target.value))}>
        {(envRevisions.data?.items ?? []).map((r) => <option key={r.revision} value={r.revision}>{r.revision}</option>)}
      </select></label>
      <label className="small">생성 역할 <select className="select" aria-label="생성 역할" value={role} onChange={(e) => setRole(e.target.value as typeof role)}>
        <option value="runtime_operator">런타임 운영자</option><option value="policy_operator">정책 운영자</option>
      </select></label>
    </div>
    <div className="row wrap">
      <label className="small">묶음 ID (선택) <input className="input" value={bundleId} onChange={(e) => setBundleId(e.target.value)} /></label>
      <label className="small">서버 값 덮어쓰기 JSON (선택) <textarea className="input" rows={3} value={override} onChange={(e) => setOverride(e.target.value)} /></label>
    </div>
    <button type="button" className="btn btn-primary" onClick={generate} disabled={!confirmed || !envId || envRevision == null || pending}>{pending ? "생성 중" : "정책 묶음 내보내기"}</button>
    {!confirmed && <span className="small muted">DRAFT 판본은 승인 후 내보낼 수 있습니다.</span>}
    {errors.length > 0 && <div role="alert" className="small">{errors.map((e, i) => <div key={i}>{e.path}: {e.message}</div>)}</div>}
    {result && <div className="stack small" role="status">
      <strong>묶음 다운로드 완료: {result.bundle_id}</strong>
      <div className="mono">bundle.json SHA-256: {result.bundle_sha256}</div>
      <div className="mono">정책 정규 SHA-256: {result.policy_sha256}</div>
      <div>서버 값 출처: {result.server_values_source === "human_input" ? `사람 입력 (${result.human_input_paths.join(", ")})` : "환경 판본"}</div>
      {result.files.map((f) => <div key={f.role} className="mono">{f.role}: {f.sha256} ({f.bytes} bytes)</div>)}
      <span>압축을 푼 뒤 아래 dry-run 명령을 서버에서 실행하세요.</span>
      <pre style={{ whiteSpace: "pre-wrap" }}>{command}</pre>
      <button type="button" className="btn" onClick={() => navigator.clipboard.writeText(command)}>적용 명령 복사</button>
    </div>}
  </section>;
}
