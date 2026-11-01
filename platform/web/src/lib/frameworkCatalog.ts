// 프레임워크(update/reference/framework) 입력 카탈로그.
// environment.framework_profile.conditions의 12개 환경 조건과 policy.framework_values.requirements의 36개 보호 요구를
// 화면 입력 항목으로 정한다. 키·선택지 값은 저장 형식이므로 바꾸면 기존 판본을 읽을 때 '목록 밖 값'으로 보인다.
// 근거: 02 환경별 적용기준(12개 조건), 03 체크리스트 본문 1~8장의 '환경에 맞춰 채울 값', checklist.json의
// applicability_criteria(environment_axes·required_environment_facts)와 test_branch_conditions.

export const CATALOG_VERSION = "byul-framework-input/0.1";
/** 입력 기준이 된 framework data/checklist.json bytes의 SHA-256 (UPSTREAM.json, 2026-10-27 동기화). */
export const FRAMEWORK_CHECKLIST_SHA256 = "745e18640bc0c405baee1883dee53d1f35b644f0bc6116705a880b91893c590f";

export type Choice = { value: string; label: string };
/** 이 값이 나오면 질문을 보인다. cond가 있으면 다른 환경 조건 카드의 답을 본다. 답이 없으면(미입력) 숨긴다. */
export type When = { cond?: string; key: string; is: unknown[] };
type Base = { key: string; label: string; hint?: string; when?: When | When[]; /** 프레임워크가 답을 요구하는 질문. When이면 그 조건일 때만이다. '모름'도 답이다. */ required?: boolean | When | When[] };
export type InputField =
  | (Base & { kind: "tri"; yes?: string; no?: string })
  | (Base & { kind: "choice"; options: Choice[] })
  | (Base & { kind: "multi"; options: Choice[] })
  | (Base & { kind: "int"; unit?: string })
  | (Base & { kind: "text"; placeholder?: string; none?: string })
  | (Base & { kind: "list"; placeholder?: string; none?: string })
  | (Base & { kind: "kv"; keyLabel: string; valueLabel: string; suggestions?: string[]; none?: string });

const c = (value: string, label: string): Choice => ({ value, label });

// --- 환경 12개 조건 ---------------------------------------------------------

export type EnvCondition = {
  key: string;
  no: number;
  title: string;
  /** 이 조건에서 받지 않는 값에 대한 안내. */
  elsewhere?: string;
  /** 우리 runtime을 Gateway로 쓰면 이미 갖고 있거나 수집하는 값. */
  runtime?: string;
  /** 이 조건을 쓰는 기본 확인 CHK와 나머지 개수(checklist.json environment_axes 기준). */
  baseChecks: string[];
  otherChecks: string;
  fields: InputField[];
};

