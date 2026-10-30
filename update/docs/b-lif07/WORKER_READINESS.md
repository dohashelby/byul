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


## 검증과 보존한 증거

관측기 초기29건과 startup binding 영향10건을 재사용하고, 관측 공백 수정의 직접 영향11건을 확인했다. 소비기 입력 고정·파일 수입의 직접 영향18건과 고정 변경 집중 검토를 통과했다. producer가 실제 만든 기본 미수집·최소 startup CALL/RETURN 파일을 공개 CLI로 넣어 원 manual EVD/RES/RPT와 정확 Ref 복원을 확인했다. 단위시험 임시 Store는 정상 finalizer에서 삭제돼, 검토용 산출물을 남기는 공개 명령1건만 별도 소유 경로에서 실행했다(0.514초). 기존 시험 함수·assert를 그대로 사용했고 전체 suite·실제 worker/GPU는 실행하지 않았다.

보존 RPT는 `RPT-01M671SK359WND9216SX6S2PMW@1`(SHA `bdda98d760d8ec6ef4cbbf7265b422ad272149b6c981e63cfb1fae47131b3377`)다. 저장소 밖 `byul-b-record-access-evidence-20261030/lif07-worker-readiness/import-cli-case`에 실제 argv/stdout/stderr, EXAMPLE 입력, 정제 첨부, 원본/출력/복원 Store·반출본·보고서와 SHA manifest를 보존했다. 세 축 UNKNOWN·실제 요청/Pod변경/지연0·source 자격False를 유지한다. 이 증거는 실제 Kubernetes 실행 준비 완료나 전체 LIF07 완료로 집계하지 않는다.

검토된 관측기와 기존 승인 A138 호환 adapter를 포함한 guard component는 `b15b629431777a86a151353728e612c760aff789`다. 기존 helper858·fixtureac8·전체 고정 scope를 보존하고 관련 strict11경로와 source/wheel/격리설치170파일 일치·설치본 lazy import를 확인했다. A 공유 runtime·HTTP·팩·계약은 수정하지 않았다.

## native21 관측 전용 수집

A152 `c39a36b7a6f306b910f0314b0deee10b55390dc4`의 공개 native21 준비·reader·parser를 소비하는 별도 모드다. 기존 공개 준비 명령으로 모델 읽기 범위를 결합한 `vllm-native-lifecycle-metadata/21` 자산을 준비한 뒤 다음 명령을 사용한다.

```sh
PYTHONPATH=src:. python -m tools.b_lif07_readiness --store SOURCE --plan PLAN --pack packs/PACK-BASELINE --target target.json --prepared prepared/manifest.json --manifest cohort.json --output /fresh/native --entered-by operator --collect-native
```

`--collect-native`, `--execute`, `--import-capture`는 함께 지정하지 않는다. 기본 preflight와 기존 `--execute`는 native 수집 helper를 호출하지 않는다. `--execute`의 전체 준비·Unready HTTP·controlled delay 계약은 계속 `None`이다. 관측 전용 수집은 그 계약과 독립적으로 기존 `collect_startup` helper를 호출하며, 읽기 전후 선정 Pod 세대 확인·최초 journal 수집·선정 session seal·마지막 journal 수집·후기 상태와 observer 정리를 각각 보존한다.

HTTP 요청·Pod 변경·지연 요청은 0이다. 선정 session seal은 해당 session의 control 파일 신호를 실제로 변경하므로 `selected_session_seal_attempted`와 `selected_session_sealed`를 별도로 기록한다. 초기·마지막 capture, target 읽기 시도와 host 소유 정리도 별도다. seal 성공으로 worker·모델·GPU 복구를 확정하지 않는다.

초기와 마지막 관측은 `stage=initial/final`로 나누고 recording·PID/start·원 outer seq와 callback local seq를 구분한다. 같은 journal prefix의 재관측은 보존하되 다른 stage·recording 사이의 CALL/RETURN을 합쳐 메서드 짝이나 새 세대로 만들지 않는다. 후기 수집·census·seal 실패가 앞선 정제 사실을 지우지 않는다. 원 snapshot 512행 상한을 유지하며 생략 개수와 gap을 기록한다.

A parser가 검증한 Worker source/body/entry/code 사실, 국소 객체 생성 후보와 선정 원 pipe의 bootstrap `SEND`/`CLOSE` CALL·RETURN·RAISE를 정제해 첨부한다. `READY`와 `other`, worker main의 terminal 미관측도 구분한다. callback의 `source_qualified`·실제 worker 자격은 계속 False이고, 실제 body 진입·GPU 완료·전체 rank/model 준비·상위 EngineCore READY 수신·서비스 gate는 미수집이다. bootstrap READY 전송은 KV·warmup 완료나 receiver 확인과 별개다. `tp_groups`로 필수 rank 목록을 만들지 않는다.

추가 native21 사실은 실제 helper→공개 reader/capture_session→검증된 packet 경로에서만 소비한다. `--import-capture` 파일에 추가된 native21 상자나 긍정 플래그는 이 경로의 자격을 얻지 못한다. 모델 repository/revision/inventory와 API alias는 기존 `guard.model-request-mapping`·transport `api_model` 검증을 유지한다. 원문 payload·요청·IPC handle·FD·주소·예외문은 첨부하지 않는다.

입력 reader는 path-stat끼리와 FD-stat끼리 ctime 안정성을 확인하고, path→FD→path의 device/inode/size/mtime 및 regular-file·symlink 교체 방어를 유지한다. 서로 다른 stat 계열의 ctime 의미를 같다고 가정하지 않는다. 별도 macOS 직접 회귀로 이 경계와 실제 경로 교체 거부를 확인하며 Windows 실행 결과와 구분한다. A가 승인한 runtime/code literal만 소비하고 macOS Python 3.12.13을 새 native runtime으로 승인하지 않는다.

현재 고정 A152의 공통 eager import 간선 때문에 기존 기본 preflight의 callback-module 미적재 회귀 1건은 실패 근거로 보존한다. 같은 공개 target validator를 직접 호출하는 동치 경로를 사용해도 공통 registry/report 간선이 남는다. 원 assertion과 기본 모드 관찰 계약을 유지하며 A 공통 lazy 수정 확정 후 그 한 사례를 재검증한다. 새 native 관측의 동작과 이 공통 의존성 수정을 구분한다.

A의 callback 오류는 `None` 또는 `CALLBACK_UNAVAILABLE` enum으로, 정리 실패는 `CALLBACK_CLOSE_UNAVAILABLE` gap으로 보존한다. 알 수 없는 값은 원문을 저장하지 않고 미수집 상태와 구분한다. native manual 본문은 선정 bootstrap SEND의 관측 상태를 첨부로 안내하고, 상위 READY·receiver·서비스·canary는 별도 미확인으로 적는다.

직접 연결 시험은 기본적으로 회차별 소유 임시 폴더에서 실행하고 정상 finalizer로 정리한다. 보존이 필요할 때만 `BYUL_LIF07_NATIVE_CONTRACT_EVIDENCE`에 새 외부 경로를 명시한다. 이미 존재하는 보존 경로는 재사용·삭제하지 않는다. 최초 1532161 판본의 273개 보존 파일과 Ref는 당시 source 근거로 유지하며, 이후 helper 수정 결과를 소급하지 않는다.
