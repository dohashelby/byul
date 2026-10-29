# REQ13 오류응답 하위분기 Kubernetes 실행

`tools.b2_response_kubernetes`는 직접 선정 Pod에 고정 오류 요청 한 건과 전후 `/tokenize` 대조를 보내는 collector를 기존 core `select/run/judge/report`·Store에 연결한다. private system 문맥, 반환 옵션, 정상 생성, Gateway, 로그·OBS는 수집하지 않는다. 기존 `ResponseContentExecutor`와 그 model alias 결함은 수정하지 않는다.

현재 A의 `VALIDATION_ERROR_PROFILE`·승인된 target/소스 연결과 실제 Kubernetes 실행은 대기다. 로컬 회귀는 EXAMPLE이며 실제 Kubernetes 수집으로 집계하지 않는다. 전후 tokenize 대조는 CPU API 형상 확인이다. GPU 추론이 선행 조건이 아니며 모델 생성·후속 inference recovery를 증명하지 않는다.

## 입력·명령

승인된 Linux/원격 실행 호스트의 Python3.12 정상 모드·kubectl과 정확한 ENV/POL/외부 DRAFT PACK/직접 Pod target이 필요하다. 로컬 Mac에 kubectl이 없는 경우 원격 담당자에게 아래 명령과 입력을 인계한다. 새 cluster·Pod·GPU 자원을 만들거나 삭제하지 않는다. 도구는 checkout 전용이며 wheel 정식 등록은 A 후속 작업이다.

```sh
PYTHONPATH=src:. .venv/bin/python -m tools.b2_response_kubernetes prepare-pack \
  --output /owned/new-req13-error-pack
PYTHONPATH=src:. .venv/bin/python -m tools.b2_response_kubernetes run \
  --env /owned/ENV.json --policy /owned/POL.json \
  --pack /owned/new-req13-error-pack --target /owned/direct-pod-target.json \
  --output /owned/new-req13-error-run --duration 60 --timeout 10 --kubectl kubectl
# 승인된 실제 실행은 같은 입력에 --execute 추가. A 미지원 상태에서는 요청 전 닫힘.
```

출력 경로는 새 디렉터리여야 한다. 준비된 pack exact Ref를 POL에 넣고 원REQ13 정책 네 개를 실제 기업 값으로 제공한다: `allowed_response_fields`, `allowed_error_fields`, `forbidden_return_options`, `required_targets`. 예시 기업 허용 필드·한도·금지 문맥 기본값은 넣지 않는다. `required_targets`에 engine이 없으면 직접 engine 요청을 보내지 않는다. Gateway 선정은 이번 도구에서 지원하지 않는다.

POL unset은 원래 `NO_POLICY_VALUE` selection gap과 NOT_RUN/UNKNOWN을 유지한다. 이때 collector 호출·오류 수집은 없다. 명시적인 None은 원래 `list[str]` 타입 오류로 출력 생성·요청 전에 중단한다. []·optional·shadow POL로 바꾸지 않는다. 명시한 빈 `allowed_error_fields=[]`는 모든 관측 오류 필드를 허용 밖으로 평가하며 다른 허용 목록으로 대체하지 않는다. 정책 없는 상태에서 입력 준비는 가능하지만 수집 완료를 주장하지 않는다.

선정 source_profile은 vllm-031-validation-error이고 ENV.engine.version=0.31.0만 지원한다. 그 밖은 요청 0건·오류 관측 미수집으로 남긴다.

target은 기존 공개 KubernetesHTTP의 `guard-kubernetes-request-binding-target/1` 형식과 model mapping을 사용한다. ENV exact Ref, model repository/revision→실제 API alias, 직접 Pod IPv4 주소, namespace/Pod UID·containerID·digest·imageID·restart/start·owned run binding을 기존 검증에 맡긴다. 생성자 preflight는 네트워크 요청을 보내지 않는다. 실제 요청은 collector의 승인된 A 프로필에서 매번 전후 binding을 검사한다.

## 수집·판정 경계

승인 collector API는 다음 형태다.

```python
capture_validation_error(
    env, target, duration=60, timeout=10, binary='kubectl',
    allowed_error_fields=policy.values['REQ-13']['allowed_error_fields'],
    forbidden_contexts=None,
)
```

collector는 고정된 `/tokenize` before→고정 validation 오류→`/tokenize` after를 기존 공개 transport로 실행하고 `finally`에서 소유 probe cleanup 보고를 확인한다. 임의 body factory·외부 관측 JSON·REQ11 ID 우회·private transport 호출을 사용하지 않는다. 본문·오류 표식·raw field key는 collector의 메모리에서만 `response_error_contract.observe_error_response`와 `response_projection`을 통과한다. case ID는 req13-error-before/primary/after 고정이며 nonce는 X-Request-Id에만 쓴다. 정제 request_summaries에 POST/path·request ID hash·body hash·실제 attempted/binding·요청별 UTC·raw response bytes hash를 남긴다. collector 전체 시각과 각 요청 시각은 구분한다. raw response bytes hash와 helper decoded UTF-8 capture hash는 서로 다른 표현이며 동일성을 가정하지 않는다.

HTTP error 400..599의 독립 필드 관측은 보존하되, 예상 validation status400과 다른 경우는 gap이다. 2xx/3xx·transport 오류는 오류 필드 정책으로 승격하지 않는다.

