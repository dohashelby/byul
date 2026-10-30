# LIF07 worker 준비 관측

B 소비기는 원 PACK31 LIF07 revision 5의 질문·정상 대조·challenge 3개와 `required_readiness_items` 정책을 그대로 사용한다. 정책 미입력은 `NO_POLICY_VALUE`, 원 policy records는 빈 배열로 남는다. EXAMPLE callback metadata와 실제 target 수집을 구분하며 현재 세 판정축은 UNKNOWN이다.

```sh
PYTHONPATH=src:. python -m tools.b_lif07_readiness --store SOURCE --plan PLAN --pack packs/PACK-BASELINE --target target.json --prepared prepared/manifest.json --manifest cohort.json --output /fresh/output --entered-by operator
```

기본은 preflight이며 native observer·torch·vLLM import와 대상 I/O를 수행하지 않는다. `--execute`는 기존 원Store를 export/restore한 신선한 Store에 현재 capability 공백과 관측을 저장한다. 승인된 A worker/READY-send·Unready HTTP·별도 지연 계약은 모두 `None`이라 실제 요청·Pod 변경·지연은 0이다. 다른 점검 profile이나 private `_verify(False)`로 관문을 우회하지 않는다.

manifest는 `byul-lif07-cohort/1`, 정확 ENV Ref/target canonical SHA/session/run_id/owner/recovery_owner와 단일 `request`를 요구한다. 요청은 기존 chat body의 API alias·user message 1개·max_tokens 1·temperature 0이다. ENV 모델 repository/revision/inventory와 `guard.model-request-mapping`의 API model은 별도로 검증한다. prompt·token·RID·secret을 증거에 저장하지 않는다.

worker의 init_device/load_model/initialize_from_config/compile_or_warm_up_model CALL/RETURN/RAISE와 rank/local_rank·설정·opaque owner/thread 및 LIFO pairing metadata를 보존한다. outer native record seq와 B observer seq는 각자 보존한다. factory RETURN·PodReady·HTTP200·단발 CPU generation_readiness로 실제 모든 rank/model 준비를 확정하지 않는다. CPU EXAMPLE callback에도 실제 worker/source 자격을 부여하지 않는다.

A에 필요한 최소 연결은 다음과 같다. 이는 B의 공용 계약 확정이 아니라 A 단일작성 요청이다.

- source/module/code가 승인된 별도 LIF07 namespace에 Worker 4point, WorkerProc 모델 load 뒤의 READY, EngineCoreProc._perform_handshake의 실제 READY-send, frontend 수신을 서로 다른 point/신호로 고정한다. WorkerProc READY는 KV·warmup 이전일 수 있다. TP1 uni에는 WorkerProc가 없을 수 있어 mp 경로를 필수화하거나 활성화하지 않는다. 실제 topology census·expected-ranks receipt·worker PID/rank·target epoch binding이 필요하다. ENV.runtime.tp_groups=1은 기존 선정 입력 필터이며 vLLM tensor_parallel_size/world_size나 ranks=[0]으로 변환하지 않는다. 관측된 rank 전원의 함수 반환에서도 전체 rank 준비를 도출하지 않는다. startup binding은 session·worker/rank·PID/process-start·target generation을 사용하며 아직 없는 request/ASGI/native RID를 만들거나 요구하지 않는다. HTTP/외부 gate 연결에는 별도 startup causal receipt가 필요하다.
- 기존 NamespaceUID/PodUID/containerID/imageID/restart/start·lab·image gates를 유지하며 Running/Unready engine의 선정 health/listener/1token canary만 허용하는 공개 profile이 필요하다. probe Pod는 Ready를 유지한다. 현재 public HTTP는 요청 전후 engine Ready가 필수다. Pending·컨테이너 생성 전 Pod까지 지원한다고 주장하지 않는다.
- controlled delay는 readonly sensor callback과 분리한 실행/복구 프로토콜로 제공한다. owner/recovery_owner, 실제 delay activated와 resumed receipt, 소유 worker와 재개·정리 결과가 필요하다. collection/HTTP/after 실패와 독립된 finally에서 재개·정리를 시도하고 각각 receipt를 반환해야 한다. 기존 KubernetesTransition은 읽기 wait만 수행한다.
- native worker/probe/host monotonic과 Kubernetes wall clock을 구분한다. A가 제공한 인과 receipt나 검증된 동일 clock 영역 없이 서로 빼거나 준비 전후 순서를 확정하지 않는다.

소비기는 정제 첨부 저장 실패 시 같은 safe facts를 원 manual note와 반환 summary에 보존한다. 이후 Store/보고서 저장까지 실패하면 safe_facts와 명시 실패·복구 상태를 예외/CLI에 남긴다. worker observer.close와 host 소유 파일·프로브 정리는 별도 항목이며 reader seal만으로 worker cache 정리를 확정하지 않는다. 현재 전체 정상·원 challenge와 실GPU/Kubernetes/controlled delay는 미수행이다.

## 기수집 snapshot 파일 소비

`--import-capture /owned/capture.json`은 라이브 `--execute`와 별도인 명시적 입력 경로다. prepared와 target/ENV/session/run_id를 정확히 묶은 기존 `byul-lif07-worker-readiness-capture/1` envelope 안에 실제 observer의 `snapshots`를 넣는다. binding 없는 단독 snapshot이나 임의 JSON 배열은 받지 않는다. import와 execute를 함께 지정하지 않는다.

```sh
PYTHONPATH=src:. python -m tools.b_lif07_readiness --store SOURCE --plan PLAN --pack packs/PACK-BASELINE --target target.json --prepared prepared/manifest.json --manifest cohort.json --output /fresh/import --entered-by operator --import-capture /owned/capture.json
```

입력은 symlink 없는 regular file·2MiB 이하·중복 키/비유한 값 없는 strict JSON object이며 첫 읽기 전후 파일 identity/크기/변경 시각을 대조한다. capture 전 원Store inventory와 manifest/target/prepared 및 준비 asset bytes를 고정하고 import/project 전후·증거 저장 후 다시 대조한다. 수집 중 원본 변경은 실패로 남기고 이미 정제한 safe facts를 보존한다. 원본을 덮어쓰거나 재수집해 오류를 지우지 않는다.

import는 신고된 관측을 기존 manual EVD로 보존한다. 파일의 source/worker/READY 긍정 플래그는 자격으로 사용하지 않는다. ENV가 EXAMPLE이면 capture도 EXAMPLE로 강제하며, DECLARED 파일도 대상 source/GPU/worker 진위 검증을 부여하지 않는다. capture의 알려지지 않은 필드·원 prompt/token/RID/secret은 projection에서 제거하며 원 JSON bytes를 artifact로 복사하지 않는다. CLI summary의 `actual_target_executed=False`, `input_mode=imported_observation_file`과 세 UNKNOWN으로 구분한다.
