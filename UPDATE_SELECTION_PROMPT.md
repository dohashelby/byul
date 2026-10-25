변경 유형별 시험 선정(U1)을 구현하는 작업이다. 먼저 `CLAUDE.md`, `SPEC.md`의 §0·§1·§4.3·§4.4·§6·§7.2와 맨 끝 "U1" 절, `docs/UPDATE_SELECTION.md` 전체, `DECISIONS.md`의 "U1 개정"·"U1 개정 2" 절과 열린 질문 U1-Q2~Q6, `PROGRESS.md` 맨 위 항목을 읽어라. 설계는 사용자와 합의가 끝났다. **`docs/UPDATE_SELECTION.md`가 이번 작업의 정본**이다. 거기 없는 결정이 필요하면 추측하지 말고 멈추고 물어라.

요약: 엔진 정식 릴리스(major·minor 변경)는 지금처럼 적용 점검 전체를 기준·후보로 시험하고, vLLM 소스 diff는 보고서(바뀐 영역·도달 확인·체크리스트 공백)에만 쓴다. 엔진 패치 릴리스(0.xx.1 …)는 항상 시험 20개와 diff 변경 종류에 걸리는 점검만 기준·후보로 시험하고 나머지는 이전 PASS를 이월한다. 엔진 이미지가 같은 모델·환경·정책 변경은 증분 감사(audit + `--previous-plan`)로 걸리는 점검만 시험하고 나머지는 이월한다.

**시작 조건**: PR #30·#31은 2026-10-25 main에 병합됐다(`fb0c103`, 팩 22, REQ-09 rev6·PLT-04 rev3). 최신 main에서 시작하고 시작 커밋을 PROGRESS에 기록한다. REQ-09 길이 원인 프로파일은 `src/guard/adapters/reviewed_profiles/`의 데이터 파일이다.

이번 작업은 사용자가 **한 묶음**으로 승인했다(2026-10-24·25). 구현 순서는 설계 §8.2를 따른다(CHG 제품 연결 → 자동 추출 → 선정과 이월 → 대상별 도달 확인). 아래 0~6을 모두 구현한 뒤 테스트 → 실패 수정 → 필요한 재검증 순서로 진행한다. 이 승인에는 `core/`·`models/` 변경, INV-05 개정, INV-19 추가, 새 플러그인 종류(엔진 소스 diff 추출기, 모델 manifest 추출기), vLLM 저장소 clone(네트워크)이 포함된다. 그 밖의 `judges/` 변경이나 판정 규칙 변경(예: 미도달 변경을 UNKNOWN으로 낮추기, U1-Q2)은 승인 범위가 아니다.

공통 규칙
- 판정 규칙·기존 기대값을 결과에 맞추려고 고치지 마라. 기존 audit 모드(`--previous-plan` 없음), 기존 `guard update`(`--previous-plan` 없음)의 시험 범위, `guard.update_validation`의 동작은 그대로여야 한다(팩 revision 증가로 팩 Ref가 바뀌는 것은 예외이며 그 범위를 보고한다). 기존 저장 증거·과거 팩 보존본의 재판정 결과도 그대로여야 한다.
- 코어(`core/`·`models/`·`store/`)에 엔진 이름을 쓰지 마라. vLLM 경로 규칙·추출기는 `adapters/`에만, 모델 파일 형식 규칙은 `collectors/`에 둔다.
- 새 Python 의존성 추가 금지. git은 시스템 `git`을 subprocess 인자 배열로 호출하고 `--no-ext-diff --no-textconv`를 쓴다. 파일 해시는 `git cat-file blob`의 원본 바이트로 낸다. 분석 대상 소스는 import·실행하지 않는다. clone은 `--filter=blob:none`으로 하고, 소스 파일이 필요하면 태그 단위 `--depth 1` clone을 쓴다(blobless clone에서 checkout으로 파일을 펼치면 매우 느리다, 설계 §3.1).
- 네트워크는 `guard diff`의 vLLM 저장소 clone/fetch에만 쓴다. 엔진 상태를 바꾸는 요청은 보내지 않는다.
- 새 객체 필드는 기본값이 있는 선택 필드로 추가해 과거 객체 해시가 바뀌지 않게 한다.
- 기본 `uv run pytest`는 네트워크·Docker 없이 통과해야 한다. 네트워크가 필요한 새 테스트는 새 마커 `network`로 분리하고 기본 실행에서 뺀다(기존 테스트에 새 skip·제외를 붙이지 마라). 시간 제한 gate를 추가하지 말고 pytest 시간과 부모 벽시계를 기록한다.
- **git commit / push는 하지 마라.** 마지막 보고에 내가 실행할 git 명령을 적어라.

