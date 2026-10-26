# 실행·실증 안내

## 첨부 읽기·저장 실패와 부분 증거

실행기가 유효한 draft를 반환한 뒤 첨부 파일 읽기·Store 저장이 실패하면, EVD는
`ABORTED`와 고정 `ARTIFACT_PERSISTENCE_INCOMPLETE` 진단을 남긴다. 이미 수집한 정식 관측,
실제 정상/도전 사실, 성공한 첨부 참조를 빈 값으로 바꾸지 않는다. 원문 예외·실패 경로를
진단에 넣지 않는다. 실패한 첨부를 성공으로 표시하거나 기존 파일·객체를 덮어쓰지 않는다.

기존 PASS 관문이 실행 미완료를 보류하고, 기존 판정기는 독립적으로 확인한 위반을 유지한다.
특정 판정에 실제 첨부가 필요하면 그 근거가 없는 범위는 계속 UNKNOWN이다. 출처는 최대
DECLARED이며 EXAMPLE은 그대로다. 부분 사실 보존은 전체 실행·증거 수집 완료의 증명이 아니다.
이 출처는 EVD의 `data_origin`이며, ABORTED 결과의 `verification_level`은 기존 규칙대로 null이다.
ABORTED 증거는 기존 이월 관문에서 거부하며 과거 저장 EVD/RES·팩에는 새 의미를 소급하지 않는다.
과거에 이미 빈 관측으로 저장된 EVD는 이 수정이나 재판정만으로 잃은 사실을 복원할 수 없다.

## PLT-07 명시 금지 TCP 연결 한 분기

`boundary_egress`는 기존 `guard select/run/judge/report` 경로에 연결한다.
첫 분기는 이미 준비된 단일 소유 대상의 audit 구간이다. container ID·UID·netns·정책 구간이
바뀌면 새 명시 정책이 필요하며, 두 이미지/재기동에 같은 식별자를 자동 이월하지 않는다.
`examples/boundary-egress.toml`은 실행 설정 양식이며 회사 정책값을 제공하지 않는다.
새 정책 양식은 현재 팩의 `guard policy template`로 만들고 PLT-07의 11개 필수 값을 사람이
명시해야 한다. subject의 정확한 container ID·UID·netns와 별도 owned receiver ID·IPv4·port,
금지 protocol/작업, aware ISO 적용 구간을 관측값이나 다른 점검 정책으로 보충하지 않는다.

첫 프로파일 `explicit-forbidden-egress-v1`은 `subject_scope=all_processes_in_container_uid_netns`,
`forbidden_protocol=tcp`, `forbidden_operation=tcp_connect`만 지원한다. 이는 해당 UID+netns의
모든 프로세스를 포함하는 정책을 입력한 경우다. worker·SDK·특정 실행 파일만의 정책을
이 프로파일로 바꾸지 않는다. 목록 밖 자동 금지, 허용 목록·유효 기간·유예의 기본값은 없다.

송신 target과 receiver target은 이번 run의 별도 pinned Linux 컨테이너여야 한다.
host/다른 컨테이너의 PID·network 공유나 proc/cgroup을 가리는 mount를 거부하며 명시 private
cgroup namespace 프로파일을 요구한다. 소유 CID/image ID→RepoDigest/start와 bridge endpoint를
전후 대조한다. 수신처는 패키지의 standalone `egress_receiver.py` source를 정확한
`python3 -u -c <source> <port>` 명령으로 실행하고 새 `/guard-observations/egress.jsonl`을 만든다.
실행기가 이 명령·실제 PID/start/UID/netns/listen socket·파일 수집 경계를 대조한다.
수신처 준비는 승인된 격리 환경에서 수행하며 제품 실행기는 컨테이너·이미지·엔진을 만들지 않는다.

송신 helper는 명시 숫자 UID로 실행해 실제 UID·netns를 검사한 뒤 소유 수신처 한 곳에
32바이트의 무해 일회성 상관 표식을 보낸다. 표식 원문은 증거에 남기지 않는다.
부모의 전체 예산과 검증된 Linux time namespace의 보수적 기한을 연결 전·송신 전에 확인한다.
송신 쪽 실제 socket과 수신 쪽 reverse tuple·상관 해시·실제 수신 기록·정책 적용 구간이
같은 사건으로 확인된 경우만 `forbidden_egress_successes`에 남긴다. 서로 다른 netns의
resource tuple hash를 같다고 비교하지 않는다. UTC 시계가 맞지 않으면 허용 오차를 만들지 않는다.
ACK는 부가 수집이다. TCP 연결·실제 표식 수신의 독립 근거가 완결됐다면 ACK 실패나
연결 종료 뒤 peer 메타데이터 조회 실패로 과거 관측을 지우지 않는다. 정식 증거는 EVD의
구조화 관측이며 중복 summary를 필수 Store artifact로 등록하지 않는다.

이 독립 금지 연결 성공은 보안 FAIL이다. 거부·timeout·미수집은 보안 UNKNOWN이며 전체 차단
성공으로 보지 않는다. 다른 후속 수집 오류로 이미 검증한 위반을 지우지 않는다. 정상 업무·
전체 송신 제한·worker/SDK/usage·DNS/redirect/TLS·정책 단절·만료 집행·우회 원인은 미검증이다.
보안 PASS와 기능·성능 PASS 규칙은 없고 전체 `egress_scope_complete=false`를 유지한다.
EXAMPLE 대역·소유 시험 socket·격리 Docker stub은 실제 vLLM 실증으로 집계하지 않는다.

