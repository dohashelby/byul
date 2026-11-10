# 선정 A 인수 실행 묶음

`tools/selected_acceptance_run.py`는 승인된 `selected_runtime_check.py`의 일곱 모드를 순서별 checkpoint로 재사용한다. root가 현재 환경을 기동하고 각 단계의 공개 config를 제공한다. 실행기는 loopback GET, 선정 helper, 명시 파일 읽기와 자기 child process만 사용한다. 모델/정책 변경과 수집기 actor는 두 명령 사이에서 기존 담당자가 수행한다.

## 기존 준비 자료의 재사용

workspace `outputs/runtime-development-be07-20261106/` 아래에서 확인한 자료다. 아래 경로의 원본은 보존한다.

| 자료 | 사용할 부분 |
|---|---|
| `evidence/aws-resume-20261106/scripts/01-host.sh`, `02-cluster.sh`, `03-download-assets.sh` | root의 fresh Ubuntu 준비, 재부팅, 클러스터, 기존 digest/model 다운로드 순서 |
| 같은 디렉터리 `images.lock`, `profile.sh` | 기존 단일 L4 dev 프로필과 두 엔진 고정 digest |
| `evidence/aws-ready/execution-manifest.json` | Qwen/Qwen2.5-0.5B-Instruct revision `7ae557604adf67be50417f59c2c2f167def9a775`, inventory `b3c4615e85827a75e4e271b1bf325bd640c4627bc1e0d18d67223e319aced59e` |
| `evidence/dev-d/PREPARATION-v3-validated.json` | bundle/backend/asset 및 환경변수 참조 구조. 만료된 기존 승인 시각과 옛 세대는 이력으로 유지 |
| `evidence/aws-ready/deploy_runtime_session.py`, `rollout_gateway_e4.py` | root의 기존 렌더러·run/observation-run 결속과 정확 Pod/container 대조 절차 |
| `evidence/dev-a/A4_actual/public-preparation/platform_a4_server.py` | loopback Platform, 별도 data/reports, 메모리 viewer와 공개 import/web 해시 receipt |
| 같은 디렉터리 `pin_engine_forward.py`, `run_isolated_a4.py` | 정확 Pod forward와 UID/CID/startedAt 전후, 별도 backend 주소/owned stack |
| `evidence/aws-ready/export_public_b.py`, `public-B-20261106/ALLOWLIST.json` | 명시 파일·hash·환경변수 값 검사 후 public allowlist 반출 방식 |

옛 script의 host/Pod/소스 경로/포트 PID/만료 시각은 새 실행 입력으로 옮기지 않는다. 새 host와 최종 고정 소스·기존 image/model에 대해 root가 새 receipt와 config를 만든다. bootstrap·AWS CLI·kubectl apply·engine rollout을 이 실행기가 호출하는 경로는 없다.

## root가 준비할 정확한 입력

두 엔진은 각각 별도 `session`, 새 출력 경로, 해당 엔진의 기대 세대를 사용한다.

1. 최종 소스 절대 경로와 full40 SHA. 반입 archive에는 `SOURCE_SHA`를 보존한다. 로컬 worktree는 읽을 수 있는 Git HEAD를 사용한다. 소스의 API/web build를 함께 제공한다.
2. 검증된 Python과 `PYTHONPATH=runtime:runtime/src:update/src:platform/api/src`. helper를 실행할 Python이 현재 실행기와 같아야 한다. `prepare`가13개 모듈과 실행기 자신의 실제 `__file__` 및 파일 hash를 확인한다.
3. 이미 기동된 Gateway/Platform의 명시 loopback HTTP origin. viewer/inference 값은 서버 process 환경변수에만 로드한다. config에는 **환경변수 이름**만 넣는다.
4. root의 새 Platform process receipt. 정확 `source_sha`, `runtime_base_url`, `host=127.0.0.1`, `port`, `imports`의 `byul_platform.main`/`byul_platform.sources.runtime` 각각 `{file,sha256}`, `web_dist_sha256`가 아래 source pins와 일치해야 한다. 별도 Platform data/reports dir을 사용한다.
5. 엔진의 `{kind,image_digest,model_revision,inventory_sha256}`와 단계별 expected scope. scope는 아래9키를 모두 가지며 run·Pod UID·container ID·image/policy hash는 실측값이어야 한다. `target_instance_id`는 설정이 명시 미지정인 경우에만 null을 쓴다.
6. 해당 단계의 `options`, 외부 입력 file SHA256, 필요 시 root가 이미 공개 검토한 witness refs. 입력은 유한 일반 파일이며 helper가 쓰는 data-dir 외에는 읽기용이다. 검증한 입력 byte를 새0700 private 디렉터리의0400 snapshot으로 고정해 child에 전달한다. 원경로의 실행 중 교체는 소비 byte를 바꾸지 않는다.

