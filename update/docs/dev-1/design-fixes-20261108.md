# 설계 보수 진행 기록 — 2026-11-08

사용자는 기능 추가를 멈추고 감사 D01~D12를 수정하도록 지시했다. 커밋하지 않는다. 과거 EVD/RES/POL/PACK 및 `/1` 기록의 bytes·판정 의미를 보존한다.

## 묶음 1 — worker 범위 보존

`scoped_consumption`은 명시적인 `/2` writer/reader이며 `/1` source와 등록8종을 수정하지 않는다. worker의 실제 closed native admission을 별도 신원으로 기록한다. scheduler retry 횟수나 실제 GPU tensor 읽기와 혼동하지 않는다. 단계별 기대 rank 집합은 실행 계획에서 입력받고, 관측된 worker 집합으로 채우지 않는다. 모르는 rank/admission·한 rank 누락·source 미종료는 gap이며 독립 옵션 변경 후보는 남긴다.

새 scoped sensor는 원 CALL/RETURN과 실제 TP rank를 보존한다. GPU 수집 도구의 새 runner 기록은 이 writer를 사용하고, legacy prepared에서 필요한 scope metadata가 없으면 complete로 올리지 않는다. 새 생산자는 GPU 재확인 전 미등록이다. 과거 `/1` writer는 역사 재현용으로 보존한다.

요청·worker·프로세스 시작 ticks·native admission·단계·rank를 함께 보존한다. 기대 요청 목록도 실행 전에 고정한다. 원 행이 아예 없는 요청, 기대 rank를 줄인 계획, 세대를 읽지 못한 경우 모두 완료를 거부한다. worker admission 신원은 scheduler retry 횟수가 아니다.

## D01~D12 반영 범위

아래는 코드·계약 보수 상태다. GPU·SGLang·TensorRT-LLM의 새 실행 qualification이나 기업 배포 승인을 뜻하지 않는다. 과거 계약은 그대로 읽고 새 실행은 별도 계약을 선택한다.

