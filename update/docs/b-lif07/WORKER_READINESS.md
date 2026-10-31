# LIF07 worker 준비 관측

B 소비기는 원 PACK31 LIF07 revision 5의 질문·정상 대조·challenge 3개와 `required_readiness_items` 정책을 그대로 사용한다. 정책 미입력은 `NO_POLICY_VALUE`, 원 policy records는 빈 배열로 남는다. EXAMPLE callback metadata와 실제 target 수집을 구분하며 현재 세 판정축은 UNKNOWN이다.

```sh
PYTHONPATH=src:. python -m tools.b_lif07_readiness --store SOURCE --plan PLAN --pack packs/PACK-BASELINE --target target.json --prepared prepared/manifest.json --manifest cohort.json --output /fresh/output --entered-by operator
```

기본은 preflight이며 고정 callback의 metadata 정의·closed vocabulary 적재를 허용한다. observer 생성·snapshot·close 실행, native 수집 helper·torch·vLLM import와 대상 I/O는 수행하지 않는다. `--execute`는 기존 원Store를 export/restore한 신선한 Store에 현재 capability 공백과 관측을 저장한다. 승인된 A worker/READY-send·Unready HTTP·별도 지연 계약은 모두 `None`이라 실제 요청·Pod 변경·지연은 0이다. 다른 점검 profile이나 private `_verify(False)`로 관문을 우회하지 않는다.

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

고정 A metadata import로 callback 모듈이 적재된다는 이유로 실패했던 기존 preflight 결과는 당시 test 가정의 역사 근거로 보존한다. CheckDef의 관측 범위는 실행·대상 I/O이며 모듈 미적재를 필수로 요구하지 않는다. 공통 코드의 정확성 결함으로 집계하지 않고, fresh subprocess에서 tool import 전부터 실제 실행을 직접 계측한다. 정확한 callback source 경로와 원 code qualname을 거르는 profile을 설치해 import·preflight·closed execute 각 phase의 WorkerReadinessObserver.__init__/snapshot/close 호출을 모두 0으로 확인한다. socket bind/connect·subprocess audit, Torch/vLLM/helper 미수입과 출력 조건을 함께 유지한다. 측정 뒤 별도 양성 대조 phase에서 원본 생성자·미지원 snapshot·close를 한 번씩 호출해 같은 filter가 각각 1회를 포착하는지 확인한다. 모듈 삭제·메서드 stub 치환·새 skip 없이 실제 무실행 계약을 검증하며 과거 실패를 소급해 통과로 바꾸지 않는다.

A의 callback 오류는 `None` 또는 `CALLBACK_UNAVAILABLE` enum으로, 정리 실패는 `CALLBACK_CLOSE_UNAVAILABLE` gap으로 보존한다. 알 수 없는 값은 원문을 저장하지 않고 미수집 상태와 구분한다. native manual 본문은 선정 bootstrap SEND의 관측 상태를 첨부로 안내하고, 상위 READY·receiver·서비스·canary는 별도 미확인으로 적는다.

직접 연결 시험은 기본적으로 회차별 소유 임시 폴더에서 실행하고 정상 finalizer로 정리한다. 보존이 필요할 때만 `BYUL_LIF07_NATIVE_CONTRACT_EVIDENCE`에 새 외부 경로를 명시한다. 이미 존재하는 보존 경로는 재사용·삭제하지 않는다. 최초 1532161 판본의 273개 보존 파일과 Ref는 당시 source 근거로 유지하며, 이후 helper 수정 결과를 소급하지 않는다.

### A156 고정 공통 소스 소비

A156 고정 head `3feae2234a4516e024882fd2206c062d52e1e287`(통합 source `a5c855bd431a3ccaaad9209da243437456f9bcc2`)는 B154 전용7파일을 원 bytes로 포함한다. 원 A152 gate와 검토 common `062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89`의 허용은 유지하고, 검토한 A156 gate bytes와 common `9d8987a5a306ffce28fcb735e012c400589303f2`의 정확한 쌍만 추가한다. 각 gate의 원 full-scope ancestor/index/archive/shadow·PACK31·framework·helper858 검사를 그대로 수행한다. 현재 소스의 해시를 새 승인값으로 채택하지 않는다.

native21 원 소스 승인 `b88b4a6491a9a0c2908b962b837247c0cea80b55`와 bootstrap/상위 READY 구분은 유지한다. 소비 첨부의 `reviewed_common_commit`에는 실제 선택한 검토 common을 보존하며 `approved_native_source_commit`은 원 native21 승인값이다. 이 두 값은 서로 다른 역할이다. A156 full383 소비 검증의 manifest는 `35054eaf31cc41a50c9f8ad568510f7afe5cacac27d1ceadac05bad65cde3070`이며 원 A152 full378 근거도 보존한다. 미승인 gate/common 조합은 수집 전 닫힌다.

A156가 보고한 원 입력파일 변경 시험의 keyword 인자 누락은 해당 fixture가 `native_observation`을 원 project에 그대로 전달하도록 보수했다. 입력 변경 감지·안전 facts 보존·출력/EVD 미생성·비공개 값 비저장 assert를 유지하며 원 실패를 보존한다. 이 보수와 source 소비 추가는 actual worker/READY 수신/서비스/GPU 실증을 추가하지 않는다.

고정 A156 소비 소스에서 새 helper와 consumer를 함께 연결한 공개 `main(argv)`→원 Store/반출·복원 1건이 통과했다(1.10초). A 공개 gate와 실제 B helper는 주입 없이 실행했고 reader 전송만 `UnavailableReader` EXAMPLE로 바꿨다. 새 보고서는 `RPT-01M67VAS8V18B8QQV30DS9EQ3J@1`, SHA `de39ccdd3b00181442b1af016ec7ab923bafc837a87c2f901da159f68111cbec`다. 첨부 reviewed common은 `9d8987a…`, native 승인은 원 `b88b4a6…`이며 원 source·정확6Ref·복원 검증·3UNKNOWN과 실제 대상0을 보존한다. 이전 A152/EXAMPLE 보고서와 원 실패는 새 결과로 덮어쓰지 않는다.