export const ENV_CONDITIONS: EnvCondition[] = [
  {
    key: "engine",
    no: 1,
    title: "엔진·버전·의존성",
    runtime: "runtime이 엔진 /version에서 종류·버전을 수집합니다. digest·커밋·의존성은 수집하지 않습니다.",
    baseChecks: ["SUP-003", "SUP-004", "REQ-001", "REQ-009", "PLT-002", "PLT-006", "OBS-004"],
    otherChecks: "분기 19 · 조건부 14 · 선정 구현 6",
    fields: [
      { key: "install_method", label: "설치 방식", kind: "choice", options: [c("official_image", "공식 이미지"), c("custom_image", "자체 빌드 이미지"), c("pip_package", "pip 패키지"), c("source_build", "소스 빌드")] },
      { key: "patch_state", label: "공식 판본 대비 수정", kind: "choice", options: [c("none", "수정 없음"), c("backport", "backport 패치 있음"), c("fork", "포크 사용")], hint: "버전 이름이 같아도 패치가 있으면 다른 환경으로 봅니다." },
      { key: "package_list", label: "패키지 목록·SBOM 보유", kind: "tri", yes: "있음", no: "없음" },
      { key: "dependencies", label: "주요 의존성", kind: "kv", keyLabel: "패키지", valueLabel: "판본", suggestions: ["torch", "cuda", "transformers", "xformers", "flashinfer", "fastapi"] },
    ],
  },
  {
    key: "entry",
    no: 2,
    title: "진입 경로·인증·인가",
    elsewhere: "byul runtime을 Gateway로 쓴다고 보고 호출 경로·Gateway 종류·신원 검증 위치·엔진까지 전달되는 신원은 묻지 않습니다. runtime은 사용자 신원을 엔진에 넘기지 않고 자기 backend 자격만 씁니다(cache 격리를 켜면 호출자별로 만든 salt만 더합니다). 엔진에 직접 닿는 길이 막혔는지는 따로 확인합니다.",
    runtime: "인증 주체·역할·route 역할·허용 모델은 runtime 설정에 있습니다.",
    baseChecks: ["SUP-002", "REQ-001", "PLT-010", "LIF-010", "OBS-001"],
    otherChecks: "분기 7 · 조건부 6",
    fields: [
      {
        key: "direct_block",
        label: "엔진 직통 차단 수단",
        kind: "multi",
        options: [c("network_policy", "Kubernetes NetworkPolicy·Cilium"), c("firewall", "보안 그룹·방화벽"), c("bind_localhost", "엔진을 localhost에만 바인드"), c("mesh_authz", "서비스 메시 인가"), c("none", "차단 없음")],
      },
      {
        key: "active_routes",
        label: "엔진에 열린 경로",
        kind: "multi",
        hint: "/v1만이 아니라 등록된 관리·진단 경로도 고릅니다.",
        options: [
          c("chat", "/v1/chat/completions"),
          c("completions", "/v1/completions"),
          c("embeddings", "/v1/embeddings"),
          c("responses", "/v1/responses"),
          c("audio", "/v1/audio/*"),
          c("score_rerank", "/score · /rerank · /pooling"),
          c("tokenize", "/tokenize · /detokenize"),
          c("models", "/v1/models"),
          c("health_version", "/health · /version"),
          c("metrics", "/metrics"),
          c("lora_admin", "LoRA 적재·해제"),
          c("cache_admin", "/reset_prefix_cache"),
          c("sleep_wake", "/sleep · /wake_up"),
          c("profiler", "/start_profile · /stop_profile"),
          c("collective_rpc", "/collective_rpc"),
        ],
      },
      { key: "other_routes", label: "그 밖의 경로", kind: "list", placeholder: "/custom/path" },
    ],
  },
  {
    key: "users",
    no: 3,
    title: "사용자·역할·공유 관계",
    elsewhere: "공유 허용 범위는 정책(4.2)에서 정합니다.",
    runtime: "runtime에는 Gateway에 등록된 주체만 있습니다. 앱 내부 사용자는 알 수 없습니다.",
    baseChecks: ["SUP-001", "SUP-002", "SUP-014", "REQ-001", "PLT-001", "PLT-003", "PLT-006", "PLT-010", "PLT-012", "OBS-001", "OBS-010"],
    otherChecks: "분기 9 · 조건부 7",
    fields: [
      { key: "tenancy", label: "사용 구성", kind: "choice", options: [c("single_team", "한 팀"), c("multi_team", "한 조직의 여러 팀"), c("multi_org", "여러 조직·외부 고객")] },
      {
        key: "shared_service_account",
        label: "공용 서비스 계정 뒤에 여러 사용자가 있음",
        kind: "tri",
        yes: "예",
        no: "아니오",
      },
      { key: "ops_owner", label: "운영 책임 담당", kind: "choice", options: [c("platform_ops", "플랫폼 운영"), c("ml_ops", "ML 운영"), c("security", "보안"), c("vendor", "외부 사업자")] },
    ],
  },
  {
    key: "supply",
    no: 4,
    title: "모델·공급물·로딩",
    runtime: "runtime이 모델 ID와 승인 자산 목록 대비 디스크 파일 해시를 수집합니다.",
    baseChecks: ["SUP-001", "SUP-002", "SUP-003", "SUP-004", "SUP-005", "SUP-014", "REQ-009", "PLT-003", "LIF-005", "LIF-015", "OBS-001"],
    otherChecks: "분기 13 · 선정 구현 2 · 조건부 7",
    fields: [
      { key: "model_source", label: "모델 공급원", kind: "choice", options: [c("local_bundle", "내부 반입 묶음"), c("hf_hub", "Hugging Face Hub"), c("object_storage", "객체 저장소(S3·GCS 등)"), c("registry", "내부 모델 레지스트리")] },
      { key: "acquisition", label: "가져오는 시점", kind: "choice", options: [c("baked_image", "이미지에 포함"), c("mounted", "볼륨 마운트"), c("download_at_start", "기동 시 내려받기")] },
      { key: "loader", label: "가중치 loader", kind: "choice", options: [c("safetensors", "safetensors 지정"), c("auto", "자동 선택(auto)"), c("pytorch", "PyTorch .bin·.pt"), c("gguf", "GGUF"), c("remote_streamer", "원격 streaming loader"), c("other", "기타")] },
      { key: "weight_formats", label: "가중치 형식", kind: "multi", options: [c("safetensors", "safetensors"), c("pt_bin", "PyTorch .bin·.pt"), c("gguf", "GGUF"), c("other", "기타")] },
      { key: "quantization", label: "양자화", kind: "choice", options: [c("none", "없음"), c("fp8", "FP8"), c("awq", "AWQ"), c("gptq", "GPTQ"), c("bitsandbytes", "bitsandbytes"), c("other", "기타")] },
      { key: "extra_files", label: "함께 쓰는 공급물", kind: "multi", options: [c("tokenizer", "별도 tokenizer"), c("chat_template", "chat template 파일"), c("generation_config", "generation config"), c("lora", "LoRA adapter")] },
      { key: "remote_code", label: "모델 저장소 코드 실행(trust_remote_code)", kind: "tri", yes: "사용", no: "사용 안 함" },
      { key: "plugins", label: "plugin·custom op", kind: "tri", yes: "사용", no: "사용 안 함" },
    ],
  },
  {
    key: "features",
    no: 5,
    title: "요청·활성 기능",
    elsewhere: "byul runtime Gateway는 /v1/chat/completions·/v1/completions만 열고 기본으로 model·messages(문자열)·prompt(문자열)·max_tokens·stream·n만 넘깁니다. chat 요청은 정책의 추가 허용 필드만큼 더 넘길 수 있습니다. 객체 필드는 열 수 없어 스트리밍(항상 열림)·구조화 출력·tools·embedding·token ID·미디어·Responses·batch는 묻지 않습니다.",
    baseChecks: ["SUP-003", "REQ-009", "LIF-005", "LIF-010"],
    otherChecks: "분기 15 · 조건부 11 · 선정 구현 3",
    fields: [
      { key: "offset_params", label: "offset 파라미터(routed_experts_prompt_start 등)", kind: "tri", yes: "수용", no: "수용 안 함", hint: "정책의 추가 허용 필드로 열었는지" },
      { key: "echo_logprobs", label: "입력 반환·logprobs 옵션", kind: "tri", yes: "노출", no: "노출 안 함", hint: "정책의 추가 허용 필드로 열었는지" },
      { key: "lora", label: "LoRA adapter 요청", kind: "tri", yes: "사용", no: "사용 안 함", hint: "정책의 허용 모델에 adapter 이름을 넣어 요청으로 고르게 하는지", required: true, when: { cond: "supply", key: "extra_files", is: ["lora", null] } },
    ],
  },
  {
    key: "placement",
    no: 6,
    title: "실행 배치·내부 통신",
    runtime: "runtime이 Pod 상태와 교체 이력을 수집합니다.",
    baseChecks: ["REQ-009", "PLT-001", "PLT-002", "PLT-006", "LIF-005", "LIF-010"],
    otherChecks: "분기 13 · 선정 구현 6 · 조건부 4",
    fields: [
      { key: "tp_size", label: "TP 크기", kind: "int", unit: "GPU", hint: "GPU 수와 같다고 가정하지 않습니다." },
      { key: "pp_size", label: "PP 크기", kind: "int" },
      { key: "replicas", label: "복제본 수", kind: "int" },
      { key: "executor", label: "실행기", kind: "choice", options: [c("mp", "multiprocessing"), c("ray", "Ray"), c("uni", "단일 프로세스"), c("external_launcher", "외부 launcher")] },
      { key: "runner", label: "실행 runner", kind: "choice", options: [c("v1", "V1"), c("v2", "V2"), c("other", "기타")], hint: "실제 선택된 runner를 적습니다. 다른 runner의 시험 결과는 쓰지 않습니다." },
      { key: "pd_disaggregation", label: "Prefill·Decode 분리(KV 전송)", kind: "tri", yes: "사용", no: "사용 안 함" },
      { key: "ipc_transport", label: "내부 IPC 방식", kind: "multi", options: [c("unix_socket", "Unix socket"), c("shared_memory", "공유 메모리"), c("tcp_loopback", "loopback TCP"), c("tcp_network", "노드 간 TCP")] },
    ],
  },
  {
    key: "host",
    no: 7,
    title: "호스트·자격·외부 접근",
    elsewhere: "비밀값 원문은 받지 않습니다.",
    runtime: "runtime이 Cilium·Hubble·Tetragon으로 송신 차단과 파일·실행 이벤트를 관측합니다(로컬 kind 확인).",
    baseChecks: ["SUP-004", "SUP-005", "REQ-001", "PLT-001", "PLT-002", "PLT-003", "PLT-006", "PLT-010", "PLT-012", "LIF-015", "OBS-004", "OBS-010", "OBS-011"],
    otherChecks: "분기 7 · 선정 구현 1 · 조건부 7",
    fields: [
      { key: "network_zone", label: "엔진이 있는 망", kind: "choice", options: [c("office", "사무용 PC 망"), c("server", "내부 서버망"), c("cloud_private", "클라우드 사설망"), c("public", "공개 진입점")] },
      { key: "user_network_zone", label: "사용자 단말이 있는 망", kind: "choice", options: [c("office", "사무용 PC 망"), c("server", "내부 서버망"), c("cloud_private", "클라우드 사설망"), c("public", "공개 인터넷")], hint: "엔진이 있는 망과 따로 기록합니다." },
      { key: "host_sharing", label: "호스트·GPU 공유", kind: "choice", options: [c("dedicated", "전용"), c("shared_host", "다른 작업과 호스트 공유"), c("shared_gpu", "GPU까지 공유")] },
      { key: "run_as_root", label: "엔진을 root로 실행", kind: "tri", yes: "예", no: "아니오" },
      { key: "model_mount_readonly", label: "모델 경로 읽기 전용 마운트", kind: "tri", yes: "예", no: "아니오" },
      { key: "credential_delivery", label: "자격 전달 방식", kind: "multi", options: [c("env_var", "환경변수"), c("mounted_file", "파일 마운트"), c("workload_identity", "workload identity"), c("secret_manager", "비밀 관리자"), c("none", "자격 없음")] },
      { key: "egress_control", label: "외부 송신 제한", kind: "choice", options: [c("none", "없음"), c("network_policy", "네트워크 정책"), c("proxy_allowlist", "프록시 허용 목록"), c("firewall", "방화벽")] },
      { key: "tls", label: "API 구간 TLS", kind: "choice", options: [c("none", "없음"), c("to_gateway", "Gateway까지"), c("end_to_end", "엔진까지")] },
    ],
  },
  {
    key: "weights",
    no: 8,
    title: "가중치 제공·공유",
    baseChecks: [],
    otherChecks: "분기 3(SUP-006·012, REQ-005) · 조건부 1(LIF-013)",
    fields: [
      { key: "delivery", label: "가중치 공급 방식", kind: "choice", options: [c("local_file", "로컬 파일"), c("shared_daemon", "공유 daemon"), c("remote_streaming", "원격 streaming")] },
      { key: "fallback", label: "다른 공급원으로 fallback", kind: "tri", yes: "있음", no: "없음", required: { key: "delivery", is: ["shared_daemon", "remote_streaming", null] } },
      { key: "zero_copy", label: "copy·zero-copy 공유 참조", kind: "tri", yes: "사용", no: "사용 안 함", required: true, when: { key: "delivery", is: ["shared_daemon", "remote_streaming"] } },
    ],
  },
  {
    key: "deploy",
    no: 9,
    title: "배포·변경·복귀",
    runtime: "runtime이 Pod 교체·정책 이력과 승인 기준 대비 drift를 수집합니다.",
    baseChecks: ["SUP-001", "SUP-002", "SUP-004", "SUP-005", "SUP-014", "PLT-012", "LIF-005", "LIF-015", "OBS-001"],
    otherChecks: "분기 9 · 선정 구현 1 · 조건부 3",
    fields: [
      { key: "deploy_tool", label: "배포 도구", kind: "choice", options: [c("helm", "Helm"), c("kustomize", "Kustomize"), c("gitops", "GitOps(Argo CD 등)"), c("terraform", "Terraform"), c("script", "수동·스크립트")] },
      { key: "change_strategy", label: "변경 방식", kind: "choice", options: [c("stop_redeploy", "중단 후 재배포"), c("rolling", "rolling"), c("canary", "canary"), c("blue_green", "blue-green"), c("live_update", "실행 중 교체")] },
      { key: "previous_kept", label: "직전 승인 구성 보존", kind: "tri", yes: "보존", no: "보존 안 함" },
      { key: "change_gate", label: "배포 전 승인 gate", kind: "tri", yes: "있음", no: "없음" },
    ],
  },
  {
    key: "load",
    no: 10,
    title: "부하·자원·수용",
    runtime: "runtime에 동시성·timeout 설정이 있고 엔진 running·waiting 지표를 수집합니다.",
    baseChecks: ["LIF-010", "OBS-011"],
    otherChecks: "분기 8 · 조건부 4",
    fields: [
      { key: "expected_concurrency", label: "예상 동시 요청", kind: "int", unit: "요청" },
      { key: "typical_input_tokens", label: "평균 입력 길이", kind: "int", unit: "token" },
      { key: "typical_output_tokens", label: "평균 출력 길이", kind: "int", unit: "token" },
      { key: "load_pattern", label: "부하 모양", kind: "choice", options: [c("steady", "일정"), c("bursty", "급증 있음"), c("batch", "일괄 처리")] },
      { key: "budget_units", label: "예산 계측 단위", kind: "multi", options: [c("tokens", "token"), c("bytes", "bytes"), c("seconds", "시간"), c("requests", "요청 수"), c("gpu_memory", "GPU 메모리")] },
      { key: "retry_actors", label: "실제 자동 재시도 주체", kind: "multi", options: [c("app", "업무 앱"), c("sdk", "SDK"), c("gateway", "Gateway"), c("engine", "엔진"), c("none", "없음")] },
    ],
  },
  {
    key: "observability",
    no: 11,
    title: "관측·보존·폐기",
    runtime: "runtime Gateway가 요청 기록을 남깁니다(prompt 원문 미저장, 7일·5GiB 기준 계산).",
    baseChecks: ["PLT-012", "OBS-001", "OBS-004", "OBS-010", "OBS-011"],
    otherChecks: "분기 8 · 조건부 7",
    fields: [
      { key: "log_sinks", label: "로그·지표 목적지", kind: "multi", options: [c("stdout", "표준 출력"), c("file", "로컬 파일"), c("loki", "Loki"), c("elasticsearch", "Elasticsearch"), c("cloud_logging", "클라우드 로깅"), c("otel_collector", "OTel collector")] },
      { key: "body_logging", label: "요청·응답 본문 기록", kind: "choice", options: [c("none", "기록 안 함"), c("metadata", "메타데이터만"), c("full", "본문까지")] },
      { key: "usage_stats", label: "엔진 사용 통계 보고", kind: "tri", yes: "켜짐", no: "꺼짐" },
      { key: "tracing", label: "OTLP trace 송신", kind: "tri", yes: "켜짐", no: "꺼짐" },
      { key: "diagnostics", label: "진단 dump·profiler", kind: "tri", yes: "사용", no: "사용 안 함" },
      { key: "replay", label: "기록 replay 도구", kind: "tri", yes: "사용", no: "사용 안 함" },
      { key: "backups", label: "로그·결과 백업 사본", kind: "tri", yes: "있음", no: "없음" },
    ],
  },
  {
    key: "responsibility",
    no: 12,
    title: "운영 책임·관리형",
    elsewhere: "관리형 서비스나 외부 추론 대체를 쓰는 환경은 이 체크리스트로 전체 평가를 끝낼 수 없습니다. 볼 수 있는 항목만 점검합니다.",
    baseChecks: [],
    otherChecks: "평가 기준 마련 전 · 관리형 사용 여부가 바뀌면 LIF-013·PLT-005·REQ-007·SUP-012·SUP-013을 다시 봅니다",
    fields: [
      { key: "operator", label: "엔진 운영 주체", kind: "choice", options: [c("self", "직접 운영"), c("cloud_managed", "클라우드 관리형"), c("vendor", "외부 사업자")] },
      { key: "evidence_access", label: "사업자가 주는 증거 범위", kind: "choice", options: [c("full", "설정·로그 모두"), c("partial", "일부"), c("none", "없음")], required: true, when: { key: "operator", is: ["cloud_managed", "vendor"] } },
    ],
  },
];