## LIF-04 느린 수신과 독립 자원 부분 관측

`guard capture-stream-pressure`는 기존 SSE 수집에 명시적인 `slow-reader-v1`을 연결한다.
기존 `read_delay_ms`는 최초 한 번 지연한다. 새 프로파일은 제한된 chunk를 읽을 때마다
기다리고 정한 nonempty read 횟수 뒤 클라이언트 연결을 닫는다. 종료는 worker 취소 증명이 아니다.

```text
guard capture-stream-pressure --env "<SEALED-ENV.json>" --target "<OWNED-LAB-TARGET.json>" --settings examples/stream-pressure-settings.json --output "<NEW-EXTERNAL-ATTACHMENT.json>"
```

기본 실행은 입력 검사만 하고 Docker·요청·출력을 만들지 않는다. `--execute`는 소유 중인
격리 대상에 전후 정상 요청과 느린 SSE/병행 정상 요청을 보낸다. 엔진이나 daemon을 기동하지 않는다.
target은 명시 boolean `lab`(stub이면 true, 기존 managed 실제 엔진이면 false), 정확한 run label·
pinned 엔진 이미지·명시 port·trusted network가 필요하다. HTTP host는 컨테이너 DNS 이름 또는
소유 endpoint의 IP다. 실행 시 network의 소유 라벨·ID와 engine CID/endpoint/attachment 주소를
대조하고 실제 요청은 검증한 IP로 고정한다. `probe_image`도 로컬 pinned Linux 이미지여야 한다.
이미지를 조회한 뒤 실제 probe 실행은 `--pull=never`로 제한한다. 엔진과 다른 새 probe 실행 ID로
이번 probe만 정리하며 기존 컨테이너·network는 정리하지 않는다.
부모의 남은 전체 예산을 Docker 시작부터 적용하고, 시작 전 자원 표본의 Linux 시간 namespace와
보수적인 절대 기한을 probe의 연결 전·HTTP 전에도 확인한다. 같은 시계를 확인하지 못하거나
기한이 만료되면 HTTP를 시작하지 않는다. 수집 종료 후 이번 probe 정리는 별도 최대 30초다.

