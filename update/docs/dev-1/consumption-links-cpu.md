# 실제 CPU 소비 연결 — LIF-06·SUP-03·REQ-09·10 (2026-11-08)

사용자 지시에 따라 커밋 없이 기존 구성요소 지원을 실제 엔진 소비 경로로 확장했다.
기존 GPU 생산자 8종의 등록·B 전용 소비자·판정·팩33·원 GPU Ref/FAIL은 유지한다.
아래 fixture는 별도 k3d CPU Pod에서 실행한 **EXAMPLE**이며 전체 점검 PASS나 GPU 실측이 아니다.

| 분기 | 이번 실제 연결 | 남은 경계 |
|---|---|---|
| LIF-06 | 외부→내부 요청 ID의 원 assignment, admission 객체·attempt, 실제 전송 payload, worker 상태, 모델 forward. 진행 중 요청에서 원 `reload_weights`의 부분 반영 후 예외와 명시 복구, 같은 worker·상태의 후속 4단계 | 선택한 CPU parameter 하나의 bytes만 관측. 전체 weights 원자성·자동 rollback·요청별 KV kernel read·GPU 미확인 |
| SUP-03 | 실제 boot의 Python FD read→config 내용 해시→worker의 HF config. tokenizer 로더 객체→실제 cached tokenizer 복사→요청 renderer 객체 | native Rust vocab/merges 읽기 귀속, 모든 보조 공급물, fresh boot 승인·집행 미확인 |
| REQ-10 | 실제 API→Scheduler→worker `_update_states`→실제 모델 logits→동일 SamplingMetadata·Sampler. exact allowed token 집합, top-k/p·temperature·penalty, 실제 regex grammar 적용 | V2·모든 옵션·GPU 미확인. 별도 기존 typed 거부 3사례는 구성요소 시험으로 유지 |
| REQ-09 | raw RGB/그레이 이미지→실제 vLLM typed MM feature→Scheduler→실제 worker gather→전처리 반환→모델 `inputs_embeds`→Sampler. 반복 이미지·후속 text 포함 | 랜덤 소형 Qwen2-VL·합성 tokenizer·`load_format=dummy`. 승인된 2B snapshot·GPU 미확인 |

## 닫힌 원 참조와 값 대조

`vllm_execution_link_callback.py`는 기본 꺼짐인 `execution_links` 프로파일이다. source bytes·원 함수 code와
CALL/RETURN 또는 CALL/RAISE 자격을 검사한다. 기존 `request_attempts`·`generation_transitions`·`runner_consumption`·
`transition_dispatches` 프로파일과 같은 지점을 중복 감싸지 않도록 준비 시 거부한다.
기존 프로파일이 꺼진 새 관측기에 가려지지 않도록 켜진 관측기를 지점별로 선택한다.

실제 `assign_request_id`의 staticmethod를 유지하고 외부 ID와 무작위 내부 ID 및 실제 typed 입력 fingerprint를 기록한다.
SchedulerOutput의 요청·토큰·블록·계산 카운터·MM feature를 transport 양쪽에서 해시해 정확히 대조한다.
시각·alias·접두사·서로 다른 PID의 객체 신원을 join 근거로 쓰지 않는다. worker 상태는 원 `_update_states`의 반환에서 얻는다.
모델 forward·Sampler·grammar의 부모 frame도 고정 원 함수의 code와 대조한다.

sampling은 실제 모델 logits와 SamplingMetadata 객체의 동일성을 확인한다. 허용 token은 개수뿐 아니라 실제 집합의 HMAC도 대조한다.
CPU tensor만 제한된 크기로 읽고 GPU tensor를 CPU로 옮기거나 동기화하지 않는다.
MM에서는 typed feature 신원, gather embedding bytes, 전처리 반환과 실제 모델 인자의 동일 객체 및 선택 image 위치의 bytes를 대조한다.
hash만 출력하며 원 이미지·토큰·tensor 내용·요청 본문은 fixture에 저장하지 않는다.

`vllm_auxiliary_link_callback.py`는 검토된 `get_config`, `_from_pretrained`, `get_cached_tokenizer`의 반환과
원 read를 loader call 참조로 묶는다. config는 프로세스 사이 **내용 일치**이고 tokenizer는 같은 프로세스의 실제 복사 계보다.
같은 파일명이나 같은 프로세스라는 이유로 연결하지 않는다. 관측 sink가 실패해도 원 반환과 원 예외를 유지한다.
허용 목록 미입력과 명시 빈 목록을 구분하며 후자는 연결된 읽기에 독립적인 미승인 후보를 낸다. 집행이나 기업 승인을 대신하지 않는다.

