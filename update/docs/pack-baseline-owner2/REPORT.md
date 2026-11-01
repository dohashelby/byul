# PACK-BASELINE 실행기 연결 — 담당 2 작업 기록

현재 **23개 팩 연결 완료가 아니다**. 담당 1 공통 기반 PR이 병합되기 전이므로 PACK-BASELINE 점검 파일과 pack.yaml을 수정하지 않았다. 이번 브랜치는 `c8265f135c6bce389daafc6bce9ad11d308a10c9`에서 시작한 전용 구현이다. 기존 PACK31 Ref와 모든 수동·과거 파생 팩을 보존한다.

원격 main에서 추가로 확인한 `f15210b`는 runtime PR181 병합이다. 담당 1 GitHub 계정은 사용자가 알려준 `dohashelby`이며 이후 공통 기반 PR #183 (`pack-baseline-foundation`, 확인한 HEAD `c468e9a3990f3c6b5bdaad6df2820166cec0c8eb`)을 발견했다. 아직 병합되지 않았으므로 팩 통합은 실제 병합 커밋을 확인한 뒤 별도 단계로 진행해야 한다.

## 상태의 의미

- **등록 factory**: `registry.resolve_executor`가 실제 구현 클래스를 생성한다. fake는 집계하지 않는다.
- **전용 수집·계약 구현**: 해당 코드의 정상·위반 사실·지원 불일치·세대 변경·후속 오류 보존 계약을 시험했다는 뜻이다. 전체 점검의 충족을 뜻하지 않는다.
- **팩 연결**: 현행 CheckDef의 executor·collection_profile·필수 관측·정책·judge와 지원 구현이 모두 맞아야 한다. 현재 이 담당 범위의 팩 연결은 0/23이다.
- **실측**: 해당 환경과 소스에서 실제 수집한 결과만 해당 범주에 쓴다. EXAMPLE·대역·소스 메서드 계약 시험을 Kubernetes/GPU 실측으로 바꾸지 않는다.

새 실행기 이름은 `engine_sensor`, `lifecycle_transition` 두 개다. 기존 실행기와 호환 이름은 보존했다. `real`이 없는 구현을 Kubernetes로 대체하지 않는다. LIF14는 Kubernetes의 전용 control 프로파일을 추가했으며, CPU 호환 구현은 `profiler_observation:source-cpu`로만 선택한다. Kubernetes 구현의 CPU 대체를 거부한다.

## 점검별 구현과 남은 분기

아래 판정 열은 팩에 적용된 새 판정이 아니다. 공통 기반 병합 전 연결 제약과, 실제로 판정할 수 있는 사실의 한계를 기록한 것이다. 모든 행의 Kubernetes·GPU 실측은 아직 없고, 각 점검의 원 질문·satisfied/fail/unknown 문구와 원 정책은 보존한다.