// --- 정책 36개 보호 요구 ----------------------------------------------------

export type NumberDef = { key: string; label: string; units: string[]; scopes: string[]; measure?: boolean; /** 이 값이 나오면 수치 칸을 보인다. */ when?: When };
export type Requirement = {
  id: string;
  title: string;
  checks: string[];
  fields: InputField[];
  numbers: NumberDef[];
  /** 같은 값을 다른 요구에서 이미 받을 때. */
  see?: string;
  /** byul runtime 정책 필드와 이어지는 값. */
  gateway?: string;
  /** byul runtime이 고정으로 정해 따로 결정하지 않는 요구의 안내. */
  fixed?: string;
};
export type SecurityFunction = { no: number; title: string; requirements: Requirement[] };

const n = (key: string, label: string, units: string[], scopes: string[], measure?: boolean): NumberDef => ({ key, label, units, scopes, measure });
const only = (def: NumberDef, when: When): NumberDef => ({ ...def, when });
const yesNo = (key: string, label: string, yes = "예", no = "아니오", hint?: string): InputField => ({ key, label, kind: "tri", yes, no, hint });

const LOG_ROLES = [c("gateway", "Gateway"), c("collector", "수집기"), c("security", "보안"), c("ops", "운영"), c("auditor", "감사")];

