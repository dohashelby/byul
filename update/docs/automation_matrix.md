# 77행 자동화 분류표 (요약)

> 2026-10-26 작업 후보: 팩26/DRAFT·정책25점검/54경로·보안 규칙 연결13개·관측 전용1개·실행기12종이다. PLT-02는 Unix IPC 접근 부분 범위이며 실제 엔진 시험은 보류했다. 기존 점검 재실증은 변경 영향이 있을 때만 해당 범위를 수행한다. 아래 팩25 이하·GUA PR의 현행 표기는 당시 기록이다. [관측 보강](AUTOMATION_HARDENING.md)를 따른다.

> 2026-10-26 후속: 팩25/DRAFT·정책24점검/52경로·실행/보안 판정 규칙 연결12점검·관측 전용1점검이다. SUP-04는 실행 이미지 승인 부분관측, SUP-03은 readonly template 신원·승인 membership·API projection 관측 전용이며 승인 일치/불일치 모두 보안 UNKNOWN이다. 기존 실행기11종을 재사용한다. 실제 vLLM 실증은 사용자 결정으로 보류하며 아래 과거 기록은 소급하지 않는다. [공급물 관측 한계](AUTOMATION_HARDENING.md)·[77개 추적](CHECKLIST_VALIDATION_STATUS.md)을 따른다.

> U1 원문 동기화: PACK-BASELINE 23/DRAFT·REQ-09 revision 7·PLT-04 revision 4이며 자동 연결 11개·정책 22점검/50경로와 A/B/C 분류는 유지한다. 승인한 원본 `729216b1`의 77행 질문·framework_id·mechanism_ids와 변경 선정 데이터를 반영해 CheckDef revision이 각각 1씩 증가했다. CSV는 질문만 동기화하며 기존 실측·남은 범위·근거를 보존한다. 팩23으로 새 실제 엔진 시험을 했다는 뜻이 아니며 U1 구현·검증 범위는 [변경 유형별 시험 선정](UPDATE_SELECTION.md)을 따른다.

> 2026-10-25 PR #30·#31 기준: 두 PR이 main(`fb0c103`)에 병합됐다. 당시 자동 연결 11개·PACK22는 같다. 실측은 조건별로 구분한다: PR #30 기본 실행(`length_cause_evidence` 없음)은 후보 REQ-09 보안 UNKNOWN으로 accepted=false, PR #31 후속 실행(`length_cause_evidence = true`, 검토 프로파일과 일치하는 0.32.0 CPU 이미지)은 다섯 점검 양쪽 PASS·NO_CHANGE로 accepted=true다. PLT-04는 후보 단독 시험(비어 있지 않은 EXAMPLE 후보)과 두 이미지 update(후보 `[]`, 부분 범위)를 구분한다. 아래 인용은 PR #30 당시 기록이다.

> 2026-10-24 PR #30 당시 기록: PR #29가 main `641f12f`에 반영된 뒤 PACK22/DRAFT·REQ-09 정의6·PLT-04 정의3·정책22점검50경로로 보완했다. 자동 연결11·77개 ID와 다른75개 정의를 유지한다. [새 계약 실측](UPDATE_VALIDATION.md#req-09plt-04-판정-의미-보완-후보)은 두 실제 CPU 이미지 실행·반출/복원·재판정 동일을 확인했지만 후보 REQ-09 보안 UNKNOWN으로 accepted=false다. 나머지 4개는 양쪽 보안/기능 PASS·PLT-04는 후보 빈 목록의 부분 범위다. 보안/기능 FAIL0·성능 UNKNOWN·official=false를 유지하고 실제 변화 탐지는 미증명이다. [PR #29 통합 검증](UPDATE_VALIDATION.md#v1-통합-후보-검증)과 아래 PR #28 성공은 팩21의 이력이며 팩22로 소급하지 않는다. 인프라 가능 등급 A/B/C와 정책 소비 분류 a/b/c/d는 별개다.

> 판정 의미: REQ-09는 상태 코드와 실제 소비를 구별한다. 원시 2xx 목록인 accepted_challenges는 신규 FAIL 근거가 아니며, 본문을 포함한 명시적 거부는 challenge_rejection_results, 실제 소비는 challenge_consumption, 확인된 위반은 confirmed_input_violations로 구분한다. 소비·피해 근거가 없으면 UNKNOWN이다. PLT-04의 forbidden_mount_paths는 명시적 마운트 금지 정책으로 FAIL을 보존한다. 선택 credential_candidate_paths는 파일 본문을 읽지 않고 동일 worker 주체의 메타데이터·읽기 권한을 관측한다. 누락은 미수집 UNKNOWN·명시[]는 요청한 후보 없음이다. candidate_readable은 메타데이터로 확인한 권한이며 자격 내용/사용 인증이 아니다. 상위 마운트만으로 이를 추정하지 않으며 forbidden_mount_violations·credential_candidate_results·credential_candidates_complete를 구분한다. canary 인증 프로파일은 지원하지 않는다. 기업 실제 목록을 줄이거나 운영 비밀 원문을 읽지 않는다. CSV는 이 두 행의 현행 계약·검증 범위만 바꾸며 다른75행과 A/B/C 등급은 보존한다.

