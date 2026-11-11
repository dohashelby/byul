# PR #240 후속 검토·인계

입력 정의·소비 대조의 원 기준은 `88b336ed`다. 사이드 변경은 `4cf0f085`를 기준으로 고정 패치로 인계했고, root는 `1bbe6878` 위의 기존 변경을 보존해 통합했다. B03 도구·새 시험 및 원 supply validator 두 파일의 bytes/SHA가 시험한 사이드 원문과 동일함을 확인했다.

운영 상태는 [SUP-16 실제 관측 정본](../SUP16_ACTUAL_OBSERVATIONS_20261111.md)으로 갱신했다. GB12 전체 로컬 회수·SHA·최초1971 CRC·정지 검증은 완료했고, B pre-seal 관측은 CPU·한정 검토 완료다. 새 현재-case Session 수집 연결·실제 역할 검증은 별도 미완료다. 마감 요청에 따라 새 GPU·추가 실측은 시작하지 않는다. 원 FAIL/UNKNOWN·EXAMPLE/UNVERIFIED·실측 미완료를 유지한다.

게시 head·CI의 최종 상태는 [PR #240](https://github.com/dohashelby/byul/pull/240)에서 확인한다. 기존 head의 CI 성공을 이번 통합 head의 결과로 재사용하지 않는다.

## 이번에 끝낸 준비

- B03: 팩33의 비TP2 40개 정의에서 정상 probe·도전·정책 타입·필수 관측·judge 조건·factory 선언을 추출했다.
- 기존 입력 준비 선정7개+PLT-01을 제외한 추가 요구 대상은 32개다. 구체적인 runtime/POL/target/prepared 실입력은 반환 대기다.
- 기존 prepare_checklist_cases를 실제 호출해 별도 출력에 빈 정책·분기 계획을 만들었다. 이를 실행 승인·완성된 실측 입력으로 표기하지 않는다.
- B02: 20개를 기록/완결성 제한과 기존 판정 관측 연결로 나눴다. A 계약에서 REQ-12가 request-attempt의 checks에 없음을 확인했다.
- REQ-07·SUP-13: real/Kubernetes factory 모두 존재한다. Kubernetes 쪽은 SUP-03 전용 profile이라 두 점검을 거부한다. real 경로의 Docker 소유 target·readonly template·정책 요구를 명시했다.
- SUP-16: GB12 전체 회수·원 SHA·최초1971 CRC·원문 대조·실제 정지를 완료했다. 정상 전후 후보1/기록3/sealtrue도 BOUND_REACHED·UNKNOWN이고 challenge만 후보0/기록0/sealfalse다. 새 B pre-seal 관측의 CPU4·핀7·metadata4 및 독립 검토를 실제 모델 실증과 구분했다.
- B04: 7개 설계 case의 정상/장애/복구·담당·실제 반환 필드를 확정했다. 실제 rank 배정이나 TP2 실행 완료는 아니다.
- B03 공급·기동 입력은 기존 도구에 `b03-supply-pair-inputs/1` 사전검증 모드를 추가했다. exact file SHA·봉인 ENV·기존 정책·역할별 원 `_load`·서로 다른 session/target을 검사하고, 잘못된 입력은 결과 출력 전에 거부한다. live/자격/실행 승인은 별도다.

## 새 코드 검증

- 원 v1은 WSL Linux CPython3.12.3·pytest8.3.5에서 직접9개/28.33초 통과였다. 독립 검토에서 SHA null 우회를 발견해 새 모드에만7개 포인터의 path/64lowerhex SHA를 필수 검증하는 v2로 보완했다. 최종25개/21.25초·환경 포함31.3453초 통과를 확인했다. 원 v1 결과·결함·패치는 보존한다.
- 실제 `prepare_assets`가 생성한 EXAMPLE 자산을 원 `_load`로 소비했다. 원격 run/collection은 시험에서 금지했다.
- 정상1과 기존 누락/변조/역할교환/동일세션/객체SHA/ENV/만료/backend 거부8, 추가 null SHA7·비정규 SHA5·잘못된 path4를 확인했다. invalid pointer는 pinned 호출 전에 거부한다. 기존 pinned/dependency/legacy 함수는 바꾸지 않았다.
- 기존 함수5개는 새 schema dispatch 한 분기를 제외하고 AST가 동일하다. 기존 선정7개·PLT-01을 새 전체 재시험 통과로 집계하지 않는다.
- 프로젝트 의존성·제품 소스·팩·원 source gate는 바꾸지 않았다. 신규 기본 suite/전체 CI 통과는 주장하지 않는다.

## A에게 전달할 반환 요청 초안

> PR #241의 기존 반환을 기준으로 B 입력·소비 경로를 대조했습니다. 현재 다음 반환이 필요합니다.
>
> 1. REQ-12: B 소비자 CHECK_IDS에는 있지만 공통 `request-attempt`의 checks에는 없어 `B_COMMON_INPUT_CHECK_UNDECLARED:REQ-12`로 거부됩니다. 실제 질문에 맞는 사건·관측 범위와 점검 선언, fixture/투영 함수를 커밋 SHA와 함께 반환해 주세요. B가 checks를 임의 확장하지 않았습니다.
> 2. 공통 `/4`: schema·행 요약의 존재/순서/마지막 값 의미, 단위/집행 방식·완결성 진단, fixture·source SHA를 반환해 주세요. 기존 `/1~ /3`은 유지합니다.
> 3. PACK34: impact·SUP-11 지원 범위·확정 정책 5개의 새 팩과 정확 Ref를 반환해 주세요. B는 그 판본을 검토한 뒤 정본 입력을 결합합니다.
> 4. TP2: native24 TP1 지원은 확인했습니다. LIF-08 peer/rank 제어 ACK, LIF-11 실패 group 자원/복구 기록, SUP-06 rank별 required/assigned/loaded의 TP2 반환이 필요합니다. 지원 밖 case는 명시해 주세요.
>
> 각 반환에는 커밋/파일 SHA·schema·fixture·투영 함수·실제 지원 범위를 붙여 주세요. 이 요청은 GPU 기동 요청이 아닙니다.

외부 이슈·댓글로 발송하지 않은 초안이다.

## 병합 전 확인

- root는 현재 head1bbe의 worktree에서 고정 패치 SHA63eb692e·apply --check를 확인했고, 기존 운영/증거 문서를 보존해 적용했다. PR writer는 root 한 곳으로 유지한다.
- 이번 변경은 B03 준비 도구·직접 시험·B 문서다. 제품/팩/생산자/판정 코드 변경은 없다. 원 source gate 목록과 검토 기록은 재작성하지 않는다.
- 새 문서의 현재 소스 SHA·행 참조·40/20 범위·backend 조사와 local receipt를 대조한다.
- 이 사이드 패치의 독립 검토는 기존 S4에 별도 고정 diff로 배정했다. pre-seal·원자료 검토를 B03 변경의 승인으로 재사용하지 않는다. 최종 결과는 PR 본문과 해당 검토 원문으로 연결한다.
- source-only check·CPU 입력 준비·실제 GPU·공식 판정의 범위를 유지한다.
- 실제 GPU 기동, 기존 채팅 지시, 새 반복 모니터, 원 증거/공유 checkpoint 변경은 하지 않는다.

## 정리 결과를 읽는 순서

1. `INPUT_AND_CONSUMER_AUDIT.md` — 40개 입력·20개 소비 연결과 REQ-07/SUP-13 backend.
2. `input-and-consumer-matrix.json` — 원 정의·factory·행/파일 SHA를 포함한 상세 요구.
3. `OPERATING_REQUIREMENTS.md` — SUP-16 회수/진단 절차와 B04 TP2 반환.
4. 별도 guard-output의 receipt·definition-cases — 실제 준비 도구 출력. 제품 EVD/RES/RPT가 아니다.
5. `SUPPLY_PAIR_PREFLIGHT.md` — 실제 파일 반환 후 실행할 B03 사전검증 형식과 명령.
