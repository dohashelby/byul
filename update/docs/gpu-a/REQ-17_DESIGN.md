# REQ-17 재시험 통계 설계 (2026-11-02 확정, GPU 전)

첫 실증 G29REQ17: 집단별 6개, control AUC 0%, cross AUC 50%, native cross hit 5, `timing_control_has_no_power`(지금 규칙으로는 `timing_control_direction_reversed`). 결과는 UNKNOWN으로 보존한다. 이 문서는 다음 GPU 실행 전에 설계를 고정한다. 판정 규칙(`project_cache_timing`)은 바꾸지 않는다.

## 1. 첫 실증 결과의 원인 분석

아래는 소스와 CPU 측정으로 세운 설명이다. GPU에서 4절 확인 항목으로 검증한다.

### control AUC 0%: 센서가 적중한 요청만 느리게 만든다

- 첫 실증의 콜백 센서는 `KVCacheManager.get_computed_blocks` 외에 블록 단위 함수 `BlockPool.get_cached_block`, `BlockHashToBlockMap.get_one_block`도 감쌌다. vLLM은 적중 블록마다 이 둘을 부르고 첫 미적중에서 멈춘다. 그래서 적중 블록이 많을수록 감싼 호출이 늘었다.
- 감싼 호출은 전후로 원본 소스를 다시 읽고 compile해 확인했다. 로컬 macOS arm64에서 1회 1.7~2.1ms, 호출당 약 3.5~4.3ms였다(`REQ-14_REJECTION.md`).
- 첫 실증 prompt는 약 150 token(약 9블록)이었다. control-present는 블록 약 8개가 적중해 감싼 호출이 16개 이상 늘고, control-absent는 첫 블록에서 멈춘다. GPU에서 150 token prefill을 아끼는 시간은 1ms 안팎이라, 계측 시간이 적중 이득을 덮는다.
- CPU 실측(opt-125m, block 32)에서는 control AUC가 100%였다. CPU prefill은 token 수에 비례해 느리므로 적중 이득이 계측 비용보다 컸다.
- 블록 단위 이벤트는 어떤 투영·판정에도 쓰이지 않는다(`computed_rows`는 `get_computed_blocks` RETURN만 읽는다).

### native cross hit 5: 공격 쪽 요청끼리의 템플릿 적중

- 첫 실증 사례는 present와 absent가 모두 salt `other`를 썼다. Qwen2.5 채팅 템플릿의 system 문장과 실행 nonce가 앞 2~3블록(block 16)을 채우므로, 두 번째 `other` 요청부터는 이 블록이 적중한다.
- p-0은 앞선 `other` 요청이 없어 미적중이고, p-1~p-5가 적중해 5개다. 피해 쪽 비밀 prefix의 교차 재사용이라는 근거는 아니다.
- CPU 실측에서 0개였던 것은 opt-125m 템플릿이 짧아 block 32 하나를 채우지 못했기 때문으로 본다.
- `_hit`는 계산된 token이 0보다 크면 적중으로 센다. 그래서 같은 salt를 공유하는 사례 설계에서는 cross AUC가 우연히 기준을 넘으면 템플릿 적중이 위반 근거로 쓰일 수 있다. 판정 규칙은 그대로 두고, 사례 설계로 막는다(2절).

## 2. 고정한 설계

| 항목 | 값 | 이유 |
|---|---|---|
| 기대 방향 | control-present가 control-absent보다 빠름 | `project_cache_timing`의 정상 대조 조건 |
| prompt | 숫자만 1,500자, 사례마다 새로 생성 | Qwen2.5는 숫자를 한 자리씩 끊어 길이가 같으면 token 수가 같다. 템플릿·nonce를 더해 약 1,560 token, block 16으로 약 98블록 |
| 길이 상한 | 4,096자, 256블록 이하 | `validate_cases`와 `MAX_BLOCKS`. 넘으면 생성기가 거부한다 |
| 표본 | 집단별 24개(quartet 24개) | 아래 오탐 계산 |
| 워밍업 | 2쌍(새 prompt 미적중 → 같은 prompt 적중), role `prime` | 첫 요청 초기화 비용을 표본에서 뺀다. `prime`은 표본으로 쓰지 않는다 |
| 순서 | quartet마다 prime을 먼저 보내고, 나머지 4개(cp·ca·p·a)는 seed로 섞음 | 시간 흐름과 직전 요청 영향이 집단에 고르게 퍼진다 |
| salt | 피해 쪽 prime·cp는 `owner`, ca·p·a는 사례마다 고유 | 공격 쪽 자기 적중을 없애 `_hit`가 교차 재사용만 가리키게 한다 |
| 출력 | `max_tokens` 1, temperature 0 | 실행기가 고정한다. 시간 차이가 prefill에서 나게 한다 |
| 총 사례 | 4 + 24×5 = 124 | `MAX_CASES` 512 이하 |
| seed | 실행 전에 정해 이 문서 실행 기록에 적음 | 같은 seed면 같은 사례 목록(`cases_sha256`) |

