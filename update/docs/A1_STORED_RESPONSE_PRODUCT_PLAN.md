# REQ06 저장 객체 소유권 제품 계약 v1

부모 A39/56/4·A1 19/27/2와 공동 c9af729를 유지한다. PR121의 f8995a879db324b57fb8ccb09d57ffb38984f558에서 별도 소유 worktree로 시작한다. PR121의 guard 검증 소스 c5905e5와 실제 REQ11 배포 wheel04f7856은 서로 다른 판본이다. 기존 원본·sources/·미커밋·worktree·팩/정확Ref·A2/B bytes·실증은 보존한다.

대상은 REQ06 SC02-03 전체다. 두 주체 A/B가 자신의 저장 Responses 객체를 만들고, 다른 주체의 ID로 조회·취소·previous_response 연결·동일request_id 덮어쓰기를 시도했을 때의 객체별 실제 효과를 Gateway와 직접 경로에서 구분한다. 메모리 저장도 대상이며 일반 OutputCollector의 내부 취득, REQ12의 요청시도 결합, B의 영속 job 점검을 저장 Responses 객체 권한 검사로 바꾸지 않는다.

기존 vllm 어댑터의 MUTATING_USER_ROUTES/ROUTE_REVIEWS는 POST /v1/responses와 POST /v1/responses/{response_id}/cancel을 일반 프로브에서 제외한다. GET의 존재하지 않는 ID 표본과 401/403/404·HTTP200만으로 이 점검을 완료하지 않는다. 새 작은 프로필이 기존 경로 전체의 mutating gate를 풀거나 다른 점검의 ID/private 호출을 빌리지 않는다. 등록 전 초안은 네트워크 요청을 보내지 않는다.

재사용한 읽기 전용 정적 소스는 기존 guard-output/byul-developer-a-execution-20261028/gpu-installed-source의 세 파일이다. 보관 manifest의 bytes SHA와 일치함을 확인했다. 이것은 당시 추출 소스의 보존 검증이며 현재 배포·이미지 ancestry·실제 store 활성·실행 증명이 아니다.

| 소스 | 고정 bytes SHA256 | 이번 검토 지점 |
| --- | --- | --- |
| responses/api_router.py | cffe4bb3cd4cfa2bfd71039c0125b68ad730f1d302a37e128f1055a335c503a3 | create/retrieve/cancel method·path 및 handler 경계 |
| responses/serving.py | c701580373b32b57e2da6091ec3c242bbae0439974b5af088ba93cc588aaf3c8 | store 비활성 시 store 무시, previous lookup, background 저장/task, retrieve/cancel |
| responses/protocol.py | 5fa24e326726dd3e73b15314921f99dca2bf2f587e0bb2b1b286b525ed218460 | store 기본값·background·max_output_tokens·명시 request_id/응답 ID |

serving.py의 retrieve(1119행부터)는 저장소 lookup 결과를 반환한다. cancel(1142행부터)은 queued/in_progress가 아니면 동기 응답 취소 오류를 반환하고, 가능한 상태면 stored status 변경과 background task.cancel을 수행한다. 따라서 동기 completed 객체에서 얻은 취소 오류는 타 주체 권한 거부의 정상 대조가 아니다. 표시 status=cancelled도 독립 worker/GPU 회수 증명이 아니다. 실제 배포 Gateway/주체/정책 효과는 별도다.

이번 구현 순서는 다음과 같다.

1. 명시 선택한 origin/주체/operation과 소유 UUID의 객체 식별·생성 receipt·전후 세대 계약을 확정한다. 임의 ID·다른 실행·주체·소스의 객체를 읽거나 취소하지 않도록 메모리 내 ledger를 먼저 만든다. ledger의 '우리 요청 ID와 같은 응답' 주장은 실제 owner 증명이 아니다.
2. 기존4Pod owner·source/model/ENV·예열·출력/시간 계약을 재사용한 공개 닫힌 Responses 전송을 만든다. 실측이 없는 lab/source/image·native0.32 gate는 유지한다. 응답원문/자격/객체ID 원문은 전송·일시 구조검사에만 쓰고 정제 hash/선택 enum만 저장한다.
3. A/B 자신의 객체 정상 대조와 서로의 객체 조회, cancel 가능한 소유 background 객체·취소 전후 효과, previous 연결, 동일 ID 덮어쓰기 효과를 각각 수집한다. 조회 허용과 취소·변경 허용은 별개다. 정상객체가 없거나 store 활성·기능 지원·owner/실제효과가 불명하면 해당 결과는 UNKNOWN이다.
4. 실제 source/record·request/call/session/PID/start와 owner/조직/공유 정책을 결합해 분석하고, 기존 CLI/EVD/RES/RPT/verify/반출·복원·저장팩 정확Ref를 연결한다. 필수 분기/원팩31·다른76정의는 유지하며 닫히지 않은 동작을 전체 완료로 기록하지 않는다.
5. 정상·확인된 위반·누락·store 비활성·동기 취소·교체/restart·재확보·후속 수집/첨부·정리 오류를 준비한 뒤 소스 고정→직접/동일계약/불변식·Linux/설치→초안PR/정확HEAD CI/인계를 마감한다.

