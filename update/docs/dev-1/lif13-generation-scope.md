# LIF-13 가중치 수명 행의 세대 결합 (개발 담당, 2026-11-06)

## 원 결과와 원인

2026-11-05 L4 실행(`dev1-gpu-g13-20261105/LIF-13`)은 `ENGINE_SENSOR_CALLBACK_SCOPE_INCOMPLETE`로 ABORTED, 세 축 UNKNOWN이었다.
원 EVD/RES(`RES-01M6NZZ5F15XWHZSVA84N7TMEF@1`)와 원자료는 그대로 둔다.

- 실행기는 같은 세션의 앞선 시험 행을 버리려고 프로세스마다 "경계 요청이 처음 보인 곳부터"만 쓴다(`window()`).
- 그런데 가중치 daemon은 HTTP 요청을 받지 않아 경계 요청을 볼 수 없고, 소비자 엔진은 기동 때(경계 요청 전) 가중치를 취득한다.
- 그래서 daemon 취득 3회·release 1회와 소비자 loader 행이 모두 빠져 소비자 0·release 미관측이 됐다.
- 원 콜백 행의 시각(`operation_started_ns`, 노드 monotonic): 엔진 취득 6251초 → 두 번째 소비자 취득 6284초(실행 전 수작업) →
  release 6301초(실행 안 lab 동작) → fallback 소비자 거절·디스크 적재 6327초(실행 뒤 수작업). 실행 안에는 release만 있었다.

## 고친 것

1. **세대 범위 결합**(`engine_callback_projection.weights_window`): LIF-13의 가중치 수명 지점 행
   (daemon `_handle_get_state`·`_handle_release`, loader `_request_state`·`_fallback_load`)은 경계 요청 대신 현재 엔진 컨테이너 세대로 고른다.
   - 수집 프로그램이 이 컨테이너 PID 1의 시작 시각을 같은 monotonic 시계로 환산해 함께 돌려준다(`container_start_monotonic_ns`).
     그보다 앞선 행(같은 세션 폴더의 이전 컨테이너)은 버리고 개수만 남긴다. 값을 못 읽으면 `container_generation_unverified`로 범위 미완이다.
   - 경계 요청이 엔진에서 처음 보인 시각(anchor)보다 앞선 release는 이번 실행의 release가 아니다(`release_before_run`). anchor가 없으면 `run_anchor_unobserved`.
   - 투영은 서로 다른 프로세스의 행을 같은 노드 monotonic 시작 시각으로 정렬한다. 같은 시각이면 CALL을 먼저 둔다.
   - 다른 관측 행은 지금처럼 `window()`를 쓴다. 시각 비교는 같은 노드 프로세스 사이에서만 하며 실행기 시계와 비교하지 않는다.
2. **정본 실행 안의 소비자 동작**(lab 전용, `weights_consumer_start`): 엔진(PID 1)과 같은 인자로 같은 컨테이너에 소비자 엔진을 하나 더 띄운다(포트 8001부터).
   같은 `--load-format ipc_cache` 설정이라 release 전에는 daemon에서 취득하고, release 뒤에는 거절된 뒤 디스크 fallback으로 뜬다.
   `/health` 200까지만 기다리며, 실행이 끝나면 이 실행이 띄운 소비자만 시작 ticks를 확인해 끝낸다(실패하면 `ENGINE_SENSOR_LAB_CONSUMER_CLEANUP_UNVERIFIED`).
   소비자 기동 두 번이 들어가므로 LIF-13만 실행 상한을 900초까지 받는다(다른 점검은 300초 그대로).

## GPU 재실측 사례

```json
[{"case_id": "before", "role": "before", "prompt": "hello", "max_tokens": 2},
 {"case_id": "consumer", "role": "switch", "action": "weights_consumer_start", "variant": "active"},
 {"case_id": "release", "role": "switch", "action": "weights_release", "variant": "active"},
 {"case_id": "fallback", "role": "switch", "action": "weights_consumer_start", "variant": "active"},
 {"case_id": "after", "role": "after", "prompt": "hello", "max_tokens": 2}]
```

- 세대는 11-05와 같은 daemon 부트스트랩(엔진 `--load-format ipc_cache`, zero_copy, fallback true)을 쓰고 `max_duration_seconds`를 600 이상으로 둔다.
- 엔진·소비자 둘의 GPU 메모리 비율 합이 L4 한 장 안에 들어가야 한다(11-05는 0.23씩 세 개로 동작).

## 진단 재투영(증거 아님)

11-05 원 콜백 행 전체에 새 투영을 적용하면 소비자 2, release 뒤 거절 1·fallback 1, 위반 없음이고 남는 것은 `release_authorization_policy_unavailable`뿐이다.
다만 release 뒤 행은 실행이 끝난 뒤 수작업 harness가 만든 것이다. 그래서 원 RES를 바꾸지 않고, 정본 실행으로 다시 측정한다.
release 주체의 기업 인가 정책은 입력이 없어 범위는 계속 미완(full_scope false)이다.

## 시험

`tests/test_engine_callback_weights_lifetime.py`(실제 vLLM 0.31.0 daemon·loader 코드를 감싼 행): 세대 결합 뒤 소비자 2·거절·fallback, 이전 컨테이너 행 제외,
경계 전 release 제외, 컨테이너 시작·anchor 미상 시 범위 미완, 소비자 동작의 LIF-13 한정·선언 daemon 결합, 띄운 소비자만 정리.
