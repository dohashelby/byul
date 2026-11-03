# GPU B 수명주기 실행기 경계와 개발 기록

현재 실행 계약은 `3bba3c73a6bee1966b784cd90a9690e3e9d2512a` 기준이다. 개발 기록은 2026-11-02의 `origin/main` 2f28942에서 시작했다. `update/docs/evidence/lab-activation-cpu-20261102.json`과 아래 당시 시험 결과는 과거 CPU 원본이며 현재 source의 GPU/TP 실증으로 소급하지 않는다. 이후 별도 후보의 수정은 이 문서에 반영하지 않는다.

## LIF-08 주입 대상과 SIGSTOP

기존 `signal_worker`는 `/proc` 수집 배열의 마지막 PID를 골랐고 SIGCONT 때 다시 골랐다. 배열 순서나 재기동에 따라 다른 프로세스를 신호할 수 있었다. 이제 `worker_pod`로 Pod를 고정하고, 단일 후보이거나 `worker_pid`/`worker_start_ticks`, 또는 `worker_rank`와 첫 캡처 그룹이 있는 `worker_rank_regex`로 정확히 한 후보를 선택해야 한다. 주입 전과 Linux 컨테이너 내부 신호 직전에 PID의 `/proc/.../stat` 시작 tick을 확인하고, SIGCONT도 동일 Pod·PID·시작 tick에만 보낸다. 모호하거나 세대가 바뀌면 주입/해제 실패로 남긴다. 예시 설정의 rank 정규식은 실제 선정 executor 명령 형식과 확인한 뒤 입력해야 한다.

SIGSTOP 직후와 요청 종료 직후, SIGCONT 직전의 프로세스 상태를 기록한다. `T`/`t`가 확인되지 않거나 요청 종료 전에 다시 실행 가능한 상태가 되면 범위를 미완료로 둔다. 2026-11-02 CPU 기록의 약 15초 뒤 200 완료는 당시 PID·시작 tick·SIGCONT 전 상태가 없어서 원인을 특정할 수 없다. 현 코드는 요청 future의 `result()`가 반환된 뒤 `finally`에서 SIGCONT를 보낸다. 이 순서만 보면 실행기 SIGCONT가 200 완료보다 앞섰다고 주장할 수 없다. 엔진 자체 복구, worker 교체, 다른 프로세스의 신호 여부는 새 상태 기록과 엔진 로그가 필요하다. SIGSTOP은 프로세스 지연이며 통신만 멈춘 별도 장애를 증명하지 않으므로 `COMMUNICATION_STALL_INJECTION_UNOBSERVED`로 둔다. CPU 단일 worker 시험은 TP rank 실증이 아니다.

후속 리뷰 반영: exception 파일은 선택한 Pod UID에 고정해 쓴다. 고정 callback 파일 protocol에는 rank 지정/확인 필드가 없으므로 이 방식으로 선택 rank 예외 발생을 증명하지 않으며 `EXCEPTION_RANK_ATTRIBUTION_UNOBSERVED`를 남긴다. 모든 challenge 완료 표시는 전체 scope가 채워졌을 때만 한다. 별도 fault에서 FAIL을 관측했더라도 미실행 통신 정지나 rank 예외를 시도 완료로 표시하지 않는다. 현재 `_one_fault`는 관측 예외·조기 반환을 포함한 모든 종료 경로의 `finally`에서 복원을 시도한다. SIGSTOP 해제도 같은 target에 시도하며 오류를 따로 남긴다. 복원은 명시적인 `True`만 확인으로 인정한다. `False`·`None`·예외이면 앞 fault의 FAIL과 원 오류를 보존하고 missing·cleanup error를 추가한 뒤 다음 fault를 주입하지 않는다. `run_scenario`도 일반 수집 예외 종류에 관계없이 마지막 `finally` 복원을 시도한다.