POL에는 정확ENV/target·선택source/image/model revision과 전체 파일 inventory·run/window, 주체 A/B와 실제 principal 연결·작업별 owner/공유/취소 허용 기준·선택origin과 동작·수집/보존/소유정리 책임이 필요하다. 실행 중 읽은 hash·HTTP200·정적 bearer 이름을 승인값이나 인증된 principal로 올리지 않는다. 기업 기준은 임의로 채우지 않는다.

작업 규모는 새 관측/닫힌 전송/전용 소비를 포함하는 중간 규모 묶음이다. read/creation과 cancellation/chain/overwrite의 확인 가능한 기능을 순서대로 고정 제공하되 전체 SC02-03은 유지한다. 저장소 소유권·실제 operation 효과의 독립 자료가 없으면 전축/각 하위결과의 의미를 정확히 남기며 helper/등록/EXAMPLE만으로 실제 완료를 추정하지 않는다.

실제 실행에는 전용 시험 객체·두 유효 주체·정상 source/기능지원·store/background 확인·Gateway/직통 허용표·활성worker/세대·정리 책임이 필요하다. 검토한 router에는 임의 DELETE 정리 경로가 없으므로 새 삭제 API를 가정하지 않는다. 공유 서버 재시작이나 제3자 객체 취소를 정리 수단으로 쓰지 않는다. 원GPU창종료/추가Start0/마지막확인stopped/46.66USD 상한을 유지하고 새 실제 기동은 하지 않는다. 실제 대기가 준비된 공통 지원과 다른 담당 개발을 막지 않게 한다.

registry `stored_responses:kubernetes`/실행기0.1.0, 닫힌 전송·소유범위 ledger·선언 효과 분석과 명시 DRAFT 팩을 연결했다. 아래는 구현·시험 초안이며 소스 고정 후 영향/설치 검증 전이다. 실제 실증과 전체REQ06 완료를 뜻하지 않는다.

```toml
[executors]
stored_responses="kubernetes"
[adapter]
name="vllm"
[stored_responses]
target="<검토한 4Pod target.json>"
roles={A="REQ06_A",B="REQ06_B"}
```

공개 `KubernetesStoredResponsesHTTP.perform(origin,actor,owner,kind,action,stage)`만 사용한다. 임의 request/path/body/object ID 입력이나 부모의 일반 `__call__`은 거부한다. 프로필은 `kubernetes-owned-stored-responses-http-v1`, 수집은 `kubernetes-stored-response-ownership-v1`이다. lab=true/vLLM0.31·명시모델 repository/revision/alias·기존검토이미지 경계에서 두 credential은 서로 다른 ASCII 출력문자1~256자다. env변수 이름은 명시하며 raw credential은 stdin에만 전달한다. 짧은 값으로 모든 hash를 전역 치환하지 않고 닫힌 출력 필드로 원문 비저장을 보장한다.

POL은 `stored_environment_sha256`, `stored_target_sha256`, `stored_session`(32hex), `stored_identity_policy_sha256`, `stored_api_router_source_sha256`, `stored_serving_source_sha256`, `stored_protocol_source_sha256`, `stored_validity_start/end`, `stored_origins`, `stored_operations`, `stored_retention`이다. 3source SHA는 위 검토 판본과 정확히 일치해야 한다. aware 창은 최대600초이고 각 요청 전후 검사한다. origins는 gateway/direct, operations는 read/cancel/previous/overwrite의 명시 중복 없는 선택이며 미선택은 missing이다. retention은 `retain-owned-lab-objects`를 명시한다. 실행 중 발견한 hash·store활성·정책·principal을 자동 승인하지 않는다.

