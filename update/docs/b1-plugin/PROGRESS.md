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