추가 코드 조사: 과거 소스의 `_one_fault`는 `running.result()`가 돌아온 다음 `finally`에서 SIGCONT를 호출한다. 기록의 delay 200/15,016ms와 stall 200/18,324ms는 그 반환값이다. `CLIENT`의 HTTP timeout은 정책 30초+5초, `Lab.requests`의 kubectl timeout은 120초이며, `DockerCluster._run`의 subprocess timeout도 전달값이다. 15초 SIGCONT 타이머나 `trap`은 `lab_activation.py`·`lab_activation_cpu_smoke.py`에 없다. `DockerCluster`의 `docker restart -t 2`는 시나리오 복구에서만 호출된다. CPU smoke의 `--launch-script`는 외부 필수 인수이고 과거 기록에는 경로/내용이 없어 그 스크립트의 trap·finally·timeout은 확인할 수 없다. 로컬 vLLM 0.31.0 참고 소스에서 `SIGSTOP`/`SIGCONT` 직접 호출은 검색되지 않았다. 따라서 과거 약 15초 완료를 내부 자동재개나 수정 완료로 귀속하지 않는다.

## LIF-03/10/11 범위

LIF-03은 부하 전/중 RSS, 대기·실행 요청 집계 지표와 각 HTTP 결과를 기록한다. 이 값만으로 요청별·단계별 예산, KV/GPU 사용량과 비용 회복을 증명할 수 없다. 확인된 RSS·지연 위반은 그대로 FAIL로 보존하면서 나머지는 `activation_missing`에 남긴다.

LIF-10은 연결 종료 요청에 고유 클라이언트 `X-Request-Id`를 붙여 결과와 기록한다. 집계 `vllm:num_requests_running == 0`은 그 ID/attempt의 Core abort, worker 완료, 전송·참조 회수를 특정하지 못한다. 집계 실행 요청이 0보다 큰 경우도 다른 정상 요청 때문일 수 있어 해당 요청의 기한 위반 FAIL로 단정하지 않고 `AGGREGATE_RUNNING_NOT_REQUEST_ATTRIBUTED`로 남긴다. 현재 실행기는 stream 연결 종료만 수행하며 별도 취소·timeout 경로를 수행하지 않는다. 요청 직접 결합이 생기면 그때 확인된 지속 위반을 FAIL로 판정한다.

LIF-11은 선택한 Pod/UID의 PID·시작 tick과 `/dev/shm` 이름의 전후 목록을 기록한다. 재조회 때 같은 이름의 Pod가 다른 UID로 교체되면 예전 세대의 inventory로 취급하지 않는다. `CENSUS`는 읽기 실패·ENOENT·PID start 교체·열거 실패/변동을 `process_scope_errors`와 전후 열거수에 보존한다. target이 행에서 보이지 않는다는 이유만으로 종료를 주장하지 않고, `process_scope_complete is True`, 빈 오류 목록, 같은 비음수 정수 전후 열거수가 모두 확인될 때만 target PID/start 부재를 `death_observed`에 쓴다. legacy·필드 누락·모순·부분 목록이면 `SELECTED_WORKER_DEATH_CENSUS_INCOMPLETE`와 종료 미관측을 남기며 관측한 다른 process 행과 독립 FAIL은 보존한다.

종료가 확인된 worker 뒤 동일 Deployment 소유의 새 세대가 관측됐지만 Ready가 되지 않은 경우의 `NEXT_START_BLOCKED_AFTER_WORKER_FAILURE`, 안정된 새 engine의 직접 정상 요청 실패는 별도로 판정한다. 단순 재기동·항목 개수만으로 IPC 소유자·GPU 자원 반환·실패한 그룹의 회수 기한을 증명하지 않으며 전체 범위는 미완료다. `good_manifest`는 hash와 YAML 스키마·대상·실제 mount를 검증한다. 세션은 컨트롤러 로컬 hostPath가 아니라 UID가 고정된 실제 Pod에서 읽고, 장애 전 보존한 원 세션과 복원 세션을 구분한다. 고정 reader만 `-I -S -B`로 실행하고 parent dirfd no-follow·최종 파일 NONBLOCK/no-follow·소유자/권한·크기/JSON·전후 파일 및 Pod/CID/restart/start/node/mount를 확인한다. 검증된 whole sensor RO+data RW와 A204의 callbacks RO/data RW 분리 mount는 허용하며, 다른 가림·subPath·권한 불일치는 거부한다. 복원이 기존 기한 안에 확인되지 않으면 다음 장애/전환을 실행하지 않는다.

### A 공통 센서 입출력 요청