export const SECURITY_FUNCTIONS: SecurityFunction[] = [
  {
    no: 1,
    title: "인증·접근 통제",
    requirements: [
      {
        id: "1.1",
        title: "신원 인증·전달",
        checks: ["REQ-004"],
        fixed: "byul runtime이 정합니다. Gateway가 토큰 해시로 호출자를 인증하고 그 결과로만 권한을 판단하며, 엔진으로 보내는 header는 새로 만들어 외부 신원 header를 넘기지 않습니다.",
        fields: [],
        numbers: [],
      },
      {
        id: "1.2",
        title: "작업·대상 인가",
        checks: ["REQ-001", "REQ-002", "REQ-003", "REQ-005", "REQ-006", "SUP-002", "OBS-007"],
        fields: [
          { key: "caller_roles", label: "호출자별 역할", kind: "kv", keyLabel: "호출자", valueLabel: "역할(쉼표로 구분)", hint: "토큰 해시는 받지 않습니다. runtime 인증 파일에 따로 둡니다." },
          { key: "caller_models", label: "호출자별 허용 모델", kind: "kv", keyLabel: "호출자", valueLabel: "모델(쉼표로 구분)" },
          { key: "route_roles", label: "경로별 허용 역할", kind: "kv", keyLabel: "경로", valueLabel: "역할(쉼표로 구분)", suggestions: ["/v1/chat/completions", "/v1/completions", "/api/*"] },
          { key: "model_access", label: "모델 사용 권한 단위", kind: "choice", options: [c("authenticated", "인증된 모두"), c("per_role", "역할별 허용표"), c("per_user", "사용자별 허용표")] },
          { key: "direct_access", label: "엔진 직통을 허용할 주체", kind: "multi", options: [c("none", "없음"), c("ops", "운영 관리자"), c("monitoring", "모니터링"), c("test_harness", "시험 도구")] },
          { key: "admin_routes", label: "관리·진단 경로", kind: "choice", options: [c("not_exposed", "노출 안 함"), c("ops_only", "운영 역할만"), c("deny_all", "모두 거부")] },
          { key: "retired_model", label: "사용 종료 모델 처리", kind: "choice", options: [c("reject_new", "신규 요청 즉시 거부"), c("drain", "진행 요청 완료 후 제거"), c("alias_redirect", "alias를 새 모델로 전환")] },
          { key: "diag_readers", label: "진단 자료 열람 역할", kind: "multi", options: [c("security", "보안"), c("ops", "운영"), c("developer", "개발"), c("vendor", "외부 사업자")] },
        ],
        numbers: [n("retire_deadline", "사용 종료 처리 기한", ["일", "시간"], ["모델별", "alias별"]), n("temp_access_ttl", "진단·반출 임시 권한 기간", ["시간", "일"], ["건별"])],
      },
      {
        id: "1.3",
        title: "승인 권한 분리",
        checks: ["SUP-001"],
        fields: [
          { key: "approval_tool", label: "승인 기록 도구", kind: "choice", options: [c("git_review", "Git 리뷰"), c("ticket", "티켓 시스템"), c("model_registry", "모델 레지스트리"), c("byul_platform", "byul platform")] },
          { key: "separation", label: "제출·승인 분리", kind: "choice", options: [c("full", "제출·승인·배포 모두 다른 사람"), c("reviewer_differs", "검토자만 제출자와 다름"), c("single_allowed", "한 사람 허용")] },
          yesNo("emergency_access", "긴급 권한 사용", "사용", "사용 안 함"),
          { key: "role_accounts", label: "역할별 담당자·계정", kind: "kv", keyLabel: "역할", valueLabel: "담당자·계정(쉼표로 구분)", suggestions: ["제출", "승인", "배포"] },
          { key: "deploy_matrix", label: "모델×환경×작업 허용표", kind: "kv", keyLabel: "모델@환경", valueLabel: "허용 작업(쉼표로 구분)", hint: "1.2의 호출자별 허용 모델은 추론 권한입니다. 여기에는 제출·승인·배포 권한을 적습니다." },
        ],
        numbers: [only(n("emergency_ttl", "긴급 권한 기간", ["시간", "일"], ["부여 건별"]), { key: "emergency_access", is: [true, null, undefined] })],
      },
      {
        id: "1.4",
        title: "실행 최소 권한",
        checks: ["PLT-001", "PLT-002", "PLT-006"],
        fields: [
          { key: "run_user", label: "실행 계정", kind: "choice", options: [c("non_root", "전용 비root 계정"), c("root_dropped_caps", "root·권한 축소"), c("root", "root")] },
          { key: "allowed_resources", label: "허용 자원·권한", kind: "multi", options: [c("model_ro", "모델 읽기 전용"), c("cache_rw", "캐시 쓰기"), c("log_rw", "로그 쓰기"), c("shm", "공유 메모리"), c("gpu_device", "GPU 장치만")] },
          { key: "ipc_scope", label: "내부 IPC 접근", kind: "choice", options: [c("engine_only", "엔진 프로세스만"), c("same_pod", "같은 Pod"), c("host", "호스트 전체")] },
          { key: "allowed_processes", label: "허용 프로세스·명령", kind: "list", placeholder: "/usr/local/bin/vllm-nonroot-entrypoint.sh" },
          { key: "allowed_uids", label: "허용 UID", kind: "list", placeholder: "2000" },
          { key: "allowed_capabilities", label: "허용 capability", kind: "list", none: "없음" },
          { key: "allowed_mounts", label: "허용 mount", kind: "list", placeholder: "/models", none: "없음" },
          { key: "allowed_devices", label: "허용 장치", kind: "list", placeholder: "/dev/nvidia0", none: "없음" },
          { key: "protected_paths", label: "보호 경로", kind: "list", placeholder: "/var/run/secrets/kubernetes.io/serviceaccount" },
          { key: "exceptions", label: "권한 예외", kind: "text", placeholder: "예: 초기 장치 준비에만 privileged", none: "없음" },
        ],
        numbers: [],
      },
      {
        id: "1.5",
        title: "자격 제공·사용 제한",
        checks: ["PLT-003", "PLT-004", "PLT-005"],
        fields: [
          { key: "credential_scope", label: "자격 범위", kind: "choice", options: [c("per_role", "역할별 최소 범위"), c("shared", "공용 자격"), c("none", "자격 불필요")] },
          { key: "worker_credentials", label: "worker에 주는 자격", kind: "choice", options: [c("none", "없음"), c("read_storage", "저장소 읽기"), c("same_as_api", "API와 같음")] },
          { key: "fallback_credential", label: "자격 누락·만료 시 다른 자격 사용", kind: "choice", options: [c("forbidden", "금지"), c("listed_only", "지정한 대체만")] },
        ],
        numbers: [n("credential_ttl", "자격 유효 기간", ["시간", "일"], ["자격 종류별"]), n("rotation", "갱신 주기", ["일"], ["자격 종류별"]), n("error_wait", "오류 대기 기한", ["초"], ["요청별"])],
      },
      {
        id: "1.6",
        title: "통신 접근·송신 제한",
        checks: ["PLT-007"],
        see: "허용 프로세스·신뢰 집단은 1.4에서 정합니다.",
        fields: [
          { key: "egress_default", label: "기본 송신 정책", kind: "choice", options: [c("deny_allowlist", "모두 차단·허용 목록만"), c("allow_all", "모두 허용")] },
          { key: "egress_rules", label: "허용 송신 대상", kind: "kv", keyLabel: "목적지(FQDN·CIDR)", valueLabel: "포트/프로토콜·용도 (예: 443/TCP 모델 취득)", none: "없음" },
          { key: "on_policy_expiry", label: "정책 만료·부재 시", kind: "choice", options: [c("keep_last", "마지막 정책 유지"), c("deny_new", "신규 연결 거부"), c("fail_closed", "전체 차단")] },
        ],
        numbers: [n("policy_ttl", "정책 유효 기간", ["일"], ["정책 판본별"]), n("disconnect_response", "관리 연결 단절 대응 기한", ["초", "분"], ["관리 연결별"])],
      },
      {
        id: "1.7",
        title: "권한 철회",
        checks: ["PLT-010", "PLT-011", "PLT-012"],
        fields: [
          { key: "in_flight", label: "철회 시 진행 중 요청", kind: "choice", options: [c("finish", "끝까지 처리"), c("cancel", "즉시 취소"), c("finish_deadline", "기한 안에서 처리")] },
          { key: "cleanup_owner", label: "자격 사본 정리 담당", kind: "choice", options: [c("platform_ops", "플랫폼 운영"), c("security", "보안"), c("app_owner", "앱 담당")] },
        ],
        numbers: [n("propagation", "신규 접근 차단 전파 기한", ["초", "분"], ["철회 건별"]), n("session_end", "기존 세션 종료 기한", ["분", "시간"], ["철회 건별"]), n("copy_cleanup", "사본 정리 기한", ["시간", "일"], ["철회 건별"])],
      },
    ],
  },
  {
    no: 2,
    title: "공급물 무결성",
    requirements: [
      {
        id: "2.1",
        title: "승인 대상·출처 식별",
        checks: ["SUP-003", "SUP-004"],
        see: "여기에는 승인한 값을 적습니다. 실제 배포값은 환경 문서에 두고 대조합니다.",
        fields: [
          { key: "approval_unit", label: "승인 단위", kind: "choice", options: [c("bundle", "이미지+모델+설정 묶음"), c("model_only", "모델만"), c("image_only", "이미지만")] },
          { key: "acquisition_mode", label: "허용 반입 방식", kind: "choice", options: [c("offline_import", "내부 반입"), c("direct_pull", "직접 취득"), c("mount", "마운트")] },
          { key: "identification", label: "승인 식별 수단", kind: "multi", options: [c("image_digest", "이미지 digest"), c("model_revision", "모델 revision"), c("file_hashes", "파일 해시 목록"), c("signature", "서명")] },
          { key: "approved_image_digests", label: "승인한 이미지 digest", kind: "list", placeholder: "sha256:…" },
          { key: "approved_model_revisions", label: "승인한 모델 revision", kind: "list" },
          { key: "approved_template_sha256", label: "승인한 chat template 해시", kind: "list", placeholder: "64자리 hex" },
          { key: "approved_sources", label: "승인한 공급원", kind: "list", placeholder: "s3://models/approved" },
          { key: "approved_files", label: "승인한 파일 목록", kind: "list", placeholder: "model-00001.safetensors sha256:…" },
          { key: "approved_packages", label: "승인한 패키지 판본", kind: "kv", keyLabel: "패키지", valueLabel: "판본" },
          { key: "approved_tp_size", label: "승인한 TP 크기", kind: "int", unit: "GPU" },
          { key: "approved_settings", label: "승인한 유효 설정", kind: "kv", keyLabel: "설정", valueLabel: "값", suggestions: ["entrypoint"] },
        ],
        numbers: [],
      },
      {
        id: "2.2",
        title: "실제 사용의 무결성",
        checks: ["SUP-005", "SUP-006", "SUP-007"],
        fields: [
          { key: "integrity_controls", label: "변경 방지 수단", kind: "multi", options: [c("readonly_mount", "읽기 전용 마운트"), c("immutable_storage", "불변 저장소"), c("verify_before_read", "읽기 전 검증"), c("signed", "서명 확인")] },
          { key: "modifiers", label: "변경을 허용할 주체", kind: "multi", options: [c("release_manager", "배포 담당"), c("platform_ops", "플랫폼 운영"), c("none", "없음")] },
        ],
        numbers: [],
      },
      {
        id: "2.3",
        title: "생성물 완전성",
        checks: ["SUP-016"],
        fields: [
          { key: "cache_publish", label: "캐시 공개 방식", kind: "choice", options: [c("atomic", "원자적 교체"), c("lockfile", "잠금 파일"), c("none", "규칙 없음")] },
          { key: "cache_invalidation", label: "캐시 무효화 시점", kind: "multi", options: [c("model_change", "모델 변경"), c("dtype_quant", "dtype·양자화 변경"), c("tp_change", "TP 변경"), c("engine_change", "엔진·장치 변경")] },
        ],
        numbers: [],
      },
    ],
  },
  {
    no: 3,
    title: "입력 처리·코드 실행 안전성",
    requirements: [
      {
        id: "3.1",
        title: "최종 입력 검증",
        checks: ["REQ-007", "REQ-008", "REQ-009", "REQ-010"],
        see: "byul runtime은 chat·completions 두 경로만 열고 텍스트 입력만 받습니다. 객체 필드를 열 수 없어 구조화 출력은 쓸 수 없습니다.",
        fields: [
          { key: "template_override", label: "요청자의 template 변경", kind: "choice", options: [c("forbidden", "금지"), c("admin_only", "운영자만"), c("allowed", "허용")] },
          { key: "offset_params", label: "offset 파라미터", kind: "choice", options: [c("forbidden", "금지"), c("allowed", "허용")] },
        ],
        numbers: [
          n("max_input_tokens", "입력 길이 한도", ["token"], ["모델별", "호출자별"]),
        ],
      },
      {
        id: "3.2",
        title: "추가 코드 실행 허용",
        checks: ["SUP-008", "SUP-009", "SUP-010", "SUP-011"],
        fields: [
          { key: "remote_code", label: "모델 저장소 코드", kind: "choice", options: [c("forbidden", "금지"), c("allowlisted", "승인 목록만"), c("allowed", "허용")] },
          { key: "plugins", label: "plugin·custom op", kind: "choice", options: [c("forbidden", "금지"), c("allowlisted", "승인 목록만"), c("allowed", "허용")] },
          { key: "code_revision", label: "요청별 코드 판본 선택", kind: "choice", options: [c("forbidden", "금지"), c("pinned_only", "고정 판본만")] },
        ],
        numbers: [],
      },
      {
        id: "3.3",
        title: "자료 해석 안전성",
        checks: ["SUP-012", "SUP-013", "OBS-008"],
        see: "진단 자료 권한은 1.2에서 정합니다.",
        fields: [
          { key: "formats", label: "허용 형식", kind: "multi", options: [c("safetensors", "safetensors"), c("gguf", "GGUF"), c("pt_weights_only", "PT(weights_only)"), c("pt_pickle", "PT(전체 pickle)")] },
          { key: "format_change", label: "다른 형식이 필요할 때", kind: "choice", options: [c("reapprove", "정책 변경·재승인·재시험"), c("allowed", "바로 허용")] },
        ],
        numbers: [n("parse_bytes", "입력 처리 예산(크기)", ["MiB", "GiB"], ["형식별"]), n("parse_time", "입력 처리 예산(시간)", ["초"], ["형식별"])],
      },
      {
        id: "3.4",
        title: "GPU 메모리 안전성",
        checks: ["REQ-019"],
        fields: [{ key: "supported_gpus", label: "지원 GPU·runner", kind: "text", placeholder: "예: H100 · V1 runner" }],
        numbers: [n("numeric_tolerance", "허용 수치 오차", ["%"], ["모델·dtype별"], true), n("perf_baseline", "성능 저하 허용", ["%"], ["모델별"], true)],
      },
    ],
  },
  {
    no: 4,
    title: "상태 격리·재사용 안전성",
    requirements: [
      {
        id: "4.1",
        title: "요청·결과 귀속",
        checks: ["REQ-011", "REQ-012", "REQ-014"],
        fields: [
          { key: "result_reader", label: "결과 수신자", kind: "choice", options: [c("requester", "요청자만"), c("requester_org", "요청자 조직"), c("shared_group", "지정 공유 집단")] },
          { key: "late_output", label: "취소 후 늦은 출력", kind: "choice", options: [c("discard", "폐기"), c("log_only", "기록만")] },
          { key: "auto_retry", label: "추론 자동 재시도", kind: "choice", options: [c("forbidden", "금지"), c("gateway_only", "Gateway만"), c("app_only", "앱만")] },
          { key: "stored_results", label: "결과 저장 기능", kind: "choice", options: [c("disabled", "쓰지 않음"), c("owner_only", "소유자만 조회")], hint: "기준 환경의 엔진 경로에 /v1/responses가 있거나 모르면 묻습니다.", required: true, when: { cond: "entry", key: "active_routes", is: ["responses", null] } },
          { key: "cancel_point", label: "취소 확정 시점", kind: "text", placeholder: "예: 연결 종료를 Gateway가 감지한 시각" },
        ],
        numbers: [],
      },
      {
        id: "4.2",
        title: "공유 권한·범위",
        checks: ["REQ-015"],
        gateway: "cache 격리는 runtime 정책의 cache_isolation 값입니다.",
        fields: [
          { key: "cache_isolation", label: "runtime cache 격리", kind: "choice", options: [c("off", "끔"), c("per_principal", "호출자별 격리")] },
          { key: "sharing_unit", label: "cache 공유 단위", kind: "choice", options: [c("user", "사용자"), c("service_account", "서비스 계정"), c("tenant", "tenant"), c("global", "전체")] },
          { key: "public_prefix", label: "공개 prefix 공유", kind: "choice", options: [c("forbidden", "금지"), c("public_only", "공개 내용만 허용")] },
          { key: "salt_issuer", label: "cache salt 발급", kind: "choice", options: [c("gateway", "Gateway"), c("app", "업무 앱"), c("none", "쓰지 않음")] },
          { key: "shared_account_measure", label: "공용 계정 뒤 여러 사용자 대응", kind: "choice", options: [c("forward_identity", "사용자 신원 전달"), c("separate_deployment", "실행 분리"), c("disable_prefix_cache", "prefix cache 끔"), c("not_applicable", "해당 없음")] },
        ],
        numbers: [n("isolation_cost", "격리로 인한 성능 저하 허용", ["%"], ["모델별"], true)],
      },
      {
        id: "4.3",
        title: "재사용 문맥의 호환성",
        checks: ["SUP-015", "REQ-016"],
        see: "캐시 규칙은 2.3, 공유 단위는 4.2에서 정합니다.",
        fields: [{ key: "cache_key_context", label: "cache key에 넣을 문맥", kind: "multi", options: [c("model_revision", "모델 판본"), c("adapter", "adapter"), c("tenant_salt", "tenant salt")] }],
        numbers: [],
      },
      {
        id: "4.4",
        title: "실행 상태·버퍼 수명",
        checks: ["REQ-018", "REQ-020", "REQ-021", "LIF-013"],
        fields: [{ key: "force_kill", label: "강제 종료", kind: "choice", options: [c("after_deadline", "기한 후 허용"), c("never", "쓰지 않음")] }],
        numbers: [n("request_end", "요청 종결 기한", ["초"], ["취소 시점부터"]), n("process_end", "rank·프로세스 종료 기한", ["초"], ["종료 요청부터"]), n("ref_release", "참조 회수 기한", ["초"], ["요청 종결부터"])],
      },
    ],
  },
  {
    no: 5,
    title: "정보·통신 보호",
    requirements: [
      {
        id: "5.1",
        title: "반환 정보 최소 공개",
        checks: ["REQ-013"],
        fields: [
          { key: "allowed_return", label: "응답에 허용할 정보", kind: "multi", options: [c("usage", "사용량"), c("finish_reason", "종료 사유"), c("logprobs", "logprobs"), c("prompt_echo", "입력 반환"), c("token_ids", "token ID")] },
          { key: "error_detail", label: "오류 응답 상세도", kind: "choice", options: [c("generic", "일반 문구만"), c("code_message", "코드와 메시지"), c("debug", "내부 상세")] },
        ],
        numbers: [],
      },
      {
        id: "5.2",
        title: "수집 정보 최소화",
        checks: ["OBS-003"],
        fields: [
          { key: "body_logging", label: "본문 기록", kind: "choice", options: [c("forbidden", "금지"), c("metadata", "메타데이터만"), c("approved_sampling", "승인 시 표본")] },
          { key: "allowed_log_fields", label: "로그 허용 필드", kind: "list", placeholder: "request_id" },
          { key: "forbidden_log_fields", label: "로그 금지 필드", kind: "list", placeholder: "prompt" },
          { key: "diagnostics", label: "진단 dump·profiler", kind: "choice", options: [c("forbidden", "금지"), c("approved", "승인 시 허용")] },
          { key: "diag_approval", label: "진단 승인자·수집 목적", kind: "text", placeholder: "예: 보안 책임자 승인, 장애 원인 분석" },
        ],
        numbers: [n("collect_interval", "수집 주기", ["초", "분"], ["수집기별"])],
      },
      {
        id: "5.3",
        title: "부가정보 수집·송신 통제",
        checks: ["OBS-004", "OBS-005"],
        see: "수집 필드·주기는 5.2에서 정합니다.",
        fields: [
          { key: "usage_stats", label: "엔진 사용 통계 보고", kind: "choice", options: [c("forbidden", "금지"), c("partial", "일부 허용"), c("allowed", "허용")] },
          { key: "destinations", label: "허용 목적지", kind: "multi", options: [c("internal", "내부 수집기"), c("vendor", "외부 사업자"), c("none", "없음")], required: true, when: { key: "usage_stats", is: ["partial", "allowed"] } },
        ],
        numbers: [],
      },
      {
        id: "5.4",
        title: "저장 자료 보호",
        checks: ["OBS-006"],
        fields: [
          { key: "encryption", label: "저장 암호화", kind: "choice", options: [c("all", "모든 자료"), c("sensitive", "민감 자료만"), c("none", "요구 안 함")] },
          { key: "decrypt_roles", label: "복호화·열람 역할", kind: "multi", options: [c("security", "보안"), c("ops", "운영"), c("app_owner", "앱 담당")] },
          { key: "key_management", label: "키 관리", kind: "choice", options: [c("kms", "KMS"), c("hsm", "HSM"), c("app", "앱 자체")] },
        ],
        numbers: [only(n("key_rotation", "키 교체 주기", ["일"], ["키별"]), { key: "encryption", is: ["all", "sensitive", null, undefined] })],
      },
      {
        id: "5.5",
        title: "통신 상대·전송 보호",
        checks: ["PLT-008", "PLT-009"],
        fields: [
          { key: "api_tls", label: "API 구간 암호화", kind: "choice", options: [c("end_to_end", "엔진까지"), c("to_gateway", "Gateway까지"), c("none", "요구 안 함")] },
          { key: "internal_ipc", label: "내부 IPC·TP 전송", kind: "choice", options: [c("trusted_host", "신뢰 호스트 안에서만"), c("encrypted", "암호화"), c("not_applicable", "해당 없음")] },
          { key: "trusted_ca", label: "신뢰 인증기관", kind: "choice", options: [c("internal_ca", "내부 CA"), c("cloud_ca", "클라우드 CA"), c("public_ca", "공인 CA"), c("not_applicable", "해당 없음")] },
          { key: "peer_auth", label: "상대 인증", kind: "choice", options: [c("mtls", "mTLS"), c("token", "토큰"), c("none", "없음")] },
        ],
        numbers: [n("cert_rotation", "인증서·키 교체 주기", ["일"], ["구간별"]), n("tls_failure_detect", "전송 장애 감지 기한", ["초", "분"], ["구간별"])],
      },
      {
        id: "5.6",
        title: "부채널 노출 제한",
        checks: ["REQ-017"],
        see: "공유 정책은 4.2에서 정합니다. 표본·반복 횟수는 시험 절차에서 정합니다.",
        fields: [{ key: "acceptance", label: "허용 기준", kind: "choice", options: [c("chance_level", "우연 수준을 넘는 추론 불허"), c("bounded", "정한 성공률 이하")] }],
        numbers: [only(n("max_inference_rate", "허용 추론 성공률", ["%"], ["공격자 관측 범위별"]), { key: "acceptance", is: ["bounded", null, undefined] })],
      },
      {
        id: "5.7",
        title: "보존·삭제",
        checks: ["OBS-012"],
        fields: [
          { key: "deletion_check", label: "삭제 확인 수준", kind: "choice", options: [c("log", "삭제 기록"), c("verified", "삭제 확인 보고"), c("third_party", "제3자 확인")] },
          yesNo("include_backups", "백업 사본도 같은 기준 적용", "적용", "적용 안 함"),
          { key: "retention_exceptions", label: "보존 예외·승인자", kind: "text", placeholder: "예: 감사 대상 로그 1년, 보안 책임자 승인", none: "없음" },
        ],
        numbers: [n("log_retention", "로그 보존 기간", ["일"], ["자료 종류별"]), n("diag_retention", "진단 자료 보존 기간", ["일"], ["자료 종류별"]), n("delete_deadline", "삭제 완료 기한", ["일"], ["보존 만료부터"])],
      },
    ],
  },
  {
    no: 6,
    title: "자원 관리·서비스 가용성",
    requirements: [
      {
        id: "6.1",
        title: "자원 예산·공정성",
        checks: ["LIF-001", "LIF-003", "LIF-004"],
        gateway: "동시 처리 한도는 runtime --max-concurrency와 같아야 합니다.",
        fields: [
          { key: "overflow", label: "한도 초과 시", kind: "choice", options: [c("reject", "거부"), c("queue", "대기"), c("degrade", "축소 처리")] },
          { key: "acquisition_retry", label: "모델 취득 재시도", kind: "choice", options: [c("forbidden", "금지"), c("limited", "한도 안에서 허용")] },
        ],
        numbers: [
          n("max_concurrency", "동시 처리 한도", ["요청"], ["전체", "호출자별"], true),
          n("queue_length", "대기열 한도", ["요청"], ["전체"], true),
          n("request_timeout", "요청 처리 시간 한도", ["초"], ["요청별"], true),
          n("gpu_memory", "GPU 메모리 한도", ["GiB", "%"], ["엔진별"], true),
          n("stream_buffer", "연결별 스트림 버퍼 상한", ["MiB"], ["연결별"], true),
        ],
      },
      {
        id: "6.2",
        title: "재시도 비용 제한",
        checks: ["LIF-002", "LIF-012"],
        see: "재시도 허용 주체는 4.1(추론)과 6.1(모델 취득)에서 정합니다.",
        fields: [],
        numbers: [n("retry_count", "재시도 횟수", ["회"], ["주체별"]), n("backoff", "재시도 간격", ["초"], ["주체별"]), n("retry_total", "누적 재시도 기한", ["초", "분"], ["요청별"])],
      },
      {
        id: "6.3",
        title: "실패 감지·상위 통지",
        checks: ["LIF-008"],
        fields: [
          { key: "alert_channels", label: "알림 경로", kind: "multi", options: [c("pager", "호출"), c("chat", "메신저"), c("email", "메일"), c("ticket", "티켓")] },
          { key: "failure_owner", label: "1차 대응 담당", kind: "choice", options: [c("platform_ops", "플랫폼 운영"), c("ml_ops", "ML 운영"), c("vendor", "외부 사업자")] },
        ],
        numbers: [n("detect", "실패 감지 기한", ["초", "분"], ["실패 유형별"]), n("recover", "복구 목표 기한", ["분", "시간"], ["실패 유형별"])],
      },
      {
        id: "6.4",
        title: "종결·자원 회수",
        checks: ["LIF-010", "LIF-011", "LIF-014"],
        see: "회수 대상·기한은 4.4에서 정합니다.",
        fields: [{ ...yesNo("shared_lifecycle", "공유 weights·profiler에 별도 수명 계약", "있음", "없음"), required: [{ cond: "weights", key: "delivery", is: ["shared_daemon", "remote_streaming", null] }, { cond: "observability", key: "diagnostics", is: [true, null] }] }],
        numbers: [],
      },
    ],
  },
  {
    no: 7,
    title: "구성·변경 관리",
    requirements: [
      {
        id: "7.1",
        title: "필수 검사 결과 집행",
        checks: ["SUP-014"],
        fields: [
          { key: "required_checks", label: "필수 검사", kind: "multi", options: [c("malware", "악성코드 검사"), c("vuln", "취약점·SBOM 검사"), c("signature", "서명 확인"), c("license", "라이선스"), c("model_scan", "모델 파일 검사")] },
          { key: "exception_approver", label: "예외 승인자", kind: "choice", options: [c("security_lead", "보안 책임자"), c("ciso", "CISO"), c("change_board", "변경 위원회")] },
          { key: "recheck_condition", label: "재검사 조건", kind: "text", placeholder: "예: 예외 만료 또는 공급물 변경 시" },
        ],
        numbers: [n("exception_ttl", "예외 기한", ["일"], ["예외 건별"])],
      },
      {
        id: "7.2",
        title: "기동 준비 확인",
        checks: ["LIF-007", "LIF-009"],
        see: "실패 처리는 6.3에서 정합니다.",
        fields: [
          { key: "readiness", label: "필수 준비 확인", kind: "multi", options: [c("health", "health"), c("model_loaded", "모델 적재"), c("smoke", "정상 추론 1회"), c("perf", "성능 기준")] },
          { key: "normal_test_input", label: "정상 시험 입력", kind: "text", placeholder: "예: 한 문장 요약 요청, 답변 128 token" },
          { key: "resume_approver", label: "재개 승인", kind: "choice", options: [c("oncall", "당직 운영자"), c("release_manager", "배포 담당"), c("automatic", "자동")] },
        ],
        numbers: [n("startup_timeout", "기동 제한 시간", ["초", "분"], ["엔진별"], true), n("smoke_latency", "정상 응답 시간 기준", ["ms", "초"], ["모델별"], true)],
      },
      {
        id: "7.3",
        title: "변경 중 일관성",
        checks: ["LIF-005", "LIF-006"],
        fields: [
          { key: "strategy", label: "허용 변경 방식", kind: "choice", options: [c("stop_redeploy", "중단 후 재배포"), c("rolling", "rolling"), c("canary", "canary"), c("blue_green", "blue-green"), c("live_update", "실행 중 교체")] },
          { key: "mixed_generation", label: "세대 혼용", kind: "choice", options: [c("forbidden", "금지"), c("during_rollout", "전환 중에만 허용")] },
        ],
        numbers: [n("drain", "drain 기한", ["초", "분"], ["그룹별"]), n("maintenance", "허용 유지보수 시간", ["분", "시간"], ["변경 건별"])],
      },
      {
        id: "7.4",
        title: "복구 정책·상태 검증",
        checks: ["LIF-015", "LIF-016"],
        fields: [
          { key: "restore_target", label: "복원 대상", kind: "choice", options: [c("last_approved", "마지막 승인 구성"), c("snapshot", "지정 snapshot")] },
          yesNo("gitops_reconcile", "GitOps 재조정으로 복원", "사용", "사용 안 함"),
        ],
        see: "복구 목표 기한은 6.3에서 정합니다.",
        numbers: [],
      },
    ],
  },
  {
    no: 8,
    title: "감사 기록·관측 신뢰성",
    requirements: [
      {
        id: "8.1",
        title: "필수 사건 기록",
        checks: ["OBS-001"],
        fields: [
          { key: "events", label: "필수 사건", kind: "multi", options: [c("access", "접근 허용·거부"), c("approval", "승인·철회"), c("deploy", "배포·설정 변경"), c("lifecycle", "준비·실패·종료"), c("resume", "재개 판단")] },
          { key: "linkage", label: "연결 식별자", kind: "multi", options: [c("request_id", "요청 ID"), c("execution_id", "실행 ID"), c("bundle", "배포 묶음"), c("principal", "주체")] },
          { key: "record_owner", label: "기록 책임자·수집 위치", kind: "text", placeholder: "예: 플랫폼 운영, 중앙 Loki" },
        ],
        numbers: [n("collect_delay", "수집 지연 한도", ["초"], ["사건별"]), n("clock_skew", "허용 시계 오차", ["ms", "초"], ["수집원별"])],
      },
      {
        id: "8.2",
        title: "기록 위변조 방지",
        checks: ["OBS-009", "OBS-010"],
        fields: [
          { key: "integrity", label: "변조 방지 수단", kind: "multi", options: [c("append_only", "추가 전용"), c("hash_chain", "해시 연결"), c("worm", "WORM 저장소"), c("signed_export", "서명 반출")] },
          { key: "log_writers", label: "기록 작성 역할", kind: "multi", options: LOG_ROLES },
          { key: "log_readers", label: "기록 열람 역할", kind: "multi", options: LOG_ROLES },
          { key: "log_admins", label: "기록 관리 역할", kind: "multi", options: LOG_ROLES },
          { key: "on_failure", label: "수집 장애 시 운영", kind: "choice", options: [c("continue_alert", "계속·알림"), c("restrict", "기능 제한"), c("stop", "중단")] },
        ],
        numbers: [n("loss_detect", "누락 탐지 기한", ["초", "분"], ["수집원별"]), n("storage", "기록 저장 용량", ["GiB"], ["전체"])],
      },
      {
        id: "8.3",
        title: "실행 상태 관측·수집 건전성",
        checks: ["OBS-002", "OBS-011"],
        see: "필수 사건은 8.1, 수집 장애 처리는 8.2에서 정합니다.",
        fields: [{ key: "health_signals", label: "수집 건전성 신호", kind: "multi", options: [c("heartbeat", "heartbeat"), c("gap_record", "공백 구간 기록"), c("loss_counter", "유실 수")] }],
        numbers: [n("alert_deadline", "경보 기한", ["초", "분"], ["사건별"])],
      },
    ],
  },
];

