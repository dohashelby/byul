> 역사적 Phase 1 정책 양식 작업 프롬프트다. 아래의 3행 구조화·코드/커밋 금지와 단계별 수치는 당시 범위이며 현행 개발 지시가 아니다. 현재 팩20·정책21점검46경로·자동11의 계약은 [현행 보강](docs/AUTOMATION_HARDENING.md), 최근 진행은 [PROGRESS](PROGRESS.md)를 읽는다. 실행할 작업 범위와 커밋/푸시는 현재 사용자 지시를 따른다.

이 세션의 목표는 **기업 정책값(POL)을 입력할 수 있는 체계를 정비하는 것**이다. 먼저 `CLAUDE.md`(특히 "진행 방식"), `SPEC.md`(§2.1, §3, §4.2 POL, §4.4), `PROGRESS.md`, `DECISIONS.md`, `examples/policy.yaml`, `lab/policy.yaml`을 읽어라.

## 용어 구분 (먼저 이해할 것)
- **점검 정의(CheckDef)**: 무엇을 어떻게 시험하나. `packs/PACK-BASELINE/checks/*.yaml`(원본은 `reference/framework/data/checklist.json`).
- **정책값(POL)**: 기업이 정하는 값. 허용 주체·허용 목록·예산·한도·기한 등. 파일은 `policy.yaml`(SPEC §4.2). **`checklist.json`이 아니다.**
- **환경 사실(ENV)**: 실제 구성에 대한 사실. 파일은 `env.yaml`. 이번 작업 범위 밖.
- `checklist.json`/CheckDef에서 정책 관련 정보는 "무엇을 정해야 하는지"의 **설명**일 뿐 값이 아니다: `policy_to_decide`, `enterprise_decisions`, `numeric_values_pending`(40행), `scope`/`policy_to_decide_scope`, `policy_owner`, 그리고 CheckDef의 `policy_params`(지금 REQ-01·REQ-03·PLT-01 3행만 타입이 정해져 있음)와 `policy_prose`(77행 모두 있음).

## 절대 규칙
- **정책값을 지어내지 마.** 예산, 한도, 기한, 허용 계정, 허용 목록의 실제 값은 기업이 정한다. 네가 만드는 예시 값은 반드시 `EXAMPLE`로 표시하고 "공식 아님"을 유지해. 숫자는 근거(측정 또는 기업 결정) 없이 채우지 마.
- `policy.yaml`의 `status`는 DRAFT, `confirmed_by`는 비워둬. 확정은 사람이 한다.
- 판정 규칙, 불변식 INV-01~18, 기존 점검의 동작을 바꾸지 마. 새 의존성 금지. git commit/push 금지(마지막에 사람이 실행할 명령만 알려줘).
- 이번 작업은 입력 양식과 문서 정비다. 시험을 실행하거나 엔진을 띄우지 않는다.

## Phase 1 — 정책 카탈로그 작성 (코드·팩 변경 없음)
77행 각각의 `policy_prose`와 `checklist.json`의 해당 필드(`policy_to_decide`, `enterprise_decisions`, `numeric_values_pending`, `scope`, `policy_owner`, `satisfied_if`/`fail_criteria`/`unknown_criteria`에서 정책값을 참조하는 부분)를 읽고 다음을 만들어라.
1. `docs/policy_catalog.csv` + `docs/policy_catalog.md`(요약). 열:
   - 정책 항목 ID(제안), 이 항목을 쓰는 점검 ID 목록, 한 줄 설명, 값의 형태(`str`, `int`, `bool`, `list[str]` 중 지금 select가 지원하는 타입 안에서 표현 가능한지, 아니면 새 타입이 필요한지), 누가 정하는가(`policy_owner` 원문), 숫자인가(예산·한도·기한), 숫자면 어떻게 정하는가(예: 기준선 측정 후 한도 규칙, 기업 SLA), 환경 사실에 의존하는가(ENV 키), 이미 자동화된 점검이 소비하는가.
2. **중복 정리**: 여러 행이 같은 결정을 요구하면(예: 역할별 허용 작업, 공통 예산) 하나의 정책 항목으로 묶고 어떤 행이 공유하는지 표시. 묶은 개수와 원래 행 수를 요약에 적어.
3. 분류: (가) 이미 구조화됨(REQ-01·03, PLT-01), (나) 값 형태가 단순해 바로 구조화 가능, (다) 숫자라서 도출 방법 결정이 먼저 필요, (라) 복잡하거나 형태 불명(예: 표·절차·여러 주체 관계) — 이유와 함께. 불확실하면 단정하지 말고 "불확실"로 표시.
4. 요약: 정책 항목 총수, 분류별 개수, 중복으로 줄어든 비율, 숫자 항목 중 도출 방법이 필요한 개수, **값을 채우기 위해 누가 무엇을 결정해야 하는지의 질문 목록(역할별로 묶음)**. 이 질문 목록이 사람이 팀에 가져가는 산출물이다.
5. 판단이 필요한 열린 질문(예: 숫자 도출 방법의 일반 규칙, 정책 항목 ID 체계)은 DECISIONS.md "열린 질문"과 보고에 적고 **여기서 멈춰서 내 검토를 기다려라.**

## Phase 2 — 내가 "진행"한 뒤에만 (계획 먼저)
- 선택한 (나) 항목에 대해 CheckDef의 `policy_params`를 구조화(팩 revision 증가, 기존 보존 방식 유지). 타입 검증은 기존 `select` 규칙을 그대로 쓴다.
- `guard policy template --pack ...`: 정책 항목마다 설명·타입·정하는 주체·소비하는 점검·값 칸(null)이 주석으로 달린 빈 `policy.yaml`을 생성(`guard evidence template`과 같은 방식). 생성된 양식을 import하는 별도 명령은 만들지 말고 기존 `select`의 검증을 재사용.
- 값이 비어 있는 점검은 기존대로 `NO_POLICY_VALUE` 공백으로 보고서에 나타나는지, 부분적으로 채운 정책이 정상 동작하는지 테스트.
- 완료 기준: 기본 `uv run pytest` 1분 이내 통과, 불변식 유지, 코어 변경 건수 보고(0~1건 예상), PROGRESS.md 갱신.
