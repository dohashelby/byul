# vLLM 0.30.0 → 0.31.0 증거 입력 인계

`PACK-BASELINE@39`와 #292 병합 소스를 기준으로 확인한 준비 목록이다. 현재 저장소의 기록만 확인했다. 다음 항목의 실행, 자격 등록, 정책 확정은 실제 대상에서 확인해야 한다.

## 판본별 소스와 관측기

| 범위 | 0.30.0 출발 근거 | 0.31.0에서 필요한 입력 |
| --- | --- | --- |
| REQ-08·09 원본 입력 | 설치 소스 후보와 원본 거부 위치 행이 있다. 선택된 원 호출을 다루는 native SDK 등록 기록과 REQ-08·09 각각의 투영 자격은 별개다. 두 투영 자격 표는 비어 있다. | 실제 Pod의 설치 파일 크기·SHA-256, Python ABI, 대상 함수 bytecode와 원본 검증 위치. 현재 Pod의 각 정책 입력 쌍에서 관측한 정상 대조 및 같은 요청의 원본 결과. SDK와 각 투영의 새 생산자 SHA 및 별도 자격 기록. |
| LIF-08·11 rank 관측 | V1 rank/IPC 및 원본 응답 추적 소스 후보가 있다. 파일 후보로 남겨진 범위와 실제 탐지·상위 전달·정상 복귀 관측을 구분한다. | 실제 rank 소유·역할과 설치 소스/ABI, 동일 실패 세대의 rank 및 상위 응답 연결, 후속 정상 세대. 계획 digest는 현재 rank/IPC·응답 추적 표에 없다. |
| LIF-11 호스트 장치·GPU PID | GPU 프로세스 자격 표는 비어 있다. rank 결과와 별도로 호스트의 컨테이너·PID 세대·장치 참조 및 GPU 프로세스 관측이 필요하다. | 실제 노드의 컨테이너 task·root mount·PID 시작점·GPU 식별자를 결합한 정상과 전후 관측. 관측기 소스 또는 소유자·시작점이 달라졌을 때 기록을 거절하는 검증. 관측 범위는 GPU 프로세스와 장치 참조이며 전체 회수 자격은 별도로 확인한다. |
| PLT-01·04 worker 보안 | 0.30.0의 등록 증거는 선택한 소유 TP worker와 정확한 소스·이미지·런타임 범위를 다룬다. | 현재 worker·rank별 신원 및 같은 권한, 장치·마운트, 자격 공급원 연결. 설치 bytes와 현재 생산자·ABI를 확인하고 대상 범위마다 자격을 남긴다. |

관련 자료는 [입력 SDK 프로파일](../../src/guard/adapters/assets/vllm_native_input_profiles.json), [원본 거부 프로파일](../../src/guard/adapters/assets/native_input_rejection_profiles.json), [SDK 자격](../../src/guard/adapters/assets/native_input_qualifications.json), [거부 투영 자격](../../src/guard/adapters/assets/native_input_rejection_qualifications.json), [재사용 투영 자격](../../src/guard/adapters/assets/reused_input_qualifications.json), [rank IPC 프로파일](../../src/guard/adapters/assets/vllm_rank_ipc_profiles.json), [GPU 프로세스 자격](../../src/guard/adapters/assets/gpu_process_qualifications.json), [worker 보안 자격](../../src/guard/adapters/assets/worker_security_qualifications.json)에 있다.

## 우선 유예 네 항목

| 점검 | 정책 및 원본으로 받을 항목 | 제품 경로에서 닫을 공백 |
| --- | --- | --- |
| REQ-02 | 쪽별 ENV·target·기록 세션, 승인된 경로와 권한 매핑, 정상 대조, 최종 처리기와 동일 요청을 묶는 기록. | 복합 실행기에서 파일 기록과 native 보고서를 모두 보존한다. 기존 기업 기록 검증은 native 권한과 범위를 채운다. `route_producer_authenticated`와 `route_authorization_scope_complete`, 정상 대조, 소유 lab 기록의 출처를 별도로 검증해야 한다. [팩](../../packs/PACK-BASELINE/checks/REQ-02.yaml) [실행기](../../src/guard/executors/kubernetes_route_record.py) [기록 검증](../../src/guard/executors/record_attestation.py) |
| OBS-01 | 확정된 필수 사건·필드·전달 기한, 허용·거부 시험 주체의 결합, 원본 감사 기록 생산자 SHA, 현재 ENV·target·기록 세션. 각 정상 제어 및 트리거에서 원본 사건과 수집 시각을 식별한다. | 기록 생산자와 독립 트리거를 검증하는 기존 경로가 있다. 현재 수집기는 HTTP 응답만으로 정상대조 성공을 확정하지 않고, lab 수집은 EXAMPLE로 남긴다. 같은 주체·사건·시간 창의 정상대조와 실측 원본을 결합해야 한다. [팩](../../packs/PACK-BASELINE/checks/OBS-01.yaml) [실행기](../../src/guard/executors/kubernetes_observation.py) [입력 결합](../../src/guard/executors/run_binding.py) |
| OBS-09 | 승인된 기업 수집기·표시 프로파일, 신뢰 필드와 요청 ID 신뢰 정책, 같은 요청의 원본 로그·수집기 항목·실제 표시 결과. | 현재 실행기는 Guard 참조 프로파일과 EVD용 참조 HTML을 검증한다. 기업 경로에서는 실제 수집·표시의 연결과 경계 보존을 검증할 증거 소스가 필요하다. [팩](../../packs/PACK-BASELINE/checks/OBS-09.yaml) [실행기](../../src/guard/executors/log_integrity.py) |
| OBS-11 | 필수 기록 ID와 수집·탐지·경보 기한, 허용 시계 오차, ENV·세션과 묶인 원본 사건 및 정상 도착, 공백 표시·경보·의존 판정·정상 복귀 시각. | 현재 상태 파일은 원본 생산자 인증과 독립 트리거를 확정하지 않는다. 시계 오차가 음수인 행 및 사건보다 앞선 도착·경보·공백 시각은 정상 또는 기한 충족 근거에서 제외하도록 검토한다. [팩](../../packs/PACK-BASELINE/checks/OBS-11.yaml) [시간·기한 계산](../../src/guard/executors/log_collection_gap.py) [Kubernetes 실행기](../../src/guard/executors/kubernetes_log_integrity.py) |

기존 정책 승인 범위는 0.30.0 소유 lab이다. A는 두 판본의 승인 범위와 증거 생산자를 확정하고, B는 정확 Ref와 해당 범위 자료를 받아 판정 및 비교를 검토한다. [기존 정책 기록](../dev-1/mvp-enterprise-policy-20261118.json)