> 기존 제한 계약: [REQ-15 M14](POLICY_M14.md)의 수동 신고 계약은 실제 캐시 실행기가 아니다. [판정·준비 보강](AUTOMATION_HARDENING.md)의 입력 projection 성공과 생성 출력 귀속 미검증을 구분하는 REQ-11 보안 PASS 보류는 유지한다. 과거 팩19의 실제 CPU PASS를 소급 변경하지 않는다.

> 두 이미지 실측: [PR #28의 C 후속 결과](UPDATE_VALIDATION.md#실측-결과--c-후속-2026-10-24)는 pinned vLLM CPU 0.31.0/0.32.0·기존 OPT 모델·동일 CPU 프로파일에서 REQ-01·REQ-03·REQ-09·PLT-01·PLT-04를 실제 실행하고 수락·반출/복원·저장 팩 재판정을 통과했다. 5개 점검의 양쪽 보안/기능은 PASS이며 전부 NO_CHANGE다. 실제 차이 탐지와 연결 11개의 모든 분기를 입증한 결과가 아니다. EVD 10개는 MEASURED이지만 REQ-01/03의 RES.verification_level은 조건의 신고 출처 때문에 DECLARED이고 나머지 3개는 MEASURED다. PACK21·예시 정책 DRAFT로 official=false, 성능 UNKNOWN이다. PR #27의 과거 실패와 앞선 미실측 기록은 별도로 보존한다.

> 실측 범위 주의: REQ-01의 실제 등록 목록은 기준15/후보16개이고 고정 합집합 요청 자료는 동일하다. 미검토 상태 변경 경로 `/v1/responses`·`/v1/responses/{response_id}/cancel`에는 HTTP를 보내지 않고 기존 TCP 차단 근거를 사용했다. REQ-03은 양쪽 관리·진단 등록5개/시험5개이며 검토한 버전·digest에 한정한 안전 probe를 사용했다. 미검토 이미지·다른 endpoint/입력 형식·GPU·기업 승인은 별도 범위다. CSV의 해당 5행에서 검증·남은 범위·근거만 갱신했으며 A/B/C 등급과 정책 소비 분류는 바꾸지 않았다.

> 3차 기록 당시 구현: [연결 귀속·로그 수집](AUTOMATION_PHASE3.md)에서 REQ-11 `request_binding`과 OBS-09 `log_integrity`를 제품 registry에 연결했다. PACK19/DRAFT·자동연결11개·정책21점검/46경로·adapter0.4.0·declared0.12.0이다. 아래 2차/1차 결과는 과거 기록이며 이번 직접 검증 결과와 구분한다. 직접 CPU에서 새 두 점검과 PLT-01 S/F PASS·PUNKNOWN, 반출/복원/저장 팩 재판정을 확인했다. HTML 줄바꿈 digest는 새 시도02로 보수·재검증했다. 두 제한 프로파일은 기업 신원·SIEM·worker 내부 병렬성·전체77행 완료를 뜻하지 않는다.

> 2차 기록 당시 구현: [자동화 2차](AUTOMATION_PHASE2.md)에서 REQ-07 revision 2의 `template_validation`을 연결해 자동 실행기 9개가 연결됐다. PACK-BASELINE 18/DRAFT·정책 19점검/41경로·adapter 0.3.0·declared/0.11.0 기준이다. 고정 CPU text-chat 프로파일에서 렌더된 실제 token IDs와 소비 prompt_token_ids, 정책 경계와 요청 override를 검증하는 제한 범위다. [별도 설정](../examples/windows-cpu-chat.toml)과 [EXAMPLE/DRAFT 정책](../examples/automation-chat-policy.yaml), [완료 계약](AUTOMATION_PHASE2_PROMPT.md)을 참고한다. 기본 전체 1,781통과·5 skip·18제외, 마지막 오류 직렬화 보수 후 관련 128회귀 통과. 최종 127 경계 확인과 별도 32 반례, REQ-13 private_system·PLT-01의 실제 관측, 반출·복원·저장 팩 재판정은 [직접 검증 기록](AUTOMATION_PHASE2.md#검증-기록)과 [선별 증거](evidence/automation-phase2-20261023.json)를 따른다. 연결 수가 완료 수는 아니다. 성능 UNKNOWN·official=false를 유지한다.
> 아래 1차 직접 결과와 A/B/C 분류의 역사적 수치는 보존한다. REQ-10 하위 runner 소비·OBS-03 예외 dump 등 남은 분기가 새 REQ-07 등록만으로 검증되지는 않는다.

> 1차 기록 당시 구현: [Windows CPU·자동화 1차](AUTOMATION_PHASE1.md). 실행기 연결은 기존 REQ-01·REQ-03·PLT-01·PLT-04·REQ-09와 신규 REQ-10·REQ-13·OBS-03의 8개다. 신규 3개는 제한 관측 프로파일이며 전체 분기 검증 완료를 뜻하지 않는다.
> 이번 코드에서 Windows CPU 제품 명령으로 8개를 실제 실행했다. 기존 5개는 위반 구성에서 모두 FAIL·강화 구성에서 모두 PASS였고, 짧은 관측 시간 구성은 4개 UNKNOWN·PLT-01 PASS였다(원래 보안 기대 14/15 일치, 행렬 exit 1 보존). 신규 3개는 보안 REQ-10 UNKNOWN·REQ-13 FAIL·OBS-03 UNKNOWN이다. 근거와 한계는 위 문서를 따른다. CSV에 구현·검증·남은 범위·근거를 구분했다.
> 아래 A/B/C와 불확실성 표는 2026-10-19 맥 CPU 기준의 가능성 분류 이력이다. Windows 지원 여부·실제 완료 수는 별도이며, 한 실행기 종류로 묶였다는 이유만으로 기존 코드가 모든 행을 실행할 수 있다고 보지 않는다.

> 작성 2026-10-19. 판정·코드 변경 없음. 원본: `packs/PACK-BASELINE/checks/*.yaml`(checklist.json 원문 보존 필드) — 행별 표는 [`automation_matrix.csv`](automation_matrix.csv).

**등급 정의**  A: 맥 Docker + CPU vLLM(+ 로컬 컨테이너·fixture)만으로 실행기·판정을 실험실 위상에서 끝까지 재현 가능 · B: 추가 인프라 필요(GPU/TP≥2, 다중 노드, Ray, 부하 도구, 외부 서비스, 추가 모델 등) · C: 기업 실제 환경(승인·배포 시스템, IdP, 기록 저장소 등)이 있어야 의미 있는 판정 · M: 수동 판단만.

`불확실` 열이 채워진 행은 등급을 단정하지 않았다(사유 기재). 정책 값(예산·기한·허용표)이 필요한 행은 등급과 별개로 `value_policy`로 표시했다 — 정책 값은 POL 입력이며 인프라가 아니다.

## 1. 가능성별 행 수

| 등급 | 행 수 | 그중 불확실 |
|---|---|---|
| A | 47 | 22 |
| B | 14 | 6 |
| C | 16 | 3 |
| M | 0 | 0 |
| 합계 | 77 | 31 |

- A 47행 중 불확실 없이 확정한 A는 **25행**(REQ-01 구현 포함).
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
| http_boundary | 3 | 5 | 0 | 0 | 5 |  |
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
| 1 | REQ-03 (불확실 추가됨, 아래 5절) | http_boundary | REQ-01 실험실·실행기·판정표 형식을 그대로 재사용(경로별 키·역할 대조). 새 연산자 없음. 관리 경로 실측 목록이 5b에서 이미 확보됨 |
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
| REQ-03 | A | (2026-10-19 REQ-03 작업 중 추가) 상태 변경 관리 경로 시험 방식 — /scale_elastic_ep는 검증된 안전 프로브로 해결, /v1/responses/{response_id}/cancel 분류 문제 남음 |
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

> 2026-10-19 갱신: REQ-03 원문 확인 결과 분류(http_boundary, A)는 맞음. 관측 이름을 실제 설계로 고치고 "불확실"을 추가했다(이유: 등록된 상태 변경 관리 경로의 시험 방식이 정해지지 않으면 PASS를 증명할 수 없음).
> 2026-10-19 갱신: PLT-01 원문 확인 결과 분류(container_config, A)는 맞음. 필요한 연산자는 `none_in`이 아니라 `subset_of`·`not_subset_of`·`value_policy`·`when_policy`로 정정. hardened 기동 실패로 "불확실" 추가.
> 2026-10-19 갱신: PLT-01 자동화 완료(hardened PASS·overprivileged/tmp_exec/default FAIL 실측). "불확실" 해제. 자동화 3/77(REQ-01·REQ-03·PLT-01).
