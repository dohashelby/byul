# 요청별 native 근거 연속 연결

Gateway는 기존 root host observer가 정제한 결과만 읽는다. UPDATE venv의 기존 `tools.update_engine_link`에 `watch` 모드를 연결했으며 원 `KubernetesLifecycleReader`, `capture_session`, `select_request`, `project_termination`을 그대로 사용한다. Gateway에는 guard 의존성·kubectl·센서 읽기 권한을 추가하지 않는다. native feed 오류는 관측 UNKNOWN/수집 상태로 나타나며 추론의 health/target/정책 보류 계약을 바꾸지 않는다.

## 운영 경로

native observer 설정에는 별도 `kubeconfig` 절대 경로를 지정한다. root 소유 비공개 파일을 사용하며 해당 고정 context와 namespace에서 기존 수집기의 Pod 조회·exec를 허용한다. `/etc/serving-runtime/kubeconfig`는 Tetragon 상태 서비스의 읽기 전용 자격 파일이므로, native observer에는 준비한 `/etc/serving-runtime/native-kubeconfig`를 별도로 지정한다. installer와 직접 실행 모두 이 명시 경로를 사용하며 자격 값·해시는 출력하지 않는다.

승인된 source/image/ENV/target/owner preflight 및 native assets 설치·Ready·정상 요청 확인은 기존 배포 절차에서 수행한다. 환경 파일은 정확한 ENV Ref와 해시, target은 관측된 Pod UID/CID·image·restart/run 및 전체 canonical target SHA, prepared는 원 native session을 가리켜야 한다. observer 시작 후 이 값들을 다른 세대로 자동 재발급하지 않는다.

host에 root 소유 feed 디렉터리(0750, 명시 reader GID)와 전용 snapshot 디렉터리(0700)를 준비한다. 기존 observation 디렉터리의 owner를 바꾸지 않고 root 전용 하위 디렉터리를 추가한다.

```sh
sudo install -d -o 0 -g 10010 -m 0750 /srv/serving/observation/native
sudo install -d -o 0 -g 0 -m 0700 /srv/serving/runtime/native-snapshots
```
 Gateway journal은 원 writer UID 10000의 0600 파일을 root observer가 읽는다. 다음은 기존 host UPDATE venv에서 실행하는 연결 명령의 형태다. 파일 경로와 reader GID는 원 배포의 고정 입력으로 치환한다.

```sh
PYTHONPATH=runtime:runtime/src:update/src /host/venv-update/bin/python -B -m tools.update_engine_link watch \
  --environment /owned/ENV.json --target /owned/target.json \
  --prepared /owned/assets/manifest.json --records /srv/serving/runtime/data/gateway_records.jsonl \
  --output /srv/serving/observation/native/receipts.json --capture-dir /srv/serving/runtime/native-snapshots \
  --reader-gid 10010 --gateway-writer-uid 10000 --interval 5 --duration 30 \
  --max-delay-ms 1000 --required-scopes asgi_transport frontend_request engine_core scheduler_state worker_batch kv_references gpu_transfers \
  --node-image-verification local-containerd-v1
```

Gateway `mvp`의 원 설정에 `--native-receipt-feed /srv/serving/observation/native/receipts.json --native-receipt-reader-gid 10010 --native-receipt-producer-sha256 <승인된-runtime/tools/update_engine_link.py-전체SHA256>`를 추가한다. 기존 observation mount는 RO로 유지하고 root:10010/0640 feed를 명시 reader만 읽는다. producer source hash와 내용 hash는 정확한 판본·원 capture bytes 참조이며 native 생산자 인증을 신설하지 않는다. `producer_authenticated=False`와 원 EXAMPLE/DECLARED/MEASURED 등급을 유지한다.

SGLang은 원 준비 profile `sglang-native-lifecycle-metadata/1`과 원 여섯 scope를 사용하며 위 vLLM 명령에서 `engine_core`를 제외한다. SGLang anchor에 없는 vLLM 전용 물리·외부 ID 필드를 생성하지 않고, 원 projector 호출에 사용한 상류 ID hash를 wrapper의 입력 참조로 보존한다. SGLang의 MEASURED 수신도 원 verified closure가 없으면 unknown을 유지한다.

