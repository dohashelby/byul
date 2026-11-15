import type { FieldIssue, InputCatalog } from "../api/types";
import { ALL_REQUIREMENTS, ENV_CONDITIONS } from "./frameworkCatalog";

export const fieldMessageId = (path: string) => `input-message-${encodeURIComponent(path)}`;

export function hasConfirmedAnswer(value: unknown): boolean {
  if (value === undefined || value === null) return false;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(hasConfirmedAnswer);
  if (typeof value === "object") return Object.values(value).every(hasConfirmedAnswer);
  return true;
}

export function inputIssueLabel(path: string): string {
  const jsonLabels: Record<string, string> = { "/verification/values": "점검별 정책 JSON", "/verification/exceptions": "검증 예외 JSON", "/runtime/enforcement": "집행 의도 JSON", "/runtime/exec_exceptions": "실행 예외 JSON" };
  if (jsonLabels[path]) return jsonLabels[path];
  const labels: Record<string, string> = { environment_id: "환경 ID", policy_id: "정책 ID", environment_ref: "기준 환경", target_ref: "대상 ID", name: "엔진", version: "엔진 판본", image_digest: "이미지 digest", id: "모델명", valid_from: "유효 시작", valid_until: "유효 종료", pack: "검증 Pack", allowed_models: "허용 모델", output_max_tokens: "최대 출력 토큰", max_input_tokens: "입력 길이 한도" };
  const req = /^\/framework_values\/requirements\/([^/]+)(?:\/(?:values|numbers)\/([^/]+))?/.exec(path);
  if (req) {
    const requirement = ALL_REQUIREMENTS.find((r) => r.id === req[1]);
    const field = [...(requirement?.fields ?? []), ...(requirement?.numbers ?? [])].find((f) => f.key === req[2]);
    return `${req[1]} ${requirement?.title ?? "보호 요구"}${field ? ` · ${field.label}` : ""}`;
  }
  const cond = /^\/framework_profile\/conditions\/([^/]+)(?:\/values\/([^/]+))?/.exec(path);
  if (cond) {
    const condition = ENV_CONDITIONS.find((c) => c.key === cond[1]);
    return condition?.fields.find((f) => f.key === cond[2])?.label ?? condition?.title ?? "환경 입력";
  }
  const parts = path.split("/").filter(Boolean);
  return labels[parts.at(-1) ?? ""] ?? (path.startsWith("/verification/values/") ? `${parts[2]} 정책 기준` : "입력 내용 확인");
}

/** 표시만 분류한다. 실행기 공백·지원 여부·판정은 변경하지 않는다. */
export function inputIssueGroup(issue: FieldIssue): "customer" | "system" | "connection" {
  if (issue.path === "/verification/pack" || /\/verification\/values\/[^/]+\/.*(?:sha256|session|prepared|validity_start|validity_end)/.test(issue.path)) return "system";
  if (/미지원|지원 정보|연결되지|불러오|연동 모듈/.test(issue.message)) return "connection";
  return "customer";
}

export function mappedIssuePath(path: string, catalog?: InputCatalog): string {
  const match = /^\/verification\/values\/([^/]+)\/([^/]+)$/.exec(path);
  const binding = match && catalog?.bindings.find((b) => b.check_id === match[1] && b.key === match[2]);
  return binding ? `/framework_values/requirements/${binding.requirement}/${binding.source}/${binding.field}` : path;
}

export function focusInputIssue(path: string, catalog?: InputCatalog): void {
  const actual = mappedIssuePath(path, catalog);
  const nodes = Array.from(document.querySelectorAll<HTMLElement>("[data-field-path]"));
  const exact = nodes.find((node) => node.dataset.fieldPath === actual);
  const parent = nodes.filter((node) => actual.startsWith(`${node.dataset.fieldPath}/`)).sort((a, b) => b.dataset.fieldPath!.length - a.dataset.fieldPath!.length)[0];
  const child = nodes.find((node) => node.dataset.fieldPath?.startsWith(`${actual}/`));
  const target = exact ?? parent ?? child;
  if (!target) return;
  let ancestor: HTMLElement | null = target;
  while (ancestor) {
    if (ancestor instanceof HTMLDetailsElement) ancestor.open = true;
    ancestor = ancestor.parentElement;
  }
  target.scrollIntoView?.({ block: "center", behavior: "auto" });
  const control = target.querySelector<HTMLElement>('input:not(:disabled), select:not(:disabled), textarea:not(:disabled), button:not(:disabled), summary, [tabindex="0"]');
  (control ?? target).focus({ preventScroll: true });
}

export function openInputSection(id: string): void {
  const target = document.getElementById(id);
  if (target instanceof HTMLDetailsElement) target.open = true;
  target?.scrollIntoView?.({ block: "start", behavior: "auto" });
}

export function formatAnswer(value: unknown): string {
  if (value === undefined) return "미입력";
  if (value === null) return "확인 필요";
  if (value === true) return "예";
  if (value === false) return "아니오";
  if (Array.isArray(value)) return value.length ? `${value.slice(0, 2).join(", ")}${value.length > 2 ? ` 외 ${value.length - 2}개` : ""}` : "명시적 없음";
  if (typeof value === "object") return `항목 ${Object.keys(value).length}개`;
  return String(value);
}
