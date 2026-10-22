# M12 REQ-16 필수 캐시 문맥 수동 계약

## 1. 범위와 기준

M11 `06efef4a7253e4bce6c76f49d571a8d761f8ad97` / [PR13](https://github.com/dohashelby/gua/pull/13)에서 `codex/gua-policy-cache-m12`로 분기했다. M11이 미병합이므로 PR base는 M11이다. [SPEC](../SPEC.md)이 정본이며 사용자 지시로 [M11 §4](POLICY_M11.md)의 기술 계약을 채택했다. 기업 실제값·담당자·승인·실제 엔진 시험을 승인하거나 추정하지 않는다.

구현은 REQ16의 필수 문맥 정책과 **제한적 DECLARED 수동 PASS 상한**이다. 실제 캐시 격리·신뢰 신원·객체/첨부 진위·공개 공유·통계 보호 검증은 아니다. 새 실행기는 없으며 NO_EXECUTOR가 남는다. M11의 Linux998통과/18제외·Windows993통과/5skip/18제외는 과거 기록이며 M12 결과에 합산하지 않는다.

## 2. 정책·정의·경계

`values.REQ-16.required_cache_contexts`는 required `list[str]`다. 지원 식별자는 `model_generation`, `adapter_content`, `media_identity`다. 기업 전체 목록을 입력하며 비어 있지 않은 고유 부분집합만 첫 프로파일을 지원한다. 모델/adapter/media 기능이 실제 활성이라고 추정하지 않는다.

키 누락은 기존 NO_POLICY_VALUE, 잘못된 타입/null은 select의 기존 PolicyValueError다. []·공백·중복·미지원/혼합 목록은 의미 부족으로 수동 PASS를 보류한다. 재판정에 잘못된 저장 값이 있어도 신고 FAIL은 보존한다. trim·alias·부분 선택·POL 정렬/재작성·기본값은 없다. 빈 정책 양식은 REQ-16:{}다.

REQ16정의2·팩16/DRAFT·77점검·15정책점검27경로·303결정595연결·a24/b3/c161/d115·자동실행5다. 공유 후보 CACHE-CONTEXT의 REQ15/16/17 연결은 보존하되 실제 등록은 REQ16 한 경로뿐이다. PUBLIC-PREFIX·REQ15/17은 미등록이다. 예시/실험실 정책은 팩 Ref만15→16이며 values/scope/승인은 그대로다.

[override](../tools/overrides/REQ-16.yaml)에서 [CheckDef](../packs/PACK-BASELINE/checks/REQ-16.yaml)와 manifest를 생성하고 설명 metadata를 재생성한다. 실행 로직은 [순수 helper](../src/guard/judges/manual_cache_context.py)와 [declared0.9.0](../src/guard/judges/declared.py) 두 파일이다. core/models/store/collector/adapter/HTTP/실행기·의존성·양식 버전·config hash 입력 변경은 없다.

## 3. 수동 증거 계약

기존 /3 generic required_observations의 `declared_cache_context_evidence`에 dict를 넣는다. /1·/2 읽기도 유지한다. 별도 payload나 /4를 만들지 않는다. 모든 새 표식이 없는 과거 정의는 legacy이며 일부/잘못된 선언은 PASS를 보류한다.

상위 필드는 정확히 `version`, `profile`, `sharing_group_ref`, `identity_basis`, `records`다. version=`guard-cache-context-evidence/1`, profile=`same_authenticated_group_private`다. 집단/신원 근거는 비밀값 없는 신고이며 인증된 사실로 승격하지 않는다.

행 필드는 정확히 `context_id`, `case`, `trial_id`, `event_id`, `target_scope`, `workload_ref`, `context_before`, `context_after`, `object_change`, `other_conditions`, `key_relation`, `cache_effect`, `inference`, `observation_detail`, `evidence`다. 필드 추가/누락·잘못된 타입·공백 문자열은 UNKNOWN 사유다. 식별자/문자열을 자동 보정하지 않는다.

| 필드 | 허용값 |
|---|---|
| case | same_context / changed_context / post_control |
| object_change | unchanged / observed / unknown |
| other_conditions | same / changed / unknown |
| key_relation | same / different / unknown |
| cache_effect | before_context_hit / isolated_after_change / after_context_hit / old_context_reused / unknown |
| inference | succeeded / failed / not_attempted / unknown |

unknown은 충족 근거가 아니다. 각 evidence 참조는 정확히 sha256/locator/basis이며 비공백이어야 한다. SHA-256은 소문자64hex와 해당 EVD.artifacts membership을 검사한다. 다른 entry/side에만 있는 첨부는 근거가 아니다. 같은 bytes가 해당 entry에도 실제 첨부돼 있다면 content-addressed hash가 같은 것은 허용한다. 첨부 내용·locator 의미·신원/승인은 자동 판독/인증하지 않는다.

각 문맥에 3case가 필요하다. 중복 (context_id,case), foreign 문맥, 전체 행의 event_id 재사용, trial/target/workload 불일치를 거부한다. 집단은 상위 필드 하나로 신고한다. 사건 순서는 observation_detail와 첨부 locator/basis에 신고하며 별도 timestamp를 만들지 않는다.

| case | 전후/객체·key·효과 |
|---|---|
| same_context | old→old, unchanged, other_conditions=same, key same, before_context_hit |
| changed_context | old→new, observed, other_conditions=same, key different, isolated_after_change |
| post_control | 앞과 같은 old→new 쌍, observed, other_conditions=same, key different, after_context_hit |

post_control의 key different는 바로 직전 요청이 아니라 옛 key와 새 key의 비교다. 변경 후 새 key에서 정상 재사용해야 전체 비활성/miss를 성공으로 숨기지 않는다. 각 문맥의 before는 same_context의 old와 같고 after는 실제 다른 식별자다. 실제 객체·consumer·승인 세대·entry/hit/출력/순서에 대한 설명·첨부를 요구하되 그 진위는 DECLARED 한계로 남긴다. 이름/URI/key 차이·단순 miss/시간 차만으로 실제 보호가 인증되지 않는다.

## 4. 축별 판정과 호환

| 신고 | 보안·기능 |
|---|---|
| PASS + 해당 축 계약 완결 | 기존 첨부/답변·INV11 정상/챌린지 관문을 통과할 때 PASS 후보 |
| PASS + 누락/미지원/상충 | UNKNOWN |
| FAIL | FAIL 보존 |
| UNKNOWN | UNKNOWN 유지 |

보안은 전체 문맥·3case·객체/다른조건/key/cache effect의 제한 계약을 요구한다. 기능은 이 조건과 모든 행의 추론 succeeded를 요구한다. 구조·전제가 유효할 때 추론 failed만으로 별도 신고된 격리 사실을 자동 부정하지 않는다. old_context_reused는 두 축 PASS를 UNKNOWN으로 보류하고 CACHE_CONTEXT_CONTRADICTION으로 표시한다. 독립 신뢰 관측으로 새 자동 FAIL을 만드는 기능은 없다.

신규 계약의 성능 PASS는 기준 부재로 UNKNOWN이다. 비CPU 성능 신고 FAIL/UNKNOWN은 보존하며 명시 CPU는 기존 코어의 성능 UNKNOWN 규칙이 우선한다. 의존 예외도 안전한 CACHE_CONTEXT_EVALUATION_ERROR로 처리하고 보안/기능 신고 FAIL을 지우지 않는다. raw payload/예외 원문을 사유에 복사하지 않는다.

기존 POLICY_CONTEXT 전달을 재사용해 JSON/HTML에 출처·보류 사유·한계를 표시한다. official/escape·비교·Ref·side 규칙은 그대로다. generic 관측 변경은 EVD content hash를 바꾸지만 기존 config hash 입력은 그대로다. 과거 EVD/RES를 덮지 않고 새 declared 판정 버전의 RES를 추가한다. 같은 as_of 재판정은 멱등이다. REQ09 M11·SUP/LIF/OBS 계약과 INV01~18을 유지한다.

## 5. 입력 절차와 합성 예시

기존 CLI의 pack/store/env/policy 경로를 자신이 만든 새 M12 위치로 지정한다. 아래 파일명·문맥은 EXAMPLE이며 실제 기업 승인값이 아니다.

1. `guard policy template --pack packs/PACK-BASELINE --out <새경로>/policy.yaml`로 빈 정책을 만든다. 기업 전체 required_cache_contexts와 검토 범위를 사람이 정한다. 합성 시험에서는 EXAMPLE임을 명시한다.
2. `guard select --pack packs/PACK-BASELINE --env <env.yaml> --policy <policy.yaml> --store <새store>`로 PLAN을 만든다. REQ16의 NO_EXECUTOR는 남는다.
3. `guard evidence template --plan <PLAN-ID> --pack packs/PACK-BASELINE --store <새store> --out <새경로>/manual.yaml`로 /3 양식을 만든다. verdicts·첨부·정상 대조·챌린지·관측 답변과 generic 값을 입력한다.
4. 실제 첨부 bytes의 SHA-256을 직접 계산하고 각 record의 evidence에 해당 entry 첨부 hash/locator/basis를 연결한다. [M11 §4.5 예시](POLICY_M11.md)는 합성3행이며 반복 a 해시는 가짜다. 그대로 실제 근거로 쓰지 않는다.
5. `guard evidence import <manual.yaml> --pack packs/PACK-BASELINE --store <새store>`를 실행한다. payload의 파일명/URL을 별도로 읽지 않는다.
6. `guard judge --plan <PLAN-ID> --pack packs/PACK-BASELINE --store <새store> --as-of <고정UTC>`와 `guard report --plan <PLAN-ID> --pack packs/PACK-BASELINE --store <새store> --as-of <같은UTC> --json <새경로>/report.json --html <새경로>/report.html`로 판정·보고한다.

예시 정책 조각은 `values: {REQ-16: {required_cache_contexts: [model_generation]}}`이다. 실제 기업 전체 목록이 아니며 policy status는 DRAFT, confirmed_by는 공란이다. 수동 값의 상위 모양은 아래와 같고 records에 필수 문맥마다 위 표의3행을 전부 입력해야 한다. records=[]는 PASS 예시가 아니다.

```yaml
version: guard-cache-context-evidence/1
profile: same_authenticated_group_private
sharing_group_ref: EXAMPLE-group
identity_basis: EXAMPLE-declared-identity-mapping-and-attachment-locator
records: []  # EXAMPLE 미입력. 문맥별3행과 실제 첨부 hash 필요
```

## 6. 직접 검증

구현·테스트·문서 초안 완료 후 Windows/CPython3.11.16/uv0.12.11에서 아래를 직접 실행했다. 로그/도우미/별도 설치본은 ignored `out/test-runs/policy-m12-20261022/`에 보존하며 과거 결과에 합산하지 않는다.

| 명령/단계 | 직접 결과 |
|---|---|
| `uv sync --frozen` | 19패키지 확인, lock/의존성 변경 없음 |
| `uv run pytest -q --durations=20 --basetemp=.review-tests-policy-m12-20261022 --junitxml=out/test-runs/policy-m12-20261022/full-first.xml` | 최초 **1135통과/4실패/5skip/18제외,187.25초** |
| `uv run pytest -q --durations=20 tests/test_policy_m12.py tests/test_policy_template.py tests/test_policy_m2.py --basetemp=.review-tests-policy-m12-fix-20261022 --junitxml=out/test-runs/policy-m12-20261022/fix.xml` | 수정 관련 **188통과/1skip,27.75초**, 실패0 |
| `uv run pytest -q --durations=20 --basetemp=.review-tests-policy-m12-final-20261022 --junitxml=out/test-runs/policy-m12-20261022/full-final.xml` | 최종 **1139통과/실패0/5skip/18제외,299.01초**. 신규146사례 포함, 신규 사례 JUnit 누적7.804초 |
| `uv build --wheel --out-dir out/test-runs/policy-m12-20261022/dist` | wheel 성공. 소비 버전 설명 metadata 수정 후 재빌드/재설치 |
| `uv export --frozen --no-dev --no-emit-project --format requirements-txt --output-file out/test-runs/policy-m12-20261022/requirements.txt` → 별도 `uv venv ... --python 3.11` → `uv pip sync --python <설치본Python> <requirements.txt>` → `uv pip install --no-deps --python <설치본Python> <wheel>` | frozen 런타임14의존성+wheel 설치 성공 |
| `python -X utf8 -B out/test-runs/policy-m12-20261022/installed_smoke.py`와 `resume_installed.py` | 저장소 밖 Python -I/실제 CLI **최종35명령 exit0**. 아래 보조검사 가정 수정 후 해당 지점만 재개 |
| `python -X utf8 -B out/test-runs/policy-m12-20261022/final_install.py` | 최종 wheel **추가3명령 exit0**, 패키지61파일과 소스 바이트 일치·빈 정책 CLI 재확인 |
| `python -X utf8 -B out/test-runs/policy-m12-20261022/verify.py` | 팩 Ref/manifest·metadata 일치, 카탈로그3행만 변경, 비대상 원본·기존out61·core8/models7/store4/adapters5/executors11/reference21 보존, 정책은 pack Ref만 변경, 상대링크 검증 |

최초4실패는 Windows 텍스트 쓰기의 CRLF 첨부 bytes와 테스트에 미리 계산한 LF hash가 달라서였다. 실제 판정기는 ATTACHMENT_NOT_IN_ENTRY로 올바르게 UNKNOWN을 냈다. 테스트 fixture를 정확한 bytes로 쓰도록 수정했다. 이후 보고서에는 비선정 행도 있다는 기존 계약에 맞춰 REQ16 행을 선택하도록 테스트/설치본 확인 코드를 고쳤다. 설치본 최초 확인의 KeyError는 이 보조검사 가정 오류이며 제품 실패가 아니다. 실패 로그를 보존하고 성공한 이전 단계는 불필요하게 반복하지 않았다. 정합성 보조검사의 소비 버전 문구 대조에서 누락된 공백도 실제 catalog/metadata에 맞춰 수정했다. 제품 판정·해시·불변식은 완화하지 않았다.

설치 호환에서는 실제 보존 M11 wheel의 declared0.8.0 바이트를 고정 커밋과 대조하고 팩15 hash `19fa4f3fd0c2348f8ad5384bb32eac74071c02bb342d6818b1c7205dfe12da63`를 확인했다. **이번 실행에서** 그 과거 설치본으로 legacy REQ16과 SUP/LIF/OBS의 /3 양식·EVD/RES를 만들고 M12로 재판정해 축별 결과·원본 Ref/hash·무결성과 멱등을 대조했다. 같은 M11 설치본에서 생성한 REQ09 정상 자동/수동 EVD도 M12에서 보존했다. REQ09 상충/FAIL 경로는 기존 M11 회귀를 포함한 기본 전체에서 확인했다. 새 설치본의 실제 policy template→select→evidence template/import→judge→report에서 세 문맥 완결 PASS/성능UNKNOWN과 old_context_reused의 보안/기능UNKNOWN, config hash 불변·EVD hash 변경·원본 보존·JSON/HTML manual 출처·official=false를 확인했다.

기존 skip5는 Windows 루프백 timeout 차이3·symlink 권한2이고 기존 engine/docker marker18을 제외했다. 새 skip/제외는 없다. Windows1분 목표는 미달이며 서로 다른 실행의 시간 차이를 제품 성능 개선으로 단정하지 않는다. 최종 SHA의 지원 Linux 기본 전체·wheel·durations 및 원격/base/diff는 초안 PR에 기록한다.

신규146사례는 잘못된 타입/목록·세 문맥과 전수 커버리지·누락/상충/첨부 귀속·신고 PASS/FAIL/UNKNOWN·의존 오류/순수성·legacy/부분 선언·/1/2/3·side·원본/hash/멱등·CPU/INV11을 검증한다. 커밋 전 staged 공백 검사에서 새 Python 두 파일의 끝 빈 줄을 정리했고 AST가 동일함을 확인한 뒤 wheel 재빌드·재설치·최종3명령을 다시 확인했다. 합성/DECLARED 계약이며 실제 엔진·Docker stub·두 실제 이미지·운영·유료 자원 시험은 하지 않았다.

## 7. 미검증과 후속

기업 전체 문맥과 실제 활성 객체/consumer·승인 세대, 인증 집단·신원 귀속, 첨부 수집/검토 담당, 공개 분류·정책 승인, 시험 대상/권한/예산/정상 대조/중단 조건은 미정이다. EXAMPLE/DECLARED 검증은 이 업무 결정을 대신하지 않는다.

PUBLIC-PREFIX·REQ15 신원/공개 공유·REQ17 통계·실제 캐시 자동 실행은 후속이다. core 변경/새 수치/양식 확장 없이 제한 수동 계약을 먼저 검증한다. 실제 엔진·Docker stub·두 실제 이미지·운영·유료 자원 시험과 M13은 이번에 수행하지 않는다.