| 점검 | 확정 실행기 / collection_profile | 구현·직접 계약 | 판정 규칙·미구현 분기 | CPU 검증 / UNKNOWN 사유 |
|---|---|---|---|---|
| LIF-03 | stream_buffer / kubernetes-stream-buffer-budget-observation-v1 | 기존 stream·HTTP 관측과 별개로 검증된 PID의 실제 /proc RSS를 조회; 당시 session/context/recording/file/owner 계보 보존 | 팩 연결 보류. 검증된 budget_process_rss_exceeded=true는 eq true로 독립 FAIL 표현 가능. queue/KV·동시성·미디어 단계/누적 예산·공정성은 미구현 | EXAMPLE producer/교체/누락/정리·등록 factory 전체 파이프라인. 별도 WSL 소유 프로세스 /proc 실조회 확인; 엔진 실측 아님 |
| LIF-04 | stream_buffer / kubernetes-stream-buffer-observation-v1 | 기존 수집기 및 제품 경로 보존 | 팩 연결 보류. 기존 confirmed=false 후보를 독립 FAIL로 올리지 않음 | 기존 제품·직접 계약. 실제 서비스 buffer/해제 실측 없음 |
| LIF-05 | lifecycle_transition / lif05-stopped-redeploy-v1 | 전환 전 수집·정상 요청·진행 요청·during/after/recovered 수집 | 팩 연결 보류. 실제 전환 활성화·기존 요청 drain·잔존 접근은 미증명 | 진행 요청/교체/복구/정리 오류 계약. 준비된 target 정적 비교를 전환 증명으로 쓰지 않음 |
| LIF-06 | lifecycle_transition / lif06-live-weights-v1, lif06-lora-v1, lif06-rolling-v1, lif06-multi-replica-v1 | 같은 실행기 내 프로파일·세션/owner/request ID로 세대·설정 사실 연결 | 팩 연결 보류. 설정 fingerprint 불일치는 weights/cache 실제 혼합과 별개. 실제 소비/전환 완전성 미증명 | 각 프로파일 직접 계약. 변경·복구할 준비 환경 및 실제 소비 계측 없음 |
| LIF-07 | initialization / kubernetes-mp-readiness-observation-v1 | 기존 MP 초기화·READY 관측; 원 required_readiness_items 정책 보존 | 팩 연결 보류. 필수 readiness 항목을 다른 목록으로 치환하지 않음. 외부 admission·지연 활성화·모든 준비 조건 미증명 | 정상/부분 실패/교체/후속 오류 계약. 요구 readiness 항목은 미확인 목록에 보존 |
| LIF-08 | rank_failure / kubernetes-mp-rank-failure-observation-v1 | 기존 rank 수집기 재사용 | 팩 연결 보류. 후보·실제 rank 장애 활성화·분산 정리 완전성 구분 | 기존 제품 계약. Kubernetes/GPU 장애 실측 없음 |
| LIF-09 | initialization / kubernetes-mp-initialization-observation-v1 | 기존 초기화 수집기 재사용 | 팩 연결 보류. 부분 실패·외부 service gate·회복 계보는 기존 한계 유지 | 기존 제품 계약. 실제 초기화 장애/회복 실측 없음 |
| LIF-10 | request_termination / kubernetes-request-termination-observation-v1 | check_id/profile에 따라 KubernetesRequestTerminationExecutor로 닫힌 분기 | 팩 연결 보류. 요청 종료와 실제 worker/GPU 해제를 동일시하지 않음 | 제품·닫힌 분기 계약. 실제 worker/GPU 해제는 미실측 |
| LIF-11 | resource_recovery / kubernetes-selected-resource-recovery-v1 | 기존 선택 자원 복구 수집기 재사용 | 팩 연결 보류. 범위별 실제 회수·반복 안정성 미관측은 유지 | 기존 제품 계약. 실제 자원 회수 실측 없음 |
| LIF-12 | request_termination / kubernetes-automatic-retry-record-observation-v1 | KubernetesRetryRecordExecutor로 닫힌 분기 | 팩 연결 보류. 재시도 기록과 실제 중복 실행/취소 효과 구분 | 기존 제품 및 분기 계약. 실제 자동 재시도·후속 실행 실측 없음 |
| LIF-13 | engine_sensor / engine-sensor-weights-lifetime-v1 | 기존 b_lif13_weights.project의 보수적 경계 재사용 설계 | **native 구현 미연결**. release 권한·다른 소비자 재취득/fallback 소비·native 참조 효과 검증 producer 필요. DECLARED 필드를 verified로 승격하지 않음 | 기존 도구 계약만 존재. 단일 release 응답은 충분한 관측이 아님 |
| LIF-14 | profiler_observation:kubernetes / kubernetes-profiler-control-v1; CPU 호환 torch-profiler-lifecycle-reference-cpu-v1 | 명시된 전용 실험 소유 범위에서 start→정상→stop→다음 start/정상/stop를 실제 수집 코드로 수행. prepared15·정확 API 호출 링크·별도 adapter 충돌 관측. 기존 CPU 도구 재사용 | 팩 연결 보류. API 200·flag를 실제 backend 종료·trace 완료·다음 backend 수집으로 승격하지 않음. 오류 후 정리 미확인은 보존 | 실제 source-body producer→수집→등록 factory→저장 팩 재판정 EXAMPLE 계약. 실제 Torch CPU·Kubernetes·GPU 신규 실측 없음 |
| LIF-16 | lifecycle_transition / lif16-restore-redeploy-v1 | 전환 전 요청/세대와 복구 후 정상 대조 수집 | 팩 연결 보류. 복원물 변조 활성화·거부·복원 내용 무결성·잔존 접근은 미증명 | 복구/교체/후속 오류 계약. 실제 복원 환경 및 변조 관측 없음 |
| OBS-02 | observation_state / kubernetes-observation-state-composite-v1 | 별도 identity/profiler target·prepared·session을 사전 검증하고 각각 수집; 한쪽 실패에도 다른 관측·artifact 보존 | 팩 연결 보류. 세션별 행에 component/session 표지. 시간 근접으로 인과 결합하지 않음. 별도 audit 지원이며 external update의 단일 target pin은 거부 | 두 실제 producer 형식→소비 계약. 별도 대상 실측 및 공통 다중 대상 승인 계약 없음 |
| REQ-12 | request_termination / kubernetes-request-attempt-observation-v1 | KubernetesRequestAttemptsExecutor로 닫힌 분기 | 팩 연결 보류. 실제 retry/late delivery/최종 소비 미검증은 그대로 | 기존 제품·분기 계약. 실제 재시도/지연 전달 실측 없음 |
| REQ-14 | engine_sensor / engine-sensor-distributed-attribution-v1 | 고정 /13 MP sourcepoint의 rank·request·설정·출력 객체 전용 콜백 본체 | **native 구현 미연결**. TP 중간 buffer·다른 프로세스 전달·선정 출력 rank→클라이언트 귀속 producer 필요. decoder 점검으로 재분류하지 않음 | 고정 소스/실제 메서드 code·원 예외·후속 오류 계약. TP/PP/PD 실제 귀속 미실측 |
| REQ-15 | engine_sensor / engine-sensor-cache-group-v1 | 고정 소스 cache lookup·key·반환 block 중첩 콜백; 세션별 HMAC | **native 구현 미연결**. 검증 신원→발급/정규화 집단·salt, 모든 집단 ordered pair와 실제 entry 소비 필요 | 정상/교체/예외/개인값 제거 계약. public_prefix_sharing_allowed·isolated_cache_groups·cache_identity_policy_ref 원문 유지 |
| REQ-16 | engine_sensor / engine-sensor-cache-context-v1 | 같은 cache 콜백 본체·기존 정책 원문 보존 | **native 구현 미연결**. required_cache_contexts의 실제 객체 변경→key 생성→hit→소비·독립 대조 필요 | 고정 소스 콜백 계약. key hash 존재를 문맥 격리 증명으로 쓰지 않음 |
| REQ-17 | engine_sensor / engine-sensor-cache-timing-v1 | 반복 순서 보존·제한된 실제 KubernetesHTTP 호출 본체·완전 응답 cached_tokens 정제 | **native 구현 미연결**. private cache 존재/신원/소비 대응·독립 잡음/부하 대조·기업 통계 기준 미제공. 단순 지연 차를 유출 판정으로 쓰지 않음 | 반복/시계 오류/응답 누락/후속 실패 계약. caller label 원문·무염 hash 미저장 |
| REQ-18 | engine_sensor / engine-sensor-slot-lifetime-v1 | 원 b_req18_slot 및 실제 CPU source-component 별도 진단 factory 재사용 | **native 구현 미연결**. A_SOURCE_PINS·POINT_CONTRACTS·CALLBACK_CODE_SHA256·전용 transport 계약이 미확정. CPU 결과를 실제 배포 ENV로 이식하지 않음 | 별도 source-cpu ENV의 EXAMPLE 파이프라인·독립 FAIL/후속 오류·반출/재판정 계약. 실제 CPU sampler 신규 실측 없음 |
| REQ-19 | engine_sensor / engine-sensor-kernel-bounds-v1 | 실제 kernel/shape/buffer/초기화/완료/독립 대조를 요구하는 capability 경계 | **native producer 미구현**. 선택 kernel 도달·buffer 범위 접근/초기화 소비·GPU 완료·대조 출력 없이는 판정 불가. API 200·센서 횟수로 대체 안 함 | 지원 거부 계약. 실제 kernel/GPU 대조 실측 없음 |
| REQ-20 | engine_sensor / engine-sensor-graph-lifetime-v1 | b_req20_graph의 보수적 native 수집/정제 경계 재사용 | **native 구현 미연결**. NATIVE_CONTRACT 미확정. 입력 갱신·실제 key·출력 소비·GPU 완료·buffer 세대·대조 출력 모두 필요. version 변화 후보를 위반으로 올리지 않음 | capability 거부·기존 도구 계약. 실제 graph replay/출력 소비 실측 없음 |
| REQ-21 | engine_sensor / engine-sensor-communication-buffer-v1 | 등록·종료·재기동·buffer 세대/완료/소비를 요구하는 capability 경계 | **native producer 미구현**. 실제 communicator 등록 handle·참조 해제·재등록·현재 세대 소비 producer 필요 | 지원 거부 계약. 실제 TP/GPU native 참조 실측 없음 |

