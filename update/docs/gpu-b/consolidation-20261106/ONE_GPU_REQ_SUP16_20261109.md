# REQ-08·09·10 / SUP-16 GPU 1장 선정 검증

사용자 요청 범위는 기존 세션을 재사용한 GPU 1장·TP1 실측이다. 실제 요청 대응, 고정 runtime·Ready·정상 대조를 먼저 확인하고, 생성된 증거만 반영한다. PR #232가 2026-11-09 14:25 KST에 병합되어 [후속 Draft PR #240](https://github.com/dohashelby/byul/pull/240)에서 이어간다. [#232의 연결 댓글](https://github.com/dohashelby/byul/pull/232#issuecomment-6733655479)도 남겼다. 이 문서는 원 43개 전체의 PASS 또는 전체 완료 판정이 아니다.

## 2026-11-10 현재 LAB 잔여 재개

[43개 최신 업무표](LAB43_CURRENT_20261110.md)의 명시 선정/1차 제외 LAB 범위는 **완료10개·잔여33개**다. 기존6개에 실제 CPU 지원 프로파일의 REQ-07·LIF-02·PLT-03·PLT-05를 근거 대조 후 추가했다. 모든 분기·보안 PASS나 새 실험 개수가 아니며 원 FAIL/UNKNOWN·등급·정확 Ref는 보존한다.

[B02 판정 연결](B02_JUDGMENT_CONNECTION_20261110.md)은 기존 /1 재사용 입력 위반 사실과 /2·/3 사건 대응을 보강했다. 최종 소스 f8d431ba, 제품 목록510파일/SHA8ff741fc와 세션6 독립 영수증·해시 gate를 연결했다. 이는 소프트웨어 검증이며 /2·/3의 실제 자격 영수증이나 B02 전체20개 완료가 아니다. [B03·B04 입력 준비](B03_B04_WORK_ORDER_PREPARATION_20261110.md)는 비TP2 40개·TP2 3개 템플릿/계획이며 실제 입력·PACK34·TP2 실행 완료와 구분한다.

SUP-16은 [순차 역할 준비 기록](SUP16_SEQUENTIAL_PREPARATION_20261110.md)을 따른다. 이전 브라우저 연결 장애 이후 세션2가 11월10일13:58:26~33 KST AWS 조회에 성공했다. 당시 소유3대 stopped·IP/EIP0·활성SSM0이며 EBS440GiB 저장비는 계속된다. 설치기 수정판의 독립 검토는 끝났고 운영 드라이버·현재 proof 결합은 진행 중이다. 이 갱신은 새 GPU 기동·역할 실측 완료를 주장하지 않는다.

[LIF06/SUP03 역사CPU 지원 연결](CPU_SUPPORT_CONNECTION_20261110.md)과 [REQ09/10 무ID거부 A반환 요청](A_REQ_NOID_RETURNS_20261110.md)은 원 범위를 유지한다. 현재 제품510/8ff와 아래 원V13 실제 GPU466/2cb는 서로 다른 기준선이다.

## V13-r4 실제 선정 실측 — 기존 완료 기록

[실제 선정 REQ 결과·정확 Ref·복원 검증](ACTUAL_GPU_SELECTED_REQ_20261109.md)에서 source2cb/V4의 결과를 확정했다. EVD3·RES3·공유RPT1이 생성됐고, 독립 별도 DB의 정확 Ref10 복원·원 저장 팩 재판정264개와 실제 의미702개 대조는 불일치0이다.

| 구분 | 확정한 범위 | 남은 범위 |
| --- | --- | --- |
| runtime·Ready·정상 대조 | 실제 Ready97초·원Pod600초 안·health/정상응답·source/실제utils/준비권한 연결 | 원90초 성능 실패 보존 |
| REQ-08 | 실제 선정3요청 대응·EVD COMPLETED, EXAMPLE 기능/보안 PASS | 성능 UNKNOWN·인증/토큰동일성/전체정책 범위 |
| REQ-09 | 실제선정5요청·EVD ABORTED·length독립보안FAIL 보존 | ID없는거부요청의공통소비연결, 기능/성능UNKNOWN |
| REQ-10 | 실제선정9요청·EVD ABORTED·원3축UNKNOWN 보존 | ID없는거부응답의공통소비연결·전체runner 소비 |
| SUP-16 | 순차역할 caller·pureCONFIG·독립aggregate 준비 및 CPU 경계 검증 | 실제 phase별감독기·현재proof 최종결합·실제3역할 관측·별도fullpair |
| 정지·비용 | 원stop180미관측보존후14:45:25~26UTC 소유3대stopped/IP0후속관측, 보수추정$76.24573072675766 | 원control회수·독립최종운영/비용200개대조일치, EBS440GiB계속 |

