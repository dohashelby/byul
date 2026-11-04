// 데이터의 영문 enum → 화면 한국어. 변환표는 이 파일 하나에 모은다(데이터는 영문 그대로 둔다).

// 톤은 serving-runtime 대시보드의 배지 규칙을 따른다:
// 중립이 기본, 통제 결과·충족·정상은 강조색(accent), 오류만 빨강(error), 경고·지연·예시는 주황(warn),
// 집행 시도는 점선 주황(caution: 탐지와 구분), 미확인·미설정은 회색(muted), 해당 없음은 점선 회색(faint).
export type Tone = "neutral" | "accent" | "warn" | "caution" | "error" | "muted" | "faint";

type Entry = { label: string; tone: Tone };

// 결과 배지(3.9). 탐지·집행 시도를 "차단됨"으로 쓰지 않는다. 거부 확인은 통제 결과 강조색이고,
// 탐지(주황)·집행 시도(점선 주황)와 색으로도 구분된다.
export const OUTCOME: Record<string, Entry> = {
  allowed: { label: "허용", tone: "neutral" },
  denied: { label: "거부 확인", tone: "accent" },
  invalid: { label: "입력 오류", tone: "accent" },
  engine_rejected: { label: "엔진 거부", tone: "accent" },
  observed: { label: "관측", tone: "faint" },
  detected: { label: "탐지", tone: "warn" },
  enforce_attempted: { label: "집행 시도", tone: "caution" },
  error: { label: "오류", tone: "error" },
  unknown: { label: "미확인", tone: "muted" },
};

// 요청 결과 집계에서 unknown은 "final 기록 없음"이다.
export const REQUEST_OUTCOME_LABEL: Record<string, string> = {
  allowed: "허용",
  denied: "거부",
  invalid: "입력 오류",
  engine_rejected: "엔진 거부",
  error: "오류",
  unknown: "판정 미기록",
};

export const VERDICT: Record<string, Entry> = {
  PASS: { label: "충족", tone: "accent" },
  FAIL: { label: "미충족", tone: "error" },
  UNKNOWN: { label: "미확인", tone: "muted" },
  NOT_APPLICABLE: { label: "해당 없음", tone: "faint" },
};

export const DATA_ORIGIN: Record<string, Entry> = {
  MEASURED: { label: "측정", tone: "accent" },
  DECLARED: { label: "신고", tone: "neutral" },
  EXAMPLE: { label: "예시", tone: "warn" },
};

export const SOURCE: Record<string, string> = {
  gateway: "요청 통제 (Gateway)",
  management_listener: "관리 요청 수신기",
  hubble: "네트워크 흐름 (Hubble)",
  tetragon: "프로세스·파일 이벤트 (Tetragon)",
  policy_state: "정책 상태",
  target_monitor: "대상 감시",
  collector: "관측 기록 수집",
};

export const CATEGORY: Record<string, string> = {
  request: "요청",
  network: "네트워크",
  process: "프로세스",
  file: "파일",
  policy: "정책",
  target: "대상",
  collector: "수집기",
};

export const PHASE: Record<string, string> = {
  loading: "모델 적재",
  compiling: "컴파일",
  inferring: "추론",
  maintenance: "유지보수",
  unknown: "단계 미확인",
};

export const COLLECTOR_STATE: Record<string, Entry> = {
  ok: { label: "정상", tone: "accent" },
  stale: { label: "지연", tone: "warn" },
  error: { label: "오류", tone: "error" },
  disconnected: { label: "미연결", tone: "error" },
  never: { label: "수집 전", tone: "muted" },
};

export const STORE_STATE: Record<string, Entry> = {
  ok: { label: "정상", tone: "accent" },
  error: { label: "오류", tone: "error" },
  unknown: { label: "미확인", tone: "muted" },
};

export const APPROVAL_STATE: Record<string, Entry> = {
  not_configured: { label: "미설정", tone: "muted" },
  verified: { label: "확인됨", tone: "accent" },
  last_verified: { label: "마지막 확인 유지", tone: "warn" },
  unverified: { label: "미확인", tone: "warn" },
  rejected: { label: "거부됨", tone: "error" },
  error: { label: "오류", tone: "error" },
};

