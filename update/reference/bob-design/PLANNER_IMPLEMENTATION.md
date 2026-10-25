# 변경 적용 조건·시험 선정 — 구현된 범위

근거는 A4 §7·§10·§11, A7 §3·§6b·§6c, A17 §7입니다. 조건·기대·요구·영향·선정 계산에 공용 `PlanningService`의 현재 출처/승인 검증과 공식 IA/PLAN 발행을 연결했습니다. 실제 조건 근거/등록 템플릿·자료/실행 준비 제공자·보고서·CLI/API·실측은 후속입니다. 이 문서는 새로운 외부 입력이나 교환 스키마를 확정하지 않습니다.

## 변경 적용 조건

조건 이름은 검토된 규칙이 등록한 키를 사용합니다. 설명 문장은 코드로 해석하지 않습니다. TRUE/FALSE는 실제 값·출처·위치·파일 SHA를 갖춘 내부 관측을 요구하고, 누락은 UNKNOWN·상충은 CONFLICT입니다. 확인된 FALSE가 있으면 다른 조건의 UNKNOWN/CONFLICT보다 먼저 NOT_APPLICABLE로 계산하되, 다른 누락 근거는 결과와 설명에 보존합니다. 필수 조건이 모두 근거와 함께 TRUE일 때만 APPLICABLE입니다. 빈 전제 목록/미등록 규칙/조건 목록 불일치는 전체 참으로 보지 않으며, 규칙에 없는 거짓 조건을 넣어 비적용으로 만드는 것도 거부합니다.

ENV의 등록된 boolean 필드만 읽고 원문 파일 SHA·field evidence를 대조합니다. DECLARED 파일 사실은 계획의 출처로 표시하며 실제 런타임 효과를 관측했다고 바꾸지 않습니다. 상충 값은 ENV의 null을 유지합니다. `observed_network.egress_restricted` 같은 기존 방화벽 보호 설정은 변경 비적용의 조건으로 사용하지 않습니다. 등록되지 않은 path_used 등의 observed_* 키를 새로 만들지 않습니다. 해당 사실은 실제 검증 제공자와 등록된 원문 근거가 필요하며, 없으면 UNKNOWN입니다. 이 계산 함수 자체가 원문 bytes의 존재·공개 출처를 검증하는 adapter는 아닙니다.

