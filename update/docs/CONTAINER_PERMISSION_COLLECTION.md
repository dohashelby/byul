# 실행 권한 관측의 소유 대상·세대

`container_config/0.6.0`은 현행 PLT-01의 `permission_scan_complete` 흐름에서
[공통 엔진 소스 계약](A1_TARGET_BINDING.md)을 소비한다. 정확한 ENV Ref·pin 이미지·
소유 run·engine.container와 실제 실행 instance가 일치해야 한다. 처음부터 다른
대상이나 누락된 기술 입력은 `CONTAINER_TARGET_UNVERIFIED`로 거부하며 프로세스
reader·sentinel을 실행하지 않는다. 과거 target의 값을 현재 ENV로 자동 채우지 않는다.

최초 owner tuple을 한 세대의 기준으로 두고 inventory·process·sentinel·mount table·
실행 가능 경로의 sentinel·정상 대조에 각각 전후 owner 확인을 적용한다. 실제
snapshot·reader·sentinel 호출은 최초의 불변 container ID를 사용한다. 이름만 잠시
다른 컨테이너로 재지정돼도 다른 세대에 쓰기나 관측을 수행하지 않도록 한다.

구간의 신원을 확인하지 못하면 그 구간에서 받은 권한 자료를 채택하지 않는다.
뒤 구간의 restart·replacement·image ID 변화나 수집 실패는 앞서 별도 구간에서
확정한 privileged·capability·root UID·mount·device·쓰기 성공 위반을 지우지 않는다.
권한 자료의 수집이 불완전하거나 소유 신원·세대 확인 실패를 발견하면 전체 권한 수집 완료를
주장하지 않는다. 기존 명시 정책·판정·팩과 부분 관측의 독립 FAIL 보존은 유지한다.

첨부 `source_binding`에는 `docker-engine-source-intervals/1`, ENV Ref, 최초 owner의
SHA-256과 구간별 신원 확인 여부만 남긴다. inspect 전체·Config.Env·StartedAt·예외
원문은 기록하지 않는다. CPU 회귀는 명시적인 EXAMPLE ENV/pin/run과 소유 대역을
쓰며 inventory 오류는 초기 신원 확인과 구분해 해당 관측 source에만 주입한다.

SUP-04 실행 이미지 승인 분기는 기존 전용 계약을 사용한다. 과거 정의의 완전 목록
계약과 저장된 EVD/RES/RPT·정확 Ref는 재해석하지 않는다. 이 live 수집 연결은 실제
Docker daemon·정책 집행·worker 귀속·생산자 인증·Kubernetes 권한 수집·전 구간
무누락이나 PLT-01 전체 완료의 실증이 아니다. 정적 이월 미리보기와 동적 소유 세대의
관계는 공통 reuse 경계의 별도 검토 대상으로 남긴다.
