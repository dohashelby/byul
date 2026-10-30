# A에게 필요한 B-1 연결 인계

## REQ-09 원 정의 연결 추가 — 2026-10-30

`run_req09_pipeline(source_root, output_root, *, source_store, plan_id, pack_path, entered_by)`는
원 PACK31 REQ-09 revision 8의 고정 source CPU 부분을 기존 manual override에 연결한다.
기존 Online 3점검 proposal 함수·source profile·pin 의미는 유지한다. 대상 source는
db9527a46873454610df6dbedf79a36d6bf1a7f6 하나이며 실제 이미지·모델·HTTP·GPU는 실행하지 않는다.

인계 시 원 source ENV/POL/PACK/PLAN과 정책 4개, 새 output, 신고 입력자를 명시한다.
원 source Store는 보존하고 새 사본에만 append-only EVD/RES/RPT를 추가한다. 전체 정상
대조와 원 token/길이 챌린지를 부분 mask/offset CPU 성공으로 대체하지 않는다.
원 10개 관측 중 `confirmed_input_violations`만 자격 있는 실제 행에서 투영하고 다른
9개는 미수집이다. 수동 판정의 기존 input scope ceiling과 DECLARED 출처도 유지한다.

원 gate·component·collector literal의 변경은 root가 선정 소스 전체를 고정한 뒤 별도로
수행한다. bridge는 관측한 현행 bytes를 새 승인값으로 쓰지 않는다. 단위 시험은
EXAMPLE fixture만 사용하며 수집기 실제 실행은 통합 후 한 번으로 구분한다.
artifact·후속 정상·마지막 component 확인이나 export 실패가 독립적으로 확인된 위반을
삭제하지 않는 기존 수동 증거/판정 계약을 사용한다. 원 모델 consumer·HTTP·메모리 영향과
전체 REQ-09 충족은 남은 범위로 표시한다.
행별 사전 검증·실제 invalid 입력 수용·입출력 동일성을 확인했다면 명시적 후기
`unavailable`이 독립 FAIL을 지우지 않는다. 이 경우 수집은 incomplete이고 전체 범위는
UNKNOWN이다. 명시적 source mismatch·후기 상태 누락/오류는 계속 자격에서 제외한다.

PR43은 보존한다. PR46은 그 브랜치를 base로 하는 B-1 전용 후속이다.
REQ-10 structured_limits.py/sampling_cpu.py, REQ-07·08·09 input_cpu.py,
REQ-09 embedding_cpu.py와 CPU 실행 도구를 제공한다. 공통 등록/CLI/팩은 수정하지 않았다.
이 도구 JSON은 아직 정식 ENV/POL/PLAN/EVD/RES/RPT가 아니다. 임의 Ref를 만들지 않는다.

받을 입력:
1. 공동 검증 SHA, 원격 branch/PR head/base, 확인한 범위와 남은 실패.
2. 정확 PACK Ref(id/revision/정규 SHA/DRAFT 여부), 전용 collection profile 선택 경계.
3. sampling_validation.py의 기존 보수 및 단독 수정 소유권 인계 여부.
4. HTTP/응답 정제와 첨부 저장 실패 보존 API. 기존 독립 FAIL과 성공 첨부가
   다른 수집/저장 오류로 지워지지 않는 계약/회귀.
5. 실제 설치판/API/backend/활성 기능. 미정이면 해당 범위만 미정으로 유지.

최소 연결 자료:
- CPU 입력: 고정 source SHA·깨끗한 checkout·격리 Python·선정 profile·새 workdir.
  실제 엔진/운영 대상으로 자동 전송하지 않는다.
- 출력: 사례 ID·실제 경계·입력 정규 해시·관측 enum·정제된 normalized metadata·
  함수 진입·후속 CPU 정상 결과·source/config bytes 해시·설치 의존성·한계.
- check별 전용 fail 관측: structured_limits_confirmed_violations,
  sampling_cpu_confirmed_violations, input_cpu_confirmed_violations,
  embedding_cpu_confirmed_violations. judge_params는 기존 rule_table 형식이다.
- 입력/경계 해시 불일치는 projection 오류로 중단한다. 수집/실행 오류는 UNKNOWN.
  확인된 독립 위반은 다른 사례/후속 실패가 있어도 유지한다. 보안 PASS 규칙은 없다.
- 기존 sampling-api-v1 관측·정상/병행/후속·누락·runner_consumption_verified=false를
  새 CPU 분기 결과로 덮어쓰지 않는다. 기존 pack/판정에 소급하지 않는다.
- 정식 ENV/POL/PLAN 선택과 collection profile에 따른 CheckDef 관측·판정/첨부
  연결은 A 소유 경계다. source-CPU 환경과 향후 설치판 ENV를 구분한다.

필요 시점: 정확한 통합 소스에서 제품 실행→증거 저장→재판정→RPT 연결을 검증하기 전.
자동 merge/rebase/checkout은 하지 않고 새 기준의 별도 worktree에 본인 diff를 이식해
재검증한다. A/B-2의 미완성 폴더를 import하지 않는다.
