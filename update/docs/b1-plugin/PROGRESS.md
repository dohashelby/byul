한 일: SUP-10 기존 초안을 재사용해 실제 설치 entry point·그룹별 import/factory/hook CPU 수집과 독립 FAIL 보존 projection/회귀를 구현. PR43/46·PR47 파일 보존.
검증: 계약60 passed(0.34초/부모0.63초). 실제 CPU10사례+후속10건(부모52.02초), in-memory ASGI3건. 설치 fixture/source bytes 일치. 제품 lock·원문 source 미변경.
다음: 독립 검토 가능한 plugin 기능 묶음을 본인 브랜치 커밋/PR로 올리고 정확 head CI 확인. 같은 기능 수정은 그 PR에 유지. 마감 후 공유 기록 재확인·다음 준비된 묶음 선점.
막힌 것: 제품 profile 등록/CheckDef/CLI·첨부 계약은 A 인계, 기업 승인 설치물·group별 정책·실제 runner/IO/권한·정상 생성 대상은 별도 미정. SUP-10 전체 완료/보안 PASS 아님.

## SUP-10 source CPU → EVD/RES/RPT 소비 초안 — 2026-10-29

한 일: 외부 DRAFT pack·별도 source-profile ENV/POL/PLAN·process-local executor를 사용하는
전용 integration 도구 초안을 추가했다. 기존 plugin CASES/GROUPS/PHASES·project/judge와
core/store를 재사용한다. source profile/commit과 ENV hash·engine vllm·reference version을
결합하고 runner_hook_verified=false·installed_target=null·DECLARED 상한을 유지한다.
정제 event/상태/hash만 첨부하며 원문·예외 본문·외부 관측 JSON 입력은 받지 않는다.
독립 위반은 후속 수집·source postcheck·첨부 실패와 분리해 ABORTED에서도 보존한다.

검증: private EXAMPLE callable 계약과 실제 진입 gate 계약을 분리해 **29 passed / pytest 0.72초 / 부모 wall 0.89초**를 확인했다.
collector 68db72f의 승인 literal SHA256 `9a704be6bc6b89968091db0732f4460e75f371418dc77e3c8c57f0016580ff2e`를 연결했다.
lif14 gate의 own file/function/hash1af와 기존 B1 helper85866·fullguard c0 scope를 보존한다.
실제 CPU는 두 구현을 통합한 고정 source에서 담당자가 한 번 실행한다.

첫 시험의 2실패는 초안 시험의 functional PASS 가정과 첨부 실패 주입이 선행 pack 보존까지
막은 범위 오류였다. 기존 judge의 기능축 규칙 부재에 따른 UNKNOWN(INV-18)을 유지하고,
실패 주입을 plugin 첨부에만 한정했다. 준비 명령의 cwd 중복 경로 오류도 바로잡았다.
최종 회귀는 정상·독립 위반·미수집·미지원·후속 실패·postcheck·첨부 실패, 정확 Ref 재판정과
Store 검증·원문/예외 미저장·registry 복구를 확인한다. 기능 PASS 규칙은 추가하지 않았다.

다음: collector와 공동 snapshot을 통합한 뒤 관련 직접 회귀·집중 검토·실제 CPU/정확 Ref·Store 결과를 기록한다.
막힌 것: 기업 plugin 허용 정책·실제 runner/배포 대상은 별도 경계이며 보안 PASS를 추가하지 않는다.


## SUP-10 child 귀속 검증과 보조 저장 실패 보존 — 2026-10-29

확인한 결함: 잘못된 fixture import가 남긴 이벤트도 선정 source의 허용 경계 위반으로
승격될 수 있었고, 정제 첨부에서 child별 source 검증 근거가 빠졌다. collector의 최종
`result.json` 보조 저장 실패는 이미 수집한 독립 관측까지 반환하지 못하게 했다.

수정: producer가 child의 실제 target 시작, 선정 source profile/commit, 사전 검증과 경계,
사후 검증 상태를 확인한 뒤 기존 product projection에 전달한다. 명시적인 child 귀속
불일치와 검증 누락은 UNKNOWN 입력으로 바꾸며, 원래 enum 이벤트·실제 target 시작 여부·
정제한 child 검증 상태는 첨부에 보존한다. 사전 귀속이 검증된 child의 사후 확인 불가는
명시적인 불일치와 구분한다. 다른 child 또는 parent의 후속 실패는 검증된 독립 FAIL을
유지한다. 보조 저장 실패의 상태·예외 타입만 첨부하고 ABORTED 사유에 반영한다.

회귀 범위: private EXAMPLE의 shadow 상당 이벤트·child 검증 누락·개별 사후 확인 불가·
검증된 위반과 다른 child 불일치의 공존·보조 저장 실패·첨부 실패, core의 ABORTED와
security FAIL/UNKNOWN·정확 Ref 재판정·Store 검증·정제 첨부를 확인한다.
검증: collector 수정 `1839c77`을 통합하고 승인 literal SHA256
`cd281c1c1f820773661ebcfeb8743ebf960a44087dcf2bcb2acc4328b6fe0503`을 연결했다.
producer 직접 계약 **42 passed / pytest 0.99초 / 부모 wall 1.07초**를 확인했다.
실제 source CPU 재실행은 공동 snapshot을 인계받은 collector 담당자가 한 번 수행한다.