```sh
cd /ABS/final-frozen-source
export PYTHONPATH=runtime:runtime/src:update/src:platform/api/src
$PY runtime/tools/selected_acceptance_run.py pins \
  --source /ABS/final-frozen-source --output /NEW/public/source-pins.json
```

config의 `source`에는 이 JSON 객체를 그대로 넣는다. 실행기는 각 파일과 build asset을 재계산하고 수입 경로를 확인한다. source marker만으로 원격 image/source 동일성을 판정하지 않으므로 root가 기존 image/source receipt를 같이 보존한다.

다음은 config의 형식이다. `ACTUAL_*`와 source 객체는 root의 새 공개 근거에서 채운다. 비밀값·비밀파일 경로는 config에 넣지 않는다.

```json
{
  "schema": "byul-selected-acceptance-run/1",
  "session": "vllm-A-SESSION",
  "source": {"path":"/ABS/final-frozen-source","sha":"FULL40","files":{},"web":{}},
  "engine": {
    "kind":"vllm", "image_digest":"sha256:ACTUAL64",
    "model_revision":"7ae557604adf67be50417f59c2c2f167def9a775",
    "inventory_sha256":"b3c4615e85827a75e4e271b1bf325bd640c4627bc1e0d18d67223e319aced59e"
  },
  "gateway_url":"http://127.0.0.1:18080", "platform_url":"http://127.0.0.1:18700",
  "platform_receipt":{"path":"/PUBLIC/platform-process.json","sha256":"ACTUAL64"},
  "inference_token_env":"RUNTIME_INFERENCE_TOKEN", "viewer_token_env":"RUNTIME_VIEWER_TOKEN",
  "model":"test-model", "stage":"requests",
  "expected_scope": {
    "backend_kind":"vllm", "policy_sha256":"ACTUAL64", "target_id":"ACTUAL_TARGET",
    "run_id":"ACTUAL_RUN", "target_instance_id":"ACTUAL_INSTANCE",
    "pod_uid":"ACTUAL_UID", "container_id":"containerd://ACTUAL_CID",
    "restart_count":0, "image_digest":"sha256:ACTUAL64"
  },
  "timeout_seconds":15, "stage_deadline_seconds":180,
  "options":{}, "input_sha256":{}, "witnesses":[]
}
```

## 일곱 묶음과 실행 순서

모든 단계는 같은 명령 형식이다. `prepare`는 파일·수입 검증과 argv 저장만 수행하고 네트워크 요청을 보내지 않는다. `run`은 새 디렉터리를 만들고 protected viewer와 정확 scope를 확인한 뒤 helper를 한 번 호출한다. 각 디렉터리의 부모는 미리 존재해야 한다.

```sh
$PY runtime/tools/selected_acceptance_run.py prepare \
  --config /PUBLIC/STAGE.json --output-dir /NEW/prepared-STAGE
# root가 해당 POST 창 또는 actor 단계 확인 후 실행
$PY runtime/tools/selected_acceptance_run.py run \
  --config /PUBLIC/STAGE.json --output-dir /NEW/actual-STAGE
```

| 묶음 | stage와 options | 실인수 연결 |
|---|---|---|
| A1 기본 | `live`, `{"forbidden_keys":["private_debug","prompt_token_ids"]}` | UTF8 이후 정상·2연결 UUID·민감 metadata 관측·roles/window. 기존 AWS 정상/안전 오류와 연결 |
| 요청 권한 | `requests`, `{}` | 정상 전후·auth3종·위조header/body·field/route. 각 UUID의 원장/API와 실제 backend 수신/미도달 witness 연결 |
| 응답 분리 | `separation`, `{}` | SSE즉시/지연·buffered A close→B6 POST. 생성 표식·원장·별도 UUID. 기존 동시 REQ011 실측도 보존 |
| 모델 전후 | `model-before`/`model-after`, `replacement_model`, 선택 `backend_config`, after의 `before_report` | root의 실제 모델 selector 허용 종료 사이 각3 POST. 기존 manifest/modelmap·실엔진로그와 연결. 같은 weights alias 전환은 그 범위로 표시 |
| 관측 | `observe-baseline`/`observe-gap`/`observe-recovered`, `source`, `expected_state` | 기존 actor의 source 중단·무해 사건·복구 전후 GET. raw/API/raw·gap/drop/storage·window/37ID. 각 source별 새 output |
| 보존/캐시 | `isolated`, `test_data_dir`,`backend_config`,`engine_forward_receipt` | NEW owned dir, 같은 실엔진2 control,4파일 hold/삭제·report-only·크기·history·freshdetail/권한복구. 합성198history/120관측 등은 별도 표기 |
| 사본/OS | `copies`, `files`,`marker_envs`,`credential_envs`,`allowed_reader`,`denied_reader`,`volume_evidence` | 명시16파일/각8MiB, 기존 계정 실제 읽기 허용/거부. root의 active-copy owner/policy/mount→volume 근거와 연결 |

