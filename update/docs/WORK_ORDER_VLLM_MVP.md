# BYUL UPDATE 작업지시서 3차 — vLLM MVP(1차 완성)

- 작성: 2026-11-12
- 기준: main `e2219176`(PR #240 B 입력 검증·SUP-16 인계, PR #246 SUP-08 코드 판본 선택 GPU PoC 병합 직후)
- 대체: [작업지시서 2차](WORK_ORDER_VLLM_1.md)의 남은 항목을 이 문서로 옮긴다. 2차의 A·B 담당, 파일 소유, 점검 배정(1단계 분담 2026-11-04)은 그대로 쓴다.
- 근거: `../CLAUDE.md`·`../AGENTS.md`, [11-09 결정](dev-1/user-decisions-20261109.md), [11-08 결정](dev-1/user-decisions-20261108.md), `../DECISIONS.md`, [현황표](CHECKLIST_VALIDATION_STATUS.md)

## 0. MVP 정의

**MVP는 기업이 vLLM 0.31.0을 0.32.0으로 올릴 때 guard 하나로 업데이트 보안 검증을 끝까지 받을 수 있는 상태다.** 2차 지시서의 1차 완성 기준을 그대로 MVP 완료 기준으로 쓴다.

| # | 완료 기준 | 확인 방법 |
|---|---|---|
| 1 | update 범위 69개를 vLLM 0.31.0 GPU lab에서 제품 경로(`guard select → run → judge → report`)로 실행한다. 새 정본 팩 PACK-BASELINE@34를 쓴다. | 점검마다 EVD·RES·RPT 정확 Ref, `docs/evidence/` 공개 요약, 현황표 |
| 2 | 1차 제외 10개(REQ-02, OBS-01·06·11, PLT-07·08·10·11·12, OBS-09 기업 수집기 범위)는 정본 팩에서 실행하고 UNKNOWN과 원인을 남긴다. | RES의 UNKNOWN 사유 |
| 3 | 대상 비적용 2개(REQ-04·06)는 정본 선정에서 비적용 근거를 남긴다. | 선정 기록 |
| 4 | vLLM 0.32.0 고정값과 지원표 행을 더하고 `guard update`로 0.31.0과 비교한다. 일부러 넣은 회귀 사례 하나가 `REGRESSED`로 나온다. | 비교 RPT, 소스 diff 보고 |
| 5 | 정책 확정자(사용자)와 팩 검토자를 거쳐 `official=true` RPT를 만든다. | 공식 RPT Ref |

MVP에서 허용하는 것과 허용하지 않는 것:
- **허용:** 관측할 수 없는 분기, 기업 입력이 없는 분기, 1차 제외 범위의 UNKNOWN(원인 기록 필수). 보안 FAIL도 실제 결과면 그대로 둔다. MVP는 "69개가 모두 PASS"가 아니라 "69개가 모두 제품 경로로 정직한 결과를 낸다"이다.
- **불허:** CPU·대역 결과를 GPU 실측으로 올리기, 실행하지 않은 점검을 결과로 세기, 판정 기준을 결과에 맞춰 고치기, 다른 판본 결과를 옮겨 쓰기.

MVP 밖(후속): SGLang 생산자·실측, TensorRT-LLM, 11-08 결정 6-2~6-4 기업 입력(2차 과제), 2차 지시서 A08(runtime·platform 제안)·A09(CPU 실행기 provider 이동)·A10(작업 폴더 정리), `kubernetes_http.py`의 SUP-08 부분 분리.

## 1. 현재 상태 (main `e2219176`)

| 담당 | 현황표 상태 | 점검 수 |
|---|---|---:|
| A | lab 완료 | 8 |
| A | 기업 입력 대기 | 12 |
| A | GPU 확인만(TP2) | 3 |
| A | 측정 남음 | 1 |
| A | 대상 비적용 | 2 |
| B | CPU 개발 남음 | 30 |
| B | lab 완료 | 6 |
| B | 측정 남음 | 7 |

현행 팩(@33) GPU 실행 기록은 77개 중 MEASURED 11·EXAMPLE 14·DECLARED 1·중단 1이고 50개는 미측정이다. **@34가 77개 정의를 모두 바꾸므로 지금까지의 실측은 모두 옛 정의 기록이 된다. 정본 실측은 @34 이후에만 센다.**

두 PR로 들어온 것:
- #240: REQ 수집의 엔진별 규칙을 adapter 프로필로 옮김, 입력 준비를 제품(`guard.executors.runner_inputs`)에 포함, B03 공급 입력·PLT-01 준비 CLI, B04 TP2 입력 계획, SUP-16 회수·root client CPU 준비. 현황 정정으로 REQ-07·PLT-03·PLT-05·LIF-02는 GPU 정본 실측 전까지 "측정 남음"이다.
- #246: SUP-08 코드 판본 선택 관측(동적 모듈 목록과 import 표식), `input_validation` 순차 Pod 전환, `kubernetes_http.py`의 입력 계열 판본 비교를 지원표 조회로 전환, vLLM 0.7.0·0.30.0 지원표 행(SUP-08 코드 선택 전용). L4 PoC에서 0.7.0 PASS → 0.30.0 FAIL `REGRESSED`, 0.31.0 거부 PASS(모두 DRAFT/EXAMPLE, `official=false`).

2차 지시서 이후 main에 들어온 A 작업은 없다. A01~A07이 모두 남아 있다.

## 2. 단계와 순서

| 단계 | 내용 | 시작 조건 |
|---|---|---|
| 0. GPU 없이 (지금) | M-A1~A7, M-B1~B6 | 바로 |
| 1. TP2 세션 J01 | REQ-14·16·21, LIF-08·11, SUP-06, 생산자 확인·등록 | M-A2·A3·A4 병합, AWS 할당량(결정 6) |
| 2. 정본 실측 J02 | J01 외 63개, @34, 0.31.0 | M-A4·A7, M-B2~B6 입력, J01 생산자 등록, 비용 승인(결정 1) |
| 3. 두 판본 비교 J03 | 69개를 0.31.0·0.32.0에서 실행, 회귀 사례 `REGRESSED` 확인 | M-A5·A6 병합, J02 완료, 비용 승인(결정 2) |
| 4. 공식 재판정 J04 | `official=true` RPT | J01~J03, 정책 CONFIRMED, 팩 검토 |

**가장 긴 경로는 M-A4(@34) → J02 → J03 → J04다.** @34가 늦으면 모든 GPU 세션이 밀린다. M-A6(0.32.0 고정값)은 단계 0~2와 나란히 한다. GPU를 켜 둔 채 CPU 준비를 기다리지 않는다.

## 3. A 작업 (개발 담당)

### M-A1. 규칙·문서 정리 (작게, 먼저)
- 2차 지시서 A01을 마친다: 11-08·11-09 결정의 "공통 입력 `/2`" 내용을 `/4`로 옮긴다(`docs/dev-1/user-decisions-20261109.md`, `docs/dev-1/gpu-common-inputs-196.md`, `CLAUDE.md`·`AGENTS.md` 2절). 내용은 그대로 두고 번호만 고친다.
- `CLAUDE.md`·`AGENTS.md` 5절의 "현재 작업지시서" 링크를 이 문서로 바꾸고, 2차 지시서는 요점을 PROGRESS에 남기고 지운다(7.6절).
- 결정 5(검토 기록 규칙)의 결과를 6절 소스 고정 목록 규칙에 반영한다.
- 완료: 문서 PR 병합.

### M-A2. 공통 입력 계약 `/4` 구현 (2차 A02)
- 반복 행 요약(값이 바뀌면 끊고 첫·끝 행은 원 행, 상한 4,096), V2 sampler `logit_bias` 비교, KV 엔진 고유 단위+단위 필드, `limit_enforced` 모든 한도 집행+방식 필드, 생산자 신원에 공통 감싸기 기반 파일 포함.
- 생산자 변환(`collectors/gpu_input_producers.py` 등)과 B 소비자 형식을 함께 맞춘다(M-B1).
- 완료: `/1`·`/4` 직접 시험, B 소비자가 두 판본을 모두 읽음, PR 병합. 생산자 SHA가 바뀌므로 등록은 J01에서 한다.

### M-A3. rank-fault 생산자 (LIF-08, 2차 A03)
- 계약 종류 `rank-fault`(`collectors/gpu_common_inputs.py`)와 판정 쪽(`executors/rank_failure_contract.py`)은 있고 엔진 안 관측기·변환이 없다.
- 사건: `fault_applied`·`fault_released`·`detected`·`client_delivered`·`recovered`.
- 완료: CPU 직접 시험, PR 병합. GPU 확인·등록은 J01.

### M-A4. 새 정본 팩 PACK-BASELINE@34 (2차 A04, 최우선)
- 77개 점검의 `impact`를 11-08 초안(HIGH 33·MEDIUM 38·LOW 6)대로 넣는다. 지금은 77개 모두 `impact: null`이다.
- SUP-11 `-OO` 지원 범위 밖 표기를 넣는다(판정 규칙은 그대로).
- 확정 정책 값 5개(REQ-03·13·17, OBS-05, LIF-13, 11-08 결정 6-1)를 정본 실측용 정책 파일(`verification.values`)에 넣는다.
- **SUP-08 정의:** #246의 `PACK-CODE-SELECTION`(SUP-08만 revision 5, `approved_code_revisions` 정책 키, 중첩 필드)을 정본 SUP-08 정의로 올릴지 결정 7에 따라 반영한다.
- REQ-12의 실제 질문에 맞는 사건·점검 선언·투영 함수를 B에 반환한다(#240 인계의 A 반환 요청).
- 과거 팩·결과는 소급해서 바꾸지 않는다.
- 완료: 팩 검토(결정 5), 현황표 재생성, PR 병합.

### M-A5. 남은 vLLM 판본 고정 옮기기 (2차 A05의 남은 부분)
- #246이 `kubernetes_http.py`의 입력 계열 8개 profile 비교를 `require_transport` 조회로 바꿨다. 남은 고정 비교:
  - `src/guard/runtime/kubernetes_http.py`의 `ROUTE_AUTHENTICATION_PROFILE` 분기(`engine.name!='vllm'` 비교)
  - `src/guard/runtime/kubernetes_startup_binding.py`(`engine_version != '0.31.0'`)
  - `src/guard/runtime/kubernetes_worker_readiness.py`(`env.engine.version != '0.31.0'`)
- 세 파일의 bytes는 수집기 신원과 `runtime/reviewed_http_contract.py` 고정값에 쓰인다. 고정값과 소스 목록을 같은 PR에서 갱신하고 검토 기록을 넣는다(결정 5).
- 완료: 0.31.0 동작 동일, 관련 B 시험 통과, PR 병합. **0.32.0 비교(J03) 전에 필수다.**

### M-A6. vLLM 0.32.0 판본 지원 (2차 A06)
- 판본 고정값 갱신 도구: 두 판본 이미지에서 감싼 함수 코드를 함수 단위로 비교해, 같으면 고정값 이월을 제안하고 다르면 검토 목록을 낸다.
- 0.32.0 GPU 이미지 digest를 고정한다(결정 2). 지금 `adapters/vllm.py`의 0.32.0은 CPU 이미지(`vllm/vllm-openai-cpu`)만 있다.
- 관측기 `adapters/vllm_*.py`·`adapters/vllm_runner_capture.py`에 0.32.0 행, `engine_profiles.json`에 0.32.0 행을 더한다. 관측 의미가 바뀐 함수는 다시 검토한다. 고정값은 실제 이미지 설치 bytes와 대조하기 전까지 후보다.
- `adapters/assets/vllm_change_rules.yaml`로 0.31.0→0.32.0 소스 diff 보고를 확인한다(0.31→0.32는 정식 릴리스라 U1상 전체 시험).
- 회귀 사례 하나를 설계해 승인받는다(결정 3).
- 완료: 0.32.0 지원 profile 조회 성공, 갱신 도구 시험, PR 병합.

### M-A7. A 담당 점검 정본 실측 입력과 세션 계획 (2차 A07)
- 대상 A 26개: lab 완료 8개 재실행, 확정 값 5개 재측정, 1차 제외 8개 실행·UNKNOWN 기록, TP2 3개(J01), 비적용 2개 근거 기록.
- J01·J02 실행 입력(target·prepared·정책 파일)을 `tools/gpu_a_run_inputs.py`, `tools/gpu_a_aws_lab.py`, `docs/dev-1/tp2-run-plan.md`로 준비한다. #246이 더한 판본·모델·코드 판본 입력을 0.31.0 기본값으로 쓴다.
- **J02 세션 계획:** 같은 이미지 digest·모델·기동 설정의 점검을 한 번의 기동으로 묶는다(상태 독립성 확인 뒤). 묶음·순서·예상 시간·예상 비용을 표로 만들어 결정 1의 근거로 보고한다. B 입력(M-B2)과 한 계획으로 합친다.
- 완료: 입력 사전 점검 통과, 세션 계획 보고.

## 4. B 작업 (GPU 실증 담당)

### M-B1. 공통 입력 판정 연결 마감 (2차 B02)
- 대상: LIF-03·05·06·07·09·10·11·12·14, REQ-08·09·10·12, OBS-02·03, PLT-01·02·04, SUP-03·06.
- #240이 기존 공통 입력의 소비 경로와 REQ-08 native 거부 소비를 연결했다. 남은 것은 `/4` 소비(M-A2), REQ-12 점검 선언 반영(M-A4)이다.
- `/1`~`/4`를 모두 읽고, 등록되지 않은 생산자 기록으로 PASS를 만들지 않는다.
- 완료: 직접 시험, PR 병합.

### M-B2. CPU 개발 남은 30개 마감과 정본 실측 입력 (2차 B03)
- 묶음별로 정본 실측 입력을 준비하고 입력 사전 점검을 통과시킨다.
  - 공급·기동: SUP-07·09·10·11·12·15, LIF-01, 측정 남음 SUP-05·PLT-03·PLT-05·LIF-02
  - 입력·샘플링·템플릿: SUP-03, REQ-09·10, 측정 남음 REQ-07·SUP-13 (SUP-08은 M-B4)
  - 수명 주기: LIF-03·04·05·06·07·09·10·12·14·16
  - 플랫폼: PLT-02·06, REQ-05
  - 관측: OBS-02·03, REQ-12
  - lab 완료 6개(SUP-04, REQ-08, PLT-01·04·08·09): @34에서 정본 재실행 입력
- CPU 개발을 마감한다는 것은 "GPU에서 제품 경로로 실행되어 EVD를 남기는 실행 경로"까지다. 관측할 수 없는 분기는 UNKNOWN과 원인을 남긴다.
- `docs/phase1/gpu.yaml`을 실제 상태로 갱신한다. CPU 결과만으로 DONE을 올리지 않는다.
- 완료: 묶음별 입력 사전 점검 통과.

### M-B3. TP2 분기 입력 확정 (LIF-08·11, SUP-06, 2차 B04)
- #240의 TP2 입력 계획을 새 main 기준으로 확인한다. 정상·장애·복구 대조와 시간표를 J01 전에 확정한다.
- 필요한 A 반환: LIF-08 peer/rank 제어, LIF-11 실패 group 회수·복구, SUP-06 rank별 required/assigned/loaded 지원 범위와 원자료.

### M-B4. SUP-08 정본화 (#246 후속)
- #246 PoC 관측(동적 모듈 목록+import 표식)을 @34 SUP-08 정의(M-A4, 결정 7)에 맞춰 0.31.0 정본 실측 입력으로 만든다. 모델·fixture는 PoC의 Phi-3 vision 고정 snapshot과 무해한 승인/비승인 processor를 재사용할 수 있다.
- 0.7.0·0.30.0 지원표 행은 SUP-08 코드 선택 전용이며 정본 실측에 쓰지 않는다.
- PoC 결과(DRAFT/EXAMPLE, `official=false`)를 정본 결과로 옮기지 않는다.

### M-B5. SUP-16 실제 수집 완성
- #240 인계 기준: GB12 회수 자료는 SHA·CRC 확인까지다. 정상·부분 파일·복구의 실제 수집과 제품 판정이 남았다. journal `BOUND_REACHED`, 부분 파일 후보 없음, install·첫 emit·Writer 원인 미확정 상태를 해소한다.
- 원 R8 전체 FAIL과 읽기 수락 UNKNOWN은 유지한다. 회수 성공을 PASS로 세지 않는다.
- 완료: CPU 준비 → J02에서 실제 수집.

### M-B6. 기업 입력이 필요한 B 점검 (2차 B05)
- PLT-08(1차 제외): 정본 팩 실행, UNKNOWN+원인.
- PLT-03·05, SUP-04, REQ-05·07의 기업 입력 분기: EXAMPLE 정책으로 실행하고 미입력 분기는 UNKNOWN으로 남긴다.

### M-B7. 두 판본 비교 원인 기록 (J03 뒤, 2차 B07)
- B 담당 점검의 0.31.0·0.32.0 비교 결과(`NO_CHANGE`·`REGRESSED`·`NOT_COMPARABLE` 등)와 원인을 기록한다.

## 5. 합동 세션

### J01. TP2 GPU 세션
- g6.12xlarge(L4 4장 중 2장), 한 번 기동, EC2 상한 $20(11-08 승인, EBS 별도).
- 범위: A REQ-14·16·21(REQ-16 모델 Qwen2-VL-2B-Instruct는 EC2 안에서 내려받기), B LIF-08·11·SUP-06, `/4` 생산자 8종·rank-fault·failed-group-resources·ipc-ownership·worker-shard-load GPU 확인과 등록.
- 마감: 증거 반출 SHA 대조, 인스턴스 유형 g6.2xlarge로 복구, stopped 확인. 상한에 닿으면 그 자리에서 멈추고 정리한다.

### J02. 정본 실측 세션 (단일 L4)
- J01 외 63개, PACK-BASELINE@34, vLLM 0.31.0. 계획은 M-A7·M-B2의 합본을 따른다.
- 점검마다 EVD·RES·RPT 정확 Ref를 `docs/evidence/` 공개 요약과 현황표에 연결한다.

### J03. 두 판본 비교 세션
- 69개를 0.31.0·0.32.0 두 이미지에서 실행하고 회귀 사례로 `REGRESSED`를 확인한다. 같은 엔진 재사용으로 두 이미지 비교를 대신하지 않는다.

### J04. 공식 재판정
- 정책 확정자(사용자)가 정책을 CONFIRMED로 확정하고 팩 검토를 마친 뒤, J01~J03 결과로 `official=true` RPT를 만든다.
- MVP 완료 보고: 공개 실측 요약, 현황표, 비교 RPT, 공식 RPT의 정확 Ref.
- 외부 의존: RPT를 화면에 보여 주는 verification-result 변환은 platform 담당(#226)이다. MVP 시연 전에 platform 담당에게 공식 RPT 형식을 넘긴다.

## 6. 사용자 결정이 필요한 것

| # | 결정 | 필요한 때 | 권장 |
|---|---|---|---|
| 1 | J02 비용 상한 | J02 전 | M-A7 세션 계획 보고 뒤 승인 |
| 2 | 0.32.0 GPU 이미지 digest와 J03 비용 상한 | M-A6·J03 전 | 0.32.0 공식 GPU 이미지의 digest를 고정 |
| 3 | 회귀 사례 방식 | J03 전 | 후보 쪽에만 보안 설정을 약하게 하는 방식. #246처럼 SUP-08 코드 판본 선택을 쓰는 안도 가능(0.32.0 자체 차이가 아니어도 됨을 명시) |
| 4 | 공통 입력 번호 `/4` | M-A1 | `/2`·`/3`은 이미 사용 중이라 `/4`로 확정 |
| 5 | 소스 검토 기록·팩 검토 담당 | M-A1, 모든 제품 소스 PR | 11-12 결정은 #240·#246만 B 검토를 면제했다. MVP까지 계속 면제할지, B 또는 대리 검토자를 둘지 정한다. 면제하면 `reviewed_by`에 면제를 밝히는 방식을 규칙으로 적는다 |
| 6 | TP2 할당량(G and VT vCPU 8→48) | J01 전 | 승인 상태 확인. 승인이 늦으면 J02·J03을 먼저 하고 TP2 6개는 J01 뒤 합친다 |
| 7 | SUP-08 정본 정의 | M-A4 | #246의 코드 선택 정의(revision 5)를 @34 정본에 넣는다 |

## 7. 점검별 배정표 (69개)

현재 상태는 main `e2219176`의 현황표 기준이다. "M-B2" 등은 이 문서의 작업 번호다.

| 점검 | 담당 | 현재 상태 | MVP까지 할 일 | 세션 |
|---|---|---|---|---|
| SUP-03 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| SUP-04 | B | lab 완료 | @34에서 정본 재실행 | J02 |
| SUP-05 | B | 측정 남음 | 정본 실측 입력 확정(M-B2) | J02 |
| SUP-06 | B | CPU 개발 남음 | B02 판정 연결 + M-B3 TP2 입력, TP2 실측 | J01 |
| SUP-07 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| SUP-08 | B | CPU 개발 남음 | M-B4: 코드 선택 관측(#246)을 @34 SUP-08 정의로 반영, 0.31.0 정본 실행 | J02 |
| SUP-09 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| SUP-10 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| SUP-11 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| SUP-12 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| SUP-13 | B | 측정 남음 | 정본 실측 입력 확정(M-B2) | J02 |
| SUP-15 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| SUP-16 | B | 측정 남음 | M-B5: 정상·부분 파일·복구 실제 수집 경로 완성, 정본 실행 | J02 |
| REQ-01 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-02 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| REQ-03 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 재측정 | J02 |
| REQ-04 | A | 대상 비적용 | 정본 선정에서 비적용 근거 기록 | - |
| REQ-05 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| REQ-06 | A | 대상 비적용 | 정본 선정에서 비적용 근거 기록 | - |
| REQ-07 | B | 측정 남음 | 정본 실측 입력 확정(M-B2) | J02 |
| REQ-08 | B | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-09 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| REQ-10 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| REQ-11 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-12 | B | CPU 개발 남음 | A: 점검 선언·투영 함수 반환 → B02 판정 연결 | J02 |
| REQ-13 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 재측정 | J02 |
| REQ-14 | A | GPU 확인만 | TP2 실측 | J01 |
| REQ-15 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-16 | A | GPU 확인만 | TP2 실측 | J01 |
| REQ-17 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 재측정 | J02 |
| REQ-18 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-19 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-20 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| REQ-21 | A | GPU 확인만 | TP2 실측 | J01 |
| PLT-01 | B | lab 완료 | @34에서 정본 재실행 | J02 |
| PLT-02 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| PLT-03 | B | 측정 남음 | 정본 실측 입력 확정(M-B2) | J02 |
| PLT-04 | B | lab 완료 | @34에서 정본 재실행 | J02 |
| PLT-05 | B | 측정 남음 | 정본 실측 입력 확정(M-B2) | J02 |
| PLT-06 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| PLT-07 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-08 | B | lab 완료 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-09 | B | lab 완료 | @34에서 정본 재실행 | J02 |
| PLT-10 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-11 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| PLT-12 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| LIF-01 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| LIF-02 | B | 측정 남음 | 정본 실측 입력 확정(M-B2) | J02 |
| LIF-03 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-04 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| LIF-05 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-06 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-07 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-08 | B | CPU 개발 남음 | A03 rank-fault 생산자 + B02 판정 연결 + TP2 실측 | J01 |
| LIF-09 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-10 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-11 | B | CPU 개발 남음 | B02 판정 연결 + M-B3 TP2 입력, TP2 실측 | J01 |
| LIF-12 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-13 | A | 기업 입력 대기 | 확정 정책 값(11-08 6-1)으로 재측정 | J02 |
| LIF-14 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| LIF-16 | B | CPU 개발 남음 | CPU 개발 마감 + 실측 입력(M-B2) | J02 |
| OBS-01 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| OBS-02 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| OBS-03 | B | CPU 개발 남음 | B02 판정 연결 + 실측 입력(M-B2) | J02 |
| OBS-04 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| OBS-05 | A | 측정 남음 | 확정 정책 값(11-08 6-1)으로 재측정 | J02 |
| OBS-06 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |
| OBS-09 | A | lab 완료 | @34에서 정본 재실행 | J02 |
| OBS-11 | A | 기업 입력 대기 | 정본 팩 실행, UNKNOWN+원인 기록(1차 제외) | J02 |

## 8. 공통 규칙 (변경 없음, 요약)

- 엔진·판본에 묶이지 않는다. 새 코드에 `engine.name != 'vllm'`·`version != '0.31.0'` 비교를 쓰지 않고, 판본 지원은 `engine_profiles.json`과 provider로 정한다.
- 제품 소스를 바꾸면 `uv run python tools/verify_req10_consumption.py --write`로 소스 목록을 다시 만들고 검토 기록을 넣는다(결정 5). 나중에 병합하는 쪽이 다시 만든다.
- 전체 기본 시험은 PR의 Linux CI(CPython 3.12.3)로 확인한다. 로컬에서는 바꾼 부분의 직접 시험과 `tools.checklist_status --check`만 한다.
- 현황표 DONE은 lab 단계 완료이고 보안 PASS·기업 승인이 아니다. CPU·대역 결과를 GPU 실측으로 올리지 않는다.
- GPU는 준비된 시험 묶음과 비용 승인 범위에서만 쓰고, 세션마다 정리와 stopped 확인을 기록한다.
- update 작업에서 `../runtime/`은 고치지 않는다. runtime CI의 시간 민감 시험 실패(예: `test_downstream_deadline.py`)는 runtime 담당에게 넘긴다.
