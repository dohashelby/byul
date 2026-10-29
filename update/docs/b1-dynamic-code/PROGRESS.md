한 일: SUP09/11의 같은 loader12사례를 재사용한 전용 구현·정제 관측·판정 자료, 새 main으로 전용 diff 이식.
검증: 실제 CPU12+전12+후12 완료58.80초, 계약·인접65 passed0.35초.
다음: 본인 브랜치 커밋·초안 PR·최종 CI, A/B2 대조 후 다음 준비된 묶음.
막힌 것: 정식 profile/등록·기업 승인 정책·실제 설치 소비 연결. CPU 검증은 차단 아님.

## SUP-09/11 공유 수집의 core 소비 — 2026-10-29 초안

한 일: 외부 DRAFT 팩의 SUP-09·SUP-11 두 CheckDef를 같은 ENV/POL/PLAN으로 선정하고
직접 collector의 12행을 한 번만 수집해 두 EVD·RES와 RPT 한 개로 연결하는 전용 도구를 추가했다.
공유 객체는 run마다 새로 만들며 ENV/PLAN exact Ref와 source profile에 결합한다.
각 EVD는 자기 정제 첨부를 만들고 같은 collection ID·실제 수집 시작/종료 시각을 기록한다.
외부 관측 JSON·디스크 cache·새 기업 정책·PASS 규칙·영구 등록은 추가하지 않았다.

귀속: child의 source/profile/collection/mode·pre 검증을 확인하며 명시 post mismatch는
정제 기록에는 보존하고 source 특정 FAIL의 근거에서는 제외한다. 검증 불가와 mismatch를
구분하며 검증된 다른 case의 독립 FAIL은 aux·첨부·후속 실패가 지우지 않게 한다.
SUP11 정상-mode 비교는 기존 project의 실제 거부·marker 수집완료·무import 조건을 유지하고,
별도 before/after 대조 실패는 complete/normal_control을 불완전으로 만들되 독립 FAIL을 보존한다.

검증: EXAMPLE private API·공유 수집·gate 회귀 **42 passed / pytest 0.91초 / 부모 wall 1.09초**.
같은 실제 수집 창을 두 EVD에 유지하고 RPT의 두 EVD/RES exact Ref, Store·원문 미저장,
registry 복구, 새 PLAN/ENV revision/origin/profile로의 cache 재사용 거부를 확인했다.
collector 4b6e53b의 고정 SHA256 `5d98ca0feea7be74c98ab2f575e8439d5638d84ec04912d879fdb345c7aed0b5`를 연결했다.
helper1af→85866/c0 경계는 그대로다. 첫 시험에서 ENV 시험 fixture의 미등록 fact key와
미수집 product 관측의 []→EVD null 경계를 교정했다. 판정·제품 소스는 변경하지 않았다.
실제 CPU는 통합 source freeze 뒤 지정 담당자가 한 번 실행한다.
다음: 직접 회귀·집중 검토·통합 source 고정 및 실제 양 profile의 정확 Ref·Store 결과 기록.
막힌 것: 기업 승인 설치·최종 model-code 소비·정식 registry/공유팩은 A 조건으로 남는다.

### 집중 검토: 사례 준비 오류 뒤 공유된 독립 FAIL 보존

세 번째 사례의 폴더/fixture 준비 오류 뒤에도 앞선 normal-mode 거부와 O-mode import를
두 EVD의 독립 FAIL로 보존하는 EXAMPLE core 회귀를 추가했다. 부분 수집 record의 실제
collection ID·수집 시각을 유지하며 capture 1회, 두 EVD ABORTED, 정확 Ref·Store와
원문/임의 경로 미저장을 확인했다. collector f239748의 고정 SHA256
`5c74ada591ae00df75c52cf7eb920dce586aec9d16e547846391e35bce10b269`를 연결했다.
미실행 실패 row의 preparation_status=failed·child_executed=false·event_collection_status=not_collected를
정제 첨부에 보존하며
미관측 Python mode/source validation을 검증 성공으로 채우지 않는다. 소비 판정 로직은 그대로다.
최종 전용 회귀 **43 passed / pytest 1.27초 / 부모 wall 1.45초**. 실제 CPU는 root 통합 후 지정 담당자 한 번 실행한다.