연속 경로에는 seal 옵션이 없다. live session의 실제 anchor/events를 보존하며, 일반 미봉인 자료의 강한 종결·GPU release는 UNKNOWN이다. 명시적 checkpoint 수집은 아래 별도 coverage 조건과 기존 요청별 양성 근거를 모두 확인한 범위만 연결한다. 원 판정기는 고유 selection/disconnect 요청을 다룬다. 정상 요청·미완료 요청·고유 UUID 미관측은 기존 not_connected를 유지한다. 원 packet과 projection이 없는 경우 수신·종결 True를 만들지 않는다. EXAMPLE/DECLARED의 연결은 unknown이며 MEASURED의 원 physical request anchor가 맞을 때만 received로 표시한다.

## 보존·경계

각 prepared session의 0700 하위 디렉터리에 현재와 직전 live capture bytes 두 개만 보존하며 현재 feed의 원 capture hash를 확인할 수 있다. 외부에서 sealed된 session은 한 session당 최초 `sealed-<session>.json` 원 bytes를 고정한다. 후속 bytes가 달라지면 덮어쓰지 않고 unavailable로 내린다. 기존 sealed capture를 사용할 때는 `--packet /owned/original.json --packet-sha256 <원전체SHA256>`를 명시한다. 입력은 root 소유 0600 또는 명시 reader GID의 0640 정규 파일이어야 한다. 원 자료가 다른 공개 모드라면 private 경로에 bytes 그대로 복사하고 원 hash를 대조한다. 원파일을 다시 쓰거나 seal하지 않으며 ENV Ref·전체 target SHA·prepared session·owner/run/generation·원 projector를 다시 결속한다.

producer 갱신은 reader의 생존 시각이다. native 활성/봉인/한도도달/종료 상태, 실제 capture 읽기 시각, 원 native monotonic/clock context와 분리한다. 봉인 자료를 반복 발행해도 현재 감시는 성공으로 표시하지 않는다. live 미봉인 관측도 향후 감시 가능성은 unknown/수집 partial이며 원 발생 시각을 UTC로 변환할 근거가 없으면 신선도 unknown을 유지한다. 한도도달·종료·오류는 현재 수집 미확인이다. 정확한 source pin·내용 hash·문맥을 검증한 봉인 exactUUID 근거는 producer가 stale이어도 과거 자료로 읽을 수 있으며 현재 관측 실패와 별도로 보존해 과거 요청 상세에 표시하고 engine_live는 not_available로 둔다. 과거 observation session도 원 요청 context가 정확히 일치할 때 과거 근거로만 조회한다.

root 소유·명시 reader mode·정규 파일·NOFOLLOW·inode/내용 안정성·크기·strict JSON·생성 시각/freshness·source pin을 검사한다. Gateway journal은 끝의 16 MiB·최근 최대 100개 요청만 읽는다. 잘린 구간의 불완전한 첫 행은 버리고 시작/종료 구간을 함께 확보하지 못한 요청은 연결하지 않는다. 각 요청은 원 UUID/상류 ID·현재 Gateway observation session·run·환경·Pod/CID/image/restart·정책/번들·route·요청 구간 전체를 기존 select_request로 맞춘다. 과거 Gateway session의 근거를 현재 live 요청이나 현재 준비 상태로 연결하지 않는다. collection failure/stale/bad hash/다른 세대는 기존 not_connected/UNKNOWN으로 돌아간다. 원 일곱 scope 결과와 모든 부정·미확인 진단은 projection에 남고 기존 요청 상세의 원본 기록에서 볼 수 있다.

이 연결은 기존 host observer를 상시 실행하는 배포 설정과 reader RO mount를 필요로 한다. 현재 작업은 로컬 제품·CPU 계약 검증이며 실제 엔진/GPU 인수는 별도다.

## 호스트 상시 서비스 설치

`deploy/gpu-lab/host/install-native-observer.sh`가 승인된 UPDATE Python과 현재 소스 경로로 system unit `byul-native-receipts.service`를 생성·검사하고 `enable`·`restart`한다. 서비스는 부팅 후 시작하고 프로세스 장애 때 5초 뒤 재시작한다. 설정 오류는 종료 코드 2로 멈추므로 원인을 수정한 뒤 명시적으로 재시작한다. Gateway·Platform 서비스와 별개로 실행한다.

