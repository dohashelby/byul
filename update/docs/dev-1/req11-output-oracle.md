# REQ-11 생성 출력 귀속 오라클 (개발 담당, 2026-11-06)

## 원 결과와 한계

2026-11-05 L4 G1 REQ-11은 JSON/SSE 8개 사례의 입력 투영·연결 대응·템플릿 신원을 검증해 기능 PASS였다. 보안은 UNKNOWN이었다.
rev3~6 정의는 생성 출력이 원래 요청에서 나왔다는 독립 근거가 없어 보안 PASS 규칙을 비워 두었다.
입력 투영이 모두 맞아도 출력 문구·token만 두 응답 사이에서 바뀐 경우를 잡지 못하기 때문이다([AUTOMATION_HARDENING.md](../AUTOMATION_HARDENING.md#req-11-새-판정-계약)).
원 EVD/RES와 과거 팩 결과는 당시 규칙대로 남는다.

## 오라클: 요청별 배타 허용 token (`exclusive-allowed-token-ids-v1`)

사용자 결정(2026-11-06)으로 다음을 택했다.

- 두 합성 요청 A·B의 독립 `/tokenize` 결과에서 자기에게만 있는 token을 고른다(A 배타 = A − B, B 배타 = B − A). 둘 다 비어 있지 않아야 한다.
- 정상 대조(before·after)와 8개 사례의 모든 생성 요청에 자기 배타 집합을 `allowed_token_ids`로 준다(temperature 0).
- 각 연결의 응답 출력 token이 모두 자기 배타 집합 안이면 그 응답의 출력 귀속을 확인한다.
- 다른 연결의 배타 집합 token이 하나라도 있으면 타 요청 출력이 이 연결로 왔다는 독립 위반이다(`security_violations`, FAIL).
- 둘 다 아니거나(엔진이 제약을 지키지 않음) 응답이 불완전하면 출력 귀속 UNKNOWN이다.
- 출력은 요청 제약의 결정적 함수이므로 GPU 배치 비결정성에 기대지 않는다. 생성 문장·usage 개수·클라이언트 ID는 쓰지 않는다.
- 제약과 출력이 함께 바뀌는 경우(요청 상태 전체 교환)는 기존 입력 투영 대조가 다른 연결의 prompt로 잡는다.

## 판정 정의 rev7 · PACK-BASELINE@33

- 새 관측 `output_binding_cases_verified`(출력 귀속까지 확인된 사례).
- 보안 PASS 규칙: 지원 프로파일·소스 계약·템플릿 신원·정책값 일치, 8개 사례의 입력 투영·연결 중첩·출력 귀속, `output_binding_verified=true`, 수집 완결, 정상 대조.
- FAIL 규칙은 그대로 `security_violations` 비어 있지 않음이다(타 prompt 소비·스트림 ID 변경·타 요청 출력).
- 다른 76개 점검 정의는 바뀌지 않았다. 팩 ref `74219b48…cd5b6`.
- 실행기 `request_binding` 0.4.0, Kubernetes 0.2.0. 비교 조건 지문(fixture)에는 고정 오라클 이름·본문 형태만 넣고, 실행마다 바뀌는 허용 집합 해시는 요약에만 둔다.
- 함께 바꾼 것: GPU 정책 초안 팩 revision 33, OBS-01 파생 팩 빌더 seed 33·파생 revision 2(11-05의 파생 @1과 결과는 그대로).

## 확인

- 직접 시험: 제약을 지키는 대역 → 보안 PASS. 한 연결에 다른 쪽 배타 token이 오는 출력만 교환 반례 → FAIL(입력 투영은 그대로 통과하는 경우). 제약을 무시하는 대역 → UNKNOWN. 불완전 응답 → PASS 아님.

## GPU 재실측

G1 묶음에서 REQ-11을 팩 33으로 다시 실행한다. 실제 vLLM 0.31.0이 chat 요청의 `allowed_token_ids`를 지키는지가 이 실측의 핵심이며, 지키지 않으면 결과는 UNKNOWN이다.