| ID | 반영한 변경 | 남겨 둔 검증 경계 |
|---|---|---|
| D01 | `scoped_consumption` `/2`, V1/V2 실제 rank·PID·시작 ticks·closed admission, 기대 요청/단계별 rank 대조. 동일 worker 값으로 다른 worker를 제거하지 않음 | 새 생산자는 미등록. TP2 실제 GPU 확인 전 complete 불가 |
| D02 | `engine_provider`로 managed 준비·launch·권한 환경·이미지 entrypoint·자원 한계·template source 계약 이동. application/sandbox/readiness는 provider를 호출. 미구현 optional 계약은 명시적으로 부재 처리 | SGLang/TRT는 기존 HTTP 어댑터 범위. managed launch는 아직 미지원으로 표시 |
| D03 | `engine_profiles.json` + resolver, feature/버전별 가용성, exact image·모델·backend의 구현 선택. 공통 Pod 메타데이터는 버전 제한에서 분리 | 검토되지 않은 native source는 거부. 0.32/0.33 native를 자동 허용하지 않음. 기존 length-cause 0.32 한정 프로파일은 별도 선택 |
| D04 | 준비와 실행이 같은 resolver를 사용. code_available/profile_available/runtime_qualified를 구분. 등록된 외부 Kubernetes 실행을 transport 선택으로 처리 | 정적 SUPPORTED는 실행 source·신원·GPU·rank 검증 완료가 아님 |
| D05 | 기능별 명시 의존성과 wire schema로 새 release 신원 계산. receipt는 engine/version/image/backend/TP/단계별 rank/origin에 결합 | legacy `/1` source와 등록8종은 동결. 새 source의 hash를 자동으로 등록하지 않음 |
| D06 | 엔진별 source-analysis provider, repository 기본값·규칙·진입점·CHG의 profile 신원 분리. CLI `diff --engine` | SGLang/TRT 규칙 미검토 영역은 전부 unmapped → 기존 full-scope fallback 유지 |
| D07 | `guard.reference`에 source-CPU collector·fixture builder·순수 slot reader와 독립 release pin을 패키징. 기본 설치 경로는 tools/tests/worktree를 요구하지 않음 | 외부 엔진 source와 CPU 의존성은 별도 준비. 명시적인 component_root/producer_checkout은 과거 재현 모드로 보존 |
| D08 | 새 `execution-v2`는 선언된 constraint 버전·출처를 동등 시험 조건에서 분리. 실제 probe 옵션·launch·모델·정책·하드웨어 비교 유지. 새 harness 준비도 동일 projection 사용 | `execution-v1`과 기존 객체 해시는 보존. 임의 설정 차이를 무시하거나 비교 결과를 소급 변경하지 않음 |
| D09 | `guard-sensor-run-binding/2`와 새 CheckDef profile로 실행 SHA를 cfg binding에 둠. 원 팩을 복사해 새 DRAFT revision을 만드는 이행 함수. 수동 log ceiling도 버전 있는 순수 계약으로 분리 | 기존 팩33/POL은 유지. 새 revision을 선택해야 새 binding 계약이 적용됨. 기업 정책 확인/승인을 자동 이전하지 않음 |
| D10 | 새 acceptance는 immutable PLAN의 선정 집합에서 필수 EVD/RES 쌍 도출. 과거 5점검 함수는 legacy-five/1로 보존. 현황은 팩 정의 hash 대조, CI/설치본 conformance 추가 | 시험 통과로 엔진 qualification receipt를 발급하지 않음 |
| D11 | 고정 prefix snapshot·inode/offset cursor·bounded page·원 page hash/파일 보존. core callback과 Gateway도 새 page transport 사용. 늦은 읽기 실패·불연속·projection 상한·journal 손실은 gap | 프로세스 16MiB와 projection 4096 상한 유지. 무한 보존/무손실 장기 수집을 주장하지 않음. 과거 pinned transport는 변경하지 않음 |
| D12 | 새 scoped release는 초기 source bytes 검증→호출별 code/stat 교체 감지→최종 bytes 검사. bounded 비동기 journal로 callback 디스크 I/O 분리. 큰 parameter 내용 해시는 기본 off, 명시적 CPU debug에서만 선택 | 새 계측은 functional/security 범위. timing qualification은 false이며 독립 on/off 대조 전 성능 근거로 승격하지 않음 |

## 새 실행 계약과 호환 경로

`tools.gpu_a_producers_collect`의 새 runner 출력은 항상 `/2`다. 과거 준비 자산을 읽더라도 rank/admission/세대가 비면 gap이다. `--runner-scope`에는 `runner_received`, `sampler_applied`의 기대 rank 배열을 넣는다. sampler가 특정 rank에서만 실행되는 구성도 별도로 적을 수 있다. `--expected-requests`에는 실행 전에 정한 요청 HMAC 배열을 넣는다. 누락 시 관측된 목록으로 추정하지 않는다. 원 page는 출력 옆의 `.pages` 디렉터리에 배타 생성한다.

새 receipt는 위 단계별 rank 계획까지 정확히 맞아야 한다. TP2 receipt를 backend/버전/TP1로 재사용하거나 rank 집합을 줄여 통과시킬 수 없다. production qualification 목록은 비어 있고 EXAMPLE receipt는 시험의 메모리 patch에만 존재한다.

기존 `runtime/kubernetes_http.py`, `gpu_input_producers.py`, `runner_consumption_v2_producer.py`와 기존 callback source pin을 보존한다. 공통 파일을 직접 바꾸어 기존 source receipt를 깨뜨리는 방식 대신 새 `engine_http` 초기화·page transport와 새 producer release를 추가했다. 기존 등록8종을 새 다중 worker 계약으로 해석하지 않는다.

`execution_conditions(..., profile="execution-v1")`는 과거 projection을 유지한다. 현재 HTTP 실행은 `/2`를 사용한다. `constraint_engine_version`은 실행 시 ENV와 여전히 일치해야 한다. 그 문자열이 실제로 사용되는 sampling 경계값이라는 주장을 하지 않는다. 실제 경계값·probe 예산·fixture는 계속 비교한다.

