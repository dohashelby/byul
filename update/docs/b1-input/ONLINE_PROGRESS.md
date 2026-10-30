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

## 새 native 모듈과 기존 CPU 도구의 소스 소비

Kubernetes용 B 전용 `guard/adapters/vllm_online_input.py`를 추가했으므로 기존 CPU 도구의
전체 `guard` snapshot pin도 정확한 새 소스에 맞췄다. 명시 component는
`abd0a9f23fbc0dcf39d47d8886b32476d3801d27`이며, 이전 `c0af481`과 pinned scope를
비교한 제품 차이는 이 파일 하나다. `packs`, lock, pyproject, framework는 같다.
공동 base c9af729·B1 helper SHA858666·pinned scope·index/worktree/archive 및 shadow
검증은 그대로이며, 실행 시 HEAD에서 승인값을 만들지 않는다.

B 전용 도구7개는 component/gate/collector 승인 literal만 변경했다. 해당 할당값을 제외한
AST 동일성을 대조했고, 실제 현재 소스에서 strict component와 Online/plugin/dynamic
세 소비 gate를 통과했다. 기존 정상·위반·미수집·변조·unconfigured pin 회귀는
**280 passed / pytest 10.83초 / 부모 wall 10.99초**다. 실제 엔진 수집은 반복하지 않았다.
이전 CPU 결과는 당시 소스·해시·Ref로 보존하며, 이번 갱신을 새 실수집으로 집계하지 않는다.
source pin 대조 기록은 `/private/tmp/byul-online-input-pin-refresh-20261029.json`,
정상 소비 기록은 `/private/tmp/byul-online-input-pin-refresh-positive-20261029.json`이다.
이 변경은 고정 소스의 집중 검토 대상에 포함한다.

## B 전용 Kubernetes 연결 소스의 로컬 검증

코드 snapshot `c0a1d413cf0244c9d9c579b53fe34fc813b25d15`의 최종 전체 회귀는
**6390 passed / 기존 제외19**, pytest **154.12초**·부모 wall **154.55초**다.
검증 중 source는 변경하지 않았다. 검토 5건(이미지/출처 자격, 기본거부 gate 연결,
실제 길이 경계, 정상 after 대조, text-only probe 제한)과 gate의 check 오귀속 후속을
수정했고 검토자가 해당 지적의 해결만 확인했다. 전용 native 회귀48건·runner 회귀73건과
실제 generate/pooling 두 객체의 길이 경계 관측10.10초를 재사용했다.

최종 native bytes SHA는 `bd15958029e91ba189c2fe588009a97ddd4d3d339e32824e5e5016a52fa736b1`이다.
wheel의 guard158파일은 소스 bytes와 같고, 별도 Python3.12 환경의 설치본에서 stdlib-only
callback import와 미지원 point의 unavailable 반환을 확인했다. native 경계 직접 관측 두
판본과 최종 로그/소스 pin 대조·wheel 검증은 형제 증거 폴더에 보존한다.

이 단계에서 실행 명령·실제 객체 snapshot·B native join/판정·EVD/RES/RPT·Store 검증·
export/restore·exactRef 재판정·소유 probe/session 정리 본체를 작성했다. A native/HTTP/seal
및 provenance 계약·literal pin은 미확정 값으로 차단한다. 실제 배포 대상의 model/domain/
활성 기능·운영/복구 담당·정확 복구 절차를 받은 뒤 확정 통합 source에서 실행해야 한다.
기존 CPU 증거와 EXAMPLE 회귀를 Kubernetes 실측으로 집계하지 않으며 전체 묶음의
실환경 마감은 남겨 둔다. 명령과 입력은 `KUBERNETES_RUN.md`, callback은
`NATIVE_CALLBACK.md`를 따른다.


## 2026-10-30 A108 Online K8 소비 계약 갱신

기존 `b_online_kubernetes_run.py`에서 A108 `e24dfb5653d66f9d82fc4537a995208f29ad113e`의 exact source pins와 `/9` source-relative code 계약을 소비한다. callback provenance의 `code_runtime`으로 CPython3.12.3·3.12.14 고정 code literal을 선택하고 MODULE `online_code_sha256`를 검증한다. hook source·ASGI/native/call·PID/start·session·nonce·domain/config·after 대조와 독립 FAIL을 유지한다. 기존 public HTTP→owned seal→capture_session→판정→Store/export/restore 경로를 전용 EXAMPLE 대역으로 검증한다.

현재 B 브랜치에는 A108 공통 bytes가 미통합되어 source gate가 요청 전에 닫힌다. A108 reader의 `image_check=False`도 유지되어 diagnostic recording은 B native join의 image gate를 통과하지 않는다. `/9` read/parser2MiB·2048행과 기본 `/8`256KiB·512행, 현행 session seal256KiB 상한을 구분한다. 256KiB 초과 `/9` 파일의 owned seal 지원은 A 후속 대기다. local Python3.12.13 회귀와 serving native 승인3.12.3/.14를 구분한다.

CPU16+after16 실측 재실행0, 실제 Kubernetes/네트워크0, callback SHA bd159580… bytes·기존 CPU pin·A 공통/runtime/registry/팩 수정0이다. 직접 영향 테스트 결과는 커밋 인계 보고에 기록한다. 실제 실행 준비 완료·실수집·영구 제품 등록으로 집계하지 않는다.
