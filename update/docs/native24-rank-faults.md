# Native24 단일 worker fault 관측

`vllm-native-lifecycle-metadata/24`는 고정 vLLM0.31.0의 실제 MultiprocExecutor·WorkerProc·FutureWrapper와 요청 ID 할당을 연결한다. 원13 rank 관측과 원23의 checked-loader/ASGI 구성을 재사용한다. 요청 종결·GPU 자원 해제는 별도 native23 기준으로 검증한다.

실행 선언은 TP/PP/DP1, `--distributed-executor-backend mp`, LAB native RPC 설정 `VLLM_EXECUTE_MODEL_TIMEOUT_SECONDS=5`다. 고정 원소스의 기본 RPC timeout은300초다. 판정기의 detection/notification/response 기한은 운영 복구 timeout과 구분한다.

## 준비

먼저 `/bin/sleep` holder를 기동하고 현재 PodUID/CID/start를 관측해 canonical ENV/native-target을 고정한다. 준비 시 ReadyFalse/prestart 표식은 별도 증거에 보존한다. Ready 뒤 ENV/target을 새로 발급하지 않고 실제 Ready·정상 canary·실 vLLM PID/start와 같은 세대를 별도 확인한다.

```sh
guard prepare-lifecycle-sensor \
  --profile vllm-native-lifecycle-metadata/24 \
  --fault-control-target-sha256 <canonical-native-target-sha256> \
  --output <new-private-directory>
```

`prepare_assets(..., profile=..., fault_control_target_sha256=...)`도 같은 기능을 제공한다. manifest 외9파일은 `guard_lifecycle_sensor.py`, `sitecustomize.py`, `settings.json`, `guard_lifecycle_base.py`, `guard_termination.py`, `guard_rank_failure.py`, `guard_fault_control.py`, `guard_fault_runtime.py`, `fault-control-settings.json`이다. 각 source dependency는 정확한 raw SHA로 검증한다. 자산을 RO로 설치하고 같은 inode에 최종 bytes를 반영한 뒤 같은 CID에서 원 vLLM을 child exec한다.

`PYTHONPATH=/guard-lifecycle`, `GUARD_LIFECYCLE_CONFIG=/guard-lifecycle/settings.json`, 기존 `guard_lifecycle_sensor.Middleware` 연결이 필요하다. native session의 data 디렉터리와 별도 `/guard-lifecycle/fault-control`은 engine UID 소유0700 RW다. assets와 parent는 원본 쓰기를 허용하지 않는다. control session은 manifest.session이고 target SHA는 실제 native-target 전체 canonical SHA다.

## 관측과 판정

ARM은 실제 ASGI invocation의 InputProcessor 할당 RETURN에서만 ASSIGNED로 연결한다. 선택한 WorkerProc RPC의 실제 GPUModelRunner 호출 직전에서 READY/barrier를 열고 최대30초 안에 release/timeout으로 닫는다. exception은 이 경계에서 원 RPC try 안으로 실제 RuntimeError를 전달하고 실제 catch→handle_output에서 EXCEPTION_CAUGHT를 기록한다. communication-stall은 실제 async_output_busy_loop caller·weak output 결속과 다른 thread의 고정 worker_busy_loop frame/self가 확인될 때만 응답 enqueue 경계를 지연한다. SIGSTOP으로 통신 정지를 대신하지 않는다.

Controller API는 `vllm_fault_control.validate_source_rank(value)`와 `validate_fault_case(packet, clientrow, injection_receipt, expected_scope, deadlines)`다. 파일 READY는 선택 자료이며, 판정은 현재 sensor/settings bytes와 canonical hash-chain을 다시 검증한 원 source packet을 사용한다. 정확한 요청·nonce·session·target·PID/start·clock, 실제 signal/activate 범위, native failure/Future/Core 수신, 최종 client error를 결속한다. `_wait_for_response`의 실제 exception 처리는 RETURN+future_has_exception이고, `FutureWrapper.result`의 RAISE와 구분한다.

살아있는 파일이 append되는 pre-signal 관측은 별도 `active_prefix_binding`으로 process/context와 동일 inode의 원 prefix를 검증한다. 원 process_context_verified·global census/closed 상태는 그대로 보존한다. SIGKILL의 열린 worker 기록은 pre-signal live prefix, 현재 동일 inode/hash-chain, 실제 PID/start SIG9 및 원 source monitor의 -9 관측으로 별도 `expected_killed_prefixes`에 기록한다. END를 만들어 넣지 않는다. 다른 읽기/이미지/세대/clock 공백은 UNKNOWN으로 남긴다.

반환7개 bool은 occurrence, overlap, detection deadline, upper notification deadline, affected response, clock, whole_kind_complete다. `whole_kind_complete`는 확정 FAIL을 포함한 기준 증거 완비 여부이며, 위반은 `violations`에 보존한다. 누락·기한과 겹치는 시간 구간은 `missing`으로 남긴다. 원 security verdict·worker GPU release 상한은 유지한다. 네 kind는 각각 새 generation/session에서 실행하고 finally release/CONT와 운영 복구를 수행한다.
