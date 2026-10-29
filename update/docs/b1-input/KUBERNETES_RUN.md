# Online Kubernetes 실행 인계

REQ-07·REQ-08·SUP-08의 B 전용 orchestration 소스다. 현재 A Online HTTP 프로필·native payload 검증·session seal 지원이 없어 `--execute`도 요청 전에 닫힌다. 이 상태를 세 ABORTED EVD/RES와 RPT, 검증 가능한 export/restore로 남긴다. 실제 Kubernetes 완료는 운영자 실행 뒤 확정한다. CPU16 결과와 EXAMPLE 회귀는 별도다.

## 실행 입력과 명령

Python 3.12 정상 모드, 설치된 kubectl, 승인된 Linux/원격 실행 호스트를 사용한다. 현재 Mac에는 kubectl이 없고 Docker daemon도 가동되지 않았다. Mac 우회·daemon 기동·새 서버 생성은 이 도구 범위에 없다. 도구는 checkout의 `tools`에 있으며 wheel 정식 executor/registry 등록은 A 후속 작업이다.

```sh
PYTHONPATH=src:. .venv/bin/python -m tools.b_online_kubernetes_run prepare-pack --output /owned/new-online-pack
PYTHONPATH=src:. .venv/bin/python -m tools.b_online_kubernetes_run run \
  --env /owned/ENV.json --policy /owned/POL.json --pack /owned/new-online-pack \
  --target /owned/http-target.json --prepared /owned/sensor/manifest.json \
  --case-manifest /owned/approved-cases.json --output /owned/new-run
# 동일 입력의 승인된 실제 실행에만 --execute 추가. 현재 A capability gate에서 닫힘.
```

POL은 출력된 exact PACK Ref와 세 check를 포함해야 한다. Pack은 외부 DRAFT이며 실행기는 `online_input_kubernetes`, judge는 기존 `online_input_cpu.judge_params()`의 fail 규칙만 사용한다. CPU fixture/project/source eligibility는 호출하지 않는다. 기존 Pack31·REQ10 numeric/schema-tools는 변경하지 않는다.

`http-target.json`은 현재 공개 `guard-kubernetes-request-binding-target/1` 입력이다. 기존 `KubernetesHTTP` 생성자가 exact ENV Ref, repository/revision→API alias, 직접 IPv4 Pod URL, 소유 run label, engine/probe namespace·Pod UID/containerID/imageID/restart/start를 검증한다. lifecycle target은 같은 engine binding에서 만들어 기존 검증을 통과해야 한다. 현재 lifecycle `lab=true` 조건도 유지한다.

case manifest는 B 도구 로컬 입력이며 공통 교환 계약을 추가하지 않는다. 키는 다음과 같다.

- `schema`: `byul-online-kubernetes-run-input/1`
- `environment_ref`, `target_sha256`: seal된 ENV exact Ref와 target canonical SHA256
- `session`, `run_id`: 검증된 prepared session과 target run ID
- `owner`, `recovery_owner`: 운영·복구 담당 식별자. 이 선언의 해시는 승인 서명이나 실제 cleanup 증명이 아니다.
- `isolated_run: true`: 공유 GPU OOV 발송을 허용하지 않는다. 실제 격리·승인은 실행 운영자가 확인한다.
- `domain_config_sha256`: 실제 native `effective_config`와 독립 token domain canonical hash를 묶은 아래 값. CPU32/16 값 이월 금지.
- `cases`: 1~16개 승인된 subset. 각 항목은 `id`, `check_id`, `body`, `after_body`만 가진다. 모든 CPU16 사례를 강제하지 않는다. 각 body는 정확한 API model, messages, `max_tokens: 1`, JSON 비스트리밍 요청이다. 허용 추가 필드는 reuse/echo/template/MM 입력으로 한정한다. 예상 거부·verified 플래그·외부 관측은 입력받지 않는다.

domain/config 값은 `SHA256(canonical_json({effective_config: 실제값, token_id_domain_sha256: 실제 독립 vocabulary ID-set hash}))`다. 실제 모델/config·tokenizer domain에 맞춰 운영자가 준비하며 B native join에서 실제 값과 비교한다. cached max-token 값을 허용 oracle로 쓰지 않는다.

활성 trust/template/MM 설정에서 가능한 정상·위반·지원 밖 사례를 운영자가 선택한다. 실제 모델의 OOV/negative/길이 사례는 격리 승인 없이 발송하지 않는다. 요청은 최대16 primary+16 after이며 sensor의 기존 MAX_ROWS512/MAX_BYTES262144 범위를 해제하지 않는다. 사례별 실제 hook 수에 따라 더 작은 subset이 필요할 수 있다. 실제 잘림·누락은 gap으로 남기고, 먼저 검증된 독립 위반은 보존한다.