API 실제 통신은 external_probe→선택 Gateway 또는 external_probe→선택 engine Pod다. direct를 선택하면 기존 direct_targets에도 같은 external/engine endpoint가 있어야 한다. trusted probe를 외부 호출자로 가장하지 않는다. 같은 namespace/Pod/container/image/restart/start·run/ENV/model 결합을 모든 요청 전후 확인한다. Gateway→engine 실제 내부 통신과 주체 전달은 여기서 실증되지 않았다.

객체 이름은 정책 session을 재사용해도 매 실행 fresh UUID로 분리한다. read/previous/overwrite 각A/B, cancel-normal/cancel-target 각A/B로 origin당10개·총20개가 상한이다. 생성은 고정 hello/1token/temperature0/streamfalse와 명시store, cancellation용만backgroundtrue다. 현재 실행이 예상한 ID/모델/상태/response 구조의 생성 응답과 owner별 정상 GET이 없으면 다른 주체의 그 객체를 조회·취소·이어쓰거나 덮어쓰지 않는다. 실제 owner 인증과는 다른 프로브 범위 제한이다.

취소는 별도 자기 cancel-normal 객체의 pending 상태→API cancel→owner GET cancelled 대조가 필요하다. 타 주체 cancel-target은 그 대조와 해당 대상의 queued/in_progress가 있어야 한다. previous는 각 호출자의 자기 이전 응답 대조 후, 동일 request_id 덮어쓰기는 자기 overwrite→후속 GET의 정해진 marker/내용hash 대조 후에만 다른 주체의 소유 시험 객체에 시도한다. 같은조건이 공개 전송에도 적용된다. 조회·취소·연결·덮어쓰기와 두 origin 결과는 각각 보존한다.

nonstored continuation은 같은실행의 별도 ID로 최대8개다. 전체 최대128 HTTP 요청, 요청당10초·전체300초, stdout2MiB·본문128KiB·response output16항목 한도다. 본문/객체ID/본문metadata/예외는 저장하지 않는다. 고정 enum·정해진 probe marker·비교용 canonical metadata/output hash만 보존한다. 단순 해시 일치를 principal/worker 소비 증명으로 올리지 않는다.

소유 cancel-target 정리는 같은 세대·소유 ID·pending 상태가 검증된 범위만 시도한다. API cancelled와 독립 worker 종료는 별개다. 저장된 결과는 명시 retention에 따라 hash 목록으로 남기고, DELETE·공유서버 restart·EC2 변경을 수행하지 않는다. 교체/restart/실행창 만료/부모 정리 불명 이후에는 추가 접근을 막고 앞선 생성·요청·효과를 보존한다. ABORTED/UNKNOWN·후속첨부/정리오류·원문비저장을 기존 제품 경로에서 검증한다.

`tools/build_kubernetes_stored_responses_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새 폴더>`는 다른76정의 bytes 및 REQ06 원질문·기준·모든하위동작을 보존한 revision4 DRAFT 팩을 만든다. CLI select/run/judge/report/verify/export/restore·저장팩 정확6Ref 재판정을 사용한다. 모든 축 UNKNOWN, normal_control.succeeded=null, producer/owner/worker/scope=false다. HTTP 거부/허용·객체표시·후보만으로 실제 권한 FAIL/PASS나 전체 SC02-03을 완료로 올리지 않는다.

- 제품 검증 마감: 정확bef4e0e/검토pind5f06b2의Windows 187 passed, 1 skipped, 1 deselected in 52.80s·부모53.596초,동일1225bytes Linux 642 passed, 1 deselected in 84.25s (0:01:24)·부모84.71초,source/wheel/격리설치201파일일치·설치CLI4/정확24Ref왕복7.906초를확인했다. 첫610통과/31실패·원소스와실패로그를보존했고POL지원타입/list[str]·시험역할사전격리를보수했다. 소유HTTP실제소켓5요청·두origin/네동작의정상·선언위반후보·store비활성/동기취소·누락/교체·후속오류·원문비저장을확인했다. 검증중변경/새제외/기대값완화없음,B309전체pin·B원c9/A2선정12/B원본27/native8~11/IPC/원팩31을보존한다. 실제principal/owner/생산자·worker효과·기업정책/실제환경·전체REQ06/부모A는미완료이며전축UNKNOWN·normalnull·신뢰/전체범위false다. 결과문서만갱신하고초안PR/정확HEAD CI/인계를마감한다.