## 실제 실행

CPU 이미지 digest `045fb648a61cfcd32d4b9ca5d0c391013afadae0178d2e23ac292597f2f64036`, Python3.12.15/arm64.
사용자 serving Pod와 다른 sleep 전용 Pod에서 수행했다. service-account token 없음, imagePullPolicy=Never,
기존 OPT 모델은 읽기 전용 `/models/opt-125m`. 다운로드·제품 의존성·AWS 작업은 0이다.

- `consumption_links_k3d_20261108.json`: 실제 OPT 엔진 7요청·16단계. 정상·allowed·다음 정상·top-k·penalty·grammar·진행 중 전환.
  전환의 선택 parameter bytes는 정상 A→실패 후 B→명시 복구 A→후속 A다. 원 `reload_weights` CALL/RAISE의 A/B 차이는
  `cpu_parameter_changed_during_failed_reload` 후보로 보존한다. 시험이 의도한 실패이며 운영 취약점·정책 FAIL로 승격하지 않는다.
- `multimodal_worker_k3d_20261108.json`: 랜덤 소형 Qwen2-VL 실제 vLLM worker 4요청·8단계. RGB·그레이·같은 RGB 반복·text.
  이미지 3요청 각각 4 embedding token의 gather→전처리→모델 인자가 일치했다. 반복 결과 일치는 관측했지만 encoder cache hit로 단정하지 않는다.
- `multimodal_processor_model_cpu_20261108.json`: 실제 HF processor→vision→언어 모델→logits 4사례.
  잘못된 image token 수는 실제 ValueError로 언어 모델 전 거부되고 후속 정상 logits가 복원됐다. 별도 랜덤 micro 구성요소 근거다.

원 callback 전체와 stdout/stderr, 모든 실패 시도는 저장소 밖 Codex 산출물의 `consumption-links/`·`mm-model/`에 보존한다.
초기 ID/weakref/복사 계보 누락, multiprocessing RPC 직렬화 거부, MM constructor 입력 변경에 따른 config hash 불일치,
CPU head_dim=8 미지원, architecture 미지정으로 pooling 선택된 실패도 보존했다.
원 엔진을 수정하거나 insecure serialization을 켜지 않고 in-process EngineCore+실제 mp worker 및 명시 generate 설정으로 실행했다.

원 GAP `CACHE_CALLBACK_WEAK_OBJECT_IDENTITY_UNAVAILABLE`·`DISTRIBUTED_NON_EXECUTE_RPC_UNSUPPORTED` 두 개는 그대로 남는다.
따라서 선택한 요청 사슬·MM embedding 연결과 사례 집합을 확인해도 전체 `capture_complete=False`다.
`weights_contents_verified`·`kv_kernel_reads_verified`·`GPU_verified`·`producer_authenticated`는 False를 유지한다.

## 재현과 소비

새 별도 sleep 전용 CPU Pod와 출력 경로를 준비하고 다음 도구의 필수 context/namespace/pod/container/out을 명시한다.

```text
uv run python -m tools.consumption_links_capture --help
uv run python -m tools.multimodal_worker_capture --help
uv run python -m tools.multimodal_processor_model_capture --help
```

첫 도구는 추가로 읽기 전용 model-dir을 받는다. MM worker는 private 임시 경로에 config/processor/tokenizer를 생성하며
기존 경로가 있으면 덮어쓰지 않고 거부한다. 모델 weights를 내려받거나 pretrained 모델로 표시하는 옵션은 없다.
준비 자산 경로와 출력은 재사용하지 않는다. source가 다르면 자동 pin 갱신으로 우회하지 않는다.
도구는 실패해도 원 stdout/stderr와 가능한 raw callback을 먼저 보존하고 source 종료 실패를 성공 exit로 만들지 않는다.

소비자는 `consumption_links.project`, `multimodal_worker_cpu.project`, `multimodal_processor_model.project`다.
기존 B 판정에 자동 편입하지 않는 A 지원 계약이다. 원 session/process/call 참조를 유지해 추가 증거로 소비하고
선택 범위·EXAMPLE·미확인 경계를 보존해야 한다. 원 B 정책·계약을 이 지원 범위에 맞춰 완화하지 않는다.

직접·인접·불변식 423통과/기존제외1(pytest2.16초/부모2.331초). 최초416통과/7실패의 원본과 누락 step 소비 결함 보수를 보존했다. source466파일·팩33 검증 및 offline wheel 별도 설치본의355파일/자산26개·세 실제 fixture 소비가 일치했다. 자원 정리와 전체 suite/Linux CI/GPU 미실행 경계는 PROGRESS 및 continuation에 기록한다.
