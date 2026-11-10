# SGLang 요청 종결 관측 프로필

`sglang-native-lifecycle-metadata/1`은 단일 CUDA scheduler의 기본 텍스트 경로를 위한 선택적 계측이다. 고정 대상은 SGLang 0.5.22의 OCI manifest `5fc09edb6a0bb1d5b6992b543723c66d08f86d7a507e951e87e0d05db4ac766e`다. 기존 native8의 변경하지 않은 sink·source/code gate·PID/start·clock context를 재사용한다. 기존 vLLM 프로필의 바이트와 판정은 바꾸지 않는다.

기존 wire의 `commit`/manifest `public_commit` 필드는 이 프로필에서 위 OCI SHA-256을 담으며, source reference 종류는 `oci-manifest-sha256`이다. Git 커밋으로 표시하지 않는다. 등록 시 이 프로필의 명시적 source reference와 assets를 선택해야 한다.

프로필 자격은 실제 runtime context와 scheduler에서 TP/PP/DP 1, speculative/PD/HiSparse/dLLM/continuous-input-polling 비사용, CUDA 장치, overlap 비사용을 관측해야 성립한다. 시험 배포는 `--disable-overlap-schedule`을 사용한다. 부가 옵션 지원 전체나 분산 실행을 검증하는 프로필이 아니다.

관측 범위는 다음과 같다.

- `/v1/completions`, `/v1/chat/completions`의 서버 `X-Request-ID` hash·ASGI invocation·실제 disconnect
- `TokenizerManager._init_req_state`에서 최종 native RID와 frontend state 연결
- frontend 삭제, scheduler queue/batch, KV row·할당 길이·Mamba 상태의 요청별 변화
- 동일 batch operation의 forward/copy/schedule/return CUDA stream에 기록한 observer Event의 bounded `query()` 영수증

Event는 tensor·native batch를 보관하지 않으며 device synchronization, native 상태 복구, 예외 변경을 하지 않는다. 최대64개 Event와5초 query 예산을 둔다. sink는4096행·4MiB에서 닫히고 관측 누락·상한·source 불일치는 미확인으로 남긴다. GPU 전체 메모리 감소나 후속 요청 성공으로 요청별 회수를 대신하지 않는다.

`project_native`는 같은 session의 host-검증 capture, 정확한 PID/start·image·source·clock, 유일한 ASGI/native 연결, 기한 내 각 상태 해제와 마지막 operation의 모든 stream 영수증을 요구한다. 하나라도 부족하면 전체 종결은 미확인이다. 기술적 종결 사실과 producer 인증·조직 보안 판정은 분리되며 `security=UNKNOWN`과 `producer_authenticated=False`를 유지한다.

고정 tag에서 보존한 source fixture는 source/code gate의 후보 기준이다. 실제 OCI 내부 파일 SHA가 모든 literal과 일치해야 계측이 설치된다. 테스트의 AST·CUDA Event 대역과 합성 capture는 CPU 계약 검증이며 실제 엔진/GPU 인수 성적으로 계산하지 않는다. 일반 collector/parser 등록 및 실제 Kubernetes capture는 별도 통합 경계다.
