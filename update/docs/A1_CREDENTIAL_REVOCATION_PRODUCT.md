# PLT10 자격 철회 표본 제품 계약

`credential_revocation:kubernetes` 0.1.0과 `kubernetes-credential-revocation-observation-v1`은 기존 CLI/증거/정확Ref 제품 경로의 한 부분이다. 원팩31 PLT10/SC19-01의 실제 issuer 철회·철회 전 정상·신규 접근/전파 기한·FAIL/UNKNOWN 기준과 다른76개 정의 bytes를 보존한다. `tools/build_kubernetes_credential_revocation_pack.py --seed packs/PACK-BASELINE --output <새 외부 폴더>`는 해당 점검만 명시 DRAFT로 연결한다.

기존 native8을 명시 소비하며 새 sensor/hook/profile을 추가하지 않는다. `prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/8 --output <새 외부 폴더>`를 재사용한다. native8~14와 A2/B 원본·과거팩/Ref의 bytes를 유지한다. 현재 지원은 lab=true/vLLM0.31·선정 engine/probe·직접 Pod HTTP·GET /v1/models다. source/image/lab/현재 PID-start 관문·native0.32 미지원은 그대로이며 일반 Gateway/TLS/다른 경로나 모든 issuer로 확대하지 않는다.

설정은 `[executors] credential_revocation="kubernetes"`, `[adapter] name="vllm"`, `[credential_revocation] target="..." prepared=".../manifest.json" retired_key_env="..." control_key_env="..."`이다. 선택 kubectl_binary/max_duration_seconds만 추가 허용한다. 두 키는 서로 다른 명시 환경변수의 값으로 메모리에서 고정하고, 미입력·동일값·HTTP 헤더로 사용할 수 없는 값을 거부한다. 새 자격을 생성하거나 키값·키값hash를 식별자로 저장하지 않는다. 증거에는 retired/control 고정 역할 라벨만 남긴다. issuer/event 라벨에 키값을 넣지 않는다.

14개 필수 정책은 `credential_revocation_contract.POLICY_TYPES`가 정본이다. 정확 ENV/target/prepared SHA, aware600초 이하 창, issuer/event 비밀 아닌 라벨·사건 시각 선언·deny-after, absolute-unconditional/after-verified-issuer-event, 명시 전파 예산/clock 오차·retired 역할·최대3개 신규 접근을 입력한다. 조건부 선언의 사건 시각+전파 예산과 선언 기한을 대조하되 실제 issuer receipt로 인증하지 않는다. hash/issuer/principal/기한/키값을 실행 중 자동 승인하거나 추정하지 않는다. ENV의 전체 모델 inventory/revision과 target의 API 별칭도 결합한다.

공개 `KubernetesCredentialRevocationHTTP.perform(slot,request_id)`는 control-before·retired-first·retired-second·retired-third·control-after 순서만 한 번씩 허용한다. 같은 UUID의 `byul-plt10-<32hex>-<slot>`을 사용하고 고정 GET /v1/models와 선택 역할의 Authorization만 보낸다. body·임의 경로/메서드·raw callable·다른 점검의 ID를 받지 않는다. 잘못된 응답 ID/세대/출력/시간은 해당 전송을 실패 상태로 고정하고 다음 wire 접근을 보내지 않는다. 처음 정상 control shape가 없으면 retired 접근을 보내지 않는다.

기존 고정 stream probe는 요청마다 HTTPConnection을 만들고 닫는다. 이는 source의 연결 설계이며 실제 대상에서 독립 connection receipt를 수집했다고 표시하지 않는다. 각 요청10초·전체300초 이하·응답 본문128KiB·stdout2MiB 상한과 소유 namespace/Pod/container/image/restart/start·ENV/run·Ready 전후 확인을 유지한다. 본문/오류/민감 필드는 메모리에서 구조·완전성만 확인하고 저장하지 않는다. issuer 변경·키 교체/삭제·Secret 조회·접근 예외 부여·원격 재시작을 수행하지 않는다.

유일 requestID/ASGI invocation과 같은 recording/PID/start/session·source module·token bool·등록 handler/반환을 기존 native index에서 소비한다. hash가 같거나 시간이 가깝다는 이유로 여러 invocation/프로세스를 합치지 않는다. 중복·재귀·context/source 누락·교체/restart는 미확인이다. auth bool이나 모델목록 shape는 trusted principal/실제 issuer 사건/전파·worker 추론의 증명이 아니다. 종료 기록에 현재 PID/start가 없으면 기존 의미대로 미확인이다.

`revocation_absolute_denial_constraint_violated`는 명시 무조건 접근 금지 시각·허용 clock 오차를 지난 retired 표본에서 정상 control과 HTTP shape·동일 source/process의 수락/등록 handler/반환을 확인한 제약 위반이다. 기존 rule_table의 functional FAIL에만 연결한다. 사건 producer를 확인해야 하는 after-verified-issuer-event에서는 같은 표본도 실제 전파 위반으로 확정하지 않는다. 이 단계에서 security/performance는 UNKNOWN이며 보안 우회 판정을 만들지 않는다.

401/403·native 거부 또는 해당 제약 위반 부재는 전체 PASS가 아니다. 실제 issuer/cache/사건·전파 시계·철회 전 정상·principal·독립 새 연결 receipt가 미확인이다. issuer_event/provider_propagation/normal_before_issuer/new_connection_receipt/scope_complete=false, normal_control.succeeded=null이다. NetworkPolicy 회수는 issuer 철회와 다르고 PLT11의 기존 session/handle 및 PLT12의 사본 처리/재사용은 별도다. 부모 A39/56/4·A1 19/27/2 완료 수를 늘리지 않는다.

소유 session만 seal/capture하고 EVD/RES/RPT→verify→반출/복원·저장팩 정확Ref 재판정에 연결한다. 이후 control timeout·native 일부 수집·seal·첨부·부모 정리·잘못된 응답 ID가 이전 안전 관측과 확인한 제약 FAIL을 지우지 않는다. 필수 source 결합 자체가 없으면 후보/누락이며 FAIL로 채우지 않는다.

CPU 검증은 기존 고정 AuthenticationMiddleware/ModelsRouter/serving 함수와 작은 Router·동일 공개 kubectl/HTTP 계약의 EXAMPLE 대역을 재사용한다. 키 원문과 키hash·민감 오류 본문이 EVD/artifact에 없는지, 조건부/미래 기한·거부/수락·중복/누락·교체·후속 오류·정확Ref를 확인한다. 별도 소유 socket 시험은 고정 helper가 실제5연결을 사용하는 CPU 근거이며 실제 Pod·issuer·serving/GPU 인수로 올리지 않는다.

실제 실증에는 선택 issuer/유효 정책/cache·시험 자격·철회 전 정상/원사건·시계·실제 새 접근과 거부/허용·전파·복구/정리 책임 및 정확 ENV/POL/source/image/model이 필요하다. 원GPU 승인창 종료·추가Start0·마지막확인stopped·기존46.66USD/과거 사용량·보존120GiB EBS과금·CI 결제 차단을 유지한다. 별도 새 시간/권한/민감 접근을 이 구현에서 승인하지 않는다.

현재 구현·회귀·문서 초안 종료 뒤 source 고정·영향/Linux·설치본/정확Ref·커밋/초안PR/정확CI/인계를 준비한다. 이전 LIF11의222파일/CPU 검증과 실제 배포04f7856 wheel을 이 새 소스 검증으로 소급하지 않는다.
