# SUP-06 worker shard 공통 입력 (개발 담당, B 의존 반환 5, 2026-11-08)

B 요청(`gpu-b/consolidation-20261106/DEPENDENCY_RETURNS.md` 5번): worker/rank별 필수 shard 배정·실제 적재·READY 판정·누락 실패를
공식 결합으로 돌려줄 것. native17 단일 파일 읽기 후보를 roster 완전성으로 쓰지 않을 것.

## 반환물

| 항목 | 내용 |
|---|---|
| 계약 종류 | `gpu_common_inputs.KINDS['worker-shard-load']` (소비 점검 SUP-06). 행: `shard_required`, `shard_assigned`, `load_completed`, `load_failed`, `rank_ready` |
| 관측기 | `adapters/vllm_worker_shard_callback.py` — 엔진 콜백 센서의 선택 관측 `worker_shards`(기본 꺼짐, `prepare_assets(worker_shards=True)`) |
| 생산자 | `collectors/worker_shard_producer.py` — `guard-engine-callback-worker-shard-load` 판본 1, 소스 SHA = 관측기+생산자 bytes |
| 기존 소비자 연결 | `worker_shard_producer.shard_rows(record)` → `supply_boot_contract._worker_shard`가 받는 `{rank, required_missing, ready_accepted}` 행(+적재 완료/실패) |
| 등록 | **미등록**. TP2(rank 2개)에서 확인한 뒤 `SUPPORTED_PRODUCERS`에 더한다. 그 전 기록은 `GPU_INPUT_PRODUCER_UNSUPPORTED`로 complete가 아니다 |

## 무엇을 관측하나

vLLM 0.31.0 원 소스 해시로 고정한 세 지점:
- `DefaultModelLoader._prepare_weights` 반환: 이 프로세스가 읽을 가중치 파일(keyed 이름·크기), safetensors 여부, index의 weight_map이 가리키는 필수 shard.
  index가 없으면 배정 파일을 필수로 보고 `REQUIRED_FROM_ASSIGNED` 진단을 남긴다. 예외(예: index가 가리키는 shard 없음)는 `load_failed`.
- `DefaultModelLoader.track_weights_loading` 반환/예외: 가중치 적재 직후 누락 텐서 확인 → `load_completed`/`load_failed`.
- `WorkerProc.monitor_death_pipe` 호출: worker 생성(모델 적재 포함) 뒤 READY를 보내기 직전 → `rank_ready`(READY 후보).

모든 행에 PID·시작 ticks·TP rank를 남겨 프로세스별 바인딩(Pod UID·컨테이너·재시작 수·PID·시작 ticks·rank)을 만든다.
`load_weights`·`WorkerProc.__init__`은 `@instrument` 추적 장식자 때문에 원 코드 자격을 얻지 못해 감싸지 않는다(k3d 실측).
위반 후보(판정 아님): `missing_required_shard_reported_ready`, `failed_load_reported_ready`.

## 무엇을 관측하지 않나

원 경로·tensor·payload, 실제 GPU 메모리 적재(함수 완료만), 교체 모델의 내용 동일성, 복구 소비. rank를 읽지 못하면 0과 `WORKER_RANK_UNOBSERVED`.

## 확인

- k3d vLLM 0.31.0 CPU 이미지(arm64, EXAMPLE)의 **별도 임시 생산자 Pod**(메모리 8Gi)에서 offline LLM(mp 실행기, rank 1개) 실제 3사례.
  서빙 엔진 Pod는 건드리지 않았다. 출력 `tests/fixtures/worker_shard_k3d_20261108.json`.
  - 단일 파일(opt-125m bin): 배정 1 = 필수, 적재 완료, READY. 엔진 준비 완료.
  - 같은 가중치를 safetensors shard 2개+index로 나눈 사본: 필수 2 = 배정 2, 적재 완료, READY.
  - 그 사본에서 shard 하나 삭제: `_prepare_weights` 예외 → `load_failed`, 배정 미관측(source_ended=false), READY 없음, 종료 코드 1.
- 시험 `tests/test_worker_shard.py` 9개: 실제 3사례, 등록 시 complete, 필수 shard 누락 뒤 READY·적재 실패 뒤 READY 변형은 후보이고
  기존 소비자가 `missing_shard_reported_ready`로 판정, 비자격 진입·신원 없음 처리, 감싸는 지점과 자산 플래그.
- rank 2개 이상(TP2)은 미확인이다. EXAMPLE이며 MEASURED가 아니다.

## B 연결 방법(제안)

공급 boot SUP-06 수집이 엔진 콜백 자산을 `worker_shards=True`로 준비하고, 세션 행을 `record()`로 기록한 뒤 `shard_rows()`를 기존
`_worker_shard`에 넘긴다. 기존 판정 규칙은 바꾸지 않는다. B 소비자 수정은 B가 한다.
