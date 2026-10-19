# 77행 자동화 분류표 (요약)

> 작성 2026-10-19. 판정·코드 변경 없음. 원본: `packs/PACK-BASELINE/checks/*.yaml`(checklist.json 원문 보존 필드) — 행별 표는 [`automation_matrix.csv`](automation_matrix.csv).

**등급 정의**  A: 맥 Docker + CPU vLLM(+ 로컬 컨테이너·fixture)만으로 실행기·판정을 실험실 위상에서 끝까지 재현 가능 · B: 추가 인프라 필요(GPU/TP≥2, 다중 노드, Ray, 부하 도구, 외부 서비스, 추가 모델 등) · C: 기업 실제 환경(승인·배포 시스템, IdP, 기록 저장소 등)이 있어야 의미 있는 판정 · M: 수동 판단만.

`불확실` 열이 채워진 행은 등급을 단정하지 않았다(사유 기재). 정책 값(예산·기한·허용표)이 필요한 행은 등급과 별개로 `value_policy`로 표시했다 — 정책 값은 POL 입력이며 인프라가 아니다.

## 1. 가능성별 행 수

| 등급 | 행 수 | 그중 불확실 |
|---|---|---|
| A | 47 | 21 |
| B | 14 | 6 |
| C | 16 | 3 |
| M | 0 | 0 |
| 합계 | 77 | 30 |

- A 47행 중 불확실 없이 확정한 A는 **26행**(REQ-01 구현 포함).
- M(수동 판단만)으로 분류한 행은 없다. 사람 결정이 필요한 행(신뢰 단위·지원 모드·수용 기준)은 결정 후 실행 가능하므로 해당 등급 + 불확실로 표시했다.

### 영역별

| 영역 | A | B | C | M |
|---|---|---|---|---|
| SUP | 11 | 1 | 4 | 0 |
| REQ | 16 | 5 | 0 | 0 |
| PLT | 5 | 4 | 3 | 0 |
| LIF | 10 | 3 | 3 | 0 |
| OBS | 5 | 1 | 6 | 0 |

### review_order별

| review_order | A | B | C | M |
|---|---|---|---|---|
| reference_baseline_first | 38 | 7 | 12 | 0 |
| implementation_path_first | 2 | 3 | 0 | 0 |
| conditional_when_selected | 7 | 4 | 4 | 0 |

## 2. 실행기 종류별 행 수 (만들면 풀리는 행, 많은 순)

'A 확정'은 그 실행기 하나로 지금 환경에서 바로 풀리는 행, 'A 전체'는 불확실 포함.

| 실행기 종류 | A 확정 | A 전체 | B | C | 합계 | 신규 |
|---|---|---|---|---|---|---|
| input_validation | 4 | 5 | 1 | 0 | 6 |  |
| http_boundary | 4 | 5 | 0 | 0 | 5 |  |
| log_analysis | 3 | 3 | 1 | 1 | 5 |  |
| code_load_control | 2 | 3 | 0 | 0 | 3 | 신규 |
| load_resource | 2 | 2 | 1 | 0 | 3 |  |
| container_config | 2 | 2 | 0 | 0 | 2 |  |
| supply_integrity | 1 | 6 | 0 | 2 | 8 |  |
| fault_injection | 1 | 6 | 0 | 0 | 6 |  |
| request_attribution | 1 | 3 | 1 | 0 | 4 | 신규 |
| lifecycle_transition | 1 | 2 | 2 | 1 | 5 | 신규 |
| cache_isolation | 1 | 2 | 1 | 0 | 3 | 신규 |
| telemetry_policy | 1 | 2 | 0 | 0 | 2 | 신규 |
| request_lifecycle | 1 | 1 | 0 | 1 | 2 | 신규 |
| egress_control | 1 | 1 | 0 | 0 | 1 | 신규 |
| response_content | 1 | 1 | 0 | 0 | 1 |  |
| ipc_access | 0 | 2 | 0 | 0 | 2 | 신규 |
| object_authz | 0 | 1 | 0 | 0 | 1 | 신규 |
| approval_workflow | 0 | 0 | 0 | 4 | 4 | 신규 |
| credential_lifecycle | 0 | 0 | 0 | 3 | 3 | 신규 |
| kernel_memory | 0 | 0 | 3 | 0 | 3 | 신규 |
| credential_scope | 0 | 0 | 2 | 0 | 2 | 신규 |
| record_access | 0 | 0 | 0 | 2 | 2 | 신규 |
| record_integrity | 0 | 0 | 0 | 2 | 2 | 신규 |
| transport_protection | 0 | 0 | 2 | 0 | 2 | 신규 |

