# SUP-10 plugin runner 지원 계약 (개발 담당, B 의존 반환 2, 2026-11-08)

B 요청(`gpu-b/consolidation-20261106/DEPENDENCY_RETURNS.md` 2번): 기존 `plugin_loading` 10사례·pre/post enum과 별개로,
distribution/entrypoint/class source·선택 ModelConfig/renderer·request/model 세대·실제 IO/효과와 그룹별 허용/거부를 결합하는 지원 버전.
기존 10사례 완전성을 새 raw 하나로 충족시키지 말 것. VLLM_PLUGINS가 logits에도 보장된다고 가정하지 말 것.

## 반환물

| 항목 | 내용 |
|---|---|
| 계약 | `guard-plugin-runner/1` — `executors/plugin_runner.py` (`fixtures`·`identity`·`clean_events`·`project`·`evaluate`) |
| 실제 생산자 | 같은 모듈의 `RUNNER`와 무해 배포물(`byul-plugin-runner-probe` dist-info·entry points, IO processor 2종) |
| 수집 도구 | `tools/plugin_runner_capture.py` — **별도 생산자 Pod**(엔진과 같은 이미지·모델 폴더)에서 실행 |
| 기존 계약 | `plugin_loading`(v1 10사례)·`supply_boot_contract._plugin`은 바꾸지 않았다. general·endpoint·logits는 v1 범위 그대로 |

## 무엇을 결합하나 (IO processor 그룹)

- 선택: `EngineArgs(model).create_engine_config()` → `renderer_from_config(config)` → `get_io_processor(config, renderer)`.
  플러그인은 모델 config.json의 `io_processor_plugin`으로 선택된다(vLLM 0.31.0 원 경로, `plugin_from_init` 아님).
- 생성자가 받은 ModelConfig(hf_config 정규 JSON SHA-256)를 생산자가 만든 설정과 대조, renderer 생성 여부·형식.
- 요청별 pre/post 처리의 요청 ID SHA-256과 실제 출력 SHA-256을 생산자가 보낸 요청·받은 출력과 대조.
- `get_io_processor`는 지목된 플러그인 하나만 만들지만 그 전에 그룹의 **모든 허용 플러그인의 등록 함수**를 호출한다.
  `VLLM_PLUGINS` 허용 목록만이 그 호출을 거른다.

## 사례·판정

| 사례 | VLLM_PLUGINS | 모델이 지목 | 기대 |
|---|---|---|---|
| io-config-approved | byul-approved | byul-approved | 선택·생성·pre/post, 설정·요청·효과 일치 |
| io-config-unapproved | byul-approved | byul-unapproved | not installed 거부, 비승인 코드 0 |
| io-config-allowlist-unset | 없음 | byul-approved | 선택. 비승인 등록 함수 호출은 **사실**로만 남김 |
| io-config-none | byul-approved | 없음 | 플러그인 생성·처리 없음 |

위반: 허용 목록이 있는데 비승인 코드 실행, 비승인 선택, 다른 ModelConfig에 묶인 생성, 요청 ID가 다른 IO 효과, 지목 없는데 생성·처리.
허용 목록이 없을 때의 등록 함수 호출은 이 계약만으로 기업 정책 FAIL로 바꾸지 않는다.

## 실제 생산자 결과 (k3d vLLM 0.31.0 CPU 이미지, opt-125m, arm64, EXAMPLE)

네 사례 모두 기대대로이며 `complete=true`, 위반 없음. 허용 목록 없음 사례에서 `byul-unapproved` 등록 함수가 실제로 호출됐다(사실).
원 행: `tests/fixtures/plugin_runner_k3d_20261108.json`.

처음에는 서빙 엔진 컨테이너 안에서 돌렸다가 메모리 상한(4Gi)을 넘겨 k3d lab 엔진 Pod(`serving-engine-dev1-k3d-18`)가 OOM으로 종료됐다
(restartPolicy Never라 Failed). 이후 같은 이미지·모델 폴더의 별도 생산자 Pod(메모리 8Gi)에서 측정했고 그 Pod는 지웠다.

## 확인

`tests/test_plugin_runner.py` 19개: fixture 신원·모델 경로 검사, 실제 선택의 설정·요청·효과 결합, 허용 목록 차단·미설정 사실,
변형한 실제 행의 위반 5종, 미설정 등록만으로 위반 아님, 선택 사례 미완 5종, 형식 오류는 위반 아님, v1 계약 불변.

## B 연결 방법(제안)

`supply_boot_contract`의 SUP-10 평가에 `plugin_runner.evaluate(rows)`를 별도 분기로 더한다(행의 `contract`로 구분). v1 행은 기존 `_plugin` 그대로.
GPU 이미지의 실제 설치 distribution·그룹별 기업 승인은 B·운영 입력이다.
