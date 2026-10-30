# Online Kubernetes 실행 인계

REQ-07·REQ-08·SUP-08의 B 전용 orchestration 소스다. A108의 공개 Online HTTP 프로필·`/9` native parser·owned session seal 계약을 소비한다. 이 B 브랜치에는 A108 공통 소스가 아직 통합되지 않아 exact source gate에서 요청 전에 닫힌다. 미수집 상태를 세 ABORTED EVD/RES와 RPT, 검증 가능한 export/restore로 남긴다. 실제 Kubernetes 완료는 운영자 실행 뒤 확정한다. CPU16 결과와 EXAMPLE 회귀는 별도다.

## 실행 입력과 명령

실행 호스트는 Python 3.12 정상 모드와 kubectl을 사용한다. native `/9` controller의 승인된 serving 런타임은 CPython 3.12.3·3.12.14다. 로컬 도구 회귀 환경 3.12.13을 serving 설치 승인으로 이월하지 않는다. 현재 Mac에는 kubectl이 없고 Docker daemon도 가동되지 않았다. Mac 우회·daemon 기동·새 서버 생성은 이 도구 범위에 없다. 도구는 checkout의 `tools`에 있으며 wheel 정식 executor/registry 등록은 A 후속 작업이다.

```sh
PYTHONPATH=src:. .venv/bin/python -m tools.b_online_kubernetes_run prepare-pack --output /owned/new-online-pack
PYTHONPATH=src:. .venv/bin/python -m tools.b_online_kubernetes_run run \
  --env /owned/ENV.json --policy /owned/POL.json --pack /owned/new-online-pack \
  --target /owned/http-target.json --prepared /owned/sensor/manifest.json \
  --case-manifest /owned/approved-cases.json --output /owned/new-run
# 동일 입력의 승인된 실제 실행에만 --execute 추가. 현재 checkout은 A108 source gate에서 닫힘.
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

활성 trust/template/MM 설정에서 가능한 정상·위반·지원 밖 사례를 운영자가 선택한다. 실제 모델의 OOV/negative/길이 사례는 격리 승인 없이 발송하지 않는다. 요청은 최대16 primary+16 after다. 명시적 `/9`의 writer/parser·owned file reader는 2048행/2MiB를 지원하고, 기본 `/8`의 512행/256KiB는 유지한다. 현재 A108 `SESSION_SEALER` census는 파일당 256KiB까지 허용하므로 256KiB 초과 `/9` 파일의 seal은 실패·gap으로 남는다. 사례별 실제 hook 수에 따라 더 작은 subset이 필요할 수 있다. 실제 잘림·누락은 gap으로 남기고, 먼저 검증된 독립 위반은 보존한다.

## production 본체와 A108 소비 계약

B `_ProductionSession`은 기존 public API를 연결한다.

1. `KubernetesHTTP(...profile=ONLINE_INPUT_PROFILE)`과 `KubernetesLifecycleReader(...)` 생성.
2. reader `snapshot()`으로 선정 engine 세대를 확인한다.
3. 공개 transport `__call__('kubernetes-probe', [request], timeout=30)`로 사례 primary와 after를 보낸다. 각 호출의 기존 전후 Pod 검증을 유지한다. body/response는 transient이며 저장하지 않는다.
4. reader의 공개 `seal_session() -> {'sealed': bool}`을 호출하고, seal 실패 때도 기존 `lifecycle_session.capture_session(reader, target, container=..., image=..., prepared=..., owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)`로 수집을 시도한다. 실제 파일은 한 run에서 한 번 수집하며 세 EVD가 공유한다.
5. B가 parser 검증된 recordings를 요청별로 join한 뒤 판정용 정제 관측을 만든다. reader 최종 snapshot·session census·recording 종료/잘림을 완결성과 별도로 확인한다.
6. `finally`에서 소유 session seal을 재시도하고 transport cleanup report를 확인한다. 고정 HTTP probe watchdog은 자기 실행 프로세스만 끝낸다. Pod 삭제·GPU kill·클러스터 변경은 수행하지 않는다. cleanup 미확인은 EVD와 summary에 남고 복구 담당자에게 인계한다.

A108 확정 source `e24dfb5653d66f9d82fc4537a995208f29ad113e`의 공개 계약을 사용한다.
참조 checkout `414a7c9`의 추가 변경은 B REQ13 adapter이며 승인 A 파일 bytes는 같다.

- `ONLINE_INPUT_PROFILE`은 `kubernetes-owned-online-input-http-v1`이다. `online-<case>-primary/after` ID와 `byul-online-<run nonce>-<case>-primary/after` X-Request-Id를 사용한다. 같은 nonce의 primary→after 직렬 순서를 유지한다. 공개 HTTP gate는 body 16KiB, messages 최대16개, 문자열당4096자, reuse ID 최대4096개와 닫힌 JSON 필드·중첩 제한을 검사한다. B의 선행 body 상한128KiB와 구분하며, 최종 전송은 A의 더 작은 한도를 따른다.
- prepared는 명시적 `vllm-native-lifecycle-metadata/9`를 사용한다. 기본 `/8`은 변경하지 않는다. parser는 callback bytes/code와 module source/코드, CALL/RETURN·RAISE의 binding을 검증한다.
- `online_code_contract`는 `python312-source-relative-code/1`이다. MODULE의 `online_code_sha256`를 callback provenance의 `code_runtime`에 해당하는 승인 module code와 비교한다. 기존 `code_sha256`는 원래 filename을 보존한 코드 해시이므로 source-relative 승인값과 별도로 유지한다. A controller는 각 hook의 승인 function code·async·module도 검사한다.
- callback provenance는 `{module, bytes_sha256, code_sha256, code_runtime}`이며 3.12.3·3.12.14 각각의 고정 literal을 사용한다. 런타임·manifest·진단값으로 승인 literal을 생성하지 않는다.
- `KubernetesLifecycleReader.seal_session()`은 선정된 session만 봉인하고 고정 프로그램의 전후 세대를 확인한다. B는 공개 API를 호출하고 실패 때에도 수집과 owned cleanup을 시도한다.

`A_SOURCE_PINS`는 이 계약에 필요한 A 파일과 B callback bytes를 고정한다. 현재 checkout의 A 공통 파일이 일치하지 않으면 요청 전 중단한다. A108이 통합된 후에도 `image_check=False`가 유지되므로 `image_digest_verified=True`를 요구하는 B native join은 진단 recording을 자격 관측으로 올리지 않는다. 이미지 증명 지원은 별도 대기다.

## native 관측과 판정 경계

`_join_native`는 현재 run의 `capture_session` 반환만 받는다. 외부 native JSON ingest 경로는 없다. session owner·file identity·process context 및 parser의 module/source/code 검증을 통과한 기록에서 같은 ASGI/PID/start 그룹을 만든다. `InputProcessor.process_inputs` CALL/RETURN은 native RID와 call hash가 같아야 하며, callback이 독립 해시한 실제 RID도 binding과 일치해야 한다. render CALL의 실제 parsed field별 hash는 발송한 field와 비교한다. request/body hash와 ID 문자열 hash는 각각 callback ASCII canonical JSON과 raw UTF-8 규칙을 구분한다.

REQ08은 실제 input/admitted hash가 같은 상태에서 독립 tokenizer ID-set/model membership 위반을 확인한다. REQ07과 SUP08은 source-qualified gate의 실제 보호필드·trust·default-deny 조건과 admission을 연결한다. REQ07의 실제 decoder 길이 경계도 확인한다. 해당 gate 귀속을 확보하지 못하면 UNKNOWN이며, template 최종 효과와 MM 최종 소비는 별도 관측이다. parsed presence·HTTP200/400·빈 trace로 native 소비를 추정하지 않는다. 단순 거부 응답에 source별 거부 성공을 부여하지 않는다. source 미지원·binding 누락·domain/config 불일치도 미수집이다.

callback은 안전 snapshot이며 native producer 인증을 자체 제공하지 않는다. Kubernetes API 소유 확인과 소스 계측으로 확인한 범위는 DECLARED로 유지한다. 실제 serving model weights/GPU worker 소비 및 runtime image ancestry는 별도 지원 범위다.

## 저장·복구와 완료 조건

각 EVD는 같은 실제 수집 시각/collection ID를 사용하고 자기 artifact를 갖는다. 정제 artifact에는 enum·bool·count·hash만 남기며 요청본문·토큰 배열·응답·임의 예외본문·raw Pod 객체를 저장하지 않는다. 이후 사례/after/seal/첨부/cleanup 실패가 발생해도 이미 요청·source·process에 귀속된 위반은 ABORTED EVD의 독립 FAIL로 남긴다.

출력 `store/`, `summary.json`, `evidence.zip`, `restored/`에는 exact ENV/POL/PACK/PLAN/EVD/RES/RPT Ref와 결과가 보존된다. 기존 core `select/run/judge/report`, Store 검증, exactRef 재판정, bundle export/restore 및 복원 Store의 같은 Ref 재판정을 수행한다. 복원 실패도 원 Store/EVD를 삭제하지 않는다. 기존 출력은 덮어쓰지 않는다.

로컬 EXAMPLE 회귀 통과 후에도 A 지원·운영자 승인 입력·실제 remote 실행·native 귀속·Store/복구·owned cleanup이 모두 확인되어야 실제 묶음을 마감한다. 현 단계는 B orchestration 준비이며 실제 Kubernetes 실수집은 대기다.

## 집중검토 보수 계약

이미지 digest 검증이 없는 진단 recording은 자격 관측으로 승격하지 않는다. 각 native point는 고정 `ONLINE_POINT_CONTRACTS`의 module/source/code hash, prepared 선정 source hash, 실제 MODULE 기록을 모두 비교한다. callback bytes/code provenance도 같은 capture에 필요하다. `capture.online_callback_provenance={module,bytes_sha256,code_sha256,code_runtime}`와 `online_code_contract`는 A108 확정 출력이다. `ONLINE_POINT_CONTRACTS`와 `ONLINE_RUNTIME_CONTRACTS`에는 해당 A source의 고정 literal을 사용한다. 기존 Kubernetes image_check=False를 완화하지 않는다.

REQ07/SUP08은 없는 template_consumed/MM최종소비 플래그를 요구하지 않는다. 실제 renderer CALL/RETURN 및 parsed 보호필드 보존, source-qualified gate CALL/RETURN의 trust=false와 source의 default-deny 조건을 admission에 연결해 gate 우회를 판정한다. template은 직접값이 None이 아니거나 nested chat_template이 None이 아닌 경우, MM은 kwargs가 비어 있지 않은 경우다. empty MM kwargs는 거부 대상에 넣지 않는다. template_gate_bypass와 mm_gate_bypass를 독립 관측하고 REQ07에는 template gate, SUP08에는 MM gate만 귀속한다. 교차 필드·혼합 필드도 같은 규칙을 적용한다. 이는 해당 gate의 우회이며 template 최종 렌더 효과·MM 코드 최종 실행은 별도 관측이다.

REQ08 정상 ID의 reuse도 실제 decoder admission이 model max보다 길면 위반이다. REQ07 역시 같은 경계를 사용한다. 길이가 같은 경우는 source input_processor의 실제 runner_type=generate 조건일 때만 위반이며 pooling/미확인을 generate로 추정하지 않는다. 실제 validation CALL의 prompt_type=decoder와 입력 길이/해시·config가 일치해야 한다. encoder/MM 길이 경계는 확장하지 않는다.

After는 보호필드 없는 독립 text 요청으로 제한한다. 그 요청의 renderer parsed defaults, 실제 InputProcessor CALL/RETURN, config와 독립 input/admitted domain membership=True, 비어 있지 않은 integer ID와 동일 admission hash를 확인한다. OOV32가 반환된 after는 정상으로 인정하지 않으며 primary의 이미 확인된 위반은 보존한다.

messages는 role(system/user/assistant)와 문자열 content만 허용하고 remote URL/data URI·image/audio/video/file 항목을 거부한다. primary와 after 각각 canonical body 128KiB 상한을 적용한다. 이는 선정된 안전 text probe의 기술 범위다.

## 실제 실행 재개와 운영자 복구 인계

A의 현재 `KubernetesTransition`은 읽기 전용 전환 gate다. 배포·종료·복원은 운영자가 수행한다.
실제 실행 전 A/운영자는 복원할 exact manifest/config bytes, 해당 namespace/workload/run 소유
검증 조건, 승인된 Recreate/복구 명령과 성공 확인 절차를 제공해야 한다. 이 입력이 없는 동안
현재 B 실행을 Kubernetes 실행 준비 완료로 집계하지 않는다.

B 도구는 원본 EVD/RES/RPT를 보존하고 소유 계측 session/probe를 종료한다. 서비스 실패·GPU
오류가 발생하면 해당 run의 복구 담당자가 승인된 절차로 복원한다. 재시작 뒤에는 실제 새
Pod/container/image/restart/start 신원으로 target을 다시 수집하고 정상 대조를 새 run과
별도 exact Ref로 남긴다. 자동으로 최신 revision/다른 Pod를 선택하거나 기존 target 신원을
재사용하지 않는다. Ready 상태와 실제 worker 종료/GPU 반납 관측은 각각 확인한다.

최종 로컬 고정 코드 `c0a1d413`은 전체6390건·기존제외19 통과(pytest154.12초/부모154.55초),
native48건·runner73건 및 검토 지적 해결 확인을 마쳤다. 실제 Kubernetes 요청은 실행하지
않았으며 당시의 A 미확정 pin 차단 결과다. 현재 소비 계약은 아래 A108 갱신을 따른다.


## A108 소비 갱신의 검증 범위

기존 CPU16+after16 실측, native callback bytes와 CPU source pins는 그대로 보존한다.
이번 변경은 B 명령의 source/runtime code 계약·명시적 `/9` 선택·기존 수집/판정/저장/owned seal 연결의 직접 회귀에 한정한다. 실제 Kubernetes 요청은 실행하지 않았다.
A source 통합, image 증명, 256KiB 초과 recording seal, 승인 target/config와 복구 절차가 남아 있어 실제 실행 준비 완료로 집계하지 않는다.
