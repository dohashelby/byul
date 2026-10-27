# 실행·실증 안내

## 일반 HTTP 요청기의 HTTPS 전송

`http_probe.py`는 패키지 밖 probe 컨테이너에서도 표준 라이브러리만 사용한다.
`http://`는 HTTPConnection·기본80, `https://`는 HTTPSConnection·기본443으로 연결하고
명시한 포트는 그대로 사용한다. TCP 사전 확인도 같은 포트에 연결만 하고 바이트를 보내지
않는다. 지원 밖 scheme·잘못된 host/port는 네트워크 요청 없이 오류로 남긴다.

HTTPS는 Python 기본 신뢰 저장소의 인증서·호스트명 검증을 사용한다. 인증서/호스트명 또는
TLS 연결 실패는 미응답 오류이며 평문으로 재시도하거나 인증서 검증을 끄지 않는다. 기업 CA가
필요하면 승인된 probe 이미지/환경의 신뢰 저장소를 준비해야 한다. 이번 보수는 CA 등록이나
배포 설정 변경을 하지 않는다. 요청 헤더 값은 결과에 추가하지 않는다.

공통 HTTP 실행기의 실행 조건/config 해시에 `http_probe_profile=http-probe/2`를 포함한다.
과거 팩·EVD·비교 조건은 다시 쓰지 않는다. TCP 도달은 TLS 성공이 아니며 HTTP 성공도
Gateway 재작성·최종 handler·기업 인가 또는 PLT-08 전체 준수의 증명이 아니다. stream_probe의
전송/시간 측정과 제한된 HTTP-only 소유 endpoint 프로파일은 이 보수로 확장하지 않는다.
테스트 인증서·키는 소유 localhost 서버용 EXAMPLE fixture이며 사용자 자격 증명이 아니다.

## LIF-10 native metadata 센서 준비와 소유 기록 수집

현행 profile5/events5는 아래 REQ-02의 인증/serving 문맥도 관측한다. source23개를 대조하며
과거 profile4의20개 source·가중치 파일 선택과 고정 bytes를 그대로 읽는다. profile1·2·3의
당시 의미도 유지한다. 새 준비·수집은 기존 CLI를 사용하고 자동 판정 등록은 늘리지 않는다.

Docker 대상의 `lab=true`는 기존 진입점에서 stub을 뜻하므로 개별·세션 native 수집은
ENV가 DECLARED/MEASURED로 입력돼도 EXAMPLE을 유지한다. `lab=false` 또는 과거 입력의
미지정 표식은 EXAMPLE ENV를 승격하지 않으며 나머지도 DECLARED 상한이다. Kubernetes의
`lab=true`는 소유 비운영 대상 선언으로 의미가 다르며 기존 전송·ENV 출처 상한을 따른다.

아직 CheckDef executor/judge는 미등록이며 종료 기한·회수 대상은 기업 결정이다.
이 절은 실제 생산자/파일 수집의 준비 경계다. 단일 파일 일치·HTTP 취소·finished ID·
batch row 제거를 전체 Core/worker/GPU 회수나 LIF-10 PASS로 해석하지 않는다.

```powershell
guard prepare-lifecycle-sensor --output C:\guard-data\lifecycle-assets-new
```

새 저장소 밖 폴더에 `guard_lifecycle_sensor.py`, `sitecustomize.py`, `settings.json`,
`manifest.json`을 만든다. 기존 출력은 덮어쓰지 않는다. 준비는 엔진/클라이언트/컨테이너를
만들지 않고 image 내용 검증 완료도 표시하지 않는다. 센서는 표준 라이브러리만 사용하며
준비와 수집은 같은 bytes의 센서 배포본을 사용한다.
고정 source 후보 commit과 실제 source/loader code가 맞는 module에만 hook을 넣는다.
현재 actual 대상의 모든 Core/worker 파일과 GPU 동작 검증은 미완료다.

실제 활성화는 별도로 승인·준비된 **소유 격리 대상**에서만 한다. 고정 자산을 읽기 전용
`/guard-lifecycle`에 두고, 설정의 `/guard-lifecycle/data/<session>`만 해당 engine UID 소유
0700의 새 쓰기 공간으로 제공한다. `PYTHONPATH=/guard-lifecycle`과
`GUARD_LIFECYCLE_CONFIG=/guard-lifecycle/settings.json`, native `--middleware
guard_lifecycle_sensor:Middleware`가 필요한 새 계측 기동 설정이다. 이 설정은 기존 causal
프로파일/ENV/fixture와 구분한다. 예전 증거에 소급하거나 기존 요청/스트림 측정을 바꾸지 않는다.
이 문서는 로컬 Docker/엔진 기동이나 새 image/model 취득 승인이 아니다.

센서는 실제 함수의 반환/예외/취소를 유지하며 원문 body·token·비밀·동적 예외 내용을 쓰지 않는다.
입력 Iterator를 선소비하지 않고 request/tensor/block 객체를 영구 보유하지 않는다.
generator close, 강제 GC·CUDA synchronize·fence 진전·참조 삭제를 수행하지 않는다.
metadata 읽기 구간과 host callback 시각을 기록하며 장치 clock이나 정확한 내부 종결 시각이라고
표현하지 않는다. 각 파일은 512행/256 KiB 상한이며 초과는 `BOUND_REACHED`다.

관측을 마칠 때 소유 대상에서 `python /guard-lifecycle/guard_lifecycle_sensor.py --seal`로
해당 **센서 기록만** 닫을 수 있다. 작업·worker는 종료하지 않는다. 프로세스가 종료되거나
제어/쓰기/상한 문제가 생긴 기록은 별도 이유나 미완성 행으로 구분한다. 그 뒤 실제 프로세스가
살아 있는 동안 생성된 `pid-<pid>-<start_ticks>.jsonl`을 읽는다.

`capture-lifecycle-observations`에는 정확한 `--env`, `--target`, `--prepared <manifest.json>`,
`--container`, `--image <고정 digest>`, `--path`, 저장소 밖 새 `--output`이 필요하다.
target의 `environment_ref`는 해당 ENV의 정확 Ref이고 `engine.container`와 `run_id`는 소유
대상과 같아야 한다. 파일 경로도 prepared session 및 기록 PID/start와 정확히 일치해야 한다.
기본 실행은 입력 검증만 하고 `--execute`에서만 파일·프로세스·image metadata를 읽는다.

원시 파일의 bytes SHA와 행 chain, schema·자산·세션, nofollow/fstat·소유 신원 전후 확인,
실제 image ID→RepoDigest와 PID/start/UID/namespace 대조를 구분한다. 후속 확인 실패나 마지막
부분 행 때문에 앞서 읽은 안전한 metadata를 지우지 않는다. 원문 tail은 hash/길이만 남긴다.
`process_context_verified`는 살아 있는 프로세스 문맥의 일치이며 생산자 인증이 아니다.
단일 supplied file 수집은 전체 프로세스 목록·모든 요청·native 회수 완전성을 확정하지 않는다.
출처는 EXAMPLE/DECLARED 상한이고 전체 `security`는 UNKNOWN이다.

세션의 여러 기록은 `capture-lifecycle-session --env <ENV> --target <target.json>
--prepared <manifest.json> --container <소유 이름> --image <고정 digest> --output <새 JSON>`으로
수집한다. 기본은 입력 검증이며 `--execute`에서만 같은 소유 컨테이너의 세션 디렉터리와
프로세스를 읽는다. argv/environ·파일 본문은 목록 수집에 쓰지 않고 native 종료 요청도 보내지 않는다.
기존 개별 기록 수집의 nofollow/fstat·행 chain·image·프로세스 문맥 대조를 그대로 재사용한다.

`live_census_stable`은 전후의 제한된 목록과 소유 세대가 같다는 뜻이다. 두 시점 사이에 생겼다
사라진 프로세스나 전체 시험 구간을 증명하지 않는다. `recorded_files_covered`도 그 목록에
있는 파일의 안전한 기록 prefix와 파일 신원이 수집됐다는 뜻이며 native 종결·회수가 아니다.
같은 Python 실행 파일을 쓰지만 기록이 없는 프로세스는 `python_processes_without_recording`에
남긴다. 보조 프로세스도 포함할 수 있으므로 worker 누락으로 단정하지 않는다.

요청 index는 `recordings[i].capture.records[seq]`의 정확한 사건 참조와 지문만 연결한다.
native ID 할당의 RETURN에 있는 외부/내부 지문 쌍을 보존하고, 외부 ID 재사용은 모호한
다중 연결로 남긴다. 소유 세대와 boot/PID·time namespace/offset 지문이 다른 자료는 분리한다.
이 index는 인증된 귀속·전역 시각 순서·`abort_to_stop_seconds`나 전체 LIF-10 판정이 아니다.