- 지시의 기존 8종 외 **신규 제안 16종**: `approval_workflow`, `cache_isolation`, `code_load_control`, `credential_lifecycle`, `credential_scope`, `egress_control`, `ipc_access`, `kernel_memory`, `lifecycle_transition`, `object_authz`, `record_access`, `record_integrity`, `request_attribution`, `request_lifecycle`, `telemetry_policy`, `transport_protection`.
- `http_boundary`는 REQ-01로 이미 구현됨 → 같은 실험실·실행기를 확장하면 REQ-02~05가 이어진다.

## 3. 필요한 새 rule_table 연산자·기능 (행 수)

| 연산자·기능 | 행 수 | 용도 |
|---|---|---|
| `none_in` | 10 | 모음에 금지 값이 하나도 없음(표식 누출·금지 import 없음의 PASS 조건). 지금은 any_in으로 FAIL만 표현 가능 |
| `value_policy` | 9 | 기대값을 정책(POL.values)에서 읽음(예산·기한·한도). 판정기 입력에 policy_values는 이미 있음 |
| `subset_of` | 2 | 관측 ⊆ 허용 목록(읽은 파일 ⊆ 승인 목록, payload 필드 ⊆ 허용 필드) |
| `통계 비교` | 2 | 표본 분포 비교(시간 차 누출·부하 지연). 순수 함수로 구현 가능 |
| `예: diff_le_with_samples)` | 1 |  |

## 4. A 등급 추천 구현 순서 상위 10

| 순위 | 행 | 실행기 | 이유 |
|---|---|---|---|
| 1 | REQ-03 | http_boundary | REQ-01 실험실·실행기·판정표 형식을 그대로 재사용(경로별 키·역할 대조). 새 연산자 없음. 관리 경로 실측 목록이 5b에서 이미 확보됨 |
| 2 | REQ-05 | http_boundary | Gateway 키별 모델 허용표만 추가하면 됨. 단일 기본 모델로 기본 분기 시험 가능, LoRA 분기는 나중(B) |
| 3 | REQ-13 | response_content | 합성 표식 + Gateway/직통 응답 비교. REQ-01 위상 재사용. `none_in` 연산자 하나로 PASS 조건 표현 |
| 4 | REQ-09 | input_validation | 범위 밖 token ID·길이 단독 제출 + 후속 정상 요청. opt-125m vocab·max_len이 고정이라 경계가 명확, 상태 변경 없음 |
| 5 | REQ-10 | input_validation | REQ-09와 같은 실행기(옵션별 경계 밖 1개 + 병행 정상 요청). 실행기 하나로 두 행 |
| 6 | PLT-01 | container_config | `docker inspect` + sentinel 접근만으로 판정. 엔진 요청 불필요, 가장 저위험. PLT-04와 실행기 공유 |
| 7 | PLT-04 | container_config | 가짜 자격 표식을 launcher env에 넣고 worker 프로세스 env·mount에서 존재 확인. PLT-01과 같은 수집기 |
| 8 | LIF-09 | fault_injection | INV-15(빈 모델 디렉토리 → ABORTED) 실험실을 그대로 재사용. 부분 준비 실패가 성공으로 표시되는지 확인 |
| 9 | OBS-03 | log_analysis | 합성 표식 요청 후 엔진·Gateway 로그에서 표식 검색. REQ-13과 같은 표식 생성기 공유, `none_in` |
| 10 | OBS-09 | log_analysis | OBS-03과 같은 로그 수집기. 무해 구분문자 입력이 별도 사건으로 해석되는지 raw 로그 대조 |