모든 record에는 `target_id`, 선정 executor backend, Pod/container ID, worker PID와 시작 tick, monotonic 시각 기준, 세션/실행 세대를 넣어야 한다. 사건별 `request_id`와 `attempt_id`는 HTTP 클라이언트 ID만 믿지 않고 API 접수→Core→worker→전송 계층에서 같은 요청에 연결되어야 한다. 연결이 끊긴 record·세대 불일치·시각 기준 불명·관측 간극은 해당 범위 미완료다. 관측하지 않은 비용을 0으로 채우지 않는다.

| 점검 | 필요한 입력 필드와 결합 | 완료 주장 거부 조건 |
| --- | --- | --- |
| LIF-03 | `request_id`/`attempt_id`별 admission 결론·queue 진입/퇴장, 입력/출력 토큰·n·priority, scheduler queue/KV 점유, GPU 사용량, 전처리/실행/전송 단계 비용 및 단위·허용 오버헤드·누적량·예산값·집행 시각. 동시 정상 대조 ID와 부하 ID를 같은 시간축에서 결합하고 거부 후 후속 소비·회복을 확인 | 집계 RSS/HTTP/metric만 존재, 정책 한도·단위 미정, 단계·요청·전체 예산 중 하나라도 누락, 미디어 수용 경로의 실제 decode/누적 입력 미관측, 거부 뒤 후속 소비 불명 |
| LIF-10 | 각 종료 요청의 `request_id`/`attempt_id`, 취소/연결 종료/timeout 접수 시각, Core abort/finished, worker 실행행 종료, queue/KV 참조·전송 참조·GPU 사용 종료/반환 시각과 소유 ID. 정상 완료와 종료 시나리오를 별도 결합 | HTTP close 또는 집계 running=0만 있음, 해당 ID의 Core/worker/참조 행이 하나라도 안 보임, 취소·timeout 경로 미실행, 세대/ID 귀속 불명, 정책 `termination_required_scopes` 중 한 범위 누락 |
| LIF-11 | 실패 worker의 PID·시작 tick·rank·그룹 ID, 실패 시각, 같은 소유 그룹의 process/IPC socket/shared memory/GPU allocation 목록과 고유 ID·소유·반환 시각. 실패 전후와 재기동 세대를 나눠 inventory 차집합 및 새 요청을 결합 | `/dev/shm` 이름·개수나 컨테이너 종료만 있음, 자원 소유/그룹 연결 불명, 정책의 required resource kind·absence deadline 미설정 또는 한 자원이라도 미확인, 재기동만 성공 |

센서는 원본 측정 상태·오류도 반환해야 하며, 실행기는 측정 불가를 `activation_missing`, 확인된 기한 초과·잔존을 `activation_violations`로 독립 보존해야 한다. 선정 executor에서 이 연결이 생긴 뒤 같은 시나리오의 실제 관측과 정책 기한을 재대조해야 한다.

현재 선택 진단은 필수 Service 관측·판정 뒤 별도 남은 예산에서 수행한다. `_optional_observation_end`는 apply·마지막 Ready 조회·정상 요청의 기존 호출 한도와 cfg Ready 창·poll을 남기며, 진단 성공을 위해 원 기한을 늘리지 않는다. `Lab._kubectl`은 남은 기한 안의 양수 ns request-timeout을 사용하고 진단 기록도 주입된 clock을 사용한다. 선택 진단/artifact 실패는 앞선 FAIL을 덮지 않는다.

## LIF-14 GB03 이후 범위·실패 주입 준비

