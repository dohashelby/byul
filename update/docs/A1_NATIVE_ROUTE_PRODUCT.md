# REQ02 native 인증·등록 handler 관측 제품

REQ02/SC01-02의 기존 `route_record:kubernetes` 선언 파일 제품은 유지한다. 새 `native_route:kubernetes`는 기존 native8의 AuthenticationMiddleware 경로/token bool·등록 GET /v1/models 및 POST /v1/completions callback·source/code/session/PID-start 기록을 기존 lifecycle_session의 route_handler_index로 소비한다. 새로운 sensor/hook/native 판본이나 Gateway 구현은 없다. 현재 관측을 실제 principal 권한·신뢰된 경로 재작성·기업 허용 정책·인가 우회 FAIL·최종 worker/tensor 소비로 올리지 않는다.

기존 CLI select→run→judge→report→verify→반출/복원·저장팩 정확 Ref 재판정을 사용한다. `tools/build_kubernetes_native_route_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새폴더>`는 PACK31의 REQ02 revision4/executor native_route/profile `kubernetes-native-route-observation-v1`만 명시 DRAFT로 연결한다. 원질문/정상/challenge/기준/분기와 다른76개 정의 bytes·원PACK31 Ref를 보존한다. 판정은 모든축UNKNOWN, 정상 succeeded=null이며 전용 관측은 trials/events/associations/missing 및 authorization/scope false다. HTTP200·token bool·등록 함수 관측은 전체 정상·인가 증명이 아니다.

```toml
[executors]
native_route = "kubernetes"
[adapter]
name = "vllm"
[native_route]
target = "/owned/exact-target.json"
prepared = "/owned/reviewed-native8/manifest.json"
# 운영자가 시험 키를 제공할 때만 환경 변수 이름을 명시한다.
probe_key_env = "BYUL_EXPLICIT_REQ02_TEST_KEY"
max_duration_seconds = 120
```

probe_key_env는 선택 입력이다. 없으면 anonymous 요청만 사용하고 다른 환경 변수·Pod env·Secret을 검색하지 않는다. 있으면 명시한 변수 하나의1~512 printable ASCII 키만 읽어 해당 소유 engine의 세 요청에 Bearer로 전달한다. 키값/키hash를 결과·인계에 저장하지 않으며 ctx.secrets masking과 기존 응답 정제 경계를 적용한다. 시험 키 제공을 기업 principal/허용표 확정으로 해석하지 않는다. 기본 native 실행기의 config 허용집합은 원래4개로 유지하고 이 실행기만 probe_key_env 한 키를 추가한다.

POL은 정확 route_native_environment_sha256, route_native_target_sha256, route_native_prepared_sha256와 aware route_native_validity_start/end의5개다. window는0<초<=600이다. prepared는 기존 load_prepared/source/assets/code/shadow 검사를 그대로 거친 뒤 사전에 검토한 canonical SHA와 비교한다. 실행 때 읽은 해시를 승인값으로 사용하지 않는다. cfg는 위 target/prepared/kubectl_binary/max_duration_seconds/probe_key_env만 받는다. lab=true/vLLM0.31/native8만 지원하고 native0.32/다른 native 숫자/production은 전송 전에 거부한다.

전송 profile `kubernetes-owned-native-route-http-v1`는 REQ02 전용 models-before→completion→models-after 순서의 단일 요청3개다. wire ID는 req02-<slot>, X-Request-Id는 byul-req02-<고유32hex>-<slot>이고 한 실행에 같은 token만 허용한다. GET 두 개는 /v1/models·body=null, POST 하나는 /v1/completions·model=정확 API별칭/prompt=hello/max_tokens=1/temperature=0/stream=false다. headers는 X-Request-Id와 선택 Bearer만 받고 URL/host/query/fragment·mode/field/시간/출력 한도는 닫힌 공통 검사를 유지한다. 기존 다른 profile의 POST/path/body/ID 관문을 완화하거나 다른 점검 ID를 이용하지 않는다. 응답 ID/method/mode와 세대도 대조한다.

