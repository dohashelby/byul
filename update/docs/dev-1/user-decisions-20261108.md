# 사용자 결정 (2026-11-08, 권장안 일괄 승인)

사용자가 정책 확정자로서 아래 권장안을 승인했다. 한 줄 요약은 `DECISIONS.md`에 있다.
이 문서는 결정을 기록할 뿐이며, 팩·정책 파일·코드에는 아직 반영하지 않았다(반영 상태는 각 절 끝에 적는다).

## 1. TP2 GPU 세션 비용 상한

- g6.12xlarge(L4 4장 중 2장 사용, 콘솔 표시 $5.65823/h) **한 번 기동, A+B 묶음, EC2 상한 $20**(보존 EBS 별도).
- 범위: A의 REQ-14·21, 미등록 생산자 5종 GPU 확인과 등록(rank-fault, failed-group-resources, ipc-ownership, worker-shard-load, LIF-11 복구 계보),
  B의 LIF-08·11·SUP-06 TP2 분기, REQ-16(2절).
- 조건: AWS G and VT vCPU 할당량 8→48 승인 뒤 시작. 시작 전 CI 성공, 증거 반출 SHA 대조, 끝나면 유형을 g6.2xlarge로 되돌리고 stopped 확인.
  상한에 닿으면 그 시점에서 중단·정리한다.
- B 분기 실행 입력은 세션 전에 `tp2-run-plan.md`에 들어와 있어야 한다. 없으면 A 범위만 실행한다.

## 2. REQ-16 멀티모달 모델

- **Qwen2-VL-2B-Instruct**(약 4.5GB, Apache-2.0). TP2 세션의 EC2 안에서 Hugging Face로 내려받는다(로컬 다운로드 없음).
- 내려받은 snapshot의 revision·파일 SHA를 고정해 기록한다. REQ-09의 실제 MM processor 반환도 이 모델을 쓴다.
- 판정 기준은 2026-11-05 결정 그대로다(이미지 토큰 블록까지 적중해야 위반, `--media-text-after` 사용).

## 3. 공통 입력 계약 P2-Q1·P2-Q2 (`phase2-contract-questions.md`)

- **Q1(사건 상한 4,096):** 당분간 C(시험 요청 수·길이 제한)를 유지하고, **A(단계 요약 행)를 schema `guard-gpu-common-input/2`로 구현**한다.
  요약 규칙: 같은 요청·attempt·단계의 연속 행만 묶는다. 값이 바뀌는 지점마다 행을 끊는다. 첫 행과 끝 행은 원 행으로 남긴다.
  요약 행은 첫·끝 시각, 개수, 최대값을 가진다. 상한 4,096은 바꾸지 않는다.
  생산자 소스가 바뀌므로 GPU 재확인·재등록이 필요하다. TP2 세션 전에 끝내면 그 세션에서 함께 확인한다.
- **Q2(V2 logit bias):** 승인. sampler 행의 `logit_bias`가 null이 아니면 API 값과 비교하고(다르면 `value_changed_before_consumption` 후보),
  null이면 지금처럼 gap을 붙인다. V1 기록에는 영향이 없다.
- 반영 상태: 미구현. `gpu_common_inputs.py`를 다른 작업(LIF-06·SUP-03 반환)이 수정 중이라 그 작업이 커밋된 뒤 구현한다.

## 4. SUP-11 `-OO` 지원 범위

- transformers 5.17 판본에서 **`-OO`는 지원 범위 밖**으로 명시한다. `-OO` 분기는 UNKNOWN(지원 범위 외)으로 남고 normal·`-O`는 그대로 판정한다.
- 정상 대조 모델을 다른 것으로 바꾸지 않는다. 라이브러리 판본이 바뀌면 다시 확인한다. 운영 `-OO` 금지 정책은 두지 않는다.
- 반영 상태: 미반영. 지원 범위 표기는 SUP-11 정책 문안·`sup09-model-entry.md`에 반영할 때 판정 규칙은 바꾸지 않는다.

## 5. 정책 확정자·팩 검토자·영향도

- 정책 확정자: **사용자**. 팩 검토자: **B 담당자**(확정자와 다른 사람).
- 점검별 `impact`: 아래 초안을 사용자가 확정한다. 확정 전에는 팩 `impact: null`을 바꾸지 않는다.
  확정되면 새 팩 판본으로 반영하고 과거 팩·결과는 소급 변경하지 않는다.