기술 상한은 파일32·현재 프로세스128·index 지문256·사건 참조4096이다. 초과·누락·개별 실패는
표식으로 남기며 이미 수집한 다른 기록은 보존한다. 파일 저장은 새 JSON에만 하고, 늦은 저장
실패는 종료 코드1과 `artifact_saved:false` 및 검증된 metadata 전체를 표준 출력으로 반환한다.
부분 파일은 성공 참조로 쓰지 않는다. 입력/준비 검증 실패는 종료 코드2다. 결과의 출처 상한·
생산자 인증 false·전체 범위 false·security UNKNOWN은 단일 파일 수집과 같다.

### ASGI 헤더 문맥과 native ID 할당

헤더 문맥은 private profile `vllm-native-lifecycle-metadata/2`와 event schema
`guard-native-lifecycle-events/2`에서 추가했다. completion의 외부 ID는 헤더 원문과 다를 수 있으므로
접두사/순번을 추측해 hash를 연결하지 않는다. `/v1/completions` middleware는 기존 헤더
지문만 ContextVar에 두고, 같은 PID 문맥의 동기 `InputProcessor.assign_request_id`에서만
`asgi_request_sha256`를 관측한다. 원래 함수가 반환한 사건의 외부/내부 ID 지문과 함께
index에 남긴다. 별도 receive/send·native 호출·스트림 시간 측정은 추가하지 않는다.

헤더 누락/빈 값/상한 초과와 프로세스가 달라진 상속 문맥은 연결하지 않는다. 장수명
output handler의 문맥을 다른 요청 ID에 적용하지 않으며 예외·취소·중첩 호출 뒤 문맥을
복원한다. 같은 헤더가 여러 내부 ID와 연결되면 `ambiguous_asgi_request_sha256`로 남긴다.
이는 헤더 문맥에 대한 생산자 주장이고 고유 HTTP 요청 신원·인증·인과성의 독립 증명은
아니다. 전체 실행·종결/회수의 상한은 유지한다.

profile1은 고정된 과거 sensor bytes의 LF/CRLF 두 spelling과 manifest/개별 자산 SHA를
모두 대조해 읽기만 지원한다. 과거 기록에 새 연결 필드를 소급하지 않으며 새 필드가
섞인 profile1은 거부한다. 알 수 없는 sensor bytes와 profile/source 교차 조합도 거부한다.
수집기는 과거 센서 파일을 import/실행하지 않는다. 현행 새 자산 준비는 profile3만 생성한다.

### REQ-05 native 적재 객체와 모델 호출 관측

profile5/events5는 기존 준비·소유 개별/세션 수집을 재사용한다. 추가 실행기나 판정을
등록하지 않는다. 고정 `BaseModelLoader.load_model`의 반환 객체와
`DefaultModelLoader.load_weights`의 입력 객체, CPU/GPU runner의 적재 후 `self.model`,
native `get_model` 반환, `GPUModelRunner._model_forward`의 호출 객체를 구분한다.
기존 source/loader code 대조를 거친 지점만 계측하고 원래 model/입력/반환 객체를 유지한다.

객체 참조는 세션·물리 프로세스 문맥과 증가하는 세대 번호의 지문이다. raw `id()`·모델명·
revision·경로·tensor·token·예외 원문은 저장하지 않는다. 약한 참조를 쓰고 최대64개의
살아 있는 객체만 추적한다. 상한/약한 참조 미지원은 null이며 기존 참조를 지우거나
다른 객체에 재사용하지 않는다. fork 뒤 추적을 초기화한다.

`configured_model_sha256`/`configured_revision_sha256`/`load_format_sha256`는 **설정값**의
지문이다. 객체 참조는 가중치 지문이 아니며 같은 객체의 in-place reload를 새 가중치
revision으로 구분하지 못한다. wrapper의 참조와 native getter의 반환은 각각 기록하며
서로 다른 객체를 자동으로 동일시하지 않는다. dummy/LoRA/speculative 설정의 존재도
관측일 뿐 해당 선택 기능의 실제 소비 증명이 아니다.

`_model_forward`의 요청 지문은 같은 호출 문맥에서 관측한 scheduler 선택 ID다.
tensor row 귀속·장치 실행 완료·생성 출력 귀속은 확정하지 않는다. 해당 문맥이 없는
warmup 호출은 요청 사건으로 만들지 않는다. 세션 index는 최대256개의 `model_observations`에
정확한 파일/행 참조를 남긴다. 초과 시 미완전 표식과 앞선 관측을 보존한다.
`final_model_verified`·`actual_consumption_verified`·`weights_revision_verified`는 false다.

profile1·2는 각각 고정 과거 source bytes, 당시17개 source와 지점 집합을 확인해 읽는다.
과거 기록에 새 모델 필드·module·지점을 소급하지 않는다. profile3도 당시19개 source와
고정 bytes로 읽으며 profile4는20개 source를 대조한다. Gateway 인가 기록과 독립된 최종 가중치 신원·실제 요청별 소비·alias/퇴역/LoRA·
신뢰 생산자·기업 정책 대조는 남아 있다. 실제 엔진·Kubernetes/GPU 실증으로 집계하지 않는다.

### REQ-05 native 파일 선택과 독립 파일 해시

profile4는 고정 `DefaultModelLoader._prepare_weights`의 준비 목록과 `_get_weights_iterator`,
`weight_utils`의7개 iterator factory 인수를 구분한다. 최종 factory 인수는 필터 후 목록이며,
최대64개 경로를 POSIX CWD 기준 정규화한 지문으로 남긴다. 파일 목록이 list가 아니거나
상한·CWD 변경이 있으면 미완전으로 남긴다. 부모 `load_weights` 호출 지문과 모델 객체
지문이 같은 PID에서 연결된 경우만 파일 대조 대상으로 삼는다. 원래 generator를 그대로
반환하며 센서는 이를 추가 소비하거나 tensor·내용을 읽지 않는다.

`capture-native-weight-files` 입력은 `--env --target --prepared --context --model-manifest
--native-session --output`이다. `--execute` 없이 입력과 출력 예약만 검사하며 접속·파일 생성을
하지 않는다. context는 아래의 비공개 입력 형식이며 정확 ENV Ref·session·입력 bytes SHA를 채운다.

```json
{"schema":"guard-native-weight-files-context/1","environment_ref":{"id":"ENV-...","revision":1,"sha256":"정확 ENV SHA"},"session":"준비된32자hex","transport":"docker","model_root":"/models/소유-root","native_session_sha256":"세션파일 bytes SHA","model_manifest_sha256":"manifest파일 bytes SHA"}
```

기존 `guard.model-manifest/1`의 scope=all 파일 목록과 `ENV.model.inventory_sha256`가 정확히
일치해야 한다. Docker target의 `lab`은 명시 bool이며 기존 진입점의 stub=true·실제 엔진=false를
그대로 받는다. 어느 값이든 소유 run/container·정확 ENV Ref·pinned 이미지 확인이 필요하며
실제 엔진 실행을 승인하거나 자료 등급을 높이지 않는다. Kubernetes는 기존 lab=true·고정
context/namespace/Pod UID/container 세대 target을 사용한다. `.bin/.pt/.safetensors/.gguf` 중
native 반환 사건에 관측된 최대32개 파일만 별도 stdlib helper가 SHA를 계산한다. 기본 파일당
1GiB(상한도1GiB), 전체180초(최대300초), 각 소유 helper는 최대5초다. 큰 shard·원격/지원 밖
로더는 미수집이며 상한을 모델 허용 정책으로 쓰지 않는다. 모델·Torch를 import하지 않는다.

root와 resolved 경로의 디렉터리 구성요소는 nofollow로 열고, root 내부 snapshot→blob symlink만
허용한다. root 밖 symlink·FIFO·특수 파일·상한 초과는 거부한다. 열린 FD 전후 신원과 logical
경로 재확인을 별도로 보존한다. 이는 시점별 파일 대조이며 불변 mount·native 실제 파일 읽기·
tensor 적재·최종 소비 증명이 아니다. 원문 경로·내용·예외는 출력하지 않는다.

파일 불일치는 유효한 관측으로 보존하고 새 보안 FAIL/PASS를 만들지 않는다. 뒤 파일 실패는
앞선 성공 해시를 유지한다. 늦은 소유/경로 재확인 실패도 해시를 유지하고 binding=false로
남긴다. 출력 저장이 실패하면 종료1과 함께 전체 안전한 capture를 stdout에 남기며 기존
출력 파일을 덮어쓰지 않는다. 자료 등급은 DECLARED 상한이고 대역·EXAMPLE 입력과 알려진
Docker stub(`lab=true`)은 EXAMPLE이다. Kubernetes의 `lab=true` 선언을 Docker stub으로
취급하지 않으며 실제 Kube 수집도 DECLARED 상한이다. native 소유자가 다른 전송의 신원과
맞지 않으면 해시는 보존하고 연결 미확인 상태를 유지한다.
최종 모델·weights revision·실제 소비·생산자 인증·전체 실행 확인은 모두 false/UNKNOWN이다.

