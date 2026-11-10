# LIF003 Gateway 혼합 부하 실행

`strict-criteria-actual-v2.json`의 CHK-LIF-003 D1·D2·D3·A1 원문4개와 conditional/remove_or_defer를 대조한 범위다. 이번 도구는 D1의 실제 혼합 부하·정상 주체 처리 기회, D2의 사전 LAB 예산/SLO, A1의 실측을 연결한다. 기존 body/n/output/context·전역동시성·connection·기한 cap 시험은 원 matrix/CPU 회귀와 정확 hash ref로 재사용한다. 요청별 GPU 정산은 해당 정책이 채택될 때의 조건부 기능이며 이번 필수 조건에 추가하지 않는다.

원 `run_mixed_budget_resume.py`와 그 서명은 보존한다. 이 도구는 **Gateway**를 통하고 A와 B의 서로 다른 인증 principal을 실제 원장에서 확인한다. 기존 HTTP client, `stream_probe.collect`, `record_for`를 그대로 사용한다. Gateway·limiter·collector·native 코드 수정은 없다.

## root가 먼저 고정할 LAB 설정

설정 예시는 다음과 같다. expected_scope·connection limit은 root가 승인된 현재 실행 설정에서 채운다. 조회된 현재 값을 자동으로 새 기준으로 채택하지 않는다. SLO5000ms는 선택 가능한 LAB 선언 예시이며 기업 SLO가 아니다. root가 해당 시험에서 사용할 수치와 처리 기회를 실행 전에 확정한다.

```json
{
  "schema": "byul-gateway-mixed-budget/1",
  "expected_scope": {
    "policy_sha256": "현재 승인 runtime policy SHA",
    "target_id": "현재 승인 target ID",
    "run_id": "현재 실행 run ID",
    "target_instance_id": "현재 실제 Pod/CID instance ID"
  },
  "principal_a": "client-a",
  "principal_b": "client-b",
  "normal_tokens": 8,
  "long_tokens": 128,
  "baseline_count": 1,
  "rounds": 1,
  "global_concurrency_limit": 2,
  "expected_connection_limit": 64,
  "normal_slo_ms": 5000,
  "required_underload_successes": 1,
  "request_timeout_seconds": 15,
  "max_group_seconds": 90,
  "observe_load_probes": 25,
  "observe_interval_ms": 20,
  "recovery_probe_count": 2,
  "recovery_interval_ms": 250,
  "caps_refs": [],
  "lif010_refs": []
}
```

각 ref는 `{"path":"/명시/검토된/공개결과.json","sha256":"원파일 SHA256","scope":"기존 body/n/output/context/global/connection/deadline cap 또는 LIF010 범위"}`다. symlink·상대경로·8MiB초과·hash 불일치는 실행 전에 거부한다. 참조의 원 판정을 유지하며 파일 확보만으로 내용을 PASS로 바꾸지 않는다. reference에는 token/auth private 파일을 넣지 않는다.

## 한 그룹의 실행 순서와 단위

1. 보호 viewer/API, 사전 고정 policy/target/run/instance, global cap2·idle0, connection cap, 출력허용128 이상을 확인한다.
2. B의 정상8token 기준 요청을 선언한 1~2회 실행해 HTTP 지연과 소표본 완료율을 기록한다.
3. A의 장기128token SSE 한 개가 실제 in-flight인 것을 bounded status poll로 관측한 뒤 다른 principal B의 정상 요청을 한 번 보낸다. 이 단계는 전역 slot 하나를 남겨 둔다. 선언한 1~2round 중 정한 횟수 이상 B가 정상 완료하고 LAB SLO를 지켜야 처리 기회가 PASS다. B가 계속429이면 FAIL이다. A 부하가 관측되기 전에 끝났거나 세대/원장이 미확인이면 해당 부하 증거는 UNKNOWN이다.
4. A 두 개의 실제 in-flight를 관측한 뒤 B 한 개를 보낸다. 정확 원장의 `CONCURRENCY_LIMIT`429/relay0을 확인한다. 전체 slot 포화의 B429는 위 정상 처리 기회와 구분한다. A가 빠르게 끝나 B가200이면 포화 거부 증거는 UNKNOWN으로 남긴다.
5. 부하 수집이 끝나면 사전 선언한 1~3개의 회복 probe를 정한 간격으로 실행하고 fresh B stream을 한 번 실행한다. 정상/stream 완료·SLO·최종 global slot0을 확인한다.

설정에 따라 inference POST는 최대13개, 요청당 A128/B8token, 총 출력 상한 최대584token이다. 위 예시는9POST/432token이다. 요청별 timeout과 전체 그룹 deadline을 함께 적용한다. load/기준/부하중 요청은 자동 재시도하지 않는다. 회복 probe만 사전 선언된 고정 횟수만큼 실행한다. status/원장 GET polling은 inference 재시도가 아니다.

```sh
# root가 기존 private env를 프로세스에만 로드한 상태에서 실행한다.
PYTHONPATH="$SOURCE/runtime:$SOURCE/runtime/src:$SOURCE/update/src" \
 "$PY" "$TOOL" --gateway http://127.0.0.1:18080 --model test-model \
 --config /명시/사전고정-LAB-config.json \
 --inference-a-env RUNTIME_INFERENCE_TOKEN_A --inference-b-env RUNTIME_INFERENCE_TOKEN_B \
 --viewer-env RUNTIME_VIEWER_TOKEN --out /명시/새-LIF003-실행
```

도구는 현재 승인 source의 helper/collector를 import하도록 PYTHONPATH를 고정한다. 앱 이미지 재빌드는 필요 없다. root는 source/도구/config 해시를 실행 기록에 남긴다. 새 output 디렉터리만 허용하며, POST 전에 CONFIG.json을 0600으로 저장하고 원값을 argv·로그에 출력하지 않는다. 결과 helper.json도0700/0600이다. raw 생성문·환경·token을 저장하지 않는다.

## D3와 판정 범위

반복/취소 후 엔진 잔존 비용은 기존 LIF010의 same-request native witness를 별도로 연결한다. 이 도구는 `lif010_refs`를 정확 hash 참조로 보존하고 `LIF003-LIF010-LINK`를 UNKNOWN으로 남긴다. 파일 참조나 Gateway slot 회복을 native 취소·엔진 비용 회수로 승격하지 않는다. 별도 실측과 UUID/세대/시간 결속을 root가 확인한 범위만 후속 원장에 반영한다.

caps 원증거와 이 도구의 실측, LIF010 결속은 서로 다른 근거다. 개별 mixed/SLO PASS와 전체 CHK 인수, 기업 보안 판정은 구분한다. 출력 `security_verdict`는 null이며 앞서 확인된 실패를 그룹 중단이나 UNKNOWN으로 덮어쓰지 않는다. 실제 SLO가 정해지지 않았다면 설정을 확정해 실행하거나 그 미정 상태를 별도로 기록한다.

## 로컬 검증

실제 loopback HTTP/stream/원장API로 다른 principal 처리 기회·포화429·fresh 정상/stream·slot회복을 확인했다. A1 중 B를 계속429시키는 반례는 처리기회FAIL, 포화429와 구분된다. 설정 cap불일치는 POST0, scope변경은 중단/이전 결과 보존, hash변조와 예산 초과는 사전 거부한다. 기존 product stream/global limiter/connection/HTTP deadline 및 request cap 회귀를 재사용했다. 실제 엔진/GPU/AWS는 실행하지 않았다.