export const ALL_REQUIREMENTS: Requirement[] = SECURITY_FUNCTIONS.flatMap((f) => f.requirements);

// --- 프레임워크가 정한 필수 입력 ---------------------------------------------
// 프레임워크에는 칸별 필수 표가 없다. 아래는 그 문서가 입력값으로 직접 지정한 항목만 고른 것이다.
// 조건 카드는 02 '현재 공통 구성·미정 사항'의 [환경값 입력], 보호 요구는 기본 확인·기본+조건부 CHK의 enterprise_decisions가 근거다.
// 조건부 CHK의 값은 그 조건일 때만 필수다. 사용 여부를 모르면 제외하지 않고 필수로 둔다. 선정 구현 CHK에서만 쓰는 값과 수치(미정·측정 후 결정을 허용)는 넣지 않는다.

const ENV_REQUIRED: Record<string, string[]> = {
  engine: ["dependencies"],
  entry: ["direct_block", "active_routes"],
  users: ["tenancy", "shared_service_account", "ops_owner"],
  supply: ["model_source", "acquisition", "weight_formats", "loader"],
  features: ["offset_params", "echo_logprobs"],
  placement: ["tp_size", "executor", "ipc_transport"],
  host: ["network_zone", "user_network_zone", "host_sharing", "run_as_root", "model_mount_readonly", "credential_delivery", "egress_control", "tls"],
  deploy: ["deploy_tool"],
  load: ["expected_concurrency", "typical_input_tokens", "typical_output_tokens", "load_pattern"],
  observability: ["log_sinks"],
};

