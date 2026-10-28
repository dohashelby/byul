// 계약 스키마(environment, policy)의 필드와 프레임워크 입력(framework_profile, framework_values)을 구역별 폼으로 나눈다.
// 프레임워크 입력은 선택지 위주다. 값이 없으면 미입력, null이면 '모름'으로 저장한다.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useDocList, useDocRevisions } from "../api/hooks";
import type { EnvironmentDoc, FieldIssue, PolicyDoc } from "../api/types";
import { InputFieldView, KvInput, NumberEntry, Segmented, type PolicyNumber } from "../components/Choice";
import {
  DateTimeField,
  JsonField,
  ListField,
  NumberField,
  SelectField,
  TextField,
  deleteIn,
  getIn,
  issuesAt,
  setIn,
  type Doc,
  type FormCtx,
  FieldMessages,
} from "../components/Form";
import { environmentSnapshotSha256 } from "../lib/canonical";
import {
  ALL_REQUIREMENTS,
  CATALOG_VERSION,
  ENV_CONDITIONS,
  FRAMEWORK_CHECKLIST_SHA256,
  SECURITY_FUNCTIONS,
  visible,
  type EnvCondition,
  type InputField,
  type Requirement,
} from "../lib/frameworkCatalog";
import { DEPLOYMENT_KIND, ENGINE_NAME, FACT_LABEL } from "../lib/labels";
import p from "./pages.module.css";

// 자료 등급(data_origin)은 '값을 얻은 방법'으로 묻는다. 입력 폼에서는 EXAMPLE(예시·시연)을 고르지 않는다.
const ORIGIN_SEG = [
  { value: "DECLARED", label: "직접 입력" },
  { value: "MEASURED", label: "자동 수집" },
];
const ORIGIN_HINT: Record<string, string> = {
  DECLARED: "사람이 확인하고 적거나 설정 파일에서 옮겨 적은 값",
  MEASURED: "도구가 실제 환경에서 읽어 온 값 (예: runtime이 엔진에서 조회)",
  EXAMPLE: "예시·시연용 자료로 저장돼 있습니다. 판정 근거로 쓰지 않습니다. 직접 입력이나 자동 수집으로 바꾸세요.",
};

/** 값을 얻은 방법. 고른 값의 뜻을 아래 한 줄로 보여준다. */
function OriginField({ label, value, onChange }: { label: string; value: string | null | undefined; onChange: (v: string | null) => void }) {
  return (
    <div className={p.qField}>
      <div className={p.qLabel}>{label}</div>
      <Segmented label={label} value={value ?? undefined} onChange={(v) => onChange((v ?? null) as string | null)} options={ORIGIN_SEG} />
      <span className={value === "EXAMPLE" ? p.fieldWarn : "muted small"}>{value ? ORIGIN_HINT[value] : "직접 입력: 사람이 적은 값 · 자동 수집: 도구가 읽어 온 값"}</span>
    </div>
  );
}

export const REGISTERED_FACTS = Object.keys(FACT_LABEL);

/** 사실 키별로 무엇을 묻는지 한 줄 설명. */
const FACT_HINT: Record<string, string> = {
  gateway: "엔진 앞에 Gateway를 두는가",
  prefix_cache: "엔진의 prefix cache를 켰는가",
  prefix_cache_or_shared_cache: "prefix cache 또는 그 밖의 공유 cache를 하나라도 쓰는가 (묶음 전체)",
  shared_or_remote_weights: "공유 daemon·원격 streaming으로 가중치를 받는가",
  dynamic_import: "설정·요청으로 Python 모듈을 동적으로 불러오는가",
  alternate_sources_or_formats_or_derived_cache: "대체 공급원·다른 형식·파생 캐시 중 하나라도 쓰는가 (묶음 전체)",
  remote_acquisition_or_retry: "모델을 원격에서 받거나 취득을 재시도하는가 (묶음 전체)",
  managed_or_external_fallback: "관리형 서비스나 외부 대체 추론을 쓰는가",
  r10_features: "R10(미디어·URL·영속 작업 등) 기능을 쓰는가",
  request_code_revision_selection: "요청으로 코드 판본을 고를 수 있는가",
};

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
      out.push({ path: `/facts/${name}/absence_evidence`, message: "'사용 안 함'으로 두려면 비사용 근거가 필요합니다(없으면 update가 인정하지 않음)" });
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

type Values = Record<string, unknown>;

/** 객체 하나에서 키를 넣거나(값) 지운다(undefined). */
function putKey(obj: Values, key: string, value: unknown): Values {
  const next = { ...obj };
  if (value === undefined) delete next[key];
  else next[key] = value;
  return next;
}

function filled(fields: InputField[], values: Values): [number, number] {
  const shown = fields.filter((f) => visible(f, values));
  return [shown.filter((f) => values[f.key] !== undefined).length, shown.length];
}

function Progress({ done, total }: { done: number; total: number }) {
  if (total === 0) return null;
  return (
    <span className={`${p.progress} ${done === total ? p.progressDone : ""}`}>
      {done}/{total}
    </span>
  );
}

function CheckChips({ checks }: { checks: string[] }) {
  if (!checks.length) return null;
  return (
    <span className={p.tagRow}>
      {checks.map((c) => (
        <span key={c} className={p.tag}>
          {c}
        </span>
      ))}
    </span>
  );
}

function Note({ children, tone }: { children: ReactNode; tone?: "info" | "new" }) {
  return <div className={`${p.noteBox} ${tone === "new" ? p.noteNew : ""}`}>{children}</div>;
}

function ReadOnly({ label, value }: { label: string; value: string }) {
  return (
    <label className={p.field}>
      {label}
      <input className="input" value={value} readOnly />
    </label>
  );
}

// --- 환경 -------------------------------------------------------------------

type Profile = {
  catalog_version: string;
  framework_checklist_sha256: string | null;
  role: string | null;
  profile_status: string | null;
  conditions: Record<string, { values: Values; origin: string | null; source: string | null }>;
  absence_claims: Record<string, Claim>;
};
type Claim = {
  status: "draft" | "reviewed";
  reviewer: string | null;
  reviewed_at: string | null;
  disabled: boolean | null;
  nonexposed: boolean | null;
  references: string[];
  target: { engine: string | null; version: string | null; source_commit: string | null; image_digest: string | null; environment_snapshot_sha256: string | null };
};