실제 serving에 미리 설치한 기존 native8 hook 기록만 읽고 정확히 선택한 session만 seal한다. 별도 Pod exec Python을 serving hook 실행으로 대체하지 않는다. collector/probe PID와 실제 recording PID를 구분한다. 원래 고정 nofollow/fstat·PID/start/시계·source/code/MODULE·전후 namespace/Pod/container/image/restart/start·census/파일 한도와 source/image/lab gate를 재사용한다. KubernetesLifecycleReader.image_check의 강한 false 관문과 노드 이미지/manifest ancestry 미확인은 유지한다.

선정 요청 header SHA와 index의 invocation·entry method/path/root-path SHA를 대조하고, 중복 header/다른 physical recording/열린 기록/누락 source module·HTTP binding 미확인을 별도 표시한다. 같은 recording의 순서·locators·token 결과·등록 callback/return·auth 대 handler path 변화는 정제된 관측 사실이다. 실제 principal·operation 인가·Gateway 신뢰·worker 귀속은 false다. 정상 승인/거부 전체 대조와 기업 재작성 정책은 별도 확정 대상이다.

후속 HTTP/native 수집·교체/restart·부분 파일·seal/report·첨부·parent 정리 실패가 앞선 정제 trials/index 자료를 지우지 않게 한다. 기술 수집 불완전은 ABORTED이고 선언된 deny+handler 관측은 미인증 후보로 남긴다. 새 PASS/독립 위반 규칙은 만들지 않으며 기존 다른 점검의 독립 FAIL은 유지한다. 원문 요청/응답·키와 키hash·Pod env/argv·임의 예외문은 저장하지 않는다.

검증 source는 `3b871a8a2fe55256f890c0e1591d13a648d72c0b`, 검토 pin은 `065eb9b1e24648c84efa9ec4fd327088e9b85e3b`다. Windows45통과72.41초(parent73.535), 동일1324파일 Linux45통과21.68초(parent22.549)를 확인했다. 설치241파일 일치20.413초, 격리 설치본 CLI4사례(normal/after-timeout/native-one-missing/attachment)15.488초·정확24Ref 반출/복원·저장팩 재판정, 전체소비349파일·기존96파일 보존을 확인했다. native8..15 hook/source bytes와 기존 IPC/egress method는 그대로다. 이전 인접 통과를 새 전체suite 통과로 합산하지 않는다.

실패 이력은 보존했다. 최초 d91b4f0의 Windows356통과/신규15실패/기존skip1/미선정1은 새profile 모델별칭 호환 누락과 시험 report 기본출력의 source report.json 생성이 있었다. 생성 보고서는 저장소 밖 증거로 보존하고 출력 경로를 fixture tmp로 고쳤으며 최초 전체 source 불변 성공을 주장하지 않는다. f2af6d4는 Windows54통과/12실패·Linux381통과/12실패였고 신규fixture가 외부socket 차단 뒤 asyncio 루프를 만들어 막힌 경우다. 기존 owned_loop를 재사용했고 차단·기대 판정은 유지했다. e51b819의35/35통과와 정리 사례1실패 후, 기존 CLI cleanup exit1을 명시 검증하고 새제품의 cleanup ABORTED를 연결했다. 최신45/45는 이 보수·pin에 필요한 직접 범위이며 변경 없는 인접 근거와 구분한다.

최종 문서 마감 커밋·검증 source·wheel source·정확HEAD CI를 구분한다. 실제 serving/Gateway/backend/Kubernetes/GPU 검증·전체REQ02/부모A 완료는 별도다. 원GPU 승인창 종료/추가Start0·46.66USD/기존 사용량/보존120GiB EBS·CI결제 외부차단·부모A39/56/4·A1 19/27/2·c9와 원본/sources/worktree/팩/Ref를 보존한다.
