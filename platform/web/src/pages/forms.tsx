// 계약 스키마(environment, policy)의 필드와 프레임워크 입력(framework_profile, framework_values)을 구역별 폼으로 나눈다.
// 프레임워크 입력은 선택지 위주다. 값이 없으면 미입력, null이면 '모름'으로 저장한다.
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useDocList, useDocRevisions, useEnvironmentRevision, usePolicyRevision, useInputCatalog, usePolicyInputPreview } from "../api/hooks";
import type { EnvironmentDoc, FieldIssue, PolicyDoc, InputCatalog, PolicyInputPreview } from "../api/types";
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
  isRequired,
  visible,
  type EnvCondition,
  type InputField,
  type Requirement,
} from "../lib/frameworkCatalog";
import { DEPLOYMENT_KIND, ENGINE_NAME, FACT_LABEL } from "../lib/labels";
import { packFields, versionFieldVisible, versionProfile } from "../lib/inputPreparation";
import p from "./pages.module.css";
import { InputNavigation, PreparationGroup, StateCard } from "../components/InputEditor";
import { formatAnswer, hasConfirmedAnswer, inputIssueGroup, openInputSection } from "../lib/inputUx";

export const REGISTERED_FACTS = Object.keys(FACT_LABEL);

/** 사실 키별로 무엇을 묻는지 한 줄 설명. */
const FACT_HINT: Record<string, string> = {
  gateway: "엔진 앞에 Gateway를 두는가",
  prefix_cache: "엔진의 prefix cache를 켰는가",
  prefix_cache_or_shared_cache: "prefix cache 또는 그 밖의 공유 cache를 하나라도 쓰는가 (묶음 전체)",
  shared_or_remote_weights: "공유 데몬(daemon)·원격 스트리밍으로 가중치를 받는가",
  dynamic_import: "설정·요청으로 Python 모듈을 동적으로 불러오는가",
  alternate_sources_or_formats_or_derived_cache: "대체 공급원·다른 형식·파생 캐시 중 하나라도 쓰는가 (묶음 전체)",
  remote_acquisition_or_retry: "모델을 원격에서 받거나 취득을 재시도하는가 (묶음 전체)",
  managed_or_external_fallback: "관리형 서비스나 외부 대체 추론을 쓰는가",
  r10_features: "미디어 입력·URL 가져오기·영속 작업 같은 기능을 하나라도 쓰는가 (묶음 전체)",
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
    facts: Object.fromEntries(Object.entries(FIXED_FACTS).map(([k, f]) => [k, { value: f.value, origin: "DECLARED", source: f.source }])),
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
    verification: { pack: { id: "", revision: 0 }, values: {}, scope: { include: [], exclude: [] }, exceptions: [] },
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

type FormProps<T> = { doc: T; setDoc: (d: T) => void; issues: FieldIssue[]; warnings: FieldIssue[]; isNew: boolean; onLocalIssue?: (path: string, message: string | null) => void };

function useCtx<T>({ doc, setDoc, issues, warnings, onLocalIssue }: FormProps<T>): FormCtx {
  return {
    doc: doc as unknown as Doc,
    set: (path, value) => setDoc((value === undefined ? deleteIn(doc, path) : setIn(doc, path, value)) as T),
    issues,
    warnings,
    reportIssue: onLocalIssue,
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

function filled(fields: InputField[], values: Values, others?: Record<string, Values | undefined>): [number, number] {
  const shown = fields.filter((f) => visible(f, values, others));
  return [shown.filter((f) => hasConfirmedAnswer(values[f.key])).length, shown.length];
}

function Progress({ done, total }: { done: number; total: number }) {
  if (total === 0) return null;
  return (
    <span className={`${p.progress} ${done === total ? p.progressDone : ""}`} aria-label={`입력 ${done}/${total}`}>
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

function Note({ children }: { children: ReactNode }) {
  return <div className={p.noteBox}>{children}</div>;
}

function ReadOnly({ label, value }: { label: string; value: string }) {
  return (
    <label className={`${p.field} ${p.readOnly}`}>
      <span className={p.readOnlyLabel}>{label}<small aria-hidden="true">읽기 전용</small></span>
      <input className="input" aria-label={label} value={value} readOnly />
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

/** 제품 전제로 정해진 사실. 묻지 않고 항상 이 값으로 저장한다. */
export const FIXED_FACTS: Record<string, { value: boolean; source: string; note: string }> = {
  gateway: { value: true, source: "byul runtime Gateway 전제", note: "byul runtime을 Gateway로 쓴다고 봅니다." },
};

type DerivedFact = { cond: string; key: string; from: string; yes: string[]; no: string[] };
/** 조건 답 하나로 정해지는 사실. 따로 묻지 않고 그 답을 고칠 때 함께 바꾼다. 목록에 없는 답은 모름(null)이다. */
export const DERIVED_FACTS: Record<string, DerivedFact> = {};

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

/** 조건 카드별 답. 다른 카드의 답을 보고 나타나는 질문에 쓴다. */
function conditionValues(profile: Profile | null): Record<string, Values | undefined> {
  return Object.fromEntries(Object.entries(profile?.conditions ?? {}).map(([k, v]) => [k, v.values]));
}

/**
 * 사실 하나를 넣거나(값) 지운다(undefined). 화면에서 고른 값이라 출처는 직접 입력(예시 문서면 예시)이다.
 * update는 source가 없는 사실을 모름으로 보므로 어디서 입력했는지 항상 남긴다. '사용 안 함'이 아니면 비사용 근거를 지운다.
 */
function withFact(d: EnvironmentDoc, name: string, value: unknown, source = `byul platform 환경 입력 (${FACT_LABEL[name]})`): EnvironmentDoc {
  const prev = d.facts?.[name];
  const facts = { ...(d.facts ?? {}) } as Record<string, Fact>;
  if (value === undefined) delete facts[name];
  else {
    const next = { value, origin: d.data_origin === "EXAMPLE" ? "EXAMPLE" : "DECLARED", source } as Fact;
    if (value === false && prev?.absence_evidence != null) next.absence_evidence = prev.absence_evidence;
    facts[name] = next;
  }
  let out = { ...d, facts } as EnvironmentDoc;
  if (value !== false && profileOf(out)?.absence_claims[name]) {
    out = withProfile(out, (pr) => ({ ...pr, absence_claims: putKey(pr.absence_claims, name, undefined) as Profile["absence_claims"] }));
  }
  return out;
}

/** 조건 카드 하나의 답을 고친다. 그 답으로 정해지는 사실도 함께 바꾼다. */
function withCondition(d: EnvironmentDoc, key: string, values: Values): EnvironmentDoc {
  const before = profileOf(d)?.conditions[key];
  // 이전 판본에 저장된 카드 출처는 지우지 않는다(화면에서는 더 묻지 않는다).
  const next = { origin: null, source: null, ...before, values };
  const empty = !Object.keys(values).length && next.origin === null && next.source === null;
  let out = withProfile(d, (pr) => ({ ...pr, conditions: putKey(pr.conditions, key, empty ? undefined : next) as Profile["conditions"] }));
  if (key === "placement") {
    for (const runtimeKey of ["tp_size", "pp_size"] as const) {
      if (Object.is(before?.values[runtimeKey], values[runtimeKey])) continue;
      if (values[runtimeKey] !== undefined && values[runtimeKey] !== null) out = setIn(out, `/target/runtime/${runtimeKey}`, values[runtimeKey]);
      else if (out.target.runtime) out = deleteIn(out, `/target/runtime/${runtimeKey}`) as EnvironmentDoc;
    }
  }
  for (const name of CONDITION_FACTS[key] ?? []) {
    const rule = DERIVED_FACTS[name];
    const changed = rule && !Object.is(before?.values[rule.key], next.values[rule.key]);
    if (changed) out = withFact(out, name, deriveFact(rule, next.values[rule.key]), `byul platform 환경 입력 (${rule.from} 답)`);
  }
  return out;
}

/** update에 넘길 비사용 근거 요약. 확인한 사실과 확인 자료가 없으면 근거로 보지 않는다(null). */
export function claimSummary(name: string, claim: Claim): string | null {
  const found = [claim.disabled && "기능 비활성", claim.nonexposed && "외부 비노출"].filter(Boolean);
  if (!found.length || !claim.references.length) return null;
  return [
    `${claim.status === "reviewed" ? "검토 완료" : "작성 중"} 근거 framework_profile.absence_claims.${name}`,
    `확인: ${found.join("·")}`,
    `자료: ${claim.references.join(", ")}`,
    `검토: ${claim.reviewer ?? "검토자 미기록"}, ${claim.reviewed_at ?? "시각 미기록"}`,
  ].join(" / ");
}

/** 구조화된 비사용 근거를 고치고, 사실의 근거 요약을 그 내용으로 다시 만든다. */
function withClaim(d: EnvironmentDoc, name: string, claim: Claim | undefined): EnvironmentDoc {
  const out = withProfile(d, (pr) => ({ ...pr, absence_claims: putKey(pr.absence_claims, name, claim) as Profile["absence_claims"] }));
  const fact = out.facts?.[name];
  if (!fact) return out;
  return { ...out, facts: { ...out.facts, [name]: { ...fact, absence_evidence: claim ? claimSummary(name, claim) : null } } } as EnvironmentDoc;
}

/**
 * 화면에서 고친 문서를 저장 형식에 맞춘다. 사람이 고친 값이므로 자동 수집(MEASURED) 문서도 직접 입력(DECLARED)이 된다.
 * 전제로 정해진 사실은 항상 그 값으로 둔다.
 */
function normalizeEnv(d: EnvironmentDoc): EnvironmentDoc {
  let out = d.data_origin === "MEASURED" ? { ...d, data_origin: "DECLARED" as const } : d;
  for (const [name, f] of Object.entries(FIXED_FACTS)) {
    const cur = out.facts?.[name];
    if (cur?.value !== f.value || cur.source !== f.source) out = withFact(out, name, f.value, f.source);
  }
  // 이전 화면에서 source 없이 저장한 사실은 update가 모름으로 보므로 출처를 채운다.
  for (const [name, fact] of Object.entries(out.facts ?? {})) {
    if (fact && !fact.source) out = { ...out, facts: { ...out.facts, [name]: { ...fact, source: `byul platform 환경 입력 (${FACT_LABEL[name] ?? name})` } } } as EnvironmentDoc;
  }
  return out;
}

/** 카드 진행도: 보이는 조건 항목 + 따로 묻는 사실. 조건 답이나 전제로 정해지는 사실은 세지 않는다. */
function cardProgress(cond: EnvCondition, doc: EnvironmentDoc, profile: Profile | null, hidden: string[] = []): [number, number] {
  const [d, t] = filled(cond.fields.filter((f) => f.key !== "echo_logprobs" && versionFieldVisible(hidden, cond.key, f.key)), profile?.conditions[cond.key]?.values ?? {}, conditionValues(profile));
  const asked = (CONDITION_FACTS[cond.key] ?? []).filter((n) => !DERIVED_FACTS[n] && !FIXED_FACTS[n]);
  const contract = cond.key === "engine" ? [doc.target.engine.name || null, doc.target.engine.version, doc.target.engine.image_digest]
    : cond.key === "supply" ? [doc.target.model.id, doc.target.model.revision]
    : cond.key === "placement" ? [doc.target.runtime?.gpu_count, doc.target.runtime?.gpu_model]
    : cond.key === "host" ? [doc.deployment?.kind] : [];
  return [d + asked.filter((n) => doc.facts?.[n]?.value != null).length + contract.filter((v) => v != null).length, t + asked.length + contract.length];
}

type EnvEdit = { ctx: FormCtx; doc: EnvironmentDoc; profile: Profile | null; apply: (fn: (d: EnvironmentDoc) => EnvironmentDoc) => void; snapshot: string | null; hiddenFields: string[]; catalog?: InputCatalog };

export function EnvironmentForm(props: FormProps<EnvironmentDoc>) {
  const setDoc = (d: EnvironmentDoc) => props.setDoc(normalizeEnv(d));
  const ctx = useCtx({ ...props, setDoc });
  const { doc } = props;
  const profile = profileOf(doc);
  const apply = (fn: (d: EnvironmentDoc) => EnvironmentDoc) => setDoc(fn(doc));
  const catalogQuery = useInputCatalog();
  const catalog = catalogQuery.data;
  const selectedProfile = versionProfile(catalog, doc);
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
      const [d, t] = cardProgress(c, doc, profile, selectedProfile?.hidden_fields ?? []);
      return [acc[0] + d, acc[1] + t];
    },
    [0, 0],
  );
  const edit: EnvEdit = { ctx, doc, profile, apply, snapshot, hiddenFields: selectedProfile?.hidden_fields ?? [], catalog };

  return (
    <div className={`${p.inputForm} stack`}>
      <nav className={p.toc} aria-label="입력 구역">
        <a href="#env-basic">기본·프로필</a>
        <a href="#env-conditions">
          12개 환경 조건 <Progress done={condDone[0]} total={condDone[1]} />
        </a>
      </nav>

      <div className={p.formOverview}><strong>확정한 세부 입력 <Progress done={condDone[0]} total={condDone[1]} /></strong><span>확인 필요는 미완료 · 실행 준비와 별도</span></div>

      <section id="env-basic" className="card card-pad stack">
        <h2>기본 정보·프로필</h2>
        <div className="small muted">
          판본 번호와 생성 시각은 저장할 때 서버가 정합니다. <span className={p.req}>표시</span>는 필수 입력입니다.
        </div>
        <div className={p.formGrid}>
          {props.isNew ? <TextField ctx={ctx} path="/environment_id" label="환경 ID" required /> : <ReadOnly label="환경 ID" value={doc.environment_id} />}
          <ReadOnly label="현재 판본" value={String(doc.revision)} />
          <TextField ctx={ctx} path="/target/target_ref" label="대상 ID (target_ref)" hint="runtime 정책의 target_id와 같은 값" required />
        </div>
        <Note>기준·후보 역할은 비교 실행 설정에서 선택합니다. 기존 입력 상태와 출처는 그대로 보존합니다.</Note>
        <div className="small muted" role="status">
          {selectedProfile ? `판본 지원 프로파일: ${selectedProfile.profiles.join(", ")}. 등록된 기능 범위만 확인되며 모든 시험 지원을 뜻하지 않습니다.` : "판본별 지원 정보 미확인 — 공통 필드를 유지합니다."}
          {catalogQuery.isError && " 지원 정보를 불러오지 못했습니다."}
        </div>
        {doc.data_origin === "EXAMPLE" && <Note>예시·시연용 자료로 저장돼 있습니다. 판정 근거로 쓰지 않습니다.</Note>}
        <FieldMessages ctx={ctx} path="/framework_profile" exact />
      </section>

      <InputNavigation label="환경 구역 바로가기" items={ENV_CONDITIONS.map((c) => {
        const [done, total] = cardProgress(c, doc, profile, selectedProfile?.hidden_fields ?? []);
        return { id: `env-${c.key}`, title: c.title, state: `${done}/${total}` };
      })} />
      <ConditionsSection edit={edit} />
    </div>
  );
}

/** 조건 카드 안에 그리는 계약 필드(target·deployment). 다른 구역에서 다시 묻지 않는다. */
function ContractFields({ cond, ctx, doc, catalog }: { cond: string; ctx: FormCtx; doc: EnvironmentDoc; catalog?: InputCatalog }) {
  switch (cond) {
    case "engine": {
      const name = doc.target.engine.name;
      return (
        <div className={p.formGrid}>
          <SelectField ctx={ctx} path="/target/engine/name" label="엔진" options={Object.entries(ENGINE_NAME).map(([value, label]) => ({ value, label }))} required />
          <SelectField ctx={ctx} path="/target/engine/runtime_kind" label="런타임 엔진 종류" options={["vllm", "sglang", "mock"].map((value) => ({ value, label: value }))} />
          <TextField ctx={ctx} path="/target/engine/version" label="버전" nullable required suggestions={catalog?.versions.filter((v) => v.engine === name).map((v) => v.version)} placeholder="목록에서 고르거나 입력 (모르면 비워 둠)" />
          <TextField ctx={ctx} path="/target/engine/image_digest" label="이미지 digest" nullable required placeholder="sha256:… (모르면 비워 둠)" />
          <TextField ctx={ctx} path="/target/engine/source_commit" label="소스 커밋" nullable placeholder="7~40자리 hex (모르면 비워 둠)" />
        </div>
      );
    }
    case "supply":
      return (
        <div className={p.formGrid}>
          <TextField ctx={ctx} path="/target/model/id" label="API 모델명" hint="요청의 model 값과 일치하는 이름" nullable required />
          <TextField ctx={ctx} path="/target/model/revision" label="모델 판본" nullable required />
          {doc.target.model.inventory_sha256 && <ReadOnly label="snapshot 목록 해시" value={doc.target.model.inventory_sha256} />}
          <TextField ctx={ctx} path="/target/model/tokenizer_revision" label="토크나이저 판본" nullable />
        </div>
      );
    case "placement":
      return (
        <div className={p.formGrid}>
          <NumberField ctx={ctx} path="/target/runtime/nodes" label="노드 수" optional />
          <NumberField ctx={ctx} path="/target/runtime/tp_groups" label="TP 그룹 수" optional />
          <NumberField ctx={ctx} path="/target/runtime/gpu_count" label="GPU 수" nullable required />
          <TextField ctx={ctx} path="/target/runtime/gpu_model" label="GPU 모델" nullable required suggestions={GPU_MODELS} placeholder="목록에서 고르거나 입력 (모르면 비워 둠)" />
          <TextField ctx={ctx} path="/target/runtime/driver" label="드라이버" nullable />
        </div>
      );
    case "host": {
      const deployment = doc.deployment;
      return (
        <div className="stack" style={{ gap: 10 }}>
          <div className={p.qField}>
            <div className={p.qLabel}>
              <span className={p.req}>배치 종류</span>
            </div>
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
              {deployment.kind === "kubernetes" && <TextField ctx={ctx} path="/deployment/engine_container" label="엔진 컨테이너" nullable />}
              <SelectField ctx={ctx} path="/deployment/gateway_schema_version" label="Gateway 정책 스키마" options={["runtime-mvp/1.1", "runtime-mvp/1.2"].map((value) => ({ value, label: value }))} />
              <SelectField ctx={ctx} path="/deployment/gateway_cache_isolation" label="Gateway cache isolation (1.2)" options={["off", "per_principal"].map((value) => ({ value, label: value }))} />
              <NumberField ctx={ctx} path="/deployment/engine_port" label="엔진 포트" nullable />
              <NumberField ctx={ctx} path="/deployment/management_port" label="관리 포트" nullable />
              <LabelsField ctx={ctx} path="/deployment/gateway_labels" label="Gateway labels" />
              <LabelsField ctx={ctx} path="/deployment/engine_labels" label="엔진 labels" />
              <JsonField ctx={ctx} path="/deployment/enforcement_intent" label="환경 집행 intent (정책에 없을 때)" rows={8} hint="runtime-enforcement-intent/1.1 원본 JSON. 모르면 비워 둡니다." />
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
  const fixed = FIXED_FACTS[name];
  const claim = profile?.absence_claims[name];
  const answered = rule ? entry?.values[rule.key] !== undefined : false;
  return (
    <div className={p.factRow}>
      <div className={p.factHead}>
        <div>
          <strong>{FACT_LABEL[name]}</strong> <span className="mono muted small">{name}</span>
          <div className="muted small">
            {fixed ? fixed.note : rule ? (answered ? `위 '${rule.from}' 답으로 정해집니다.` : fact ? `이전에 입력한 값입니다. 위 '${rule.from}'을 고르면 그 답으로 바뀝니다.` : `위 '${rule.from}'을 고르면 정해집니다.`) : FACT_HINT[name]}
          </div>
        </div>
        {fixed || rule ? (
          <span className={p.factDerived}>{FACT_VALUE_LABEL(fixed ? fixed.value : fact?.value)}</span>
        ) : (
          <Segmented
            label={FACT_LABEL[name]}
            value={fact ? fact.value : undefined}
            onChange={(v) => apply((d) => withFact(d, name, v))}
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
          {!claim && fact.absence_evidence && <div className="small">이전에 적은 근거: {fact.absence_evidence}</div>}
          {claim && (
            <div className="small muted">
              {fact.absence_evidence ? `update에 넘길 근거 요약: ${fact.absence_evidence}` : "확인한 사실(기능 비활성·외부 비노출)과 확인 자료를 하나 이상 채우면 근거 요약이 만들어집니다."}
            </div>
          )}
          <ClaimEditor
            name={name}
            claim={claim}
            doc={doc}
            snapshot={snapshot}
            issues={issuesAt(ctx.issues, `/framework_profile/absence_claims/${name}`)}
            onChange={(next) => apply((d) => withClaim(d, name, next))}
          />
        </>
      )}
      <FieldMessages ctx={ctx} path={base} />
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
}: {
  name: string;
  claim: Claim | undefined;
  doc: EnvironmentDoc;
  snapshot: string | null;
  issues: FieldIssue[];
  onChange: (c: Claim | undefined) => void;
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
  const reviewed = claim.status === "reviewed";
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
          <span className={reviewed ? p.req : undefined}>검토자</span>
          <input className="input" value={claim.reviewer ?? ""} onChange={(e) => set({ reviewer: e.target.value || null })} />
        </label>
        <label className={p.field}>
          <span className={reviewed ? p.req : undefined}>검토 시각</span>
          <input
            className="input"
            type="datetime-local"
            value={local}
            onChange={(e) => set({ reviewed_at: e.target.value ? new Date(e.target.value).toISOString().replace(/\.\d{3}Z$/, "Z") : null })}
          />
        </label>
        <div className={p.qField}>
          <div className={p.qLabel}>
            <span className={reviewed ? p.req : undefined}>확인한 사실</span>
          </div>
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
        required={reviewed}
        value={claim.references.length ? claim.references : undefined}
        onChange={(v) => set({ references: Array.isArray(v) ? (v as string[]) : [] })}
      />
      <div className={p.claimTarget}>
        <div className="small">
          <span className={`muted ${reviewed ? p.req : ""}`}>대상</span>{" "}
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
        <div className={p.inputToolbar}>{[false, true].map((open) => <button key={String(open)} type="button" className="btn btn-small" onClick={(e) => e.currentTarget.closest("section")?.querySelectorAll<HTMLDetailsElement>("details[data-editor-card]").forEach((node) => { node.open = open; })}>{open ? "전체 펼치기" : "전체 접기"}</button>)}</div>
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
        const [done, total] = cardProgress(cond, edit.doc, edit.profile, edit.hiddenFields);
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
  const others = conditionValues(profile);
  const update = (next: Values) => apply((d) => withCondition(d, cond.key, next));
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
    <details id={`env-${cond.key}`} data-editor-card data-field-path={path} className={p.fold} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className={p.foldHead}>
        <span className={p.foldNo}>{cond.no}</span>
        <span className={p.foldTitle}>{cond.title}<small className={p.foldSummary}>{cond.key === "engine" ? `${doc.target.engine.name || "엔진 미선택"} · ${doc.target.engine.version || "판본 미확인"}` : cond.key === "supply" ? doc.target.model.id || "모델 미입력" : cond.fields.filter((f) => values[f.key] !== undefined && f.key !== "echo_logprobs").slice(0, 2).map((f) => `${f.label}: ${formatAnswer(values[f.key])}`).join(" · ") || "아직 작성한 세부 입력이 없습니다"}</small></span>
        <Progress done={done} total={total} />
      </summary>
      <div className={p.foldBody}>
        {(cond.elsewhere || cond.runtime) && (
          <details className={p.inlineHelp}><summary>기존 설정·확인 자료 안내</summary><Note>
            {cond.elsewhere && <div>{cond.elsewhere}</div>}
            {cond.runtime && <div>byul runtime: {cond.runtime}</div>}
          </Note></details>
        )}
        <ContractFields cond={cond.key} ctx={ctx} doc={doc} catalog={edit.catalog} />
        {cond.key === "features" && values.echo_logprobs !== undefined && <Note>이전 입력 반환·logprobs 통합 답변: {FACT_VALUE_LABEL(values.echo_logprobs)}. 원본만 보존하며 세 기능의 개별 답변으로 추정하지 않습니다.</Note>}
        <div className={p.qGrid}>
          {cond.fields
            .filter((f) => f.key !== "echo_logprobs" && visible(f, values, others) && (versionFieldVisible(edit.hiddenFields, cond.key, f.key) || issuesAt(ctx.issues, `${path}/values/${f.key}`).length > 0))
            .map((f) => (
              <InputFieldView
                key={f.key}
                field={f}
                path={`${path}/values/${f.key}`}
                value={values[f.key]}
                required={isRequired(f, values, others)}
                error={issuesAt(ctx.issues, `${path}/values/${f.key}`)[0]?.message}
                onChange={(v) => update(putKey(values, f.key, v))}
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

function PolicyPreparation({ catalog, preview, failed, pending, onRetry }: { catalog?: InputCatalog; preview?: PolicyInputPreview; failed: boolean; pending: boolean; onRetry: () => void }) {
  const issues = [...(preview?.errors ?? []), ...(preview?.gaps ?? []), ...(preview?.warnings ?? [])];
  const customer = issues.filter((issue) => inputIssueGroup(issue) === "customer");
  const system = issues.filter((issue) => inputIssueGroup(issue) === "system");
  const connection = issues.filter((issue) => inputIssueGroup(issue) === "connection");
  return <section id="pol-preparation" className="card card-pad stack" aria-label="입력 연결·준비 상태" data-field-path="/verification/pack">
    <div className={p.preparationHeading}><h2>입력 연결·준비 상태</h2><span className={p.preparationStatus}>{failed ? "조회 실패" : catalog?.available ? "실행 준비는 별도 확인" : "Pack 준비 확인 필요"}</span></div>
    <div className="small muted" role="status">{!catalog?.available ? catalog?.reason ?? "Pack 준비 상태 미확인" : pending ? "현재 입력 확인 중" : "Pack 정의를 확인했습니다. 입력 완료와 실제 시험 준비·판정은 별도입니다."}</div>
    {failed && <div className="row wrap"><span className={p.fieldWarn}>입력 연결 정보를 불러오지 못했습니다. 작성한 값은 유지됩니다.</span><button type="button" className="btn btn-small" onClick={onRetry}>연결 정보 다시 확인</button></div>}
    <div className={p.stateGrid}>
      <StateCard label="01 · 정책 요구값" title={preview ? `${preview.connected.length}개 카드값 연결` : "입력값 확인 중"}>
        {preview ? <details className={p.inlineHelp}><summary>실행에 전달할 기준 보기</summary><pre>{JSON.stringify(preview.document.verification.values, null, 2)}</pre></details> : "입력 형식 확인 후 연결값을 표시합니다."}
      </StateCard>
      <StateCard label="02 · 현재 적용 설정" title="실제 설정 미확인">이 화면에서 실제 적용 설정을 확인하지 않았습니다.</StateCard>
      <StateCard label="03 · 시험 결과" title="연결된 결과 없음">이 입력에 연결된 시험 결과가 없습니다.</StateCard>
    </div>
    <PreparationGroup title="입력에서 확인할 내용" note="정책 기준이나 연결할 환경을 확인하세요. 저장 가능한 초안과 실행 준비 상태는 구분됩니다." issues={customer} catalog={catalog} />
    <PreparationGroup title="시스템 준비·수집에서 확보할 내용" note="해시·세션·prepared 등은 시험 준비 과정에서 확보합니다. 고객이 직접 만들어 입력할 항목이 아닙니다." issues={system} catalog={catalog} />
    <PreparationGroup title="지원 범위·미연결 답변" note="지원되지 않거나 아직 연결되지 않은 답변은 원본을 보존합니다. 비사용·통과로 바꾸지 않습니다." issues={connection} catalog={catalog} />
    {!!catalog?.limitations.length && <details className={p.inlineHelp}><summary>현재 준비 범위와 남은 의존성</summary>{catalog.limitations.map((message) => <p key={message} className="small muted">{message}</p>)}</details>}
  </section>;
}

/** 구체적인 질문은 '변경'을 고를 때 나타난다. 이미 저장된 값이 있으면 숨기지 않는다. */
function showDetail(entry: ReqEntry | undefined): boolean {
  return entry?.decision === "change" || Object.keys(entry?.values ?? {}).length > 0 || Object.keys(entry?.numbers ?? {}).length > 0;
}

/** 지금 답에서 보일 수치 칸. 이미 저장된 수치는 숨기지 않는다. */
function shownNumbers(req: Requirement, values: Values, numbers: Record<string, PolicyNumber>, others: Record<string, Values | undefined>) {
  return req.numbers.filter((d) => visible(d, values, others) || numbers[d.key]);
}

export function decisionComplete(entry: ReqEntry | undefined): boolean {
  if (entry?.decision === "change") return true;
  return entry?.decision === "keep" && (Object.values(entry.values).some((v) => v != null) || Object.values(entry.numbers).some((n) => n.status === "confirmed" && n.value != null));
}

function reqProgress(req: Requirement, entry: ReqEntry | undefined, others: Record<string, Values | undefined>, catalog?: InputCatalog, doc?: PolicyDoc): [number, number] {
  if (!showDetail(entry)) return [decisionComplete(entry) ? 1 : 0, 1];
  const [d, t] = filled([...req.fields, ...packFields(catalog, req.id, req.fields)], entry?.values ?? {}, others);
  const shown = shownNumbers(req, entry?.values ?? {}, entry?.numbers ?? {}, others);
  const nums = shown.filter((x) => entry?.numbers[x.key]?.status === "confirmed" && entry?.numbers[x.key]?.value != null).length;
  const gateway = doc && req.id === "1.2" ? [doc.runtime.gateway.allowed_models.length > 0] : doc && req.id === "3.1" ? [doc.runtime.gateway.output_max_tokens != null] : [];
  return [d + nums + (decisionComplete(entry) ? 1 : 0) + gateway.filter(Boolean).length, t + shown.length + 1 + gateway.length];
}

/** 저장할 때마다 정책 판본을 올린다. 끝의 숫자를 올리고, 숫자가 없으면 붙인다. */
export function nextPolicyVersion(version: string): string {
  const m = /^(.*?)(\d+)$/.exec(version);
  return m ? `${m[1]}${Number(m[2]) + 1}` : `${version}-2`;
}

/** 사람이 고친 문서는 직접 입력(DECLARED)이다. 예시 문서는 예시로 둔다. */
function normalizePolicy(d: PolicyDoc): PolicyDoc {
  return d.data_origin === "MEASURED" ? { ...d, data_origin: "DECLARED" } : d;
}

// runtime 정책 값은 해당 보호 요구 카드에서 받는다. 오류가 있으면 그 카드를 펼친다.
const GATEWAY_PATHS: Record<string, string[]> = {
  "1.2": ["allowed_models"],
  "3.1": ["output_default_tokens", "output_max_tokens", "max_body_bytes", "max_context_tokens", "max_n", "extra_fields"],
};

function GatewayFields({ req, ctx }: { req: string; ctx: FormCtx }) {
  if (req === "1.2") {
    return (
      <div className={p.formGrid}>
        <ListField ctx={ctx} path="/runtime/gateway/allowed_models" label="허용 모델" required />
      </div>
    );
  }
  if (req !== "3.1") return null;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className={p.formGrid}>
        <NumberField ctx={ctx} path="/runtime/gateway/output_default_tokens" label="기본 출력 토큰" />
        <NumberField ctx={ctx} path="/runtime/gateway/output_max_tokens" label="최대 출력 토큰" required />
        <NumberField ctx={ctx} path="/runtime/gateway/max_body_bytes" label="최대 본문 바이트" />
        <NumberField ctx={ctx} path="/runtime/gateway/max_context_tokens" label="최대 문맥 토큰 (입력+출력)" nullable />
        <NumberField ctx={ctx} path="/runtime/gateway/max_n" label="max_n (선택)" optional />
      </div>
      <ExtraFieldsEditor ctx={ctx} />
      <FieldMessages ctx={ctx} path="/runtime/gateway" exact />
    </div>
  );
}

type ExtraRule = Record<string, unknown>;
const EXTRA_TYPES = [
  { value: "number", label: "실수" },
  { value: "integer", label: "정수" },
  { value: "boolean", label: "참·거짓" },
  { value: "string", label: "문자열" },
  { value: "array", label: "문자열 목록" },
];
// runtime이 항상 직접 검증하는 필드라 추가로 열 수 없다(RESERVED_REQUEST_FIELDS).
const RESERVED_FIELDS = ["model", "messages", "max_tokens", "stream", "n", "max_completion_tokens"];

/** 기본 profile에서 runtime이 요구하는 범위가 빠졌으면 그 이름을 돌려준다. */
function missingBounds(rule: ExtraRule): string | null {
  const unset = (...keys: string[]) => keys.filter((k) => typeof rule[k] !== "number");
  const items = rule.items as ExtraRule | undefined;
  const missing =
    rule.type === "number" || rule.type === "integer"
      ? unset("min", "max")
      : rule.type === "string"
        ? unset("max_length")
        : rule.type === "array"
          ? [...unset("max_items"), ...(typeof items?.max_length === "number" ? [] : ["항목 최대 길이"])]
          : [];
  return missing.length ? missing.join("·") : null;
}

function isEditableRule(rule: ExtraRule): boolean {
  if (rule.mode !== "validate") return false;
  if (rule.type === "array") return (rule.items as ExtraRule | undefined)?.type === "string";
  return EXTRA_TYPES.some((t) => t.value === rule.type);
}

function BoundInput({ label, value, onChange }: { label: string; value: unknown; onChange: (v: number | undefined) => void }) {
  return (
    <>
      <span className="small muted">{label}</span>
      <input className="input" type="number" aria-label={label} style={{ maxWidth: 120 }} value={typeof value === "number" ? value : ""} onChange={(e) => onChange(e.target.value === "" ? undefined : Number(e.target.value))} />
    </>
  );
}

/** chat 요청에서 엔진에 더 넘길 필드(runtime.gateway.extra_fields). 기본 profile은 범위가 있는 validate 규칙만 받는다. */
function ExtraFieldsEditor({ ctx }: { ctx: FormCtx }) {
  const path = "/runtime/gateway/extra_fields";
  const rules = (getIn(ctx.doc, path) ?? {}) as Record<string, ExtraRule>;
  const [name, setName] = useState("");
  const write = (next: Record<string, ExtraRule>) => ctx.set(path, Object.keys(next).length ? next : undefined);
  const put = (key: string, rule: ExtraRule | undefined) => write(putKey(rules, key, rule) as Record<string, ExtraRule>);
  const trimmed = name.trim();
  const reserved = RESERVED_FIELDS.includes(trimmed);
  return (
    <div className={`${p.qField} ${p.qWide}`}>
      <div className={p.qLabel}>추가 허용 필드</div>
      <span className="muted small">chat 요청에서 엔진에 더 넘길 필드입니다. 비워 두면 기본 필드만 넘깁니다. 객체 필드는 열 수 없습니다.</span>
      {Object.entries(rules).map(([key, rule]) => {
        const editable = isEditableRule(rule);
        const lacking = editable ? missingBounds(rule) : null;
        const set = (k: string, v: unknown) => put(key, putKey(rule, k, v));
        const items = (rule.items ?? {}) as ExtraRule;
        return (
          <div key={key} className={p.ruleRow}>
            <span className="mono">{key}</span>
            {editable ? (
              <>
                <select
                  className="select"
                  aria-label={`${key} 형식`}
                  style={{ maxWidth: 140 }}
                  value={String(rule.type)}
                  onChange={(e) => put(key, e.target.value === "array" ? { mode: "validate", type: "array", items: { type: "string" } } : { mode: "validate", type: e.target.value })}
                >
                  {EXTRA_TYPES.map((t) => (
                    <option key={t.value} value={t.value}>
                      {t.label}
                    </option>
                  ))}
                </select>
                {(rule.type === "number" || rule.type === "integer") && (
                  <>
                    <BoundInput label="최소" value={rule.min} onChange={(v) => set("min", v)} />
                    <BoundInput label="최대" value={rule.max} onChange={(v) => set("max", v)} />
                  </>
                )}
                {rule.type === "string" && <BoundInput label="최대 길이" value={rule.max_length} onChange={(v) => set("max_length", v)} />}
                {rule.type === "array" && (
                  <>
                    <BoundInput label="최대 개수" value={rule.max_items} onChange={(v) => set("max_items", v)} />
                    <BoundInput label="항목 최대 길이" value={items.max_length} onChange={(v) => set("items", putKey(items, "max_length", v))} />
                  </>
                )}
              </>
            ) : (
              <span className="muted small">{JSON.stringify(rule)} (시험용 규칙, 화면에서 고칠 수 없음)</span>
            )}
            <button type="button" className="btn btn-small btn-ghost" onClick={() => put(key, undefined)} aria-label={`${key} 지우기`}>
              삭제
            </button>
            {lacking && <span className={p.fieldWarn}>필요한 범위가 비어 있습니다: {lacking}</span>}
          </div>
        );
      })}
      <div className={p.ruleRow}>
        <input className="input" aria-label="추가할 필드 이름" value={name} placeholder="예: temperature" style={{ maxWidth: 240 }} onChange={(e) => setName(e.target.value)} />
        <button
          type="button"
          className="btn btn-small"
          disabled={!trimmed || reserved || trimmed in rules}
          onClick={() => {
            put(trimmed, { mode: "validate", type: "number" });
            setName("");
          }}
        >
          필드 추가
        </button>
        {reserved && <span className={p.fieldWarn}>runtime이 직접 다루는 필드라 열 수 없습니다.</span>}
      </div>
      <FieldMessages ctx={ctx} path={path} />
    </div>
  );
}

export function PolicyForm(props: FormProps<PolicyDoc>) {
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const setDoc = (d: PolicyDoc) => props.setDoc(normalizePolicy(d));
  const ctx = useCtx({ ...props, setDoc });
  const { doc } = props;
  const envs = useDocList("environments");
  const envId = doc.environment_ref?.id ?? null;
  const envRevs = useDocRevisions("environments", envId);
  const baseEnv = useEnvironmentRevision(envId, doc.environment_ref?.revision ?? null).data?.document;
  const envAnswers = { ...conditionValues(baseEnv ? profileOf(baseEnv) : null), facts: Object.fromEntries(Object.entries(baseEnv?.facts ?? {}).map(([k, f]) => [k, f?.value])) };
  const catalogQuery = useInputCatalog();
  const catalog = catalogQuery.data;
  const preview = usePolicyInputPreview(doc);
  ctx.issues = [...ctx.issues, ...(preview.data?.errors ?? []).filter((e) => !ctx.issues.some((old) => old.path === e.path))];
  const policyRevs = useDocRevisions("policies", props.isNew ? null : doc.policy_id);
  const confirmedRev = policyRevs.data?.items.filter((r) => r.status === "CONFIRMED").at(-1)?.revision ?? null;
  const previous = usePolicyRevision(props.isNew ? null : doc.policy_id, confirmedRev).data?.document;
  const rawFv = valuesOf(doc);
  const priorFv = previous ? valuesOf(previous) : null;
  const fv = rawFv ? { ...rawFv, requirements: Object.fromEntries(Object.entries(rawFv.requirements).map(([id, e]) => {
    if (e.decision !== "keep") return [id, e];
    const direct = Object.fromEntries((catalog?.bindings ?? []).filter((b) => b.requirement === id && b.source === "values" && previous?.verification.values[b.check_id]?.[b.key] !== undefined).map((b) => [b.field, previous!.verification.values[b.check_id][b.key]]));
    const prior = priorFv?.requirements[id] ?? (Object.keys(direct).length ? { ...emptyReq(), values: direct } : undefined);
    return [id, prior ? { ...prior, decision: "keep" } : { ...emptyReq(), decision: "keep" }];
  })) } : null;
  useEffect(() => {
    if (props.isNew && catalog && !doc.verification.pack.id) ctx.set("/verification/pack", catalog.pack);
  }, [catalog, props.isNew, doc.verification.pack.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const setFv = (fn: (v: FrameworkValues) => FrameworkValues) => setDoc({ ...doc, framework_values: fn(fv ?? emptyValues()) } as PolicyDoc);
  const [filter, setFilter] = useState<"all" | "undecided" | "measure">("all");

  // 대상 ID는 기준 환경을 따르고, 허용 모델이 비어 있으면 환경의 모델로 시작한다.
  useEffect(() => {
    if (!baseEnv) return;
    const ref = baseEnv.target.target_ref;
    const model = doc.runtime.gateway.allowed_models.length ? null : baseEnv.target.model.id;
    if (doc.target_ref === ref && !model) return;
    const next = { ...doc, target_ref: ref };
    setDoc(model ? setIn(next, "/runtime/gateway/allowed_models", [model]) : next);
  }, [baseEnv]); // eslint-disable-line react-hooks/exhaustive-deps

  const active = useMemo(() => ALL_REQUIREMENTS.filter((r) => !r.fixed), []);
  const totals = useMemo(
    () =>
      active.reduce(
        (acc, r) => {
          const e = fv?.requirements[r.id];
          const [done, total] = reqProgress(r, e, envAnswers, catalog, doc);
          return { decided: acc.decided + (done === total && decisionComplete(e) ? 1 : 0), measure: acc.measure + Object.values(e?.numbers ?? {}).filter((x) => x.status === "after_measurement").length };
        },
        { decided: 0, measure: 0 },
      ),
    [active, fv, catalog, doc, baseEnv],
  );
  // 근거 칸은 문서에 하나다. 계약의 요구별 basis에 같은 값을 넣어 저장한다.
  const entries = Object.values(fv?.requirements ?? {});
  const basis = entries.find((e) => e.basis)?.basis ?? null;
  // 프레임워크는 변경한 값에 근거를 요구한다.
  const changed = entries.some((e) => e.decision === "change");

  return (
    <div className={`${p.inputForm} stack`}>
      <nav className={p.toc} aria-label="입력 구역">
        <a href="#pol-basic">기본·유효 기간</a>
        <a href="#pol-approval">승인 조건</a>
        <a href="#pol-reqs">
          보호 요구 <Progress done={totals.decided} total={active.length} />
        </a>
        <a href="#pol-advanced" onClick={(event) => { event.preventDefault(); setAdvancedOpen(true); openInputSection("pol-advanced"); }}>고급(JSON)</a>
      </nav>

      <div className={p.formOverview}><strong>입력 확정 {totals.decided}/{active.length}</strong><span>확인 필요·측정 후 결정은 미완료 · 승인·시험과 별도</span></div>

      <section id="pol-basic" className="card card-pad stack">
        <h2>기본 정보·유효 기간</h2>
        <div className="small muted">
          {props.isNew ? "판본은 1부터 시작하고 저장할 때마다 자동으로 올라갑니다." : `현재 판본 ${doc.policy_version}. 저장하면 다음 판본의 초안(DRAFT)이 됩니다.`} <span className={p.req}>표시</span>는 필수 입력입니다.
        </div>
        {doc.data_origin === "EXAMPLE" && <Note>예시·시연용 자료로 저장돼 있습니다. 판정 근거로 쓰지 않습니다.</Note>}
        <div className={p.formGrid}>
          {props.isNew ? <TextField ctx={ctx} path="/policy_id" label="정책 ID" required /> : <ReadOnly label="정책 ID" value={doc.policy_id} />}
          <ReadOnly label="사용 Pack (백엔드 지정)" value={`${doc.verification.pack.id || catalog?.pack.id || "확인 중"}@${doc.verification.pack.id ? doc.verification.pack.revision : catalog?.pack.revision ?? "미확인"}`} />
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
          {doc.environment_ref ? <ReadOnly label="대상 ID (기준 환경)" value={doc.target_ref} /> : <TextField ctx={ctx} path="/target_ref" label="대상 ID (target_ref)" required />}
          {doc.environment_ref && (envRevs.data?.items.length ?? 0) > 1 && (
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
                {envRevs.data?.items.map((r) => (
                  <option key={r.revision} value={r.revision}>
                    판본 {r.revision}
                  </option>
                ))}
              </select>
            </label>
          )}
          <DateTimeField ctx={ctx} path="/validity/valid_from" label="유효 시작" required />
          <DateTimeField ctx={ctx} path="/validity/valid_until" label="유효 끝" />
        </div>
      </section>

      <PolicyPreparation catalog={catalog} preview={preview.data} failed={catalogQuery.isError || preview.isError} pending={preview.isFetching} onRetry={() => { void catalogQuery.refetch(); void preview.refetch(); }} />

      <section id="pol-approval" className="card card-pad stack">
        <h2>승인 조건</h2>
        <div className="small muted">여기에는 무엇을 어떤 조건으로 승인하는지 적습니다. 승인자는 저장한 뒤 아래 '판본 승인'에서 기록합니다. 서명이 아닙니다.</div>
        <div className={p.qGrid}>
          <div className={p.qField}>
            <div className={p.qLabel}>
              <span className={p.req}>승인 결정</span>
            </div>
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
            <span className={p.req}>승인 범위</span>
            <input
              className="input"
              aria-required
              value={fv?.approval_terms.scope ?? ""}
              placeholder="예: 운영 서비스 A의 현재 환경, 보호 요구 전체"
              onChange={(e) => setFv((x) => ({ ...x, approval_terms: { ...x.approval_terms, scope: e.target.value || null } }))}
            />
          </label>
          <label className={p.field}>
            <span className={changed ? p.req : undefined}>근거 문서</span>
            <input
              className="input"
              aria-required={changed}
              value={basis ?? ""}
              disabled={!entries.length}
              placeholder={entries.length ? "예: 보안정책 v3 §4" : "보호 요구를 하나 이상 결정하면 입력할 수 있습니다"}
              onChange={(e) => {
                const value = e.target.value || null;
                setFv((x) => ({ ...x, requirements: Object.fromEntries(Object.entries(x.requirements).map(([k, v]) => [k, v.decision === "change" ? { ...v, basis: value } : v])) }));
              }}
            />
          </label>
          {fv?.approval_terms.decision === "conditional" && (
            <label className={p.field} style={{ gridColumn: "1 / -1" }}>
              <span className={p.req}>승인 조건</span>
              <textarea
                className="input"
                aria-required
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

      <InputNavigation label="정책 구역 바로가기" items={SECURITY_FUNCTIONS.map((fn) => ({ id: `pol-function-${fn.no}`, title: fn.title }))} />

      <section id="pol-reqs" className="card card-pad stack">
        <div className="row wrap" style={{ justifyContent: "space-between" }}>
          <h2>보호 요구별 승인 기준</h2>
          <div className={p.inputToolbar}>{[false, true].map((open) => <button key={String(open)} type="button" className="btn btn-small" onClick={(e) => e.currentTarget.closest("section")?.querySelectorAll<HTMLDetailsElement>("details[data-editor-card]").forEach((node) => { node.open = open; })}>{open ? "전체 펼치기" : "전체 접기"}</button>)}</div>
          <Segmented
            label="보기"
            value={filter}
            onChange={(v) => setFilter((v ?? "all") as typeof filter)}
            options={[
              { value: "all", label: "전체" },
              { value: "undecided", label: "보완 필요한 요구" },
              { value: "measure", label: "측정 후 결정 수치" },
            ]}
          />
        </div>
        <div className="small muted">
          {props.isNew ? (
            <>
              요구마다 <b>새로 정함</b>·<b>확인 필요</b> 중에서 고릅니다. 구체적인 값은 새로 정함을 고르면 나타납니다.
            </>
          ) : (
            <>
              요구마다 기존 승인 정책을 <b>유지</b>·<b>변경</b>·<b>확인 필요</b> 중에서 고릅니다. 구체적인 값은 변경을 고르면 나타납니다.
            </>
          )} 수치는 확정·측정 후 결정·미정 상태로 남기고, 근거 없는 숫자를 채우지 않습니다. 입력 확정 {totals.decided}/{active.length} · 측정 후 결정 수치 {totals.measure}개
        </div>
        {SECURITY_FUNCTIONS.map((fn) => {
          const reqs = fn.requirements.filter((r) => {
            const e = fv?.requirements[r.id];
            if (filter === "undecided") { const [done, total] = reqProgress(r, e, envAnswers, catalog, doc); return !r.fixed && (!decisionComplete(e) || done !== total); }
            if (filter === "measure") return Object.values(e?.numbers ?? {}).some((x) => x.status === "after_measurement");
            return true;
          });
          if (!reqs.length) return null;
          const countable = fn.requirements.filter((r) => !r.fixed);
          const decided = countable.filter((r) => { const [done, total] = reqProgress(r, fv?.requirements[r.id], envAnswers, catalog, doc); return decisionComplete(fv?.requirements[r.id]) && done === total; }).length;
          return (
            <div key={fn.no} id={`pol-function-${fn.no}`} className="stack" style={{ gap: 8 }}>
              <div className={p.fnHead}>
                <span>
                  {fn.no}. {fn.title}
                </span>
                <Progress done={decided} total={countable.length} />
              </div>
              {reqs.map((r) => (
                <RequirementCard
                  key={r.id}
                  req={r}
                  entry={fv?.requirements[r.id]}
                  ctx={ctx}
                  isNew={props.isNew}
                  emptyModels={!doc.runtime.gateway.allowed_models.length}
                  envAnswers={envAnswers}
                  catalog={catalog}
                  priorRef={previous && confirmedRev ? { id: previous.policy_id, revision: confirmedRev, sha256: policyRevs.data?.items.find((r) => r.revision === confirmedRev)?.sha256 ?? "" } : undefined}
                  onChange={(next) => setFv((x) => ({ ...x, requirements: putKey(x.requirements, r.id, next && next.basis === null ? { ...next, basis } : next) as FrameworkValues["requirements"] }))}
                />
              ))}
            </div>
          );
        })}
      </section>

      <details id="pol-advanced" open={advancedOpen} onToggle={(event) => setAdvancedOpen(event.currentTarget.open)} className={`card card-pad ${p.fold}`}>
        <summary className={p.foldHead}>
          <span className={p.foldTitle}>고급 — 검증 팩·집행 의도·exec 예외 (JSON)</span>
        </summary>
        <div className={`${p.foldBody} stack`}>
          <div className="small muted">update POL 고유 필드와 runtime 집행 의도 원문입니다. 예외는 판정을 바꾸지 않고 '예외 승인됨'으로만 표시됩니다.</div>
          <div className={p.formGrid}>
            <FieldMessages ctx={ctx} path="/verification/pack" />
            <ListField ctx={ctx} path="/verification/scope/include" label="범위 포함" />
            <ListField ctx={ctx} path="/verification/scope/exclude" label="범위 제외" />
            <SelectField ctx={ctx} path="/runtime/gateway/profile" label="runtime profile" options={[{ value: "default", label: "기본 (default)" }, { value: "zeroday-lab", label: "시험용 (zeroday-lab)" }]} />
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

function RequirementCard({ req, entry, ctx, isNew, emptyModels, envAnswers, catalog, priorRef, onChange }: { req: Requirement; entry: ReqEntry | undefined; ctx: FormCtx; isNew: boolean; emptyModels: boolean; envAnswers: Record<string, Values | undefined>; catalog?: InputCatalog; priorRef?: { id: string; revision: number; sha256: string }; onChange: (e: ReqEntry | undefined) => void }) {
  const cur = entry ?? emptyReq();
  const connectedFields = packFields(catalog, req.id, req.fields);
  const update = (patch: Partial<ReqEntry>) => {
    const next = { ...cur, ...patch, ...(cur.decision === "keep" && (patch.values || patch.numbers) ? { decision: "change" } : {}) };
    const empty = next.decision === null && !Object.keys(next.values).length && !Object.keys(next.numbers).length && next.note === null;
    onChange(empty ? undefined : next);
  };
  const [done, total] = reqProgress(req, entry, envAnswers, catalog, ctx.doc as PolicyDoc);
  const numbers = shownNumbers(req, cur.values, cur.numbers, envAnswers);
  const path = `/framework_values/requirements/${req.id}`;
  const errs = issuesAt(ctx.issues, path);
  const hasError = errs.length > 0 || (GATEWAY_PATHS[req.id] ?? []).some((k) => issuesAt(ctx.issues, `/runtime/gateway/${k}`).length > 0);
  // 허용 모델은 필수라 비어 있으면 처음부터 펼친다.
  const [open, setOpen] = useState(() => hasError || (req.id === "1.2" && emptyModels));
  useEffect(() => {
    if (hasError) setOpen(true);
  }, [hasError]);
  return (
    <details id={`req-${req.id}`} data-editor-card data-field-path={path} className={p.fold} open={open} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className={p.foldHead}>
        <span className={p.foldNo}>{req.id}</span>
        <span className={p.foldTitle}>{req.title}<small className={p.foldSummary}>{req.fixed ? "기존 runtime 기준을 확인하세요" : cur.decision === "needs_confirmation" ? "확인 필요 · 입력 확정에 포함하지 않음" : Object.values(cur.numbers).some((n) => n.status === "after_measurement") ? "측정 후 결정할 수치가 있습니다" : done === total && decisionComplete(cur) ? "표시된 입력을 작성했습니다 · 승인·시험 결과는 별도" : "필요한 기준을 확인하고 작성하세요"}</small></span>
        {req.fixed ? <span className={p.decision}>runtime이 정함</span> : entry?.decision && <span className={p.decision}>{DECISION_LABEL[entry.decision]}</span>}
        {!req.fixed && <Progress done={done} total={total} />}
      </summary>
      <div className={p.foldBody}>
        {(req.fixed || req.see || req.gateway) && (
          <details className={p.inlineHelp}><summary>기준·적용 범위 안내</summary><Note>
            {req.fixed && <div>{req.fixed}</div>}
            {req.see && <div>{req.see}</div>}
            {req.gateway && <div>{req.gateway}</div>}
          </Note></details>
        )}
        <GatewayFields req={req.id} ctx={ctx} />
        {!req.fixed && (
          <div className={p.qField}>
            <div className={p.qLabel}>
              <span className={p.req}>기존 승인 정책 처리</span>
            </div>
            <Segmented
              label="기존 승인 정책 처리"
              value={cur.decision ?? undefined}
              onChange={(v) => update({ decision: (v ?? null) as string | null })}
              options={[
                ...(isNew ? [] : [{ value: "keep", label: "유지" }]),
                { value: "change", label: isNew ? "새로 정함" : "변경" },
                { value: "needs_confirmation", label: "확인 필요" },
              ]}
            />
          </div>
        )}
        {showDetail(entry) && req.fields.length > 0 && (
          <div className={p.qGrid}>
            {req.fields
              .filter((f) => visible(f, cur.values, envAnswers) || cur.values[f.key] !== undefined)
              .map((f) => (
                <InputFieldView
                  key={f.key}
                  field={f}
                  path={`${path}/values/${f.key}`}
                  value={cur.values[f.key]}
                  required={isRequired(f, cur.values, envAnswers)}
                  error={issuesAt(ctx.issues, `${path}/values/${f.key}`)[0]?.message}
                  onChange={(v) => update({ values: putKey(cur.values, f.key, v) })}
                />
              ))}
          </div>
        )}
        {cur.decision === "keep" && priorRef && <div className={p.priorReference}><strong>유지할 이전 값</strong><span>{priorRef.id} · 저장 판본 {priorRef.revision}</span><details><summary>출처 해시 확인</summary><code>{priorRef.sha256}</code></details></div>}
        {cur.decision === "keep" && !decisionComplete(cur) && <Note>유지할 이전 승인 정책값이 없습니다. 미정으로 남깁니다.</Note>}
        {showDetail(entry) && connectedFields.length > 0 && (
          <div className="stack">
            <div className={p.qLabel}>Pack 실행 기준</div>
            <div className={p.qGrid}>
              {connectedFields.map((field) => <InputFieldView key={field.key} field={field} path={`${path}/values/${field.key}`} error={issuesAt(ctx.issues, `${path}/values/${field.key}`)[0]?.message} value={cur.values[field.key]} onChange={(v) => update({ values: putKey(cur.values, field.key, v) })} />)}
            </div>
          </div>
        )}
        {showDetail(entry) && numbers.length > 0 && (
          <div className="stack" style={{ gap: 8 }}>
            <div className={p.qLabel}>수치·기한</div>
            {numbers.map((d) => (
              <NumberEntry
                key={d.key}
                def={d}
                path={`${path}/numbers/${d.key}`}
                error={issuesAt(ctx.issues, `${path}/numbers/${d.key}`)[0]?.message}
                value={cur.numbers[d.key]}
                onChange={(v) => update({ numbers: putKey(cur.numbers, d.key, v) as Record<string, PolicyNumber> })}
              />
            ))}
          </div>
        )}
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