설치 전 기존 승인 절차에서 ENV·전체 target·prepared session과 원 assets를 확정한다. `host/native-observer.example.json`의 네 SHA256 자리에는 승인된 실제 bytes의 해시를 넣는다. ENV·target·manifest는 root 소유 0600 또는 지정 reader GID의 0640 정규 파일로 보관하고 상위 디렉터리의 group/other 쓰기를 막는다. manifest와 같은 디렉터리에 원 assets 전체를 bytes 그대로 보존한다. 이 입력들은 서비스를 재시작해도 고정되며 파일이 바뀌면 새 세대에 자동 결속하지 않고 설정 오류로 멈춘다. 엔진 교체 시 기존 target 비교가 수집 오류를 내고 이전 feed는 stale이 된다. 새 ENV·target·prepared와 해당 해시는 관리자가 승인 절차에서 함께 교체한다.

소스·의존성·UPDATE Python은 승인된 배포 사본을 사용하고 root 실행 중 일반 사용자의 교체를 제한한다. root 전용 kubeconfig `/etc/serving-runtime/kubeconfig`는 기존 host native observer의 명령·컨텍스트 범위를 사용한다. Gateway에는 이 kubeconfig를 마운트하지 않는다. 서비스가 root인 이유는 UID10000의 0600 Gateway 저널, native capture 파일과 기존 Kubernetes native reader를 기존 host 경계에서 읽기 위해서다. 출력은 root:10010/0640이며 Gateway의 기존 RO observation mount만 사용한다.

```sh
# 위의 root 소유 feed/snapshot 디렉터리를 먼저 준비한다.
# native-observer.json에는 승인된 경로·해시·reader GID를 기록한다.
sudo install -o 0 -g 0 -m 0600 /approved/native-observer.json /etc/serving-runtime/native-observer.json
sudo /approved/byul/runtime/deploy/gpu-lab/host/install-native-observer.sh \
  /approved/update-venv/bin/python /etc/serving-runtime/native-observer.json
sudo systemctl status byul-native-receipts.service
```

`check`는 설정의 보호 권한·source pin·세 입력의 bytes pin을 확인한다. 실제 native 접근·수집 성공은 시작 후 feed와 Gateway `/api/status`의 `native_receipt_observer`를 함께 확인한다. systemd의 `active`는 수집 성공 판정이 아니다. 중지·오류 뒤에는 마지막 feed의 freshness window가 지나면 현재 관측이 unavailable/stale로 바뀐다. active 미봉인 자료의 수집 상태는 partial, 봉인 자료·한도도달·중단은 현재 관측 오류로 유지한다. 설치는 native session을 새로 만들거나 seal하지 않으며, 과거 봉인 근거는 기존 조회 경로로 보존한다.

SGLang 설정은 승인된 SGLang ENV·target·prepared를 사용하고 `required_scopes`에서 `engine_core`를 제외한다. 양 엔진 모두 Gateway의 `--native-receipt-producer-sha256`를 설정의 `producer_source_sha256`와 일치시킨다. 실제 서비스 설치·중지·재시작과 엔진/GPU 수집 인수는 배포 환경에서 별도 확인한다.


## 열린 session의 요청별 checkpoint

host watch는 `capture --checkpoint`를 사용한다. writer가 기록한 CHECKPOINT까지의 원 hash-chain prefix만 선택하고 같은 파일 inode·owner·process/clock context에서 두 번째 읽기에도 동일 prefix가 존재하는지 확인한다. pre/post 전체 census는 파일 증가 bytes/mtime/ctime만 분리하고 process·파일 집합/identity·권한·owner를 정확히 대조한다. 새 process·미계측 Python process·부분 행·observer gap·BOUND/종료는 coverage 미확인이다.

marker는 같은 session의 prefix seq/hash를 참조하며 각 관련 process의 cutoff가 실제 disconnect monotonic 시각+선정 기한 이후여야 한다. native context와 같은 time namespace/offset에서 읽은 현재 monotonic 시각으로 marker가 15초 이내인지 확인한다. writer가 멈춰 이전 marker만 남으면 현재 수집은 `unavailable`·`checkpoint_stalled` 사유이다. 확정됐던 과거 요청 checkpoint는 제한된 history cache에서 현재 감시 상태와 분리해 조회한다.