## REQ-02 인증 입력·serving 호출 부분 관측

profile5는 고정 `AuthenticationMiddleware.__call__`/`verify_token`, 모델 목록 router의
동적 `models(request)` helper와 `OpenAIServingModels.show_available_models`를 관측한다.
ASGI HTTP 호출에 유일한 `x-request-id` 헤더 하나(1~512bytes, 전체 header64개 이하)가 있을
때만 별도 invocation 지문을 연다. 없는/중복/상한 초과 헤더는 연결하지 않으며 계획한
요청의 미수집으로 다룬다. 고유 호출 지문과 동일 헤더 재사용의 모호함을 구분한다.

진입 경로/root_path/method와 인증·router helper에서 관측한 scope의 지문을 구분한다.
원문 경로·헤더 값·자격 증명·요청/응답 본문·ModelList 내용은 기록하지 않는다. `verify_token`
반환 bool은 관측한 인증 함수 결과다. 동기 `__call__`은 awaitable을 반환하므로 그 RETURN은
다운스트림 완료가 아니다. 원래 coroutine을 그대로 반환하고 추가 await·receive·send는
하지 않는다. serving 반환은 원래 async 메서드의 await 반환이며 HTTP 전송/인가 완료가 아니다.

같은 PID의 살아 있는 ASGI 문맥만 연결하며 호출이 끝나면 상속된 늦은 자식 문맥도 닫는다.
살아 있을 때 시작했지만 종료 뒤 반환한 작업은 앞선 CALL을 유지하고 반환 연결은 미수집으로
남긴다. 비HTTP 중첩 호출도 활성 HTTP 문맥에 결합하지 않는다.
기존 completions의 callback 관측 범위·원래 ID 할당 연결·스트림 측정은 유지한다.
새 인덱스 `route_authentication_index`는 소유/프로세스 문맥을 지문으로 구분하고 최대256개
관측과 정확한 파일/행 참조를 보존한다. 초과·충돌 시 앞선 자료를 유지하고 미완전으로 남긴다.

이것은 신뢰된 호출자·실제 router의 endpoint 선택·Gateway 유효 재작성 설정·최종 인가 판정이
아니다. router decorator가 잡은 함수는 모듈 변수 교체만으로 계측되지 않아 동적 조회 helper와
serving 메서드를 관측한다. 다른 handler/경로·동일 헤더 재사용·누락은 전체로 일반화하지 않는다.
기업 허용 관계와 외부 요청/인증 입력/최종 handler의 독립 대응은 남아 있으며 모든 검증 flag는
false·보안 UNKNOWN이다. EXAMPLE 조사와 실제 엔진 실증은 별도로 집계한다.

## LIF-10 Kubernetes 파일·프로세스 읽기


`capture-kubernetes-lifecycle-session --env <ENV> --target <target.json> --prepared <manifest.json>
--output <새 JSON>`은 기본 입력 검증만 한다. `--execute`에서만 기존 kubectl·kubeconfig의
명시 context로 선택한 Namespace/Pod를 get하고, 선택 container에서 고정 metadata helper를
exec한다. Pod·namespace·RBAC·image·mount·설정을 생성/변경하지 않는다. 준비된 센서 자산과
UID 소유 0700 세션 디렉터리는 별도로 승인된 기동 단계에서 제공해야 한다.

target은 아래 **형식 예시**이며 모든 Ref·UID·세대·image 값은 실제 선택값으로 채운다.
실제 클러스터 수집 완료를 뜻하지 않는다. `kubernetes.engine`의 필드는 기존 Kubernetes
HTTP target의 동일 역할 형식을 재사용하며 probe Pod나 HTTP URL은 필요하지 않다.

```json
{
  "schema": "guard-kubernetes-lifecycle-target/1",
  "lab": true,
  "run_id": "guard-owned-run",
  "environment_ref": {"id": "ENV-ID", "revision": 1, "sha256": "정확한 정규 문서 해시"},
  "kubernetes": {
    "context": "명시-context",
    "engine": {
      "namespace": "owned-namespace", "namespace_uid": "실제 UUID",
      "pod": "owned-engine", "pod_uid": "실제 UUID", "container": "engine",
      "container_id": "containerd://실제-64hex", "image": "저장소@sha256:실제-64hex",
      "image_id": "API가 보고한 정확한 imageID", "restart_count": 0,
      "node": "실제-node", "pod_ip": "실제-IPv4", "started_at": "실제-aware-시각"
    }
  }
}
```

get/exec 전후 Namespace UID·Pod UID·guard.run·node·spec image·보고 imageID·container ID·
restart·startedAt을 대조한다. HTTP 경로의 Ready 요구는 유지한다. 파일 읽기는 같은 세대의
Running container에서 Ready가 내려가도 장애 metadata를 읽을 수 있다. helper는
`python3 -I -S -B`로 site hook/bytecode 쓰기를 건너뛰고 자기 프로세스만 끝내는 watchdog을
사용한다. native 작업이나 다른 PID를 종료하지 않는다. 개별 호출은 최대5초, 전체 창 기본
180초/상한300초이며 기업 종료 기한이 아니다. `/proc` 가시 영역은 Pod의 PID namespace 설정에
따라 달라지므로 그 목록을 모든 worker의 목록으로 단정하지 않는다.

Pod status의 `imageID`는 `reported_image_id`로 보존한다. spec/status의 선택값 일치는 Docker
RepoDigests 대조나 node CRI image 검증이 아니다. `image_digest_verified`,
`node_runtime_image_verified`, `manifest_ancestry_verified`는 이 경로에서 false다.
세션 파서·행 chain·파일/fstat·프로세스 대조·지문 index·개별 실패/늦은 저장 실패 보존은
기존 경로를 재사용한다. 생산자 인증/전체 실행 구간·회수는 미검증이며 출처는 EXAMPLE/DECLARED,
security는 UNKNOWN이다. 주입 runner는 ENV 표시와 무관하게 EXAMPLE로 제한한다.

## PLT-08 명시 소유 API 시험 구간의 평문 성공

`api_transport:real`은 기존 `guard select/run/judge/report`에서 사용한다.
[설정 예제](../examples/api-transport.toml)와 기존 소유 target, 명시 POL을 제공한다.
`guard policy template`의 PLT-08 필수9개 값은 비어 있으며 관측에서 자동 채우지 않는다.
`api_hop_scope`는 `guard_probe_to_owned_direct_api`만 지원하고 CID·network/endpoint ID·IP·port,
`plaintext_forbidden` 및 aware ISO 유효 시작/끝을 실제 수집과 대조한다. 불일치는 HTTP 전 거부한다.

같은 제품 실행에서 기존 LIF-04의 bounded 정상/slow/병행/후속 요청을 수집한다.
HTTP-only 수집기, 고정 소유 bridge 종단의 전후 안정성, source guard·시간창과 완전 정상
응답이 결합된 명시 금지 평문 성공만 보안 FAIL이다. URL scheme이나 임의 수집 JSON을
import해 판정하는 경로는 없다. 부가 자원·후속 스트림 수집 누락과 cleanup 실패는 이미
확인한 초기 API 위반을 지우지 않는다. 필요한 종단·시간창 결합 자체가 없으면 UNKNOWN이다.

출처는 기존 수집의 DECLARED 상한·EXAMPLE을 유지한다. 평문 허용·거부·timeout·미수집을
전체 PASS로 해석하지 않는다. 시험 caller의 직접 구간이며 배포 호출 앱/Gateway 경로,
암호학적 peer 확인·TLS 종료/재암호화·인증서·IPC/TP·무결성 변조와 전체 PASS는 미검증이다.
실제 엔진·daemon 기동·이미지 pull·모델 다운로드를 이 실행기가 수행하지 않는다.

## 첨부 읽기·저장 실패와 부분 증거

실행기가 유효한 draft를 반환한 뒤 첨부 파일 읽기·Store 저장이 실패하면, EVD는
`ABORTED`와 고정 `ARTIFACT_PERSISTENCE_INCOMPLETE` 진단을 남긴다. 이미 수집한 정식 관측,
실제 정상/도전 사실, 성공한 첨부 참조를 빈 값으로 바꾸지 않는다. 원문 예외·실패 경로를
진단에 넣지 않는다. 실패한 첨부를 성공으로 표시하거나 기존 파일·객체를 덮어쓰지 않는다.