## production 본체와 A 최소 변경 요청

B `_ProductionSession`은 기존 public API를 연결한다.

1. `KubernetesHTTP(...profile=ONLINE_INPUT_PROFILE)`과 `KubernetesLifecycleReader(...)` 생성.
2. reader `snapshot()`으로 선정 engine 세대를 확인한다.
3. 공개 transport `__call__('kubernetes-probe', [request], timeout=30)`로 사례 primary와 after를 보낸다. 각 호출의 기존 전후 Pod 검증을 유지한다. body/response는 transient이며 저장하지 않는다.
4. reader의 **추가 제안** `seal_session() -> {'sealed': bool}`을 호출하고, seal 실패 때도 기존 `lifecycle_session.capture_session(reader, target, container=..., image=..., prepared=..., owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)`로 수집을 시도한다. 실제 파일은 한 run에서 한 번 수집하며 세 EVD가 공유한다.
5. B가 parser 검증된 recordings를 요청별로 join한 뒤 판정용 정제 관측을 만든다. reader 최종 snapshot·session census·recording 종료/잘림을 완결성과 별도로 확인한다.
6. `finally`에서 소유 session seal을 재시도하고 transport cleanup report를 확인한다. 고정 HTTP probe watchdog은 자기 실행 프로세스만 끝낸다. Pod 삭제·GPU kill·클러스터 변경은 수행하지 않는다. cleanup 미확인은 EVD와 summary에 남고 복구 담당자에게 인계한다.

필요한 A 변경은 다음 세 지점이다. 전용 새 전체 session collector API는 요구하지 않는다.

- `runtime/kubernetes_http.py`: 명시적 `ONLINE_INPUT_PROFILE`, `online-<case>-primary/after` ID, `/v1/chat/completions` JSON과 허용 reuse/echo/template/MM 필드·X-Request-Id. 기존 두 프로필의 제한·source/digest/ENV/model mapping gate 유지. 별도 private `_call`이나 REQ11 ID 우회는 없다.
- native sensor/profiles 및 `lifecycle_capture.parse_recording`: source/module/code hash가 검증된 hook에서 `metadata.online_input` namespace의 B `guard.adapters.vllm_online_input.snapshot_online_input` 전체 반환값을 검증·기록. 함수/phase는 B callback의 닫힌 POINTS를 따른다. callback 파일 bytes/code provenance도 승인 소스에 포함한다. ASGI/native request, call, session, PID/start binding은 A가 실제 문맥에서 공급한다. payload namespace와 아래 binding 해시 규칙은 **A 미확정 제안**이며 현재 동작 계약으로 간주하지 않는다.
- `KubernetesLifecycleReader.seal_session() -> {'sealed': bool}`: exact selected session만 고정 프로그램으로 seal하고 세대 전후를 검증. 임의 exec/경로/명령 입력을 받지 않는다. 이 공개 지원이 생길 때까지 B가 임의 kubectl 쓰기로 대체하지 않는다.

`A_SOURCE_PINS`의 B callback bytes는 고정하고 미확정 A source는 None으로 닫힌다. 승인된 HTTP/reader/parser/session/native callback/sensor/profiles exact bytes가 확정되면 root가 literal hash를 연결하고 동일 source로 검증해야 한다. 런타임 파일해시나 manifest의 `verified=true`로 승인값을 생성하지 않는다.

## native 관측과 판정 경계

`_join_native`는 현재 run의 `capture_session` 반환만 받는다. 외부 native JSON ingest 경로는 없다. session owner·file identity·process context 및 parser의 module/source/code 검증을 통과한 기록에서 같은 ASGI/PID/start 그룹을 만든다. `InputProcessor.process_inputs` CALL/RETURN은 native RID와 call hash가 같아야 하며, callback이 독립 해시한 실제 RID도 binding과 일치해야 한다. render CALL의 실제 parsed field별 hash는 발송한 field와 비교한다. request/body hash와 ID 문자열 hash는 각각 callback ASCII canonical JSON과 raw UTF-8 규칙을 구분한다.