`backend_config`, `before_report`, `engine_forward_receipt`, `volume_evidence`를 options에 넣으면 각각 해당 이름의 SHA256을 `input_sha256`에 넣는다. root가 만든 파일 byte의 SHA256이다. 각 파일을 한 번 읽은 검증 byte로 private snapshot을 만들고 그 경로를 argv에 전달한다. 실행 전후 snapshot hash를 다시 대조한다. Platform/forward/witness도 검증한 동일 snapshot byte에서 해석한다. copies의 검사대상 파일 자체는 scan의 generation/metadata 검사를 사용한다.

isolated의 backend는 정확 Pod forward origin을 사용한다. `engine_forward_receipt`는 기존 A4 형식의 `argv`, `bind`, `remote_port`, `ready`, `same_pod_generation`, `pod_before`, `pod_after`를 가진다. 두 snapshot 전체가 같고 started_at이 있으며 UID/CID/restart가 expected scope와 같아야 한다. `argv`의 마지막은 `pod/정확이름`, `LOCAL:REMOTE`, `--address=127.0.0.1`이고 backend origin의 포트가 일치해야 한다. root가 직접 forward를 만들고 실제 kubeconfig/context/Pod UID/CID/startedAt를 기록한다.

실행 전후 same scope가 아니거나 상태/권한/파일·API contract가 부족하면 UNKNOWN 또는 미실행으로 남는다. 실제 helper의 failed는 전후 확인 실패와 함께 있어도 유지한다. 모델의 non-echo나 첫 protocol byte만 본 close를 생성 귀속 PASS로 올리지 않는다.

## 요청별 외부 증거 연결

각 `run.json`은 helper case의 원결과와 UUID 집합, helper index·hash를 보존한다. 원장/API 상세는 안전한 `helper.json`에 있고 같은 request ID로 찾는다. backend 수신 자격·엔진 로그 미도달·actor 사건/사본·실제 UI 근거는 root가 기존 도구에서 명시 public 자료로 준비한 후 아래처럼 연결한다.

```json
{"witnesses":[
  {"path":"/PUBLIC/engine-log-request-proof.json","sha256":"ACTUAL64","role":"engine_log"},
  {"path":"/PUBLIC/backend-receive-proof.json","sha256":"ACTUAL64","role":"backend_receiving"}
]}
```

각 witness는 원자료를 공개 반출하지 않고 기존 판정기가 만든 JSON과 해시를 사용한다. 구조 안에 `request_id` 또는 `request_ids`로 실제 UUID가 있어야 case와 연결된다. 단순 Pod/deployment 자료는 request ID가 없어도 stage의 plan witness 참조로 보존할 수 있으며 요청별 귀속은 별도로 남는다.

```sh
$PY runtime/tools/selected_acceptance_run.py link \
  --run-dir /NEW/actual-requests --witnesses /PUBLIC/request-witness-refs.json \
  --output /NEW/public/requests-links.json
```

link는 저장된 plan/helper hash를 확인하고 정확 UUID 교집합만 연결한다. 다른 요청 UUID는 unmatched 목록으로 남긴다. witness의 내용 판정과 실제 원자료 대조는 root/R2가 맡고 `witness_claims_independently_verified=false`를 유지한다. 기존 case의 실패/UNKNOWN은 이 참조를 추가해 바꾸지 않는다.

## 결과와 반출

각 단계의 `ALLOWLIST.json`에 있는 `plan.json`, `run.json`, 안전 검사를 끝낸 `helper.json`만 명시 반출한다. 링크 보고서는 별도 명시 파일이다. test data-dir·raw logs·인증파일·브라우저 profile을 재귀 반출하지 않는다. 값은 process env에서 검사하고 argv/보고서에는 env 이름만 남는다. helper의 첫 출력은 private에 두며, JSON을 해석한 모든 string/key에서 raw·JSON escape·UTF8 hex·base64/URL-safe base64 변형을 검사한 뒤에만 public helper.json을 쓴다. 검출은 failed로 남기고 자신의 private helper 출력은 제거한다. private 입력 snapshot과 helper 임시 출력은 allowlist에 포함하지 않는다.

`scope_result=passed`는 실행한 helper 사례와 그 단계의 환경 결속 범위다. `security_verdict=null`, `gpu_execution_verified=false`를 유지하며 전체37 판정을 만들지 않는다. 종료코드0은 prepared 또는 completed/UNKNOWN을 포함하므로 각 case를 확인한다.1은 실제 failed,2는 미실행/중단/입력 오류다. 작업별1..60초 외에 stage 전체1..900초 기한을 적용하며 초과하면 자신이 시작한 child group만 종료한다. 재시도와 정상 출력으로 실패 덮어쓰기는 없다.

로컬 회귀는 config/파일/import·OS child/판정 경계를 시험한다. 실제 root AWS 결과는 별도 새 자료로 보존한다.
