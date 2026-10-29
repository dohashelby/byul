# REQ04 전달 신원 제품 연결 계약 v1

PR120의 c88a951e6320f53b06b37652fe55896f058f092a에서 이어간다. 공동 c9af729·기존 미커밋/worktree·원본/sources/·팩/Ref·실증과 A2/B 전용 bytes를 보존한다. 부모 A39/56/4와 A1 19/27/2는 유지한다.

담당은 REQ04 SC01-04다. 사용자 입력 header → Gateway의 제거/정규화/재발급 → 엔진에서 실제 소비된 값 → 인가 principal/최종 권한 결정의 관계가 최종 대상이다. priority/session/DP rank/request ID의 소비는 운영 선택 관측이며 그것만으로 사용자 신원이나 권한을 확정하지 않는다. 기존 REQ01/03의 직통/역할 결과를 전달 신원 위조 방지 결과로 소급하지 않는다.

현재 기존 KubernetesRoutesHTTP는 engine/probe/external_probe/gateway의4Pod·namespace/Pod/container/image/restart/start·ENV/model mapping·owned run을 검증한다. Gateway normalized header나 최종 trusted principal을 수집하는 기능은 없다. 그 관측이 필요한데 일반 HTTP응답만 받았다고 연결 완료로 삼지 않는다.

시작할 범위는 명시된 전달 header와 정상/대체 값을 사용하는 닫힌 무해 요청, 전후 정상대조·실제 정규화/소비 source의 판본 및 관측 결합, 기존 CLI 선정/실행/EVD/RES/RPT/verify/반출·복원·정확 Ref다. 기존4Pod 전송을 재사용하되 다른 점검 requestID/private 호출로 우회하거나 일반 route 프로필의 허용 범위를 통째로 완화하지 않는다. 실제 Gateway 서비스·인증 구현을 새로 만들지 않는다.

필요한 기업 입력은 신뢰 header/issuer·전달 책임·호출자→실제 principal/모델·작업 허용표와 검증 대상 경로다. 정상/대체 값은 명시한 서로 다른 환경 변수에서만 읽어 stdin으로 전달하고 원문·자격·응답·Pod env/argv·예외를 저장하지 않는다. 이름 validator는 issuer 승인·native 신뢰를 제공하지 않는다. transport credential/프레이밍/request ID header를 별도 claim 슬롯으로 재사용할 수 없다. 그 외 신원 형식은 전체 REQ04의 미완료 범위로 남기며 이 작은 범위로 최종 요구를 줄이지 않는다.

프로브/실제 인증·인가 주체를 구분한다. declared identity claim과 실제 권한 결정이 독립 source·request/call/session/PID/start·전후 세대에 결합되지 않으면 UNKNOWN이다. 값 hash 일치·HTTP200·설정만으로 trusted principal/PASS/FAIL을 만들지 않는다. 운영 selector 변경은 실제 선택·권한 영향이 따로 확인되어야 한다.

구현 순서는 입력·신뢰/소비 계약 확정 → 공개 닫힌 전송과 실제 소비 source 경계 → 전용 분석·명시 DRAFT 팩·registry·CLI/증거 → 정상/위반후보/누락/교체/restart/후속 수집·첨부·정리 → source 고정/영향/설치/정확 Ref다. registry `forwarded_identity:kubernetes`와 실행기0.1.0, 전송·정제 parser·명시 DRAFT 팩 및 시험 초안을 구현했다. 아래 계약은 소스 고정 후 영향/설치 검증 대상이며 실제 실증 완료가 아니다.

새 실제 기동은 없다. 원래 GPU 창 종료·추가Start0·마지막 확인 stopped·총46.66USD 상한과 CI 결제 차단을 유지한다. 실제 재개에는 별도 승인 시간, fresh ENV/POL/source/image·실제 Gateway/principal·소유권/차례·정상대조·정리 책임이 필요하다. 그 대기가 다른 준비된 담당 개발을 막지 않게 한다.

공개 제품 입력은 다음과 같다. Python3.12, `lab=true`, vLLM0.31, 명시된 모델 repository/revision과 API alias의 기존 결합만 지원한다. 기존 routes/REQ01/03 프로필이나 native0.32 gate는 바꾸지 않는다.

```toml
[executors]
forwarded_identity="kubernetes"
[adapter]
name="vllm"
[forwarded_identity]
target="<검토한 4Pod target.json>"
roles={allowed="REQ04_KEY"}
[forwarded_identity.header]
header_name="X-Example-User"
field_kind="identity_claim"
normal_value_env="REQ04_NORMAL"
alternate_value_env="REQ04_ALTERNATE"
```

header 이름은 ASCII 문자로 시작하는1~64자 이름이며 Authorization·Cookie·Host·Content-Length·X-Request-ID 등 예약 필드는 거부한다. `field_kind`는 `identity_claim` 또는 `operating_selector`다. 세 값은 서로 다르고 ASCII 출력 문자1~256자다. 숫자 selector `0/1`은 허용하되 사용자 신원으로 해석하지 않는다. 알려진 값이 정제 필드에 원문으로 복사되면 길이와 무관하게 거부한다. 길이8 이상 값은 부분 문자열도 거부한다. 한 자리 selector를 모든 해시의 숫자에 전역 치환하지 않으며 닫힌 필드형식·알려진 원문 일치 검사로 처리한다. 원문을 해시 필드로 재명명하는 우회는 거부한다.