const POLICY_REQUIRED: Record<string, string[]> = {
  "1.2": ["caller_roles", "route_roles", "model_access", "direct_access", "admin_routes", "retired_model"],
  "1.3": ["approval_tool", "separation", "emergency_access", "role_accounts", "deploy_matrix"],
  "1.4": ["run_user", "allowed_resources", "ipc_scope", "allowed_processes", "allowed_uids", "allowed_capabilities", "allowed_mounts", "allowed_devices", "exceptions"],
  "1.5": ["credential_scope", "worker_credentials", "fallback_credential"],
  "1.6": ["egress_default", "on_policy_expiry"],
  "1.7": ["in_flight", "cleanup_owner"],
  "2.1": ["acquisition_mode", "identification", "approved_image_digests", "approved_model_revisions", "approved_sources", "approved_files", "approved_packages", "approved_tp_size", "approved_settings"],
  "2.2": ["integrity_controls", "modifiers"],
  "2.3": ["cache_publish", "cache_invalidation"],
  "3.1": ["template_override", "offset_params"],
  "3.2": ["remote_code", "plugins"],
  "3.3": ["formats", "format_change"],
  "4.1": ["result_reader", "late_output", "auto_retry"],
  "4.4": ["force_kill"],
  "5.1": ["allowed_return", "error_detail"],
  "5.2": ["body_logging", "allowed_log_fields", "forbidden_log_fields", "diagnostics"],
  "5.3": ["usage_stats"],
  "5.4": ["encryption", "decrypt_roles"],
  "5.5": ["api_tls", "internal_ipc", "peer_auth", "trusted_ca"],
  "5.7": ["deletion_check", "include_backups"],
  "6.1": ["overflow", "acquisition_retry"],
  "6.3": ["alert_channels", "failure_owner"],
  "7.1": ["required_checks", "exception_approver"],
  "7.2": ["readiness", "resume_approver", "normal_test_input"],
  "7.3": ["strategy"],
  "7.4": ["restore_target", "gitops_reconcile"],
  "8.1": ["events", "linkage"],
  "8.2": ["integrity", "log_writers", "log_readers", "log_admins", "on_failure"],
};