선정 기준: ① 불확실 없는 A ② `reference_baseline_first` ③ 기존 실험실·실행기 재사용(새 인프라 최소) ④ 엔진 상태를 바꾸지 않음 ⑤ 실행기 하나로 여러 행을 푸는 순. 이 10개를 하면 실행기 4종(http_boundary 확장, input_validation, container_config, log_analysis)과 response_content·fault_injection 일부가 생기고, 다음 후보는 REQ-11(request_attribution), REQ-07(input_validation), OBS-04(telemetry_policy), REQ-15(cache_isolation).

## 5. 불확실 행 목록

| 행 | 등급 | 사유 |
|---|---|---|
| SUP-03 | A | 컨테이너 안 실제 파일 읽기 계측 방법 미정(strace/fanotify 권한 필요 여부) |
| SUP-04 | C | 우리 도구가 배포 검사 역할을 맡는다고 정하면 A(실행 이미지·패키지 수집과 manifest 대조는 로컬 가능) |
| SUP-05 | A | 교체가 실제 소비 시점 전후 중 언제였는지 계측 방법 |
| SUP-06 | A | CPU 단일 worker만 가능, 다중 rank 배정은 GPU TP(B) |
| SUP-07 | A | CPU 빌드의 Inductor cache 실제 경로·소비 여부 미확인 |
| SUP-08 | B | 멀티모달 모델이 있어야 도달. 텍스트 모델(opt-125m)에선 비도달 확인만 A |
| SUP-11 | A | "지원 실행 모드" 목록이 사람 결정 |
| SUP-15 | A | CPU 빌드에서 cache key·재생성 여부를 관측하는 방법 |
| SUP-16 | A | 안전한 중단 지점(예: compile cache 생성 중 kill) 선정 필요 |
| REQ-04 | A | 엔진이 실제로 소비하는 신원·priority header 목록 확인 필요 |
| REQ-06 | A | v0.31 Responses 저장 기능의 활성 조건·owner 개념 미확인 |
| REQ-08 | A | Harmony·kv_transfer_params 경로의 endpoint 도달성 |
| REQ-12 | A | 내부 attempt ID를 밖에서 관측하는 방법 |
| REQ-16 | B | LoRA adapter·MM fixture가 있어야 함. 텍스트 기본 경로만은 A |
| REQ-17 | A | CPU 지연 잡음, 수용 기준은 정책 값 |
| REQ-18 | A | CPU runner의 slot 재사용이 V1/V2 GPU 분기와 같은지 |
| PLT-02 | A | CPU 단일 프로세스의 IPC 구성이 GPU TP와 다름 |
| PLT-05 | B | 선정 SDK의 provider chain 미정 |
| PLT-06 | A | "신뢰 단위" 정의가 사람 결정 |
| PLT-09 | B | 이미지에 OTLP SDK가 들어 있는지 |
| PLT-11 | C | 실험실 nginx 키 교체로 모사는 가능하나 실제 issuer 동작과 다를 수 있음 |
| LIF-02 | A | HF 클라이언트를 로컬 장애 stub 저장소로 돌릴 수 있는지(HF_ENDPOINT) |
| LIF-03 | B | 작은 부하는 A, 처리율·공정성 기준은 대표 하드웨어 필요 |
| LIF-07 | A | CPU 단일 프로세스에선 rank 지연 주입 불가(그 부분은 B) |
| LIF-08 | A | 단일 worker 프로세스 예외·사망·지연만 가능, TP rank 통신 정지는 B |
| LIF-11 | A | 단일 worker 기준만 가능 |
| LIF-14 | A | 개발 모드·상태 변경 경로(/start_profile) 필요, CPU profiler 지원 미확인 |
| OBS-01 | C | Gateway↔엔진 기록 연결만은 A, 배포·승인 사건은 기업 기록 |
| OBS-05 | A | usage 목적지를 시험 sink로 돌릴 수 있는지 |
| OBS-11 | B | 우리 도구의 누락 처리(INV-04)는 검증됨. 대상은 기업 collector·경보 |