필수 POL은 `forwarded_environment_sha256`, `forwarded_target_sha256`, `forwarded_session`(32hex), `forwarded_gateway_source_sha256`, `forwarded_engine_source_sha256`, `forwarded_identity_policy_sha256`, `forwarded_validity_start/end`다. aware 유효 구간은 최대600초이며 각 요청/수집 전후 확인한다. 값은 소비 전 검토해 고정하며 실행 중 발견한 값을 자동 승인하지 않는다. target/model/ENV/예상target pin 불일치는 I/O 전에 거부한다.

`KubernetesForwardedIdentityHTTP.verify_owned_generations()`와 `__call__(EXTERNAL,[request],timeout)`은 같은 기존 kubectl 포획/4Pod owner 검사를 사용한다. HTTP 프로필은 `kubernetes-owned-forwarded-identity-http-v1`이다. 한 UUID의 `normal-before/alternate/normal-after`만 지원하며, alternate는 정상 전대조 구조 확인 후에만 보낸다. 같은 allowed bearer로 선택 Gateway의 `POST /v1/completions`, 고정 hello·1token·temperature0·stream=false·정확 모델 alias를 보낸다. 요청당10초/실행 전체최대300초/HTTP 출력2MiB/본문128KiB 상한을 재사용한다. 원문은 전송/일시 구조 검사에만 쓰고 report·EVD·첨부에는 넣지 않는다. 실제 통신 구간은 external probe→Gateway이며 Gateway→engine 통신은 별도로 관측되지 않았다.

기록 프로필 `kubernetes-forwarded-identity-observation-v1`, 문서 `guard-forwarded-identity-observations/1`은 두 role의 `/guard-identity/data/<session>/forwarded-identity.json`만 읽는다. 초기 및 각 요청 뒤 최대8회, 파일64KiB·role당64행·합계최대512개의 고유 행이다. 기존 nofollow/regular file·전후 파일 identity·namespace/Pod/container/image/restart/start·소유 run을 검사한다. 이 reader의 별도 Python은 파일 수집자이며 실제 serving hook이나 신원 생산자가 아니다. native sealer·worker 종료는 호출하지 않는다.

각 문서는 role·정확ENV/target/run/session·선고정 source/policy SHA·header 이름SHA/종류·source_ended/dropped_events와 events만 가진다. events의 닫힌 필드는 `seq,event_id_sha256,request_sha256,phase,claim_sha256,principal_sha256,issuer_sha256,call_sha256,parent_call_sha256,operation_sha256,action,authorization,monotonic_ns`다. Gateway는 INGRESS/NORMALIZED/AUTHORIZED, engine은 CONSUMED/AUTHORIZED를 지원한다. 값은 해시 또는 명시된 enum/null이며 `producer_authenticated=false`만 수락한다. SHA 주장은 파일 소비 일치이며 해당 코드의 실제 실행·서명·PID/start 인증이 아니다.

단일 request의 ingress→normalized→consumed는 유일한 미변경 행, 정확한 입력/operation과 call/parent 관계를 요구한다. 인가는 동일 role에서 해당 정규화 또는 소비 call의 자식이어야 한다. 동일 eventID 내용 교체·중복 phase·누락/끊긴 call은 귀속 불명이다. 다른 header가 소비되거나 같은 bearer에서 principal 주장이 바뀌면 `confirmed_violation=false` 후보만 남긴다. 전후 source/세대 교체와 후속 읽기·첨부·정리 오류는 앞선 유효 기록을 지우지 않고 ABORTED/UNKNOWN으로 보존한다. 관계 중복만 있으면 수집 절차 COMPLETED라도 판정은 UNKNOWN이다.

`tools/build_kubernetes_forwarded_identity_pack.py --seed packs/PACK-BASELINE --output <새 폴더>`가 원팩31과 다른76정의를 bytes 그대로 보존한다. REQ04 revision4의 질문·분기·필수결과·기준을 보존하며 전축 rule_table UNKNOWN인 DRAFT 팩만 만든다. 기존 `guard select/run/judge/report/verify`, `guard evidence export/restore`와 저장팩 정확6Ref 재판정을 사용한다. normal HTTP 구조·선언 관계는 별도 사실이며 `normal_control.succeeded=null`, producer/principal/scope=false다.

읽기 전용 참고 `runtime/src/serving_runtime/mvp/auth.py`는 LAB static bearer와 IdP/계정 역할 미결을 명시한다. `request_records.py`의 runtime-mvp-record/1.0은 이 정제 문서와 다르고, `backend.py`의 고정 헤더와 `request_flow.py`의 Authorization 소비를 전달 신원 normalization 생산자로 취급하지 않는다. 루트 runtime 파일/구현/상태는 수정하지 않았다. 실제 Gateway/engine 생산자·코드와 실행 PID/start·issuer·최종권한 정책 및 전체 SC01-04/부모A 완료는 미완료이며 별도 실제 관측이 필요하다.

- 검증 마감: 정확c5905e5/검토pin8af2031의 Windows205통과·기존skip1/제외1(74.82/75.627초),동일1217bytes Linux508통과·제외1(70.28/70.602초),source/wheel/격리설치196파일일치와설치CLI4/정확24Ref왕복(7.720초)을확인했다. 초기505통과/2시험준비실패와원소스를보존했으며제품guard변경·기대값완화·새제외·검증중변경은없다. 소유HTTP소켓3요청·정상/선언위반후보·누락/교체·후속오류·원문비저장을확인했다. B전체304파일고정소비·A2선정12/B원본27·기존native8~11/IPC/원팩31을보존한다. 새실제Kubernetes/worker/GPU·trusted issuer/producer/PID-start·기업권한정책·전체REQ04/부모A는미완료다. 결과문서만갱신하고초안PR/정확HEAD CI/인계를마감한다.
