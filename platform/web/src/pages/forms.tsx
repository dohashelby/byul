// 계약 스키마(environment, policy)의 필드를 구역별 폼으로 나눈다.
import { useDocList, useDocRevisions } from "../api/hooks";
import type { EnvironmentDoc, FieldIssue, PolicyDoc } from "../api/types";
import {
  DateTimeField,
  JsonField,
  ListField,
  NumberField,
  Section,
  SelectField,
  TextField,
  deleteIn,
  getIn,
  setIn,
  type Doc,
  type FormCtx,
  FieldMessages,
} from "../components/Form";
import { DEPLOYMENT_KIND, FACT_LABEL } from "../lib/labels";
import p from "./pages.module.css";

const ORIGINS = [
  { value: "DECLARED", label: "신고 (DECLARED)" },
  { value: "MEASURED", label: "측정 (MEASURED)" },
  { value: "EXAMPLE", label: "예시 (EXAMPLE)" },
];

export const REGISTERED_FACTS = Object.keys(FACT_LABEL);

export function newEnvironment(): EnvironmentDoc {
  return {
    schema_version: "byul-environment/0.1",
    environment_id: "",
    revision: 1,
    created_at: new Date().toISOString(),
    data_origin: "DECLARED",
    target: {
      target_ref: "",
      engine: { name: "", version: null, image_digest: null, source_commit: null },
      model: { id: null, revision: null, inventory_sha256: null },
    },
    deployment: null,
    facts: {},
  } as EnvironmentDoc;
}

export function newPolicy(): PolicyDoc {
  return {
    schema_version: "byul-policy/0.1",
    policy_id: "",
    policy_version: "1",
    created_at: new Date().toISOString(),
    data_origin: "DECLARED",
    status: "DRAFT",
    target_ref: "",
    environment_ref: null,
    validity: { valid_from: null, valid_until: null },
    approval: null,
    verification: { pack: { id: "PACK-BASELINE", revision: 1 }, values: {}, scope: { include: [], exclude: [] }, exceptions: [] },
    runtime: {
      gateway: { allowed_models: [], output_default_tokens: 128, output_max_tokens: 128, max_body_bytes: 65536, max_context_tokens: null },
      enforcement: null,
      exec_exceptions: [],
    },
  } as PolicyDoc;
}

/** facts 값을 false로 두면서 비사용 근거가 비어 있으면 경고(계약 원칙). 저장은 막지 않는다. */
export function factWarnings(doc: EnvironmentDoc): FieldIssue[] {
  const out: FieldIssue[] = [];
  for (const [name, fact] of Object.entries(doc.facts ?? {})) {
    if (fact && fact.value === false && !(fact.absence_evidence ?? "").trim()) {
      out.push({ path: `/facts/${name}/absence_evidence`, message: "false로 신고하려면 비사용 근거가 필요합니다(없으면 update가 인정하지 않음)" });
    }
  }
  return out;
}

type FormProps<T> = { doc: T; setDoc: (d: T) => void; issues: FieldIssue[]; warnings: FieldIssue[]; isNew: boolean };

function useCtx<T>({ doc, setDoc, issues, warnings }: FormProps<T>): FormCtx {
  return {
    doc: doc as unknown as Doc,
    set: (path, value) => setDoc((value === undefined ? deleteIn(doc, path) : setIn(doc, path, value)) as T),
    issues,
    warnings,
  };
}

