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
  unknown: "미완료",
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
  gateway: "Gateway",
  management_listener: "관리 리스너",
  hubble: "Hubble",
  tetragon: "Tetragon",
  policy_state: "정책 상태",
  target_monitor: "대상 감시",
  collector: "수집기",
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
  loading: "로딩",
  compiling: "컴파일",
  inferring: "추론",
  maintenance: "유지보수",
  unknown: "미확인",
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
  SUP: "공급물",
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
  config_drift: "구성 변경",
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
  connected: { label: "연결됨", tone: "accent" },
  disconnected: { label: "끊김", tone: "error" },
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
  unknown: { label: "미완료", tone: "muted" },
};

export const APPROVAL_DETAIL: Record<string, string> = {
  not_configured: "미설정",
  verified: "확인됨",
  unverified: "목록에서 확인 못함 — 추론 요청은 503",
  rejected: "불일치·만료 — 추론 요청은 503",
  error: "확인 오류 — 추론 요청은 503",
  last_verified: "목록 확인 불가 — 마지막 확인 승인 유지",
};

export const VALIDITY_ITEM: Record<string, string> = { period: "기간", revocation: "철회", approval: "승인 확인" };
export const VALIDITY_RESULT: Record<string, string> = { not_configured: "미설정", POLICY_APPROVAL_VERIFIED: "확인됨" };

export const RT_TRIGGER: Record<string, string> = {
  asset_changed: "자산 변경",
  config_drift: "구성 drift",
  policy_content_changed: "정책 내용 변경",
};

export const EXEC_VERDICT: Record<string, string> = {
  allowed_in_phase: "단계 허용",
  exception: "승인 예외",
  outside_allowlist: "목록 밖",
  suspect_binary: "의심 실행",
  exec_from_cache: "캐시에서 실행",
  phase_unknown: "단계 불명",
  phase_signal_unverified: "엔진 신호만 근거 — 확인 필요",
};

export const CACHE_GENERATION: Record<string, string> = { current: "현재 캐시 세대", other: "다른 캐시 세대", outside: "캐시 밖" };

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