공통 HTTP 수집기가 draft를 반환하기 전 샘플·프로브 로그의 형식화/쓰기나 첨부 확인에서
실패한 경우에는 `ARTIFACT_CAPTURE_INCOMPLETE`로 구별한다. 수집기가 유효하게 반환한
관측·정상/도전 사실은 보존하고, 실패한 부분 파일은 첨부 참조에서 제외하며 다른 성공 첨부는
계속 저장한다. `ABORTED`·최대 DECLARED(기존 EXAMPLE 유지) 관문은 같다. 뒤의 Store 저장도
실패하면 두 고정 진단을 함께 남긴다. 일반 수집·설정·비교 조건·모델 검증 오류를 이 경계에서
복구하거나 유효한 관측으로 추정하지 않는다. work/의 부분 파일은 검증 bundle에 포함되지 않는다.
샘플 쓰기를 시도했으나 참조 생성 전에 파일이 사라진 경우도 실패로 남긴다. 쓰기를 시도하지 않은
선택적 첨부의 기존 미생성 처리는 유지한다.

OBS-09의 별도 참조 HTML도 같은 캡처 경계에서 UTF-8 bytes로 기록한다. 분석·렌더링·메모리
상 표시 대응을 마친 뒤 인코딩/디렉터리/쓰기 실패나 파일 소실이 발생하면 실패한 HTML을
제외하고 관측·다른 성공 첨부를 보존한다. 메모리상 표시 검증을 저장 성공으로 해석하지 않으며
ABORTED는 기존 PASS 관문에서 보류된다. 정상 HTML bytes·해시·사건 대응은 유지한다.

기존 PASS 관문이 실행 미완료를 보류하고, 기존 판정기는 독립적으로 확인한 위반을 유지한다.
특정 판정에 실제 첨부가 필요하면 그 근거가 없는 범위는 계속 UNKNOWN이다. 출처는 최대
DECLARED이며 EXAMPLE은 그대로다. 부분 사실 보존은 전체 실행·증거 수집 완료의 증명이 아니다.
이 출처는 EVD의 `data_origin`이며, ABORTED 결과의 `verification_level`은 기존 규칙대로 null이다.
ABORTED 증거는 기존 이월 관문에서 거부하며 과거 저장 EVD/RES·팩에는 새 의미를 소급하지 않는다.
과거에 이미 빈 관측으로 저장된 EVD는 이 수정이나 재판정만으로 잃은 사실을 복원할 수 없다.

일반 실행기 생성·수집·무효 draft 오류의 기존 빈 ABORTED 경계는 `EXECUTOR_FAILURE`,
판정 플러그인의 기존 예외 경계는 `JUDGE_PLUGIN_FAILURE`로 진단한다. 이 두 경계에서는
예외의 클래스 이름·문자열·입력·경로를 저장하거나 문자열화하지 않는다. 뒤쪽 판정 출력의
오류가 확인되면 앞에서 적용한 PASS도 INV-15에 따라 UNKNOWN으로 보류하고 이미 확보한
FAIL은 유지한다. 관측 복구·새 기업 기준을 추가하지 않으며 KeyboardInterrupt/SystemExit는
그대로 전파한다. 과거 EVD/RES는
고쳐 쓰지 않는다. 실행기 자체의 모든 자유 진단·기존 로그 재생 경계 전체를 바꾼 것은 아니다.

## PLT-07 명시 금지 TCP 연결 한 분기

`boundary_egress`는 기존 `guard select/run/judge/report` 경로에 연결한다.
첫 분기는 이미 준비된 단일 소유 대상의 audit 구간이다. container ID·UID·netns·정책 구간이
바뀌면 새 명시 정책이 필요하며, 두 이미지/재기동에 같은 식별자를 자동 이월하지 않는다.
`examples/boundary-egress.toml`은 실행 설정 양식이며 회사 정책값을 제공하지 않는다.
새 정책 양식은 현재 팩의 `guard policy template`로 만들고 PLT-07의 11개 필수 값을 사람이
명시해야 한다. subject의 정확한 container ID·UID·netns와 별도 owned receiver ID·IPv4·port,
금지 protocol/작업, aware ISO 적용 구간을 관측값이나 다른 점검 정책으로 보충하지 않는다.

첫 프로파일 `explicit-forbidden-egress-v1`은 `subject_scope=all_processes_in_container_uid_netns`,
`forbidden_protocol=tcp`, `forbidden_operation=tcp_connect`만 지원한다. 이는 해당 UID+netns의
모든 프로세스를 포함하는 정책을 입력한 경우다. worker·SDK·특정 실행 파일만의 정책을
이 프로파일로 바꾸지 않는다. 목록 밖 자동 금지, 허용 목록·유효 기간·유예의 기본값은 없다.

송신 target과 receiver target은 이번 run의 별도 pinned Linux 컨테이너여야 한다.
host/다른 컨테이너의 PID·network 공유나 proc/cgroup을 가리는 mount를 거부하며 명시 private
cgroup namespace 프로파일을 요구한다. 소유 CID/image ID→RepoDigest/start와 bridge endpoint를
전후 대조한다. 수신처는 패키지의 standalone `egress_receiver.py` source를 정확한
`python3 -u -c <source> <port>` 명령으로 실행하고 새 `/guard-observations/egress.jsonl`을 만든다.
실행기가 이 명령·실제 PID/start/UID/netns/listen socket·파일 수집 경계를 대조한다.
수신처 준비는 승인된 격리 환경에서 수행하며 제품 실행기는 컨테이너·이미지·엔진을 만들지 않는다.

송신 helper는 명시 숫자 UID로 실행해 실제 UID·netns를 검사한 뒤 소유 수신처 한 곳에
32바이트의 무해 일회성 상관 표식을 보낸다. 표식 원문은 증거에 남기지 않는다.
부모의 전체 예산과 검증된 Linux time namespace의 보수적 기한을 연결 전·송신 전에 확인한다.
송신 쪽 실제 socket과 수신 쪽 reverse tuple·상관 해시·실제 수신 기록·정책 적용 구간이
같은 사건으로 확인된 경우만 `forbidden_egress_successes`에 남긴다. 서로 다른 netns의
resource tuple hash를 같다고 비교하지 않는다. UTC 시계가 맞지 않으면 허용 오차를 만들지 않는다.
ACK는 부가 수집이다. TCP 연결·실제 표식 수신의 독립 근거가 완결됐다면 ACK 실패나
연결 종료 뒤 peer 메타데이터 조회 실패로 과거 관측을 지우지 않는다. 정식 증거는 EVD의
구조화 관측이며 중복 summary를 필수 Store artifact로 등록하지 않는다.

이 독립 금지 연결 성공은 보안 FAIL이다. 거부·timeout·미수집은 보안 UNKNOWN이며 전체 차단
성공으로 보지 않는다. 다른 후속 수집 오류로 이미 검증한 위반을 지우지 않는다. 정상 업무·
전체 송신 제한·worker/SDK/usage·DNS/redirect/TLS·정책 단절·만료 집행·우회 원인은 미검증이다.
보안 PASS와 기능·성능 PASS 규칙은 없고 전체 `egress_scope_complete=false`를 유지한다.
EXAMPLE 대역·소유 시험 socket·격리 Docker stub은 실제 vLLM 실증으로 집계하지 않는다.

## LIF-04 느린 수신과 독립 자원 부분 관측

`guard capture-stream-pressure`는 기존 SSE 수집에 명시적인 `slow-reader-v1`을 연결한다.
기존 `read_delay_ms`는 최초 한 번 지연한다. 새 프로파일은 제한된 chunk를 읽을 때마다
기다리고 정한 nonempty read 횟수 뒤 클라이언트 연결을 닫는다. 종료는 worker 취소 증명이 아니다.

```text
guard capture-stream-pressure --env "<SEALED-ENV.json>" --target "<OWNED-LAB-TARGET.json>" --settings examples/stream-pressure-settings.json --output "<NEW-EXTERNAL-ATTACHMENT.json>"
```