/** 사실이 '사용 안 함'으로 정해지지 않은 동안. */
const unlessFalse = (key: string): When => ({ cond: "facts", key, is: [true, null, undefined] });
/** prefix cache나 공유 cache를 쓰는 환경에서만 적용되는 조건부 CHK(REQ-015·016·017)의 결정값. */
const WHEN_PREFIX_CACHE: When[] = [unlessFalse("prefix_cache"), unlessFalse("prefix_cache_or_shared_cache")];
const POLICY_REQUIRED_WHEN: Record<string, Record<string, When | When[]>> = {
  "1.2": { caller_models: { key: "model_access", is: ["per_role", "per_user", null, undefined] } },
  "1.6": { egress_rules: { key: "egress_default", is: ["deny_allowlist", null, undefined] } },
  "3.2": { code_revision: unlessFalse("request_code_revision_selection") },
  "5.4": { key_management: { key: "encryption", is: ["all", "sensitive", null, undefined] } },
  "4.2": { sharing_unit: WHEN_PREFIX_CACHE, public_prefix: WHEN_PREFIX_CACHE, salt_issuer: WHEN_PREFIX_CACHE, shared_account_measure: WHEN_PREFIX_CACHE },
  "4.3": { cache_key_context: WHEN_PREFIX_CACHE },
  "5.6": { acceptance: WHEN_PREFIX_CACHE },
  // LIF-005: live·rolling·canary를 고르면 허용 세대 혼용을 별도로 정한다.
  "7.3": { mixed_generation: { key: "strategy", is: ["rolling", "canary", "blue_green", "live_update"] } },
};

