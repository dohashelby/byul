# A1·A4 추가 인수

`tools/selected_runtime_check.py`는 고정 통합 소스에서 실행한다. 기존 AWS-01~20의 네트워크·권한·정책 시험은 그 도구를 사용하고, 이 도구는 Unicode 이후 정상 요청·동시 UUID·명시한 응답 키의 관측·관측원별 조회 창·Platform 상세 캐시/보존의 추가 경계만 검사한다. 새로운 보안 판정을 만들지 않는다.

## 현재 Gateway 읽기·요청

기존 배포의 추론·viewer 환경변수와 검증된 loopback/port-forward 주소를 사용한다. 토큰 값은 인자나 출력에 넣지 않는다. UPDATE `stream_probe.collect`가 두 연결의 크기·프레이밍·기한을 제한한다. 추가 HTTP 조회는 응답 8MiB와 연결·헤더·본문 전체의 절대 기한으로 제한한다. 기존 runtime `DeadlineWatchdog`가 기한에 소켓을 중단하고 연결을 닫는다. redirect는 따라가지 않는다. timeout은 1~60초, 동시 probe는 두 요청, 생성 출력은 요청당 최대8토큰이다.

```sh
cd /path/to/frozen/byul
export PYTHONPATH=runtime:runtime/src:update/src:platform/api/src
/path/to/validated/python runtime/tools/selected_runtime_check.py live \
  --gateway-url http://127.0.0.1:18080 --platform-url http://127.0.0.1:18700 \
  --model test-model --inference-token-env RUNTIME_INFERENCE_TOKEN \
  --viewer-token-env RUNTIME_VIEWER_TOKEN --timeout 30 \
  --forbidden-key private_debug --forbidden-key prompt_token_ids \
  --output /NEW/evidence/selected-live-vllm.json
```

두 신원을 사용하려면 별도 검증된 `--second-inference-token-env NAME`을 지정한다. 두 신원 옵션 없이 얻은 UUID 독립 결과는 한 주체의 두 연결 범위다. 생성 내용의 수신자 귀속·실제 엔진 병렬실행은 이 UUID 검사에서 확정하지 않는다.

두 연결을 보내기 전 `A1-CONCURRENT-PRECONDITION`은 viewer의 `GET /api/status`에서 `concurrency.in_flight=0`, `limit>=2`를 확인한다. 정상 응답 수신 뒤에도 이전 요청의 최종 기록 저장과 슬롯 반환이 남을 수 있어 최대 `min(timeout, 5초)` 동안 GET만 반복한다. 조회 권한·상태 필드·용량을 확인하지 못하거나 기한 안에 유휴 상태가 되지 않으면 두 요청을 보내지 않고 해당 검사를 UNKNOWN으로 남긴다. viewer가 없으면 익명 GET만 사용한다. 시작 후 받은 429는 그대로 실패로 기록하며 inference POST를 재시도하지 않는다.

`A1-METADATA-SAMPLE`은 명시한 키가 선택한 정상·stream 응답에서 관측됐는지 확인한다. 해당 키의 상시 제거·금지 집행과 모든 오류 분기의 검증은 별도이며 `enforcement_verified=false`를 남긴다. 본문·생성문·인증 header는 결과 파일에 저장하지 않고 byte/hash와 금지 키의 경로만 남긴다. viewer 보호가 설정되지 않은 경우 역할 검사는 UNKNOWN이다.

## 선정 요청 행렬

`requests`는 기존 client/watchdog와 구조화 저널·Platform API를 사용한다. 보호된 viewer 조회와 출력 상한을 먼저 확인한 뒤 정상 전후 대조, 누락·오류·중복 인증, 위조 principal/priority/session/rank/request ID, 여러 줄 본문의 예약필드 유사 문자열, token/KV/embedding/tool/반환 옵션, 출력 상한+1과 경로·method 변형을 확인한다. 실제 프로필이 선택해 연 필드는 거부 대상으로 보내지 않고 조건부 미실행으로 남긴다.

```sh
/path/to/validated/python runtime/tools/selected_runtime_check.py requests \
  --gateway-url http://127.0.0.1:18080 --platform-url http://127.0.0.1:18700 \
  --model test-model --inference-token-env RUNTIME_INFERENCE_TOKEN \
  --viewer-token-env RUNTIME_VIEWER_TOKEN --timeout 15 \
  --output /NEW/evidence/selected-requests.json
```