기본 실행은 입력 검사만 하고 Docker·요청·출력을 만들지 않는다. `--execute`는 소유 중인
격리 대상에 전후 정상 요청과 느린 SSE/병행 정상 요청을 보낸다. 엔진이나 daemon을 기동하지 않는다.
target은 명시 boolean `lab`(stub이면 true, 기존 managed 실제 엔진이면 false), 정확한 run label·
pinned 엔진 이미지·명시 port·trusted network가 필요하다. HTTP host는 컨테이너 DNS 이름 또는
소유 endpoint의 IP다. 실행 시 network의 소유 라벨·ID와 engine CID/endpoint/attachment 주소를
대조하고 실제 요청은 검증한 IP로 고정한다. `probe_image`도 로컬 pinned Linux 이미지여야 한다.
이미지를 조회한 뒤 실제 probe 실행은 `--pull=never`로 제한한다. 엔진과 다른 새 probe 실행 ID로
이번 probe만 정리하며 기존 컨테이너·network는 정리하지 않는다.
부모의 남은 전체 예산을 Docker 시작부터 적용하고, 시작 전 자원 표본의 Linux 시간 namespace와
보수적인 절대 기한을 probe의 연결 전·HTTP 전에도 확인한다. 같은 시계를 확인하지 못하거나
기한이 만료되면 HTTP를 시작하지 않는다. 수집 종료 후 이번 probe 정리는 별도 최대 30초다.

