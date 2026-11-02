# GPU 시험 전 점검 엔진 완료 상태 (2026-11-02)

## 무엇이 PASS까지 갈 수 있나

| 묶음 | 점검 | PASS 근거 | 실측 |
|---|---|---|---|
| lab 능동 활성화 | LIF-03~12, LIF-14, LIF-16, OBS-02, REQ-12 (14개) | `executors/lab_activation.py`가 소유 네임스페이스에서 장애·재배포·연결 종료를 일으키고 부모가 직접 측정한 `activation_*` 사실 | 실제 vLLM 0.31.0 CPU, kubectl 대신 docker 대역 (`docs/evidence/lab-activation-cpu-20261102.json`) |
| native 콜백 | REQ-15, REQ-16, REQ-17, REQ-18 | `guard_engine_callbacks` 준비 자산이 고정 소스 메서드를 감싸 남긴 행 | 실제 vLLM 0.31.0 CPU, REQ-15는 runtime Gateway 경유 포함 (`docs/evidence/engine-callback-cpu-20261102.json`) |
| 기업 시스템 서명 기록 | LIF-01, LIF-02, PLT-03, PLT-05, SUP-07, SUP-09~12, SUP-15, OBS-01, PLT-10~12, REQ-02, REQ-04~06 (18개) | `executors/record_attestation.py`가 검증한 `byul-signed-record/1` 기록 | 대역 기록만. 기업 내보내기 도구는 고객 환경에서 연결 |
| 기존 연결 | 나머지 | 기존 수집기 | 과거 기록만 |

REQ-14는 같은 콜백으로 출력 귀속 위반을 FAIL로 잡지만, 중간 tensor 버퍼를 보지 못해 PASS는 GPU 다중 rank 시험이 필요하다.

## GPU가 꼭 필요한 것

REQ-19(kernel 접근 범위), REQ-20(CUDA Graph 재실행), REQ-21(TP 통신 buffer), LIF-13(weights daemon release)은 수집기 코드가 없다. GPU kernel·CUDA·native IPC를 관측해야 하므로 GPU 환경에서 관측 지점을 확인하며 만든다. GPU 할당 후보 40개 중 나머지 36개(조건부)는 코드는 있으나 GPU·다중 rank 구성의 결과를 CPU로 대신할 수 없다.

## 실행

- lab 활성화: `examples/update-lab-activation.toml`. 네임스페이스에 `byul.lab/owned=true`가 있어야 한다. 실제 엔진 확인은 `tools/lab_activation_cpu_smoke.py`.
- 콜백 센서: `guard prepare-engine-callbacks --output <폴더> [--lab-faults]`로 자산을 만들고 엔진 Pod의 `/guard-lifecycle/engine-callbacks`에 읽기 전용으로, `/guard-lifecycle/data`를 쓰기 가능으로 붙인 뒤 `PYTHONPATH`에 넣는다. CPU 확인은 `tools/engine_callback_cpu_smoke.py`. CPU 엔진은 `--block-size 32 --enable-prefix-caching`이 필요하다.
- 서명 기록: `{"schema":"byul-signed-record/1","system","check_id","environment_sha256","issued_at","facts":{...},"challenge_steps_completed","resolves":[...],"signature"}`. 서명은 signature를 뺀 정규 JSON의 HMAC-SHA256이다. 점검별 facts 키는 `record_attestation.ATTESTED`가 정한다.

## 실측에서 고친 결함

- 콜백 기록이 메모리 상한 1024행에서 끊겼다. 파일로만 보내게 바꿨다.
- 같은 엔진을 다시 시험하면 앞선 실행의 캐시가 교차 적중으로 보였다. 실행마다 nonce를 붙이고 경계 요청 이후 행만 쓴다.
- 쓰는 중인 마지막 행을 읽어 JSON 오류가 났다. 줄바꿈 전까지만 읽는다.
- REQ-14 출력 대조가 실제 worker 호출 순서(execute_model → sample_tokens → handle_output)와 맞지 않았다.
- 끝나지 않은 실행을 MEASURED로 만들려다 INV-02에 걸렸다. DECLARED로 남긴다.
- Gateway는 temperature 필드를 거부한다. Gateway 경로에서는 보내지 않는다.

## 아직 사람이 정할 것

- 각 deadline·budget 값과 REQ-17 `cache_timing_max_auc_percent`·`cache_timing_min_samples`는 기업 기준이다.
- 서명 기록 키 관리와 기업 시스템별 내보내기 도구는 고객 환경에서 정한다.
- LIF-08 delay·stall에서 SIGSTOP한 worker가 약 15초 뒤 재개됐다. 재개 원인은 확인하지 못했다.

## 2026-11-02 활성화 범위 보강

- LIF-16: 복원 대상(ConfigMap·Secret)의 승인 해시를 시험 전후로 대조하고, 변조 사본 복원이 Ready가 되지 않는지, 승인 원본 복구 뒤 Ready Pod가 변조 사본을 참조하지 않는지 본다. volume·snapshot 내용은 읽지 않으며 snapshot 분기는 별도 시험이다.
- LIF-09: faulty 세대가 선언한 한 구성요소만 실패했다는 로그 표식(`failure_marker`)을 확인해야 범위가 찬다.
- LIF-06: 두 세대의 응답 모델 별칭이 달라야 범위가 찬다. 같은 별칭 안에서 일부 weights·cache만 바뀌는 혼합은 이 방법으로 보이지 않는다. 한 stream 응답에 별칭이 둘 이상이면 LIF-05·06 위반이다.
- 실패 중 응답이 허용 세대 밖에서 오면 위반이다.
- REQ-17: 정상 대조가 기대와 반대 방향으로 갈리면 `timing_control_direction_reversed`로 따로 남긴다.
- CPU 실측 기록의 LIF-06·09·16 값은 보강 전 시나리오의 결과다.

