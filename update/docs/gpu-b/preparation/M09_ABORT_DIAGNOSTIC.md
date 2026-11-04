# M09의 ABORTED 진단 전달 보완

원 실행은 LIF07-normal/v1, 제품 소스3bba다. 정상 준비·HTTP200·모델 형상·선택 GPU worker 결합과, 제품 EVD의 ABORTED/EXAMPLE·세 축 UNKNOWN을 구분한다. 원 EVD의 `abort_reason`은 null이었지만 `probe_log.jsonl`의 수집 summary에는 `INITIALIZATION_READY_ENDPOINT_PARTIAL`이 있었다. reader/transport collection gap은 false, native 파일 coverage는 true였다. 원 기록·Ref·판정은 변경하지 않는다.

GPU 담당 소유 `kubernetes_initialization.py`는 기존 수집 진단을 내부 context로 전달하고, 기존 ABORTED 상태에서 이유가 null일 때만 고정 진단 코드를 이유에 남긴다. 기존 이유가 있으면 보존한다. 상태·관측·정책·기한·source/profile gate·판정·센서에는 변경이 없다. 선택한 native READY 관측이 부분적이라는 사실을 설명하는 보완이며 원 관측을 완전하게 만드는 수정이 아니다.

직접 계약은 수정 전3실패/2통과를 보존하고 수정 후5통과다. 기존 source3bba의 실제 자산 생산자와 EXAMPLE 외부 I/O 경계를 사용한 후보 collector→finish 계약도1통과했다. 기존 관측·인증/범위 false와 ABORTED를 유지하면서 실제 collector의 진단이 이유로 이어지는지 확인했다. 최초 시험 fixture의 필수 필드 누락과 수정 중 sequence 타입 오류도 원 로그로 보존했다.

새 실행 관측을 추가하지 않는 진단 메타데이터 변경이므로 같은 GPU 정상 분기의 반복을 요청하지 않는다. 새 코드의 source manifest 통합은 개발 담당이 기존 공식 절차로 처리해야 한다. 이 수정에서 `reviewed-source.json`을 바꾸거나 hash 승인을 만들지 않았다. 공통 manifest 통합 전에는 현재 실험에 수정본을 섞지 않는다. 전체 suite와 설치 검증은 통합 소스에서 시행할 대상으로 남긴다.

## 공통 생산자에 필요한 관측

M09의 native worker-generation events4/candidates3, ready-endpoint events4/candidates0, ready-send0을 API200이나 실제 GPU worker 존재로 보충하지 않는다. 공통 계약 담당의 입력은 원 sealed recording·prepared profile22·ENV/target과 세션3의 검증 기록이다. 필요한 출력은 다음 결합의 직접 근거 또는 명시적인 미지원 이유다.

- READY endpoint의 유일 creator/sender와 실제 observer close. 원 사유는 `ready_endpoint_unique_creator_sender_unavailable`, `ready_endpoint_observer_close_unavailable_or_ambiguous`다.
- worker-generation의 객체·유일 CALL/RETURN·observer close와 같은 PID/start·CID/세대·모델의 결합. 원 사유는 `worker_generation_object_link_unavailable`, `worker_generation_unique_call_pair_unavailable`, `worker_generation_observer_close_unavailable_or_ambiguous`다.
- 실제 worker READY 송신·종단 준비와 model-root 연결. `worker_selected_ready_send_unavailable`, `worker_main_terminal_not_observed` 및 startup 연결 누락을 그대로 둔다.

이는 개발 담당 소유 native sensor/공통 계층 의존이다. GPU 담당이 수작업 event·우회 wrapper·서명만 추가해 사실을 채우지 않는다. 봉인 성공과 전체 수집·준비 완전성도 별개다. 원 13개 준비 항목·service gate·복구 lineage와 TP2/rank/장애 분기는 남는다.