새 sensor pack은 `guard.runtime.sensor_binding.migrate_pack(source, new_output)`으로 생성한다. 기존 정책의 보안 기준과 judge 규칙은 그대로 두고 기술 SHA 네 개를 policy_params에서 분리한다. 새 팩은 DRAFT이며 원 팩/POL을 덮어쓰지 않는다. 실행 설정 예시는 다음과 같다.

```toml
[engine_sensor.binding]
schema = "guard-sensor-run-binding/2"
sensor_environment_sha256 = "<exact ENV SHA-256>"
sensor_target_sha256 = "<exact target SHA-256>"
sensor_prepared_sha256 = "<exact prepared SHA-256>"
sensor_manifest_sha256 = "<exact manifest SHA-256>"
```

수동 로그 판정의 기존 `legacy-native-info/1`은 기존 collector/trusted-fields 조건을 유지한다. 다른 엔진의 collector는 새 CheckDef/judge 계약 판본에서 지원해야 한다. 기존 계약에 collector를 추가해 과거 UNKNOWN을 바꾸면 안 된다.

source-CPU 새 설치 모드는 REQ-18 설정에서 component_root를 생략하고 SUP-13 설정에서 producer_checkout을 생략한다. SUP-13의 독립 collector/callback 승인 SHA는 계속 필요하다. 외부 vLLM source 검증과 CPU 의존성 검증은 생략하지 않는다. 과거 명시적 checkout 모드는 원 frozen verifier를 보존한다.

## 검증과 보존

- 관련 회귀 1,185 passed / 1 skipped, pytest 20.45초, 부모 20.67초. 기존 건너뜀은 원 source-component 실행 시험이다. 최종 journal의 첫 행 이전 전체 유실까지 추가한 경계48개도 통과(0.62초). 총합으로 중복 집계하지 않는다.
- 범위: 새 12개 경계, legacy 생산자·runner V1/V2, 기존 callback, source selection/rename, 준비·managed launch, 요청 귀속 FAIL 보존, acceptance, 로그 판정·재판정, 원 CPU 연결 fixture. 기존 assert를 삭제하거나 완화하지 않았다.
- 중간 회귀 실패와 수정 후 결과를 별도 파일로 보존했다. 예: legacy optional 호출부 치환 오류, fixed-five API 호환, stub generation-readiness 거부 복구. 최종 통과를 최초 시도 통과로 기록하지 않는다.
- 설치 검증은 offline wheel을 새 venv에 설치하고 저장소 밖 `python -I`에서 수행한다. source/wheel/설치 파일 bytes 대조, packaged source-CPU 모듈 검증, PLAN/EVD/RES/RPT fake 예시 실행, 원 pack 재판정·export/restore·변조 거부, network/subprocess 차단을 확인한다. 실제 엔진/GPU 실행은 아니다.
- 시작 snapshot의 `.guard`·팩·과거 seed·fixture·공개 증거 보호 파일359개 bytes 동일. 원 등록8종, 기존 Ref·FAIL·판정 규칙은 보존한다. reviewed-source manifest는 새 구현 bytes로 새로 고정하며 과거 receipt 값은 덮어쓰지 않는다.

원자료는 [설계 보수 검증 산출물](/Users/doha/.codex/visualizations/2026/11/08/01a1b99a-d7c6-79e0-8db2-4cb4b4cc71ed/design-fixes)에 있다. `regression-1/2/3`, `installed-current`, `before.json`, `sensor-run-binding-pack-ref.json`을 함께 남긴다. 검토 가능한 새 DRAFT 팩34의 Ref는 `8e0d4229443fba328c1be66124f03016faf0c742bdd863fc459900f609aacb4a`이며 저장소 기본 팩33을 바꾸지 않았다.

## 이어서 필요한 실증

기능 추가는 계속 보류한다. 새 scoped release의 TP2 실제 GPU qualification, 허용할 native 인접 릴리스의 source/entry qualification, SGLang/TRT 실제 launch·native 구현, 독립 계측 on/off 대조는 완료되지 않았다. 이 작업에서 source pin을 자동 갱신하거나 이를 지원 완료로 집계하지 않았다. AWS·모델 다운로드·실제 엔진 기동·새 의존성 다운로드·커밋·push는 하지 않았다.