const emptyProfile = (): Profile => ({
  catalog_version: CATALOG_VERSION,
  framework_checklist_sha256: FRAMEWORK_CHECKLIST_SHA256,
  role: null,
  profile_status: null,
  conditions: {},
  absence_claims: {},
});
const emptyClaim = (): Claim => ({
  status: "draft",
  reviewer: null,
  reviewed_at: null,
  disabled: null,
  nonexposed: null,
  references: [],
  target: { engine: null, version: null, source_commit: null, image_digest: null, environment_snapshot_sha256: null },
});

function profileOf(doc: EnvironmentDoc): Profile | null {
  const v = (doc as unknown as Values).framework_profile;
  return v && typeof v === "object" ? (v as Profile) : null;
}

// 엔진·GPU 칸에서 고를 수 있는 값. 목록 밖 값도 직접 입력할 수 있다.
const ENGINE_VERSIONS: Record<string, string[]> = {
  vllm: ["0.32.0", "0.31.0", "0.29.0"],
  sglang: ["0.5.22", "0.5.21"],
  "tensorrt-llm": ["0.18.2"],
};
const GPU_MODELS = ["H200", "H100", "A100", "L40S", "L4", "A10G"];

/** 사실(facts)마다 묻는 조건 카드. 사실 하나는 한 카드에서만 묻는다. */
export const CONDITION_FACTS: Record<string, string[]> = {
  entry: ["gateway"],
  users: ["prefix_cache", "prefix_cache_or_shared_cache"],
  supply: ["dynamic_import", "alternate_sources_or_formats_or_derived_cache", "remote_acquisition_or_retry"],
  features: ["r10_features", "request_code_revision_selection"],
  weights: ["shared_or_remote_weights"],
  responsibility: ["managed_or_external_fallback"],
};

type DerivedFact = { cond: string; key: string; from: string; yes: string[]; no: string[] };
/** 조건 답 하나로 정해지는 사실. 따로 묻지 않고 그 답을 고칠 때 함께 바꾼다. 목록에 없는 답은 모름(null)이다. */
export const DERIVED_FACTS: Record<string, DerivedFact> = {
  gateway: { cond: "entry", key: "call_path", from: "호출 경로", yes: ["gateway", "app_gateway"], no: ["direct", "app_direct", "in_process"] },
  shared_or_remote_weights: { cond: "weights", key: "delivery", from: "가중치 공급 방식", yes: ["shared_daemon", "remote_streaming"], no: ["local_file"] },
};

/** 조건 답에서 사실 값. 답이 없으면 undefined(정하지 않음). */
export function deriveFact(rule: DerivedFact, answer: unknown): boolean | null | undefined {
  if (answer === undefined) return undefined;
  if (typeof answer === "string" && rule.yes.includes(answer)) return true;
  if (typeof answer === "string" && rule.no.includes(answer)) return false;
  return null;
}

/** 이 조건 카드에 그리는 계약 필드의 경로. 오류가 있으면 카드를 펼친다. */
const CONDITION_PATHS: Record<string, string[]> = {
  engine: ["/target/engine"],
  supply: ["/target/model"],
  placement: ["/target/runtime"],
  host: ["/deployment"],
};

type Fact = NonNullable<EnvironmentDoc["facts"]>[string];
type CondEntry = Profile["conditions"][string];

const FACT_VALUE_LABEL = (v: unknown) => (v === true ? "사용" : v === false ? "사용 안 함" : v === null ? "모름" : "미입력");

function withProfile(d: EnvironmentDoc, fn: (pr: Profile) => Profile): EnvironmentDoc {
  return { ...d, framework_profile: fn(profileOf(d) ?? emptyProfile()) } as EnvironmentDoc;
}

/** 사실 하나를 넣거나(값) 지운다(undefined). 출처는 그 사실을 묻는 조건 카드의 값을 따른다. '사용 안 함'이 아니면 비사용 근거를 지운다. */
function withFact(d: EnvironmentDoc, name: string, value: unknown, entry: CondEntry | undefined): EnvironmentDoc {
  const prev = d.facts?.[name];
  const facts = { ...(d.facts ?? {}) } as Record<string, Fact>;
  if (value === undefined) delete facts[name];
  else {
    const next = { value, origin: entry?.origin ?? prev?.origin ?? "DECLARED", source: entry?.source ?? prev?.source ?? null } as Fact;
    if (value === false && prev?.absence_evidence != null) next.absence_evidence = prev.absence_evidence;
    facts[name] = next;
  }
  let out = { ...d, facts } as EnvironmentDoc;
  if (value !== false && profileOf(out)?.absence_claims[name]) {
    out = withProfile(out, (pr) => ({ ...pr, absence_claims: putKey(pr.absence_claims, name, undefined) as Profile["absence_claims"] }));
  }
  return out;
}

/** 조건 카드 하나를 고친다. 그 답으로 정해지는 사실과 사실의 출처도 함께 바꾼다. */
function withCondition(d: EnvironmentDoc, key: string, patch: Partial<CondEntry>): EnvironmentDoc {
  const before = profileOf(d)?.conditions[key];
  const cur = before ?? { values: {}, origin: null, source: null };
  const next = { ...cur, ...patch };
  const empty = !Object.keys(next.values).length && next.origin === null && next.source === null;
  let out = withProfile(d, (pr) => ({ ...pr, conditions: putKey(pr.conditions, key, empty ? undefined : next) as Profile["conditions"] }));
  for (const name of CONDITION_FACTS[key] ?? []) {
    const rule = DERIVED_FACTS[name];
    const changed = rule && !Object.is(before?.values[rule.key], next.values[rule.key]);
    if (changed) out = withFact(out, name, deriveFact(rule, next.values[rule.key]), next);
    else if (out.facts?.[name] && (cur.origin !== next.origin || cur.source !== next.source)) out = withFact(out, name, out.facts[name].value, next);
  }
  return out;
}

