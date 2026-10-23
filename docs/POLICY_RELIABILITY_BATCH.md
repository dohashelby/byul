# 정책·로그 증거 신뢰성과 설치 재현성 보강

작성: 2026-10-24, Asia/Seoul. 출발 커밋은 `49526fb6caa9b3885ac912930ac635d069a48e5a`, 작업 브랜치는 `codex/gua-policy-reliability-batch`다. 시작 시 main은 `24f7d90`였고 부모 PR #20·그 부모 #19는 OPEN/DRAFT였다. 이번 변경은 #20 위에 쌓으며 기존 PR을 병합·재지정하지 않는다. 사용자 지시에 따라 구현·회귀 코드·문서 초안을 먼저 완성하고 테스트→수정→필요 재검증→커밋·일반 push·초안 PR 순서로 진행한다.

## 작업 범위

| 작업 | 산출물 | 완료 확인 |
|---|---|---|
| A 로그 수집 | OBS-03/09의 수신 byte cap·deadline·부분 수집·파일 출처 일관성 | 로컬 자식 프로세스/대역으로 큰 양쪽 출력·UTF-8·timeout·파일 교체·독립 FAIL 보존 |
| B 저장 로그 재검증 | 원본 RPT/팩/첨부·관측 연결의 읽기 전용 분석 | 정상·변조·foreign·legacy·부분 수집·원본 inventory 보존 |
| C REQ-15 | M13의 제한적 수동 그룹 격리 계약·정책3경로·판정·보고 | ordered pair×6case·축별 unknown·legacy/partial·첨부 귀속·신고 FAIL 보존 |
| D 설치본 CI | 별도 wheel 설치 환경·외부 CWD/Python -I·오프라인 계약 검증 | 설치 경로/bytes·CLI·반출/복원·저장 팩 재판정·새 정책/로그 정상·손상 |
| E 준비 안내 | 기존 준비 결과에 오류 코드/단계/side/check/원인/조치 추가 | 입력 누락·동일 이미지·CPU/모델·미지원 정책·Docker/자원 실패 대역 |
| F 테스트 I/O | 테스트 전용 불변 seed/복사와 순수 수락 graph | 기존 회귀명/선택 범위·독립 통합 경로 보존, 기준 커밋과 공통 집합 비교 |

로그 수집/재검증은 [계약](LOG_EVIDENCE.md), REQ-15는 [M14](POLICY_M14.md), 설치본은 [설치 검증](INSTALLED_VERIFICATION.md)을 따른다. 새 자동 실행기 종류는 추가하지 않는다. REQ-15는 수동 증거 검증이며 캐시 격리 실제 자동 시험이 아니다.

## 준비 실패 안내

기존 `guard prepare-validation`, `guard prepare-update`의 `ready`, `checks`, `check_support`와 종료0/1은 유지하고 `guidance`를 추가한다. 스키마는 `guard-preparation-guidance/1`이며 각 issue는 code/stage/side/check_id/status/blocking/cause/action을 가진다. scope=preparation-only의 engine_started/downloads_performed는 준비 단계에만 해당한다. 이후 명시적 실행 여부는 전체 summary의 mode·stage·commands에서 구분한다. 사람용 summary도 JSON 안에 있어 기존 JSON 소비를 깨지 않는다.

독립 입력 파일은 각각 읽되 팩이 무효이면 그 팩을 요구하는 정책 검사는 UNKNOWN으로 남긴다. 이때 Docker를 조회하지 않는다. 오류 본문·비밀값을 안내에 복사하지 않는다. 파일이 유효해도 의미 검사에서 선행 조건이 실패하면 이후 조건은 미검증이다. 모든 가능한 오류를 전수 검사한 것으로 표시하지 않는다.

`python -m guard.update_validation`의 준비/명시적 실행/기존 실행 분석과 종료0/1/2 계약은 그대로다. 최소5점검 수락·서로 다른 digest·동일 CPU/모델/의미 있는 설정 조건을 완화하지 않는다. 입력 파일/인자가 부족한 실패는 구조화 guidance를 stderr JSON으로 주고 원래처럼 종료1이다. 안전한 출력/모델 경로를 확인하기 전에는 오류 파일을 만들지 않는다. 정상적인 준비 결과는 새 summary.json의 readiness/guidance에서 읽는다.

