import type { EnvironmentDoc, InputCatalog } from "../api/types";
import type { InputField } from "./frameworkCatalog";

export function versionProfile(catalog: InputCatalog | undefined, doc: EnvironmentDoc) {
  return catalog?.versions.find((v) => v.engine === doc.target.engine.name && v.version === doc.target.engine.version);
}

/** 비사용을 추정하지 않는다. 백엔드가 명시한 비적용 필드만 숨긴다. */
export function versionFieldVisible(hidden: string[], condition: string, key: string) {
  return !hidden.includes(`${condition}.${key}`);
}

const LABELS: Record<string, string> = {
  allowed_callers: "허용 호출자",
  general_allowed_kinds: "일반 호출자에게 허용할 작업 종류",
  allowed_endpoints: "시험에서 허용할 입력 경로",
  allowed_input_formats: "시험에서 허용할 입력 형식",
  rejection_codes: "허용 거부 코드",
  allowed_sampling_options: "허용 sampling 옵션",
  result_binding: "요청·결과 귀속 기준",
  client_request_id_trust: "클라이언트 요청 식별자 신뢰 기준",
  approved_code_revisions: "승인 코드 판본 목록",
  allowed_response_fields: "허용 응답 필드",
  allowed_error_fields: "허용 오류 필드",
  forbidden_return_options: "금지 반환 옵션",
  required_targets: "시험할 반환 대상",
  public_prefix_sharing_allowed: "공개 prefix 공유 허용",
  isolated_cache_groups: "분리할 캐시 그룹",
  cache_identity_policy_ref: "캐시 신원 정책 참조",
  required_cache_contexts: "필수 캐시 재사용 문맥",
  cache_timing_max_auc_percent: "캐시 부채널 최대 AUC (%)",
  cache_timing_min_samples: "캐시 부채널 최소 표본 수",
  require_non_root: "비root 실행 요구",
  allowed_exec_writable_paths: "허용 실행 가능 쓰기 경로",
  forbidden_env_names: "금지 자격 환경변수 이름",
  forbidden_mount_paths: "금지 자격 마운트 경로",
  credential_candidate_paths: "시험할 자격 후보 경로",
  forbidden_marker_contexts: "금지 로그 표식 문맥",
  required_sources: "필수 로그 수집원",
  required_branches: "필수 로그 수집 분기",
  usage_allowed: "사용 통계 보고 허용",
  allowed_usage_fields: "허용 사용 통계 필드",
  allowed_usage_sinks: "허용 사용 통계 목적지",
  usage_report_paths: "사용 통계 보고 경로",
  usage_settings_fresh_process: "새 프로세스의 통계 설정 확인 요구",
};

/** 실제 Pack에 존재하는 연결 키만 기존 카드 안에서 편집한다. 센서·해시는 만들지 않는다. */
export function packFields(catalog: InputCatalog | undefined, requirement: string, existing: InputField[]): InputField[] {
  const seen = new Set(existing.map((f) => f.key));
  return (catalog?.bindings ?? []).filter((b) => {
    if (b.requirement !== requirement || b.source !== "values" || seen.has(b.field)) return false;
    seen.add(b.field);
    return true;
  }).flatMap((b): InputField[] => {
    const base = { key: b.field, label: LABELS[b.field] ?? b.field, hint: `${b.check_id} · ${b.key}` };
    if (b.type === "bool") return [{ ...base, kind: "tri", yes: "예", no: "아니오" }];
    if (b.type === "int") return [{ ...base, kind: "int" }];
    if (b.type === "str") return [{ ...base, kind: "text" }];
    if (b.type === "list[str]") return [{ ...base, kind: "list", none: "없음" }];
    return [];
  });
}