사례 목록은 `python -m tools.gpu_a_req17_cohort --seed <값> --out <파일>`로 만든다. 출력의 `cases`를 manifest에 넣고 `session`·`target_sha256`·`environment_ref`를 붙인다.

### 표본 수 24의 근거

정책 기준 AUC 75%(DRAFT)에서, 교차 재사용이 없을 때(present와 absent가 같은 분포) cross AUC가 우연히 75%를 넘을 확률을 Mann-Whitney 정규 근사로 계산했다.

| 집단별 표본 | 기준을 넘을 확률 |
|---|---|
| 6 | 약 7.5% |
| 24 | 약 0.15% |

설계상 공격 쪽 적중이 없으므로 이때도 FAIL은 나지 않는다. 대신 `timing_gap_without_native_hit`로 UNKNOWN이 되고, 그 확률을 줄이는 값이다.

### 기록 예산

`/guard-lifecycle/data`를 읽는 경로는 세션당 6MiB까지만 읽고 나머지는 `truncated`로 버린다. 행 크기를 소스의 필드 구성으로 추정했다(98블록 기준).

| 센서 구성 | quartet당 | 24 quartet |
|---|---|---|
| 보수 전(블록 단위 함수 포함) | 약 709KB | 약 17MB. 읽기 상한과 파일 상한 16MiB를 모두 넘음 |
| 보수 뒤(`get_computed_blocks`만) | 약 95KB | 약 2.3MB |

## 3. 실행 전 조건

1. **센서 보수가 들어간 자산:** 블록 단위 함수를 감싸지 않고, 호출마다 compile을 다시 하지 않는 센서(`README.md` 보수 2·3)로 `prepare-engine-callbacks` 자산을 새로 만든다. 요청마다 감싼 호출은 `get_computed_blocks` 1개뿐이고 적중 길이와 상관없다. 첫 실증 때의 자산은 자산 해시가 달라 `load_prepared`가 거부한다.
2. 엔진 `max_model_len` ≥ 1,600, prefix caching 켬, block size 기록.
3. 새 `prepare-engine-callbacks` 세션에서 REQ-17을 먼저 돌린다. 같은 세션에서 앞 점검의 행이 쌓이면 읽기 상한에 먼저 걸린다.
4. 정책 `cache_timing_max_auc_percent` 75, `cache_timing_min_samples` 24는 시험용 DRAFT(`policy-a-draft.yaml`)다.

## 4. 실행 뒤 확인 (판정 전에 원자료로 본다)

| 확인 | 기준 | 어긋나면 |
|---|---|---|
| 읽기 잘림 | `truncated=false`, `ENGINE_SENSOR_CALLBACK_EVENTS_TRUNCATED` 없음 | 예산 재계산 뒤 다시 실행 |
| prompt 길이 | `get_computed_blocks` 행의 `request_block_hashes_sha256` 개수(전체 블록 수)가 모든 사례에서 같음 | 숫자 토큰화 가정이 틀림. 생성기 수정 |
| 적중 형태 | cp의 `num_computed_tokens`가 마지막 불완전 블록을 뺀 전체 블록 token 수, ca·p·a는 0 | p가 0이 아니면 교차 재사용 근거로 보존 |
| 계측 비용 균형 | 사례별 RETURN 행의 `native_started_ns − operation_started_ns`(호출 전 확인·기록 시간)의 집단 평균 차이가 1ms 미만, 사례당 감싼 호출 수가 같음 | 계측 편향이 남음. 판정을 결과로 쓰지 않음. 호출 뒤 확인 시간은 행에 남지 않으므로 호출 수로 함께 본다 |
| 정상 대조 | control AUC > 75 | 방향이 반대면 `timing_control_direction_reversed`, 원인을 순서·워밍업·계측부터 다시 본다 |

판정은 기존 규칙 그대로다: control AUC > 75이고 cross AUC ≤ 75이며 present 적중 행이 모두 있으면 full_scope. cross AUC > 75이고 present 적중이 있으면 위반이다.
