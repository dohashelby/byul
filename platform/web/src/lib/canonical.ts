// 정렬된 키·공백 없는 JSON과 SHA-256. 서버 byul_platform.contracts.canonical_json과 같은 규칙이다.
// 정수가 아닌 숫자는 Python(1.0)과 JS(1)의 표기가 다를 수 있으므로 서버 경고를 기준으로 삼는다.

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const obj = value as Record<string, unknown>;
    const keys = Object.keys(obj)
      .filter((k) => obj[k] !== undefined)
      .sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonicalJson(obj[k])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** 비사용 근거가 묶일 환경 입력: revision·created_at·framework_profile.absence_claims를 뺀 문서. */
export function environmentSnapshot(doc: Record<string, unknown>): Record<string, unknown> {
  const { revision: _r, created_at: _c, ...rest } = doc;
  const profile = rest.framework_profile;
  if (profile && typeof profile === "object") {
    const { absence_claims: _a, ...p } = profile as Record<string, unknown>;
    rest.framework_profile = p;
  }
  return rest;
}

export function environmentSnapshotSha256(doc: Record<string, unknown>): Promise<string> {
  return sha256Hex(canonicalJson(environmentSnapshot(doc)));
}
