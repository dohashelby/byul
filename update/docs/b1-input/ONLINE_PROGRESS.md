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


## OOV 독립 domain 근거와 공유 판정 보정 — 2026-10-29

확인한 결함: producer가 검증 대상 tokenizer의 cached max token ID를 OOV 허용 범위의
기준으로 다시 사용했다. target031의 cached max는 32였지만 실제 고정 tokenizer의
model·added ID 합집합은 0..31이고 model vocab 크기는 32였다. 실제 parsed request의
`[32]`가 InputProcessor에 그대로 전달되고 admission을 마친 관측이 이 순환 기준 때문에
confirmed FAIL 입력에서 빠졌다.

수정: cached max는 진단값으로 보존하고 owned tokenizer/config 파일에서 독립 수집한
ID domain 요약을 기준으로 삼는다. 파일 SHA256을 capture config hashes와 결합하고,
선정 fixture의 ID count/min/max/정규 hash·0부터 연속 여부·model vocab32·요청 hash·
실제 membership을 확인한다. 이 근거와 parsed/processor/admitted hash가 일치하면
기존 product projection에 reuse-oov 행을 전달한다. domain 근거 누락·불일치 또는
실제 added token32를 포함하는 다른 domain은 정제 metadata와 제외 이유를 보존한다.
reference의 다른 gate 조건과 target의 미보장 3사례, 모든 원래 fixture expected/hash,
제품 project/judge와 source·helper gate를 유지했다.

정제 첨부에는 ID집합 전체나 token 문자열 대신 count/min/max/hash·범위 여부만 저장한다.
한 번의 수집을 공유하는 세 EVD의 ID·UTC 시각·각 첨부·정확 Ref·Store 검증과 독립 FAIL의
aux/첨부 실패 보존을 직접 회귀했다. collector `de799c2`의 승인 literal SHA256
`31c4d3bd3ff6416b9a174dcfe7514766e7be12e4fb819499499bd32a6f830dfe`를 연결한 뒤
producer 직접 계약 **62 passed / pytest 1.52초 / 부모 wall 1.58초**를 확인했다.
실제 CPU 재실행은 통합 snapshot을 인계받은 collector 담당자가 한 번 수행한다.

## 실제 CPU 재검증과 Kubernetes 연결 잔여 — 2026-10-29

통합 코드 `a5ab8992b25ed2a0c452b3734856de5244212229`에서 target031 실제 16건과
후속 정상 16건을 한 번 수집했다. tokenizer/config 독립 domain 0..31·model vocab32와
실제 parsed→processor→admitted `[32]`의 동일 hash를 확인했고, 세 EVD는
COMPLETED/DECLARED이며 REQ-08 security FAIL을 보존했다. REQ-07·SUP-08 및 나머지
판정 축은 UNKNOWN이다. 실제 CPU 수집 38.52초, 파이프라인 부모 wall 40.60초이며
Store 오류0·exact Ref 재판정 동일·정제 첨부3개의 bytes/SHA 일치를 확인했다.
독립 검토자는 기존 OOV 오라클 finding의 해결을 읽기 전용으로 확인했다.

로컬 전체 회귀는 **6269 passed / 기존 제외19**, pytest **160.79초**, 부모 wall
**161.31초**다. 최초 실행은 Mac 기본 임시 경로의 심볼릭 링크를 기존 출력 정책이
거부해 8건 실패했고, 원본 로그를 보존했다. 기존 실경로 설정 `TMPDIR=/private/tmp`로
같은 코드 전체를 재검증했으며 코드·시험·판정·제외 조건은 바꾸지 않았다.

원본 `/private/tmp/byul-online-input-product-target031-20261029-2`와 형제 증거 폴더
`byul-b1-online-evidence-20261029/product-target031-run-2`의 파일35개 hash를 대조했다.
후자의 Store에서 bundle export→새 경로 restore를 실행해 manifest·7종 객체의 exact Ref
일치와 복원 Store 오류0을 확인했다. 이전 `-1`과 환경152개 freeze는 원본대로 보존한다.
전체 로그는 `/private/tmp/byul-online-input-local-final-20261029/`에 있다.

이 결과는 CPU 단계의 근거다. 사용자 최신 마감 기준에 따라 현재 묶음은 Kubernetes
실제 대상의 요청·실제 프로세스 수집·판정·증거 저장·복구/정리 연결까지 계속 개발한다.
B 전용 native snapshot과 실행 orchestration은 별도 worktree에서 병렬 작성 중이며,
A 공통 Online HTTP profile·source-qualified sensor/parser·소유 session seal 확장과
승인 대상·run/복구 담당자 인계는 [구체 요청](https://github.com/dohashelby/byul/pull/67#issuecomment-6492998272)으로 남겼다.
현재 Kubernetes 실수집·실행 준비 완료·영구 제품 등록을 집계하지 않는다. GitHub CI의
결제 차단은 별도 대기로 유지하며 이 CPU 단계만을 위한 추가 PR은 만들지 않는다.