export function EnvironmentForm(props: FormProps<EnvironmentDoc>) {
  const ctx = useCtx(props);
  const { doc } = props;
  const deployment = doc.deployment;
  return (
    <div className="stack">
      <Section title="기본 정보" note="판본 번호와 생성 시각은 저장할 때 서버가 정합니다.">
        {props.isNew ? <TextField ctx={ctx} path="/environment_id" label="환경 ID" /> : <ReadOnly label="환경 ID" value={doc.environment_id} />}
        <ReadOnly label="현재 판본" value={String(doc.revision)} />
        <SelectField ctx={ctx} path="/data_origin" label="자료 등급" options={ORIGINS} />
      </Section>
      <Section title="대상">
        <TextField ctx={ctx} path="/target/target_ref" label="target_ref" hint="runtime 정책의 target_id와 같은 값" />
      </Section>
      <Section title="엔진">
        <TextField ctx={ctx} path="/target/engine/name" label="이름" />
        <TextField ctx={ctx} path="/target/engine/version" label="버전" nullable />
        <TextField ctx={ctx} path="/target/engine/image_digest" label="이미지 digest" nullable placeholder="sha256:… (모르면 비워 둠)" />
        <TextField ctx={ctx} path="/target/engine/source_commit" label="소스 커밋" nullable />
      </Section>
      <Section title="모델">
        <TextField ctx={ctx} path="/target/model/id" label="모델 ID" nullable />
        <TextField ctx={ctx} path="/target/model/revision" label="모델 판본" nullable />
        <TextField ctx={ctx} path="/target/model/inventory_sha256" label="snapshot 목록 해시" nullable />
        <TextField ctx={ctx} path="/target/model/tokenizer_revision" label="토크나이저 판본" nullable />
      </Section>
      <Section title="실행 자원" note="비워 둔 칸은 필드를 넣지 않습니다(GPU 항목은 null).">
        <NumberField ctx={ctx} path="/target/runtime/nodes" label="노드 수" optional />
        <NumberField ctx={ctx} path="/target/runtime/tp_groups" label="TP 그룹 수" optional />
        <NumberField ctx={ctx} path="/target/runtime/gpu_count" label="GPU 수" nullable />
        <TextField ctx={ctx} path="/target/runtime/gpu_model" label="GPU 모델" nullable />
        <TextField ctx={ctx} path="/target/runtime/driver" label="드라이버" nullable />
      </Section>
      <Section title="배치">
        <label className={p.field}>
          배치 정보
          <select
            className="select"
            value={deployment ? deployment.kind : ""}
            onChange={(e) => ctx.set("/deployment", e.target.value ? { ...(deployment ?? {}), kind: e.target.value } : null)}
          >
            <option value="">모름 (null)</option>
            {Object.entries(DEPLOYMENT_KIND).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
          <FieldMessages ctx={ctx} path="/deployment" exact />
        </label>
        {deployment && (
          <>
            <TextField ctx={ctx} path="/deployment/kubernetes_context" label="Kubernetes context" nullable />
            <TextField ctx={ctx} path="/deployment/namespace" label="namespace" nullable />
            <NumberField ctx={ctx} path="/deployment/engine_port" label="엔진 포트" nullable />
            <NumberField ctx={ctx} path="/deployment/management_port" label="관리 포트" nullable />
            <JsonField ctx={ctx} path="/deployment/gateway_labels" label="Gateway labels" rows={3} />
            <JsonField ctx={ctx} path="/deployment/engine_labels" label="엔진 labels" rows={3} />
          </>
        )}
      </Section>
      <FactsSection ctx={ctx} doc={doc} />
    </div>
  );
}

function ReadOnly({ label, value }: { label: string; value: string }) {
  return (
    <label className={p.field}>
      {label}
      <input className="input" value={value} readOnly />
    </label>
  );
}

const VALUE_OPTIONS = [
  { value: "true", label: "사용함 (true)" },
  { value: "false", label: "사용 안 함 (false)" },
  { value: "null", label: "모름 (null)" },
];

function FactsSection({ ctx, doc }: { ctx: FormCtx; doc: EnvironmentDoc }) {
  const facts = doc.facts ?? {};
  return (
    <section className="card card-pad stack">
      <h2>사실 (facts)</h2>
      <div className="small muted">
        update에 등록된 키만 넣습니다. 값마다 출처 등급과 근거를 함께 적습니다. false는 비사용 근거가 있어야 인정됩니다.
      </div>
      {REGISTERED_FACTS.map((name) => {
        const fact = facts[name];
        const base = `/facts/${name}`;
        return (
          <div key={name} className="card card-pad stack" style={{ gap: 8 }}>
            <label className="row">
              <input
                type="checkbox"
                checked={!!fact}
                onChange={(e) => ctx.set(base, e.target.checked ? { value: null, origin: "DECLARED", source: null } : undefined)}
              />
              <strong>{FACT_LABEL[name]}</strong>
              <span className="mono muted small">{name}</span>
            </label>
            {fact && (
              <div className={p.formGrid}>
                <label className={p.field}>
                  값
                  <select
                    className="select"
                    value={fact.value === true ? "true" : fact.value === false ? "false" : fact.value === null ? "null" : "other"}
                    onChange={(e) => ctx.set(`${base}/value`, e.target.value === "true" ? true : e.target.value === "false" ? false : null)}
                  >
                    {VALUE_OPTIONS.map((o) => (
                      <option key={o.value} value={o.value}>
                        {o.label}
                      </option>
                    ))}
                    {typeof fact.value !== "boolean" && fact.value !== null && <option value="other">{JSON.stringify(fact.value)}</option>}
                  </select>
                  <FieldMessages ctx={ctx} path={`${base}/value`} exact />
                </label>
                <SelectField ctx={ctx} path={`${base}/origin`} label="출처 등급" options={ORIGINS} nullable />
                <TextField ctx={ctx} path={`${base}/source`} label="근거 (source)" nullable />
                <TextField ctx={ctx} path={`${base}/absence_evidence`} label="비사용 근거" nullable placeholder={fact.value === false ? "false이면 필요" : "false일 때만 필요"} />
                {Array.isArray(getIn(doc, `${base}/conflict`)) && <JsonField ctx={ctx} path={`${base}/conflict`} label="충돌 후보" rows={3} />}
              </div>
            )}
          </div>
        );
      })}
      <FieldMessages ctx={ctx} path="/facts" exact />
    </section>
  );
}

export function PolicyForm(props: FormProps<PolicyDoc>) {
  const ctx = useCtx(props);
  const { doc } = props;
  const envs = useDocList("environments");
  const envId = doc.environment_ref?.id ?? null;
  const envRevs = useDocRevisions("environments", envId);
  return (
    <div className="stack">
      <Section title="기본 정보·유효 기간" note="상태는 이번 단계에서 항상 초안(DRAFT)입니다. 승인(CONFIRMED 전환)은 다음 단계입니다.">
        {props.isNew ? <TextField ctx={ctx} path="/policy_id" label="정책 ID" /> : <ReadOnly label="정책 ID" value={doc.policy_id} />}
        <TextField ctx={ctx} path="/policy_version" label="policy_version" />
        <ReadOnly label="상태" value="DRAFT" />
        <SelectField ctx={ctx} path="/data_origin" label="자료 등급" options={ORIGINS} />
        <TextField ctx={ctx} path="/target_ref" label="target_ref" />
        <label className={p.field}>
          기준 환경
          <select
            className="select"
            value={envId ?? ""}
            onChange={(e) => {
              const item = envs.data?.items.find((x) => x.doc_id === e.target.value);
              ctx.set("/environment_ref", item ? { id: item.doc_id, revision: item.revision, sha256: item.sha256 } : null);
            }}
          >
            <option value="">없음 (null)</option>
            {envs.data?.items.map((x) => (
              <option key={x.doc_id} value={x.doc_id}>
                {x.doc_id}
              </option>
            ))}
          </select>
          <FieldMessages ctx={ctx} path="/environment_ref" />
        </label>
        {doc.environment_ref && (
          <label className={p.field}>
            환경 판본
            <select
              className="select"
              value={doc.environment_ref.revision}
              onChange={(e) => {
                const r = envRevs.data?.items.find((x) => x.revision === Number(e.target.value));
                if (r) ctx.set("/environment_ref", { id: r.doc_id, revision: r.revision, sha256: r.sha256 });
              }}
            >
              {(envRevs.data?.items ?? [{ revision: doc.environment_ref.revision }]).map((r) => (
                <option key={r.revision} value={r.revision}>
                  판본 {r.revision}
                </option>
              ))}
            </select>
            <span className="muted mono">{doc.environment_ref.sha256.slice(0, 16)}…</span>
          </label>
        )}
        <DateTimeField ctx={ctx} path="/validity/valid_from" label="유효 시작" />
        <DateTimeField ctx={ctx} path="/validity/valid_until" label="유효 끝" />
      </Section>
      <Section title="검증 정책 (verification)" note="update POL 고유 필드와 같습니다. 예외는 판정을 바꾸지 않고 '예외 승인됨'으로만 표시됩니다.">
        <TextField ctx={ctx} path="/verification/pack/id" label="팩 ID" />
        <NumberField ctx={ctx} path="/verification/pack/revision" label="팩 판본" />
        <ListField ctx={ctx} path="/verification/scope/include" label="범위 포함" />
        <ListField ctx={ctx} path="/verification/scope/exclude" label="범위 제외" />
        <JsonField ctx={ctx} path="/verification/values" label="점검별 정책 값 (check_id → 값)" rows={8} />
        <JsonField ctx={ctx} path="/verification/exceptions" label="예외 목록" rows={5} />
      </Section>
      <Section title="Gateway 정책 (runtime.gateway)">
        <ListField ctx={ctx} path="/runtime/gateway/allowed_models" label="허용 모델" />
        <NumberField ctx={ctx} path="/runtime/gateway/output_default_tokens" label="기본 출력 토큰" />
        <NumberField ctx={ctx} path="/runtime/gateway/output_max_tokens" label="최대 출력 토큰" />
        <NumberField ctx={ctx} path="/runtime/gateway/max_body_bytes" label="최대 본문 바이트" />
        <NumberField ctx={ctx} path="/runtime/gateway/max_context_tokens" label="최대 context 토큰" nullable />
        <TextField ctx={ctx} path="/runtime/gateway/profile" label="profile (선택)" optional placeholder="비우면 필드 없음" />
        <NumberField ctx={ctx} path="/runtime/gateway/max_n" label="max_n (선택)" optional />
      </Section>
      <Section title="집행 의도 (runtime.enforcement)" note="runtime-enforcement-intent 문서를 그대로 넣습니다. 쓰지 않으면 null. 내부 형식 검증은 runtime이 합니다.">
        <JsonField ctx={ctx} path="/runtime/enforcement" label="enforcement" rows={10} hint='예: {"intent_schema": "runtime-enforcement-intent/1.1", "intent": {...}}' />
      </Section>
      <Section title="exec 예외 (runtime.exec_exceptions)">
        <JsonField ctx={ctx} path="/runtime/exec_exceptions" label="exec_exceptions" rows={6} />
      </Section>
    </div>
  );
}