## 판정과 증거 보존

현재 판정기를 변경하거나 새 연산자를 추가하지 않았다. 기존 `eq`, `nonempty`, `when_policy`로 표현 가능한 분기에서 수집기가 독립적으로 확인한 typed 사실을 제공해야 한다. LIF03의 직접 검증 RSS 초과는 `budget_process_rss_exceeded eq true`로 표현한다. 아직 qualified=false인 후보 목록에 단순 nonempty 규칙을 붙이지 않는다. 실제 GPU 완료·소비·세대/참조 대응 없이 전체 충족 규칙을 만들지 않는다.

새 native engine_sensor 프로파일은 미확정 contract를 입력/현재 HEAD의 hash로 자동 승인하지 않는다. `support(...).implemented=false`와 `EngineSensorNotImplemented`로 구현 대기를 구분한다. 등록된 factory의 존재를 native 지원·연결 수로 세지 않는다.

REQ18 source CPU는 `engine-sensor-slot-reference-cpu-v1`과 `source-<고정 vLLM commit>` ENV의 별도 경계다. 부모가 고정 tools/tests/product snapshot을 검증한 뒤 자식의 import 경로를 연다. private empty pycache prefix와 `-B`로 미검증 캐시 코드 읽기를 막는다. 이미 수집된 독립 source 불일치는 후속 postcheck/정리/저장 오류 때문에 지우지 않으며, 배포·GPU 사실로 올리지 않는다.

