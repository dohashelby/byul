# BYUL UPDATE A·B 작업지시서 2차 — vLLM 1차 완성

- 작성: 2026-11-09
- 기준: main `3bd6a995`(PR #241 병합 직후)
- 근거:
  - [2026-11-09 사용자 결정](dev-1/user-decisions-20261109.md)
  - [2026-11-08 사용자 결정](dev-1/user-decisions-20261108.md)
  - `../CLAUDE.md`·`../AGENTS.md`
- A는 개발 담당(dev-1), B는 GPU 실증 담당(gpu)이다. 점검 담당은 1단계 분담(2026-11-04, 실행기 파일 소유)을 그대로 쓴다.
- B는 기존 [BYUL A·B 작업지시서]의 GPU 실증을 이어서 하는 중이다. 이 지시서는 B의 진행 중인 일을 멈추지 않는다(B01).

## 0. 목표와 완성 기준

**목표: vLLM 대상 1차 완성.** 다음을 모두 만족하면 1차 완성이다.

1. **69개 점검 실측**
   - update 범위 69개를 vLLM 0.31.0 GPU lab에서 실측한다.
   - 제품 경로(`guard select → run → judge → report`)로 돌리고, 새 정본 팩 판본(PACK-BASELINE@34, 영향도 반영)으로 실행한다.
   - 점검마다 EVD·RES·RPT의 정확한 Ref를 남긴다.
2. **1차 범위에서 뺀 10개**
   - 대상: REQ-02, OBS-01·06·11, PLT-07·08·10·11·12, OBS-09 기업 수집기 범위
   - 실행 경로를 준비해 정본 팩에서 실행하고, UNKNOWN과 원인을 기록한다.
   - PASS·NOT_APPLICABLE로 바꾸지 않는다.
3. **대상 비적용 2개**(REQ-04·06): 정본 선정에서 비적용 근거를 기록한다.
4. **두 판본 비교**
   - vLLM 0.32.0 고정값을 만들고 0.31.0과 비교한다(`guard update`).
   - 일부러 넣은 회귀 사례 하나로 `REGRESSED` 탐지를 확인한다.
5. **공식 재판정**: 정책 확정자(사용자)와 팩 검토자(B)를 거쳐 `official=true` RPT를 만든다.

범위 밖:
- SGLang 생산자·설치기·실측. SGLang은 구조 준비까지만 한다(결정 4-3).
- TensorRT-LLM 작업.
- platform·runtime 코드(runtime은 update 작업에서 고치지 않는다).
- 과거 팩 원본 대조. 과거 기록은 이력으로 보존하고, 현행 팩 실측으로 대신한다.

## 1. 공통 규칙

- **제1원칙: 엔진·판본에 묶이지 않는다.**
  - 새 코드에 `engine.name != 'vllm'`, `version != '0.31.0'` 같은 비교를 쓰지 않는다.
  - 엔진·판본 지원은 판본 profile 목록(`adapters/assets/engine_profiles.json`, `adapters/engine_profiles.py`)과 엔진 provider(`adapters/engine_provider.py`)가 정한다.
  - 0.32.0 지원도 고정값과 profile 행을 더하는 방식으로 한다.
- **병합 규칙(새로 생김, PR #241부터)**
  - 대상: 제품 소스(`update/src/guard`, `update/packs`, `update/uv.lock`, `update/pyproject.toml`, `update/reference/framework`)나 B 도구 3개(`tools/b1_integration_cpu.py`, `tools/b_lif07_native_capture.py`, `tools/b_lif07_readiness.py`)를 바꾸는 PR
  - 절차:
    1. `uv run python tools/verify_req10_consumption.py --write`로 목록을 다시 만든다.
    2. diff를 검토한다.
    3. **B가 병합 직전 최종 목록 SHA로 `tools/reviewed_manifest_receipts.json`에 검토 기록 1건을 넣는다.**
    4. CI를 확인한다.
  - 작성자는 검토 기록을 넣지 않는다.
  - `tools/verify_req10_consumption.py`(게이트) 바이트가 바뀌면 B가 다시 검토하고 `MANIFEST_GATE_SHA256`을 갱신한다.
- **생산자 등록**
  - `/1` 기록은 `collectors/gpu_common_inputs.py`의 `SUPPORTED_PRODUCERS`에 등록한다.
  - `/2` 이상은 `collectors/producer_qualifications.json`의 자격 receipt로 등록한다.
  - 둘 다 실제 이미지·GPU 확인 뒤 독립 검토자가 넣는다.
- **시험**
  - 전체 기본 suite는 PR CI(Linux, CPython 3.12.3)로 확인한다.
  - 로컬에서는 바꾼 부분의 직접 시험만 CPython 3.12.3으로 돌린다.
- **파일 소유**
  - 개발 담당(A)은 core·models·judges·registry·cli·packs·공통 계층·현황 도구를 맡는다.
  - GPU 담당(B)은 자기 43개 점검의 실행기 파일을 맡는다.
  - 상대 담당 파일을 고치면 PR에 적고 그 담당의 검토를 받는다.
- **결과 표기**
  - 현황표의 DONE은 lab 단계 완료다. 보안 PASS·기업 승인이 아니다.
  - CPU·대역 결과를 GPU 실측으로 올리지 않는다.
  - 실행하지 않은 결과를 만들지 않는다.

## 2. 현재 상태 (main `3bd6a995`)

| 담당 | 현황표 상태 | 점검 수 |
|---|---|---:|
| A | lab 완료 | 8 |
| A | 기업 입력 대기 | 12 |
| A | GPU 확인만 | 3 |
| A | 측정 남음 | 1 |
| A | 대상 비적용 | 2 |
| B | CPU 개발 남음 | 36 |
| B | lab 완료 | 2 |
| B | 기업 입력 대기 | 1 |
| B | 측정 남음 | 4 |

이미 들어온 것(#239·#241):
- 엔진 provider와 판본 profile 목록
- 실행기 `vllm`·`0.31.0` 비교 27개 파일 중 24개 제거
- 실제 소비 연결(LIF-06·SUP-03·REQ-09·REQ-10)
- 공통 입력 `/2`(요청 단위 소비 기록)와 `/3`(actor 배치)
- 환경 변환 `guard-byul-input-import/2`
- 검토 기록 기반 소스 검사, 엔진 식별 공용 계약

등록된 생산자는 `/1` 8종이다.

**모든 점검을 새 팩에서 다시 돌려야 한다.** 영향도는 점검 정의 파일 77개 모두의 `impact` 칸에 들어간다. 그래서 새 팩 판본(@34)을 만들면 77개 정의가 다 바뀌고, 지금까지의 실측은 모두 옛 정의 기록이 된다. 이 때문에 A04(새 팩)가 정본 실측(J01·J02)보다 먼저다.

## 3. 단계와 순서

| 단계 | 내용 | 시작 조건 |
|---|---|---|
| 0. 지금(GPU 없이) | A01~A06·A08, B01~B04·B06 | 바로 |
| 1. TP2 GPU 세션 | J01(REQ-14·16·21, LIF-08·11, SUP-06, 생산자 확인·등록) | A02·A03·A04 병합, AWS 할당량 승인 |
| 2. 정본 실측 | J02(나머지 63개) | A04·A07 입력, B03 입력, 생산자 등록 |
| 3. 두 판본 비교 | J03(0.31.0 대 0.32.0) | A05·A06, J02 |
| 4. 공식 재판정 | J04 | J01~J03 |

A06(0.32.0 지원)은 단계 0~2와 나란히 진행한다. GPU를 켜 둔 채 CPU 준비를 기다리지 않는다.

## 4. A 작업 (개발 담당)

### A01. 규칙 문서 정리 (작게, 먼저)
- `CLAUDE.md`·`AGENTS.md` 6절(소스 고정 목록)에 1절의 병합 규칙(B 검토 기록)을 넣는다.
- **계약 판본 번호를 정리한다.**
  - 11-08·11-09 결정의 "공통 입력 `/2`" 내용(반복 행 요약, V2 logit bias, KV 단위, 한도 집행 방식, 생산자 신원)은 다음 빈 번호 `/4`로 한다.
  - `/2`·`/3`은 이미 A 설계 보수가 다른 의미로 쓴다.
  - `docs/dev-1/user-decisions-20261109.md`, `docs/dev-1/gpu-common-inputs-196.md`, `CLAUDE.md`·`AGENTS.md` 2절의 번호를 고친다(내용은 그대로).
- `docs/dev-1/remaining-plan-20261108.md`는 이 지시서로 대체됐다고 표시하거나 지운다.
- 완료: 문서 PR 병합.

### A02. 공통 입력 계약 `/4` 구현
- 내용(결정 그대로):
  - 반복 `stage`·`gpu_fence`·`kv_owned`·`consumed`·`worker_state` 행 요약(값이 바뀌면 끊고, 첫·끝 행은 원 행, 상한 4,096 유지)
  - V2 sampler `logit_bias` 비교
  - KV 값은 엔진 고유 단위 + 단위 필드
  - `limit_enforced`는 모든 한도 집행 + 방식 필드
  - 생산자 신원에 공통 감싸기 기반 파일(`adapters/vllm_cache_callback.py` 등) 포함
- 생산자 변환(`collectors/gpu_input_producers.py` 등)을 고치고, 형식을 B와 맞춘다(B02).
- 완료:
  - `/1`·`/4` 직접 시험 통과
  - B 소비자가 두 판본을 모두 읽음
  - PR 병합(B 검토 기록 포함)
- 생산자 SHA가 바뀌므로 등록은 J01에서 다시 한다.

### A03. rank-fault 생산자 (LIF-08)
- 계약 종류 `rank-fault`만 있고 생산자 코드는 없다. 관측기와 변환을 만든다.
- 대상 사건: `fault_applied`·`fault_released`·`detected`·`client_delivered`·`recovered`
- 완료: CPU 직접 시험 통과, PR 병합. GPU 확인·등록은 J01.

### A04. 새 정본 팩 PACK-BASELINE@34
- 77개 점검의 `impact`를 11-08 초안(HIGH 33·MEDIUM 38·LOW 6)대로 넣는다.
- SUP-11 `-OO` 지원 범위 밖 표기를 넣는다(판정 규칙은 그대로).
- 확정 정책 값 5개를 정본 실측용 정책 파일(`verification.values`)에 넣는다. 값은 11-08 결정 6-1에 있다.
  - REQ-03, REQ-13, REQ-17, OBS-05, LIF-13
  - platform 화면 변환(#226)은 platform 담당이다.
- 과거 팩·결과는 소급해서 바꾸지 않는다.
- 완료: B 팩 검토, 현황표 재생성, PR 병합.

### A05. 남은 vLLM 판본 고정 옮기기 (0.32 비교 전 필수)
- `runtime/kubernetes_http.py`·`runtime/kubernetes_startup_binding.py`·`runtime/kubernetes_worker_readiness.py`의 `0.31.0` 비교를 판본 profile 조회로 바꾼다.
- 이 파일 바이트는 증거의 수집기 신원과 B 검토 원본 대조에 쓰인다. 그래서 B 검토와 함께 바꾼다(B06).
- 완료: 0.31.0 동작 동일, B 시험 통과, PR 병합.

### A06. vLLM 0.32.0 판본 지원
- **판본 고정값 갱신 도구:** 두 판본 이미지에서 감싼 함수 코드를 함수 단위로 비교한다. 같으면 고정값 이월을 제안하고, 다르면 검토 목록으로 낸다.
- 0.32.0 GPU 이미지 digest를 고정한다(사용자 승인, 8절).
- 관측기 `adapters/vllm_*.py`에 0.32.0 고정값 행을 더하고, `engine_profiles.json`에 0.32.0 행을 더한다. 관측 의미가 바뀐 함수는 다시 검토한다.
- 변경 경로표 `adapters/assets/vllm_change_rules.yaml`로 0.31.0→0.32.0 소스 diff 보고를 확인한다.
- J03용 **회귀 사례 하나**를 설계해 사용자 승인을 받는다. 후보 쪽에만 보안 설정을 약하게 하는 등, 0.32.0 자체의 차이가 아니어도 된다는 점을 명시한다.
- 완료: 0.32.0에서 지원 profile 조회 성공, 갱신 도구 시험, PR 병합.

### A07. A 담당 점검 정본 실측 입력
- 대상: A 담당 26개(배정표 7절)
  - lab 완료 8개: 새 팩에서 재실행
  - 확정 값 5개: 재측정
  - 1차 제외 8개: 실행, UNKNOWN 기록
  - TP2 3개: J01
  - 비적용 2개: 근거 기록
- J01·J02 실행 입력(target·prepared·정책 파일·세션 계획)을 준비한다. 기존 하네스는 `tools/gpu_a_run_inputs.py`, `tools/gpu_a_aws_lab.py`, `docs/dev-1/tp2-run-plan.md`다.
- 완료: 입력 사전 점검 통과.

### A08. runtime·platform 제안 넘기기
- `claude/a-runtime-platform-proposal`(runtime 9개 파일, platform 시험 1개, CI 1줄)을 main 위로 옮긴다.
- runtime·platform 담당에게 검토 PR로 넘긴다.
- 완료: 담당 검토 요청.

### A09. (SGLang 준비, 후순위) vLLM 소스 분석 CPU 실행기 옮기기
- 대상: `executors/engine_sensor.py` source slot, `profiler_lifecycle_cpu.py`, `structured_tools_cpu.py`, `template_source_cpu.py`, `sampling_consumption_cpu.py`, `multimodal_gather_cpu.py`
- 이 vLLM 전용 구현을 provider(`adapters/`) 쪽으로 옮긴다.
- 1차 완성의 조건이 아니다.

### A10. A 작업 폴더 정리
- `Buyl/byul-dev1-k8s`의 미커밋 원본(기준 `8402ff89`)이 main에 들어간 내용과 같은지 확인한다.
- 같으면 그 폴더를 main으로 맞춘다. 원본 삭제는 A가 직접 한다.

## 5. B 작업 (GPU 실증 담당)

### B01. 기존 작업지시서 진행분 마무리
- 진행 중인 GPU 실증과 B1~B5 항목은 그대로 마친다.
- 끝나면 진행 중인 브랜치를 새 main(`3bd6a995`) 위로 옮긴다. A 설계 보수가 실행기 20개를 바꿨다. 특히 볼 것:
  - 작업 폴더 경로가 `work/<plan>/<attempt>/<check>/<side>`로 바뀌었다. 시도 ID는 `RunConfig.attempt_id`다.
  - `supply_boot`가 엔진 무관 Kubernetes 실행기가 됐다.
  - 공통 입력 `/2`·`/3`이 생겼다.
- B 시험 `test_b2_boot_evidence_persistence.py`·`test_b3_supply_records.py`는 경로만 맞춰 고쳤다(assert 동일).

### B02. 공통 입력 기록의 판정 연결
- 지금은 공통 입력 기록이 기존 실행기에 관측 사실로만 붙어 있다. 이것을 판정 규칙(PASS·FAIL 조건)으로 연결한다.
- 대상: LIF-03·05·06·07·09·10·11·12·14, REQ-08·09·10·12, OBS-02·03, PLT-01·02·04, SUP-03·06
- `/1`·`/2`·`/3`·`/4`(A02) 기록을 모두 읽는다. 등록되지 않은 생산자 기록으로 PASS를 만들지 않는다.
- A02 형식 합의에 참여한다. 확인할 것은 반복 행 요약을 존재·순서·마지막 값 판단에 쓸 수 있는지다.
- 완료: 직접 시험 통과, PR 병합.

### B03. CPU 개발 남은 36개 마감과 정본 실측 입력
- 먼저 현황 상태 파일(`docs/phase1/gpu.yaml`)과 B 자체 분류(한정 마감 25 / 제품 증거 더 필요 18)를 맞춘다.
- 묶음별 정본 실측 입력(J02)을 준비한다.
  - 공급·기동: SUP-07·09·10·11·12·15, PLT-03·05, LIF-01·02. 측정 남음 SUP-05·16 포함
  - 입력·샘플링·템플릿: SUP-03·08, REQ-08·09·10. 측정 남음 REQ-07, SUP-13 포함
  - 수명 주기: LIF-03·04·05·06·07·09·10·12·14·16
  - 플랫폼: PLT-01·02·04·06, REQ-05
  - 관측: OBS-02·03, 그리고 REQ-12
  - lab 완료 2개(SUP-04, PLT-09): 새 팩에서 GPU 정본 실행
- 완료: 묶음별 입력 사전 점검 통과.

### B04. TP2 분기 입력 (LIF-08·11, SUP-06)
- `docs/dev-1/tp2-run-plan.md` B 절과 `tools/b12_tp2_inputs.py`로 준비한 입력을 새 main 기준으로 확인한다.
- 정상·실패·복구 대조와 시간표를 J01 전에 확정한다.

### B05. 기업 입력이 필요한 B 점검
- PLT-08(1차 제외)은 정본 팩에서 실행하고 UNKNOWN과 원인을 기록한다.
- PLT-03·05, SUP-04, REQ-05·07에서 기업 입력이 필요한 분기는 EXAMPLE 정책으로 실행하고, 미입력 분기는 UNKNOWN으로 남긴다.

### B06. 검토자 역할
1. **병합 검토 기록:** 제품 소스·B 도구를 바꾸는 모든 PR에 병합 직전 최종 목록 SHA로 검토 기록 1건을 넣는다.
2. **팩 검토:** PACK-BASELINE@34(A04)를 검토한다.
3. **수집기 신원 파일 검토:** A05의 runtime 3개 파일 전환을 검토한다. 이 파일들의 바이트가 증거의 수집기 신원과 B 검토 원본 대조에 쓰인다.
4. **생산자 자격 receipt:** J01 뒤 GPU 확인을 마친 생산자의 자격 receipt를 넣는다(`/2` 이상).

### B07. 두 판본 비교 (J03)
- B 담당 점검의 0.31.0·0.32.0 실행 결과와 비교 결과(`NO_CHANGE`·`REGRESSED`·`NOT_COMPARABLE` 등)를 확인하고 원인을 기록한다.

## 6. 합동 세션

### J01. TP2 GPU 세션
- 구성: g6.12xlarge(L4 4장 중 2장), 한 번 기동, EC2 상한 $20(11-08 승인, EBS 별도)
- 시작 조건:
  - AWS G and VT vCPU 할당량 8→48 승인(11-08 기준 대기, 확인 필요)
  - A02·A03·A04 병합, B04 입력 확정, CI 성공
- 범위:
  - A: REQ-14, REQ-21, REQ-16. REQ-16 모델 Qwen2-VL-2B-Instruct는 EC2 안에서 내려받는다.
  - 생산자 GPU 확인과 등록: `/4`로 바뀐 기존 8종, rank-fault, failed-group-resources, ipc-ownership, worker-shard-load
  - B: LIF-08·11, SUP-06 TP2 분기
- 마감:
  - 증거 반출 SHA를 대조한다.
  - 인스턴스 유형을 g6.2xlarge로 되돌리고 stopped를 확인한다.
  - 상한에 닿으면 그 자리에서 멈추고 정리한다.

### J02. 정본 실측 세션 (단일 L4)
- 범위: J01 외 63개, PACK-BASELINE@34, vLLM 0.31.0
- 같은 이미지·모델·기동 설정의 점검은 상태 독립성을 확인한 뒤 한 번의 기동으로 묶는다.
- 시작 조건: A04 병합, A07·B03·B05 입력, 생산자 등록(J01), 비용 상한 승인(8절)

### J03. 두 판본 비교 세션
- 범위: 69개를 0.31.0·0.32.0 두 이미지에서 실행하고, 회귀 사례로 `REGRESSED` 탐지를 확인한다.
- 시작 조건: A05·A06 병합, J02 완료, 비용 상한 승인(8절)
- 두 이미지 비교를 같은 엔진 재사용으로 대신하지 않는다.

### J04. 공식 재판정
- 정책 확정자(사용자)가 정책을 CONFIRMED로 확정하고, 팩 검토자(B)가 팩 검토를 마친다.
- 그 뒤 J01~J03 결과로 `official=true` RPT를 만든다.
- 완료: 공개 실측 요약(`docs/evidence/`)과 현황표에 연결하고, 1차 완성을 보고한다.

## 7. 점검별 배정표 (69개)

"할 일"의 번호는 위 항목이고, "세션"은 실측하는 합동 세션이다. 모든 점검은 J03(두 판본 비교)과 J04(공식 재판정)에도 들어간다.

| 점검 | 담당 | 현황표 상태 | 1차 완성까지 할 일 | 세션 |
|---|---|---|---|---|
| SUP-03 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| SUP-04 | B | lab 완료 | B03: 새 팩(@34)에서 정본 재실행 | J02 |
| SUP-05 | B | 측정 남음 | B03: 정본 실측 입력 준비 | J02 |
| SUP-06 | B | CPU 개발 남음 | B02·B04: 판정 연결, TP2 실측 | J01 |
| SUP-07 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-08 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-09 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-10 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-11 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-12 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-13 | B | 측정 남음 | B03: 정본 실측 입력 준비 | J02 |
| SUP-15 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| SUP-16 | B | 측정 남음 | B03: 정본 실측 입력 준비 | J02 |
| REQ-01 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| REQ-02 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| REQ-03 | A | 기업 입력 대기 | A07: 확정 정책 값으로 재측정 | J02 |
| REQ-04 | A | 대상 비적용 | A07: 정본 선정에서 비적용 근거 기록 | J02 |
| REQ-05 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| REQ-06 | A | 대상 비적용 | A07: 정본 선정에서 비적용 근거 기록 | J02 |
| REQ-07 | B | 측정 남음 | B03: 정본 실측 입력 준비 | J02 |
| REQ-08 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| REQ-09 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| REQ-10 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| REQ-11 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| REQ-12 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| REQ-13 | A | 기업 입력 대기 | A07: 확정 정책 값으로 재측정 | J02 |
| REQ-14 | A | GPU 확인만 | A07: TP2 실측 | J01 |
| REQ-15 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| REQ-16 | A | GPU 확인만 | A07: TP2 실측 | J01 |
| REQ-17 | A | 기업 입력 대기 | A07: 확정 정책 값으로 재측정 | J02 |
| REQ-18 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| REQ-19 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| REQ-20 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| REQ-21 | A | GPU 확인만 | A07: TP2 실측 | J01 |
| PLT-01 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| PLT-02 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| PLT-03 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| PLT-04 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| PLT-05 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| PLT-06 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| PLT-07 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| PLT-08 | B | 기업 입력 대기 | B05: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| PLT-09 | B | lab 완료 | B03: 새 팩(@34)에서 정본 재실행 | J02 |
| PLT-10 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| PLT-11 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| PLT-12 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| LIF-01 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| LIF-02 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| LIF-03 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-04 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| LIF-05 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-06 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-07 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-08 | B | CPU 개발 남음 | A03 생산자 + B02·B04, TP2 실측 | J01 |
| LIF-09 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-10 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-11 | B | CPU 개발 남음 | B02·B04: 판정 연결, TP2 실측 | J01 |
| LIF-12 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-13 | A | 기업 입력 대기 | A07: 확정 정책 값으로 재측정 | J02 |
| LIF-14 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| LIF-16 | B | CPU 개발 남음 | B03: CPU 개발 마감·실측 입력 | J02 |
| OBS-01 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| OBS-02 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| OBS-03 | B | CPU 개발 남음 | B02: 공통 입력 판정 연결, B03: 실측 입력 | J02 |
| OBS-04 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| OBS-05 | A | 측정 남음 | A07: 확정 정책 값으로 재측정 | J02 |
| OBS-06 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |
| OBS-09 | A | lab 완료 | A07: 새 팩(@34)에서 정본 재실행 | J02 |
| OBS-11 | A | 기업 입력 대기 | A07: 정본 팩 실행, UNKNOWN 기록(1차 제외) | J02 |

## 8. 사용자 결정이 필요한 것

1. **J02 정본 실측 비용 상한:** 단일 L4 g6.2xlarge, 63개 점검. 세션 계획을 보고받은 뒤 승인한다.
2. **J03 비교 세션:** 0.32.0 GPU 이미지 digest와 비용 상한을 승인한다.
3. **회귀 사례 방식(A06):** A가 제안한 회귀 사례를 승인한다.
4. **계약 판본 번호(A01):** 11-08·11-09 결정의 "`/2`" 내용을 `/4`로 옮기는 것을 확인한다. `/2`·`/3`은 이미 사용 중이다.
5. **B 대리 검토자:** B가 없을 때 병합 검토 기록과 팩 검토를 대신할 사람을 정한다. 지금은 B 혼자 모든 제품 소스 PR의 병합 관문이다.
6. **TP2 할당량:** AWS 승인 상태를 확인한다(11-08 기준 대기).

## 9. 기존 작업지시서와의 관계

저장소 기록으로 확인한 범위에서 정리했다. 원본 HTML(`BYUL A·B 작업지시서 (1).html`)은 저장소에 없다.

| 기존 항목 | 이번 지시서 |
|---|---|
| A1 LIF-06 transition-dispatch, A2 SUP-03 보조 parser 입력, A3 REQ-09 MM·REQ-10 sampling 최종 소비 | #241로 main에 들어옴. B 판정 연결은 B02 |
| A4 공통 schema `/2` | A02(번호는 `/4`) |
| A6 TP2 하네스·입력 | A07·J01 |
| B1 등록 생산자 소비자 연결, B2 반환 5계열 | B02(관측 연결에서 판정 연결로) |
| B3 공급물 후속, B5 검토 | B01에서 마무리 |
| B4 TP2 입력 | B04 |
| J1 TP2 세션 | J01 |
| J2 공식 재판정 | J04 |

## 10. 참고 문서

- 작업 규칙: `../CLAUDE.md`·`../AGENTS.md` (1절 앵커링 금지, 6절 소스 고정, 11절 BYUL 경계)
- 결정: [2026-11-09](dev-1/user-decisions-20261109.md), [2026-11-08](dev-1/user-decisions-20261108.md), `../DECISIONS.md`
- 현황: [CHECKLIST_VALIDATION_STATUS.md](CHECKLIST_VALIDATION_STATUS.md) (`uv run python -m tools.checklist_status`로 재생성)
- 공통 입력 계약: [gpu-common-inputs-196.md](dev-1/gpu-common-inputs-196.md)
- 설계 보수 R1~R9: [design-repairs-2-20261108.md](dev-1/design-repairs-2-20261108.md)
- TP2 계획: [tp2-run-plan.md](dev-1/tp2-run-plan.md)
- 기업 입력 목록: [enterprise-input-requests.md](dev-1/enterprise-input-requests.md)
- 변경 유형별 시험 범위(U1): [UPDATE_SELECTION.md](UPDATE_SELECTION.md)