/** 카드 진행도: 보이는 조건 항목 + 따로 묻는 사실. 조건 답에서 정해지는 사실은 세지 않는다. */
function cardProgress(cond: EnvCondition, doc: EnvironmentDoc, profile: Profile | null): [number, number] {
  const [d, t] = filled(cond.fields, profile?.conditions[cond.key]?.values ?? {});
  const asked = (CONDITION_FACTS[cond.key] ?? []).filter((n) => !DERIVED_FACTS[n]);
  return [d + asked.filter((n) => doc.facts?.[n] !== undefined).length, t + asked.length];
}

type EnvEdit = { ctx: FormCtx; doc: EnvironmentDoc; profile: Profile | null; apply: (fn: (d: EnvironmentDoc) => EnvironmentDoc) => void; snapshot: string | null };

export function EnvironmentForm(props: FormProps<EnvironmentDoc>) {
  const ctx = useCtx(props);
  const { doc, setDoc } = props;
  const profile = profileOf(doc);
  const apply = (fn: (d: EnvironmentDoc) => EnvironmentDoc) => setDoc(fn(doc));
  const setProfile = (fn: (pr: Profile) => Profile) => apply((d) => withProfile(d, fn));
  const [snapshot, setSnapshot] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    environmentSnapshotSha256(doc as unknown as Values).then((h) => live && setSnapshot(h), () => live && setSnapshot(null));
    return () => {
      live = false;
    };
  }, [doc]);

  const condDone = ENV_CONDITIONS.reduce(
    (acc, c) => {
      const [d, t] = cardProgress(c, doc, profile);
      return [acc[0] + d, acc[1] + t];
    },
    [0, 0],
  );
  const edit: EnvEdit = { ctx, doc, profile, apply, snapshot };

  return (
    <div className="stack">
      <nav className={p.toc} aria-label="입력 구역">
        <a href="#env-basic">기본·프로필</a>
        <a href="#env-conditions">
          12개 환경 조건 <Progress done={condDone[0]} total={condDone[1]} />
        </a>
      </nav>

      <section id="env-basic" className="card card-pad stack">
        <h2>기본 정보·프로필</h2>
        <div className="small muted">판본 번호와 생성 시각은 저장할 때 서버가 정합니다.</div>
        <div className={p.formGrid}>
          {props.isNew ? <TextField ctx={ctx} path="/environment_id" label="환경 ID" /> : <ReadOnly label="환경 ID" value={doc.environment_id} />}
          <ReadOnly label="현재 판본" value={String(doc.revision)} />
          <TextField ctx={ctx} path="/target/target_ref" label="대상 ID (target_ref)" hint="runtime 정책의 target_id와 같은 값" />
        </div>
        <div className={p.qGrid}>
          <div className={p.qField}>
            <div className={p.qLabel}>환경 역할</div>
            <Segmented
              label="환경 역할"
              value={profile?.role ?? undefined}
              onChange={(v) => setProfile((pr) => ({ ...pr, role: (v ?? null) as string | null }))}
              options={[
                { value: "current", label: "현재 운영" },
                { value: "candidate", label: "업데이트 후보" },
                { value: "test", label: "시험 환경" },
              ]}
            />
            <span className="muted small">업데이트 평가에서는 현재·후보·시험 환경을 각각 다른 환경 ID로 저장합니다.</span>
          </div>
          <div className={p.qField}>
            <div className={p.qLabel}>값의 확인 상태</div>
            <Segmented
              label="값의 확인 상태"
              value={profile?.profile_status ?? undefined}
              onChange={(v) => setProfile((pr) => ({ ...pr, profile_status: (v ?? null) as string | null }))}
              options={[
                { value: "observed", label: "실제 설치·설정에서 확인" },
                { value: "planned", label: "계획값" },
              ]}
            />
            <span className="muted small">계획값이면 기능 비사용 근거가 있어도 비적용 후보가 되지 않습니다.</span>
          </div>
          <div className={p.qField}>
            <OriginField label="값을 얻은 방법 (문서 전체)" value={doc.data_origin} onChange={(v) => v && ctx.set("/data_origin", v)} />
            <FieldMessages ctx={ctx} path="/data_origin" exact />
          </div>
        </div>
        <FieldMessages ctx={ctx} path="/framework_profile" exact />
      </section>

      <ConditionsSection edit={edit} />
    </div>
  );
}