원43개 상태는 자동 상향하지 않았다. 제품은 EXAMPLE/DRAFT·RPT official=false이며 API 대응을 worker 인증으로 바꾸지 않는다. [SUP-16 준비·재개 입력](SUP16_SEQUENTIAL_PREPARATION_20261110.md)을 별도로 인계한다. 아래는 변경하지 않은 V12 이전 실행 이력이다.

## 이전 실제 진행 상태 — V12, 2026-11-09 21:54 KST 마감 관측

V12에서 실제97초 Ready→직접 HTTP health200→정상 chat/completion 각200까지 확인했다. 이후 선정 REQ 제품 입력 준비가 디렉터리 보호 조건에서 중단됐다. [준비 권한·native ID 최소 수정](NATIVE_ID_AND_PRIVATE_PREPARATION_FIX_20261109.md)은 CPU·독립 검토를 마치고 신규 source2cb를 별도로 고정했다. 기존 b85 결과는 보존하며 새 실제 결과를 선반영하지 않는다. [CPU 업무 완료·실제 연결 잔여](CPU_SUBTASK_STATUS_B_20261109.md)도 원43개 결과와 구분한다.

| 단계 | 실제 결과 |
| --- | --- |
| 단일 GPU 호스트 | 기존 노드의 L4 1장·CPU8·약32GiB·원 보호 타이머 활성 확인 |
| 소스 전달·보존 입력 재사용 | ZIP 전달 성공 후, 후속 실행에서 보존 bundle/pins의 현재 FD·SHA를 확인해 재사용 |
| B 소스·호스트 실행 환경 | 실제 b85/tree d1a4·2,160파일·manifest2e400·독립 새 Git copy·offline uv·표준 venv의 guard import origin 검증 성공 |
| controller 사전검증 | 경로 결합 수정 후 실제 현재 source·host 실행 환경 preflight 통과 |
| 모델 준비·Pod 생성 | 고정 모델 파일·영수증의 실제 FD 검증과 모델 준비 통과. mount 기본값 비교 수정 후 실제 Ready 관측까지 진행 |
| vLLM 엔진 runtime·Ready | 실제 모델 적재·엔진/API 기동·같은 Pod Ready=True 확인. V9 98초·V11 97초, 원90초 성능 조건은 실패 |
| 실제 입력·target 결합 | 원 JSON·입력 ENV와 새 target 생성 성공. 이전 tools import 오류 수정의 실제 운영 통과 |
| 정상 추론 응답 | 직접 HTTP health200 뒤 정상 chat/completion 각200. 같은 generation 전후 확인 |
| 선정 REQ 입력 준비 | 원027 helper에서 디렉터리0700/소유 조건 거부. 실제 stat 숫자는 미기록이라 특정 mode·owner로 단정하지 않음 |
| REQ-08·09·10 실제 제품 수집·EVD/RES/RPT | 아직 미실행 |
| SUP-16 순차 역할 실측 | 아직 미실행. 역할 준비·stdin bridge 검증은 별도 완료 |
| 자원·비용 | 21:54:39~40 KST full 조회에서 소유3대 stopped·공인IP0·활성SSM0을 각 query 시각으로 확인. 서울 실행 장부 보수 추정 $75.58237225241045, 확정 청구 아님. EBS 저장비 계속 |