원저널의 모든 request ID가 조회 ID와 일치한 경우에만 주체·정책·대상·run·최종 상태를 연결한다. 정상 전후와 위조 header 요청은 같은 검증 주체·정책·대상·cache 범위를 유지해야 한다. 거부는 응답 코드/사유와 최종 미전달 기록을 대조한다. Platform을 지정하면 같은 신뢰 필드를 다시 대조한다. prompt/인증 header·응답 본문은 저장하지 않는다.

정상 대조는 authenticated principal·정책·대상이 비어 있으면 UNKNOWN이다. field 거부는 정확한 UNSUPPORTED_FIELD/N_UNSUPPORTED/MAX_TOKENS_EXCEEDED 상태·사유와 같은 주체/정책을 요구하며 다른 인증/입력 오류를 해당 field의 통과로 세지 않는다. API contract 오류는 다른 정보 부족과 동시에 발생해도 실패를 유지한다.

기존 GET→추론 path의 405는 외부 거부를 확인하며 현재 서버 UUID/원장이 없는 경로는 내부 귀속 UNKNOWN으로 남긴다. route-audit 조회는 잘림과 유일 ID 확인을 유지한다. 실제 ingress rewrite, backend 수신 자격, 엔진 로그 미도달은 배포/로그 증거로 별도 연결한다. 이 행렬의 `gateway_journal_not_delivered`를 `engine_log_nonarrival_verified`로 올리지 않는다. 로컬 MockEngine 회귀와 실제 엔진 실행 결과를 구분한다.

## 연결 종료 뒤 새 요청 분리

기존 동시 buffered/SSE 표식 실측은 REQ011 근거로 재사용한다. `separation`은 남은 REQ012 순차 분기만 실행한다. SSE A를 느리게 읽고 닫은 뒤 같은 client label의 새 B를 즉시/250ms 뒤 보내며, buffered A도 header 뒤1byte를 읽고 닫은 다음 새 B를 보낸다. 총6 POST이며 재시도하지 않는다.

```sh
/path/to/validated/python runtime/tools/selected_runtime_check.py separation \
  --gateway-url http://127.0.0.1:18080 --model test-model \
  --inference-token-env RUNTIME_INFERENCE_TOKEN --viewer-token-env RUNTIME_VIEWER_TOKEN \
  --timeout 15 --output /NEW/evidence/selected-separation.json
```

보호된 viewer의 상태·정책·대상을 먼저 확인하고 서버 UUID·원저널의 각 query ID·신뢰 scope를 연결한다. A는 HTTP200·시작 ALLOWED·전달과 인증 주체가 확인돼야 하며 실제 생성 표식 관측 뒤 닫혔고 B가 자기 표식만 정상 수신했을 때 통과한다. 주체·정책·대상은 필수이고 run/instance는 상태 API에서 설정된 값 또는 명시 미설정과 일치해야 한다. A가 먼저 정상 완료하거나 모델이 표식을 따르지 않거나 첫 protocol byte만 관측되면 그 분기는 UNKNOWN이다. buffered의 생성은 header 이전에 끝났을 수 있어 감지 지연과 client close를 별도 기록한다. 생성 내용은 저장하지 않고 byte/hash·표식 관측만 저장한다. 엔진 종결·GPU 회수는 별도 native 증거다.

## 분리된 Gateway·Platform과 보존 시험

실제 서비스 data-dir을 인자로 받지 않는다. `--test-data-dir`은 **부모가 존재하고 자신은 아직 존재하지 않는 새 경로**여야 한다. 도구가 0700으로 만들고 자기 정책·해시 인증 파일·기록·hold를 작성한다. 디렉터리가 이미 있으면 시작 전에 거부한다. 시험 후 증거 디렉터리는 보존하며 임의 삭제하지 않는다.

실엔진을 사용하는 예:

```sh
/path/to/validated/python runtime/tools/selected_runtime_check.py isolated \
  --test-data-dir /NEW/test-data/selected-isolated-vllm \
  --backend vllm --backend-config /READONLY/validated/backend-vllm.json \
  --model test-model --fixture-count 200 --timeout 30 \
  --output /NEW/evidence/selected-isolated-vllm.json
```

backend config의 엔진 주소는 시험 프로세스가 실제 도달할 수 있어야 한다. 현재 배포와 같은 엔진·인증·모델 매핑을 읽기 전용으로 제공한다. SGLang은 `--backend sglang`과 그 엔진의 검증된 config로 순차 실행한다. 이 도구는 새 엔진/Pod·정책을 만들거나 현재 서비스를 바꾸지 않는다.

