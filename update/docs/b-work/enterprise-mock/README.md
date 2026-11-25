# 기업 입력 대기 10개 점검의 모의 기업 입력 (EXAMPLE)

PACK-BASELINE@39의 69개 공식 비교(2026-11-23 TP2, `RPT-01M83D7MZWJQ2RME46SZDV6FQH`)에서 기업 입력이 없어 실행 범위에 넣지 못한 10개 점검의 모의 입력이다. 사용자 지시(2026-11-25)는 "기업 입력이 없어서 NOT_RUN인 점검은 모의 데이터로 실행한다"이다. 이 폴더는 B가 만든 판본 무관 입력이다. AWS 실측은 A가 기존 실측 도구로 한다.

- 기계가 읽는 값: [enterprise-mock-inputs.json](enterprise-mock-inputs.json)
- 기준 소스: 2026-11-25 SUP-09 AWS 실측의 봉인 소스(tree `d85f89c8`)에서 팩·실행기 계약을 읽어 맞췄다. 로컬에서 파이썬을 실행하지 않았다.

## 표시 규칙

- 모든 값은 **EXAMPLE**이다. 이 값으로 만든 POL은 `DRAFT`이고 보고서는 `official=false`다. 공식 비교나 확인 정책 결과로 집계하지 않는다.
- 두 판본에 같은 값을 쓴다.
- 모의는 기업 쪽 **결정**과 기업 **시스템**만 대신한다. 엔진·Gateway·실험실 구성요소가 실제로 남긴 기록만 관측한다. 기록 파일을 손으로 써서 관측 결과처럼 넣지 않는다(INV-02).
- 자격 원문·토큰·세션 키는 파일·로그·EVD에 남기지 않는다.
- `<...>`는 실행마다 A가 채우는 값이다. 세션은 실행마다 새 32자리 16진수를 쓴다.

## 점검별 요약

팩 정책값은 8개 점검에 이미 확인값이 있다([11-16 확인 정책](../../dev-1/mvp-enterprise-policy-20261116.json)). 그 값을 그대로 옮겼다. 실제로 빠진 기업 입력은 실행 때 결합하는 결정과 기록이다.

| 점검 | 빠진 기업 입력 | 모의로 대신한 것 | 실험실 구성요소 | 기대 결과 |
|---|---|---|---|---|
| OBS-01 | 허용·거부 시험 주체, 필수 사건 생산자 | Gateway 허용·거부 주체 2개, chat 경로 역할 | 소유 runtime Gateway(REQ-13과 같은 준비) | COMPLETED / UNKNOWN |
| REQ-02 | 승인 경로·권한 매핑, 동일 요청 기록 | route별 역할표, 일반 주체 1개와 시험 요청 3건 | Gateway 기록 → `dev1_runtime_records.route_packet` → `/guard-route` | COMPLETED / UNKNOWN |
| REQ-03 | 관리 역할·관리 경로 정의 | 일반·관리 역할표와 주체 2개 | 소유 Gateway(관리 경로 전달 여부가 결과를 정함) | COMPLETED / UNKNOWN(11-05와 같으면 기능 FAIL) |
| REQ-17 | (정책값은 11-08 확정) 공유 그룹·metrics 독자 | cache 공유 집단 2개, metrics 독자 | Gateway 주체별 cache salt, 엔진 센서 | COMPLETED / UNKNOWN |
| LIF-13 | (정책값은 11-08 확정) release 신원 | runtime_controller 신원의 release 1회 | 11-05의 가중치 공유 daemon과 소비자 2개 | COMPLETED / UNKNOWN |
| OBS-06 | 기록 조회 허용·비허용 주체, 로그 사본 목록 | 조회 주체 계획, RBAC, 사본 3개(원본·수집·백업) | 로그 수집 사이드카, 암호화 백업, 사본 목록 생산자 | COMPLETED / UNKNOWN |
| OBS-11 | 수집 파이프라인 상태 기록 | 수집기 대역과 3초 공백 훈련 | `collection-status` 컨테이너 | COMPLETED / UNKNOWN |
| PLT-10 | 발급자 철회 사건 | 실험실 키 발급자, 대조·퇴역 키 쌍 | 엔진 `--api-key`(대조 키) 기동 | COMPLETED / UNKNOWN |
| PLT-11 | 살아 있는 세션 철회 발급자 | 같은 키 발급자와 철회 사건 | 아래 열린 문제 참고 | 준비 조건 확인 필요 |
| PLT-12 | 자격 사본 목록, 처리 기록 | canary 사본 3개(Secret·ENV·FILE), 30초 안 삭제 | canary Pod, 사본 기록 생산자 | COMPLETED / UNKNOWN |

기대 결과가 UNKNOWN인 까닭은 생산자 미인증과 관측 전용 경로이기 때문이다. 2026-11-08 사용자 결정으로 OBS-01·REQ-02·OBS-06·OBS-11·PLT-10·11·12는 1차 범위에서 빠졌다. 이 7개는 "실행 경로를 준비하고 UNKNOWN으로 남기면 완성"이 기준이다(CLAUDE.md 5절). 이번 모의 실행은 이 점검들을 NOT_RUN에서 실행된 UNKNOWN으로 바꾸는 것이 목표다.

