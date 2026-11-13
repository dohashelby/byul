# D7 엔진 실행 설정 감시

승인 환경 `byul-environment/0.2`의 `deployment.launch_settings`를 플랫폼 bundle의
서버 값 → `target.json.launch_settings` → `TargetRegistration.launch_settings`로 전달한다.
환경·정책 판본 해시와 bundle 파일 해시가 기존 승인 경로에 결속한다. 0.1 문서는 기존
형태로 수용하고 승인 실행 설정이 없는 것으로 다룬다. 실행 설정의 bundle 수동 override는
거부하며 환경 판본을 갱신해야 한다. 키 생략은 비교 제외,
명시적 null은 승인값 확인 안 됨이며 false로 대체하지 않는다.

엔진 종류는 target의 `engine.kind`, 컨테이너는 환경의 `deployment.engine_container`에서
변환한 Kubernetes 등록을 사용한다. 해당 Pod spec의 command와 args를 합쳐 허용 목록만
추출한다. 출처 `kubernetes_spec`은 선언값이며 실제 프로세스 실행값의 증명이 아니다.

| 키 | vLLM 0.31.0 기본값 | SGLang 0.5.22 기본값 |
| --- | --- | --- |
| trust_remote_code | false | false |
| trust_request_mm_kwargs | false | not_applicable |
| code_revision | null | not_applicable |
| revision | null | null |
| tokenizer_revision | null | not_applicable |
| enable_multimodal | not_applicable | false |

키의 `_`를 `-`로 바꾼 플래그를 읽는다. vLLM은 `_` 별칭과 bool의 `--no-...`도 처리한다.
판본 플래그의 `--flag=value`/`--flag value`, bool 플래그의 유무를 정규화한다.
감시 플래그(긍정형·vLLM bool의 no-형)나 config의 진부분 접두어는 argparse 약어일 수 있다.
약어의 모호성과 값 소비 범위를 추정하지 않고 이후 파싱을 멈추며, 그 관측의 적용 가능한
키를 모두 unknown으로 남긴다. vLLM은 `_`를 `-`로 정규화한 뒤 접두어를 검사한다.
기본값은 위 확인 판본 기준이며 다른 판본의 기본값 변화는 별도 검증이 필요하다.
플래그가 없다는 것은 해당 spec 선언에 없다는 뜻이다. 이미지 entrypoint·환경 변수의
숨은 설정은 검증하지 않는다. command와 args가 모두 없거나 shell wrapper,
`--config` 사용, 잘못된 값, 컨테이너 누락·다중 Pod·조회 오류·stale이면 unknown이다.

허용된 bool과 128자 이내 판본 값만 결과에 남긴다. 판본의 URL·치환식·제어문자·주소는
unknown으로 처리한다. 실행 인자 전체, 다른 플래그 값, 설정 파일 경로는 저장하지 않는다.
호스트 스냅샷도 원본 command/args 대신 엔진별 허용 목록 결과만 전달한다.
호스트 수집기의 `--target-container`는 기본 `engine`이며, 다른 이름이면 등록 컨테이너와 맞춰 지정한다(실행 설정·단계 시간표 공통, Kubernetes 이름 검증).
오래된 호스트 수집기가 만든 스냅샷에는 실행 설정이 없어 unknown으로 표시된다.

`/api/baseline`의 `launch_settings.*` 비교 행은 플랫폼 대상 엔진 화면의 기준 비교에
출처·승인값·관측값·상태로 표시한다. 승인값 없는 관측 행은 unapproved, 대응 설정이
없는 키는 not_applicable로 표시하고 비교하지 않는다. 확정값의 차이는 기존
`config_drift` 흐름으로 재검증을 요청하며 동일 근거는 중복 생성하지 않는다.
unknown은 이전 drift를 해제하지 않는다. 설정이 같아지면 현재 차이 근거가 해소되지만
열린 재검증 요청의 최종 처리는 기존 운영 절차를 따른다. 자동 차단·재시작은 없다.

주 근거는 CHK-SUP-002이며 현재 선정 CHECKS에 없어서 추가하지 않았다.
CHK-SUP-008·014의 rules에 `launch_settings_drift`, sources에 target·revalidation을 연결했다.
선정 37개 범위와 PACK 해시·판본은 유지한다.

update의 BYUL import 경계는 공유 Contracts 검증기를 사용하므로 0.1·0.2를 모두 받는다.
`guard.collectors.file.load_environment`는 BYUL 문서가 아닌 guard ENV/파일 축약형을 읽는다.
BYUL deployment는 변환된 guard ENV에 넣지 않고 원본에 보존하므로 update 실행기가
launch_settings를 사용해 검증했다고 주장하지 않는다. update 코드는 변경하지 않았다.

검증(2026-11-12, 기준 `byul_runtime` `5b2c9ae6`, 커밋 없음): D7 37건을 포함한 runtime
관련 124건 통과·8 skip. 보충 전체는 runtime 1,614 통과·49 소켓 실패·130 skip,
API 459 통과·2 실패·1 skip이다. API 실패는 소켓 1건과 기존 gateway-core 운영 시험의
일시적 VALIDATION_FAILED 1건이며, 후자는 단독 재실행 통과(원인 미확정)다.
지정 uv 실행은 의존성 DNS/캐시 제한으로 실패했고, 전체 보충 실행은 기존 venv·캐시와
외부 EpollSelector 대기 보조 설정을 사용했다. 표준 CI 전체 통과로 보지 않는다.
web 작업자 1개 시험 216건·빌드, 기존 venv의 contracts check(13 스키마·11 예시),
`git diff --check`는 통과했다. 명령은 `/tmp/byul-d7-test-commands.txt`에 남겼다.
실제 kind/AWS·엔진 프로세스 시험과 다른 판본 기본값 확인은 수행하지 않았다.
