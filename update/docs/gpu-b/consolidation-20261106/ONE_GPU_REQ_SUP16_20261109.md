# REQ-08·09·10 / SUP-16 GPU 1장 선정 검증

사용자 요청 범위는 기존 세션을 재사용한 GPU 1장·TP1 실측이다. 실제 요청 대응, 고정 runtime·Ready·정상 대조를 먼저 확인하고, 생성된 증거만 PR #232에 반영한다. 이 문서는 원 43개 전체의 PASS 또는 전체 완료 판정이 아니다.

## 선정 범위

| 항목 | 확인할 범위 | 이번 선정에서 확인하지 않는 범위 |
| --- | --- | --- |
| REQ-08 | 기존 재사용 입력 사례의 HTTP 거부·도달과 실제 요청 callback 대응 | 토큰 동일성 전체, 별도 모델 코드 선택 계약 |
| REQ-09 | 작은 text token_id·길이 경계와 정상 대조, callback 대응 | 멀티모달 전체와 processor 최종 소비 전체 |
| REQ-10 | 기존 V1 temperature·top_p 사례와 runner/sampler 관측 | min_tokens·structured·전체 V2·GPU kernel 내부 소비 |
| SUP-16 | 새 고정 pair 기록기를 사용한 정상→자기 부분 사본→복구 정상의 순차 역할별 component 관측 | 두 live role target/prepared를 요구하는 전체 paired factory 판정 |

## 요청 대응 조건

고정 vLLM 0.31 소스 7개의 실제 이미지 내부 bytes와 설치한 callback assets를 확인한다. 격리된 LAB 엔진에서 import 전에 `VLLM_DISABLE_REQUEST_ID_RANDOMIZATION=1`과 기존 V1 조건을 적용한다. 단일 prompt·n=1에서 REQ-08은 원 chat 응답 ID 그대로, REQ-09·10은 completion 응답 ID에 정확히 `-0`을 붙여 기존 공식 HMAC과 대조한다. 원 요청 body·headers·전송 횟수는 유지한다. 무응답 ID의 HTTP 거부, 소스 불일치, 잘린 기록, 재기동·세대 변경은 대응 성공으로 채우지 않는다. Secret과 원 요청 본문은 공개하지 않는다.

## 고정 준비 검증

REQ 변경은 직접 영향 기존 209개 통과와 최종 신규 14개 통과(pytest 45.00초 / 부모 46.00초)를 구분한다. EXAMPLE protocol에서 원 요청·callback → 기존 producer/preparer/consumer → run_plan·EVD/RES/RPT → 반출·별도 DB 복원·같은 시각의 저장 팩 재판정을 확인했다. 원 CPU 실패와 수정 경계를 보존했다. 실제 GPU 결과는 아니다.

SUP-16 역할별 준비의 고유 사례는 11개다. 후속 previous-binding 인계 보강의 영향 1개는 기존 사례의 재검증이며 새 사례로 합산하지 않는다. 독립 검토는 고정 준비 코드의 미해결 구체 결함 0개이며 실제 운영 join·신원·기동·full pair 완료를 의미하지 않는다.

통합 제품 해시 목록은 B 전용 소스 1개 추가·기존 5개 변경만 반영해 466개 파일을 검증했다. 기본 팩·A 공통 계약·코어·등록·판정은 변경하지 않았다. 기존 소스 gate 고유 8개도 통과했다. 최초 5개 setup 오류는 Windows의 긴 임시 경로 때문으로, 짧은 별도 경로에서 해당 5개만 재실행했다. 시험·제품 코드는 수정하지 않았다.

근거: [준비 검증](one-gpu-preparation-validation-20261109.json), [REQ 고정 인계](one-gpu-req-preparation-handoff-20261109.json), [SUP-16 독립 검토](one-gpu-sup16-preparation-review-20261109.json), [요청 ID 소스 7개](one-gpu-request-id-source-review-20261109.json).

## SUP-16 제품 연결 한계

현재 supply_boot 제품 경로는 실행 전에 두 role의 실제 target/prepared를 결합하고 같은 호출에서 두 live 대상을 읽는다. Pending Pod에는 container ID와 started_at 등이 없어 GPU 한 장의 순차 role을 전체 paired 실행으로 연결할 수 없다. 역할별 원자료·writer 결과와 전체 EVD/RES/RPT 실행을 구분한다. 삭제한 대상의 입력 재생이나 미래 신원 발급으로 이 경계를 채우지 않는다.

## 운영 및 결과 기록 기준

서울 기존 인스턴스 한 대만 세션2가 운영한다. 원 기한·보호 타이머와 소유 정리 절차를 유지한다. 원 Ready·정상 대조가 실패하면 원 실패와 늦은 Ready를 보존한다. 역할 전환 전 원자료 반출·소유 대상 부재·GPU 점유 해소를 확인한다. 실제 생성한 제품 객체에 대해서만 정확 Ref·반출·별도 DB 복원·저장 팩 재판정 결과를 기록한다. EC2 stopped 확인과 비용 상계 마감은 실험 결과와 별도로 기록한다.

현재 이 초안 작성 시점에는 새 GPU 기동과 실측 결과가 확인되지 않았다. 고정 소스·CPU 회귀·독립 검토·운영 결합 및 실제 실행 결과는 후속 증거를 확인한 뒤 갱신한다.