/** 조건 카드 안에 그리는 계약 필드(target·deployment). 다른 구역에서 다시 묻지 않는다. */
function ContractFields({ cond, ctx, doc }: { cond: string; ctx: FormCtx; doc: EnvironmentDoc }) {
  switch (cond) {
    case "engine": {
      const name = doc.target.engine.name;
      return (
        <div className={p.formGrid}>
          <SelectField ctx={ctx} path="/target/engine/name" label="엔진" options={Object.entries(ENGINE_NAME).map(([value, label]) => ({ value, label }))} />
          <TextField ctx={ctx} path="/target/engine/version" label="버전" nullable suggestions={ENGINE_VERSIONS[name]} placeholder="목록에서 고르거나 입력 (모르면 비워 둠)" />
          <TextField ctx={ctx} path="/target/engine/image_digest" label="이미지 digest" nullable placeholder="sha256:… (모르면 비워 둠)" />
          <TextField ctx={ctx} path="/target/engine/source_commit" label="소스 커밋" nullable placeholder="7~40자리 hex (모르면 비워 둠)" />
        </div>
      );
    }
    case "supply":
      return (
        <div className={p.formGrid}>
          <TextField ctx={ctx} path="/target/model/id" label="모델 ID" nullable />
          <TextField ctx={ctx} path="/target/model/revision" label="모델 판본" nullable />
          <TextField ctx={ctx} path="/target/model/inventory_sha256" label="snapshot 목록 해시" nullable />
          <TextField ctx={ctx} path="/target/model/tokenizer_revision" label="토크나이저 판본" nullable />
        </div>
      );
    case "placement":
      return (
        <div className={p.formGrid}>
          <NumberField ctx={ctx} path="/target/runtime/nodes" label="노드 수" optional />
          <NumberField ctx={ctx} path="/target/runtime/tp_groups" label="TP 그룹 수" optional />
          <NumberField ctx={ctx} path="/target/runtime/gpu_count" label="GPU 수" nullable />
          <TextField ctx={ctx} path="/target/runtime/gpu_model" label="GPU 모델" nullable suggestions={GPU_MODELS} placeholder="목록에서 고르거나 입력 (모르면 비워 둠)" />
          <TextField ctx={ctx} path="/target/runtime/driver" label="드라이버" nullable />
        </div>
      );
    case "host": {
      const deployment = doc.deployment;
      return (
        <div className="stack" style={{ gap: 10 }}>
          <div className={p.qField}>
            <div className={p.qLabel}>배치 종류</div>
            <Segmented
              label="배치 종류"
              value={deployment ? deployment.kind : deployment === null ? null : undefined}
              onChange={(v) => ctx.set("/deployment", v ? { ...(deployment ?? {}), kind: v } : null)}
              options={[...Object.entries(DEPLOYMENT_KIND).map(([k, v]) => ({ value: k, label: v })), { value: null, label: "모름" }]}
            />
            <FieldMessages ctx={ctx} path="/deployment" exact />
          </div>
          {deployment && (
            <div className={p.formGrid}>
              {deployment.kind === "kubernetes" && <TextField ctx={ctx} path="/deployment/kubernetes_context" label="Kubernetes context" nullable />}
              {deployment.kind === "kubernetes" && <TextField ctx={ctx} path="/deployment/namespace" label="namespace" nullable />}
              <NumberField ctx={ctx} path="/deployment/engine_port" label="엔진 포트" nullable />
              <NumberField ctx={ctx} path="/deployment/management_port" label="관리 포트" nullable />
              <LabelsField ctx={ctx} path="/deployment/gateway_labels" label="Gateway labels" />
              <LabelsField ctx={ctx} path="/deployment/engine_labels" label="엔진 labels" />
            </div>
          )}
        </div>
      );
    }
    default:
      return null;
  }
}

/** Kubernetes label 같은 문자열 표. 비우면 필드를 넣지 않는다. */
function LabelsField({ ctx, path, label }: { ctx: FormCtx; path: string; label: string }) {
  return (
    <div className={`${p.qField} ${p.qWide}`}>
      <div className={p.qLabel}>{label}</div>
      <KvInput value={getIn(ctx.doc, path)} onChange={(v) => ctx.set(path, v)} keyLabel="label" valueLabel="값" suggestions={["app", "app.kubernetes.io/name", "app.kubernetes.io/component"]} blank="" />
      <FieldMessages ctx={ctx} path={path} />
    </div>
  );
}

/** 조건 카드 안의 사실 하나. 조건 답에서 정해지면 값만 보여주고, 아니면 여기서 묻는다. */
function FactRow({ name, entry, edit }: { name: string; entry: CondEntry | undefined; edit: EnvEdit }) {
  const { ctx, doc, profile, apply, snapshot } = edit;
  const fact = doc.facts?.[name];
  const base = `/facts/${name}`;
  const rule = DERIVED_FACTS[name];
  const claim = profile?.absence_claims[name];
  const answered = rule ? entry?.values[rule.key] !== undefined : false;
  return (
    <div className={p.factRow}>
      <div className={p.factHead}>
        <div>
          <strong>{FACT_LABEL[name]}</strong> <span className="mono muted small">{name}</span>
          <div className="muted small">
            {rule ? (answered ? `위 '${rule.from}' 답으로 정해집니다.` : fact ? `이전에 입력한 값입니다. 위 '${rule.from}'을 고르면 그 답으로 바뀝니다.` : `위 '${rule.from}'을 고르면 정해집니다.`) : FACT_HINT[name]}
          </div>
        </div>
        {rule ? (
          <span className={p.factDerived}>{FACT_VALUE_LABEL(fact?.value)}</span>
        ) : (
          <Segmented
            label={FACT_LABEL[name]}
            value={fact ? fact.value : undefined}
            onChange={(v) => apply((d) => withFact(d, name, v, entry))}
            options={[
              { value: true, label: "사용" },
              { value: false, label: "사용 안 함" },
              { value: null, label: "모름" },
            ]}
          />
        )}
      </div>
      {fact?.value === false && (
        <>
          <TextField ctx={ctx} path={`${base}/absence_evidence`} label="비사용 근거 요약 (update 전달용)" nullable placeholder="아래 구조화 근거에서 채울 수 있음" />
          <ClaimEditor
            name={name}
            claim={claim}
            doc={doc}
            snapshot={snapshot}
            issues={issuesAt(ctx.issues, `/framework_profile/absence_claims/${name}`)}
            onChange={(next) => apply((d) => withProfile(d, (pr) => ({ ...pr, absence_claims: putKey(pr.absence_claims, name, next) as Profile["absence_claims"] })))}
            onSummary={(text) => ctx.set(`${base}/absence_evidence`, text)}
          />
        </>
      )}
      <FieldMessages ctx={ctx} path={base} exact />
    </div>
  );
}