export const COMPARE_STATE: Record<string, Entry> = {
  match: { label: "일치", tone: "accent" },
  mismatch: { label: "불일치", tone: "error" },
  drift: { label: "변경", tone: "error" },
  unknown: { label: "미확인", tone: "muted" },
  not_declared: { label: "미신고", tone: "faint" },
  missing: { label: "없음", tone: "warn" },
  stale: { label: "지연", tone: "warn" },
  error: { label: "오류", tone: "error" },
  not_collected: { label: "미수집", tone: "muted" },
};

export const APPLICABILITY: Record<string, Entry> = {
  APPLICABLE: { label: "적용", tone: "neutral" },
  NOT_APPLICABLE: { label: "해당 없음", tone: "faint" },
  UNDETERMINED: { label: "적용 미정", tone: "muted" },
  CONFLICT: { label: "값 충돌", tone: "warn" },
};

export const COMPARISON: Record<string, Entry> = {
  NO_CHANGE: { label: "변화 없음", tone: "neutral" },
  REGRESSED: { label: "후퇴", tone: "error" },
  FIXED: { label: "개선", tone: "accent" },
  STILL_FAILING: { label: "계속 미충족", tone: "error" },
  UNKNOWN: { label: "미확인", tone: "muted" },
  NOT_COMPARABLE: { label: "비교 불가", tone: "muted" },
  CARRIED_OVER: { label: "이월", tone: "faint" },
};

export const GAP: Record<string, string> = {
  NO_EXECUTOR: "실행기 없음",
  NO_POLICY_VALUE: "정책 값 없음",
  NO_FIXTURE: "시험 자료 없음",
  NO_ENV_FACT: "환경 사실 없음",
  ADAPTER_UNSUPPORTED: "어댑터 미지원",
};

export const EVIDENCE_SOURCE: Record<string, string> = { automated: "자동", manual: "수동" };

export const SIDE: Record<string, string> = { single: "단일", baseline: "기준", candidate: "후보" };

export const MODE: Record<string, string> = { audit: "감사", update: "업데이트" };

export const AREA: Record<string, string> = {
  SUP: "공급 자산 (모델·이미지)",
  REQ: "요청·상태",
  PLT: "실행 기반",
  LIF: "배포·변경",
  OBS: "관측·감사",
};

export const PACK_STATUS: Record<string, Entry> = {
  DRAFT: { label: "검토 전", tone: "warn" },
  REVIEWED: { label: "검토됨", tone: "neutral" },
};

export const DOC_STATUS: Record<string, Entry> = {
  DRAFT: { label: "초안", tone: "warn" },
  CONFIRMED: { label: "확정", tone: "accent" },
  EXAMPLE: { label: "예시", tone: "warn" },
};

export const TRIGGER: Record<string, string> = {
  asset_changed: "자산 변경",
  policy_content_changed: "정책 내용 변경",
  config_drift: "승인 구성 불일치 (drift)",
};

export const REVAL_EVENT: Record<string, string> = {
  opened: "열림",
  condition_cleared: "조건 해소",
  condition_reappeared: "조건 재발",
  superseded: "대체됨",
};

export const REVAL_STATUS: Record<string, Entry> = {
  open: { label: "열림", tone: "warn" },
  closed: { label: "닫힘", tone: "neutral" },
};

export const DECISION_RESULT: Record<string, string> = {
  ALLOWED: "허용",
  DENIED: "거부",
  INVALID: "입력 오류",
  ENGINE_REJECTED: "엔진 거부",
  ERROR: "오류",
};

export const SOURCE_MODE: Record<string, string> = { example: "예시", live: "실제 연결", file: "파일" };

export const CONNECTION: Record<string, Entry> = {
  example: { label: "예시 데이터", tone: "warn" },
  connected: { label: "연결", tone: "accent" },
  disconnected: { label: "연결 단절", tone: "error" },
  unauthorized: { label: "인증 거부", tone: "error" },
};

export const REGISTRATION: Record<string, string> = {
  registered: "등록됨",
  not_configured: "미설정",
  invalid: "형식 오류",
  error: "오류",
  unknown: "미확인",
};

export const DEPLOYMENT_KIND: Record<string, string> = { kubernetes: "Kubernetes", docker: "Docker", host: "호스트" };

// 엔진 종류. 저장값은 runtime backend kind와 같은 소문자다.
export const ENGINE_NAME: Record<string, string> = { vllm: "vLLM", sglang: "SGLang", "tensorrt-llm": "TensorRT-LLM" };