격리 stack은 실제 engine 정상 요청2개를 대조한 뒤 synthetic final 기록을 총200개까지 채워 실제 OS·Gateway·Platform HTTP로 warm 목록5회와 원천 요청수를 센다. 관측원별 synthetic60개는 기존50개 창을 검증한다. 합성 history/관측은 EXAMPLE로 유지하며 실엔진/GPU 증거로 세지 않는다. 전체 생성·요청 수와 사본 범위가 결과에 남는다.

보존은 미래 시험 시각을 기존 `RecordRetention`에 넣어 report-only 파일 불변과 enforce 삭제·hold를 검사한다. 이미 상세를 읽은 요청의 삭제 후 상세·증거404와 목록 부재, held200을 확인한다. 자체 Gateway를 같은 포트에서 재시작해 자기 viewer를 disabled로 바꾸고 warm Platform의 상세·목록·증거502와 권한 복구200을 확인한다. 실제 운영 인증의 hot reload를 시험한 결과로 확대하지 않는다.

격리 시험의 기본 보존 설정은1초/4096bytes이며 `--retention-age-seconds`(0.1..604800)/`--retention-max-bytes`(1..5GiB)로 고른다. 실제 운영 설정은 바꾸지 않는다. 관리4파일에 request hold·run hold·legacy route scope run과 연결 receipt를 배치하고, route2/receipt2/관측120 합성 기록의 수를 분리한다. report-only에서는4파일 hash가 같고 enforce에서는 각 파일의 삭제·hold·durability와 최신 삭제이력을 확인한다. 크기 기준 후보는 별도의7일/선택byte report-only로 나눠 시간 기준과 구별한다. Gateway collector와 Platform API의 삭제 수·시각·한도·mode·hold/errors도 대조한다. 짧은 시험 시각을 앞으로 옮긴 결과와 운영 wall-clock timer의 실제 경과를 구분한다.

hold 검사는 보고서의 held_units만 사용하지 않는다. 실제4파일의 전후 행을 읽어 알려진 request/run hold 집합의 정확한 row multiset·잔존수, 각 파일 전후 줄 차와 reported removed_lines를 비교한다. Hubble/Tetragon hold 관측은 각각1건씩 실제 남아야 한다.

`--backend mock`은 도구 회귀용이다. 모든 출력의 `gpu_execution_verified=false`, `security_verdict=null`을 유지한다. 정상 HTTP 완료만으로 실제 엔진 종결·GPU 회수를 판정하지 않는다. 입력 source의 판본·image/model/policy/Pod/container 세대는 기존 AWS 증거와 함께 대조해야 하며 도구가 원격 소스 동일성을 자동 인증하지 않는다.

종료코드: 0=실행한 조건에 실패 없음(UNKNOWN 포함), 1=조건 불일치, 2=실행 중단/설정 오류. JSON의 각 case와 UNKNOWN을 확인한다. 출력 파일은 새 경로로만 생성한다. 실행 오류는 종류만 남기고 메시지·응답 본문·비밀값을 출력하지 않는다.

## 실제 사본의 최소화·OS 조회 검사

`copies`는 현재 배포에서 명시한 일반 파일만 최대16개·각8MiB까지 완전 읽고, 합성표식/자격값의 존재 수와 hash·owner/group/mode를 저장한다. 디렉터리를 순회하거나 symlink를 따라가지 않는다. 초과·변경 중·읽기 불가 파일의 부재 판정은 UNKNOWN이다. 원문·표식값·자격값은 파일에 저장하지 않고 환경변수 이름으로만 입력한다.

```sh
/path/to/validated/python runtime/tools/selected_runtime_check.py copies \
  --file /EXPLICIT/run/gateway_records.jsonl --file /EXPLICIT/run/engine-log.txt \
  --marker-env SYNTHETIC_PROMPT_MARKER --credential-env RUNTIME_INFERENCE_TOKEN \
  --allowed-reader ubuntu --denied-reader nobody \
  --volume-evidence /PUBLIC/aws-describe-volumes.json \
  --output /NEW/evidence/selected-copies.json
```

OS reader 검사는 이미 존재하는 계정을 `sudo -n`으로 실행할 권한이 있는 POSIX host에서만 수행한다. 별도 종료코드로 실제 읽기 거부와 sudo 실패를 구분하며 계정·권한을 생성하거나 변경하지 않는다. 계정을 지정하지 않으면 파일 표식 검사만 수행한다. 공개 EBS `Volumes` 설정은 hash·volume ID·Encrypted·key 참조 hash로 기록하며 실제 파일→mount→volume 연결은 기존 배포 증거와 대조한다. 이 도구는 그 연결이나 모든 활성 사본의 목록 완성을 자동 판정하지 않는다. 실제 사용하는 feed·정규화·native·반출·기본 엔진 로그만 caller가 선정하고 미사용 sink/backup은 기존 배포 근거에 적는다.

