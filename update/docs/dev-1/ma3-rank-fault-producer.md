# M-A3 rank-fault 후보 생산자

기존 엔진 안 native24 fault·rank 관측을 재사용하고 `adapters/vllm_rank_fault_input.py`와 `collectors/rank_fault_producer.py`가 공통 입력으로 변환한다. 관측기 복사·새 주입·기존 소스 고정값 완화는 없다. 공통 계약의 기존 `rank-fault` 종류와 `/1` 봉투를 사용한다. M-A2의 `/4`와 독립적으로 main에서 분기했다.

| 사건 | 필요한 원 근거 |
|---|---|
| fault_applied | 기존 엄격 투영이 검증한 source/물리 주입·정확 요청·nonce·세션·대상·프로세스·clock. ARM/READY 파일만으로 만들지 않는다 |
| fault_released | 같은 worker의 같은 호출에서 나온 정확 RELEASE와 action receipt 지문. EXPIRED·파일 삭제·성공 요청으로 대신하지 않는다 |
| detected | 기존 기한 투영이 선택한 탐지 원 참조. 상위 통지만 있으면 탐지로 올리지 않는다 |
| client_delivered | 동일한 영향 요청의 실제 오류 응답 수신 또는 원 투영이 확인한 늦은 수신. 정상 HTTP 성공을 장애 전달로 바꾸지 않는다 |
| recovered | RELEASE 뒤 별도 요청의 source ID 배정·worker RPC 정상 반환·단일 rank executor·성공 future·ASGI 정상 종료·클라이언트200/완료·동일 clock이 결합된 경우만 만든다 |

공통 binding은 장애 대상 worker다. 탐지/전달 관측자의 PID를 worker PID라고 쓰지 않는다. 투영의 `observer_context`와 `source_ref`는 실제 관측 프로세스/원 행을 따로 보존한다. 공통 `at_ms`는 해당 사실을 확인한 관측 시각이며 원 주입 구간이나 정책 기한을 다시 계산하는 기준이 아니다. 원 `native_validation`의 시간 구간·위반·공백·참조를 함께 보존하며 새 보안 판정은 만들지 않는다.

## 생성

`uv run python -m tools.rank_fault_inputs --input <원-묶음.json> --output <새-입력.json>`

원 묶음은 packet·client·injection_receipt·source_scope·deadlines·pod·environment_ref·target_sha256·prepared_sha256·session·recovery_client를 가진 닫힌 JSON이다. 예상 ENV/target/prepared와 source_scope는 수집자가 별도로 제공해야 한다. 도구는 최대8MiB·동일 파일 신원·중복 키와 비정상 JSON·심볼릭 링크를 검사하고 기존 출력을 덮어쓰지 않는다. 생성물은 DECLARED이며 엔진을 실행하지 않는다. 원 파일의 bytes SHA를 남기며 기존 실측을 새 실측으로 표시하지 않는다.

## 한계와 후속

- `guard-engine-callback-vllm-rank-fault/1`은 GPU 미확인 후보이며 등록표에 추가하지 않는다. 모의 등록은 EXAMPLE 단위 시험 안에서만 한다.
- 현재 재사용하는 source profile의 검증 범위는 단일 rank·같은 소유 범위다. TP2와 기준 V0 관측은 구현/검증되지 않았고 J01 수락 자격이 없다. 기존 엄격 검사를 넓혀 TP2라고 받지 않는다.
- death/delay의 OS 신호 해제·SIGCONT·새 세대 복구는 source RELEASE와 다른 사실이다. 현재는 각각 미확인 gap이며 fault_released/recovered를 만들지 않는다. 해당 물리 영수증·새 세대 lineage와 TP2 관측 경로가 남아 있다.
- source packet 자체가 검증되지 않으면 ack를 만들지 않는다. 원 행이 없으면 가상 PID/binding을 넣지 않고 계약이 거부한다. 일부 사건만 확인되면 나머지는 누락 gap으로 남긴다.
- 정상 worker 관측은 전체 GPU 자원 회수·기업 복구 정책 준수·생산자 인증이 아니다. 기존 LIF-08 판정 규칙과 제품 실행기는 변경하지 않았다.

이 작업의 완료 범위는 후보 변환·무실행 입력 도구·직접 시험이다. M-A3 전체와 J01 준비 완료로 세지 않는다. TP2/신호 해제/새 세대 복구 연결과 실제 GPU 재확인/등록은 후속이다.