일곱 차례 선행 실행의 원 오류와 원 stop180 관측 실패는 보존한다. 정상 응답을 선정 REQ 제품 PASS로 집계하지 않는다. [첫 실행](ACTUAL_GPU_READY_ATTEMPT_20261109.md), [소스 설치 실패와 수정 후 검증](ACTUAL_GPU_SOURCE_INSTALL_ATTEMPT_20261109.md), [모델 준비·Pod 기본값 처리](ACTUAL_GPU_MODEL_PREPARE_MOUNT_20261109.md), [Ready90 실패·기한 후 Ready](ACTUAL_GPU_LATE_READY_20261109.md), [target 연결 오류와 수정](ACTUAL_GPU_TARGET_BIND_20261109.md), [실제 정상 응답·REQ 준비 경계와 마감](ACTUAL_GPU_NORMAL_AND_REQ_PREPARE_20261109.md)에 근거를 연결한다. [PR main 통합 검증](PR240_MAIN_INTEGRATION_20261109.md)과 실제 GPU source b85는 별도 기준선이다.

별도 [PR #241](https://github.com/dohashelby/byul/pull/241)은 B 상수·재생성 목록·실제 R2 검토 기록1건을 c60d2286에 반영한 뒤 Linux CI11,981통과·1skip/7check 성공을 확인했고 19:35:48 KST 병합됐다. 이 승인·CI 기준선과 현재 b85 GPU 실측을 구분한다. SUP16의 join·실행 adapter 준비와 context binder 정적 검토는 끝났으며 실제 운영 context/state·새 역할 관측 연결은 남아 있다. 이 준비를 GPU 실측 완료로 집계하지 않는다.

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

### 후반 검증 실패 시 callback 보존 보강

독립 검토에서 callback 수신 후 source/세대 재확인에 실패하면 수신 자료가 artifact 저장 전에 소실되는 오류를 확인했다. `5543eec6`은 후반 검사 전에 canonical parsed callback 결과를 원자적으로 보존하고, 실패 EVD·반출·복원에도 포함한다. 원 pipe stdout bytes나 인증된 worker 기록으로 표시하지 않으며 원 오류와 별도 staging 정리 오류를 함께 보존한다.

원 한정 4실패 후 수정 4통과(pytest 24.55초 / 부모 26.297초)를 확인했다. 정상 대표 1개는 기존 사례의 재검증이고 신규 후반 실패는 3개다. 앞선 209개·14개 전체는 반복하지 않았다. 독립 검토의 236개 대조에서 오류 0개, 기존 결함 해소와 독립 FAIL 보존을 확인했다. source hash 목록은 해당 B 파일 1개의 hash만 갱신해 466개 전체를 재확인했다.

근거: [보존 보강 검증](one-gpu-req-preservation-followup-20261109.json), [후속 고정 인계](one-gpu-req-preparation-handoff-v2-20261109.json), [독립 검토](one-gpu-req-preservation-review-20261109.json). 원 준비·실패·검토 기록은 유지한다. `0729e7b0`의 CI14/14 성공은 해당 이전 snapshot의 결과이며 새 제품 수집 snapshot의 CI와 구분한다. 독립적으로 끝나는 runtime·Ready 전용 운영은 문제가 있는 Capture 경로를 호출하지 않음을 실제 고정 운영 join에서 확인한 범위에 한해 이전 snapshot을 사용할 수 있다.

## SUP-16 제품 연결 한계

현재 supply_boot 제품 경로는 실행 전에 두 role의 실제 target/prepared를 결합하고 같은 호출에서 두 live 대상을 읽는다. Pending Pod에는 container ID와 started_at 등이 없어 GPU 한 장의 순차 role을 전체 paired 실행으로 연결할 수 없다. 역할별 원자료·writer 결과와 전체 EVD/RES/RPT 실행을 구분한다. 삭제한 대상의 입력 재생이나 미래 신원 발급으로 이 경계를 채우지 않는다.

## 운영 및 결과 기록 기준

서울 기존 인스턴스 한 대만 세션2가 운영한다. 원 기한·보호 타이머와 소유 정리 절차를 유지한다. 원 Ready·정상 대조가 실패하면 원 실패와 늦은 Ready를 보존한다. 역할 전환 전 원자료 반출·소유 대상 부재·GPU 점유 해소를 확인한다. 실제 생성한 제품 객체에 대해서만 정확 Ref·반출·별도 DB 복원·저장 팩 재판정 결과를 기록한다. EC2 stopped 확인과 비용 상계 마감은 실험 결과와 별도로 기록한다.

최초 초안 작성 시점에는 새 GPU 실측이 없었다. 이후 실제 결과는 문서 앞의 관측 시각별 상태와 개별 실행 기록으로 갱신하며, 이전 준비·실패 기록은 보존한다.