export const FACT_LABEL: Record<string, string> = {
  gateway: "Gateway 사용",
  prefix_cache: "prefix cache",
  prefix_cache_or_shared_cache: "prefix·공유 캐시",
  shared_or_remote_weights: "공유·원격 가중치",
  dynamic_import: "동적 import",
  alternate_sources_or_formats_or_derived_cache: "대체 출처·형식·파생 캐시",
  remote_acquisition_or_retry: "원격 취득·재시도",
  managed_or_external_fallback: "관리형·외부 대체",
  r10_features: "미디어·영속 작업 기능",
  request_code_revision_selection: "요청별 코드 판본 선택",
};

// update 보고서(삭제된 guard/report/html.py)의 문구를 그대로 쓴다.
export const NOT_SELECTED: Record<string, string> = {
  NOT_APPLICABLE: "해당 없음",
  POLICY_SCOPE: "정책 범위 밖",
  RETIRED: "폐기된 점검",
};

export const UNOFFICIAL_REASON: Record<string, string> = {
  POLICY_DRAFT: "정책 DRAFT",
  PACK_DRAFT: "검토 전 팩",
  EXAMPLE_DATA: "EXAMPLE 자료",
  CONDITIONS_UNMET: "조건 미충족",
};

export const EXCEPTION_STATUS: Record<string, string> = {
  APPROVED: "예외 승인됨",
  EXPIRED: "예외 만료",
  UNAPPROVED: "예외 미승인",
};

export const POLICY_STATUS: Record<string, Entry> = {
  DRAFT: { label: "정책 초안", tone: "warn" },
  CONFIRMED: { label: "정책 확정", tone: "neutral" },
};

export const MODE_LONG: Record<string, string> = { audit: "단일 점검(audit)", update: "버전 비교(update)" };

// ---- runtime 화면 문구 (삭제된 runtime 대시보드 static/app.js와 같은 문구) ----

// 요청 결과(runtime 판정) 표시. 사건 로그의 outcome 표(OUTCOME)와 달리 runtime 화면 문구를 쓴다.
export const REQUEST_RESULT: Record<string, Entry> = {
  allowed: { label: "허용", tone: "neutral" },
  denied: { label: "정책 거부", tone: "accent" },
  invalid: { label: "입력 오류", tone: "accent" },
  engine_rejected: { label: "엔진 거부", tone: "accent" },
  error: { label: "처리 오류", tone: "error" },
  unknown: { label: "판정 미기록", tone: "muted" },
};

export const APPROVAL_DETAIL: Record<string, string> = {
  not_configured: "미설정",
  verified: "승인 확인",
  unverified: "승인 목록 미확인 (추론 요청 503)",
  rejected: "승인 불일치·만료 (추론 요청 503)",
  error: "승인 확인 오류 (추론 요청 503)",
  last_verified: "승인 목록 조회 불가 (마지막 확인 승인 유지)",
};

export const VALIDITY_ITEM: Record<string, string> = { period: "기간", revocation: "철회", approval: "승인 확인" };
export const VALIDITY_RESULT: Record<string, string> = { not_configured: "미설정", POLICY_APPROVAL_VERIFIED: "확인됨" };

export const RT_TRIGGER: Record<string, string> = {
  asset_changed: "자산 변경",
  config_drift: "승인 구성 불일치 (drift)",
  policy_content_changed: "정책 내용 변경",
};

export const EXEC_VERDICT: Record<string, string> = {
  allowed_in_phase: "단계별 허용",
  exception: "승인 예외",
  outside_allowlist: "허용 목록 외 실행",
  suspect_binary: "의심 실행",
  exec_from_cache: "캐시 경로 실행",
  phase_unknown: "단계 미확인",
  phase_signal_unverified: "단계 근거 미검증 (엔진 로그)",
};

export const PARENT_CHECK: Record<string, string> = {
  matched: "부모 프로세스 일치",
  not_constrained: "부모 프로세스 제약 없음",
  unavailable: "부모 프로세스 정보 미확인",
  mismatch: "부모 프로세스 불일치",
};

export const PARENT_ATTRIBUTION: Record<string, string> = {
  available: "부모 프로세스 정보 확인",
  unavailable: "부모 프로세스 정보 미확인",
};

export const SUSPECT_CONTEXT: Record<string, string> = {
  compile_toolchain_parent: "컴파일 도구 하위 실행",
};

