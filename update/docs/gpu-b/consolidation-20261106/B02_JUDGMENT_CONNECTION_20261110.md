# B02 기존 공통 입력의 판정 연결

기존 `/1` 재사용 입력과 `/2` 요청별·`/3` actor 묶음의 B 소비자 연결을 보강했다. source `f8d431ba830dd2eb657fbdc1943e85cf7ce59437`의 변경은 `common_consumption.py`와 전용 시험 파일 두 개다.

- `/1`: 명시 정책이 재사용을 금지하고 등록·자격·동일 요청의 인과·세대/프로세스 신원을 충족할 때 기존 `reused_input_violations`에 관측 사실을 추가한다. 기존 순수 judge가 판정하며 run이 verdict를 직접 만들지 않는다.
- `/2`·`/3`: seq가 실제 해당 record 사건과 정확히 같을 때만 request 대응에 사용한다. 시간·순번 추정 매핑을 쓰지 않으며 실제 `runtime_qualified=True`가 필요하다. 현재 자격 영수증0 상태를 양성으로 대체하지 않았다. 미래 `/4`는 이번 변경 범위가 아니다.

WSL CPython3.12.3의 최초14개는 13통과·새 시험 전제 오류1실패였다. 기존 `/1`의 정수 start_ticks 계약을 보존하고 잘못된 새 전제를 수정한 영향 범위3개와 자격 검사 추가 범위3개가 각각 통과했다. 최종 주석 변경은 AST·시험 bytes 동일을 확인했다. 이를 최종 전체14개 재실행 통과나 체크 ID16개 완료로 합산하지 않는다. CPU fixture의 EVD는 ABORTED/EXAMPLE, RES는 FAIL/UNKNOWN/UNKNOWN, verification_level=null, RPT는 DRAFT/official=false를 유지했다.

세션6 focused 검토 원본 SHA `2487601a9dc6ef87616ffaf692b6ec581945ca301b3ab66d218cc59dc3700d38`의 381개 대조와 최종 목록 검토 SHA `7be42269ffa99ac10e78b6f096ee97b2245dcc4b9dd0b7dd7d71a6cdd91b5b6b`의 18개 대조는 불일치0이다. Git의 LF 정규화본은 원 CRLF 파일과 SHA가 다르므로 같은 bytes로 표기하지 않는다.

제품 목록은510개/SHA `8ff741fcc2ab223008fd6e2543b12e5c7e9aed468602010b190916e67e1ac004`다. 이전 목록 대비 common_consumption 한 파일만 바뀌었고 나머지509개·팩33·gate 코드는 같다. 세션6가 작성한 정확 목록 영수증을 원 행 그대로 전달해 해시 gate exit0을 확인했다. 이 검증은 현재 GPU 실행·새 팩34·B02 전체20개·J03/J04 완료가 아니다.

추가로 확인한 A 소유 범위는 REQ-12의 request-attempt 수집 선언, SUP-03의 승인 정책/팩 규칙, REQ-10의 native 모순 FAIL 규칙이다. 필요한 변경과 이유를 보고했으며 이번 B 변경으로 A 소유 파일을 수정하지 않았다. 미래 `/4`, rank 장애, PACK34 등 A 반환은 별도로 연결해야 한다.
