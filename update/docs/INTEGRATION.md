# 수동 증거·업데이트 기능 통합

작성: 2026-10-21, Asia/Seoul.
통합 브랜치: `codex/gua-integration-20261021`.
부모: main `d9e9729`와 codex/gua-update-mvp `42a609d`. 기존 두 체크아웃은 보존했다.

## 보존한 기능

- main: 수동 양식·DECLARED 입력/판정, 수동 증거 보호, 출처·대체 이력·건수, HTTP 요청 묶음과 14개 골든 시나리오, 캐시 추적 정리.
- update 브랜치: 제품 update/prepare-update, side 분리, 명시적 CPU 비교, 불완전 수집의 거짓 PASS 방지, 팩 보존본 재판정, 증거 반출·복원과 정리 실패 기록.
- 객체 7종·append-only·정본 불변식, 정책/팩 DRAFT와 EXAMPLE 구분을 유지한다.
- 입력자 operator/default_judge와 CPU/cleanup_errors/기능 비교를 함께 직렬화한다. 없던 선택 필드는 생략해 양쪽 부모의 과거 해시를 유지한다.

## 수동 입력 수정

1. `collected`는 실제 bool 또는 null만 받는다. `"false"`, 숫자, 목록 등을 bool로 강제 변환하지 않는다.
2. 같은 양식의 `(check_id, side)` 중복은 미기입 항목까지 포함해 저장 전에 거부한다.
3. 관측 키/설명 중복, 잘못된 notes·첨부·제한사항 타입을 먼저 검증한다.
4. 첨부 전체를 읽고 모든 EVD를 생성·검증한 뒤 저장한다. PLAN 참조는 hash까지 resolve한다.
5. EVD 배치는 `Store.put_many`의 한 SQLite 트랜잭션이다. 뒤 객체의 충돌/DB 오류가 나면 앞 객체의 새 revision도 롤백한다.

형식/중복/첨부 읽기 오류는 객체와 artifacts 저장 전에 실패한다.
저장 단계의 I/O 실패에서 새로 추가된 미참조 내용 주소 파일은 남을 수 있다.
파일과 SQLite를 묶는 새 저널 시스템은 도입하지 않았으며, 기존 증거를 지워 정리하지 않는다.
이 경우에도 객체 배치가 부분적으로 커밋되지는 않는다.

`collected: null`의 기존 의미는 유지한다: 값이 주어졌으면 수집 신고, 값도 없으면 미수집이다.
수집 여부를 명확히 적으려면 따옴표 없는 `true`/`false`를 사용한다.
기존에 저장된 수동 증거는 재작성하지 않는다.

## 제품 명령

`guard evidence`에는 `template`, `import`, `export`, `restore`가 함께 있다.

```sh
uv run guard evidence template --plan PLAN-ID --include-automated --out manual.yaml --store /test/store
# 필요한 항목만 채움. 자동 점검 대체는 override_reason 필요. 값/첨부는 시험용 자료.
uv run guard evidence import manual.yaml --store /test/store
uv run guard run --plan PLAN-ID --store /test/store
# 기본 실행은 사람이 입력한 최신 증거를 유지한다.
uv run guard run --plan PLAN-ID --replace-manual --store /test/store
# 명시한 경우에만 새 실행 revision을 추가한다. 이전 수동 revision은 남는다.
uv run guard judge --plan PLAN-ID --store /test/store
uv run guard report --plan PLAN-ID --store /test/store --json /test/report.json   # 화면: byul platform (UPDATE_REPORTS_DIR)
uv run guard evidence export --store /test/store --output /test/evidence.zip
uv run guard evidence restore --bundle /test/evidence.zip --destination /test/restored
uv run guard verify --store /test/restored
```

수동 판정은 자동 측정으로 바뀌지 않으며, default_judge=declared와 입력자/대체 사유는 증거에 남는다.
수동 증거에 자동 실행의 비교 해시를 복사하지 않는다. 조건이 없는 수동 증거는 두 이미지 실측 비교를 완성하지 않는다.
수동/자동 실행 출처와 MEASURED/DECLARED/EXAMPLE 등급은 서로 다른 축이다.

## 범위와 남은 일

팩은 revision 10/DRAFT이며 실행기·판정기가 연결된 정의 5개를 보존한다.
main에서 보류한 REQ-09의 제한 실행 코드는 보존하되 기본 real 설정에서는 활성화하지 않는다.
PLT-04·REQ-09의 신규 실제 엔진 검증 완료 주장은 하지 않는다. 기존 실제 3개 기록과 구분한다.
REQ-07·REQ-10 추가 자동화, 정책 카탈로그/양식의 실제 작성, 담당자·기업 임계값 확정은 이번 범위가 아니다.
정책값·담당자·공식 승인 기록을 만들지 않는다. CPU 성능은 기존 결정대로 UNKNOWN이다.
실제 엔진/서로 다른 두 이미지 실증과 과거 누락 artifact 복구는 [실증 안내](VALIDATION.md)의 후속 과제다.

## 검증 계약

- 잘못된 collected와 중복 입력 거부, 정상 false의 UNKNOWN 유지.
- 뒤 첨부 읽기/스키마 실패에 앞 입력 미저장, DB 두 번째 insert 실패 시 전체 EVD 롤백.
- CLI 자동→수동→기본 run 보존→replace-manual→재판정→반출·복원.
- 양쪽 부모 코드로 직렬화한 EXAMPLE 객체 fixture의 전체 필드와 해시 보존.
- HTTP 골든의 관측·대조·챌린지·마스킹·안전 프로브 동작 보존. 비교 조건 미확인 시 hash=null 전환은 구분해 기록한다.
- 기본 전체 테스트와 wheel 빌드/설치본 검증. 실제 엔진 시험은 별도 opt-in이며 미실행을 성공으로 세지 않는다.

실행 결과는 PROGRESS.md의 2026-10-21 통합 항목에서 확인한다.


## 검증 결과

- 통합 코드 ff1d488의 [Linux CI](https://github.com/dohashelby/gua/actions/runs/39544534499): **519 passed, 18 deselected / 32.03초**, frozen 설치·wheel 빌드 성공.
- 설치본 독립 CLI 15개 명령과 양쪽 부모의 EXAMPLE 직렬화 fixture 16개를 확인했다.
- 첫 Windows/원격 실행에서 발견한 골든 해시·ANSI 도움말 기대값 문제와 수정 과정은 PROGRESS.md에 기록했다. 제품 동작을 시험 결과에 맞춰 완화하지 않았다.
- 실제 엔진 opt-in 6건은 설정 부재로 skip했다. 실제 엔진·서로 다른 두 이미지·신규 두 점검 실증 완료는 아니다.
- [초안 PR #2](https://github.com/dohashelby/gua/pull/2)에서 통합 변경을 검토한다.