버전 비교는 packaging 26.3을 직접 런타임 의존성으로 잠갔습니다. 이미 잠금에 있던 dev 전이 의존성을 재사용하며 wheel/소스 해시는 uv.lock에 고정했습니다. [공식 Version 문서](https://packaging.pypa.io/en/stable/version.html)와 [Specifier 문서](https://packaging.pypa.io/en/stable/specifiers.html)의 PEP 440 비교를 사용합니다. 특정 엔진 버전이나 CVE를 코드에 넣지 않습니다. 검증된 권고가 제공한 영향 범위에 두 버전 중 하나가 포함되거나 고정 수정 버전 경계를 가로지르는 변경 쌍은 관련 경로로 남깁니다. 후보가 수정 버전이라고 기준→후보 검증을 빼지 않습니다. 두 버전이 이미 수정 경계 이후이고 영향 범위 밖이면 이 조건은 FALSE입니다. 검증된 버전/범위 근거가 없거나 파싱·한도를 넘으면 UNKNOWN이며, 순서 비교할 수 없는 arbitrary equality(===)는 사용하지 않습니다.

ChangeFact.certainty와 환경 applicability는 다른 축입니다. 조건을 평가한다고 claim의 의미를 새로 확정하거나 UNKNOWN claim을 CONFIRMED로 올리지 않습니다. 실제 서비스는 불확실성이 영향을 주는 개별 조건을 UNKNOWN으로 제출해야 하며, 보호 설정·예외·사람의 의미 검토를 조건의 참/거짓과 혼용하지 않습니다.

## 정책별 시험 선정과 미검증 범위

모든 POL은 CONFIRMED여야 하며 exact IA·ENV·CHG·TEST 그래프가 일치해야 합니다. 서로 다른 ENV/변경 집합·중복 IA/정책·끊긴 TEST revision·잘못된 정책 동작/phase는 오류입니다. 과거 Ref를 최신 revision으로 바꾸지 않습니다. 요구별 RequirementRef는 기존 IA의 고정 배열 인덱스와 phase를 사용합니다.

FUNC·PERF·AUTH의 등록된 기본 회귀 plug-in과 RUNTIME 요구는 보안 변경의 NOT_APPLICABLE/UNKNOWN만으로 제외하지 않습니다. 보안 변경 영향 UNKNOWN은 보안 시험의 판단 보류와 추가 확인 범위로 기록합니다. 확인된 전제 FALSE는 미선정 사유이며 존재하는 근거를 누락이라고 표시하지 않습니다. 정책별 면제·비적용·DEFERRED도 각각 사유를 보존하고, 면제로 변경 영향 자체를 지우지 않습니다.

TEST 정의만으로 실행 구현을 READY로 보지 않습니다. 신뢰된 ReadinessProvider가 실제 구현 등록·exact Binding·검증된 사례·고정 자료·expected/필수 증거 지원·모드·기능 설정을 확인해야 합니다. provider가 없거나 실패·잘못된 응답을 보내면 GAP이며 원문 오류를 결과에 복사하지 않습니다. DISABLED/UNSUPPORTED도 필요한 검증 gap을 유지합니다. 현재 실제 실행 구현과 Binding을 연결한 운영 제공자는 없고 검사는 명시 EXAMPLE 제공자를 사용합니다. 기존 콘텐츠 팩의 bindings=[]를 ready로 바꾸지 않았습니다.

같은 TEST가 정책 A에서 선정되고 정책 B에서 자료 부족이면 PLAN의 selected_tests에 한 번만 넣습니다. 정책 B의 RequirementTrace GAP과 필요한 사실은 별도로 보존하며, TEST 단위 not_selected_tests와 겹치지 않습니다. 같은 결과를 재사용할 수 있는지는 후속 RUN의 전체 조건·기대·자료·승인·qualification 검증에서 결정하고 이 선정 계산이 하나의 물리 실행 공유를 승인하지 않습니다. TEST가 없는 DEFERRED, 완전한 요구 정의/사례를 만들 수 없는 동작도 내부 trace에 null requirement_ref와 GAP을 남깁니다. 존재하지 않는 요구 인덱스·TEST·사례 ID를 만들지 않습니다.

137개 조건/선정 단위 사례는 A4 E1/E2/E3, 조건 64조합, 수정 후보·역방향·버전 경계·미확인/상충 ENV·빈 조건, 독립 회귀, 복수 정책과 동일 TEST의 gap·면제·DEFERRED·지원 실패를 검사했습니다. 이 검사는 실제 승인이나 시험 실행/판정, IA/PLAN 저장, 보고서, UX/UI 인수 완료를 뜻하지 않습니다. RES/CMP/REC를 만들지 않았으며 기존 고정 기대값을 변경하지 않았습니다.
## 정책 기대·요구 생성과 영향 투영

`expected.py`는 관리자 코드 등록의 exact CTRL·판정 플러그인·버전·필드 대응표를 통해 승인 POL의 값만 투영합니다. 정책 ID나 파라미터 이름으로 평가 경로를 선택하지 않습니다. MODEL/FUNC의 소유 규칙상 기대를 유지하고 SYS 목록·PERF 기준·AUTH 공개 경로 의미 해시·NET 목적지/모드·APIGW API/역할/모델을 닫힌 타입으로 검증합니다. REF·SHA를 가진다는 것만으로 실제 자료 bytes 검증 완료를 주장하지 않습니다. 그 검증은 공용 서비스와 선정 지원 제공자의 관문입니다.

`requirements.py`는 실제 Binding의 case_ids와 등록된 부분 실행 조건으로 요구를 생성합니다. 사례나 템플릿이 없거나 기대값을 만들 수 없으면 요구를 지어내지 않고 정책·동작·TEST별 gap을 반환합니다. RUNTIME에는 ISOLATED 전용 필드를 조용히 버리지 않고 미지원 gap으로 남깁니다. 실행기 준비 상태는 별도 선정 단계에서 확인하며, 이 투영만으로 시험을 실행하지 않습니다.

`impact.py`는 정책별로 각 exact CHG의 조건을 판정한 후 요구·부족 근거를 IA에 연결합니다. 여러 변경 경로 중 적용되는 경로가 있으면 요약은 APPLICABLE, 적용 경로 없이 미결 경로가 남으면 UNKNOWN, 모두 근거 있게 비적용일 때만 NOT_APPLICABLE입니다. 이는 내부 집계이며 각 경로의 구조화된 결과·미확인 사실은 별도로 보존합니다. 한 변경의 거짓 전제가 다른 변경의 미확인/적용 경로를 지우지 않습니다. 면제·범위는 Resolver의 결과이며 변경 영향과 분리합니다.

57개 요구 생성·28개 영향/선정 통합 검사는 EXAMPLE 입력입니다. 현재 팩의 빈 Binding을 준비 완료로 바꾸지 않았고 실제 자료·사람 승인은 아직 미완료입니다. 공식 IA/PLAN 저장은 아래 공용 서비스에서 연결했습니다.

## 공용 분석·계획 저장

A18의 닫힌 AnalyzeRequest와 exact POL/ENV/CHG/팩 잠금을 받습니다. OPERATOR 권한·멱등 키를 검사하고, 현재 서명 registry 관측 후 기존 관리 DB의 writer 안에서 출처를 확인합니다. `PlanningSources`는 실제 Resolver의 현재 승인, 정책이 해결된 exact ENV, 확정된 ENV 등록 이력과 CHG 검토/수동/병합 출처를 검사합니다. EXAMPLE/MEASURED 혼용·다른 출처/버전 쌍·미고정 참조 팩은 거부합니다. 관리자 팩 경로를 PackLoader로 다시 읽고 실제 파일 bytes와 manifest 잠금을 대조합니다. 현재 DRAFT 팩은 EXAMPLE만 허용합니다.

조건과 기대 템플릿은 관리자가 설치한 코드 등록에서만 공급합니다. 입력이 제공자를 선택하거나 자유문장으로 조건 의미를 추측하지 않습니다. 조건 근거 제공자가 없으면 UNKNOWN, 사례 바인딩/템플릿이 없으면 요구 생성 gap, 실행 지원 제공자가 없으면 미선정 gap입니다. 한 조건 경로가 잘못돼도 독립적으로 확인한 다른 경로는 보존합니다. 등록 바인딩/검토 표시만으로 실제 실행기·자료를 준비됐다고 표시하지 않습니다.

원래 요청·config SHA·각 출처 객체의 실제 bytes·조건/요구 투영·선정 준비 확인·고정 결과를 내부 IMPACT_PLANNING 예약에 감사 저장합니다. 다음으로 IA 전부와 PLAN을 같은 공식 객체 batch에서 원자 확정하고, 마지막 COMMITTED 이력을 감사 저장합니다. 두 DB가 원자적이라고 주장하지 않습니다. 마지막 이력이 실패하면 공식 객체 bytes가 존재해도 소비자는 이를 사용할 수 없고 원래 예약을 복구해야 합니다. 기존 ENV/POL 소비자도 최종 이력이 미확정인 객체를 차단합니다.

최종 공식 writer에서 현재 출처·승인·팩 bytes·config·조건·실행 지원을 다시 검사합니다. 바뀌면 batch 전체가 차단되고 최초 결과를 새 입력으로 덮어쓰지 않습니다. 실제 저장한 의미 bytes를 대조하므로 Python의 False==0 동등성으로 다른 정책 값을 통과시킬 수 없습니다. 각 감사 경계 실패·시각 변경·재시작·응답 재요청·동시 요청·두 번째 insert 실패를 검사합니다. 완료한 동일 요청의 재요청은 현재 OPERATOR 권한 확인 뒤 원래 기록을 반환하며, 역사 조회를 새 승인으로 취급하지 않습니다. 후속 실행은 `verify_current`와 별도 실제 preflight 관문을 통과해야 합니다.

실제 로컬 서비스 조합(임시 EXAMPLE Ed25519 승인/ENV/Resolver/CHG/PackLoader)에서 분석→IA/PLAN 저장을 검사했습니다. 현재 바인딩이 없는 팩의 시험을 준비 완료로 만들거나 RUN/RES/CMP/REC/RTP/ENS를 생성하지 않았습니다. 실제 GPU·클러스터·AWS·사람 인수와 제품 웹 통합은 수행 전입니다.