정적 UNSUPPORTED/UNKNOWN은 준비 `ready`와 별개의 안내다. 정책을 줄이거나 다른 점검을 제거하지 않는다. 총 Docker CPU/RAM 조건을 만족해도 현재 여유 메모리·swap·다른 컨테이너 부하·실제 모델 적재는 UNKNOWN이다. 안내 자체가 이를 계측하거나 Docker/WSL2를 시작·변경하지 않는다.

## 보존할 경계

- 객체7종·append-only·정확한 Ref·팩 보존본·원본 증거/보고서를 유지한다. 현재 팩을 과거 PLAN에 적용하지 않는다.
- 수집과 판정을 구분한다. 새 재분석은 새 MEASURED 실행이 아니며 신고·해시 일치를 호스트/collector 인증으로 표현하지 않는다.
- 독립 FAIL은 다른 수집 누락 때문에 지우지 않는다. UNKNOWN·미지원·미실행·해당 없음과 NOT_COMPARABLE을 구분한다.
- REQ-10 runner 소비·REQ-11 생성 출력 귀속의 미확인을 설정값이나 API 거부로 해소하지 않는다. REQ-11 rev3의 보안 PASS 보류를 유지한다.
- CPU 성능 UNKNOWN, 팩·정책 DRAFT/EXAMPLE의 official=false를 유지한다. 기업 값·승인자·시험 권한은 만들지 않는다.
- 기존 모델·컨테이너·이전 output·동기화 sources는 보존한다. 이번에는 엔진 기동·이미지/모델 다운로드·GPU·유료/운영 대상 시험을 하지 않는다.

## 검증 기록

전체 구현·테스트 코드·문서 초안을 마친 뒤 Windows/Python3.11.16·uv0.12.11의 새 외부 가상환경에서 검증했다. 합성·대역·로컬 자식 프로세스와 실제 엔진 검증은 구분한다. 기준 커밋의 과거 Windows2199통과/7skip/18제외·269.40초는 당시 기록이며 아래 새 측정과 섞지 않는다.

첫 전체는 **2547통과/1실패/13skip/18제외·266.37초**였다. 삭제된 첨부에서 OSError가 공개 분석 API 밖으로 나오는 오류를 고정 ValueError로 정규화했다. CLI 입력/무결성 오류 종료2는 유지한다. 추가 검토에서 원시 수집 cap과 디코딩·마스킹 후 표현 크기의 혼동을 고쳤다. 저장 artifact의16MiB 읽기 상한·정확한 persisted 해시는 유지하며, 확장된 부분 캡처가 재검증 일치하더라도 보안 UNKNOWN을 보존한다. 재봉인 변조 회귀의 enum 입력도 올바른 타입으로 바꿔 직전8개 serializer 경고를 해소했다. 수정 후 로그·준비 안내 집중 회귀는 **127통과/0실패/6skip·24.56초**였다.

최종 기본 전체는 **2551통과/0실패/0오류/13skip/18제외·258.46초**, 경고0이다. 13skip은 Windows 루프백3·symlink 권한5·Linux 컨테이너 파일 계약5다. 기존7skip에 신규 시험의 권한1·Linux 파일5만 더해졌다. 기본 engine/docker18 제외는 그대로이며 실제 Docker stub·엔진·두 이미지 시험은 이번에 실행하지 않았다. [선별 검증 요약](evidence/policy-reliability-20261024.json)에 직접 결과와 한계를 남겼다.

| 동일 환경의 기존 공통 시험 | 결과 | 실행 시간 |
|---|---|---|
| 기준49526fb, 팩20 | 2199통과/7skip, 기본18제외 | 278.39초 |
| 현행, 팩21 | 같은2199통과/같은7skip, 기본18제외 | 235.77초 |

기존2206 node ID를 모두 유지했고 공통 집합에서는 **42.62초·약15.3% 단축**했다. 비교 때만 신규358 node ID를 추가로 제외했으며, 위 최종 전체에서는 모두 포함했다. OS 캐시·부하·팩 구성 차이를 포함하는 단일 순차 표본이다. 제품 loader/Store cache나 기존 회귀 삭제·skip 증가는 없지만 **전체1분 목표는 미달**이다. 순수 수락 graph의 실행 수를 SQLite·첨부 bytes 검증 수로 확대하지 않는다. 실제 Store/pack snapshot·CLI 반출/복원·손상 거부·INV-16 통합 경로는 남아 있다.

