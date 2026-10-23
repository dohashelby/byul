# M10 REQ-09 endpoint·입력 형식 구현

기준: M9 c945f3dc3caa73af98974ebb0efe7392a5218812, codex/gua-policy-input-m10. [M9 설계](POLICY_M9_PLAN.md)의 단일쌍 계약을 사용자 실행 지시로 채택했다. 기업값·승인·실제 엔진 검증을 확정하지 않는다.

## 변경과 보존

REQ-09에 allowed_endpoints/allowed_input_formats와 input_endpoints_tested/input_formats_tested를 등록했다. CheckDef의 단일 engine POST·정수 prompt=[0]·model={model}·max_tokens=1·expect200 표본을 기준으로 검사한다. 순수 input_policy helper는 정확한 선언/과거/부분 선언을 구분하고 실행 전과 자동·수동 재판정에서 공유한다. 기존 정상3/경계2 요청·vocab/예산/거부 규칙·독립 FAIL을 보존한다.

core/report의 rule_table POLICY_CONTEXT 전달 한 곳을 보강했다. 나머지 코어·모델·Store·비교·어댑터·HTTP 골격은 변경하지 않는다. 수동 /3의 generic 관측과 --include-automated/override_reason을 사용하고 해당 새 계약의 두 관측 값/collected만 조건부 config hash에 포함한다. /1/2 읽기, 정확한 Ref/side, 과거 객체, 멱등은 보존한다. 새 payload/타입/의존성은 없다.

플러그인: input_validation0.3.0, rule_table0.5.0, declared0.7.0, manual-entry0.5.0. 팩15/DRAFT·REQ09정의5·14정책점검26경로·a23/b4/c161/d115·77ID/303결정595연결·자동연결5. 다른76정의/원문은 보존한다.

## 판정 계약

새 required 키 누락은 NO_POLICY_VALUE→NOT_RUN, 타입 오류는 select 거부, 빈/복수/미지원/표본 불일치는 target/readiness/Docker/probe 전 ABORTED/UNKNOWN이다. 기존 정상/거부/응답 분류를 유지한다. 두 관측 전체 일치·지원 프로파일을 PASS 전제로 사용한다. 새 전제 부족은 PASS만 UNKNOWN으로 제한하고 FAIL과 기존 UNKNOWN은 유지한다. 새 이름이 전혀 없는 과거 정의에는 소급하지 않고 부분 선언은 legacy PASS로 통과시키지 않는다. 수동 값은 DECLARED, parser 실제 소비·전체 보호·기업 승인의 증명이 아니다. 명시 CPU 비교 증거의 성능은 UNKNOWN이며 비교 hash와 조건은 유지한다. 수동 증거에 ENV의 CPU/비교값을 자동 보충하지 않는다.

변경 위치: [공통 의미 검사](../src/guard/input_policy.py), [실행기](../src/guard/executors/input_validation.py), [자동 판정](../src/guard/judges/rule_table.py), [수동 판정](../src/guard/judges/declared.py), [수동 입력](../src/guard/collectors/manual.py), [보고 연결](../src/guard/core/report.py), [정본 override](../tools/overrides/REQ-09.yaml).

## 검증

구현·테스트 코드·문서 초안을 모두 작성한 뒤 검증했다. 아래는 이번 작업의 직접 실행 결과이며 과거 M8/M9 CI 결과와 합산하지 않는다. 환경은 Windows, CPython3.11.16, uv0.12.11이다. 증거는 기존 실행과 분리한 `out/test-runs/policy-m10-20261022/`에 보존한다.

| 직접 실행 | 결과·기록 |
|---|---|
| `uv sync --frozen` | 19개 패키지 확인, lock/의존성 변경 없음 |
| `uv run pytest -q --basetemp=.review-tests-policy-m10-20261022 --junitxml=out/test-runs/policy-m10-20261022/full-first.xml` | 최초892통과/52실패/5skip/18제외,112.98초 |
| `uv run pytest -q tests/test_policy_m10.py tests/test_new_checks.py tests/test_policy_template.py tests/test_probe_cleanup.py tests/test_validation_readiness.py --basetemp=.review-tests-policy-m10-fix-20261022 --junitxml=out/test-runs/policy-m10-20261022/targeted-fixed.xml` | 수정 후143통과/실패0,26.36초 |
| `uv run pytest -q --basetemp=.review-tests-policy-m10-final-20261022 --junitxml=out/test-runs/policy-m10-20261022/full-final.xml` | 최종944통과/실패0/5skip/18제외,206.02초 |
| `uv build --wheel` → 별도 `uv venv`와 `uv pip install --python <별도환경> dist/guard-0.1.0-py3-none-any.whl` | wheel 빌드/설치 성공, 저장소 밖 -I Python과 실제 guard 진입점 사용 |
| `python -X utf8 -B out/test-runs/policy-m10-20261022/installed_smoke.py` | 최종21명령 모두 exit0. 실제 보존 M8 wheel/팩14로 자동·수동 EVD/RES를 생성한 후 M10에서 재판정, 과거 원본/해시·legacy PASS·동일 as_of 멱등·과거 정의 수동 config hash 입력 보존 확인 |
| `python -X utf8 -B out/test-runs/policy-m10-20261022/verify.py` | 팩 재생성 멱등/메타데이터 일치, 다른76정의·참고원문21파일·기존 out61파일 보존, 기업 정책은 팩 revision만 갱신, 코어 변경1파일 확인 |