function ClaimEditor({
  name,
  claim,
  doc,
  snapshot,
  issues,
  onChange,
  onSummary,
}: {
  name: string;
  claim: Claim | undefined;
  doc: EnvironmentDoc;
  snapshot: string | null;
  issues: FieldIssue[];
  onChange: (c: Claim | undefined) => void;
  onSummary: (text: string) => void;
}) {
  if (!claim) {
    return (
      <div className={p.claimBox}>
        <span className="small muted">구조화된 비사용 근거가 없습니다. 비적용 후보로 보려면 근거를 기록하세요.</span>
        <button type="button" className="btn btn-small" style={{ alignSelf: "flex-start" }} onClick={() => onChange(emptyClaim())}>
          비사용 근거 기록
        </button>
      </div>
    );
  }
  const set = (patch: Partial<Claim>) => onChange({ ...claim, ...patch });
  const engine = doc.target.engine;
  const stale = claim.target.environment_snapshot_sha256 != null && snapshot != null && claim.target.environment_snapshot_sha256 !== snapshot;
  const targetDiff =
    claim.target.engine !== null &&
    (claim.target.engine !== engine.name || claim.target.version !== engine.version || claim.target.source_commit !== engine.source_commit || claim.target.image_digest !== engine.image_digest);
  const local = claim.reviewed_at ? toLocal(claim.reviewed_at) : "";
  return (
    <div className={p.claimBox}>
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <strong className="small">비사용 근거</strong>
        <Segmented
          label="근거 상태"
          value={claim.status}
          onChange={(v) => v && set({ status: v as Claim["status"] })}
          options={[
            { value: "draft", label: "작성 중" },
            { value: "reviewed", label: "검토 완료" },
          ]}
        />
      </div>
      <div className={p.qGrid}>
        <label className={p.field}>
          검토자
          <input className="input" value={claim.reviewer ?? ""} onChange={(e) => set({ reviewer: e.target.value || null })} />
        </label>
        <label className={p.field}>
          검토 시각
          <input
            className="input"
            type="datetime-local"
            value={local}
            onChange={(e) => set({ reviewed_at: e.target.value ? new Date(e.target.value).toISOString().replace(/\.\d{3}Z$/, "Z") : null })}
          />
        </label>
        <div className={p.qField}>
          <div className={p.qLabel}>확인한 사실</div>
          <div className={p.checks}>
            <label className={`${p.check} ${claim.disabled ? p.checkOn : ""}`}>
              <input type="checkbox" checked={claim.disabled === true} onChange={(e) => set({ disabled: e.target.checked ? true : null })} />
              기능 비활성
            </label>
            <label className={`${p.check} ${claim.nonexposed ? p.checkOn : ""}`}>
              <input type="checkbox" checked={claim.nonexposed === true} onChange={(e) => set({ nonexposed: e.target.checked ? true : null })} />
              외부 비노출
            </label>
          </div>
        </div>
      </div>
      <InputFieldView
        field={{ key: "references", label: "확인 자료 (유효 설정·등록 경로·소비 경로)", kind: "list", placeholder: "예: deploy/values.yaml#L40" }}
        value={claim.references.length ? claim.references : undefined}
        onChange={(v) => set({ references: Array.isArray(v) ? (v as string[]) : [] })}
      />
      <div className={p.claimTarget}>
        <div className="small">
          <span className="muted">대상 </span>
          <span className="mono">
            {[claim.target.engine, claim.target.version, claim.target.source_commit?.slice(0, 12), claim.target.image_digest?.slice(0, 19)].filter(Boolean).join(" · ") || "미기록"}
          </span>
          <br />
          <span className="muted">환경 snapshot </span>
          <span className="mono">{claim.target.environment_snapshot_sha256 ? `${claim.target.environment_snapshot_sha256.slice(0, 16)}…` : "미기록"}</span>
          {stale && <span className={p.fieldWarn}> · 이 근거를 기록한 뒤 환경 입력이 바뀌었습니다. 다시 검토하세요.</span>}
          {targetDiff && <span className={p.fieldWarn}> · 조건 1의 엔진 값과 다릅니다.</span>}
        </div>
        <div className="row" style={{ gap: 6 }}>
          <button
            type="button"
            className="btn btn-small"
            disabled={!snapshot}
            onClick={() =>
              set({
                target: {
                  engine: engine.name || null,
                  version: engine.version ?? null,
                  source_commit: engine.source_commit ?? null,
                  image_digest: engine.image_digest ?? null,
                  environment_snapshot_sha256: snapshot,
                },
              })
            }
          >
            현재 환경으로 대상 고정
          </button>
          <button
            type="button"
            className="btn btn-small btn-ghost"
            onClick={() => onSummary(`구조화 근거 framework_profile.absence_claims.${name}: ${claim.reviewer ?? "검토자 미기록"}, ${claim.reviewed_at ?? "시각 미기록"}, 자료 ${claim.references.length}건`)}
          >
            요약을 근거 칸에 채우기
          </button>
          <button type="button" className="btn btn-small btn-ghost" onClick={() => onChange(undefined)}>
            근거 삭제
          </button>
        </div>
      </div>
      {issues.map((i, k) => (
        <span key={k} className={p.fieldError}>
          {i.path.replace(`/framework_profile/absence_claims/${name}`, "") || "근거"}: {i.message}
        </span>
      ))}
    </div>
  );
}

