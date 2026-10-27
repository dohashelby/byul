import { describe, expect, it } from "vitest";
import { canonicalJson, environmentSnapshotSha256 } from "../src/lib/canonical";
import { ALL_REQUIREMENTS, ENV_CONDITIONS, SECURITY_FUNCTIONS, visible } from "../src/lib/frameworkCatalog";

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
    const entry = ENV_CONDITIONS.find((c) => c.key === "entry")!;
    const gw = entry.fields.find((f) => f.key === "gateway_kind")!;
    expect(visible(gw, { call_path: "direct" })).toBe(false);
    expect(visible(gw, { call_path: "app_gateway" })).toBe(true);
    const features = ENV_CONDITIONS.find((c) => c.key === "features")!;
    const url = features.fields.find((f) => f.key === "media_url_fetch")!;
    expect(visible(url, { media_inputs: ["none"] })).toBe(false);
    expect(visible(url, { media_inputs: ["audio"] })).toBe(true);
  });
});
