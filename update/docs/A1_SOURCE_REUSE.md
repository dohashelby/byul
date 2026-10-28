# 현재 소스 결합 관측의 이월 경계

2026-10-28 기준선은 main `4da12c059b3e55f552e07a3866c0b878e5f6a65c`, PACK31이며 원본 체크리스트·기업 정책·두 실행의 비교 조건은 유지한다.

PLT-04의 현행 `forbidden_mount_violations` 프로파일은 수집 중 exact ENV·소유 run·컨테이너·이미지·기동 세대를 확인한다. 고정 환경변수 명령·후보 probe·정책만으로 현재 신원을 확정할 수 없다. run/container 이름이 같아도 재시작 세대는 달라질 수 있다. 이 프로파일의 정적 preview는 `None`을 반환해 기존 `PROCEDURE_OR_FIXTURE_UNCONFIRMED` 사유로 재선정한다. 선정은 Docker/HTTP를 호출하지 않는다.

수정 전 제품 Store·저장 PACK31·현행 rule_table·build_context/select에 합성 DECLARED 이전 PASS를 둔 계약 재현에서, 명시한 HTTP probe 프로파일과 나머지 입력이 같으면 run/container 변경과 동일한 선언에서도 현재 세대를 읽지 않고 이전 EVD/RES를 이월했다. 실제 재시작은 실행하지 않았다. EXAMPLE 수집 초안은 EXAMPLE로 유지했고 계약 객체는 DECLARED로 구분했다. 실제 잘못된 엔진의 PASS나 배포 결함을 실증한 결과는 아니다. HTTP 프로파일을 생략한 일부 설정은 별도 설정 해시 불일치로 이미 재선정될 수 있으므로 모든 설정에서 이월됐다고 일반화하지 않는다.

이번 보수는 현행 source-bound credential 프로파일의 공통 fallback에 한정한다. SUP-04의 동적 이미지 관측과 기존 EXAMPLE 배제는 유지한다. 현재 PLT-01의 고정 sentinel과 고정 direct/role 입력 등 다른 이월 경로를 일괄 차단하지 않는다. 구 credential 필드명의 live 소스 결합은 A-2의 별도 보수이며 이번 변경으로 완료 처리하지 않는다.

A-2가 source 결합을 새로 적용하는 실행기는 기존 `preview_reuse_inputs(check, env, values, adapter)` 훅에서 `None`을 반환할 수 있다. 공통 preview는 그 반환을 우선하고 HTTP 고정 fixture로 되돌아가지 않는다. 이 훅은 기존 내부 확장 경계이며 새 공개 계약이나 core 분기를 추가하지 않는다. 정적 동일성 확인을 위해 원격 신원 수집을 수행하지 않는다.

현재 실행을 위한 이월 자격과 과거 증거 재판정은 구분한다. 기존 EVD·RES·RPT·정확 Ref·저장 팩과 반출/복원·독립 FAIL은 보존한다. 주소/run/container를 의도적으로 제외하는 두 실행 비교 설정에 신원을 섞거나 NOT_COMPARABLE로 바꾸지 않는다. 실행기 관측 출력·버전·판정 의미·registry·팩 변경은 없다. 기업 Gateway/호출자 허용표는 준비 전이며 실제 엔진/GPU/AWS 실증 증가는 0이다.

검증은 소스 고정 후 직접 선정·저장 재생, 기존 다른 실행기의 이월, 두 실행 비교, 첨부 실패·불변식과 설치 wheel을 확인하고 정확 최종 head의 필수 CI를 확인한다. 결과와 시간은 PROGRESS.md에 기록한다.
