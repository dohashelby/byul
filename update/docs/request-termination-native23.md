# vLLM native23 요청 종료 관측

대상은 고정 vLLM 0.31.0(`db9527a46873454610df6dbedf79a36d6bf1a7f6`)의 V2 GPU runner다. 프로필은 `vllm-native-lifecycle-metadata/23`, 기록 schema는 `guard-native-lifecycle-events/23`이다. 기존 native8의 소스 로더·소유 파일·프로세스 시계 관측을 재사용한다.

## 설치

1. 기존 `prepare_assets(..., profile='vllm-native-lifecycle-metadata/23')`로 새 외부 출력 디렉터리에 자산을 만든다.
2. `sitecustomize.py`, `guard_lifecycle_sensor.py`, `guard_lifecycle_base.py`, `settings.json`과 manifest를 준비한다. 첫 네 파일은 `/guard-lifecycle`에서 읽기 전용으로 보인다.
3. `/guard-lifecycle/data/<manifest.session>`은 엔진 UID 소유의 0700 쓰기 경로로 먼저 만든다. 기본 `/guard-lifecycle`과 원본 자산의 쓰기 권한은 늘리지 않는다.
4. 실제 엔진 프로세스의 `PYTHONPATH=/guard-lifecycle`, `GUARD_LIFECYCLE_CONFIG=/guard-lifecycle/settings.json`, `--middleware guard_lifecycle_sensor.Middleware`를 확인한다. 새 Python helper에서 설치 함수를 호출한 결과를 서버 설치로 기록하지 않는다.
5. 추론 전에 실제 PID/start ticks, Pod UID/CID, source MODULE 결과, session inventory, 파일 SHA/UID/mode와 sink 쓰기 범위를 대조한다. 지원 CPython code contract는 센서의 닫힌 표에 고정돼 있다.

## 요청별 사실

- completions와 chat completions는 live HTTP invocation, 원 헤더 SHA, 실제 `assign_request_id` RETURN의 native ID SHA로 결속한다.
- Scheduler 취소 본문의 삭제 전 counter와 삭제 후 상태, V2 `RequestState`의 슬롯 상태를 기록한다. counter는 미배정 작업의 자동 완료 판정으로 사용하지 않는다.
- GPU 본문에는 실제 양수 token 스케줄 ID와 `finished_req_ids`를 구분한다. Event는 실제 작업 ID에만 결속된다.
- 실제 execute/sample 본문 뒤 observer 소유의 main/copy Event를 기록하고 bounded daemon에서 조회한다. 원 출력·예외·decorator·native cleanup은 유지된다.
- `AsyncOutput`은 weak identity로 생성·실제 get_output 반환·객체 수명 종료를 연결한다. tensor와 출력 원문은 저장하지 않는다.
- KV의 요청별 매핑과 같은 pool의 지연 해제 큐를 대조한다. 공유 block pool과 weights의 전역 메모리 0은 요청별 완료 조건에 추가하지 않는다.

## 판정 연결

기존 LIF-10 실행기는 profile23에서 연결 종료 후 선언된 관측 window를 기다린다. 세션을 먼저 seal하고 정상 대조 요청을 보내므로, 다음 요청이 지연 정리를 촉발해 원 잔존 상태를 가리는 순서를 피한다. 실제 기한 대조는 native monotonic clock으로 수행한다.

projector는 기존 PACK33의 `activation_steps`, `activation_violations`, `activation_missing`, `activation_scope_complete`를 출력한다. 요구 scope, 정상 대조, client close, policy window, seal, generation/clock/source/image/파일 coverage가 모두 맞아야 완료를 표시한다. 늦게 저장한 기록 시각만으로 실제 상태 조회가 기한 뒤였다고 추론하지 않는다.

full stream-set 조건은 eager·단일 TP/PP/DP·Qwen2·명시적 미사용 speculator/PP/PCP/aux/ubatch·기본 KV/EC connector와 NoopOffloader를 포함한다. 이 조건의 전체 request-relevant stream source qualification은 독립 검토 대상이다. 부분 Event와 미완료 coverage는 해당 범위와 UNKNOWN을 유지한다. CPU 예제는 실제 엔진/GPU 증거와 별도로 기록한다.