function toLocal(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function ConditionsSection({ edit }: { edit: EnvEdit }) {
  const [onlyMissing, setOnlyMissing] = useState(false);
  return (
    <section id="env-conditions" className="card card-pad stack">
      <div className="row wrap" style={{ justifyContent: "space-between" }}>
        <h2>12개 환경 조건</h2>
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
          미입력이 남은 조건만
        </label>
      </div>
      <div className="small muted">
        실제 배포값을 입력합니다(허용 기준은 정책 문서). 엔진·모델·실행 자원·배치 값과 기능 사용 여부(facts)도 해당 조건 안에서 한 번만 묻습니다. 모르는 값은 '모름'으로 두면
        해당 점검이 미정으로 남고 자동 제외되지 않습니다. 기능 '사용 안 함'은 실제 비활성·비노출을 확인한 근거가 있어야 인정되며, 근거가 있어도 비적용 '후보'일 뿐입니다.
      </div>
      {ENV_CONDITIONS.map((cond) => {
        const [done, total] = cardProgress(cond, edit.doc, edit.profile);
        if (onlyMissing && done === total) return null;
        return <ConditionCard key={cond.key} cond={cond} done={done} total={total} edit={edit} />;
      })}
    </section>
  );
}

function ConditionCard({ cond, done, total, edit }: { cond: EnvCondition; done: number; total: number; edit: EnvEdit }) {
  const { ctx, doc, profile, apply } = edit;
  const entry = profile?.conditions[cond.key];
  const values = entry?.values ?? {};
  const update = (patch: Partial<CondEntry>) => apply((d) => withCondition(d, cond.key, patch));
  const path = `/framework_profile/conditions/${cond.key}`;
  const facts = CONDITION_FACTS[cond.key] ?? [];
  const own = issuesAt(ctx.issues, path);
  const related = [...(CONDITION_PATHS[cond.key] ?? []), ...facts.flatMap((n) => [`/facts/${n}`, `/framework_profile/absence_claims/${n}`])];
  const hasError = own.length > 0 || related.some((r) => issuesAt(ctx.issues, r).length > 0);
  // 엔진 이름은 필수라 비어 있으면 처음부터 펼친다. 값을 고른 뒤에도 사용자가 접기 전에는 닫지 않는다.
  const [open, setOpen] = useState(() => hasError || (cond.key === "engine" && !doc.target.engine.name));
  useEffect(() => {
    if (hasError) setOpen(true);
  }, [hasError]);
  return (
    <details className={p.fold} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className={p.foldHead}>
        <span className={p.foldNo}>{cond.no}</span>
        <span className={p.foldTitle}>{cond.title}</span>
        <Progress done={done} total={total} />
      </summary>
      <div className={p.foldBody}>
        {(cond.elsewhere || cond.runtime) && (
          <Note>
            {cond.elsewhere && <div>{cond.elsewhere}</div>}
            {cond.runtime && <div>byul runtime: {cond.runtime}</div>}
          </Note>
        )}
        <ContractFields cond={cond.key} ctx={ctx} doc={doc} />
        <div className={p.qGrid}>
          {cond.fields
            .filter((f) => visible(f, values))
            .map((f) => (
              <InputFieldView
                key={f.key}
                field={f}
                value={values[f.key]}
                error={issuesAt(ctx.issues, `${path}/values/${f.key}`)[0]?.message}
                onChange={(v) => update({ values: putKey(values, f.key, v) })}
              />
            ))}
        </div>
        {facts.length > 0 && (
          <div className={p.factList}>
            {facts.map((name) => (
              <FactRow key={name} name={name} entry={entry} edit={edit} />
            ))}
          </div>
        )}
        <div className={p.qGrid}>
          <OriginField label="값을 얻은 방법" value={entry?.origin} onChange={(v) => update({ origin: v })} />
          <label className={p.field}>
            확인한 자료
            <input className="input" value={entry?.source ?? ""} placeholder="예: helm values, 엔진 기동 인자, 운영 담당 확인" onChange={(e) => update({ source: e.target.value || null })} />
          </label>
        </div>
        <div className="small muted">
          관련 기본 확인 점검: <CheckChips checks={cond.baseChecks} /> {cond.baseChecks.length ? " · " : ""}
          {cond.otherChecks}
        </div>
        {own
          .filter((e) => !cond.fields.some((f) => e.path.startsWith(`${path}/values/${f.key}`)))
          .map((e, i) => (
            <span key={i} className={p.fieldError}>
              {e.path.replace(path, "") || cond.title}: {e.message}
            </span>
          ))}
      </div>
    </details>
  );
}

// --- 정책 -------------------------------------------------------------------

type ReqEntry = { decision: string | null; values: Values; numbers: Record<string, PolicyNumber>; basis: string | null; note: string | null };
type FrameworkValues = {
  catalog_version: string;
  framework_checklist_sha256: string | null;
  requirements: Record<string, ReqEntry>;
  approval_terms: { decision: string | null; scope: string | null; conditions: string | null };
};
const emptyValues = (): FrameworkValues => ({
  catalog_version: CATALOG_VERSION,
  framework_checklist_sha256: FRAMEWORK_CHECKLIST_SHA256,
  requirements: {},
  approval_terms: { decision: null, scope: null, conditions: null },
});
const emptyReq = (): ReqEntry => ({ decision: null, values: {}, numbers: {}, basis: null, note: null });

function valuesOf(doc: PolicyDoc): FrameworkValues | null {
  const v = (doc as unknown as Values).framework_values;
  return v && typeof v === "object" ? (v as FrameworkValues) : null;
}

function reqProgress(req: Requirement, entry: ReqEntry | undefined): [number, number] {
  const [d, t] = filled(req.fields, entry?.values ?? {});
  const nums = req.numbers.filter((x) => entry?.numbers[x.key]).length;
  return [d + nums + (entry?.decision ? 1 : 0), t + req.numbers.length + 1];
}

export function PolicyForm(props: FormProps<PolicyDoc>) {
  const ctx = useCtx(props);
  const { doc, setDoc } = props;
  const envs = useDocList("environments");
  const envId = doc.environment_ref?.id ?? null;
  const envRevs = useDocRevisions("environments", envId);
  const fv = valuesOf(doc);
  const setFv = (fn: (v: FrameworkValues) => FrameworkValues) => setDoc({ ...doc, framework_values: fn(fv ?? emptyValues()) } as PolicyDoc);
  const [filter, setFilter] = useState<"all" | "undecided" | "measure">("all");

  const totals = useMemo(
    () =>
      ALL_REQUIREMENTS.reduce(
        (acc, r) => {
          const e = fv?.requirements[r.id];
          return { decided: acc.decided + (e?.decision ? 1 : 0), measure: acc.measure + Object.values(e?.numbers ?? {}).filter((x) => x.status === "after_measurement").length };
        },
        { decided: 0, measure: 0 },
      ),
    [fv],
  );

  return (
    <div className="stack">
      <nav className={p.toc} aria-label="입력 구역">
        <a href="#pol-basic">기본·유효 기간</a>
        <a href="#pol-approval">승인 조건</a>
        <a href="#pol-reqs">
          36개 보호 요구 <Progress done={totals.decided} total={ALL_REQUIREMENTS.length} />
        </a>
        <a href="#pol-gateway">Gateway 정책</a>
        <a href="#pol-advanced">고급(JSON)</a>
      </nav>

      <section id="pol-basic" className="card card-pad stack">
        <h2>기본 정보·유효 기간</h2>
        <div className="small muted">상태는 이번 단계에서 항상 초안(DRAFT)입니다. 승인(CONFIRMED 전환)은 다음 단계입니다.</div>
        <div className={p.formGrid}>
          {props.isNew ? <TextField ctx={ctx} path="/policy_id" label="정책 ID" /> : <ReadOnly label="정책 ID" value={doc.policy_id} />}
          <TextField ctx={ctx} path="/policy_version" label="정책 판본" />
          <ReadOnly label="상태" value="DRAFT" />
          <div className={p.qField}>
            <OriginField label="값을 얻은 방법 (문서 전체)" value={doc.data_origin} onChange={(v) => v && ctx.set("/data_origin", v)} />
            <FieldMessages ctx={ctx} path="/data_origin" exact />
          </div>
          <TextField ctx={ctx} path="/target_ref" label="대상 ID (target_ref)" />
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
        </div>
      </section>

      <section id="pol-approval" className="card card-pad stack">
        <h2>승인 조건</h2>
        <div className="small muted">누가·언제 승인했는지(approval)는 CONFIRMED 전환 때 기록합니다. 여기에는 무엇을 어떤 조건으로 승인하는지 적습니다. 서명이 아닙니다.</div>
        <div className={p.qGrid}>
          <div className={p.qField}>
            <div className={p.qLabel}>승인 결정</div>
            <Segmented
              label="승인 결정"
              value={fv?.approval_terms.decision ?? undefined}
              onChange={(v) => setFv((x) => ({ ...x, approval_terms: { ...x.approval_terms, decision: (v ?? null) as string | null } }))}
              options={[
                { value: "adopted", label: "채택" },
                { value: "conditional", label: "조건부 채택" },
                { value: "rejected", label: "거부" },
              ]}
            />
          </div>
          <label className={p.field}>
            승인 범위
            <input
              className="input"
              value={fv?.approval_terms.scope ?? ""}
              placeholder="예: 운영 서비스 A의 현재 환경, 36개 요구 전체"
              onChange={(e) => setFv((x) => ({ ...x, approval_terms: { ...x.approval_terms, scope: e.target.value || null } }))}
            />
          </label>
          {fv?.approval_terms.decision === "conditional" && (
            <label className={p.field} style={{ gridColumn: "1 / -1" }}>
              승인 조건
              <textarea
                className="input"
                rows={2}
                value={fv.approval_terms.conditions ?? ""}
                placeholder="조건부 채택의 조건과 기한"
                onChange={(e) => setFv((x) => ({ ...x, approval_terms: { ...x.approval_terms, conditions: e.target.value || null } }))}
              />
            </label>
          )}
        </div>
        <FieldMessages ctx={ctx} path="/framework_values/approval_terms" />
      </section>

      <section id="pol-reqs" className="card card-pad stack">
        <div className="row wrap" style={{ justifyContent: "space-between" }}>
          <h2>36개 보호 요구별 승인 기준</h2>
          <Segmented
            label="보기"
            value={filter}
            onChange={(v) => setFilter((v ?? "all") as typeof filter)}
            options={[
              { value: "all", label: "전체" },
              { value: "undecided", label: "결정 안 한 요구" },
              { value: "measure", label: "측정 후 결정 수치" },
            ]}
          />
        </div>
        <div className="small muted">
          요구마다 기존 승인 정책을 <b>유지</b>·<b>변경</b>·<b>확인 필요</b> 중에서 고르고, 변경하거나 아직 없는 값만 채웁니다. 수치는 단위·적용 범위와 함께 확정·측정 후
          결정·미정 상태로 남기고, 근거 없는 숫자를 채우지 않습니다. 결정 {totals.decided}/{ALL_REQUIREMENTS.length} · 측정 후 결정 수치 {totals.measure}개
        </div>
        {SECURITY_FUNCTIONS.map((fn) => {
          const reqs = fn.requirements.filter((r) => {
            const e = fv?.requirements[r.id];
            if (filter === "undecided") return !e?.decision;
            if (filter === "measure") return Object.values(e?.numbers ?? {}).some((x) => x.status === "after_measurement");
            return true;
          });
          if (!reqs.length) return null;
          const decided = fn.requirements.filter((r) => fv?.requirements[r.id]?.decision).length;
          return (
            <div key={fn.no} className="stack" style={{ gap: 8 }}>
              <div className={p.fnHead}>
                <span>
                  {fn.no}. {fn.title}
                </span>
                <Progress done={decided} total={fn.requirements.length} />
              </div>
              {reqs.map((r) => (
                <RequirementCard
                  key={r.id}
                  req={r}
                  entry={fv?.requirements[r.id]}
                  ctx={ctx}
                  onChange={(next) => setFv((x) => ({ ...x, requirements: putKey(x.requirements, r.id, next) as FrameworkValues["requirements"] }))}
                />
              ))}
            </div>
          );
        })}
      </section>

      <section id="pol-gateway" className="card card-pad stack">
        <h2>Gateway 정책 (runtime.gateway)</h2>
        <div className="small muted">byul runtime이 요청마다 집행하는 값입니다. 1.2 허용 모델, 3.1 입력·출력·본문 한도와 같은 값이어야 합니다.</div>
        <div className={p.formGrid}>
          <ListField ctx={ctx} path="/runtime/gateway/allowed_models" label="허용 모델" />
          <NumberField ctx={ctx} path="/runtime/gateway/output_default_tokens" label="기본 출력 토큰" />
          <NumberField ctx={ctx} path="/runtime/gateway/output_max_tokens" label="최대 출력 토큰" />
          <NumberField ctx={ctx} path="/runtime/gateway/max_body_bytes" label="최대 본문 바이트" />
          <NumberField ctx={ctx} path="/runtime/gateway/max_context_tokens" label="최대 context 토큰" nullable />
          <TextField ctx={ctx} path="/runtime/gateway/profile" label="profile (선택)" optional placeholder="비우면 필드 없음" />
          <NumberField ctx={ctx} path="/runtime/gateway/max_n" label="max_n (선택)" optional />
        </div>
        <GatewayConsistency doc={doc} fv={fv} />
      </section>

      <details id="pol-advanced" className={`card card-pad ${p.fold}`}>
        <summary className={p.foldHead}>
          <span className={p.foldTitle}>고급 — 검증 팩·집행 의도·exec 예외 (JSON)</span>
        </summary>
        <div className={`${p.foldBody} stack`}>
          <div className="small muted">update POL 고유 필드와 runtime 집행 의도 원문입니다. 예외는 판정을 바꾸지 않고 '예외 승인됨'으로만 표시됩니다.</div>
          <div className={p.formGrid}>
            <TextField ctx={ctx} path="/verification/pack/id" label="팩 ID" />
            <NumberField ctx={ctx} path="/verification/pack/revision" label="팩 판본" />
            <ListField ctx={ctx} path="/verification/scope/include" label="범위 포함" />
            <ListField ctx={ctx} path="/verification/scope/exclude" label="범위 제외" />
            <JsonField ctx={ctx} path="/verification/values" label="점검별 정책 값 (check_id → 값)" rows={6} />
            <JsonField ctx={ctx} path="/verification/exceptions" label="예외 목록" rows={4} />
            <JsonField ctx={ctx} path="/runtime/enforcement" label="집행 의도 (runtime.enforcement)" rows={8} hint='쓰지 않으면 null. 예: {"intent_schema": "runtime-enforcement-intent/1.1", "intent": {...}}' />
            <JsonField ctx={ctx} path="/runtime/exec_exceptions" label="exec 예외" rows={4} />
          </div>
        </div>
      </details>
    </div>
  );
}

/** 승인 기준과 runtime 집행 설정이 어긋나면 알린다. 저장은 막지 않는다. */
function GatewayConsistency({ doc, fv }: { doc: PolicyDoc; fv: FrameworkValues | null }) {
  const out: string[] = [];
  const nums = fv?.requirements["3.1"]?.numbers ?? {};
  const gw = doc.runtime.gateway;
  const maxOut = nums.max_output_tokens;
  if (maxOut?.status === "confirmed" && maxOut.value != null && maxOut.unit === "token" && maxOut.value !== gw.output_max_tokens) {
    out.push(`3.1 출력 길이 한도(${maxOut.value} token)와 최대 출력 토큰(${gw.output_max_tokens})이 다릅니다.`);
  }
  const body = nums.max_body;
  if (body?.status === "confirmed" && body.value != null) {
    const bytes = body.unit === "KiB" ? body.value * 1024 : body.value;
    if (bytes !== gw.max_body_bytes) out.push(`3.1 본문 크기 한도(${body.value} ${body.unit})와 최대 본문 바이트(${gw.max_body_bytes})가 다릅니다.`);
  }
  if (!out.length) return null;
  return (
    <div className="stack" style={{ gap: 4 }}>
      {out.map((m) => (
        <span key={m} className={p.fieldWarn}>
          {m}
        </span>
      ))}
    </div>
  );
}

function RequirementCard({ req, entry, ctx, onChange }: { req: Requirement; entry: ReqEntry | undefined; ctx: FormCtx; onChange: (e: ReqEntry | undefined) => void }) {
  const cur = entry ?? emptyReq();
  const update = (patch: Partial<ReqEntry>) => {
    const next = { ...cur, ...patch };
    const empty = next.decision === null && !Object.keys(next.values).length && !Object.keys(next.numbers).length && next.basis === null && next.note === null;
    onChange(empty ? undefined : next);
  };
  const [done, total] = reqProgress(req, entry);
  const path = `/framework_values/requirements/${req.id}`;
  const errs = issuesAt(ctx.issues, path);
  return (
    <details className={p.fold} open={errs.length > 0 || undefined}>
      <summary className={p.foldHead}>
        <span className={p.foldNo}>{req.id}</span>
        <span className={p.foldTitle}>{req.title}</span>
        {entry?.decision && <span className={p.decision}>{DECISION_LABEL[entry.decision]}</span>}
        {req.newBranch && <span className={p.newTag}>새 분기</span>}
        <Progress done={done} total={total} />
      </summary>
      <div className={p.foldBody}>
        <div className={p.qField}>
          <div className={p.qLabel}>기존 승인 정책 처리</div>
          <Segmented
            label="기존 승인 정책 처리"
            value={cur.decision ?? undefined}
            onChange={(v) => update({ decision: (v ?? null) as string | null })}
            options={[
              { value: "keep", label: "유지" },
              { value: "change", label: "변경" },
              { value: "needs_confirmation", label: "확인 필요" },
            ]}
          />
        </div>
        {(req.see || req.gateway || req.newBranch) && (
          <Note tone={req.newBranch ? "new" : "info"}>
            {req.newBranch && <div>{req.newBranch}</div>}
            {req.see && <div>{req.see}</div>}
            {req.gateway && <div>{req.gateway}</div>}
          </Note>
        )}
        {req.fields.length > 0 && (
          <div className={p.qGrid}>
            {req.fields
              .filter((f) => visible(f, cur.values))
              .map((f) => (
                <InputFieldView
                  key={f.key}
                  field={f}
                  value={cur.values[f.key]}
                  error={issuesAt(ctx.issues, `${path}/values/${f.key}`)[0]?.message}
                  onChange={(v) => update({ values: putKey(cur.values, f.key, v) })}
                />
              ))}
          </div>
        )}
        {req.numbers.length > 0 && (
          <div className="stack" style={{ gap: 8 }}>
            <div className={p.qLabel}>수치·기한</div>
            {req.numbers.map((d) => (
              <NumberEntry
                key={d.key}
                def={d}
                value={cur.numbers[d.key]}
                onChange={(v) => update({ numbers: putKey(cur.numbers, d.key, v) as Record<string, PolicyNumber> })}
              />
            ))}
          </div>
        )}
        <div className={p.qGrid}>
          <label className={p.field}>
            근거 (기존 승인 기록·문서 위치)
            <input className="input" value={cur.basis ?? ""} placeholder="예: 보안정책 v3 §4.2" onChange={(e) => update({ basis: e.target.value || null })} />
          </label>
          <label className={p.field}>
            메모
            <input className="input" value={cur.note ?? ""} onChange={(e) => update({ note: e.target.value || null })} />
          </label>
        </div>
        <div className="small muted">
          관련 점검: <CheckChips checks={req.checks} />
        </div>
        {errs
          .filter((e) => !e.path.startsWith(`${path}/values/`))
          .map((e, i) => (
            <span key={i} className={p.fieldError}>
              {e.path.replace(path, "") || req.title}: {e.message}
            </span>
          ))}
      </div>
    </details>
  );
}

const DECISION_LABEL: Record<string, string> = { keep: "유지", change: "변경", needs_confirmation: "확인 필요" };
