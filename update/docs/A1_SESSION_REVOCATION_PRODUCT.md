# PLT11 선정 기존 HTTP 연결 관측 계약

`session_revocation:kubernetes` / executor0.1.0, collection `kubernetes-existing-http-session-observation-v1`, HTTP `kubernetes-owned-existing-http-session-v1`이다. 파생 `PACK-KUBERNETES-SESSION-REVOCATION`은 원팩31 PLT11/SC19-02 질문·정상·challenge·충족·실패·미확인·legacy·method와 다른76개 정의 bytes를 보존하는 DRAFT다. 기본팩31을 변경하지 않고 명시 파생팩을 기존 select/run/judge/report/verify/export/restore·저장팩 정확Ref에서 소비한다.

닫힌 요청은 control-before/retired-session-open/retired-session-reuse/retired-new/control-after의 GET `/v1/models`5개다. 같은 probe 프로세스가 기존 retired 연결을 먼저 열고 재사용 뒤에도 유지하여 별도 새 연결과 비교한다. 명시 connect 뒤 `auto_open=0`으로 암묵 재연결을 금지하고 매 요청 전후 같은 socket 객체·Linux device/inode/FD/PID를 확인한다. 서버가 연결을 닫으면 재사용 미확인이며 새 연결로 채우지 않는다. 이는 선택한 probe TCP 연결이며 애플리케이션 session/handle·SSE·Gateway/TLS·worker 권한/serving receipt를 뜻하지 않는다.

`KubernetesSessionRevocationHTTP(target,env,{retired:memory_key,control:memory_key},...).perform(<32hex-token>,policy)`와 `report()`가 공개 경계다. 일반 callable/재호출/다른 method/path는 거부한다. key는 서로 다른 두 로컬 환경변수에서 메모리로만 읽는다. 원문/키hash·Authorization·원문 응답/오류·Pod env/argv를 artifact에 저장하지 않는다. issuer·정책 cache·키·Secret·Pod를 변경하지 않는다. 키와 같은 issuer/event 라벨 입력도 거부한다.

POL15는 `session_environment_sha256`, `session_target_sha256`, `session_prepared_sha256`, `session_validity_start/end`, `session_issuer_id`, `session_event_id/at`, `session_open_before`, `session_deny_after`, `session_deadline_basis`, `session_propagation_limit_ms`, `session_max_clock_skew_ms`, `session_max_wait_seconds`, `session_residual_policy`다. aware 유효창<=600초, start<open_before<=event<=deny_after<=end, open_before<deny_after, 명시 `deny-existing-after-deadline`만 지원한다. 근거는 `absolute-unconditional` 또는 `after-verified-issuer-event`다. 후자는 event+전파 예산과 deadline을 맞추지만 실제 사건 producer를 확인했다고 하지 않는다. 실사용 정책을 기본값으로 만들지 않는다.

선택 연결은 선언한 생성 기한 전에 정상 동작해야 하며 재사용은 기한+허용 clock 오차 뒤 시작한다. 준비 대기는 최대10초이고 준비 안 된 schedule은 HTTP 전에 거부한다. 요청당 최대10초·전체 source exec60초/inner예산2초 유보, body128KiB·고정 bounded HTTP framing·stdout256KiB를 유지한다. helper bytes `a687988eefb3e31f7a1f8c79c41e0da3649af89a4084ef5d6e4cf47ef4d9bf26`와 기존 stream framing `6ffd129cd855170a1a2fefb496efef4abfcfd7928d2dc6795c22344bd152a765`를 literal로 검사한다. 기존 stream probe·native8~14/source/lab/image/PID gate는 바꾸지 않는다. lab=true/vLLM0.31만 지원하고 native0.32/운영 프로필은 미지원이다.

정확 ENV/model repository/revision/inventory/API별칭·소유 engine/probe namespace/Pod/container/image/restart/start/run·Ready를 전후 확인한다. helper가 반환한 닫힌 typed safe packet은 이후 pipe/정리 오류 전에 보존하고, 현재 전후 binding과 native8의 유일 request/invocation/auth/등록 handler/반환·PID/start/session/source를 각각 결합한다. 앞선 안전 관측과 확인한 독립 FAIL을 후속 seal/capture/첨부/정리 오류로 지우지 않는다. 관측한 probe 권한을 serving worker 권한으로 올리지 않는다.

활성 control과 기한 전 선택 retired 연결의 source-bound 정상, 같은 연결의 기한 뒤 정상 HTTP/auth/등록 handler 반환이 있는 명시 unconditional 재사용 금지 제약만 functional FAIL이다. 사건 기반 정책은 실제 issuer/event/cache producer가 미확인이므로 UNKNOWN이다. security/performance UNKNOWN·normal succeeded=null·issuer/provider/application-session/serving-receipt/scope_complete=false를 유지한다. 거부나 HTTP200·기한 위반 부재로 전체 PASS를 만들지 않는다. PLT10 신규 접근과 PLT12 사본 처리/재사용은 별도다.

