# A에게 필요한 B-1 연결 인계

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
