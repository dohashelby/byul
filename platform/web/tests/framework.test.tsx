import { fireEvent, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { EnvironmentDoc } from "../src/api/types";
import { canonicalJson, environmentSnapshotSha256 } from "../src/lib/canonical";
import { ALL_REQUIREMENTS, ENV_CONDITIONS, SECURITY_FUNCTIONS, visible, type InputField } from "../src/lib/frameworkCatalog";
import { CONDITION_FACTS, DERIVED_FACTS, EnvironmentForm, REGISTERED_FACTS, claimSummary, deriveFact, newEnvironment } from "../src/pages/forms";
import { renderAt } from "./helpers";

describe("environment snapshot", () => {
  it("matches the server hash (platform documents.environment_snapshot_sha256)", async () => {
    const doc = {
      schema_version: "byul-environment/0.1",
      revision: 3,
      created_at: "x",
      target: { engine: { name: "vllm", version: null }, z: [1, "한글", true] },
      facts: {},
      framework_profile: {
        role: "test",
        conditions: { entry: { values: { b: 2, a: ["x"] }, origin: null, source: '값 "인용"' } },
        absence_claims: { gateway: { status: "draft" } },
      },
    };
    // 기대값은 서버 함수로 계산했다.
    expect(await environmentSnapshotSha256(doc)).toBe("ef155f8e1309a52a5a45e9597cf8e1439b4dded8c383a222f44094403c03cf5d");
  });

  it("sorts keys and drops undefined", () => {
    expect(canonicalJson({ b: 1, a: { d: undefined, c: [2, null] } })).toBe('{"a":{"c":[2,null]},"b":1}');
  });
});

describe("framework catalog", () => {
  it("covers 12 conditions and 36 requirements across 8 functions", () => {
    expect(ENV_CONDITIONS.map((c) => c.no)).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
    expect(SECURITY_FUNCTIONS).toHaveLength(8);
    expect(ALL_REQUIREMENTS).toHaveLength(36);
  });

  it("lists every CHK exactly once across requirements", () => {
    const all = ALL_REQUIREMENTS.flatMap((r) => r.checks);
    expect(all).toHaveLength(77);
    expect(new Set(all).size).toBe(77);
  });

  it("uses storage keys the contract accepts", () => {
    const key = /^[a-z][a-z0-9_]{0,63}$/;
    for (const c of ENV_CONDITIONS) for (const f of c.fields) expect(f.key).toMatch(key);
    for (const r of ALL_REQUIREMENTS) {
      expect(r.id).toMatch(/^[1-8]\.[1-7]$/);
      for (const f of r.fields) expect(f.key).toMatch(key);
      for (const n of r.numbers) expect(n.key).toMatch(key);
    }
  });

  it("shows conditional fields only when the trigger matches", () => {
    const weights = ENV_CONDITIONS.find((c) => c.key === "weights")!;
    const zeroCopy = weights.fields.find((f) => f.key === "zero_copy")!;
    expect(visible(zeroCopy, { delivery: "local_file" })).toBe(false);
    expect(visible(zeroCopy, { delivery: "remote_streaming" })).toBe(true);
    const responsibility = ENV_CONDITIONS.find((c) => c.key === "responsibility")!;
    const evidence = responsibility.fields.find((f) => f.key === "evidence_access")!;
    expect(visible(evidence, { operator: "self" })).toBe(false);
    expect(visible(evidence, { operator: "vendor" })).toBe(true);
  });
});

describe("questions that appear only when needed", () => {
  const field = (cond: string, key: string) => ENV_CONDITIONS.find((c) => c.key === cond)!.fields.find((f) => f.key === key)!;

  it("hides a follow-up until another card's answer needs it", () => {
    const lora = field("features", "lora");
    expect(visible(lora, {}, {})).toBe(false);
    expect(visible(lora, {}, { supply: { extra_files: ["tokenizer"] } })).toBe(false);
    expect(visible(lora, {}, { supply: { extra_files: ["lora"] } })).toBe(true);
    expect(visible(lora, {}, { supply: { extra_files: null } })).toBe(true);
    // runtime이 엔진에 사용자 신원을 넘기지 않으므로 공용 계정 질문은 항상 묻는다.
    expect(field("users", "shared_service_account").when).toBeUndefined();
  });

  it("shows when any of several triggers matches", () => {
    const f: InputField = { key: "x", label: "x", kind: "tri", when: [{ cond: "a", key: "p", is: [true] }, { cond: "b", key: "q", is: ["on"] }] };
    expect(visible(f, {}, { a: { p: false }, b: { q: ["off"] } })).toBe(false);
    expect(visible(f, {}, { a: { p: false }, b: { q: ["on"] } })).toBe(true);
    expect(visible(f, {}, { a: { p: true } })).toBe(true);
  });

  it("drops questions the byul runtime Gateway already decides", () => {
    const keys = ENV_CONDITIONS.flatMap((c) => c.fields.map((f) => f.key));
    for (const k of ["identity_to_engine", "streaming", "structured_output", "tools", "embedding_input", "token_id_input", "media_inputs", "responses_store", "batch_jobs", "echo_logprobs", "limit_enforcer"])
      expect(keys).not.toContain(k);
    // 허용 모델은 runtime 정책에서 고르는 값이라 LoRA 요청 질문은 남긴다.
    expect(keys).toContain("lora");
  });

  it("points every cross-card trigger at a real question", () => {
    for (const c of ENV_CONDITIONS)
      for (const f of c.fields)
        for (const w of f.when ? (Array.isArray(f.when) ? f.when : [f.when]) : []) {
          const target = ENV_CONDITIONS.find((x) => x.key === (w.cond ?? c.key));
          expect(target?.fields.some((x) => x.key === w.key), `${c.key}.${f.key} → ${w.cond}.${w.key}`).toBe(true);
        }
  });
});

describe("environment form: one question per value", () => {
  it("asks each registered fact in exactly one condition card", () => {
    const placed = Object.values(CONDITION_FACTS).flat();
    expect([...placed].sort()).toEqual([...REGISTERED_FACTS].sort());
    for (const cond of Object.keys(CONDITION_FACTS)) expect(ENV_CONDITIONS.some((c) => c.key === cond)).toBe(true);
  });

  it("derives facts from the matching condition answer", () => {
    for (const [name, rule] of Object.entries(DERIVED_FACTS)) {
      expect(CONDITION_FACTS[rule.cond]).toContain(name);
      const field = ENV_CONDITIONS.find((c) => c.key === rule.cond)!.fields.find((f) => f.key === rule.key)!;
      const options = field.kind === "choice" ? field.options.map((o) => o.value) : [];
      // 선택지 하나하나가 사용·사용 안 함 중 하나로 정해져야 한다.
      expect([...rule.yes, ...rule.no].sort()).toEqual([...options].sort());
    }
    const weights = DERIVED_FACTS.shared_or_remote_weights;
    expect(deriveFact(weights, "remote_streaming")).toBe(true);
    expect(deriveFact(weights, "local_file")).toBe(false);
    expect(deriveFact(weights, null)).toBe(null);
    expect(deriveFact(weights, "legacy_value")).toBe(null);
    expect(deriveFact(weights, undefined)).toBe(undefined);
  });

  it("sets the weights fact from the delivery answer and drops it with the answer", () => {
    let doc: EnvironmentDoc = newEnvironment();
    const Harness = () => {
      const [d, setD] = useState(doc);
      doc = d;
      return <EnvironmentForm doc={d} setDoc={setD} issues={[]} warnings={[]} isNew />;
    };
    renderAt("/", "/", <Harness />);
    const pick = (label: string) => fireEvent.click(within(screen.getByRole("radiogroup", { name: "가중치 공급 방식" })).getByRole("radio", { name: label }));
    pick("로컬 파일");
    // update는 source가 없는 사실을 모름으로 보므로 화면 입력에도 source를 남긴다.
    expect(doc.facts?.shared_or_remote_weights).toMatchObject({ value: false, origin: "DECLARED", source: "byul platform 환경 입력 (가중치 공급 방식 답)" });
    pick("공유 daemon");
    expect(doc.facts?.shared_or_remote_weights).toMatchObject({ value: true });
    pick("공유 daemon");
    expect(doc.facts?.shared_or_remote_weights).toBeUndefined();
    // 조건 답으로 정해지는 사실과 전제로 정해진 사실은 따로 묻지 않는다.
    expect(screen.queryByRole("radiogroup", { name: "공유·원격 가중치" })).toBeNull();
    expect(screen.queryByRole("radiogroup", { name: "Gateway 사용" })).toBeNull();
  });

  it("picks the engine from a list and keeps an older free-text value", () => {
    const old = newEnvironment();
    old.target.engine.name = "custom-engine";
    renderAt("/", "/", <EnvironmentForm doc={old} setDoc={() => {}} issues={[]} warnings={[]} isNew={false} />);
    const select = screen.getByRole("combobox", { name: "엔진" }) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["", "vllm", "sglang", "tensorrt-llm", "custom-engine"]);
    expect(select.value).toBe("custom-engine");
  });

  it("saves hand edits as declared with the byul runtime Gateway premise", () => {
    const legacy = { ...newEnvironment(), data_origin: "MEASURED", facts: {} } as EnvironmentDoc;
    let doc = legacy;
    const Harness = () => {
      const [d, setD] = useState(doc);
      doc = d;
      return <EnvironmentForm doc={d} setDoc={setD} issues={[]} warnings={[]} isNew />;
    };
    renderAt("/", "/", <Harness />);
    expect(screen.queryByRole("radiogroup", { name: /값을 얻은 방법|값의 확인 상태/ })).toBeNull();
    fireEvent.change(screen.getByLabelText(/^대상 ID/), { target: { value: "svc-a" } });
    expect(doc.data_origin).toBe("DECLARED");
    expect(doc.facts?.gateway).toEqual({ value: true, origin: "DECLARED", source: "byul runtime Gateway 전제" });
    expect(newEnvironment().facts?.gateway).toMatchObject({ value: true });
  });

  it("builds the absence summary only from a filled claim", () => {
    const claim = { status: "draft" as const, reviewer: null, reviewed_at: null, disabled: true, nonexposed: null, references: [], target: { engine: null, version: null, source_commit: null, image_digest: null, environment_snapshot_sha256: null } };
    expect(claimSummary("prefix_cache", claim)).toBeNull();
    expect(claimSummary("prefix_cache", { ...claim, references: ["values.yaml#L40"] })).toContain("기능 비활성");
    expect(claimSummary("prefix_cache", { ...claim, disabled: null, references: ["values.yaml#L40"] })).toBeNull();
  });
});
