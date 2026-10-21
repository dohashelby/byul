# M8 usage·replay 정책 문맥

## 기준·확인 범위

M7 `3841b268c65cc32fcf31eaa8de69e8f507c27fa5`와 미병합 [PR #9](https://github.com/dohashelby/gua/pull/9)를 기준으로
`codex/gua-policy-m8`에서 구현한다. [M7 계약](POLICY_M7.md)의 OBS-11·수동 /3·구형 /1·/2·참조 검증·
증거 저장·시계/조회 창·FAIL/UNKNOWN·보고 제한을 유지한다. M7 최종 CI의 752 passed/18 deselected와
설치본 29명령은 과거 결과이며 이번 M8 실행 수로 합산하지 않는다.

M8은 정책 2개를 네 점검에 연결한 **수동 신고의 정책 문맥 검사**다. 자동 usage/replay 관측·실행기·
첨부 내용 인증·기업 승인 체계를 추가하지 않는다. 공통 코어·모델·저장소·의존성·객체 7종은 유지한다.
정책 문맥이 있어도 필수 수동 근거·공통 PASS 관문을 우회하지 못하며 검증 수준을 승격하지 않는다.

## 원문·소유권·입력

| 카탈로그 결정 | 점검·경로(values 아래) | 타입 | 정책 의미와 별도 증거 |
|---|---|---|---|
| P-DRAFT-USAGE-ALLOW | OBS-04.usage_allowed / OBS-05.usage_allowed | required bool | 해당 점검 범위의 usage 생성·전송 허용 여부. 실제 env/config·새 프로세스의 판단·보고 생성·송신/수신은 별도 관측 |
| P-DRAFT-REPLAY-ALLOW | OBS-07.replay_allowed / OBS-08.replay_allowed | required bool | 해당 점검 범위의 replay 사용·지원 허용 여부. consumer 존재·도달·계정·권한·reader/parser·부수 동작은 별도 관측 |

근거는 [OBS-04](../reference/framework/data/checklist.json#/observability_assessment_draft/3),
[OBS-05](../reference/framework/data/checklist.json#/observability_assessment_draft/4),
[OBS-07](../reference/framework/data/checklist.json#/observability_assessment_draft/6),
[OBS-08](../reference/framework/data/checklist.json#/observability_assessment_draft/7)의 기업 결정·충족/실패/미확인 조건과
[CSV](policy_catalog.csv)의 기존 두 ID다. usage 원문은 허용 변경 시 필드·목적지·사본 검토·승인·재시험을
요구하고, replay는 실제 외부 consumer와 권한/자료 신뢰를 구분한다. bool로 나머지 요구를 대체하지 않는다.

원문의 정책 역할은 연결된 items의 “적용 조직의 정책 승인 담당”이며 실제 담당자·승인자는 미정이다.
POL에는 기업의 허용 의사, ENV에는 실제 구성 사실, 실행 설정에는 시험 대상/예산, EVD에는 실제 시도와
관측을 둔다. 새 ENV 키·운영 명령·기본 기업값은 없다. true는 기능 존재나 승인 완료, false는 부재 증거가 아니다.

양식은 네 점검 모두 `{}`다. 생성한 키·기본 true/false는 없고 주석만 제공한다. 실제 입력은 기업이 결정한다.
각 값은 점검별 범위의 독립 입력이다. 다른 점검으로 복사·상속하거나 전역 단일값으로 가정하지 않는다.
같은 서비스·범위의 공유 정책 일관성은 업무 검토가 필요하다. 서로 다른 값이 서로 다른 범위를 뜻하는지,
동일 범위의 충돌인지 자동 판별하지 않는다. M8은 전역 정책 충돌 해결을 구현하지 않았다.

## 분기·판정 계약

| 점검 | false | true | 키 누락 |
|---|---|---|---|
| OBS-04 | 금지 정책 검토 문맥 활성. 비생성/비전송 근거가 따로 필요 | 금지 분기의 신고 PASS를 UNKNOWN으로 보류 | NO_POLICY_VALUE, 신고 PASS 보류 |
| OBS-05 | 허용 환경 분기의 신고 PASS를 UNKNOWN으로 보류 | 허용 정책 검토 문맥 활성. 승인 범위·실제 payload·최종 수신 증거 필요 | 동일 |
| OBS-07 | 실제 비사용/미도달을 확정하지 않고 신고 PASS 보류 | replay 권한 검토 문맥 활성. reader/executor·실제 실행/거부 증거 필요 | 동일 |
| OBS-08 | 실제 비사용/미도달을 확정하지 않고 신고 PASS 보류 | replay 자료 소비 검토 문맥 활성. 출처·reader/parser·거부/부수 동작 증거 필요 | 동일 |

“활성”은 PASS가 아니라 기존 수동 검토 흐름을 적용할 수 있다는 뜻이다. 첨부·관측 답변과 실행 완료·
정상 대조·모든 챌린지·구조화 필수 관측(INV-11)은 계속 필요하다. 해당 내용의 업무 적정성·진위·실제
기업 승인은 자동 평가하지 않는다. 기능·성능을 새로 실측하거나 수동 신고를 MEASURED로 바꾸지 않는다.

비활성/미확인 문맥은 **각 축의 신고 PASS만 UNKNOWN으로 보류**한다. 신고 FAIL과 기존 판정기의
독립 확인 FAIL은 보존하고, UNKNOWN을 PASS로 승격하지 않는다. 플래그만으로 새 FAIL을 만들지 않는다.
정책 예외도 기존대로 판정을 바꾸지 않는다. scope·ENV 적용성·자동 제외·NOT_APPLICABLE은 바꾸지 않는다.

select의 기존 bool 타입 검사를 재사용한다. 키 생략과 false를 구분하며 null·정수0/1·문자열·float·목록·
사전은 거부한다. 범위 밖 점검에 제공한 값도 검사한다. 판정기를 직접 호출한 잘못된 값도 미확인으로
보류한다. ENV false에 부재 근거를 요구하는 INV-06과 POL의 유효한 false는 별도 계약이다.

## 호출·호환·보고

- [선정](../src/guard/core/select.py): 기존 required/type 검사만 사용한다. 구조화 ENV 조건은 추가하지 않는다.
- [정책 템플릿](../src/guard/collectors/policy_template.py): 팩 선언과 정확 Ref의 설명 JSON을 재사용한다.
- [declared](../src/guard/judges/declared.py): `_branch_context`가 네 ID와 정확한 required bool 선언에만
  적용된다. 같은 파일의 작은 순수 함수이며 등록 체계·외부 I/O·새 플러그인 종류는 없다.
- [보고](../src/guard/core/report.py): 기존 `POLICY_CONTEXT:` 전달과 정책 한계 치환을 재사용한다.
  플래그 true/false·활성/비활성/미확인 사유·자동 검증 한계를 JSON/HTML에 표시한다.

declared는 의미 변경으로 **0.6.0**이다. manual-entry **0.4.0**, guard-manual-evidence **/3**,
guard-policy-evidence/1·guard-audit-evidence/1은 그대로다. 네 점검에 새 payload나 항목/case를 만들지 않는다.
선언 없는 과거 CheckDef, optional/다른 타입의 선언, 다른 ID에는 새 분기 전제를 소급하지 않는다.
기존 /1·/2 읽기·정확 PLAN/POL/팩 Ref·side 분리·과거 EVD/RES·멱등성은 유지한다. 재판정은 기존 객체를
고치지 않고 새 판정기 버전의 결과를 추가할 수 있다. 입력값이 없는 기존 폴더의 증거를 덮지 않는다.

보고의 `POLICY_FLAG_UNRESOLVED`/`POLICY_BRANCH_ACTIVE`/`POLICY_BRANCH_INACTIVE`는 문맥 사유다.
활성여도 usage 필드·목적지·주기 또는 replay consumer·권한·parser·실제 상태를 자동 검증하지 않았다는
사유를 함께 표시한다. 원래 자기 신고·약한 출처·EXAMPLE·DRAFT·official 계산과 HTML escape를 유지한다.
코어 보고 코드를 복제하거나 별도 결과 모델을 만들지 않는다.

## 팩·카탈로그 정합성

PACK-BASELINE **14/DRAFT**, 네 대상 정의 **2**. 77 ID·다른73점검·원문·303결정/595연결·자동 실행/판정
연결5개를 보존한다. 구조화 정책은 **14점검/24경로**, 분류 **a21/b6/c161/d115**다. b→a의 두 항목은
문맥 소비가 구현됐다는 뜻이며 실제 기업값·승인·엔진 자동화 완료 수가 아니다. 다른 수치 도출 과제도 줄지 않는다.
override→팩 생성과 설명 JSON 생성으로 재현하며 examples/lab의 실제 값·scope·출처·승인은 보존한다.
기존 CSV의 declared 코드 줄 참조14행은 이동한 실제 함수 위치에 맞추고 ID·원문·점검 연결을 유지한다.

## 검증 방식과 한계

[M8 회귀](../tests/test_policy_m8.py)는 bool/누락·범위 밖 타입, 분기 표·FAIL/UNKNOWN 보존·비적용 금지,
순수성·구형 선언·증거/PASS 관문·/1/2/3·side·JSON/HTML·false·멱등·Store.verify를 다룬다.
기존 M7 OBS/SUP/LIF·불변식 회귀를 함께 실행한다. wheel은 저장소 밖 설치 환경에서 별도 확인한다.
직접 실행의 실제 결과는 [PROGRESS](../PROGRESS.md)와 최종 PR에 기록하고 과거 CI와 구분한다.

실제 usage 송신·replay 실행·외부 수신처·엔진/두 이미지·운영·유료 자원 시험은 하지 않는다.
자동 실행기 5개는 그대로다. 수동 합성 계약 검증을 실제 엔진 검증으로 표현하지 않는다.

## 다음 정책 후보 비교

다음은 제안이며 이번에 등록·구현하지 않는다. 원문·식별자·추가 결정은 [카탈로그](policy_catalog.csv)에 있다.

| 남은 b 후보 | 기존 연결 | 선행 조건과 최소 다음 산출물 |
|---|---|---|
| SUP-CODE-MODES | SUP-08~11 | 실행 모드 식별자와 실제 활성/반입 권한의 구분, 빈 목록 의미·동적 코드 관측 계약. 모드 문자열 목록만으로 안전성을 판정하지 않음 |
| SUP-DERIVED-LOCATIONS | SUP-15/16 | 컨테이너/호스트·실제 경로·symlink·공유 캐시 신뢰 경계. 경로 문자열 비교를 권한/출처 검증으로 취급하지 않음 |
| INPUT-ENDPOINTS + INPUT-FORMATS | REQ-07~10 | METHOD/path·입력 형식 식별·허용/등록/시험 범위 구분, 기존 REQ-09와의 충돌·호환 설계 |
| CACHE-PUBLIC-PREFIX + CACHE-CONTEXT | REQ-15~17 | 공개/비공개·tenant 문맥·실제 reuse 증거와 집단 경계. bool만으로 비공개 혼입 허용 금지 |

**M9 권고:** INPUT-ENDPOINTS/FORMATS의 의미·소비 계약을 먼저 설계한다. 기존
[REQ-09 실행기](../src/guard/executors/input_validation.py)가 한 정상 token-array 요청과 두 경계를
다루므로 재사용 접점은 있지만 다른 endpoint/format 전체를 다룬다고 볼 수 없다. M9은 변경 지점·
판정/호환 표와 구현 프롬프트를 산출하고, 그다음 승인한 작은 범위만 구현하는 순서를 권한다.
REQ-09 정책을 바꾸거나 REQ-07/08/10까지 자동 등록하는 것은 M8 범위 밖이다.

팀은 실제 허용 endpoint/형식·범위·승인 역할·빈 목록의 업무 의미를 결정한다. 개발자는 원문 대응표·
정상/금지/미시험 분기·기존 어댑터/실행기 호출·팩/정책 호환과 합성 회귀 설계를 바로 작성할 수 있다.

### A가 검토해 B에게 전달할 M9 프롬프트 초안

```text
너는 gua 정책 개발 담당 B다. M8 최신 HEAD·PR·실제 CI와 작업 지침을 확인하고
docs/POLICY_M8.md·POLICY_PROMPT.md·SPEC.md·PROGRESS.md·DECISIONS.md·카탈로그를 읽어라.
M8의 bool 문맥/FAIL 보존·실제 상태와의 구분·구형 팩/증거 계약을 먼저 검토하라.

M9는 P-DRAFT-INPUT-ENDPOINTS/FORMATS의 설계 문서 작업이다.
REQ-07/08/09/10 원문과 CheckDef, 실제 어댑터·input_validation 실행기·판정표·보고 호출을 추적하라.
허용 endpoint와 등록/시험 endpoint, 입력 형식과 실제 parser 관측을 구분하라.
METHOD/path·형식 식별자·대소문자/별칭·빈/중복/미등록 값·범위 밖 경로·미시험 분기의 의미를 정리하라.
기업 목록이나 값을 만들지 말고 기존 REQ-09의 token-array/두 경계와 실제 미검증 범위를 보존하라.

docs/POLICY_M9_PLAN.md에 정책/ENV/실행 설정/EVD 소유권, 최소 타입·정책/증거 계약,
판정 표·반례·구형 팩/부분 정책·선정 범위·baseline/candidate 호환, 실제 변경 함수와 필요한 회귀를 적어라.
코어 수정이 필요한지와 정책 플래그/목록만으로 PASS·비적용이 생기지 않는 조건을 설명하라.
기업 결정이 필요한 것과 바로 구현 가능한 기술 선택을 구분하고 작은 다음 구현 묶음 하나를 권고하라.
그 범위를 명시한 M10 구현 프롬프트를 복사 가능한 형태로 포함하라.

제품 코드·테스트·팩·CSV·기업 정책 예시·원문·증거는 바꾸지 마라.
문서를 모두 작성한 뒤 링크·코드 위치·JSON Pointer·예시·판정 표·보존 범위를 검증하고 오류를 고쳐라.
PROGRESS·DECISIONS의 해당 기록을 갱신하고 이번 문서만 커밋·푸시·초안 PR로 제출하라.
실제 실행하지 않은 테스트·엔진·정책 승인을 완료로 보고하지 마라. main 직접푸시·자동병합·유료 자원은 금지다.
```