자원 관측은 host/다른 컨테이너의 PID·network 공유를 거부하고 명시 private cgroup namespace와
source를 가리는 mount 부재를 요구한다.
읽기 전용 helper가 PID/start·namespace·RSS·cgroup v2 memory.current·port별 TCP queue/inode/owner를
상한 안에서 수집하고 전후 컨테이너 신원을 대조한다. probe와 표본의 Linux 시간 namespace가
같고 표본 측정 구간이 실제 요청/close 구간에 맞을 때만 전·중·후 구간 근거로 센다.
시간 namespace 미지원·요청 전 표본만 존재·누락·세대 변경은 미확인·고정 진단으로 남긴다.
시간 namespace가 없고 플랫폼 monotonic 시계의 해상도가 낮으면 새 읽기 프로파일의 상대 시간만
고해상도 `perf_counter`로 기록한다. namespace 연결은 계속 미확인이고 기존 deadline 시계는 유지한다.
RSS·cgroup은 총량이고 queue는 표본 시점의 socket 관측이다. 연결별 buffer 비용·worker 취소·회수
완료를 이 값이나 EOF/HTTP/DONE으로 확정하지 않는다. 원문 응답·토큰·headers·예외 원문은 첨부하지 않는다.
ESTABLISHED의 queue만 TCP sequence 바이트로 표시하고 LISTEN의 수신 queue는 대기 연결 수로
구분한다. 나머지 상태의 queue 바이트는 null·미완전 진단으로 남긴다. 이 값은 socket 메모리 점유량이 아니다
([Linux IPv4 구현](https://github.com/torvalds/linux/blob/master/net/ipv4/tcp_ipv4.c),
[IPv6 구현](https://github.com/torvalds/linux/blob/master/net/ipv6/tcp_ipv6.c)).
프로세스 RSS에는 공유 페이지가 각 프로세스에 집계될 수 있고 cgroup 총량에는 관측 helper도
포함된다. 이 관측 비용을 임의로 빼거나 총량 차이를 연결별 비용으로 바꾸지 않는다.

예시 settings는 기술적 시험 상한이며 기업 메모리·회수 예산이 아니다. 출력은 항상 세 축 UNKNOWN,
`official=false`·`product_connected=false`다. LIF-04 CheckDef의 executor/judge와 팩27은 유지하며
새 자동 판정·전체 분기 실증으로 집계하지 않는다. 실제 vLLM 실증은 별도 보류 상태다.
직접 주입한 probe/sampler는 오프라인 시험용 경로이며 출처를 EXAMPLE로 제한한다.

## REQ-02 소유 경로 관측 파일 수집

`guard capture-route-observations --help`로 설치 제품에서도 기존 파일 수집을 사용할 수 있다.
봉인 ENV와 명시한 target/run ID·소유 컨테이너·pinned 이미지를 준비한다.

```text
guard capture-route-observations --env "<SEALED-ENV.json>" --target "<TARGET.json>" --container "<OWNED-PRODUCER>" --image "<REPOSITORY@sha256:DIGEST>" --path "/guard-observations/route.json" --output "<NEW-EXTERNAL-ATTACHMENT.json>"
```

기본 실행은 입력 검사만 하며 Docker 생성·수집·출력을 하지 않는다. `--execute`는
기존 소유 컨테이너의 파일을 읽기만 하고 엔진 기동·HTTP 요청·판정을 하지 않는다.
출력은 기존 부모 아래 저장소 밖의 새 파일이어야 하며 링크·junction과 덮어쓰기를 거부한다.
독립 생산자·인가·요청 ledger가 검증되지 않아 항상 UNKNOWN·`product_connected=false`다.
EXAMPLE ENV는 EXAMPLE로 보존하며 그 외 입력도 생산자 인증 전에는 DECLARED 첨부다.
기존 `tools/capture_route_observations.py`도 같은 수집 함수를 사용한다.

## REQ-05 Gateway 기록 수집 — 최종 엔진 모델 판정은 미연결

`guard capture-model-authorization --help`로 제품 명령을 확인한다. 기존 collect/import의
봉인 ENV/POL JSON과 명시 context·target을 사용하며, 기존 봉인 파일을 직접 고쳐 쓰지 않는다.
POL의 새 판본에 사람이 `values.REQ-05.principal_model_grants`를 입력해야 한다.
이 전용 수집 입력은 CheckDef의 자동 정책 양식 등록을 뜻하지 않는다.

```powershell
guard capture-model-authorization --pack packs/PACK-BASELINE --env "<SEALED-ENV.json>" --policy "<SEALED-POL.json>" --context "<CONTEXT.json>" --target "<TARGET.json>" --gateway-container "<OWNED-GATEWAY>" --gateway-image "<REPOSITORY@sha256:DIGEST>" --gateway-path "/logs/gateway_records.jsonl" --output "<NEW-EXTERNAL-ATTACHMENT.json>"
```

기본 실행은 입력만 검사하고 출력·Docker를 만들지 않는다. 별도 승인된 소유 자료를
실제로 읽을 때만 `--execute`를 붙인다. 이 옵션도 기동·요청 발생·판정은 하지 않는다.
최종 엔진 모델 생산자가 없어 수집 성공 후에도 UNKNOWN·비공식이며 실행/인가 검증
완료가 아니다. 입력 필드·상한·남은 생산자와 PLT-07 시험 조건은
[관측 보강](AUTOMATION_HARDENING.md)의 REQ-05/PLT-07 절을 따른다.

> 2026-10-26 현행 작업 후보: 팩27/DRAFT, 보안 규칙 연결13개·SUP-03 관측 전용1개다.
> PLT-02 Unix IPC 부분 연결·선택 native 파일 수집의 범위는
> [관측 보강](AUTOMATION_HARDENING.md)을 따른다. 기존 점검 실제 재실증은 변경 영향이
> 있을 때 해당 범위만 수행한다. 아래 팩21·22·GUA PR #28~#31 기록은 과거 이력이다.
> BYUL PR #6~#9의 병합 목적지는 #5의 head 브랜치였으며 main 반영은 별도 통합 PR이 필요하다.

## v1 통합 후보와 검증 이력

**2026-10-25 현행**: PR #30·#31이 main(`fb0c103`)에 병합됐다. 실측은 실행 조건별로 구분한다. (1) PR #30 기본 실행(`length_cause_evidence` 없음): 후보 REQ-09 보안 UNKNOWN으로 수락 실패. (2) PR #31 후속 실행(`length_cause_evidence = true`, 검토 프로파일과 일치하는 0.32.0 CPU 이미지): 다섯 점검 양쪽 PASS·NO_CHANGE로 수락 성공. (3) PLT-04 후보 단독 시험: 비어 있지 않은 EXAMPLE 후보로 absent·denied PASS / readable FAIL / symlink·directory UNKNOWN. (4) 두 이미지 update의 PLT-04: 후보 `[]`인 부분 범위. 상세는 [후속 실측](UPDATE_VALIDATION.md#후보-길이-거부-원인자격-후보-후속)이다.

2026-10-24 PR #30 당시 기록: PR #29의 [v1 통합 검증](UPDATE_VALIDATION.md#v1-통합-후보-검증)은 main `641f12f`에 반영됐다. 당시 후보는 PACK-BASELINE 22/DRAFT·77개 점검 정의·정책 22점검/50경로·자동 연결 11개·REQ-09 revision 6·PLT-04 revision 3·adapter 0.4.2·declared 0.14.0이다. [새 계약 실측](UPDATE_VALIDATION.md#req-09plt-04-판정-의미-보완-후보)은 양쪽 실제 실행·반출/복원·재판정을 완료했지만 후보 REQ-09 보안 UNKNOWN으로 수락 실패다. update 종료0·harness 종료1·558.134초를 구분한다. 나머지 4개는 양쪽 보안/기능 PASS이며 PLT-04는 후보 빈 목록의 부분 범위다. PR #28/#29의 팩21 성공을 소급하지 않는다. 자동 연결 수는 실제 전체 분기 검증 수가 아니며 공식 출시·기업 승인은 별도다.

## REQ-09·PLT-04 판정 보완의 입력과 확인

REQ-09의 `max_input_tokens`·`rejection_codes`·전체 허용 endpoint/입력 형식은 기업 정책대로 입력한다. HTTP 상태·오류 본문·실제 소비 근거는 서로 다른 관측이다. `challenge_rejection_results`가 verified여야 확인된 거부 근거가 되며, HTTP 200 오류 객체는 실제 수용·소비나 FAIL의 근거가 아니다. 원시 2xx의 `accepted_challenges`를 수용 증명으로 해석하지 않는다. 자동 실행에서 소비가 미계측이면 `challenge_consumption=unknown`이며 `confirmed_input_violations`를 꾸며 채우지 않는다. 거부도 위반도 확인하지 못한 사례는 UNKNOWN으로 남긴다.

PLT-04의 기존 `forbidden_env_names`·`forbidden_mount_paths`는 유지한다. 후자는 bind/volume 목적지가 선언한 금지 경로와 상위·하위로 겹치는 것 자체를 금지하는 정책이다. `forbidden_mount_violations`는 그 정책의 위반이며, 실제 자격 전달·파일 읽기 성공을 뜻하지 않는다. 기존 금지 정책을 비워 FAIL을 없애지 않는다.

선택 입력 `credential_candidate_paths: list[str]`는 별도의 절대 경로 목록이다. 생략은 선정의 새 필수 키 오류가 아니지만 후보 관측이 미수집되어 PASS는 UNKNOWN으로 보류된다. 양식의 `null`은 실제 목록이 아니므로 미설정으로 둘 때는 키를 생략한다. 명시적인 `[]`는 요청한 후보가 없다는 뜻이며 기존 환경변수·마운트 정책의 부분 검증만 PASS 후보가 된다. 실제 기업 후보 목록을 줄여 통과시키지 않는다. 마운트 금지 목록에서 자동 복사하지 않는다. 실행기는 후보 파일 본문을 읽지 않고 worker와 수집 주체의 UID/GID/groups·namespace·capability·LSM 및 전후 메타데이터 안정성을 대조한 뒤 `os.access`의 읽기 권한을 확인한다. 후보 경로는 중복 없는 최대 32개·경로당 UTF-8 128바이트의 정규 절대 경로만 지원한다.

`credential_candidate_results`의 absent/denied/candidate_readable/unknown/shadowed와 `credential_candidates_complete`·근거를 함께 확인한다. candidate_readable은 위 주체/메타데이터 계약으로 확인한 읽기 권한이며 실제 본문 읽기·자격 내용·자격 사용의 인증이 아니다. 확인된 금지 환경변수·마운트 위반 또는 candidate_readable은 독립 FAIL이다. 상위 마운트만으로 파일 존재·worker의 접근을 추정하지 않는다. 주체 불일치·권한 메타데이터 미확인·symlink·가려진 중첩 마운트는 불명확한 상태로 보존한다. 운영 비밀 원문은 읽거나 수집하지 않는다. 실험에서는 무해한 canary를 사용할 수 있으나 canary 내용/접근 인증 프로파일은 지원하지 않는다. 이 한정 시험은 실제 자격 전체·동적 provider·issuer의 사용 권한을 입증하지 않는다. 정책 템플릿은 실제 기업값을 자동 채우지 않으며 EXAMPLE/DRAFT·official=false를 유지한다.

**알려진 한계(LSM)**: 후보 관측은 각 프로세스의 `/proc/<pid>/attr/current`(AppArmor·SELinux 등 LSM 보안 컨텍스트)가 비어 있지 않아야 주체를 확인한다. LSM이 없거나 컨텍스트를 읽을 수 없는 호스트에서는 `WORKER_SUBJECT_UNVERIFIED`로 모든 후보가 unknown이 되고 PASS는 UNKNOWN으로 남는다. 설계상 보수적 처리이며 그 호스트에서 PLT-04 후보 관측은 지원하지 않는다.

[PR #28의 두 이미지 실측](UPDATE_VALIDATION.md#실측-결과--c-후속-2026-10-24)은 vLLM API 0.31.0/0.32.0 CPU에서 기존 5개 점검의 양쪽 보안·기능 PASS와 수락·반출/복원·저장 팩 재판정 일치를 확인했다. 이 기록을 통합판에서 새로 실행한 결과로 표시하지 않는다. 비교는 모두 NO_CHANGE여서 실제 차이 탐지는 미증명이며 성능 UNKNOWN·정책/팩 DRAFT·official=false다.

실제 EVD 10개의 출처는 MEASURED지만 REQ-01/REQ-03 Result의 검증 수준은 DECLARED다. REQ-01 일부 상태 변경 경로는 HTTP를 보내지 않고 TCP 차단 근거를 사용한다. 모든 handler 실행·기업 승인·11개 점검 모든 분기의 실증으로 확대하지 않는다.

## 현행 정책·로그 증거와 설치 검증

[이번 보강](POLICY_RELIABILITY_BATCH.md)은 REQ15 수동 계약·로그 수신 상한/출처·저장 로그 재검증·설치본 CI·준비 안내를 연결한다. [로그 도구](LOG_EVIDENCE.md), [REQ15 입력](POLICY_M14.md), [wheel 검증](INSTALLED_VERIFICATION.md)을 따른다. prepare-* JSON의 guidance는 원인/다음 조치를 추가하며 ready와 점검 지원·실제 판정을 구분한다. 입력 실패 때 의존 검사는 미확인이고 총 메모리가 여유 메모리를 뜻하지 않는다.

## 두 이미지 CPU 업데이트 비교

[두 이미지 비교 안내](UPDATE_VALIDATION.md)에 준비 입력·Windows 명령·수락 기준을 정리했다. `python -m guard.update_validation`의 기본은 읽기 전용 준비 검사이며 실제 기동은 `--execute`가 필요하다. 분석은 `--analyze-existing`으로 원본을 보존한다. 최소 다섯 점검의 보안/기능 구체 판정과 반출·복원·저장 팩 재판정을 확인한다. 후보 미준비로 시험하지 못했던 [도구 구현 당시 기록](UPDATE_VALIDATION.md#이번-검증-결과)과 이후 위 PR #28의 성공 기록을 구분한다. 준비 성공은 시험 수락이 아니다.

## 현행 자동화 보강과 지원 범위

직전 [자동화 보강](AUTOMATION_HARDENING.md)의 기록은 팩20/DRAFT·REQ-11 revision 3·정책21점검/46경로·declared/0.12.0 기준이다. [정책·로그 보강](POLICY_RELIABILITY_BATCH.md)의 팩21/DRAFT·정책22점검/49경로·declared/0.13.0은 그다음 이력이며 현행은 위 팩22 판정 보완 후보를 따른다. 자동 연결11개와 REQ-11의 생성 출력 귀속 미검증·전체 보안 PASS 보류는 유지한다. 과거 1~3차 CPU 실측과 새 합성 회귀를 합산하지 않는다.

단일 엔진은 prepare-validation → validate, 두 환경은 prepare-update → update를 사용한다. 새 준비 출력의 side별 check_support에서 SUPPORTED/UNSUPPORTED/UNKNOWN·이유·한계를 읽는다. ready는 입력/호스트 준비 상태이며 모든 점검의 지원이나 PASS가 아니다. 유효하지만 미지원인 정책은 지원 안내로 구분하고 다른 점검을 제거하지 않는다. COMPLETED/MEASURED는 전체 챌린지나 생성 출력 귀속의 입증 수가 아니다.

[실제 CPU 3차 기록](AUTOMATION_PHASE3.md)·[선별 증거](evidence/automation-phase3-20261023.json), [명시적 real 설정](../examples/windows-cpu-binding-logs.toml)·[EXAMPLE/DRAFT 정책](../examples/automation-binding-logs-policy.yaml)을 참고한다. 실제 ENV·로컬 snapshot 경로가 필요하며 모델/이미지 수신은 자동으로 하지 않는다. 실행은 새 store/output으로 하나씩 진행하고 기존 컨테이너·모델·증거를 보존한다. 기본 guard.toml은 fake 시연 설정이다. CPU 성능 UNKNOWN·팩/정책 미승인·official=false를 유지한다.

아래는 과거 단계의 명령과 실증 계약이다. 당시 수치·팩 revision·지원 환경 설명을 현행 완료 주장으로 해석하지 않는다. 당시 PR 통합 선택지는 [보강 계획](AUTOMATION_HARDENING.md)의 이력이며 현행 두 이미지 수락 기준과 실측 결과는 위 UPDATE_VALIDATION 안내를 따른다.

## 기존 실행 안내와 이력

당시에는 Python 3.11과 로컬 Docker를 사용했다. Windows Desktop/WSL2의 제한 CPU 제품 실행은 과거 1~3차 기록에서 확인했다. 현재 변경의 직접 검증은 위 보강 문서에 별도로 기록하며 macOS·임의 하드웨어·GPU 전체의 실증 완료로 확대하지 않는다.
실제 엔진 시험에는 사전에 받은 HF 모델 캐시와 호환 아키텍처의 pinned 이미지가 필요하다.
정책/팩 DRAFT이면 공식 보고서가 아니다.

## 설치와 기본 검증

현행 개발·guard 실행·CI는 64비트 Python 3.12(`>=3.12,<3.13`)를 사용한다. 위 3.11 환경은 과거 실증 기록이며, 대상 vLLM 이미지의 내부 Python·digest·검토 프로파일은 호스트 버전 전환과 독립이다. 기존 3.11 가상환경과 원본 증거는 덮어쓰지 않고 새 경로에 3.12 환경을 준비한다. [전환 검증](INSTALLED_VERIFICATION.md#python-312-전환-검증)을 따른다.

```sh
uv sync --frozen
uv run guard --help
uv run pytest -q
uv build --wheel
```

기본 테스트는 Docker/engine을 제외한다. 새 실행기는 실제 엔진 검증과 구분한다.
실증의 store와 output은 기존 out/5b 밖의 새로운 경로를 사용한다.

M10의 REQ-09는 두 required 정책 `allowed_endpoints`·`allowed_input_formats`도 필요하다.
전체 승인 목록이 정의의 단일 engine POST/token_ids 표본과 일치할 때만 지원하며, 목록을 줄여 맞추지 않는다.
빈 양식은 값을 채우지 않고 미입력은 NOT_RUN, 미지원 범위는 요청 전 ABORTED다.
수동 증거는 `evidence template --include-automated`·비공백 override_reason과 두 generic 범위 관측을 사용한다.
[M10 계약과 검증](POLICY_M10.md)을 확인한다. 실제 엔진/두 이미지 실증은 별도다.

## 기존 audit

새 기업 정책 파일은 아래 명령으로 만들고 설명 주석을 따라 실제 결정값을 입력한다.
빈 양식도 기존 로더/선정에서 읽히지만 선언된 필수 키가 없으면 `NO_POLICY_VALUE`다.
기존 출력 파일은 덮어쓰지 않고 입력 팩 내부 출력도 거부한다. [입력 계약·설명 출처](POLICY_TEMPLATE.md)를 함께 확인한다.

```sh
uv run guard policy template --pack packs/PACK-BASELINE --out my-policy.yaml
```

```sh
uv run guard select --pack packs/PACK-BASELINE --env examples/env.yaml --policy examples/policy.yaml
uv run guard run --plan PLAN-ID
uv run guard judge --plan PLAN-ID
uv run guard report --plan PLAN-ID --json report.json --html report.html
uv run guard verify
```

기본 guard.toml은 FakeExecutor 시연이다. 실제 엔진 결과로 해석하지 않는다.

수동 증거는 `guard evidence template --plan PLAN-ID --out manual-v2.yaml`로 생성해 입력한다.
SUP-14/LIF-07은 항목·case·첨부 연결이 필요하며 /1 읽기 호환과 재판정 영향은
[M4 수동 증거 계약](POLICY_TEMPLATE.md)을 따른다. 필수 목록만 입력했다고 PASS가 되지 않는다.

## Managed 업데이트

[managed 설정 예시](../examples/update-managed.toml)를 복사하고 환경/정책을 준비한다.

각 ENV에는 engine.name/version/image_digest(repository@sha256:64hex),
model.id/revision/inventory_sha256, runtime.gpu_count를 명시한다.
HF 캐시는 `models_dir/hub/models--ORG--MODEL/snapshots/REVISION`을 사용한다.
model inventory는 snapshot 전체 파일의 정렬 목록 해시다.

```sh
uv run python tools/model_inventory.py /cache/hub/models--ORG--MODEL/snapshots/REVISION
uv run guard update --pack packs/PACK-BASELINE --baseline env-a.yaml --candidate env-b.yaml --policy policy.yaml --config update.toml --store .guard-update --output out/updates
uv run guard verify --store .guard-update
```

각 side는 다른 컨테이너/target으로 순서대로 기동·실행·정리된다.
ENV의 버전/이미지 digest와 실제 준비된 엔진이 다르면 ABORTED로 기록한다.
기동 실패가 있어도 가능한 다른 side와 보고서를 남긴다.
종료 코드 0은 파이프라인 실행 성공이며 보안 PASS를 뜻하지 않는다.
기동/실행/필수 자동화 미실행 실패는 종료 코드 1이다. 수동 전용 점검은 보고서에서 UNKNOWN으로 남는다.

CPU ENV 예시(실제 확인한 환경값으로 작성):

```yaml
runtime:
  gpu_count: 0
  cpu:
    architecture: x86_64
    model: REPLACE_WITH_ACTUAL_CPU
    system: Linux
    kernel: REPLACE_WITH_ACTUAL_KERNEL
    threads: 2
```

이 값은 ENV 신고값이며 자체 하드웨어 계측 증거는 아니다. CPU 성능 축은 UNKNOWN이다.
GPU 사용 시 cpu 프로파일을 넣지 말고 실제 GPU/driver를 제공한다.

## 외부에서 준비한 두 대상

[external 예시](../examples/update-external.toml)처럼 side별 target 경로를 지정한다.
같은 target 파일을 두 side에서 재사용하지 않는다. 각 실제 환경과 target을 별도로 확인해야 한다.
각 target은 lab=false, observed_version, engine.image_digest를 포함하고 해당 ENV와 일치해야 한다.
target의 `comparison_config`에는 엔진 이미지 자체를 제외한 실제 launch/보안 조건을 기록한다.
이 정보가 없는 과거 target은 보안 시험은 가능하지만 설정 조건 미확인으로 비교 불가다.
시험 역할 키는 이름으로만 설정하고 값은 환경변수에 둔다.

## 신규 점검

PLT-04의 executor 이름은 credential_exposure다. 정책은 forbidden_env_names/list[str],
forbidden_mount_paths/list[str]이며 컨테이너 환경변수 이름과 명시적 마운트 금지를 확인한다.
팩22의 선택 credential_candidate_paths/list[str]는 위 설명대로 별도 접근 관측에 사용한다.
처음에는 실제 자격 대신 GUARD_TEST_CREDENTIAL 같은 무해한 표식을 사용한다.
자격값은 수집하지 않으며 읽기 실패가 있으면 PASS가 되지 않는다.

REQ-09의 executor 이름은 input_validation이다. 정책은 max_input_tokens/int,
rejection_codes/list[str]이며 예시는 ["400", "422"]다.
실행 설정에는 vocab_size/int와 vocab_source/str를 pinned 모델 근거와 함께 넣는다.
max_probe_tokens는 기본 4096이며 예산보다 큰 시험은 요청 전 ABORTED다.
정상 token-array [0]가 통과한 뒤 범위 밖 ID와 입력 길이를 시험한다.
다른 입력 형식/모든 경계 조합/실제 모델 범위 전체를 검증한 것으로 해석하지 않는다.
이 신규 점검 절을 작성한 당시에는 PLT-04/REQ-09를 실제 엔진 실증하지 않았다. 이후 두 점검은 위 PR #28의 두 이미지 실측에 포함됐다. 다른 환경에 활성화하기 전에는 해당 정책과 설정을 검토한다.

## 실제 두 이미지 통합 테스트

```sh
export GUARD_UPDATE_CONFIG=/absolute/update.toml
export GUARD_UPDATE_BASELINE=/absolute/env-a.yaml
export GUARD_UPDATE_CANDIDATE=/absolute/env-b.yaml
export GUARD_UPDATE_POLICY=/absolute/policy.yaml
uv run pytest -m engine tests/invariants/test_inv_09.py -q
```

조건이 없으면 해당 테스트는 사유와 함께 skip한다. managed/real 설정 및 서로 다른 이미지 digest를 강제한다.
기본 테스트의 통과 수를 이 실증의 성공으로 대체하지 않는다.

## 증거 보존·복원

update 출력에는 plan.txt와 JSON/HTML이 있고 store에는 ENV/POL/PLAN/EVD/RES/RPT와 팩 보존본/artifacts가 있다.
전용 store 전체와 실행 설정을 바이트 그대로 보관한다. 키·불필요한 환경변수 파일은 보관하지 않는다.
복사본에서 verify를 실행하고 plan.txt의 PLAN으로 judge/report를 실행한다.
현재 packs 폴더가 없어도 정상 팩 보존본이 있으면 재판정할 수 있다.
과거 팩이 없는 경우 정확한 git revision에서 꺼낸 팩을 pack import로 먼저 넣는다.
현재 저장소의 과거 exposed/isolated store에는 참조 증거 파일이 각각 1개 누락돼 있다.
원본을 확보하기 전에는 해당 저장소의 verify/완전한 재판정 성공을 보장하지 않는다.
기존 evidence를 새 버전으로 재판정하면 새 RES revision이 생기며 과거 결과는 수정하지 않는다.
정리 실패 같은 실행 생명주기 한계는 최초 update 보고서도 함께 보관한다.
judge/report 재실행은 시험 자원 정리를 다시 실행하거나 확인하는 명령이 아니다.

## AWS 실증 준비

위 Linux 절차를 재사용한다. 실제 계정/리전/자원 생성·가격 산정은 이번 작업에서 수행하지 않았다.
이미지 아키텍처·모델 캐시·CPU/GPU 조건·운영자 권한·엔진 격리·실행 시간 상한·자원 보관 기간을 먼저 정한다.
시험 종료 후 guard.run 라벨의 컨테이너/네트워크, 인스턴스, 볼륨, 로그/증거 보관 자원을 확인한다.
정리 실패 시 보고서만 보고 자원 정리까지 완료됐다고 판단하지 않는다.


## 실증 준비와 증거 묶음 명령

`guard prepare-update`로 입력과 로컬 Docker/이미지를 먼저 확인한다.
`guard evidence export/restore`로 검증된 저장소를 새 위치로 반출/복원한다.
명령·제약·5개 점검의 PASS/FAIL/UNKNOWN 사례 입력은 [실증 안내](VALIDATION.md)를 따른다.
REQ-09의 `max_probe_tokens`는 실제 경계 초과 요청(max_input_tokens+1)을 포함하는 요청당 상한이다.
probe 정리 실패는 증거를 보존하고 execution.cleanup_errors와 재판정 보고서에 남으며 명령은 실패한다.


수동 입력·대체 옵션과 반출/복원을 함께 사용하는 절차는 [통합 안내](INTEGRATION.md)를 따른다.

## M5 승인 기록 체계 입력

정책 양식의 SUP-01/SUP-02 아래에 각각 기업이 정한 approval_record_system 문자열을 입력한다. 실제 키/암호·계정 허용표를 대신 넣지 않는다. 미입력은 NO_POLICY_VALUE이며 공백은 수동 PASS를 UNKNOWN으로 보류한다. 값만 채워도 승인/배포 권한이 검증되는 것은 아니다.

기존 evidence template → 관측 설명/첨부 입력 → evidence import → judge → report 절차를 사용한다. 두 점검에는 항목별 policy_evidence를 만들지 않는다. JSON/HTML의 승인 체계 문맥·POLICY_CONTEXT·미인증 자기 신고·official 여부를 함께 확인한다. 과거 revision 11 보존 팩의 SUP-01/SUP-02는 신규 전제를 소급하지 않는다. 과거 LIF-07 추론 충돌 때문에 보류된 확인 위반은 declared 0.4.0 재판정에서 FAIL이 될 수 있으며 원본 EVD/RES는 보존된다.

OBS-11의 필수 기록·기한·시계 오차·장애 대응은 [후속 결정표](POLICY_M5_PLAN.md)를 먼저 확정한다. collector 장애 자체를 공격 성공이나 종속 시험 PASS로 처리하지 않는다.

## OBS-11 수동 검증 (/3)

정책 양식에서 OBS-11의 필수 목록과 사건별4개 정수map을 기업 기준으로 입력한다. 실제 값/담당/승인은 자동 생성되지 않는다. 다른 점검처럼 select → evidence template → 각case 관측·주장별 첨부 입력 → evidence import → judge → report를 사용한다. 필수 누락에 경보가 필요한 최초 프로파일의 적용 근거를 입력하고 적용 불명/조건부 범위는 UNKNOWN으로 남긴다.

부재는 고장 난 collector 로그만으로 확정하지 않는다. 독립 발생과 전체 조회 시작/종료·경보 수신점·시계 오차를 확인한다. 모든 시각은 timezone을 포함해 따옴표로 쓰고 소수초는3자리 이하다. 누락 관측을 OBS11 FAIL과 동일시하지 말고 정상 처리/경보 부재의 추가 근거를 구별한다. 미수신·조회 실패·창 미종료는 부재 확정이 아니다. 실제 collector 주입이나 운영 중단을 이 명령이 수행하지 않는다.

관측의 실제 필드·정책/시간 의미·구형 /1·/2 처리·보고서 제한은 [M7 계약](POLICY_M7.md)을 따른다. JSON/HTML의 AUDIT_COLLECTION 이유·미인증 신고·official 상태를 함께 확인한다. 복구 사실은 기록하지만 복구 SLA·작성자 인증·실제 다른 RES 전파는 검증하지 않는다.

## M8 usage/replay 정책 문맥 확인

현재 팩14에서 정책 템플릿을 생성하면 OBS04/05의 usage_allowed, OBS07/08의 replay_allowed가
주석으로 안내되고 실제 값은 비어 있다. [정책 입력 안내](POLICY_TEMPLATE.md)를 따라 기업이 정한 bool만 입력한다.
false는 비활성 관측이 아니며 true는 실제 구현·권한·승인 증명이 아니다. 같은 범위의 공유 값은 업무 검토한다.

기존 select → evidence template(/3) → evidence import → judge → report 흐름을 사용한다.
새 증거 payload나 실행 명령은 없다. POLICY_CONTEXT의 실제 true/false·분기 사유와 JSON/HTML의
DRAFT·official·미인증 자기 신고·자동 검증 한계를 함께 확인한다. OBS04 false / 나머지 true 외의 문맥은
신고 PASS를 UNKNOWN으로 보류하며 FAIL은 보존한다. false를 비적용이나 자동 제외로 해석하지 않는다.
전체 분기·기업 결정·미검증 범위는 [M8 계약](POLICY_M8.md)에 있다.

## M11 수동 관측 상충 처리

REQ-09의 새 입력 범위 계약에서는 신고 PASS와 저장된 보안/기능 관측이 판정표와 일치해야 PASS 후보가 된다. 상충은 UNKNOWN 사유로 JSON/HTML에 표시한다. 원본을 고치지 말고 기존 PLAN/팩으로 재판정해 새 declared0.8.0 RES를 만들면 된다. 같은 as_of 재실행은 멱등이며 수동/3·override_reason·Ref/side 보호는 유지한다. [M11](POLICY_M11.md)에 명령/검증과 미구현 캐시 계약을 구분해 기록한다.

## M12 REQ-16 수동 캐시 문맥 검토

[상세 절차](POLICY_M12.md)에 따라 새 팩16과 POL로 select → evidence template → generic 값·첨부 입력 → evidence import → judge → report를 실행한다. NO_EXECUTOR는 수동 계약 등록 후에도 남는다. 기존 /1/2 읽기와 /3, Ref/side·append-only를 보존한다. 같은 as_of 재판정은 멱등이며 과거 PLAN은 정확한 팩15 보존본으로 재판정한다. 새 팩을 과거 PLAN에 강제로 대입하지 않는다. 캐시 probe나 실제 엔진을 띄우는 절차는 이번 범위에 없다.

## M13 저장 증거 재판정

M12의 inference=unknown이 구조오류로 보류된 증거는 원본을 고치지 않고 정확한 기존 PLAN/팩16과 같은 as_of로 judge한다. 보안 근거/INV11이 충분하면 새 declared0.9.1 RES에서 보안 UNKNOWN→PASS가 가능하나 원본 신고 UNKNOWN은 승격하지 않는다. JSON/HTML에 기능 미확인과 DECLARED 한계를 남긴다. 원본·Ref·side·config hash는 그대로다. [M13](POLICY_M13.md)의 직접 검증과 REQ15 후속 설계를 따른다.
# Windows CPU 자동화 1차

단일 엔진의 제품 기동은 `guard prepare-validation` → `guard validate`를 사용한다. [Windows CPU·신규 3개 점검·기존 5개 사례 검증](AUTOMATION_PHASE1.md), [실행 설정](../examples/windows-cpu.toml), [EXAMPLE/DRAFT 시험 정책](../examples/automation-policy.yaml)을 참고한다. 아래 과거 단계의 5개 실행기 설명과 당시 실측 범위는 이력이며 현재 구현·검증 상태는 자동화 1차 기록을 따른다.
