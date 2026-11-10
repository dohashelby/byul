# 관리 접근과 경로 거부 감사

관리 listener는 실행한 GET/HEAD 또는 거부한 메서드마다 서버 event ID·UTC 시각·method·경로·결과·누적 허용/거부 건수를 기존 stdout/선택 JSONL에 기록한다. 배포 시 `--target-ref`, `--run-id`, `--environment`, `--policy-ref`로 실제 설정 참조를 연결한다. 설정되지 않은 참조는 null이고 actor는 `unattributed`다. 현재 listener가 인증된 사용자 신원을 전달받지 않는 조건을 그대로 표시한다. 요청/응답 본문·헤더·토큰·쿼리와 URI userinfo는 기록하지 않는다. 허용 건수는 엔진 중계 시도여서 엔진 502/504도 포함한다.

Gateway의 지원 밖 경로는 기존 `<data-dir>/gateway_route_rejections.jsonl`을 사용한다. 기존 retention/hold가 같은 파일을 관리한다. 새 감사 DB나 runtime-event 종류를 만들지 않으며 추론 요청 목록·건수에도 합산하지 않는다.

- `/api/status.route_audit`는 프로세스 시작 이후 시도·저장 완료·한도 초과·저장 실패 수와 마지막 실패 시각·오류 종류, 한 시간 기록 한도를 보인다. 실패 후 실제 디스크에 일부 바이트가 남았는지는 저장 완료로 확정하지 않는다.
- viewer 보호 `GET /api/route-rejections?limit=100`은 마지막 최대1MiB에서 최대200건만 읽는다. 최신순이며 현재 파일 창의 반환 수·잘림·손상·조회 오류와 읽은 bytes를 표시한다. `total=null`은 전체 건수 미집계다. process 누적 건수와 현재 retention 파일의 반환 수를 구분한다.
- 플랫폼 `/api/runtime/route-audit`는 기존 RuntimeClient/viewer 자격을 사용한 읽기 전용 경로다. 공통 runtime source/models를 바꾸지 않는 독립 모델·라우터이며 구판 미지원·실제 연결 미설정·형식 오류를 명시한다. 수집 상태 화면은 이 별도 감사를 추론 요청 건수와 분리해 표시한다.
- management 기록은 현재 stdout/별도 파일 경계다. Gateway route journal과 섞지 않으며 실제 관리 listener를 사용한 배포의 scope 값·원본 접근·보존을 인수한다.

저장·조회 장애가 생겨도 원래의 HTTP 거부 판정은 유지한다. write 실패는 민감한 예외 문자열 없이 종류·시각·누적 수로 표시하고, 한도 초과와 보존 삭제는 서로 다른 범위로 남긴다. 프로세스 재시작 시 메모리 카운터는 초기화되며 과거 journal 줄은 retention 정책에 따른다. 다중 프로세스가 같은 파일을 동시에 쓰는 배치는 기존 JsonlStore의 단일 프로세스 잠금 조건을 따른다.