GB03의 실제 L4/TP1 정상 start/stop/next start/next stop 200 및 trace 3개·CUDA kernel 258/258/282 관측은 정상 분기의 자료다. 원본 ZIP을 재작성하지 않는다. 당시 passive cleanup의 `PROFILER_CONTROL_SESSION_SEAL_UNAVAILABLE`과 실패 뒤 재시작 미실행 상태를 함께 보존해야 하므로 전체 LIF-14 범위 완료는 아니다. 선택 Lab `_profiler` 경로는 다음 start와 stop 사이의 정상 요청과 세 번째 trace 수를 기록한다. passive 수집/cleanup 오류가 있거나 backend stop 실패 분기를 수행하지 않으면 Lab의 `activation_scope_complete=false`, 전체 challenge `attempted=false`를 유지하고 확인된 정상 분기의 독립 위반은 보존한다. 이 선택 계층과 canonical `KubernetesProfilerControlExecutor`는 구분한다. lab 설정이 없으면 기존 factory의 `KubernetesProfilerObservationExecutor`가 LIF-14 profile에 따라 `KubernetesProfilerControlExecutor`로 dispatch하여 지원된 `normal` 8단계 start/inference/stop/next-start/stop을 수행한다. A207을 포함한 3bba는 stop/next-stop의 미종결 공백 중 같은 session의 sealed final이 실제 해소한 것만 `profiler_control_provisional_missing`으로 보존한다. canonical은 주입 runner가 없는 기본 경로에서도 원 EvidenceDraft origin이 EXAMPLE이고, 기존 팩의 `activation_*` 필수 관측은 미수집 UNKNOWN으로 남는다. 이를 측정 등급·GPU 성공·질문 전체 완료로 계수하지 않는다. 독립 backend 종료·output·인증·cross-process와 stop-failure 미실측은 계속 false/UNKNOWN이다.

기존 `profiler_lifecycle_cpu.py`/`tools/lif14_profiler_cpu.py`는 검토 고정 vLLM 0.31.0 CPU source의 소유 임시 경로에 일반 파일을 먼저 만들어 `torch.profiler`의 기본 trace writer가 디렉터리 생성에 실패하는 두 번째 사례를 실행한다. 실제 source의 `WorkerProfiler._call_stop`은 `_stop()` 예외를 경고로 기록하지만 `_running=False`로 바꾸므로, API 200 또는 wrapper 상태만으로 native 종료 성공을 주장할 수 없다. CPU 사례는 실패 지점과 소유 경계의 참고 자료이며 GPU backend·다중 rank·HTTP 제어·재기동의 실증 근거가 아니다.

GB03 뒤 당시 GPU 실패 run 준비 구상(미실행): 소유한 별도 격리 세션/Pod의 trace 경로를 새 전용 디렉터리로 고정하고, 해당 경로에 예상 바이트·UID·mode를 기록한 일반 파일 collision을 **엔진 시작 전에** 둔다. `lstat`으로 경로와 부모가 symlink가 아니고 소유 UID·경계 안에 있는지 확인하며 원 mode와 복구 절차를 저장한다. 정상 세션의 trace 경로는 건드리지 않는다. 시작 전 정상 GPU 요청과 활성 수집을 확인하고 stop을 호출한 뒤 API 결과, worker별 warning, native 수집 상태, 실제 trace writer 실패와 산출물, 자원 반환을 같은 세션/시각에 결합한다. 오류 뒤 일반 파일이 동일 바이트/UID인지 확인해 소유 경로 안에서 제거하고 디렉터리를 복원한 다음 **같은 시험 환경을 재기동**해 정상 요청·새 profile start/stop·실제 trace 산출을 확인한다. 소유·nofollow·원상 복구 확인이 안 되면 주입하지 않고 UNKNOWN으로 둔다. 이 구상은 현재의 주입 승인이나 복원 보장 근거가 아니다. GPU 설정/trace 경로·worker별 native stop/경고·봉인 연결과 기존 기한 내 복원 조건이 검증되기 전에는 재주입하지 않는다. 이 문서는 해당 실측 수행을 주장하지 않는다.

## 전환·기동·재시도 계약 보강

LIF-05/06의 기존 `0.5`초 대기는 진행 중인 HTTP 요청의 첫 stream body 도달 증거가 아니다. 새 opt-in `probe_progress_directory`는 실제 probe Pod에 미리 준비한 디렉터리다. CLIENT는 HTTP body 첫 조각을 읽은 후 고유 파일을 `O_EXCL|O_NOFOLLOW`로 만들고, 부모는 같은 probe Pod에서 그 regular-file 표식을 확인한다. 표식을 본 순간 future가 아직 끝나지 않았을 때에만 전환 전 겹침을 관측했다고 기록한다. 디렉터리가 없거나 표식이 없거나 요청이 이미 끝났으면 `activation_missing`에 남긴다. CLIENT의 `first_chunk_elapsed_ms`는 요청 내부 경과 시간일 뿐이며 부모와 시계가 맞는다는 가정으로 겹침을 증명하지 않는다. 표식은 시나리오 뒤 같은 경로에서 제거를 시도한다. 준비 경로와 삭제를 확인할 수 없으면 미완료다.