자원 관측은 host/다른 컨테이너의 PID·network 공유를 거부하고 명시 private cgroup namespace와
source를 가리는 mount 부재를 요구한다.
읽기 전용 helper가 PID/start·namespace·RSS·cgroup v2 memory.current·port별 TCP queue/inode/owner를
상한 안에서 수집하고 전후 컨테이너 신원을 대조한다. probe와 표본의 Linux 시간 namespace가
같고 표본 측정 구간이 실제 요청/close 구간에 맞을 때만 전·중·후 구간 근거로 센다.
시간 namespace 미지원·요청 전 표본만 존재·누락·세대 변경은 미확인·고정 진단으로 남긴다.
시간 namespace가 없고 플랫폼 monotonic 시계의 해상도가 낮으면 새 읽기 프로파일의 상대 시간만
고해상도 `perf_counter`로 기록한다. namespace 연결은 계속 미확인이고 기존 deadline 시계는 유지한다.
RSS·cgroup은 총량이고 queue는 표본 시점의 socket 관측이다. 연결별 buffer 비용·worker 취소·회수
완료를 이 값이나 EOF/HTTP/DONE으로 확정하지 않는다. 원문 응답·토큰·headers·예외 원문은 첨부하지 않는다.
ESTABLISHED의 queue만 TCP sequence 바이트로 표시하고 LISTEN의 수신 queue는 대기 연결 수로
구분한다. 나머지 상태의 queue 바이트는 null·미완전 진단으로 남긴다. 이 값은 socket 메모리 점유량이 아니다
([Linux IPv4 구현](https://github.com/torvalds/linux/blob/master/net/ipv4/tcp_ipv4.c),
[IPv6 구현](https://github.com/torvalds/linux/blob/master/net/ipv6/tcp_ipv6.c)).
프로세스 RSS에는 공유 페이지가 각 프로세스에 집계될 수 있고 cgroup 총량에는 관측 helper도
포함된다. 이 관측 비용을 임의로 빼거나 총량 차이를 연결별 비용으로 바꾸지 않는다.

예시 settings는 기술적 시험 상한이며 기업 메모리·회수 예산이 아니다. 출력은 항상 세 축 UNKNOWN,
`official=false`·`product_connected=false`다. LIF-04 CheckDef의 executor/judge와 팩27은 유지하며
새 자동 판정·전체 분기 실증으로 집계하지 않는다. 실제 vLLM 실증은 별도 보류 상태다.
직접 주입한 probe/sampler는 오프라인 시험용 경로이며 출처를 EXAMPLE로 제한한다.

## REQ-02 소유 경로 관측 파일 수집

`guard capture-route-observations --help`로 설치 제품에서도 기존 파일 수집을 사용할 수 있다.
봉인 ENV와 명시한 target/run ID·소유 컨테이너·pinned 이미지를 준비한다.

```text
guard capture-route-observations --env "<SEALED-ENV.json>" --target "<TARGET.json>" --container "<OWNED-PRODUCER>" --image "<REPOSITORY@sha256:DIGEST>" --path "/guard-observations/route.json" --output "<NEW-EXTERNAL-ATTACHMENT.json>"
```

기본 실행은 입력 검사만 하며 Docker 생성·수집·출력을 하지 않는다. `--execute`는
기존 소유 컨테이너의 파일을 읽기만 하고 엔진 기동·HTTP 요청·판정을 하지 않는다.
출력은 기존 부모 아래 저장소 밖의 새 파일이어야 하며 링크·junction과 덮어쓰기를 거부한다.
독립 생산자·인가·요청 ledger가 검증되지 않아 항상 UNKNOWN·`product_connected=false`다.
EXAMPLE ENV는 EXAMPLE로 보존하며 그 외 입력도 생산자 인증 전에는 DECLARED 첨부다.
기존 `tools/capture_route_observations.py`도 같은 수집 함수를 사용한다.

## REQ-05 Gateway 기록 수집 — 최종 엔진 모델 판정은 미연결

`guard capture-model-authorization --help`로 제품 명령을 확인한다. 기존 collect/import의
봉인 ENV/POL JSON과 명시 context·target을 사용하며, 기존 봉인 파일을 직접 고쳐 쓰지 않는다.
POL의 새 판본에 사람이 `values.REQ-05.principal_model_grants`를 입력해야 한다.
이 전용 수집 입력은 CheckDef의 자동 정책 양식 등록을 뜻하지 않는다.

```powershell
guard capture-model-authorization --pack packs/PACK-BASELINE --env "<SEALED-ENV.json>" --policy "<SEALED-POL.json>" --context "<CONTEXT.json>" --target "<TARGET.json>" --gateway-container "<OWNED-GATEWAY>" --gateway-image "<REPOSITORY@sha256:DIGEST>" --gateway-path "/logs/gateway_records.jsonl" --output "<NEW-EXTERNAL-ATTACHMENT.json>"
```

기본 실행은 입력만 검사하고 출력·Docker를 만들지 않는다. 별도 승인된 소유 자료를
실제로 읽을 때만 `--execute`를 붙인다. 이 옵션도 기동·요청 발생·판정은 하지 않는다.
최종 엔진 모델 생산자가 없어 수집 성공 후에도 UNKNOWN·비공식이며 실행/인가 검증
완료가 아니다. 입력 필드·상한·남은 생산자와 PLT-07 시험 조건은
[관측 보강](AUTOMATION_HARDENING.md)의 REQ-05/PLT-07 절을 따른다.

> 2026-10-26 현행 작업 후보: 팩27/DRAFT, 보안 규칙 연결13개·SUP-03 관측 전용1개다.
> PLT-02 Unix IPC 부분 연결·선택 native 파일 수집의 범위는
> [관측 보강](AUTOMATION_HARDENING.md)을 따른다. 기존 점검 실제 재실증은 변경 영향이
> 있을 때 해당 범위만 수행한다. 아래 팩21·22·GUA PR #28~#31 기록은 과거 이력이다.
> BYUL PR #6~#9의 병합 목적지는 #5의 head 브랜치였으며 main 반영은 별도 통합 PR이 필요하다.

## v1 통합 후보와 검증 이력

**2026-10-25 현행**: PR #30·#31이 main(`fb0c103`)에 병합됐다. 실측은 실행 조건별로 구분한다. (1) PR #30 기본 실행(`length_cause_evidence` 없음): 후보 REQ-09 보안 UNKNOWN으로 수락 실패. (2) PR #31 후속 실행(`length_cause_evidence = true`, 검토 프로파일과 일치하는 0.32.0 CPU 이미지): 다섯 점검 양쪽 PASS·NO_CHANGE로 수락 성공. (3) PLT-04 후보 단독 시험: 비어 있지 않은 EXAMPLE 후보로 absent·denied PASS / readable FAIL / symlink·directory UNKNOWN. (4) 두 이미지 update의 PLT-04: 후보 `[]`인 부분 범위. 상세는 [후속 실측](UPDATE_VALIDATION.md#후보-길이-거부-원인자격-후보-후속)이다.

2026-10-24 PR #30 당시 기록: PR #29의 [v1 통합 검증](UPDATE_VALIDATION.md#v1-통합-후보-검증)은 main `641f12f`에 반영됐다. 당시 후보는 PACK-BASELINE 22/DRAFT·77개 점검 정의·정책 22점검/50경로·자동 연결 11개·REQ-09 revision 6·PLT-04 revision 3·adapter 0.4.2·declared 0.14.0이다. [새 계약 실측](UPDATE_VALIDATION.md#req-09plt-04-판정-의미-보완-후보)은 양쪽 실제 실행·반출/복원·재판정을 완료했지만 후보 REQ-09 보안 UNKNOWN으로 수락 실패다. update 종료0·harness 종료1·558.134초를 구분한다. 나머지 4개는 양쪽 보안/기능 PASS이며 PLT-04는 후보 빈 목록의 부분 범위다. PR #28/#29의 팩21 성공을 소급하지 않는다. 자동 연결 수는 실제 전체 분기 검증 수가 아니며 공식 출시·기업 승인은 별도다.

## REQ-09·PLT-04 판정 보완의 입력과 확인

REQ-09의 `max_input_tokens`·`rejection_codes`·전체 허용 endpoint/입력 형식은 기업 정책대로 입력한다. HTTP 상태·오류 본문·실제 소비 근거는 서로 다른 관측이다. `challenge_rejection_results`가 verified여야 확인된 거부 근거가 되며, HTTP 200 오류 객체는 실제 수용·소비나 FAIL의 근거가 아니다. 원시 2xx의 `accepted_challenges`를 수용 증명으로 해석하지 않는다. 자동 실행에서 소비가 미계측이면 `challenge_consumption=unknown`이며 `confirmed_input_violations`를 꾸며 채우지 않는다. 거부도 위반도 확인하지 못한 사례는 UNKNOWN으로 남긴다.

PLT-04의 기존 `forbidden_env_names`·`forbidden_mount_paths`는 유지한다. 후자는 bind/volume 목적지가 선언한 금지 경로와 상위·하위로 겹치는 것 자체를 금지하는 정책이다. `forbidden_mount_violations`는 그 정책의 위반이며, 실제 자격 전달·파일 읽기 성공을 뜻하지 않는다. 기존 금지 정책을 비워 FAIL을 없애지 않는다.

선택 입력 `credential_candidate_paths: list[str]`는 별도의 절대 경로 목록이다. 생략은 선정의 새 필수 키 오류가 아니지만 후보 관측이 미수집되어 PASS는 UNKNOWN으로 보류된다. 양식의 `null`은 실제 목록이 아니므로 미설정으로 둘 때는 키를 생략한다. 명시적인 `[]`는 요청한 후보가 없다는 뜻이며 기존 환경변수·마운트 정책의 부분 검증만 PASS 후보가 된다. 실제 기업 후보 목록을 줄여 통과시키지 않는다. 마운트 금지 목록에서 자동 복사하지 않는다. 실행기는 후보 파일 본문을 읽지 않고 worker와 수집 주체의 UID/GID/groups·namespace·capability·LSM 및 전후 메타데이터 안정성을 대조한 뒤 `os.access`의 읽기 권한을 확인한다. 후보 경로는 중복 없는 최대 32개·경로당 UTF-8 128바이트의 정규 절대 경로만 지원한다.

`credential_candidate_results`의 absent/denied/candidate_readable/unknown/shadowed와 `credential_candidates_complete`·근거를 함께 확인한다. candidate_readable은 위 주체/메타데이터 계약으로 확인한 읽기 권한이며 실제 본문 읽기·자격 내용·자격 사용의 인증이 아니다. 확인된 금지 환경변수·마운트 위반 또는 candidate_readable은 독립 FAIL이다. 상위 마운트만으로 파일 존재·worker의 접근을 추정하지 않는다. 주체 불일치·권한 메타데이터 미확인·symlink·가려진 중첩 마운트는 불명확한 상태로 보존한다. 운영 비밀 원문은 읽거나 수집하지 않는다. 실험에서는 무해한 canary를 사용할 수 있으나 canary 내용/접근 인증 프로파일은 지원하지 않는다. 이 한정 시험은 실제 자격 전체·동적 provider·issuer의 사용 권한을 입증하지 않는다. 정책 템플릿은 실제 기업값을 자동 채우지 않으며 EXAMPLE/DRAFT·official=false를 유지한다.

**알려진 한계(LSM)**: 후보 관측은 각 프로세스의 `/proc/<pid>/attr/current`(AppArmor·SELinux 등 LSM 보안 컨텍스트)가 비어 있지 않아야 주체를 확인한다. LSM이 없거나 컨텍스트를 읽을 수 없는 호스트에서는 `WORKER_SUBJECT_UNVERIFIED`로 모든 후보가 unknown이 되고 PASS는 UNKNOWN으로 남는다. 설계상 보수적 처리이며 그 호스트에서 PLT-04 후보 관측은 지원하지 않는다.

[PR #28의 두 이미지 실측](UPDATE_VALIDATION.md#실측-결과--c-후속-2026-10-24)은 vLLM API 0.31.0/0.32.0 CPU에서 기존 5개 점검의 양쪽 보안·기능 PASS와 수락·반출/복원·저장 팩 재판정 일치를 확인했다. 이 기록을 통합판에서 새로 실행한 결과로 표시하지 않는다. 비교는 모두 NO_CHANGE여서 실제 차이 탐지는 미증명이며 성능 UNKNOWN·정책/팩 DRAFT·official=false다.

실제 EVD 10개의 출처는 MEASURED지만 REQ-01/REQ-03 Result의 검증 수준은 DECLARED다. REQ-01 일부 상태 변경 경로는 HTTP를 보내지 않고 TCP 차단 근거를 사용한다. 모든 handler 실행·기업 승인·11개 점검 모든 분기의 실증으로 확대하지 않는다.

## 현행 정책·로그 증거와 설치 검증

[이번 보강](POLICY_RELIABILITY_BATCH.md)은 REQ15 수동 계약·로그 수신 상한/출처·저장 로그 재검증·설치본 CI·준비 안내를 연결한다. [로그 도구](LOG_EVIDENCE.md), [REQ15 입력](POLICY_M14.md), [wheel 검증](INSTALLED_VERIFICATION.md)을 따른다. prepare-* JSON의 guidance는 원인/다음 조치를 추가하며 ready와 점검 지원·실제 판정을 구분한다. 입력 실패 때 의존 검사는 미확인이고 총 메모리가 여유 메모리를 뜻하지 않는다.

## 두 이미지 CPU 업데이트 비교

[두 이미지 비교 안내](UPDATE_VALIDATION.md)에 준비 입력·Windows 명령·수락 기준을 정리했다. `python -m guard.update_validation`의 기본은 읽기 전용 준비 검사이며 실제 기동은 `--execute`가 필요하다. 분석은 `--analyze-existing`으로 원본을 보존한다. 최소 다섯 점검의 보안/기능 구체 판정과 반출·복원·저장 팩 재판정을 확인한다. 후보 미준비로 시험하지 못했던 [도구 구현 당시 기록](UPDATE_VALIDATION.md#이번-검증-결과)과 이후 위 PR #28의 성공 기록을 구분한다. 준비 성공은 시험 수락이 아니다.

## 현행 자동화 보강과 지원 범위

직전 [자동화 보강](AUTOMATION_HARDENING.md)의 기록은 팩20/DRAFT·REQ-11 revision 3·정책21점검/46경로·declared/0.12.0 기준이다. [정책·로그 보강](POLICY_RELIABILITY_BATCH.md)의 팩21/DRAFT·정책22점검/49경로·declared/0.13.0은 그다음 이력이며 현행은 위 팩22 판정 보완 후보를 따른다. 자동 연결11개와 REQ-11의 생성 출력 귀속 미검증·전체 보안 PASS 보류는 유지한다. 과거 1~3차 CPU 실측과 새 합성 회귀를 합산하지 않는다.

단일 엔진은 prepare-validation → validate, 두 환경은 prepare-update → update를 사용한다. 새 준비 출력의 side별 check_support에서 SUPPORTED/UNSUPPORTED/UNKNOWN·이유·한계를 읽는다. ready는 입력/호스트 준비 상태이며 모든 점검의 지원이나 PASS가 아니다. 유효하지만 미지원인 정책은 지원 안내로 구분하고 다른 점검을 제거하지 않는다. COMPLETED/MEASURED는 전체 챌린지나 생성 출력 귀속의 입증 수가 아니다.

[실제 CPU 3차 기록](AUTOMATION_PHASE3.md)·[선별 증거](evidence/automation-phase3-20261023.json), [명시적 real 설정](../examples/windows-cpu-binding-logs.toml)·[EXAMPLE/DRAFT 정책](../examples/automation-binding-logs-policy.yaml)을 참고한다. 실제 ENV·로컬 snapshot 경로가 필요하며 모델/이미지 수신은 자동으로 하지 않는다. 실행은 새 store/output으로 하나씩 진행하고 기존 컨테이너·모델·증거를 보존한다. 기본 guard.toml은 fake 시연 설정이다. CPU 성능 UNKNOWN·팩/정책 미승인·official=false를 유지한다.

아래는 과거 단계의 명령과 실증 계약이다. 당시 수치·팩 revision·지원 환경 설명을 현행 완료 주장으로 해석하지 않는다. 당시 PR 통합 선택지는 [보강 계획](AUTOMATION_HARDENING.md)의 이력이며 현행 두 이미지 수락 기준과 실측 결과는 위 UPDATE_VALIDATION 안내를 따른다.

## 기존 실행 안내와 이력

당시에는 Python 3.11과 로컬 Docker를 사용했다. Windows Desktop/WSL2의 제한 CPU 제품 실행은 과거 1~3차 기록에서 확인했다. 현재 변경의 직접 검증은 위 보강 문서에 별도로 기록하며 macOS·임의 하드웨어·GPU 전체의 실증 완료로 확대하지 않는다.
실제 엔진 시험에는 사전에 받은 HF 모델 캐시와 호환 아키텍처의 pinned 이미지가 필요하다.
정책/팩 DRAFT이면 공식 보고서가 아니다.

## 설치와 기본 검증

현행 개발·guard 실행·CI는 64비트 Python 3.12(`>=3.12,<3.13`)를 사용한다. 위 3.11 환경은 과거 실증 기록이며, 대상 vLLM 이미지의 내부 Python·digest·검토 프로파일은 호스트 버전 전환과 독립이다. 기존 3.11 가상환경과 원본 증거는 덮어쓰지 않고 새 경로에 3.12 환경을 준비한다. [전환 검증](INSTALLED_VERIFICATION.md#python-312-전환-검증)을 따른다.

```sh
uv sync --frozen
uv run guard --help
uv run pytest -q
uv build --wheel
```

기본 테스트는 Docker/engine을 제외한다. 새 실행기는 실제 엔진 검증과 구분한다.
실증의 store와 output은 기존 out/5b 밖의 새로운 경로를 사용한다.

M10의 REQ-09는 두 required 정책 `allowed_endpoints`·`allowed_input_formats`도 필요하다.
전체 승인 목록이 정의의 단일 engine POST/token_ids 표본과 일치할 때만 지원하며, 목록을 줄여 맞추지 않는다.
빈 양식은 값을 채우지 않고 미입력은 NOT_RUN, 미지원 범위는 요청 전 ABORTED다.
수동 증거는 `evidence template --include-automated`·비공백 override_reason과 두 generic 범위 관측을 사용한다.
[M10 계약과 검증](POLICY_M10.md)을 확인한다. 실제 엔진/두 이미지 실증은 별도다.

## 기존 audit

새 기업 정책 파일은 아래 명령으로 만들고 설명 주석을 따라 실제 결정값을 입력한다.
빈 양식도 기존 로더/선정에서 읽히지만 선언된 필수 키가 없으면 `NO_POLICY_VALUE`다.
기존 출력 파일은 덮어쓰지 않고 입력 팩 내부 출력도 거부한다. [입력 계약·설명 출처](POLICY_TEMPLATE.md)를 함께 확인한다.

```sh
uv run guard policy template --pack packs/PACK-BASELINE --out my-policy.yaml
```

```sh
uv run guard select --pack packs/PACK-BASELINE --env examples/env.yaml --policy examples/policy.yaml
uv run guard run --plan PLAN-ID
uv run guard judge --plan PLAN-ID
uv run guard report --plan PLAN-ID --json report.json --html report.html
uv run guard verify
```

기본 guard.toml은 FakeExecutor 시연이다. 실제 엔진 결과로 해석하지 않는다.

수동 증거는 `guard evidence template --plan PLAN-ID --out manual-v2.yaml`로 생성해 입력한다.
SUP-14/LIF-07은 항목·case·첨부 연결이 필요하며 /1 읽기 호환과 재판정 영향은
[M4 수동 증거 계약](POLICY_TEMPLATE.md)을 따른다. 필수 목록만 입력했다고 PASS가 되지 않는다.

## Managed 업데이트

[managed 설정 예시](../examples/update-managed.toml)를 복사하고 환경/정책을 준비한다.

각 ENV에는 engine.name/version/image_digest(repository@sha256:64hex),
model.id/revision/inventory_sha256, runtime.gpu_count를 명시한다.
HF 캐시는 `models_dir/hub/models--ORG--MODEL/snapshots/REVISION`을 사용한다.
model inventory는 snapshot 전체 파일의 정렬 목록 해시다.

```sh
uv run python tools/model_inventory.py /cache/hub/models--ORG--MODEL/snapshots/REVISION
uv run guard update --pack packs/PACK-BASELINE --baseline env-a.yaml --candidate env-b.yaml --policy policy.yaml --config update.toml --store .guard-update --output out/updates
uv run guard verify --store .guard-update
```

각 side는 다른 컨테이너/target으로 순서대로 기동·실행·정리된다.
ENV의 버전/이미지 digest와 실제 준비된 엔진이 다르면 ABORTED로 기록한다.
기동 실패가 있어도 가능한 다른 side와 보고서를 남긴다.
종료 코드 0은 파이프라인 실행 성공이며 보안 PASS를 뜻하지 않는다.
기동/실행/필수 자동화 미실행 실패는 종료 코드 1이다. 수동 전용 점검은 보고서에서 UNKNOWN으로 남는다.

CPU ENV 예시(실제 확인한 환경값으로 작성):

```yaml
runtime:
  gpu_count: 0
  cpu:
    architecture: x86_64
    model: REPLACE_WITH_ACTUAL_CPU
    system: Linux
    kernel: REPLACE_WITH_ACTUAL_KERNEL
    threads: 2
```

이 값은 ENV 신고값이며 자체 하드웨어 계측 증거는 아니다. CPU 성능 축은 UNKNOWN이다.
GPU 사용 시 cpu 프로파일을 넣지 말고 실제 GPU/driver를 제공한다.

## 외부에서 준비한 두 대상

[external 예시](../examples/update-external.toml)처럼 side별 target 경로를 지정한다.
같은 target 파일을 두 side에서 재사용하지 않는다. 각 실제 환경과 target을 별도로 확인해야 한다.
각 target은 lab=false, observed_version, engine.image_digest를 포함하고 해당 ENV와 일치해야 한다.
target의 `comparison_config`에는 엔진 이미지 자체를 제외한 실제 launch/보안 조건을 기록한다.
이 정보가 없는 과거 target은 보안 시험은 가능하지만 설정 조건 미확인으로 비교 불가다.
시험 역할 키는 이름으로만 설정하고 값은 환경변수에 둔다.

## 신규 점검

PLT-04의 executor 이름은 credential_exposure다. 정책은 forbidden_env_names/list[str],
forbidden_mount_paths/list[str]이며 컨테이너 환경변수 이름과 명시적 마운트 금지를 확인한다.
팩22의 선택 credential_candidate_paths/list[str]는 위 설명대로 별도 접근 관측에 사용한다.
처음에는 실제 자격 대신 GUARD_TEST_CREDENTIAL 같은 무해한 표식을 사용한다.
자격값은 수집하지 않으며 읽기 실패가 있으면 PASS가 되지 않는다.

REQ-09의 executor 이름은 input_validation이다. 정책은 max_input_tokens/int,
rejection_codes/list[str]이며 예시는 ["400", "422"]다.
실행 설정에는 vocab_size/int와 vocab_source/str를 pinned 모델 근거와 함께 넣는다.
max_probe_tokens는 기본 4096이며 예산보다 큰 시험은 요청 전 ABORTED다.
정상 token-array [0]가 통과한 뒤 범위 밖 ID와 입력 길이를 시험한다.
다른 입력 형식/모든 경계 조합/실제 모델 범위 전체를 검증한 것으로 해석하지 않는다.
이 신규 점검 절을 작성한 당시에는 PLT-04/REQ-09를 실제 엔진 실증하지 않았다. 이후 두 점검은 위 PR #28의 두 이미지 실측에 포함됐다. 다른 환경에 활성화하기 전에는 해당 정책과 설정을 검토한다.

## 실제 두 이미지 통합 테스트

```sh
export GUARD_UPDATE_CONFIG=/absolute/update.toml
export GUARD_UPDATE_BASELINE=/absolute/env-a.yaml
export GUARD_UPDATE_CANDIDATE=/absolute/env-b.yaml
export GUARD_UPDATE_POLICY=/absolute/policy.yaml
uv run pytest -m engine tests/invariants/test_inv_09.py -q
```

조건이 없으면 해당 테스트는 사유와 함께 skip한다. managed/real 설정 및 서로 다른 이미지 digest를 강제한다.
기본 테스트의 통과 수를 이 실증의 성공으로 대체하지 않는다.

## 증거 보존·복원

update 출력에는 plan.txt와 JSON/HTML이 있고 store에는 ENV/POL/PLAN/EVD/RES/RPT와 팩 보존본/artifacts가 있다.
전용 store 전체와 실행 설정을 바이트 그대로 보관한다. 키·불필요한 환경변수 파일은 보관하지 않는다.
복사본에서 verify를 실행하고 plan.txt의 PLAN으로 judge/report를 실행한다.
현재 packs 폴더가 없어도 정상 팩 보존본이 있으면 재판정할 수 있다.
과거 팩이 없는 경우 정확한 git revision에서 꺼낸 팩을 pack import로 먼저 넣는다.
현재 저장소의 과거 exposed/isolated store에는 참조 증거 파일이 각각 1개 누락돼 있다.
원본을 확보하기 전에는 해당 저장소의 verify/완전한 재판정 성공을 보장하지 않는다.
기존 evidence를 새 버전으로 재판정하면 새 RES revision이 생기며 과거 결과는 수정하지 않는다.
정리 실패 같은 실행 생명주기 한계는 최초 update 보고서도 함께 보관한다.
judge/report 재실행은 시험 자원 정리를 다시 실행하거나 확인하는 명령이 아니다.

## AWS 실증 준비

위 Linux 절차를 재사용한다. 실제 계정/리전/자원 생성·가격 산정은 이번 작업에서 수행하지 않았다.
이미지 아키텍처·모델 캐시·CPU/GPU 조건·운영자 권한·엔진 격리·실행 시간 상한·자원 보관 기간을 먼저 정한다.
시험 종료 후 guard.run 라벨의 컨테이너/네트워크, 인스턴스, 볼륨, 로그/증거 보관 자원을 확인한다.
정리 실패 시 보고서만 보고 자원 정리까지 완료됐다고 판단하지 않는다.


## 실증 준비와 증거 묶음 명령

`guard prepare-update`로 입력과 로컬 Docker/이미지를 먼저 확인한다.
`guard evidence export/restore`로 검증된 저장소를 새 위치로 반출/복원한다.
명령·제약·5개 점검의 PASS/FAIL/UNKNOWN 사례 입력은 [실증 안내](VALIDATION.md)를 따른다.
REQ-09의 `max_probe_tokens`는 실제 경계 초과 요청(max_input_tokens+1)을 포함하는 요청당 상한이다.
probe 정리 실패는 증거를 보존하고 execution.cleanup_errors와 재판정 보고서에 남으며 명령은 실패한다.


수동 입력·대체 옵션과 반출/복원을 함께 사용하는 절차는 [통합 안내](INTEGRATION.md)를 따른다.

## M5 승인 기록 체계 입력

정책 양식의 SUP-01/SUP-02 아래에 각각 기업이 정한 approval_record_system 문자열을 입력한다. 실제 키/암호·계정 허용표를 대신 넣지 않는다. 미입력은 NO_POLICY_VALUE이며 공백은 수동 PASS를 UNKNOWN으로 보류한다. 값만 채워도 승인/배포 권한이 검증되는 것은 아니다.

기존 evidence template → 관측 설명/첨부 입력 → evidence import → judge → report 절차를 사용한다. 두 점검에는 항목별 policy_evidence를 만들지 않는다. JSON/HTML의 승인 체계 문맥·POLICY_CONTEXT·미인증 자기 신고·official 여부를 함께 확인한다. 과거 revision 11 보존 팩의 SUP-01/SUP-02는 신규 전제를 소급하지 않는다. 과거 LIF-07 추론 충돌 때문에 보류된 확인 위반은 declared 0.4.0 재판정에서 FAIL이 될 수 있으며 원본 EVD/RES는 보존된다.

OBS-11의 필수 기록·기한·시계 오차·장애 대응은 [후속 결정표](POLICY_M5_PLAN.md)를 먼저 확정한다. collector 장애 자체를 공격 성공이나 종속 시험 PASS로 처리하지 않는다.

## OBS-11 수동 검증 (/3)

정책 양식에서 OBS-11의 필수 목록과 사건별4개 정수map을 기업 기준으로 입력한다. 실제 값/담당/승인은 자동 생성되지 않는다. 다른 점검처럼 select → evidence template → 각case 관측·주장별 첨부 입력 → evidence import → judge → report를 사용한다. 필수 누락에 경보가 필요한 최초 프로파일의 적용 근거를 입력하고 적용 불명/조건부 범위는 UNKNOWN으로 남긴다.

부재는 고장 난 collector 로그만으로 확정하지 않는다. 독립 발생과 전체 조회 시작/종료·경보 수신점·시계 오차를 확인한다. 모든 시각은 timezone을 포함해 따옴표로 쓰고 소수초는3자리 이하다. 누락 관측을 OBS11 FAIL과 동일시하지 말고 정상 처리/경보 부재의 추가 근거를 구별한다. 미수신·조회 실패·창 미종료는 부재 확정이 아니다. 실제 collector 주입이나 운영 중단을 이 명령이 수행하지 않는다.

관측의 실제 필드·정책/시간 의미·구형 /1·/2 처리·보고서 제한은 [M7 계약](POLICY_M7.md)을 따른다. JSON/HTML의 AUDIT_COLLECTION 이유·미인증 신고·official 상태를 함께 확인한다. 복구 사실은 기록하지만 복구 SLA·작성자 인증·실제 다른 RES 전파는 검증하지 않는다.

## M8 usage/replay 정책 문맥 확인

현재 팩14에서 정책 템플릿을 생성하면 OBS04/05의 usage_allowed, OBS07/08의 replay_allowed가
주석으로 안내되고 실제 값은 비어 있다. [정책 입력 안내](POLICY_TEMPLATE.md)를 따라 기업이 정한 bool만 입력한다.
false는 비활성 관측이 아니며 true는 실제 구현·권한·승인 증명이 아니다. 같은 범위의 공유 값은 업무 검토한다.

기존 select → evidence template(/3) → evidence import → judge → report 흐름을 사용한다.
새 증거 payload나 실행 명령은 없다. POLICY_CONTEXT의 실제 true/false·분기 사유와 JSON/HTML의
DRAFT·official·미인증 자기 신고·자동 검증 한계를 함께 확인한다. OBS04 false / 나머지 true 외의 문맥은
신고 PASS를 UNKNOWN으로 보류하며 FAIL은 보존한다. false를 비적용이나 자동 제외로 해석하지 않는다.
전체 분기·기업 결정·미검증 범위는 [M8 계약](POLICY_M8.md)에 있다.

## M11 수동 관측 상충 처리

REQ-09의 새 입력 범위 계약에서는 신고 PASS와 저장된 보안/기능 관측이 판정표와 일치해야 PASS 후보가 된다. 상충은 UNKNOWN 사유로 JSON/HTML에 표시한다. 원본을 고치지 말고 기존 PLAN/팩으로 재판정해 새 declared0.8.0 RES를 만들면 된다. 같은 as_of 재실행은 멱등이며 수동/3·override_reason·Ref/side 보호는 유지한다. [M11](POLICY_M11.md)에 명령/검증과 미구현 캐시 계약을 구분해 기록한다.

## M12 REQ-16 수동 캐시 문맥 검토

[상세 절차](POLICY_M12.md)에 따라 새 팩16과 POL로 select → evidence template → generic 값·첨부 입력 → evidence import → judge → report를 실행한다. NO_EXECUTOR는 수동 계약 등록 후에도 남는다. 기존 /1/2 읽기와 /3, Ref/side·append-only를 보존한다. 같은 as_of 재판정은 멱등이며 과거 PLAN은 정확한 팩15 보존본으로 재판정한다. 새 팩을 과거 PLAN에 강제로 대입하지 않는다. 캐시 probe나 실제 엔진을 띄우는 절차는 이번 범위에 없다.

## M13 저장 증거 재판정

M12의 inference=unknown이 구조오류로 보류된 증거는 원본을 고치지 않고 정확한 기존 PLAN/팩16과 같은 as_of로 judge한다. 보안 근거/INV11이 충분하면 새 declared0.9.1 RES에서 보안 UNKNOWN→PASS가 가능하나 원본 신고 UNKNOWN은 승격하지 않는다. JSON/HTML에 기능 미확인과 DECLARED 한계를 남긴다. 원본·Ref·side·config hash는 그대로다. [M13](POLICY_M13.md)의 직접 검증과 REQ15 후속 설계를 따른다.
# Windows CPU 자동화 1차

단일 엔진의 제품 기동은 `guard prepare-validation` → `guard validate`를 사용한다. [Windows CPU·신규 3개 점검·기존 5개 사례 검증](AUTOMATION_PHASE1.md), [실행 설정](../examples/windows-cpu.toml), [EXAMPLE/DRAFT 시험 정책](../examples/automation-policy.yaml)을 참고한다. 아래 과거 단계의 5개 실행기 설명과 당시 실측 범위는 이력이며 현재 구현·검증 상태는 자동화 1차 기록을 따른다.