cache/distributed 콜백은 원 메서드를 한 번만 호출하고 반환 객체·동일 예외 객체를 보존한다. 관측 시계·sink 실패는 별도 gap으로 남기고 문맥을 finally에서 해제한다. 반복 수집도 시계 오류와 이미 받은 응답 사실을 분리한다.

## 공통 담당에게 필요한 정확한 입력 경계

1. 공통 기반 PR의 병합 커밋과 보존된 원 PACK31 경로를 확인해야 한다. 그 후에만 23개 CheckDef 연결·revision 단일 갱신·files_sha256 및 생성 문서 재생성을 수행한다.
2. OBS02와 lifecycle_transition의 독립 audit는 각 target/prepared/ENV/정책 hash를 검증한다. external update의 단일 `_kubernetes_target_sha256`를 여러 대상의 승인으로 확장하지 않는다. 공통 입력은 component/generation ID별 target hash를 명시할 수 있어야 하며, 기존 단일 hash는 해당 단일 대상에만 유효해야 한다.
3. native18/20의 고정 sourcepoint/code/callback/transport 계약은 실제 검토된 producer bytes로 전달돼야 한다. 입력에 적힌 hash나 센서의 자기 신고 verified bool은 승인 근거가 아니다.
4. cache/분산 콜백의 설치·prepared/profile/parser 연결이 아직 없다. 해당 공통 생산 경계를 확정하고 producer의 기본·지원 밖 record를 실제 소비기에 넣어야 한다. 전용 콜백 본체만으로 배포 지원 수를 올리지 않는다.
5. 실제 ENV/POL/target/prepared 및 선정 GPU/kernel/TP 구성이 제공돼야 실제 수집을 할 수 있다. 기업 정책값·허용 통계 기준은 임의 기본값을 넣지 않는다. 이 작업은 AWS 기동·비용·권한 변경을 승인하지 않는다.
6. LIF14 control은 `profiler_control_scope=dedicated-owned-profiler`와 정확한 `profiler_control_owner_ref`를 필수로 요구한다. 해당 ENV/target/prepared에 기존 profiler 수집도 종료·재시작할 수 있는 전용 실험 범위를 명시한 입력이어야 한다. start 성공으로 소유권을 취득했다고 추정하거나 기존 수집을 임의 종료하지 않는다. 이 신고는 실제 backend 비활성 상태나 권한 진위의 자동 검증이 아니다.
7. lifecycle_transition의 공개 정책은 core가 지원하는 scalar 타입으로 선언한다: `transition_inputs_sha256`, `transition_{before,during,after,recovered}_at`, `transition_{before,during,after,recovered}_generation` 모두 str. 초기 내부 helper의 중첩 dict 형식은 읽기 호환용이며 CheckDef에 미지원 dict 정책 타입을 선언하지 않는다.

## 검증과 로그