LIF-06에서는 `generation_models.before`를 전환 전 정상·진행 요청에, `generation_models.after`를 새 세대와 실패 세대 동안의 요청에 사용한다. 복원된 정상 세대는 before 별칭으로 대조한다. 예시 `allowed_models`에는 두 별칭을 모두 넣었다. after 별칭을 old 세대에 보내 얻은 404를 전환 실패로 간주하지 않는다. 이 별칭으로 구분할 수 없는 동일 모델 내부 weights·cache 혼합은 별도 관측이 필요하다.

LIF-07은 rollout 전 Pod UID 목록을 기록하고 새 UID의 Pod Ready를 구분한다. old Pod의 Ready/200만으로 완료하지 않는다. after 별칭 응답과 새 Pod Ready의 시간 관계는 후보 관측으로 남기되 응답 Pod 귀속 없이는 조기 접수 FAIL을 만들지 않는다. LIF-12는 고유 클라이언트 ID 두 개를 남기지만 전역 `prompt_tokens_total` 증감으로 같은 요청의 실제 attempt 수를 추론하거나 retry FAIL을 내지 않는다. A의 `request_id`/`attempt_id`별 입력 소비·retry 사건이 연결되기 전까지 `REQUEST_ATTEMPT_ID_AND_TOKEN_ATTRIBUTION_UNOBSERVED`다.

후속 리뷰의 직접 반례: probe 파일과 아직 끝나지 않은 클라이언트 future는 **클라이언트 수신 겹침**만 확인한다. 서버 작업은 이미 끝났고 probe 버퍼 읽기만 남았을 수 있으므로 LIF-05/06에는 `SERVER_INFLIGHT_REQUEST_AND_GENERATION_UNOBSERVED`를 항상 남긴다. 서버 request ID→worker→weights/cache 세대 active 시각이 묶여야 전체 범위를 채울 수 있다. 3bba에서는 실제 첫 body 표식과 아직 끝나지 않은 요청이 전환 호출과 겹치고 원 요청이 timeout이면 `INFLIGHT_REQUEST_HUNG_ACROSS_TRANSITION`을 보존한다. 그 겹침 근거가 없으면 `INFLIGHT_TIMEOUT_WITHOUT_SERVER_ATTRIBUTION`으로 남긴다. 이 요청 수준의 위반은 서버 request→worker→weights/cache active 범위 전체를 확인했다는 뜻이 아니므로 위 native 미관측은 유지한다. 한 응답 안에 서로 다른 모델 별칭이 나타난 독립 위반 및 완료된 원 요청의 위반은 후속 수집·progress 정리 오류와 별개로 보존한다.

LIF-06의 faulty 세대는 적용 전 UID와 다른 Pod UID 및 선언된 `failure_marker`를 실제 그 Pod 로그에서 확인해야 부분 실패가 활성화된 것이다. 레이블만 `faulty`인 Ready Pod, marker가 없는 200 응답, 다른 Pod의 응답은 `PARTIAL_CHANGE_ACCEPTED` 근거가 아니다. 새 faulty UID·marker·요청 귀속 중 없는 항목은 각각 미완료로 남긴다. 같은 새 faulty UID의 marker와 Ready가 함께 확인된 위반은 독립 FAIL로 유지한다.

2 Pod 반례에서는 새 faulty Pod A에만 실패 marker가 있고 새 faulty Pod B만 Ready였다. 전역 `activated`와 `served`를 합치면 거짓 FAIL이므로, 이제 UID별 marker 집합과 현재 Ready 집합의 교집합이 있을 때만 `PARTIAL_CHANGE_ACCEPTED`를 기록한다. 두 집합이 서로 다르면 `FAULTY_MARKER_READY_UID_MISMATCH`를 남긴다.

같은 Pod UID에서도 engine 컨테이너가 재시작될 수 있다. LIF-06 FAIL에 쓸 marker는 선택한 `container`의 **현재** 로그에서만 찾고, 읽기 전후 `containerID`·`restartCount`·running `startedAt`와 Pod UID가 동일해야 한다. `--all-containers`/`--previous`의 이전 engine 또는 sidecar marker는 진단 목록에만 남긴다. 현재 engine 로그의 marker와 Ready가 같은 컨테이너 실행 신원으로 결합되지 않으면 `PARTIAL_CHANGE_ACCEPTED`를 기록하지 않는다. 두 반례와 로그 조회 중 컨테이너 교체 반례를 CPU 계약으로 고정했다.

