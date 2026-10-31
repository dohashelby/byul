# 현재 소스 결합 관측의 이월 경계

2026-10-28 공동 main 기준선은 `4da12c059b3e55f552e07a3866c0b878e5f6a65c`, PACK31이다. A-1 PR57 `02d1212`의 현행 credential 이월 보수에 A-2 PR55 `c47445d`의 확정 변경을 합친다. A-2의 container_config0.6.1과 credential_exposure0.5.2는 구 CheckDef 필드명으로 새로 실행하는 경로까지 ENV·소유 run·컨테이너·이미지·기동 세대를 결합한다. 저장 EVD 재생은 이 수집기를 실행하지 않는다.

현재 두 실행기의 모든 live 프로파일은 정적 preview만으로 현재 소유 신원과 세대를 확정할 수 없다. 고정 sentinel·환경변수 명령·후보 probe·정책은 source identity의 대체 근거가 아니다. 같은 run/container 이름도 재시작 세대를 증명하지 않는다. 공통 fallback은 None을 반환해 기존 PROCEDURE_OR_FIXTURE_UNCONFIRMED 사유로 재선정한다. 선정에서 Docker/HTTP/엔진을 호출하지 않는다. SUP-04의 동적 이미지 프로파일도 기존과 같이 이월하지 않는다.

수정 전 합성 DECLARED 이전 PASS를 실제 제품 Store·PACK31·현행 rule_table·build_context/select에 둔 재현에서, 명시한 HTTP probe 프로파일과 입력이 같으면 credential뿐 아니라 새 container_config0.6.1도 run/container 변경과 같은 선언에서 현재 세대를 읽지 않고 이전 EVD/RES를 이월했다. EXAMPLE 수집 초안은 EXAMPLE로 유지하고 저장 계약 객체를 DECLARED로 구분했다. 실제 재시작·잘못된 엔진의 PASS·배포 결함을 실증한 결과가 아니다. 일부 설정은 별도 설정 해시 불일치로 이미 재선정될 수 있으므로 모든 설정에서 이월됐다고 일반화하지 않는다.

현재 실행을 위한 이월 자격과 과거 증거 재판정은 구분한다. 기존 EVD·RES·RPT·정확 Ref·저장 팩과 반출/복원·독립 FAIL을 보존한다. 과거 EVD를 새 수집기로 다시 해석하지 않는다. 주소/run/container를 제외하는 두 실행 비교 조건·fixture·관측 출력·판정 의미를 이월 우회 방지 목적으로 바꾸지 않는다. 다른 실행기의 고정 direct/role 이월과 EXAMPLE 배제도 유지한다.

기존 preview_reuse_inputs(check, env, values, adapter) 훅은 공통 fallback보다 먼저 적용한다. 훅의 None 반환은 정적 동일성 미확정을 뜻하며 HTTP 고정 fixture로 되돌아가지 않는다. 이는 기존 내부 확장 경계이며 새 공개 계약이나 core 분기를 추가하지 않는다. 이번 통합은 A-2의 확정 전용 source bytes를 보존하고 공통 이월·시험·문서만 추가로 맞춘다.

원본 체크리스트·77개 정의·기업 정책·core/models/store/judges·공용 contracts·팩·registry·의존성은 바꾸지 않는다. 기업 Gateway/호출자 승인표는 준비 전이며 전체 PLT-01/04·REQ-02/05 완료로 집계하지 않는다. 실제 엔진/GPU/AWS 실증 증가는0이다. 소스 고정 후 직접 source 결합·선정·저장 재생·다른 이월·두 실행 비교·첨부 실패·불변식과 설치 wheel, 정확 최종 head 필수 CI를 확인하고 결과와 시간은 PROGRESS.md에 기록한다.