## 0. 체크리스트 갱신
1. `gh api -H "Accept: application/vnd.github.raw" repos/mg-princess/serving-security-framework/contents/data/checklist.json`으로 최신 파일을 받아 `reference/framework/data/checklist.json`을 교체한다. 받은 커밋 SHA(작성 시점 `729216b1`)를 DECISIONS에 기록한다. 그 사이 원본이 또 바뀌었으면 멈추고 알려라.
2. 77행·순서·`subtest_ids`가 이전 판과 같은지 확인한다(작성 시점에 77행 모두 같았다). 다르면 멈추고 알려라.
3. `tools/import_checklist.py`가 `framework_id`(`id` 필드, 예: `CHK-REQ-001`)와 `mechanism_ids`를 CheckDef로 가져오게 한다. 내부 ID(`REQ-01`)와 `id_map.yaml`의 대응은 유지한다. 질문 문구가 전부 바뀌었으므로 대응은 `subtest_ids`·`legacy_display_number`로 확인하고, DECISIONS POL-P1-06의 "유일 subtest 73개·의미 수동 대조 4개"와 결과가 같은지 대조한다.
4. 팩을 재생성하고 revision 변화를 보고한다. 기존 override(`tools/overrides/*.yaml`)의 자동화 정의가 그대로 살아 있는지 확인한다.

## 1. 기업 입력 유지
- `guard env update --base ENV-… --patch patch.yaml`, `guard policy update --base POL-… --patch patch.yaml`: 최신 revision에 patch를 덮어 같은 id의 새 revision을 만든다(append-only). patch 형식은 보수적으로 정하고 DECISIONS에 한 줄 적는다.
- 두 revision 사이의 입력 diff(바뀐 ENV 사실 키·runtime·model, 바뀐 POL 점검 값·범위·예외·상태)를 계산하는 순수 함수를 `core/`에 둔다.

## 1.5 CHG 제품 연결 (설계 §8.2-1)
- 사람이 쓴 CHG 파일을 읽는 로더를 두고, `guard prepare-update`·`guard update`·`guard select`가 **같은 검증 함수**를 쓴다. 준비 단계(`prepare-update`)는 저장소·출력을 만들지 않는다.
- `python -m guard.update_validation`도 CHG·이전 PLAN 입력을 CLI로 전달하고 출처를 보존한다. 단 이 도구의 수락 기준(기존 다섯 점검)은 넓히지 않는다.
- CHG 근거 파일을 내용 주소 artifact로 저장하고 CHG가 참조한다. `guard verify`·`guard evidence export/restore`가 CHG 근거의 누락·변조를 검출해야 한다(테스트로 고정: 삭제·변조·복원 후 연결).
- 분석 시각·개인 경로·임의 ID를 시험 조건 해시에 넣지 않는다. CHG가 없으면 모든 경로가 기존 동작과 같아야 한다.
- 잘못된 구조·해시·없는 점검 ID는 실행 전 거부한다. 기존 형식의 과거 CHG는 읽되 소급해 검증 완료로 올리지 않는다.