LIF-07의 새 Pod UID/after 별칭만으로 필수 rank·모델·backend 준비가 끝났는지 보이지 않는다. 또한 Service HTTP 응답은 어떤 Pod의 것인지 직접 연결되지 않아 Ready와 404/200의 선후만으로 조기 접수 또는 미준비 수락 FAIL을 만들 수 없다. `STARTUP_NATIVE_RANK_MODEL_BACKEND_UNOBSERVED`와 `STARTUP_RESPONSE_POD_UNATTRIBUTED`를 항상 남기고 과거 Service 불일치의 미귀속도 보존한다. 필수 Service 관측창이 끝난 뒤 복원 reserve 밖에 충분한 예산이 있을 때만 현재 새 Pod 하나를 직접 확인한다. 요청 전후 Ready·동일 Pod/CID/node/IP가 유지되고 실제 HTTP 5xx이면 `READY_BEFORE_INFERENCE_AVAILABLE`을 보존한다. timeout/error/403/404나 Ready·identity 교체는 이 FAIL로 승격하지 않는다. 직접 응답은 Service 귀속이나 native rank/model/backend 준비의 전체 증명이 아니다.

## 당시 직접 시험 기록 (2026-11-02)

- 명령: `uv run --python 3.12 pytest -q tests/test_lab_activation.py tests/test_lab_activation_platform.py`
- 첫 실행: 50 passed, 2 failed, pytest 6.23초, 부모 wall 10.978초. 기존 `test_passive_and_active_facts_merge_into_a_completed_evidence`, `test_passive_failure_does_not_block_active_measurement`의 완료 기대가 새 보수적 범위 조건과 충돌했다. 기대를 미완료 증거 계약으로 수정했다.
- 둘째 실행: 57 passed, 1 failed, pytest 2.16초, 부모 wall 3.370초. 모호한 선택 시험의 helper가 BASE의 선택 설정을 재합친 것이 원인이었다. 실제 무선택 설정을 직접 전달하도록 수정했다.
- 최종 직접 시험: 58 passed, pytest 2.04초, 부모 wall 2.952초. 이후 Pod/부하 지표/요청 ID 수정 후 58 passed, pytest 2.04초, 부모 wall 2.952초. (직전 Pod 수정 직후 실행은 58 passed, pytest 2.00초, 부모 wall 2.852초.)
- Windows 기본 전체 첫 실행 `uv run --python 3.12 pytest -q`: 수집 중 `tests/test_b_produced_ids.py:16`의 기본 cp949 `read_text()`가 `UnicodeDecodeError: 'cp949' codec can't decode byte 0xec in position 156`으로 오류. 19 deselected, 1 error in 17.79s, 부모 wall 19.248초. PASS 아님.
- `PYTHONUTF8=1`로 같은 전체 시험 재시작: 약 3% 시점까지 다수 `F`가 보였다. 부모의 Windows 환경 부적합/중복 시험 중단 지시로 2026-11-02 12:34:54 UTC 무렵 Ctrl-C 중단했다. 출력에 최종 pytest 요약·부모 wall은 없으며 PASS로 해석하지 않는다. Linux 3.12.3 통합 기본 suite는 부모 담당이다.
- 리뷰 후 직접 계약: `uv run --python 3.12 pytest -q tests/test_lab_activation.py tests/test_lab_activation_platform.py`에서 61 passed, pytest 22.13초, 부모 wall 23.529초. 새 사례는 2 Pod 대상 UID/예외 파일/전후 inventory, 최종 cleanup 실패 뒤 선행 FAIL 보존과 challenge 미완료, 무관 요청의 집계 running 비귀속이다. 전체 suite는 재실행하지 않았다.
- fault별 복구와 최종 복구의 예상 밖 예외도 각각 `activation_missing`·`cleanup_errors`에 남기도록 후속 보강했다. 동일 직접 계약 61 passed, pytest 1.93초, 부모 wall 2.762초. 전체 suite는 재실행하지 않았다.
- GB03 범위 보강 직접 계약: `uv run --python 3.12 pytest -q tests/test_lab_activation.py tests/test_lab_activation_platform.py`에서 63 passed, pytest 2.38초, 부모 wall 3.496초. 두 번째 profile의 정상 요청/trace, 실패 분기 미실행, passive cleanup 오류로 범위 미완료를 확인했다. 전체 suite는 재실행하지 않았다.
- 전환·기동·재시도 첫 묶음 직접 계약: 같은 명령에서 69 passed, pytest 2.07초, 부모 wall 2.892초. 준비된 probe의 첫 body 표식/진행 future 결합, 누락 시 UNKNOWN, before/after/복원 별칭, old Pod Ready 배제, 전역 retry metric의 비귀속을 시험했다. 당시 새 세대 HTTP/Ready 단독 조기 접수 위반 기대는 다음 반례 보강에서 철회했다. 전체 suite는 재실행하지 않았다.
- 후속 반례 보강 직접 계약: 같은 명령에서 71 passed, pytest 2.18초, 부모 wall 3.009초. 클라이언트 버퍼 겹침만 확인된 경우 서버 겹침 미확인, 서버 귀속 없는 timeout의 거짓 FAIL 차단, faulty 레이블 단독 수락 오판 차단, LIF-07 HTTP/Pod 미귀속의 거짓 FAIL 차단을 추가했다. 전체 suite는 재실행하지 않았다.
- 새 faulty Pod UID가 없는 marker/Ready도 수락 FAIL로 오판하지 않는 반례를 더했다. 같은 명령 최종 72 passed, pytest 2.11초, 부모 wall 2.947초. 전체 suite는 재실행하지 않았다.
- 마지막 2 Pod 교차 반례 직접 계약: 같은 명령에서 73 passed, pytest 2.05초, 부모 wall 3.276초. Pod A marker와 Pod B Ready를 하나의 수락 위반으로 합치지 않는 것을 확인했다. 전체 suite는 재실행하지 않았다.
- 같은 Pod 안의 컨테이너 경계 직접 계약: 같은 명령에서 최종 76 passed, pytest 2.22초, 부모 wall 3.065초. 이전 engine·sidecar marker 및 현재 로그 읽기 중 containerID/restartCount/startedAt 교체를 현재 Ready의 FAIL로 합치지 않는다. 전체 suite는 재실행하지 않았다.
## 현재 상태·세대 결합 경계

