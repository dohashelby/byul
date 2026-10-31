# 명시 native10 ASGI invocation 관측과 REQ12 소비 /1

기존8 센서와 Online9 controller·B callback bytes 및 기본8 선택을 보존한 별도
`vllm-native-lifecycle-metadata/10` / `guard-native-lifecycle-events/10` 계약이다.
실제 소비자는 기존 request_termination:kubernetes의 REQ12 절차와 정식 EVD/RES/RPT·
verify·반출/복원·정확 Ref 재판정이다. LIF10 기본 실행기와 A2/B 전용 소비의 지원을 확장했다고
추정하지 않는다. 늦은 native 출력의 원천·클라이언트 수신·자동 retry·worker/GPU 회수는 별도 미확인이다.

공개 준비 명령:
```text
guard prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/10 --output <new-owned-directory>
```
controller, exact private base8, sitecustomize, settings의4개 asset과 manifest를 만든다.
base8 bytes SHA는2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd다.
manifest를 수정해 새 base를 승인할 수 없고, legacy8·Online9는 각 기존 asset 집합/상한/validator를 유지한다.
controller import는 engine import/설치를 하지 않으며 설치는 기존 명시 site hook 경로에서만 이뤄진다.
독립 seal은 fixed base의 선택 session marker만 사용하고 hook·worker·다른 session을 종료하지 않는다.

vLLM0.31.0·CPython3.12.3/3.12.14·optimize0에서 검토된 자신의 `_observed` code literal을
각각 고정한다. 상대 filename을 고정한 offline source compile의 code graph이며 목표 엔진에서
관측한 code hash를 승인값으로 채우지 않는다. native module의 기존 source/code gate도 유지한다.
standalone base는 fixed /guard-lifecycle root에서 nofollow/nonblocking FD로 열고 regular·상한·
전후 identity·정확 bytes/hash를 확인한다. 임의 파일/프로세스나 코드 경로를 설정 입력으로 받지 않는다.

선정 POST /v1/completions에서 기존 live route invocation을 native selection과 그대로 공유한다.
ATTEMPT의 닫힌 필드는 request/invocation/path/root/method SHA, phase, context_live,
body_bytes/body_sha256/body_hash_complete/more_body, observer_runtime/code_sha256이다.
phase는 APP_ENTER/APP_RETURN/APP_RAISE/DISCONNECT_RECEIVED/BODY_SEND_RETURN/BODY_SEND_RAISE다.
원문 body/header/token/error/Pod env/argv는 저장하지 않는다. body hash는 최대65536bytes까지이며,
큰 원문을 자르거나 바꾸지 않고 크기와 미완결 표시만 남긴다. 원래 receive/send 호출 수·순서·
반환 객체·예외/cancellation 동일성을 보존한다. 추가 관측 실패는 원래 serving 결과를 바꾸지 않는다.

BODY_SEND_RETURN은 ASGI server callback이 반환한 사실이다. 실제 client 수신이나 native A 출력이
B에게 전달됐다는 증명이 아니다. 같은 body hash도 native 출력의 원천을 인증하지 않는다.
종결 뒤 callback이나 다른 PID에 상속된 context는 context_live=false로 보존하며 실제 시도 결합으로
올리지 않는다. 이 판본의 fork 회귀는 context/PID 불일치 CPU simulation이며 실제 worker fork 실증이 아니다.

strict parser는 `/10`에서만 새 event/field/enum을 허용한다. source/schema/session/assets·hash chain·
행16KiB·최대2048행/2MiB와 기록별 단일 고정 compiler/code를 확인한다. 같은 invocation의 request/
path/root/method 불일치는 거부한다. 누락/중복 enter·terminal은 incomplete로 남기고, 늦은 callback·
hash 미수집을 따로 집계한다. legacy ASGI 행에 없는 invocation을 순서나 시각으로 채우지 않는다.
Kubernetes 읽기는 선정10에만 기존2MiB 공개 file profile을 사용하며 기존 exact target/ENV/run·
전후 세대·source/lab/image·시간/출력·소유 process/session 경계를 유지한다. lab=false와 native0.32는 닫힌다.

REQ12의 동일 auxiliary session/native request/ASGI invocation·같은 recording/PID/start·root와
live context를 유일하게 대조해 `request_attempt_asgi_events`에 결합 관측을 남긴다. 중복/누락/다른
세대/상속/종결 뒤 사건은 case_id=null이며, 옛 ASGI 행은 기존 attempt_case_id=null을 유지한다.
`asgi_invocation_binding_observed`는 관측 관계다. client_receipt_verified/native_output_origin_verified/
producer_authenticated와 기존 late_delivery/automatic_retry/scope_complete는 false다.
실제 늦은 출력 원천과 최종 소비자·폐기/전달·취소대상·기업POL/신뢰 결합은 계속 필요하다.

derived REQ12 팩은 revision2/check revision6으로 새 두 관측을 명시한다. PACK31·77개·원래 두
분기/질문/판정 기준·다른76개 정의 bytes와 과거 팩/Ref를 보존한다. 기존4POL의 정확 ENV/target·
유효창을 재사용하며 정책·승인값을 관측에서 만들지 않는다. 판정 축은 모두 UNKNOWN이다.
수집/seal/첨부/parent cleanup 실패가 이전 metadata를 지우지 않으며 ABORTED와 UNKNOWN을 구분한다.

9ea23af 제품guard와72d3a7e 시험 보수의1182파일 고정으로 최초영향751통과·새시험12실패를
보존하고, 소유 asyncio loop/CLI manifest·공개 오류 형식을 보수한 Windows175통과·기존skip1/제외1
(39.18/39.823초)를 확인했다. 같은 bytes Linux전체6942통과·제외19(333.11/334.482초),
source/wheel/격리설치180파일 일치·준비CLI3/제품CLI4·각 정확6Ref 왕복(7.745초),
실제 Linux 소유FD/code admission·거부10사례를 확인했다. 설치 OUT 하네스의 변수 이름 충돌
실패를 보존하고 하네스만 수정했으며 제품·code literal·검사·기대값·skip/제외를 완화하지 않았다.
검증 중 source bytes는 불변이며 마지막 결과 문서만 갱신한다. 실제 serving/hook/worker/GPU 실행은
없으며 CPU/EXAMPLE을 실제 인수로 올리지 않는다. B Online9/A2 전용 native file10 소비, native0.32·
다중GPU/TP·실제 기업POL은 이 계약의 자동 지원이 아니다. 원래 GPU 승인창 종료·추가Start0·
마지막 확인stopped와 CI 결제 차단·부모A39/A1담당19 전체범위를 유지한다.
