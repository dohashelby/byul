# A·B 남은 일과 계획 (2026-11-08)

기준: `origin/main` d37f4d4a(PR #228 병합)를 `codex/dev1-p1-producers`에 합친 상태. 현황표(`CHECKLIST_VALIDATION_STATUS.md`)·B 인계(`gpu-b/consolidation-20261106/TEAM_HANDOFF.md`, `PHASE1_REVIEW.md`)·공통 생산자 등록(`gpu_common_inputs.SUPPORTED_PRODUCERS`)을 대조했다.
상태표의 DONE은 lab 실측 단계 완료다. 보안 PASS·기업 승인·원 질문 전체 완료가 아니다. 날짜 약속은 하지 않는다.

## 1. 전체 숫자

| 구분 | 점검 |
|---|---:|
| 체크리스트 | 77 |
| update 범위 제외 | 8 |
| 대상 | 69 (A 개발 담당 26 · B GPU 담당 43) |
| A lab 완료 | 8 (REQ-01·11·15·18·19·20, OBS-04·09) |
| A 대상 환경 비적용 | 2 (REQ-04·06) |
| B lab 완료(현황표) | 2 (SUP-04, PLT-09) |
| **남은 점검** | **57** (A 16 · B 41) |

B 자체 분류(11-07): 한정 마감 제안 25 · 제품 증거가 더 필요 18(구성요소·준비 12, 과거 보고만 5, SUP-16). 원 질문 전체 분기 완료는 0이다. B의 상태 파일(`phase1/gpu.yaml`)은 아직 36개를 CPU 개발 남음으로 둔다. 두 분류를 맞추는 일은 B가 자기 파일에서 한다.

## 2. A(개발 담당) 남은 일

### 2-1. 승인·자원만 남은 GPU 실측

| 점검 | 막힌 것 | 준비 상태 |
|---|---|---|
| REQ-14, REQ-21 | AWS G and VT vCPU 할당량 8→48 승인 대기 → 비용 상한 승인 | 하네스·입력 사전 점검 완료(`tp2-run-plan.md`). REQ-21은 L4 P2P가 없으면 UNKNOWN |
| REQ-16 | 멀티모달 모델 선정·내려받기 승인 | 판정 기준 확정·4문맥 사례 준비 |

### 2-2. 기업·runtime 입력 대기 (실행기·측정 경로 준비됨, 값이 없어 UNKNOWN)

REQ-02·03·13·17, PLT-07·10·11·12, OBS-01·05·06·11, LIF-13(release 인가). 요청 목록은 `enterprise-input-requests.md`. runtime이 줄 것: Gateway 기록 봉인·생산자 인증(OBS-01·REQ-02), 수집 상태 feed(OBS-11), 관리 경로 정의(REQ-03), 철회 가능한 자격(PLT-11).

### 2-3. B를 막고 있는 공통 작업(개발26 반환물)

B 인계가 "개발26 공통"으로 남긴 것 중 A가 할 일이다. 등록을 마친 생산자 8종은 B 소비자 연결이 남았고, 나머지는 A 개발이 먼저다.

| 공통 작업 | B 점검 | 상태 |
|---|---|---|
| request-attempt 생산자 | LIF-03·10·12, REQ-12 | 등록됨 → B 소비자 연결·실측 |
| generation-transition 생산자 | LIF-05·06·07·09 | 등록됨 → B 소비자 연결·실측 |
| health-worker·profiler-control 생산자 | OBS-02·03, LIF-14 | 등록됨 → B 소비자 연결·실측 |
| process-device-provider 생산자 | PLT-01·04 | 등록됨(11-05) → B 상태 메모 갱신·소비자 연결 |
| runner-consumption(V1·V2)·supply-reads | REQ-08·09·10, SUP 계열 | 등록됨 → B 소비자 연결 |
| **rank-fault 생산자** | LIF-08 | 미등록. TP2 필요 → A의 TP2 세션에서 함께 GPU 확인 |
| **failed-group-resources 생산자** | LIF-11 | 미등록. TP2 필요 → 같은 세션 |
| **IPC 소유 공통 관측** | PLT-02 | 2026-11-08 반환(`plt02-ipc-ownership.md`, 미등록·GPU 확인 대기). PLT-06 메시지 소비는 별도 |
| **LIF-11 복구 계보 공통 입력** | LIF-11 | 2026-11-08 반환(`lif11-recovery-lineage.md`, 미등록·TP2 실패/재기동 확인 대기) |
| **SUP-09·11 model-entry 지원 계약** | SUP-09·11 | 2026-11-08 반환(`sup09-model-entry.md`). -OO 정상 대조는 라이브러리 지원 범위 결정 대기 |
| **SUP-10 plugin runner 지원 계약** | SUP-10 | 2026-11-08 반환(`sup10-plugin-runner.md`) |
| **SUP-06 worker shard 공통 입력** | SUP-06 | 2026-11-08 반환(`sup06-worker-shard.md`, 미등록·TP2 확인 대기) |
| B 의존 반환 3 | LIF-06 | 실제 요청 admission/attempt→전송→worker/model 및 진행 요청의 reload 부분 실패·선택 CPU parameter A/B/A 복구 연결(`consumption-links-cpu.md`). 전체 weights 원자성·KV kernel/GPU 미확인; 전체 반환 미완료 |
| B 의존 반환 4 | SUP-03 | 실제 Python read→boot config 내용→worker HF config, tokenizer loader→cached 복사→요청 renderer 연결(`consumption-links-cpu.md`). native Rust 읽기·전체 보조물·인가/집행 미확인; 전체 반환 미완료 |
| B 의존 반환 8 | REQ-09·10 | 실제 OPT worker/model logits/grammar·Sampler 7요청16단계 및 랜덤 소형 Qwen2-VL raw image→실제 MM worker→모델 embedding 4요청8단계(`consumption-links-cpu.md`). 선정 2B·V2·GPU 미확인; 전체 반환 미완료 |
| 계약 합의 P2-Q1(사건 상한)·P2-Q2(V2 logit bias) | #196 소비 전반 | B 답변 대기 |

### 2-4. 정리 작업

- PR #223은 이미 병합됐다. 그 뒤 커밋 3개(17b6bde OBS-09·LIF-13 보수, 6e5d894 현황표 팩33 연결, e2c1e7e TP2 준비)와 이번 main 병합을 새 PR로 올린다.
- 플랫폼 이슈 dohashelby/byul#226(OBS-05 입력)과 공통 변환(화면 입력 → `verification.values`)은 플랫폼 담당.

## 3. B(GPU 담당) 남은 일 (B 인계 요약)

| 묶음 | 점검 | 다음 일 |
|---|---|---|
| 진행 중 실측 | SUP-03(R3) | R3 health·guest·제품·정리 결과 검증 |
| 준비 실패·재개 조건 | SUP-05·07·12·13·15·16 | 실패 원인별 검증 가능한 변경 뒤 재실행 |
| 공통 생산자 소비자 연결 | LIF-03·05·06·07·09·10·12·14, OBS-02·03, PLT-01·04, REQ-12 | 등록 생산자 기록을 B 소비자·판정에 연결하고 GPU 실측 |
| A 공통 작업 대기 | PLT-02·06, SUP-09, LIF-08·11 | 2-3의 A 반환물 뒤 |
| TP2 분기 | LIF-08·11, SUP-06 | A의 g6.12xlarge 세션과 묶을 수 있음 |
| 과거 원본 확인 | LIF-08·12·16, OBS-02, SUP-08 | 당시 archive·Ref 보유자 대조 |
| 기업 입력 | PLT-03·05·08, SUP-04·REQ-05·07 일부 | 정책·신원·TLS·승인 목록 |
| 실제 소비 증거 보강 | REQ-08·09·10, SUP-06·10·11 | runner/loader 실제 소비 생산자 연결 |

## 4. 팀 공통

- 정책 확정자·팩 검토자 지정(지정 전 모든 결과 `official=false`), 점검별 `impact` 값.
- 기업 입력 전달(A 목록 + B의 정책·신원·TLS 입력).
- runtime 생산자 봉인·인증 합의.

## 5. 제안 순서

1. **지금(A, 자원 불필요):** 새 PR(병합 반영 + 3커밋) → PLT-02·06 IPC 소유 공통 관측, SUP-09 model-entry 지원 개발. 그동안 B는 등록 생산자 소비자 연결과 SUP 준비 실패 재개.
2. **할당량 승인 뒤(A+B 한 세션):** g6.12xlarge에서 REQ-14·REQ-21(A) + rank-fault·failed-group-resources 생산자 GPU 확인(A) + LIF-08·11·SUP-06 TP2 분기(B). 세션을 나눌수록 기동·비용이 중복된다. 비용 상한은 B 분기 포함으로 다시 산정해 승인받는다.
3. **승인 뒤:** REQ-16 멀티모달.
4. **입력이 오는 대로:** 기업·runtime 입력 점검을 실측(실행기 준비 완료, 입력별 단일 L4로 충분).
5. **팀:** 확정자·검토자 지정 뒤 공식(official) 판정 재실행.