scan 전후와 두 reader 검사 후의 열린 descriptor/현재 path를 dev·inode·size·mtime·ctime·mode·uid/gid로 대조한다. 교체·권한 변경·삭제가 있으면 snapshot 부재 검사를 현재 path/reader 허용 판정에 재사용하지 않고 UNKNOWN을 남긴다. 원 snapshot에서 이미 발견한 금지값은 독립 발견으로 보존한다.

## 모델 허용 종료의 전후 대조

`lifecycle`은 배포 담당자가 기존 절차로 승인 모델/정책을 변경하기 전후에 각각 실행한다. 도구는 배포·정책·인증을 바꾸지 않는다. before는 old 정상/new MODEL_NOT_ALLOWED와 old 정상 후속, after는 new 정상/old MODEL_NOT_ALLOWED와 new 정상 후속을 기록한다. 각 명령은3 POST이며 동일 검증 주체·policy/target/run/generation을 phase 안에서 대조한다.

```sh
/path/to/validated/python runtime/tools/selected_runtime_check.py lifecycle \
  --phase before --gateway-url http://127.0.0.1:18080 \
  --model OLD_APPROVED_NAME --replacement-model NEW_APPROVED_NAME \
  --backend-config /PUBLIC/backend-before.json \
  --inference-token-env RUNTIME_INFERENCE_TOKEN --viewer-token-env RUNTIME_VIEWER_TOKEN \
  --output /NEW/evidence/model-before.json
# 담당자의 기존 승인·배포 절차로 모델/정책 변경 후 같은 old/new 명칭 사용
/path/to/validated/python runtime/tools/selected_runtime_check.py lifecycle \
  --phase after --gateway-url http://127.0.0.1:18080 \
  --model OLD_APPROVED_NAME --replacement-model NEW_APPROVED_NAME \
  --backend-config /PUBLIC/backend-after.json --before-report /NEW/evidence/model-before.json \
  --inference-token-env RUNTIME_INFERENCE_TOKEN --viewer-token-env RUNTIME_VIEWER_TOKEN \
  --output /NEW/evidence/model-after.json
```

after는 완전한 before의5개 필수 사례와 UUID/정상·거부 원장, 동일 actor·두 selector·달라진 정책 hash를 확인한다. 이전 증거가 없거나 부분이면 새 요청 전에 미실행으로 끝낸다. 일반 권한거부·철회·만료를 MODEL_NOT_ALLOWED로 대체하지 않는다. supplied model_map과 실제 응답 model 이름의 hash를 비교할 수 있으며, 현재 Gateway가 그 config를 실제로 사용했는지는 기존 Pod spec/승인 manifest·엔진 로그로 결합한다. 이 명령의 통과 범위는 모델 selector의 허용 종료다. 같은 weights의 명칭 변경과 다른 weights 교체를 구분하고 후자는 별도 승인·자산 증거로 확인한다.

## 수집 중단·복구의 viewer 관측

수집기 중단·무해 사건·복구는 기존 담당 actor가 수행한다. 각 단계에서 다음 GET 전용 명령을 호출해 상태·손실 수·원인/시각·gap 참조와 Platform 전파를 저장한다. 최대30초/30snapshot이며 source가 API 조회 사이에 변하면 그 sample은 UNKNOWN이다. 같은 runtime 세션/정책/target/run/instance의 안정된 source를 API와 비교한다. API contract 오류와 기한 내 반복된 안정된 불일치는 실패로 남긴다.

```sh
/path/to/validated/python runtime/tools/selected_runtime_check.py observe \
  --gateway-url http://127.0.0.1:18080 --platform-url http://127.0.0.1:18700 \
  --viewer-token-env RUNTIME_VIEWER_TOKEN --source tetragon --expected-state error \
  --wait-seconds 5 --output /NEW/evidence/collector-gap.json
```

baseline/error/recovered 단계는 새 출력 파일로 각각 보존한다. source별 total/returned/limit/truncated, 고정37개 정확ID·PACK33도 GET으로 수집한다. events와 checks의 독립 query snapshot을 하나로 합치지 않는다. 수집기·추론 요청을 이 명령이 변경하지 않으며 과거 gap 사건의 backfill 인과나 화면 렌더를 자동 주장하지 않는다. actor의 독립 사건/원feed와 실제 화면 확인을 같은 단계 자료에 연결한다. 민감한 오류 원문/gap ID는 저장하지 않고 canonical hash·알려진 오류 종류·UTC시각·수·gap hash로 대조한다.