const mark = (fields: InputField[], keys: string[] = [], when: Record<string, When | When[]> = {}) => {
  for (const f of fields) {
    if (keys.includes(f.key)) f.required = true;
    if (when[f.key]) f.required = when[f.key];
  }
};
for (const cond of ENV_CONDITIONS) mark(cond.fields, ENV_REQUIRED[cond.key]);
for (const req of ALL_REQUIREMENTS) mark(req.fields, POLICY_REQUIRED[req.id], POLICY_REQUIRED_WHEN[req.id]);

/** 지금 답값에서 이 질문이 필수인지. */
export function isRequired(field: InputField, values: Record<string, unknown>, others: Record<string, Record<string, unknown> | undefined> = {}): boolean {
  if (typeof field.required !== "object") return field.required === true;
  return visible({ ...field, when: field.required }, values, others);
}

/** when 조건에 맞는지. 조건 값이 배열(multi)이면 하나라도 겹치면 보인다. */
/** 질문을 보일지. when이 여러 개면 하나라도 맞으면 보인다. others는 환경 조건 카드별 답(cond가 있는 when에서 쓴다). */
export function visible(field: { when?: When | When[] }, values: Record<string, unknown>, others: Record<string, Record<string, unknown> | undefined> = {}): boolean {
  if (!field.when) return true;
  return (Array.isArray(field.when) ? field.when : [field.when]).some((w) => {
    const v = (w.cond ? others[w.cond] : values)?.[w.key];
    if (Array.isArray(v)) return v.some((x) => w.is.includes(x));
    return w.is.includes(v);
  });
}