최초52실패는 새 테스트의 CPU 프로파일 형식35건·수동 판정기 지정/보고서 입력 가정14건과 기존 REQ-09 테스트의 새 required 키 누락3건이었다. 테스트 fixture를 수정했고 제품 판정 기준·불변식은 완화하지 않았다. 설치 검증 도구의 잘못된 CPU 입력과 수동 성능 신고 가정도 수정했으며 첫 두 실패 로그를 별도로 보존했다. 수동 성능은 시험하지 않았으므로 해당 설치 검증 입력은 UNKNOWN으로 신고한다.

skip5는 기존 Windows 닫힌 루프백 포트 분류3건·symlink 권한2건이다. 기본 marker의 engine/docker18개는 제외했으며 새 skip/marker를 추가하지 않았다. Windows 전체1분 목표는 충족하지 못했다. 지원 Linux 기본 전체의 실제 시간·결과는 최종 커밋 SHA에 대한 CI를 확인하고 PR에 기록한다.

설치본 검증은 `%TEMP%/gua-policy-m10-installed-w027qlfw`에서 수행했다. M8의 input_validation0.2.0·rule_table0.4.0·declared0.6.0·manual-entry0.4.0와 팩14/REQ09정의4를 확인하고, M8 실행기 소스 바이트를 고정 커밋 c77636c와 대조했다. M10 설치본의 버전과 공통 helper 바이트도 확인했다. JSON/HTML·빈 기업 정책 양식·--include-automated 양식은 설치본 CLI로 검증했다. 자동 probe는 합성 EXAMPLE이고 수동 EVD는 DECLARED이며 실제 엔진 시험은 아니다.

### M9 설계 시나리오와 회귀 대응

[M10 테스트](../tests/test_policy_m10.py)의 99개 매개변수화 사례를 사용한다. 모든 사례는 합성 자료이며 엔진 시험이 아니다.

| M9 시나리오 | 구현 검증 |
|---|---|
| T01~02 | 키 누락 NOT_RUN·요청0, scope 밖 제공값의 타입 거부 |
| T03~06 | 의미 오류·복수/변형 목록·정상 표본 불일치, target/readiness/Docker 전 중단과 오프라인 PASS 상한 |
| T07~11 | 실제 collect 요청 구성·400/200/206/timeout/5xx·전후 정상 대조·독립 FAIL |
| T12~16 | 두 범위 관측의 타입/수집/전체 일치, empty=empty/부분 목록 거부, 수동 FAIL/UNKNOWN·공통 관문 보존 |
| T17~18 | 완전 부재 legacy와 부분/중복/잘못된 선언 구분, 실제 M8 wheel/팩14의 자동·수동 증거 호환 |
| T19~20 | CPU 성능 UNKNOWN, fixture/CPU 미확인 차이 비교 불가, 수동 update side 분리; 기존 비교·Ref 회귀도 전체 실행 |
| T21 | 관측 값 또는 collected 변경 시 새 hash/EVD, 다른 점검 hash 입력 보존, 원본/동일 as_of 멱등 |
| T22 | REQ07/08/10 미등록·미연결 및 자동 점검5 유지 |

직접적인 고장 주입을 포함하되 기존 core/select/run/judge/compare 동작을 변경하지 않는다. 기본 fake 실행기는 REQ-09를 지원하지 않으며 가짜 container_probe 주입은 별도 합성 계약이다. Docker stub/실제 엔진/두 실제 이미지 시험은 실행하지 않았다.

## 한계와 다음 단계

전체 기업 목록이 단일쌍이 아니면 값을 잘라 맞추지 않고 관계표 확장이 선행한다. REQ07/08/10, Gateway/다른 endpoint·형식, 금지 경로 전수, 재사용 ID/renderer/sampling, parser 소비·메모리/상태 영향은 이번 범위 밖이다. 합성 probe는 실제 엔진이나 두 이미지 실증이 아니다. 실제 기업 값·담당자·승인·대상/hop·모델/vocab·시험예산/중단조건의 근거는 팀이 준비해야 한다. 운영/유료 자원/실제 엔진은 실행하지 않는다.