OBS-02의 worker 조회 실패·부분 CENSUS를 완전한 0명으로 바꾸지 않는다. 신호를 보낸 Pod UID에서 직접 health 전후 모두 위 완전성 조건의 0명을 관측하고 같은 CID/node 및 후속 census 뒤 신원까지 유지될 때만 `HEALTH_REPORTED_AFTER_WORKERS_GONE`을 보존한다. 같은 CID에서 worker가 다시 나타나거나 읽기·열거·신원 확인이 불완전하면 해당 분기를 missing으로 두며 다른 독립 FAIL은 유지한다. Service health 응답만으로 선택 Pod의 위반을 만들지 않는다. 이 추가 health 요청과 후속 census/신원 조회는 같은 선택 deadline 안에서 수행하고 `finally`로 원 deadline을 복구한다. 예산이 부족하면 추가 조회 전에 생략 사유를 남겨 마지막 복원의 기존 호출 예산을 보존한다. LIF-05는 실제 update manifest와 서로 다른 전후 별칭을 사용하며 복구 시 before 별칭을 사용한다. LIF-09의 실패 표식과 Ready는 새 Pod의 동일한 현재 engine 컨테이너에 속해야 한다. 이전 컨테이너·sidecar·다른 Pod의 표식으로 현재 Ready를 위반으로 판정하지 않는다.

당시 상태·세대 결합 CPU 계약은 `uv run pytest -q tests/test_lab_activation.py tests/test_lab_activation_platform.py tests/test_lab_activation_observation_state.py tests/test_lab_activation_redeploy.py tests/test_lab_activation_partial_start.py`에서 90개 통과했다. 이는 GPU 질문 전체 완료를 뜻하지 않는다. Ready가 false인 실패 컨테이너의 현재 표식 인증과 요청별 worker·GPU 완료·자원 회수는 직접 관측이 없는 한 미완료로 남는다. 원본 증거·판정과 저장 팩 Ref는 변경하지 않는다.