// Gateway의 요청 판정 코드. 전달 전 종료는 request_flow.finish_without_relay에서 attempted=false로 기록한다.
export const GATEWAY_REASON: Record<string, string> = {
  REQUEST_ALLOWED: "정책 검사 통과",
  POLICY_UNAVAILABLE: "적용 정책 부재 (엔진 미전달)",
  POLICY_NOT_YET_VALID: "정책 유효기간 시작 전 (엔진 미전달)",
  POLICY_EXPIRED: "정책 만료 (엔진 미전달)",
  POLICY_REVOCATION_UNKNOWN: "정책 철회 상태 미확인 (엔진 미전달)",
  POLICY_REVOKED: "정책 철회 (엔진 미전달)",
  POLICY_APPROVAL_UNVERIFIED: "정책 승인 미확인 (엔진 미전달)",
  POLICY_APPROVAL_REJECTED: "정책 승인 거부 (엔진 미전달)",
  POLICY_APPROVAL_ERROR: "정책 승인 확인 오류 (엔진 미전달)",
  TARGET_UNAVAILABLE: "대상 엔진 조회 불가 (엔진 미전달)",
  TARGET_MISMATCH: "등록 대상 불일치 (엔진 미전달)",
  TARGET_UNVERIFIED: "대상 미확인 (엔진 미전달)",
  AUTH_UNAVAILABLE: "인증 설정 사용 불가 (엔진 미전달)",
  AUTH_REQUIRED: "인증 정보 누락 (엔진 미전달)",
  AUTH_INVALID: "인증 실패 (엔진 미전달)",
  ROLE_NOT_ALLOWED: "역할 권한 부족 (엔진 미전달)",
  PRINCIPAL_MODEL_NOT_ALLOWED: "요청 주체 모델 권한 부족 (엔진 미전달)",
  CACHE_ISOLATION_UNAVAILABLE: "사용자별 캐시 격리 불가 (엔진 미전달)",
  BACKEND_CREDENTIAL_MISSING: "엔진 인증 정보 누락 (엔진 미전달)",
  BACKEND_CREDENTIAL_INVALID: "엔진 인증 정보 형식 오류 (엔진 미전달)",
  BACKEND_MODEL_UNMAPPED: "엔진 모델 매핑 누락 (엔진 미전달)",
  UNSUPPORTED_TRANSFER_ENCODING: "전송 인코딩 미지원 (엔진 미전달)",
  INVALID_CONTENT_LENGTH: "요청 본문 길이 오류 (엔진 미전달)",
  BODY_TOO_LARGE: "요청 본문 크기 초과 (엔진 미전달)",
  UNSUPPORTED_MEDIA_TYPE: "JSON 외 미디어 유형 (엔진 미전달)",
  MALFORMED_JSON: "JSON 구문 오류 (엔진 미전달)",
  DUPLICATE_KEY: "JSON 키 중복 (엔진 미전달)",
  INVALID_REQUEST: "요청 형식 오류 (엔진 미전달)",
  UNSUPPORTED_ALIAS: "토큰 필드 별칭 미지원 (엔진 미전달)",
  UNSUPPORTED_FIELD: "요청 필드 미지원 (엔진 미전달)",
  INVALID_MODEL: "모델 식별자 오류 (엔진 미전달)",
  INVALID_MESSAGES: "메시지 형식 오류 (엔진 미전달)",
  INVALID_PROMPT_TYPE: "프롬프트 유형 오류 (엔진 미전달)",
  INVALID_MAX_TOKENS: "출력 토큰 수 형식 오류 (엔진 미전달)",
  INVALID_STREAM: "연속 응답 설정 오류 (엔진 미전달)",
  N_UNSUPPORTED: "생성 개수 허용 범위 외 (엔진 미전달)",
  INVALID_EXTRA_FIELD: "추가 필드 정책 위반 (엔진 미전달)",
  MODEL_NOT_ALLOWED: "정책 외 모델 (엔진 미전달)",
  MAX_TOKENS_EXCEEDED: "출력 토큰 한도 초과 (엔진 미전달)",
  CONTEXT_LIMIT_EXCEEDED: "입력·출력 토큰 합계 한도 초과 (엔진 미전달)",
  CONTEXT_CHECK_UNAVAILABLE: "입력 토큰 수 확인 불가 (엔진 미전달)",
  CONCURRENCY_LIMIT: "동시 요청 한도 도달 (엔진 미전달)",
  STORE_UNAVAILABLE: "요청 로그 저장 불가 (엔진 미전달)",
  REQUEST_BODY_TIMEOUT: "요청 본문 수신 시간 초과 (엔진 미전달)",
  REQUEST_HEADER_TIMEOUT: "요청 헤더 수신 시간 초과 (엔진 미전달)",
  CONNECTION_IO_TIMEOUT: "연결 입출력 시간 초과",
  CONNECTION_LIMIT: "연결 한도 도달 (엔진 미전달)",
  ENGINE_DEADLINE: "엔진 처리 기한 초과",
  ENGINE_TIMEOUT: "엔진 응답 시간 초과",
  ENGINE_UNREACHABLE: "엔진 연결 불가",
  ENGINE_BAD_RESPONSE: "엔진 응답 형식 오류",
  ENGINE_AUTHORIZATION_FAILED: "엔진 인증 실패",
  ENGINE_CAPACITY_REJECTED: "엔진 용량 부족 거부",
  ENGINE_REJECTED_REQUEST: "엔진 요청 거부",
  STREAM_DEADLINE: "연속 응답 처리 기한 초과",
  STREAM_IDLE_TIMEOUT: "연속 응답 유휴 시간 초과",
  STREAM_TRUNCATED: "엔진 연속 응답 중단",
  STREAM_RELAY_ERROR: "연속 응답 전달 오류",
  CLIENT_DISCONNECTED: "요청자 연결 중단",
};