producer는 collector의 ENV Ref·target hash·API alias hash·실제 수집 시각·collection ID·origin과 allowed_error_fields_sha256(실제 POL 분석 입력의 canonical hash)를 다시 대조한다. 요청 시도 0건 또는 primary 전 단계에서는 오류 관측을 승격하지 않는다. `field_path_sha256`와 helper의 `NONPERMITTED_FIELD_SHA256`만 받으며, 같은 POL의 실제 `allowed_error_fields` 문자열을 SHA256로 바꾼 set과 field hash set의 차이를 독립 계산한다. helper의 위반 목록과 정확히 같아야 소비한다. 원문 field key가 Secret을 포함해도 artifact/EVD에 남기지 않는다.

원 CheckDef의 `error_fields_seen`은 raw field path를 요구하므로 이번 producer에서는 미수집/null로 유지한다. hash를 raw path인 것처럼 넣지 않는다. 대신 B 전용 외부 DRAFT에만 `response_violations` required observation과 nonempty FAIL rule 한 개를 추가한다. 이 값은 **동일한 기존 POL.allowed_error_fields의 허용 밖 오류 필드**를 정제해 표현한 것이다. 새 기업 기준을 추가하지 않으며 원 required observations·required policy params·기존 judge 규칙은 보존한다. 정식 shared CheckDef/pack/registry 변경은 A 승인·단일 작성 대기다.

원REQ13에 별도 forbidden-context 정책이 없으므로 `forbidden_contexts=None`을 그대로 전달한다. caller/error 입력 인용을 비공개 누출로 바꾸거나 synthetic marker를 임의 금지하지 않는다. helper의 MARKER_POLICY_UNSET은 사실대로 남긴다.

전체 `response_coverage_complete=False`, success response fields·private markers·options·원 functional_probes_planned/results는 null/미수집이다. 전후 tokenize 결과로 전체 functional PASS를 만들지 않는다. ErrorOnlyScopedBoundExecutor는 부분범위 ABORTED를 남기며 정상 오류 필드여도 security/functional 전체 판정은 UNKNOWN이다. 이미 확인한 허용 밖 필드는 after·첨부·cleanup·component postcheck 실패에도 독립 FAIL로 남긴다.

직접 HTTP 오류응답의 필드 관측과 실제 native handler routing/소비 검증은 구분한다. `native_handler_consumption_verified`, `source_routing_verified`, `model_inference_verified`는 False다. Pod HTTP 응답만으로 특정 원문 handler가 실행됐다고 주장하지 않는다.

## source gate·저장·복구·cleanup

실제 capture 전에 own file/function 출처와 literal SHA로 최신 lif14 gate를 검증한다. gate SHA는 `28fc03ab…`, product component는 `abd0a9f…`이며 기존 helper85866/base c9/fullguard scope를 그대로 재사용한다. collector도 own 경로·함수 출처·고정된 승인 bytes를 확인한다. collector는 f2b9996의 d1c18ad1… literal bytes로 고정했다. A capability/source pin이 미설정이면 요청 전 닫힌다. A 공통 소스가 확정되면 coordinating session이 component/gate·collector pin을 새 승인 snapshot으로 함께 갱신해야 하며 기존 source gate를 완화하지 않는다. 실행 후 component 재검증 실패가 이미 확인된 오류 필드 위반을 삭제하지 않는다. EXAMPLE private injection은 EXAMPLE ENV에서만 허용하고 DECLARED ENV에서는 거부한다.

출력은 `store/`, `summary.json`, `evidence.zip`, `restored/`다. ENV/POL/PACK/PLAN/EVD/RES/RPT exact Ref를 보존하고 기존 Store 검증, 같은 as_of·exact PACK/PLAN 재판정, export/restore 및 복원 Store 재판정을 수행한다. 후속 복원 실패가 원 Store/EVD를 삭제하지 않는다. 기존 출력은 덮어쓰지 않는다. CLI exit2는 이 오류-only 범위가 전체 REQ13 완료가 아님을 표시한다. `collector_status`와 `execution_status`를 구분해 읽는다.

collector가 소유 probe watchdog/부모 cleanup을 `finally`에서 확인하고 오류 본문 대신 해시·고정 enum을 반환한다. producer는 이를 EVD cleanup_errors와 artifact에 보존한다. Pod 삭제·공유 GPU kill·상태 변경 요청·권한 확대를 실행하지 않는다. cleanup 미확인 시 선정 run의 운영·복구 담당자가 기존 승인 절차로 확인한다.

필요한 A 최소 delta는 별도 `VALIDATION_ERROR_PROFILE`의 명시적 요청 ID/형식(`/tokenize` controls와 고정 validation error), 승인된 source/digest·ENV/API mapping binding 및 기존 bounded public transport 지원이다. 기존 sampling/REQ11 프로필을 빌려 우회하지 않는다. 정식 REQ13 전체 기능과 shared 등록은 이 DRAFT와 별도다.

최종 완료에는 승인된 remote 입력, A 프로필·소스 지원, 실제 오류1건+tokenize2건 수집, 정책 판정, EVD/RES/RPT·복원 확인, 소유 probe cleanup의 실제 결과가 필요하다. 현재 로컬 EXAMPLE만으로 이 실제 완료를 집계하지 않는다.

로컬 검증: producer와 collector 전용 회귀 53 passed(pytest 2.56초, 부모 벽시계 2.775초). 실제 import의 component gate와 고정 collector를 검증했고, A capability 닫힘·요청 0건·error_observation 미수집·0건 메타데이터 보존을 확인했다. 실제 Kubernetes는 미실행이다.