## 2. 엔진 소스 diff (`guard diff`, 정식 릴리스 보고 / 패치 릴리스 선정)
설계 §3.1 그대로: clone/fetch 캐시, 태그→커밋 고정, name-status diff, 경로 규칙(`adapters/assets/vllm_change_rules.yaml`), 제외 규칙, 미분류, 이미지 대응 확인. import 전파는 하지 않는다. 결과는 CHG로 저장한다.
- 규칙 파일의 첫 내용은 설계 §8의 1차 실험 규칙을 출발점으로, 실제 vLLM 저장소 구조를 보고 확인해 만든다. 경로를 지어내지 마라. `CMakeLists.txt` 같은 빌드 파일은 `IMAGE`로 넣는다.
- 정식/패치 판별(설계 §1): major·minor가 같고 패치 자리만 올라가면 패치. 롤백·rc·dev·버전 확인 불가·같은 버전의 다른 digest는 정식 취급(전체 시험). 판별 함수는 엔진 중립 버전 비교로 `core/`에 두고, 엔진별 버전 문자열 해석은 어댑터가 한다.
- `guard update ... --change CHG-…`이면 보고서에 바뀐 영역(변경 종류별 파일 수·관련 점검, 설계 §3.2 표), 미분류 파일 목록과 "규칙표에 추가할 후보", 체크리스트 공백 후보를 넣는다. 시험 범위는 바뀌지 않아야 한다(테스트로 고정).
- 이미지 대응 확인은 불일치·관계 미확인·관계 확인 세 상태로 남긴다(설계 §3.1-5).
- 도달 확인(설계 §3.3): 실험 전 진입점 변경 표시(실행기가 다루지 않는 진입점 포함), 실험 후 바뀐 진입점마다 다섯 단계(선정·범위 포함·역할/안전 프로브·시도·관측)와 근거 종류(TCP 차단/HTTP/handler 도달) 기록, 미도달 변경 목록과 수동 시험 대상 등록. 판정은 바꾸지 않는다(U1-Q2 잠정). 테스트 사례에 "점검은 선정됐지만 새 관리 경로가 어댑터 분류 때문에 범위에서 빠짐"을 넣는다.
- 테스트는 임시 디렉토리에 만든 작은 가짜 git 저장소(태그 2개)로 오프라인 실행한다: 경로 규칙, 제외 규칙, 이름 변경, 미분류, 이미지 대응 불일치, 시험 범위 불변.

## 3. 모델 manifest diff (`guard model-diff`)
설계 §4.1 그대로: snapshot 파일 목록(경로 → sha256) manifest 생성(기존 `tools/model_inventory.py` 계산 재사용), 두 manifest 비교, `config.json`·`tokenizer_config.json`·processor 설정의 지정 키 비교(`auto_map`, `chat_template`, `architectures`, `model_type`, `vocab_size`, `max_position_embeddings`), 변경 종류 판별, 규칙에 없는 파일 → 모든 종류, 모델 id 변경 → 모든 종류. 결과는 CHG(`extractor.name: model_manifest`)로 저장한다. 테스트는 임시 디렉토리의 가짜 snapshot으로 종류마다 1개 이상.

## 4. 패치 릴리스·증분 감사 선정과 이월
설계 §1·§3.4·§4·§5·§6 그대로. 팩에 `change_selection.yaml`(엔진 변경 종류 → 점검 표, 패치 항상 시험 20개, 모델 변경 종류 → 점검, `fact_triggers`)을 추가하고 매니페스트 해시에 포함한다. `fact_triggers`는 체크리스트 구조화 필드에서 끌어낼 수 있는 것만 넣고, 넣은 수와 연결 없는 사실 수를 보고한다. PLAN에 `previous_plan`, `change_type`, `triggers`, `carried_over`를 기록하고 `NotSelectedReason.CARRIED_OVER`를 추가한다.
- 불변식 테스트를 **구현보다 먼저** 쓴다: `tests/invariants/test_inv_05.py`를 개정 문구로 고치고(기존 "변경이 영향 없음이어도 기본 점검 유지" 사례는 update 모드 전체 시험 사례로 옮겨 유지), `tests/invariants/test_inv_19.py`를 새로 만든다.
- 필수 사례: 정식 릴리스(minor 변경)는 이전 PLAN·CHG가 있어도 전체 선정·이월 없음 / 롤백·rc·버전 확인 불가 → 전체 / 패치 릴리스는 항상 시험 20개 + diff 변경 종류 점검만 선정하고 나머지 이월 / 패치에 미분류 엔진 파일 → 변경 종류 표 전체(56) + 항상 20 / 패치에서 이미지·diff 버전 불일치 → 전체 / 패치 이월은 이전 시험 이미지 = 이번 기준 이미지일 때만 / 패치 선정 점검은 기준·후보 둘 다 실행·비교 / 모델 변경 종류마다 해당 점검과 공통 점검 선정 / 규칙에 없는 모델 파일 → 모든 모델 종류 / POL 값 변경 → 그 점검만 / POL 범위 추가 → 새 점검 / POL 예외·상태만 변경 → 재시험 없음 / 연결된 ENV 사실 변경 → 참조 점검 / 연결 없는 ENV 사실 변경 → 전체 / `--previous-plan` 없음 → 전체 / 이월 조건 하나씩 깨기(이전 FAIL, UNKNOWN, 이미지 digest 다름, 점검 revision 다름, 변경 트리거에 걸림, EXAMPLE) → 각각 선정 / NOT_APPLICABLE은 미선정 / 증분 감사는 한 쪽만 실행하고 Comparison을 내지 않음.