### Running/Unready health/chat 실행

A158 고정 `1c3018f8cde4a4b8e674d112cee8e3626813b022` / 검토 common `f277fecd6df1e59505c0ad731032593f6fb22885`의 공개 `KubernetesWorkerReadinessHTTP.sample/report`와 `validate_snapshot`을 기존 B 명령에서 `--collect-http`로 소비한다. source gate의 원 A152/A156 허용은 유지하며 새 정확 pair와 미리 검토한 collector/binding/probe SHA만 추가한다. A full387 manifest는 `a5f030e4c9b37b5fea23dff42a98937ba53b1ac47a34c3d56dfa5fe5586ce58b`다.

기존 source Store·PLAN·원 PACK31·target·prepared·owner/recovery owner cohort manifest를 사용한다. manifest의 API model은 `guard.model-request-mapping/1` alias이며 요청은 `hello` 한 개·max_tokens1·temperature0이다. 공개 API가 비stream chat을 구성한다. 기존 POL의 `required_readiness_items`와 manual CheckDef는 유지한다. 두 명시 실행 시각은 timezone을 포함하고 현재 시각을 포함하는 최대600초 창이어야 한다. 공개 API의 여섯 결합값은 ENV SHA·target SHA·prepared session·manifest SHA와 이 두 시각으로 구성한다.

`update/`에서 기존 승인 대상·입력·실행 창과 run/복구 담당자를 확정한 후 다음 명령을 사용한다. 환경 변수에는 기존 파일 경로와 정확 PLAN ID, 새 외부 output, 담당자, 확정 실행 시각을 넣는다.

```sh
PYTHONPATH="$PWD/src:$PWD" uv run --frozen python -m tools.b_lif07_readiness \
  --store "$BYUL_LIF07_STORE" --plan "$BYUL_LIF07_PLAN" \
  --pack "$PWD/packs/PACK-BASELINE" --target "$BYUL_LIF07_TARGET" \
  --prepared "$BYUL_LIF07_PREPARED" --manifest "$BYUL_LIF07_MANIFEST" \
  --output "$BYUL_LIF07_OUTPUT" --entered-by "$BYUL_LIF07_OWNER" \
  --collect-http --http-validity-start "$BYUL_LIF07_HTTP_START" \
  --http-validity-end "$BYUL_LIF07_HTTP_END"
```

이 모드는 `--collect-native`·capture 수입·closed execute와 배타적이다. 동일한 fresh sample ID로 health→canary→health 최대3회만 수행하고 독립 API Pod/container Ready 관측·정확 namespace/Pod/image/restart/start/run 결합을 저장한다. 후기 report·부모 정리 오류·부분 응답은 앞서 검증한 HTTP 사실과 함께 보존한다. `requests/http_requests_attempted`는 검증된 packet의 시도 합계다. sample 미호출은0, 호출 후 유효 packet을 얻지 못한 계수는 null로 저장하고 manual에는 미수집으로 적는다. HTTP0회라도 검증된 API 수집이 있으면 target I/O는 true이며, 호출 뒤 관측을 얻지 못하면 null이다. coarse host/worker/GPU 정리는 미확인으로 유지하고 공통 API의 cleanup 오류를 그대로 첨부한다. 소유 probe parent의 종료·stream 회수는 기존 공통 bounded 전송이 담당하고 대상 Pod/worker를 종료하지 않는다.

HTTP envelope는 EXAMPLE이다. HTTP200·독립 PodReady와 native monotonic 시각을 worker 준비 전 접수·상위 READY·서비스 접수·GPU 완료로 결합하지 않는다. 원 normal/challenge 미시도·정책 미입력·세 축 UNKNOWN을 유지하며 controlled delay/resume는 다음 공통 입력 대기다. 수집 후 저장 전 검사에서 발견한 입력 변경은 안전한 HTTP facts를 복구 정보에 보존하고 EVD 저장 전에 거부한다. 저장 뒤 재검사에서 발견한 변경도 앞서 저장한 출력·안전한 facts와 오류로 보존한다.

직접21사례와 추가 영향3사례, P2 미수집 계수·API I/O의 영향7사례, 기존 기본/수입/native 직접 영향3사례를 확인했다. 각 고정 판본의 영향 범위이며 고유 사례 수를 합산하지 않는다. 주 세션은 실제 source gate/full387·공개 sample/report/parser에 EXAMPLE 전송만 연결한 Unready3요청→공개 main(argv)/원 Store/정확6Ref 복원1건을 확인했다(0.725초). 보고서는 `RPT-01M67W0C3NH0W6EFYZY64TM10D@1`, SHA `1125de5b14bad8f8a6f2ccf6b8a1386db3b57b07bdc90d65cade96a4e9314ef1`다. 이 제품 연결은 소비기694/a98 판본의 유효 Unready3요청 결과로 보존한다. 후속 P2의 미수집/null 보수는 별도7사례로 검증했고 기존 결과를 새 소스에서 재실행한 것으로 표시하지 않는다. 원 actor의 source 대역 CLI·이전 native21·실제 Linux kernel 보고와 판본별 증거를 따로 보존한다. 실제 Kubernetes/worker/GPU 실행은0이며 실제 대상 재개에는 승인 대상 접근·현재 ENV/POL/image/model·실행 창·run/복구 담당자가 필요하다.