### impact 초안 (77개)

기준: **HIGH** = 권한 우회, 다른 사용자 자료 노출, 승인되지 않은 코드·공급물 실행, 기록 위조처럼 보안 경계가 직접 깨지는 것.
**MEDIUM** = 경계가 깨질 수 있지만 조건·부분적이거나 무결성·가용성·추적성 저하. **LOW** = 비용·정리·보존처럼 영향이 제한적인 것.

| 영역 | HIGH | MEDIUM | LOW |
|---|---|---|---|
| REQ | 01, 02, 03, 04, 05, 11, 12, 14, 15, 16, 19, 20 | 06, 07, 08, 09, 10, 13, 17, 18, 21 | — |
| SUP | 01, 02, 03, 04, 05, 07, 08, 09, 10, 12, 13, 14 | 06, 11, 15, 16 | — |
| LIF | — | 01, 03, 05, 06, 07, 08, 09, 10, 12, 13, 15, 16 | 02, 04, 11, 14 |
| OBS | 01, 03, 08, 10 | 02, 04, 06, 07, 09, 11 | 05, 12 |
| PLT | 01, 04, 07, 08, 10 | 02, 03, 05, 06, 09, 11, 12 | — |

합계 HIGH 33 · MEDIUM 38 · LOW 6. 판단이 갈릴 만한 것:
- REQ-11·12·14(다른 요청자에게 출력이 감)는 다른 사용자 자료 노출이라 HIGH로 두었다.
- REQ-19·20(GPU 커널 경계·CUDA Graph 버퍼)은 다른 요청 자료가 섞일 수 있어 HIGH로 두었다.
- REQ-21은 TP 통신 버퍼 재사용이지만 재기동 조건에서만 생겨 MEDIUM으로 두었다.
- SUP-06(shard 적재)은 승인 파일의 누락이라 비승인 실행보다 낮게 MEDIUM으로 두었다.
- LIF는 모두 가용성·일관성 문제라 HIGH가 없다. 실패 정리·진단 종료(LIF-11·14)와 재시도 비용(LIF-02)·느린 연결(LIF-04)은 LOW다.

## 6. 기업 입력

### 6-1. 확정값 (BYUL 기준 정책, 사용자 확정. EXAMPLE이 아니다)

| 점검 | 값 |
|---|---|
| REQ-13 | `allowed_response_fields`: id, object, created, model, choices, usage. `allowed_error_fields`: message, type, code. `forbidden_return_options`: echo, prompt_logprobs, logprobs. `required_targets`: Gateway |
| REQ-17 | `cache_timing_max_auc_percent`: 55. `cache_timing_min_samples`: 200. 캐시 공유 단위: tenant. 공개 prefix 공유: 허용 |
| OBS-05 | `usage_allowed`: false (필드·목적지·경로 목록 없음) |
| REQ-03 | `general_allowed_kinds`: 추론(completions·chat), 모델 목록 조회. 관리·진단 경로(`/metrics` 포함)는 관리자 역할만 |
| LIF-13 | 공유 가중치 release는 runtime 제어기만 가능. release 뒤 재취득은 승인된 같은 세대만 허용. 디스크 fallback 금지 |

반영 상태: 미반영. 각 점검의 정책 값 파일(`verification.values`)에 넣는 작업은 플랫폼 공통 변환(dohashelby/byul#226)과 맞춰 한다.
REQ-13의 `choices` 안 `logprobs` 필드는 금지 옵션을 받지 않으면 생기지 않으므로 별도 금지 필드로 두지 않았다.

### 6-2~6-4. 1차 범위에서 제외 (UNKNOWN 유지, 2차 과제)

- 기록 생산자 필요: OBS-01·REQ-02(Gateway 기록 봉인·생산자 인증), OBS-11(수집 상태 feed), PLT-07(차단 집행 감사).
- 자격 관리 시스템 필요: PLT-10·11·12(lab에 철회 가능한 IdP 없음, 시험용 IdP를 두지 않는다).
- 운영 구조 필요: OBS-06(사본 목록·암호화), OBS-09(기업 수집기), PLT-08(TLS 구성).
이 점검들의 결과는 UNKNOWN이며, 제외를 이유로 PASS·NOT_APPLICABLE로 바꾸지 않는다.