재현 가능한 기본 명령은 다음과 같다. 출력/basetemp에는 기존 자료가 없는 새 외부 경로를 사용한다.

```powershell
uv sync --frozen
uv run pytest -q --durations=20 --basetemp C:/guard-verification/new-pytest
uv build --wheel
uv run python tools/verify_installed.py --wheel dist/guard-0.1.0-py3-none-any.whl --output C:/guard-verification/new-installed
```

기준 비교는 `git archive 49526fb6caa9b3885ac912930ac635d069a48e5a`의 별도 소스와 현행 소스를 같은 Python·동결 의존성·기존 node ID·독립 basetemp로 순차 실행했다. 실제 guard import 경로를 양쪽에서 확인하고 기존 선택 ID의 삭제/새 제외/새 skip을 허용하지 않았다. 단일 순차 표본이므로 OS 파일 캐시와 부하에 따른 불확실성이 있으며 통계적 속도 보장은 아니다. 현행 추가 테스트는 별도 전체 실행에 포함한다.

### 설치·과거 호환성과 보존

- 최종 wheel의 패키지87파일은 소스·wheel·별도 설치본 bytes가 일치했다. SHA256은 `895e96e7e078463d852853ee1751831e0e06b86162ce833cda4bd18eea1042e2`이다. 외부 CWD/Python -I에서 제품 네트워크/자식 프로세스를 차단하고 fake CLI·반출/복원·저장 팩 judge/report·변조 거부·REQ15 완결/누락/foreign·부분/legacy·원래 FAIL·읽기 전용 로그 재검증을 확인했다. Windows의 worker 출력도 명시적 UTF-8로 검증했다. 설치 의존성 준비 단계와 오프라인 worker 검증은 구분한다.
- 이전 실제 wheel의 declared0.12.0·팩20에서 수동/3으로 만든 REQ15 legacy PASS/UNKNOWN/FAIL과 REQ16 보안PASS/기능UNKNOWN/성능UNKNOWN을 새 declared0.13.0 설치본에서 저장 팩20으로 재판정했다. 원본 객체·EVD Ref·첨부·기존 판정 축과 official=false를 보존했다. legacy의 과거 성능PASS를 새 REQ15 계약으로 소급 변경하지 않는다.
- 다른76개 CheckDef·보호217파일·기존5개 선별 증거와 원문/과거 out·의존성을 보존했다. 여섯 예시/실험 정책은 팩 revision20→21만 바꿨고 values/scope/승인은 바꾸지 않았다.
- 외부 복사본에서 이전 팩20+현행 generator/override로 팩21을 만들고 현행79파일 bytes와 대조했다. 팩과 policy metadata를 각각 두 번 생성해 일치를 확인했고 소스는 쓰지 않았다. 팩21 SHA는 `14b3288136b37c8221d4e72d021a6cf353e8996b98e8da08f92ba7c73cbcbeb6`이다.

원본 로그/JUnit·설치본·과거 wheel 호환성·보존 inventory는 저장소 밖의 이번 작업 전용 output에 남기며, 저장소에는 선별 검증 요약만 추가한다. Linux CI의 최종 head 검증 결과는 초안 PR의 checks/본문을 따른다.

REQ-15 실제 기업 전체 그룹·신원 정본·대표 주체·entry/consumer·첨부의 진위와 수집 책임·승인은 팀 결정이다. 실제 출력/runner 계측과 두 이미지 실증은 별도 후속이며 이번 코드/합성 검증의 완료와 구분한다.

다음 작업은 기업 정책/수집 책임 확정, REQ10/11의 신뢰 가능한 실제 소비·출력 관측 설계, 준비된 두 pinned CPU 이미지의 새 수락 실증 순서로 잡는다. 테스트1분 목표는 남은 실제 I/O 병목을 계측해 검증 범위를 보존하면서 추가 개선한다. 기업 값이나 신원을 임의로 만들거나 정책을 줄여 통과시키지 않는다.