별도 `project_checkpoint` 진입점은 기존 요청 binding·slot/KV·fence/출력 반환의 양성 판정 조건을 재사용한다. 원 `project_native`의 SEALED/END 조건은 그대로 유지한다. checkpoint는 session을 seal하거나 원 metadata/END를 재작성하지 않는다. `closure_basis=live_checkpoint`, `checkpoint_coverage_verified`로 근거를 구분하며, marker 관측은 fsync 이후 내구성을 입증하지 않는다. EXAMPLE/DECLARED와 실제 엔진 실행 등급도 유지한다.

## opt-in rolling shard의 지속 수집

엔진 배포에서 `BYUL_NATIVE_ROLLING=1`을 명시했을 때 기존 writer overlay가 같은 process의 canonical 파일과 최신 두 closed shard를 유지한다. host watch의 rolling inventory는 별도 schema로 각 shard의 segment 번호·파일 identity를 수집한다. 각 파일의 START·원 seq/hash chain·SEGMENT predecessor bytes/SHA256·`END/SEGMENT_ROLLOVER`를 그대로 보존하며 원 파일들을 하나의 hash chain으로 다시 쓰지 않는다.

동일 process의 연속된 retained shard만 group으로 연결하고 최신 active shard의 fresh checkpoint가 해당 process의 cutoff를 제공한다. 호출·반환·fence가 shard 경계를 건너면 같은 물리 process/call/token으로 연결한다. 필요한 assignment가 보존 창 밖으로 밀려났거나 중간 predecessor가 빠지면 해당 요청은 UNKNOWN이다. 보존 창 안에서 새로 시작한 요청은 기존 4,096개 projection event 상한을 유지하면서 그 요청의 시간 창을 판정한다. current monitor의 marker 정지·gap·새 process·identity 변경은 기존 미확인을 유지한다.

원 sealed/nonrolling capture envelope은 16 MiB를 유지한다. rolling live capture는 최대 96개 파일(최대 32 process의 active+closed 두 개)과 64 MiB source snapshot으로 제한한다. 각 원 파일은 기존 profile의 2/4 MiB 상한을 따르므로 전체 snapshot이 64 MiB를 넘으면 수집은 UNKNOWN이다. feed는 4 MiB를 유지하고 64 MiB source 참조는 `source_capture_mode=live_checkpoint_rolling`과 검증된 segment coverage가 함께 있을 때만 허용한다. 원 capture archive는 현재·직전 live snapshot을 보존하며 더 오래된 요청의 원 bytes 보존은 보장하지 않는다.

## Gateway 재시작 후 요청 근거

검증된 native 요청 receipt는 기존 private `engine_receipts.jsonl`에 기록한다. 같은 요청은 근거 강도가 높아질 때만 추가하며 이미 확인된 부정 근거가 뒤의 약한 관측으로 지워지지 않도록 한다. 기존 request_id·received_at 보존 정책을 사용하고 새 저장소를 추가하지 않는다.

재시작 시 현재 승인된 producer pin·내용/projection hash·원 source 등급을 다시 검증하고, 조회 때 기존 요청 context 전체를 대조해 과거 근거로만 표시한다. 원본 capture archive는 별도 보존 창을 따르므로 history 항목은 `source_bytes_retention=not_guaranteed`를 명시한다. journal 쓰기 실패는 `historical_storage_state=error`로 보이며 메모리 관측과 구분한다. 기존 보존 정책으로 journal이 정리됐거나 producer pin이 다른 자료는 현재 history 근거로 복원하지 않는다.

## 백그라운드 엔진 health 장애·복구 이력

기존 `engine_health` collector의 성공·실패 후 callback이 `observations.jsonl`에 최초 상태, unavailable, recovered 전환을 기록한다. 같은 상태의 poll은 반복 기록하지 않는다. 수집 시작/종료 UTC 시각, run/session, 당시 target Pod/CID/restart/image context, 정책/묶음과 이전 사건 key를 보존한다. 반복 실패 횟수는 복구 사건에 포함하며 Gateway 재시작을 거친 횟수는 lower-bound로 표시한다. journal 쓰기 실패는 기존 collector 오류 사유로 표시하고 미기록 원 사건을 복구 사건보다 먼저 재시도한다.

Gateway 관측 GET과 Platform 기존 사건 목록·필터·상세 원본을 통해 `engine_health` 출처를 읽는다. HTTP health는 엔드포인트 도달 근거이며 target은 마지막 관측 context다. 개별 worker fault의 인과나 실제 inference 성공은 별도 native/요청 근거로 확인한다. 요청이 없어도 health 장애·복구 기록이 생성된다.