## 5. 보고와 CLI
- `guard update … --change CHG-… --previous-plan PLAN-…`로 패치 릴리스 선정을 하고, 선정된 점검만 기준·후보로 실행한다. `--previous-plan`이 없으면 기존 동작(전체)과 같아야 한다.
- `guard select --env … --policy … --previous-plan PLAN-… [--change CHG-…]`로 증분 감사 PLAN을 만들고 기존 `guard run/judge/report --plan`으로 끝까지 돌게 한다.
- 패치 릴리스 보고서: 변경 유형, 항상 시험·diff 트리거별 선정 사유, 이월 표, "이번 실행 N건 / 이월 M건", 바뀐 영역, 미분류 파일과 "규칙표에 추가할 후보".
- 보고서(JSON 원본 → HTML): 증분 감사는 변경 유형, 트리거별 선정 사유, 이월 표, "이번 실행 N건 / 이월 M건", 같은 점검의 이전 결과(참고). 이월 PASS는 집계에 포함하되 `carried` 표시(U1-Q5 잠정).
- SPEC 본문은 이미 U1 개정이 반영돼 있다. 구현하면서 SPEC과 다르게 해야 할 곳이 생기면 고치지 말고 멈추고 물어라. `docs/UPDATE_SELECTION.md`에 구현 결과(명령 예, 파일 위치)를 짧게 추가한다.

## 6. 검증
1. v0.31.0 → v0.32.0 실제 저장소로 `guard diff`를 실행하고 설계 §8의 1차 숫자(변경 경로 2,801·무시 1,146·분류 1,645·미분류 10, 변경 종류별 파일 수, 공백 후보 105개 파일)가 재현되는지 확인한다. 다르면 이유를 보고한다. 엔진은 띄우지 않는다.
2. 과거 패치 5개(v0.25.0→v0.25.1, v0.27.0→v0.27.1, v0.20.1→v0.20.2, v0.22.0→v0.22.1, v0.19.0→v0.19.1)에 패치 선정을 적용해 설계 §8 표(25·29·32·32·38/77, v0.19.1은 미분류 `CMakeLists.txt` 1개)와 비교한다. `CMakeLists.txt`를 `IMAGE`로 넣었으므로 v0.19.1은 미분류 0이 되어야 한다. 다르면 이유를 보고한다. 엔진은 띄우지 않는다(선정까지만).
3. 저장소에 기존 두 이미지 실증의 PLAN·EVD가 있으면 그 PLAN을 `--previous-plan`으로 써서, 가짜 모델 변경(예: template만 바뀐 snapshot 사본)과 가짜 정책 변경으로 증분 감사 PLAN을 만들어 선정 N/77·이월 M을 보고한다. 엔진 실행은 하지 않는다(선정까지만).

4. 평가 건수(설계 §8.1): 1·2의 결과에 대해 필수 연결 누락, 근거 없는 추가 선정, 미분류 누락, 미지원 확정, 같은 입력 두 번 실행의 결과 차이를 센다. 보안 공지 대조는 평가 사례로만 쓰고, 그 결과에 맞춰 규칙을 고치지 않는다(고칠 필요가 보이면 원인과 함께 보고).

## 완료 기준과 보고
- 기본 `uv run pytest` 전부 통과, `-m network` 테스트 통과(네트워크가 안 되면 그대로 보고). INV-05 개정·INV-19 테스트가 있다.
- 기존 audit·`guard update`·`update_validation` 회귀가 그대로 통과한다.
- `PROGRESS.md`에 "한 일 / 테스트 결과 / 다음 단계 / 막힌 것"을 갱신한다.
- 마지막 보고: 바뀐 파일 목록, `core/`·`models/` 변경 건수, 팩 revision, 검증 숫자, `fact_triggers` 연결 수, 남은 열린 질문(U1-Q2~Q6), 내가 실행할 git 명령, 실행하지 못한 것.
