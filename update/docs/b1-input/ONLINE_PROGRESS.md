# REQ07·REQ08·SUP08 OnlineRenderer 공유 수집 — 2026-10-29

한 일: 기존 16사례·후속16건을 한 번 수집하고 외부 DRAFT 세 CheckDef의 EVD/RES와
RPT 하나에 연결하는 도구를 작성했다. ENV/PLAN exact Ref·source profile·collection ID·
실제 수집 시각에 결합하고 각 EVD는 자기 정제 첨부를 만든다. 영구 registry·공유팩·제품
project/judge·기업 정책·REQ10 numeric/schema-tools는 변경하지 않는다.

source 범위는 수집 전에 고정했다. reference5d의 원래 fixture expected/stage/hash와
projection을 유지한다. target031의 reuse-empty/bool/echo는 거부/무시 보장이 달라
원문 행·실제 소비 metadata를 보존하되 confirmed FAIL 입력에서는 제외하고 UNKNOWN으로 남긴다.
나머지 행은 실제 parsed field·설정과 gate 동작 또는 강한 InputProcessor admission 증거가 필요하다.
negative/OOV/길이 초과의 실제 admission, 고정 literal template의 실제 적용 hash가 있으면
특정 validator trace가 없다는 이유만으로 독립 FAIL을 버리지 않는다. 단순 400·빈 trace·
text-only admission만으로 MM override 소비나 source 필터 위반을 만들지 않는다.

검증: EXAMPLE private capture·source별 자격·공유 수집·gate 회귀 **43 passed / pytest 2.14초 / 부모 wall 2.46초**.
source 미보장 3개 관측 보존, reference 기존 projection 의미, trace 누락의 강한 bad-admission
증거 보존, 미소비/귀속 미확인 UNKNOWN, 세 EVD의 동일 수집 창과 각 첨부·정확 Ref·
Store·registry 복구·원문 미저장을 확인했다.
정제 첨부는 고정 enum/booleans/hash/count만 남긴다. 미수집은 EVD null 계약을 유지하고
known-untrusted source/fixture 이벤트는 제외하며 검증된 다른 행의 위반은 aux/후속/첨부
실패에도 보존한다. collector eb7f568 승인 SHA256은
`80b91e1b417763bf4bc2910332eb4dd5c5d5184e44d3c52bc5edd443f0e539ef`로 고정했다.
collector 담당자의 읽기 전용 API 대조에서도 필드·enum·hash·시간·fixture 형상이 일치했다.
기존 LIF14 도구 SHA1af→B1 helper85866·전체 c0 제품 snapshot 검사를 재사용한다.

다음: 양 구현 source freeze·직접 회귀·집중 검토 후 통합 snapshot의 실제 CPU는 지정 담당자가 한 번 실행한다.
남은 A 조건: 정식 등록/공유팩·기업 허용 template/한도·API 모델 신원·MM 코드 판본과 최종 worker 소비.
모델 weights·HTTP listener·GPU·다른 backend를 사용하지 않는다. 기존 69/16 성과를 새 사례로 재집계하지 않는다.