export const CACHE_GENERATION: Record<string, string> = { current: "현재 캐시 세대", other: "타 캐시 세대", outside: "캐시 범위 외" };

export const TIMELINE_PHASE: Record<string, string> = {
  started: "전달 전 시작 기록",
  response_started: "HTTP 200 응답 시작",
  final: "최종 기록",
};

export const CONTEXT_RESULT: Record<string, string> = {
  within_limit: "한도 이내",
  exceeded: "한도 초과(미전달)",
  unavailable: "토큰 수 확인 불가(미전달)",
};

export const AUTH_RESULT: Record<string, string> = {
  not_configured: "인증 미적용",
  not_evaluated: "인증 확인 전 종료",
  authenticated: "인증됨",
  missing: "인증 정보 없음",
  invalid: "인증 실패",
  unavailable: "인증 설정 오류",
};

export const EXTRA_FIELD_MODE: Record<string, string> = { validate: "검사", passthrough: "미검사 전달" };

export const METRIC_STATE: Record<string, string> = { ok: "정상", type_mismatch: "형식 불일치", unsupported_or_absent: "미제공" };

/** runtime 화면의 상태 배지 색(app.css state-*): 정상·일치·통과는 강조색, 오류·불일치·실패는 빨강, 누락·지연은 주황, 나머지는 회색. */
export function stateTone(value: string | null | undefined): Tone {
  if (value == null) return "muted";
  if (["match", "ok", "passed", "verified", "registered"].includes(value)) return "accent";
  if (["drift", "error", "mismatch", "multiple_targets", "container_missing", "failed", "inconsistent", "rejected"].includes(value)) return "error";
  if (["missing", "stale", "partial"].includes(value)) return "warn";
  return "muted";
}

export function entry(table: Record<string, Entry>, value: string | null | undefined): Entry {
  if (value == null) return { label: "–", tone: "faint" };
  return table[value] ?? { label: value, tone: "muted" };
}

export function label(table: Record<string, string>, value: string | null | undefined): string {
  if (value == null) return "–";
  return table[value] ?? value;
}

export type AlertSeverity = "높음" | "중간" | "낮음" | "정보" | "미분류";
export const EXEC_SEVERITY: Record<string, AlertSeverity> = {
  suspect_binary: "높음",
  exec_from_cache: "높음",
  outside_allowlist: "중간",
  phase_unknown: "낮음",
  phase_signal_unverified: "낮음",
  allowed_in_phase: "정보",
  exception: "정보",
};

export function alertSeverity(verdict: string | null | undefined, rule: string | null | undefined, source: string): AlertSeverity {
  if (source === "hubble" && verdict?.toLowerCase() === "dropped") return "정보";
  if (rule && /(?:^TG-SECRET-|SECRET)/i.test(rule)) return "높음";
  return verdict ? EXEC_SEVERITY[verdict] ?? "미분류" : "미분류";
}

export const SEVERITY_ORDER: Record<AlertSeverity, number> = {
  높음: 0, 중간: 1, 낮음: 2, 정보: 3, "미분류": 4,
};