CPU의 고정 auth/등록 handler/native 프로토콜 대역은 EXAMPLE이다. 별도 소유 실제 Linux TCP 시험은 동일 keep-alive와 서버 조기 종료/재연결 금지의 근거이며 실제 Pod/issuer/serving/GPU 인수가 아니다. 구현·회귀·문서 초안 단계이며 고정·검증·설치·커밋·초안PR·정확CI·인계 전에는 기능 완료로 기록하지 않는다. PLT11 전체와 부모A39/56/4·A1 19/27/2 완료 수를 늘리지 않는다.

실제 issuer/cache/사건 및 실제 session/handle 종류·코드/설정·잔존 정책 질문은 대기 중이다. 실제 증명에는 철회 전 정상/철회/동일·신규 접근/기한과 복구·정리 책임 및 정확 ENV/POL/source/image/model이 필요하다. 원GPU 승인창 종료/Start0/마지막확인stopped·기존46.66USD·보존120GiB EBS과금·CI결제 차단을 유지한다. 과거 배포wheel04f7856/156파일·REQ11 MEASURED Ref와 PR127/225파일의 CPU검증을 이 새 소스의 검증으로 소급하지 않는다.


첫 고정9a737f4/1288파일 검증의 Windows1실패/459통과/기존skip1·제외1(212.36초, 부모213.688초), 동일bytes Linux1실패/460통과/기존제외1(69.24초, 부모69.701초)을 보존한다. 새 PLT11 제품과 실제 소유 Linux keep-alive/조기종료 시험은 통과했고 실패는 검토된 고정 소비 검사 한 건이다. Python Windows text 작성의 CRLF와 Git LF의 차이가 http_common·새 kubernetes_session_revocation 실행기·registry 세 파일에서 확인됐다. 두 실행 종료 후 이 세 파일을 이미 검토해 고정한372f354의 원래 blob bytes로 맞추고 위치를 포함한 AST 동일성을 확인했다. 새 해시를 승인값으로 삼거나 gate를 빼지 않았으며 동일 pin/B 원본/native/source gate/정책·판정·assert를 유지했다. 초기manifest/로그와 전후hash는 별도로 보존하고 영향 재검증한다.


검증 source f0c09729f8926099a81cdc3cf877bba716a9d0c5의1288파일을 고정했다. 초기 고정소스의 Windows459/Linux460 통과와 유일 pin bytes 실패를 보존하고, 검토된372f354의 같은 세 blob/LF bytes로 맞춘 뒤 Windows264통과/기존skip1·제외1(109.33초, 부모110.036초), 동일bytes Linux265통과/기존제외1(37.69초, 부모37.981초)을 확인했다. 제품 AST/판정/기존 gate/assert는 바뀌지 않았고 새 skip·제외·런타임 해시 승인·경로 제외는 없다. 변하지 않은 인접 결과를 재사용했다.

source/wheel/격리 설치229파일 일치, 설치본 CLI4사례(거부·무조건 수락·조건부 수락·첨부 실패), 정확24Refs 반출/복원·저장팩 재판정(7.772초), 검토된372f354 전체337파일 소비를 확인했다. 소유 실제 Linux TCP 서버의 동일 keep-alive/별도 새 연결 및 서버 조기 종료 후 암묵 재연결 금지를 직접 검증했다. 이 근거는 CPU/EXAMPLE이며 실제 Pod/issuer/serving/GPU 인수가 아니다. A2 전용12/B 원본27·기존c9 pin·native8~14/resource probe·기존 framing bytes와 과거 배포wheel04f7856/156파일·MEASURED Ref를 보존했다.

마감 범위는 선정 probe HTTP keep-alive의 제품 연결/명시 unconditional 재사용 금지 제약/CPU·설치 검증이다. 실제 issuer/cache/event·전체 애플리케이션 session/handle·잔존 정책의 최종 실증과 PLT11 전체/부모A39 완료는 미확인이다. 정확 최종HEAD CI는 push 뒤 따로 기록하고 기존 결제 차단을 성공으로 처리하지 않는다. 원GPU창종료/Start0/마지막확인stopped·46.66USD/보존120GiB EBS과금을 유지한다. 다음 PLT12는 실제 사본 위치·수신자·처리 기한/방법/보존 예외·재사용 경계가 준비됐는지 원기준과 기존 공통 관측을 대조한다. PLT10/11 issuer·session 및 LIF12 retry 주체 질문 대기는 별도로 보존한다.