REQ08은 실제 input/admitted hash가 같은 상태에서 독립 tokenizer ID-set/model membership 위반을 확인한다. REQ07과 SUP08은 source-qualified gate의 실제 보호필드·trust·default-deny 조건과 admission을 연결한다. REQ07의 실제 decoder 길이 경계도 확인한다. 해당 gate 귀속을 확보하지 못하면 UNKNOWN이며, template 최종 효과와 MM 최종 소비는 별도 관측이다. parsed presence·HTTP200/400·빈 trace로 native 소비를 추정하지 않는다. 단순 거부 응답에 source별 거부 성공을 부여하지 않는다. source 미지원·binding 누락·domain/config 불일치도 미수집이다.

callback은 안전 snapshot이며 native producer 인증을 자체 제공하지 않는다. Kubernetes API 소유 확인과 소스 계측으로 확인한 범위는 DECLARED로 유지한다. 실제 serving model weights/GPU worker 소비 및 runtime image ancestry는 별도 지원 범위다.

## 저장·복구와 완료 조건

각 EVD는 같은 실제 수집 시각/collection ID를 사용하고 자기 artifact를 갖는다. 정제 artifact에는 enum·bool·count·hash만 남기며 요청본문·토큰 배열·응답·임의 예외본문·raw Pod 객체를 저장하지 않는다. 이후 사례/after/seal/첨부/cleanup 실패가 발생해도 이미 요청·source·process에 귀속된 위반은 ABORTED EVD의 독립 FAIL로 남긴다.

출력 `store/`, `summary.json`, `evidence.zip`, `restored/`에는 exact ENV/POL/PACK/PLAN/EVD/RES/RPT Ref와 결과가 보존된다. 기존 core `select/run/judge/report`, Store 검증, exactRef 재판정, bundle export/restore 및 복원 Store의 같은 Ref 재판정을 수행한다. 복원 실패도 원 Store/EVD를 삭제하지 않는다. 기존 출력은 덮어쓰지 않는다.

로컬 EXAMPLE 회귀 통과 후에도 A 지원·운영자 승인 입력·실제 remote 실행·native 귀속·Store/복구·owned cleanup이 모두 확인되어야 실제 묶음을 마감한다. 현 단계는 B orchestration 준비이며 실제 Kubernetes 실수집은 대기다.

## 집중검토 보수 계약

이미지 digest 검증이 없는 진단 recording은 자격 관측으로 승격하지 않는다. 각 native point는 고정 `ONLINE_POINT_CONTRACTS`의 module/source/code hash, prepared 선정 source hash, 실제 MODULE 기록을 모두 비교한다. callback bytes/code provenance도 같은 capture에 필요하다. 새 `capture.online_callback_provenance={module,bytes_sha256,code_sha256}`는 A 추가 지원 제안이다. 현재 `ONLINE_POINT_CONTRACTS`와 `ONLINE_CALLBACK_CODE_SHA256`는 None이며 A가 검증된 출력과 literal contract를 제공하기 전 닫힌다. 기존 Kubernetes image_check=False를 완화하지 않는다.

REQ07/SUP08은 없는 template_consumed/MM최종소비 플래그를 요구하지 않는다. 실제 renderer CALL/RETURN 및 parsed 보호필드 보존, source-qualified gate CALL/RETURN의 trust=false와 source의 default-deny 조건을 admission에 연결해 gate 우회를 판정한다. template은 직접값이 None이 아니거나 nested chat_template이 None이 아닌 경우, MM은 kwargs가 비어 있지 않은 경우다. empty MM kwargs는 거부 대상에 넣지 않는다. 이는 해당 gate의 우회이며 template 최종 렌더 효과·MM 코드 최종 실행은 별도 관측이다.

REQ08 정상 ID의 reuse도 실제 decoder admission이 model max보다 길면 위반이다. REQ07 역시 같은 경계를 사용한다. 길이가 같은 경우는 source input_processor의 실제 runner_type=generate 조건일 때만 위반이며 pooling/미확인을 generate로 추정하지 않는다. 실제 validation CALL의 prompt_type=decoder와 입력 길이/해시·config가 일치해야 한다. encoder/MM 길이 경계는 확장하지 않는다.

After는 보호필드 없는 독립 text 요청으로 제한한다. 그 요청의 renderer parsed defaults, 실제 InputProcessor CALL/RETURN, config와 독립 input/admitted domain membership=True, 비어 있지 않은 integer ID와 동일 admission hash를 확인한다. OOV32가 반환된 after는 정상으로 인정하지 않으며 primary의 이미 확인된 위반은 보존한다.

messages는 role(system/user/assistant)와 문자열 content만 허용하고 remote URL/data URI·image/audio/video/file 항목을 거부한다. primary와 after 각각 canonical body 128KiB 상한을 적용한다. 이는 선정된 안전 text probe의 기술 범위다.