현재 제품 소스 고정 커밋은 `2247aabb3447ccb3210b100cade06bce51ed771e`다. `update/out/pack-owner2/source-snapshot-2247aab.json`은 변경 소스 27파일의 관측 해시이며 기존 gate의 승인값을 대체하지 않는다. 명령·pytest 시간·부모 벽시계와 원문은 같은 폴더의 `*-timing.json`, `*.log`에 보존한다.

기본 전체 시험 첫 명령 `uv run pytest -q --basetemp=C:/Users/dudwh/.codex/tmp/pack-owner2-full-20261101`은 기존 test_b_produced_ids.py의 encoding 없는 read_text에서 cp949 UnicodeDecodeError로 collection 중단됐다(19 deselected, 1 error, pytest 12.21초 / 부모 13.453초). 원문은 `full-suite-initial-cp949-error.log`에 보존했다. 기존 테스트·assert를 바꾸지 않고 다음 실행은 `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8` 환경에서 수행한다. 저장소 내부 준비물 생성 금지 방어도 유지하며 pytest 임시 경로만 저장소 밖으로 지정한다.

Windows UTF-8 기본 전체 시험은 고정 소스 `3a54ffa`에서 **10150 passed, 71 failed, 13 skipped, 19 deselected, 2 errors**, pytest **2578.14초** / 부모 **2580.55462초**였다. 최종 FAILED/ERROR 목록을 정본으로 시작 기준 `c8265f1`의 깨끗한 worktree와 대조했다. progress의 setup/teardown 두 E를 서로 다른 점검으로 세지 않는다.

- 63개 FAIL은 기존 Windows의 심볼릭 링크 권한·POSIX 경로/모드/파이프·CRLF 및 기존 시간 민감 소켓 시험에서 기준선도 실패했다. 동일 legacy 제품/시험 파일의 바이트도 확인했다.
- 2개 ERROR는 같은 oversize 입력 시험의 setup/teardown이다. Windows 환경값 길이 제한으로 기준선에서도 발생했다.
- 7개 FAIL은 새 소스와 과거 `df6255e96a877ebfbe185230ed87320aa84d2c30` 전체 product snapshot이 달라 `pinned source/index modified`로 거부된다. `test_a1_B160_http_product.py` 네 사례, native21 한 사례, req10 한 사례, LIF07 한 사례다. 시작 기준에서는 통과하므로 기존 기준선 실패로 분류하지 않는다. 과거 gate·assert·승인값을 변경하지 않았다.
- 이번 구현의 회귀 1개는 LIF12의 잘못된 프로파일 거부 코드였다. `2247aab`에서 기존 `RETRY_PROFILE_UNSUPPORTED` 계약을 구현에 복원했고, 기존 시험 assertion은 변경하지 않았다.

기준선 대조 원문·각 실행 cwd/guard 경로·부모 시간·파일 해시는 `out/pack-owner2/legacy-comparison/`에 보존한다. `review-final-late21-classification.json`은 중복 E 이후 progress 순번의 잘못된 인접 node 매핑을 바로잡은 최종 분류다. 초기 선택 원문도 지우지 않았다.

수정 뒤 Windows 관련 계약은 **258 passed**, pytest **240.71초** / 부모 **242.3331351초**였다. 공개 dispatcher·기존 retry 제품·request termination/attempts 및 직접 확장 계약을 함께 실행했다. 최종 고정 소스의 집중 검토에서도 추가 수정 사항은 없었다.

Linux WSL의 최초 Python 3.12.14 실행은 **9915 passed, 320 failed**, 네 묶음의 전체 부모 벽시계 **374.456080472초**였다. 기존 센서의 명시 허용 조합은 `(cpython-3.12.14, nt)`, `(cpython-3.12.15, posix)`, `(cpython-3.12.3, posix)`이며 이 Linux 3.12.14 조합은 거부된다. 대표 3개를 시작 기준과 대조해 동일 거부를 확인했다. 코드 계약 해시는 기존 기대값과 같았고, 추가·수정한 시험 모듈의 실패는 없었다. 320개 전부를 기준선과 대조했다고 주장하지 않는다. 원문과 대표 대조 결과는 보존했다.

