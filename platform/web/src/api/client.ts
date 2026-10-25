// API 호출. 화면은 플랫폼 API(/api/*)만 부른다. runtime·update를 직접 부르지 않는다.

export type ApiErrorKind = "disconnected" | "unauthorized" | "not_found" | "invalid" | "server" | "network";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public body?: unknown,
  ) {
    super(message);
  }

  get kind(): ApiErrorKind {
    if (this.status === 0) return "network";
    if (this.code === "RUNTIME_UNREACHABLE") return "disconnected";
    if (this.code === "RUNTIME_UNAUTHORIZED") return "unauthorized";
    if (this.status === 404) return "not_found";
    if (this.status === 422) return "invalid";
    return "server";
  }
}

async function parse(res: Response): Promise<unknown> {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function errorFrom(status: number, body: unknown): ApiError {
  const b = body as { detail?: { code?: string; message?: string }; code?: string } | null;
  const code = b?.detail?.code ?? b?.code ?? `HTTP_${status}`;
  const message = b?.detail?.message ?? `요청 실패 (${status})`;
  return new ApiError(status, code, message, body);
}

export async function getJson<T>(path: string): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { headers: { Accept: "application/json" } });
  } catch {
    throw new ApiError(0, "NETWORK", "플랫폼 API에 연결하지 못했습니다");
  }
  const body = await parse(res);
  if (!res.ok) throw errorFrom(res.status, body);
  return body as T;
}

export async function postJson<T>(path: string, payload: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new ApiError(0, "NETWORK", "플랫폼 API에 연결하지 못했습니다");
  }
  const body = await parse(res);
  if (!res.ok) throw errorFrom(res.status, body);
  return body as T;
}