## A가 할 일

1. **POL**: `enterprise-mock-inputs.json`의 `policy_values`로 DRAFT·EXAMPLE POL을 만든다. PLT-12 `copy_treatments`는 `copy_rule`과 사본 목록으로 `tools/mvp_credential_copy_inputs.py`가 채운다.
2. **Gateway 인증**: OBS-01·REQ-02·REQ-03 주체와 `route_roles`를 쪽별 auth Secret에 넣는다. 토큰은 Secret과 실행 환경변수에만 둔다.
3. **엔진 Pod 볼륨**: 기록을 읽는 위치를 읽기 전용으로 붙인다.
   - `/guard-route`: REQ-02
   - `/guard-observation`: PLT-12
   - `/guard-record-access`: OBS-06
4. **생산자**: 아래 실험실 대역이 실제 동작을 기록하게 한다.
   - Gateway 기록을 옮기는 route 기록 변환(REQ-02)
   - canary 사본 생성·삭제 기록(PLT-12)
   - 로그 사본 목록과 거부 신원의 반출·복호 시도(OBS-06)
   - 수집 상태와 공백 훈련(OBS-11)
   - 키 발급자와 철회 사건 파일(PLT-10·11)
5. **실행 결합**: 점검별 `[run_binding.<점검>]`에 `record_session`·`record_producer`·`test_actors`·`issuer_event`를 넣는다. 생산자 SHA·세션·창은 guard가 계산한다.
6. **실행**: `[update] allowed_checks`에 준비된 점검만 넣고 `guard prepare-update` → `guard update`로 EVD·RES·RPT를 만든다. 같은 이미지·모델·기동 설정의 점검은 한 번의 TP2 기동으로 묶는다.

## 열린 문제

- **PLT-10 확인값 불일치**: 11-16 확인 정책의 `revocation_subject_label`은 `byul-mvp69-retired`다. 그런데 `credential_revocation_contract.normalize_policy`는 `retired` 하나만 받는다. 확인값을 그대로 쓰면 `CREDENTIAL_REVOCATION_POLICY_INVALID`로 실행 전에 막힌다. 모의 입력은 `retired`를 쓴다. 확인 정책 쪽 정정 여부는 사용자가 정한다.
- **REQ-17 표본 수**: 역할별 최소 200을 채우려면 사용자 결정23의 분할 수집(504개씩 두 묶음)이 필요하다([설계](../../dev-1/req17-split-collection.md)).
- **LIF-13 정책값 미사용**: 세 정책값은 팩과 `tools/overrides/LIF-13.yaml`에만 있고 실행기·판정 규칙이 읽지 않는다.
- **REQ-02**: 실행 결합이 `native_route.prepared` 파일로 `route_native_prepared_sha256`을 계산한다(lifecycle 로더). 엔진 lifecycle 센서 준비물이 없을 때 결합 실패로 NOT_RUN이 되는지 확인이 필요하다.
- **PLT-11**: vLLM API 키는 기동 때 고정이라 살아 있는 연결 중에 철회할 수 없다. 엔진을 재기동해 철회하면 기존 연결이 끊긴다. 그러면 "철회 뒤 기존 세션 거부"를 시험한 것이 아니다. 엔진 앞에 철회 가능한 인증 대역을 둘지 A와 사용자가 정해야 한다.
- **PLT-12**: `tools/mvp_credential_copy_inputs.py`는 EXAMPLE ENV를 거절하고 확인 정책의 승인 규칙(`approved_rules`)을 읽는다. DRAFT POL로 돌릴 때 이 도구를 그대로 쓸지 정해야 한다.
- **REQ-17·LIF-13**: 정책값은 이미 확정돼 있다. NOT_RUN의 남은 원인은 기업 입력이 아니라 두 판본의 엔진 센서 준비물이다.
- **REQ-03**: 11-05 실측에서 runtime Gateway가 관리 경로를 전달하지 않아 404였다. update에서 runtime을 고치지 않으므로 같은 결과(보안 UNKNOWN·기능 FAIL)가 예상된다.

## 근거

- 점검 정의: `packs/PACK-BASELINE/checks/<점검>.yaml` (팩 39, main과 봉인 소스가 같다)
- 실행 결합 키: `src/guard/executors/run_binding.py` (`KEYS_BY_CHECK*`, `CONFIG_KEYS*`, `_EVENT_FIELDS`), [RUN_BINDING.md](../../RUN_BINDING.md)
- 기록 계약
  - OBS-01: `observation_contract.py`, `kubernetes_gateway_observation.py`
  - REQ-02: `kubernetes_route_record.py`
  - OBS-06: `kubernetes_record_access_product.py`
  - OBS-11: `log_collection_gap.py`
  - PLT-10·11: `credential_revocation_contract.py`, `session_revocation_contract.py`
  - PLT-12: `credential_copy_contract.py`
- 11-05 EXAMPLE 실험실 선례: [gpu-20261105.md](../../dev-1/gpu-20261105.md), `tools/dev1_k3d_cpu_lab.py`
- 범위 결정: `DECISIONS.md` 2026-11-08(1차 범위 제외·정책값 확정)