Python 3.12.15 배포본은 현재 uv에서 제공되지 않아 설치 명령이 실패했으며 원문을 `linux-runtime-preparation.json`에 기록했다. 허용된 기존 system Linux **3.12.3**(`/usr/bin/python3.12`)에서 동일 `uv.lock`·추가 제외 없이 최종 재검증했다. 변경 소스 27파일은 Windows 고정 snapshot과 Linux에서 모두 같은 bytes SHA256임을 확인했다. 기본 collection의 351파일·10235개 node를 파일별로 중복 없이 네 독립 clone/venv/temp에 분배했다. `run_linux_supported3_validation.py`와 manifest에 명령·선정 집합을 보존하며 전역 Git 설정/Windows 권한은 바꾸지 않았다. 각 검증 자식에만 Git archive의 원본 LF를 유지하는 `core.autocrlf=false` 환경을 명시했다.

최종 Linux 기본 집합은 **10228 passed, 7 failed, 오류 0·skip 0**, 전체 부모 벽시계 **349.397275459초**다. 기본 collection에서 기존 marker로 제외된 19개는 그대로이며 추가 제외는 없다. 네 clone 모두 시험 전후 HEAD `2247aab` 및 tracked 파일 변경 없음도 확인했다.

| 묶음 | 통과 | 실패 | pytest 초 | 부모 벽시계 초 |
|---|---:|---:|---:|---:|
| 0 | 2555 | 4 | 347.68 | 348.480503393 |
| 1 | 2558 | 1 | 296.95 | 297.995674745 |
| 2 | 2557 | 2 | 310.07 | 311.211262948 |
| 3 | 2558 | 0 | 338.46 | 339.342253205 |

실패 7개는 위 과거 snapshot gate 거부와 정확히 같다. 동일 Linux 3.12.3에서 7개 node만 다시 실행한 결과 현재 소스는 **7 failed, pytest 1.20초 / 부모 1.415초**, 시작 기준 `c8265f1`은 **7 passed, pytest 5.09초 / 부모 5.310초**였다. 모든 현재 실패의 직접 원인은 `pinned source/index modified`다. 따라서 기본 전체 시험을 통과했다고 보고하지 않는다. 관측 해시 목록은 새 승인 근거가 아니며 과거 gate를 자동 갱신하지 않았다. 이번에 추가·수정한 시험 모듈 8개의 실패·오류는 없다.

최종 실행 결과·명령·원문은 `out/pack-owner2/linux-supported3-final-result.json`, `out/pack-owner2/linux-supported3/`, 기준선 대조는 `out/pack-owner2/legacy-comparison/review-linux3123-*-seven-historical-gates.*`에 있다. 준비 과정의 부적합 런타임·명령 오류도 기존 원문과 함께 보존했다.

기존 파생 빌더 10종(stream_buffer, initialization, rank_failure, request_termination, resource_recovery, retry_record, request_attempts, observation_state, observation_identity, profiler_state)은 임시 외부 디렉터리에서 생성하고 `load_pack`으로 재검증했다. 77개 정의와 원 seed Ref를 보존했으며 과거 파생 팩의 관측 전용 상태도 바꾸지 않았다. 원문 결과는 `derived-builders-final.json`이다.

팩 무결성은 원 Ref `PACK-BASELINE@31 / 54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac`로 확인했다. `uv run python -m tools.checklist_status --check`도 현재 원본 상태에서 성공했다. 이 값은 담당 2의 새 연결 완료 수가 아니다.

다음 확인 명령:

```powershell
git fetch origin main
git log -12 --oneline origin/main
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
uv run python -m tools.pack_owner2_readiness --check
uv run python -m tools.checklist_status --check
uv run pytest -q --basetemp=C:/Users/dudwh/.codex/tmp/pack-owner2-full-20261101
```

`pack_owner2_readiness --check`는 23개 실제 연결이 없으면 실패하는 준비 상태 검사다. 현재 0/23이므로 실패가 예상되며, 이를 테스트 회귀 통과나 전체 완료로 표현하지 않는다. 공통 선행 조건·미구현 producer·정책/실험 입력 부족을 해소하기 전에는 69개 전체 연결 또는 시험 전체 완료를 선언하지 않는다.

현재 연결을 진행하려면 PR183이 실제 병합된 origin/main의 커밋부터 새 통합 worktree를 만들고 이 전용 구현을 옮겨야 한다. 원 PACK31 seed의 정확한 Ref를 먼저 검증한 뒤 담당 2의 23개 CheckDef만 연결하며 revision·files_sha256·생성 문서는 통합 때 한 번 갱신한다. PR 병합만으로 위 native 9개 producer나 과거 전체 소스 승인 대기가 해결되지는 않는다. 해당 미구현을 UNKNOWN wrapper로 연결 수에 넣지 않는다. 현재 백그라운드 실행이나 자동 재개 예약은 없다.
