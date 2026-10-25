# DECISIONS

- 2026-10-25 · 사용자 승인으로 현행 개발·제품·CI·gua 시험 보조 Python을 3.12(`>=3.12,<3.13`)로 전환하고 기존 의존성 버전은 호환에 필요한 경우만 바꾼다. 별도 3.11용 제품은 유지하지 않고 과거 증거·팩·실행 환경 기록과 대상 vLLM 이미지 계약은 보존한다. · 지원 환경을 통일하면서 판정·해시·Ref·과거 재현 근거를 바꾸지 않기 위함이다.

- 2026-10-25 · U1 회귀 평가에 선택적 면별 부분 정답과 정확한 CHG/CAS 결합을 사용하고, 0분모·근거 부족·과거 PACK Ref 부재를 별도 한계로 표시한다. · rename 합집합이나 exit 0을 정확성 입증으로 오해하지 않으며 기존 판정·선정·이월 계약을 보존하기 위함이다.

- 2026-10-25 · git_diff 0.2.0의 artifact /2는 rename 양쪽의 분류를 보존하고 부분 미분류를 OTHER로 별도 기록한다. 기존 classified 집계와 과거 /1 저장본은 유지한다. · 한쪽 인식으로 미분류가 숨는 결함을 고치되 기존 보수적 선정·판정 계약과 과거 근거 해시를 보존하기 위함이다.

- 2026-10-24 · 모호한 REQ-09 길이 응답에는 명시적 추가 원인 관측을 별도 증거로 연결하고, PLT-04의 비어 있지 않은 무해한 후보는 동일 worker 주체의 메타데이터만 독립 시험한다. · 기존 판정/수락 기준과 과거 증거를 보존하며 source pin·실제 신원·모델·정상 대조를 확인하지 못하면 UNKNOWN이고 파일 본문·실제 자격 사용을 인증하지 않는다.

- 2026-10-24 · 사용자 승인으로 PR #29를 main에 병합하고 641f12f 위에서 REQ-09·PLT-04만 보완한다. · 새 자동화 확대 전 판정 의미·출처와 과거 계약을 보존한다.
- 2026-10-24 · REQ-09는 HTTP 성공·정상 생성만으로 소비/피해 FAIL을 확정하지 않고, 검토된 요청별 거부 근거가 없으면 UNKNOWN으로 둔다. · 상태코드와 실제 소비를 혼동한 오탐을 막고 확인된 위반·신고 FAIL은 보존한다.
- 2026-10-24 · 사용자 선택에 따라 forbidden_mount_paths는 명시 마운트 금지로 유지하고 선택 입력 credential_candidate_paths와 동일 worker 주체의 접근 권한 관측을 분리한다. · 기존 금지 위반을 없애지 않으며 후보 누락은 UNKNOWN, 명시 []는 후보 미지정의 부분 범위이고 자격 본문·내용 인증·실제 사용은 검증하지 않는다.
- 2026-10-24 · PACK22의 REQ-09 정의6·PLT-04 정의3에만 새 의미를 적용하고 판정표의 declared_pass_ceiling으로 수동 PASS 상한을 선언한다. · 다른 75개 정의·코어·저장소와 과거 팩21 재판정을 보존하고 판정기에 새 점검 ID 분기를 추가하지 않는다.

- 2026-10-24 · v1 마감 후보는 PR #24·#25·#28만 통합하고 #26 폼은 별도, #27 실패 이력은 문서 링크로 보존한다. · 새 기능 확대보다 의존 오류 보호·최종 회귀·현재 상태 정합성을 먼저 검증한다.
- 2026-10-24 · PR #28 대비 제품 차이가 수동 declared 예외 보호뿐이고 자동 다섯 점검 경로가 같다면 기존 실측을 통합판의 새 복원본에서 재판정한다. · 저장된 재판정 분석만으로 대체하지 않고 원래 as_of·저장 팩·원본 불변과 판정/비교를 확인하며, 새 엔진 실행으로 표시하지 않는다.

- 2026-10-24 · CPU 기동 준비 생성 1회와 전체 경로 합집합 fixture를 명시적 실행 옵션으로 분리한다. · REQ-01 본시험의 timeout·정상 대조와 INV-09의 동일 시험 자료 조건을 유지하고, 안전 probe는 소스로 확인한 버전·digest만 추가한다. 사용자는 60초를 넘겨도 시간 기록 후 계속하도록 지시했다.

## 2026-10-24 — 정책·로그 증거·설치 재현성 묶음

- 사용자의 직전 통합 프롬프트 실행 지시로 A~D 구현과 E/F 추가 작업, 전체 구현 후 테스트/수정·문서·커밋/일반 push/초안PR을 진행한다. 기존 runtime/판정/정의/CLI/CI 범위 변경은 이 지시로 승인되며 기업 값·승인·실제 엔진 시험은 포함하지 않는다. 새 의존성·객체·실행기 종류·범용 DSL을 만들지 않는다.
- BASE49526fb에서 codex/gua-policy-reliability-batch로 분기한다. 시작 시 PR20/19는 미병합, main24f7d90이다. 부모 PR을 병합/재지정하지 않고 새 초안을 쌓는다. 로컬 변경/원문/과거 팩/증거와 sources는 보존한다.
- REQ15는 M13의 false-only private_groups_server_overwrite 계약·정책3키·/3 generic 증거·순수 PASS 상한을 채택한다. 실제 자동 캐시 실행기가 아닌 제한 수동 계약이다. true·shared-service·안전 거부 방식·전체 신원/승인 인증은 지원 완료로 확대하지 않는다.
- 기존 로그 수집의 저장 후 크기 제한과 수신 중 제한을 구분한다. bounded stdout/stderr·시간/출처/원시bytes 의미와 조회 중 변경을 보강하고, 저장 캡처 분석은 원본을 고치지 않는다. legacy는 당시 관측 범위만 해석하고 새 수집 보장을 소급하지 않는다.
- 준비 guidance는 기존 ready/check_support/종료 코드의 의미를 보존하는 추가 정보다. 입력 독립 조회와 정책 의존 미검증, 총 자원과 여유 자원 미계측을 명시한다. 원시 예외/자격값을 안내에 복사하지 않는다.
- 테스트 속도는 테스트 전용 불변 seed/깊은 복사·순수 graph로 개선한다. 제품 팩 검증 cache·회귀 삭제·기존 skip/제외 증가는 금지한다. 구현 완료 뒤 기준 커밋과 같은 기존 집합을 비교하고 1분 달성을 미리 보장하지 않는다.

## 2026-10-24 — 두 이미지 CPU 비교 수락 도구

- 사용자 지시로 다음 개발 프롬프트의 구현 전체→테스트/수정→문서화·커밋·일반 push·초안 PR을 진행한다. 기존 5점검 한정이며 새 정책/실행기/의존성·7객체 변경·코어 판정 완화는 없다. 원격 PR19 미병합이므로 f4541ec에서 codex/gua-update-validation으로 분기하고 부모 브랜치에 초안을 쌓는다. 기존 PR 병합/재지정/rebase/force-push는 수행하지 않는다.
- 수락은 원본 RPT manifest의 10EVD/10RES와 CPU/모델/fixture/config·고정 판정표·정상 대조/챌린지·필수 관측·구체 보안/기능 판정·반출/복원·동일 저장 팩 재판정을 요구한다. 확인된 FAIL은 유효한 시험 결과이며 배포 승인과 구분한다. CPU 성능 UNKNOWN·팩/정책 승인 상태를 보존한다. MEASURED 라벨과 해시는 호스트 인증이 아니다.
- 같은 프로파일에서 이미지 digest만 다른 비교를 수락 대상으로 한다. 같은 버전의 다른 이미지도 version-upgrade와 구분한다. 프로파일 변경은 config hash 차이로 NOT_COMPARABLE이 맞으며 진단을 FIXED/REGRESSED로 꾸미지 않는다. 기존 후속 계획의 혼동 문장만 수정한다.
- 재판정 중 예외 만료 시각이 바뀌는 문제를 피하려고 update에 기존 judge/report와 같은 선택적 --as-of를 추가한다. 기본 현재 시각 동작은 유지한다. 도구는 한 시각을 기록해 세 명령에 전달하며 원본 store 대신 별도 replay-store를 쓴다.
- 현재 Docker는 가용하지만 로컬 vLLM 이미지는 한 종류라 실제 두 이미지 실행은 미수행이다. 이미지/모델 수신·기존 컨테이너 중지/삭제는 하지 않는다. 후보 버전·실제 ENV·자원 조율은 팀 결정이며 임의 후보 digest를 만들지 않는다. 코드/오프라인 검증은 이 환경 제약과 독립적으로 완료한다.

날짜 · 결정 · 이유 (한 줄씩). SPEC과 참고 문서가 충돌하면 SPEC을 따르고 여기 적는다.

## 결정
- 2026-10-23 · 실제 OBS-09 시도01에서 메모리 LF display digest와 Windows CRLF 첨부 바이트의 차이를 확인했다. 표시 내용과 첨부 Ref는 보존하고 고정 UTF-8 바이트 저장·회귀·새 OBS 단독02로 기록 digest=첨부 digest를 검증한다. 관련 없는 REQ-11/PLT-01 실측은 반복하지 않고 시도별 소스 SHA를 남긴다. · [직접 검증과 원본 보존 범위](docs/AUTOMATION_PHASE3.md#검증-기록).
- 2026-10-23 · 사용자 확인에 따라 자동화 3차는 REQ-11 직접 일반/SSE 두 연결 귀속과 OBS-09 실제 native INFO→guard 참조 수집 경로를 구현·최종 테스트·문서·커밋·푸시·초안 PR까지 수행한다. 새 실행기·declared 상한은 이 범위의 구현에 포함하며 코어·객체·저장소·의존성은 유지한다. · [실행 프롬프트](docs/AUTOMATION_PHASE3_PROMPT.md).
- 2026-10-23 · 외부 request ID는 비신뢰 상관값이다. REQ-11은 실제 소비 prompt IDs/text와 원 연결을 대조하고, ID 충돌 자체·프로토콜 불완전·CPU 클라이언트 구간 중첩을 유출 또는 worker 병렬성으로 확대 해석하지 않는다. · 기업 principal/권한·취소·재시도는 별도.
- 2026-10-23 · OBS-09는 명시적 request-info-v1 고정 argv/env·실제 source SHA를 기록하며 임의 logging config를 받지 않는다. LF/CRLF를 보존한 CLI 디코딩 입력→순수 참조 parser→별도 JSON/escaped HTML 첨부를 검증하고 process/level/source 외 신뢰 필드를 만들어내지 않는다. · raw daemon·기업 SIEM·main RPT 상세 표시의 인증과 구분.
- 2026-10-23 · REQ-11의 두 문자열·OBS-09의 두 문자열과 필드 목록은 원래 복합 기업 정책의 제한 입력 투영이다. 비어 있지 않은 미지원 기업 값은 형태상 유효하나 수집/수동 PASS 상한에서 UNKNOWN이다. 317행 카탈로그 prefix와 과거 팩·실증은 보존한다. · 기업 기본값을 자동 채우지 않음.
- 2026-10-23 · REQ-07 첫 CPU 실증의 30초 cold 생성 timeout은 UNKNOWN으로 보존한다. template 실행기와 별도 chat 예제의 기술 관측 timeout만 상한/설정 120초로 보수하고 300초 새 송신 마감·남은 시간 제한·기업 POL·판정표·자원 상한은 유지한다. 기존 실행 안의 retry나 warmup 대체는 하지 않고 새 시도로 검증한다. · 첫 응답 30.095초 timeout, 후속 79ms 소비 일치의 실제 근거.
- 2026-10-23 · 자동화 2차는 사용자의 후속 구현·테스트·문서·커밋·푸시·초안 PR 계속 진행 지시에 따라 고정 CPU chat fixture, REQ-07 실행기와 declared 상한, 기존 REQ-13 private_system 분기, CPU 시간 제한 행렬 계약을 한 묶음으로 구현한다. 코어·객체·저장소는 유지하고 새 의존성은 추가하지 않는다. · [범위와 실행 프롬프트](docs/AUTOMATION_PHASE2_PROMPT.md).
- 2026-10-23 · `guard-text-chat-v1`은 읽기 전용 별도 자산으로만 제공한다. 기존 모델 snapshot·기본 completion 경로는 보존한다. 실제 mount 바이트/이미지/argv와 소비 prompt IDs를 확인하며 요청 임의 Jinja·fallback·멀티모달·기업 template 승인 전체를 입증하지 않는다. · CPU fixture의 정상 요청과 정확한 소비 증거를 분리.
- 2026-10-23 · REQ-07 정책의 양의 입력 한도에는 임의 기업 상한을 두지 않는다. 실행 자원 한도와 분리하고 정확한 소비 최댓값·실제 시험 경계를 저장하여 다른 POL 재판정의 과거 true 플래그 재사용을 막는다. 허용 override=true는 유효한 입력이나 초기 deny 프로파일에서 UNKNOWN이다. · 수동 신고 PASS에도 같은 순수 검증·판정표 상한 적용.
- 2026-10-23 · 1차 CPU 행렬의 14/15·exit1은 보존한다. 짧은 HTTP timeout이 별도 Docker inspect/exec 시간 상한까지 보장하지 않으므로 3번째 사례를 강제 다섯 UNKNOWN 대신 관측 계약으로 평가한다. 과거 자료는 읽기 전용 분석 후 새 위치에 별도 결과를 남긴다. · [2차 계약과 검증](docs/AUTOMATION_PHASE2.md).
- 2026-10-19 · Python 3.11은 uv가 내려받은 인터프리터를 쓴다(사람 승인 "A"). 설치용 네트워크만 사용. · 로컬에 3.14뿐이었음.
- 2026-10-19 · 점검 행 ID는 첫 변환 때 배열 순서로 붙이고 `packs/PACK-BASELINE/id_map.yaml`에 고정한다. 재변환 시 질문 문구 → `subtest_ids`(유일할 때만) 순으로 기존 ID와 대조하고, 못 찾은 새 행은 영역의 다음 번호, 사라진 행은 `retired: true`. · SPEC §2.1 "한 번 붙인 ID는 바꾸지 않는다".
- 2026-10-19 · 자동화 행의 구조화 내용은 원본을 고치지 않고 `tools/overrides/<ID>.yaml`로 덧씌운다. · 재변환해도 구조화가 유지되도록.
- 2026-10-19 · `required_observations`는 SPEC 모양(`[{key,type}]` 목록)을 유지하고 원문은 `required_observations_prose`에 둔다. `applies_when`은 SPEC 모양대로 `prose`를 안에 둔다. · SPEC §3 예시 형태 보존.
- 2026-10-19 · `policy_params`는 구조화된 행만 채우고, 원문(`policy_to_decide`, `enterprise_decisions`)은 `policy_prose`에 보존한다.
- 2026-10-19 · 정책 `scope.include`가 비어 있지 않으면 include에 맞지 않는 점검도 `POLICY_SCOPE`로 미선정한다. · SPEC은 exclude만 명시. 사람 확인("진행").
- ~~2026-10-19 · REQ-01 관측 키는 SPEC 예시대로 `direct_route_status`, `gateway_route_status` 2개.~~ → 2026-10-19 5a에서 경로별 관측으로 대체(아래).
- 2026-10-19 · REQ-01 `executor: direct_route`. 실제 실행기는 5단계에서 만든다. 이번에는 `guard.toml`의 `[executors]`에서 `direct_route = "fake"`로 연결한다.
- 2026-10-19 · `rule_table`의 평면 `pass_when_all`/`fail_when_any`는 security 축이다. 다른 축은 `axes.<축>`에 같은 형식으로 둔다. 규칙이 없는 축은 UNKNOWN. · INV-18.
- 2026-10-19 · INV-11의 PASS 관문(정상 대조 성공·모든 챌린지 시도·필수 관측 수집)은 판정 플러그인과 별개로 judge 코어가 강제한다. FAIL 조건이 수집된 관측으로 성립하면 관문과 무관하게 FAIL을 유지한다. · INV-04.
- 2026-10-19 · 정책 예외의 만료 여부는 판정 시각(`--as-of`, 기본값 현재 시각)을 judge 코어가 받아 확인한다. 판정 플러그인은 시계에 접근하지 않는다. · INV-14.
- 2026-10-19 · FakeExecutor 증거와 예시 env/policy는 `data_origin: EXAMPLE`이다. · INV-02.
- 2026-10-19 · HTML은 jinja2 없이 표준 라이브러리로 렌더링한다. ULID도 표준 라이브러리로 직접 구현한다. · 의존성 추가 금지.
- 2026-10-19 · update 모드(2개 환경)의 적용 판단은 환경별로 계산해 더 넓은 쪽(APPLICABLE > CONFLICT > UNDETERMINED > NOT_APPLICABLE)을 쓴다. · 시험을 빼지 않는 방향.
- 2026-10-19 · 변경 사실이 관련 점검으로 가리키면 NOT_APPLICABLE이어도 선정한다(정책 scope 밖은 제외). 변경이 점검을 빼는 경우는 없다. · INV-05 "추가만".
- 2026-10-19 · CONFLICT도 inventory_complete에서는 "판단받지 않음"으로 본다. · INV-13을 보수적으로 적용.
- 2026-10-19 · 정책은 팩 Ref(id·revision·sha256)가 정확히 같아야 select에 쓸 수 있다. policy.yaml은 id·revision만 쓰고 로더가 해시를 채운다.
- 2026-10-19 · NOT_RUN·ABORTED EVD의 data_origin은 min(PLAN의 data_origin, DECLARED). 관측은 전부 `collected: false`, 챌린지는 `attempted: false`. · INV-02, INV-15.
- 2026-10-19 · run은 CheckDef.required_observations 키·challenge_steps만 EVD에 남기고, 실행기가 빠뜨린 것은 미수집·미시도로 채운다.
- 2026-10-19 · 같은 PLAN·점검·side를 다시 run/judge하면 같은 EVD/RES id의 새 revision을 만든다. 보고서는 최신 revision을 Ref로 고정한다.
- 2026-10-19 · 시연용 FakeAdapter(엔진 미기동)를 `adapters/fake.py`에 둔다. 실행기에 넘길 어댑터 자리를 채우는 용도.
- 2026-10-19 · 판정 플러그인은 PASS/FAIL/UNKNOWN만 낼 수 있다. NOT_APPLICABLE 등 다른 값은 UNKNOWN으로 바꾸고 사유를 남긴다. 적용 여부는 PLAN만 정한다. · INV-15.
- 2026-10-19 · 판정기가 없는 점검(76행)의 RES는 세 축 모두 UNKNOWN, 사유 "manual judgment required". · INV-10.
- 2026-10-19 · RES의 verification_level = weakest(실행 완료 시 EVD data_origin, 아니면 null; PLAN 조건들의 origin). · INV-12.
- 2026-10-19 · 보고서 집계에서 정책 범위 밖(POLICY_SCOPE) 점검은 빼고, NOT_APPLICABLE 점검은 NOT_APPLICABLE로 넣는다. 범위 밖만 있는 목적·영역은 집계 항목을 만들지 않는다. 범위 밖 개수는 limitations에 적는다.
- 2026-10-19 · update 모드의 목적·영역 집계는 후보(candidate) 기준. (8단계에서 재확인)
- 2026-10-19 · RPT에 `official`(정책 CONFIRMED·팩 REVIEWED·EXAMPLE 미사용일 때만 true)과 `policy_status`를 추가. 행별 `tested_while_undetermined`, `exception`(승인·유효한 예외만) 추가. · INV-17, §4.4 "적용 미정 상태로 시험함", INV-08.
- 2026-10-19 · 미충족 정렬의 review_order 순서: reference_baseline_first → implementation_path_first → conditional_when_selected.
- 2026-10-19 · 비교표의 한쪽이 NOT_APPLICABLE이면 NOT_COMPARABLE. · SPEC 표에 없는 칸.
- 2026-10-19 · `guard verify`를 저장소 일괄 검증으로 함께 만들었다(SPEC §9·§10). 승인 기록 명령(`approve`)은 만들지 않았다.

### 5a (2026-10-19)
- 2026-10-19 · REQ-01 관측을 경로별로 바꾼다: `routes_registered`, `routes_tested`, `direct_outcomes`, `gateway_outcomes`, `trusted_origin_reachable`. SPEC §3·§4.5·§6(INV-03 주석)·§7.2를 함께 고쳤다. · 원문 unknown 기준("/v1만 확인") 반영, 무응답 차단 PASS 표현.
- 2026-10-19 · `rule_table`에 연산자 `nonempty`, `all_in`, `any_in`, `any_between`, `all_between`, `superset_of`(+`value_obs`)와 규칙 전제 `when`을 추가. 코어에 REQ-01 분기 없음. · 최소 추가.
- 2026-10-19 · 등록 경로 = 엔진 `/openapi.json` ∪ Gateway 설정의 알려진 경로 − 정보성 경로(`/health`, `/ping`, `/version`, `/openapi.json`, `/docs`, `/redoc`, `/docs/oauth2-redirect`, `/metrics`). `/metrics` 노출은 OBS 영역에서 본다. · 사람 확인.
- 2026-10-19 · 직통 결과 분류: HTTP 상태코드 | `refused`(ECONNREFUSED) | `timeout` | `unreachable`(ENETUNREACH/EHOSTUNREACH) | `dns_error` | `error`. `refused`·`timeout`·`unreachable`은 양성 대조가 true일 때만 차단 근거. `dns_error`·`error`는 UNKNOWN. · 사람 확인.
- 2026-10-19 · 경로별 대표 직통 결과는 "키 없음"·"잘못된 키" 두 요청으로만 만든다. 순서: 2xx > 그 밖의 코드·dns_error·error > 401/403 > timeout/unreachable > refused. 출발점이 여럿(외부 컨테이너·호스트)이면 같은 순서로 합친다. 허용 키 직통 결과는 `direct_allowed_outcomes`로 증거 파일에만 남기고 판정에 쓰지 않는다. · 사람 확인.
- 2026-10-19 · 등록된 경로에서 직통 404는 UNKNOWN. · 사람 확인(열린 질문 5).
- 2026-10-19 · `gateway_outcomes`는 허용 키 Gateway 경유 응답. Gateway의 무키·오키 거부는 증거 파일에만 남긴다(REQ-01 판정 대상 아님).
- 2026-10-19 · Gateway는 nginx(`nginx:1.27-alpine`), 테스트 키는 실행 시 `secrets`로 임의 생성(허용/비허용 2개). 환경변수 이름만 docker에 넘기고(`-e NAME`) 값은 파일·로그·증거에 남기지 않는다. · 열린 질문 1.
- 2026-10-19 · 고정 모델 `facebook/opt-125m` + revision 고정(어댑터 인자). 이미지 digest는 실행 시 읽어 ENV에 기록. · 열린 질문 2.
- 2026-10-19 · 실험실 env는 DECLARED, `engine.name: lab-stub`. 실험실 정책은 **DRAFT**, `confirmed_by` 비움(실측 때 사람이 정함). · 사람 수정(원안 CONFIRMED → DRAFT).
- 2026-10-19 · 실험실(stub) 대상 실행 증거의 `data_origin`은 **EXAMPLE**. stub은 vLLM이 아니고 MEASURED면 official 보고서가 나올 수 있으므로. 실행기는 대상이 실험실이면(`target.lab: true`) EXAMPLE, 아니면 MEASURED로 표시. · 사람 결정.
- 2026-10-19 · 팩 검토자는 아직 없음. 팩 DRAFT 유지. · 열린 질문 4.
- 2026-10-19 · Docker 제어는 `docker` CLI를 `subprocess`로(`runtime/docker.py`). 모든 호출에 timeout 필수, 이름 접두어 `guard-<run_id>-`와 라벨 `guard.run=<run_id>`, 포트는 127.0.0.1에만, `DOCKER_HOST` 등은 코드에서 설정하지 않고 그대로 통과. · 열린 질문 6.
- 2026-10-19 · pytest 임시 폴더를 프로젝트 안 `.pytest_tmp/`로(`--basetemp`). · "폴더 밖에 파일 만들지 않기".
- 2026-10-19 · 연결은 됐는데 응답 전에 끊기거나 시간이 초과되면 `error`(phase after_connect)로 분류한다. 대상 포트에 도달한 것이므로 차단 근거(`timeout`)가 아니다. `timeout`은 연결 단계 시간 초과만.
- 2026-10-19 · 상태를 바꾸는 관리 경로(`/sleep`, `/wake_up`, `/reset_prefix_cache`, `/load_lora_adapter` 등, `adapters/vllm.py` MUTATING_ADMIN_ROUTES)에는 기본적으로 직통 요청을 보내지 않는다. 그 경로는 `routes_tested`에서 빠져 REQ-01은 PASS가 될 수 없다(UNKNOWN). `allow_mutating_routes = true`일 때만 보낸다. · 엔진 손상 방지, 정직한 미시험 표시.
- 2026-10-19 · SPEC §7.1 `EngineAdapter`에 `routes_from_openapi(doc)`와 `probe_request(route, model)`을 추가(기존 메서드 유지). 실행기가 출발점별로 받아온 openapi 문서를 어댑터가 해석하고, 무해한 요청 모양(`max_tokens: 1`)도 어댑터가 정한다. · 엔진 지식은 어댑터에만.
- 2026-10-19 · 실행기 레지스트리 키는 `<CheckDef.executor>:<구현>`(예: `direct_route:real`)을 먼저, 없으면 `<구현>`(예: `fake`). guard.toml `[executors] direct_route = "real" | "fake"`.
- 2026-10-19 · 직통 출발점: 외부 호출자 컨테이너(엔진 네트워크 밖, isolated) 또는 같은 네트워크(exposed), 그리고 엔진 포트가 127.0.0.1에 공개된 경우에만 호스트. 직통 대상은 엔진 IP(이름 해석 실패를 차단으로 오인하지 않도록).
- 2026-10-19 · 실험실 Gateway는 클라이언트의 Authorization 헤더를 지우고 엔진으로 넘기며, nginx 로그 형식에 헤더를 넣지 않는다.

### 5b (2026-10-19, 맥 CPU 경로)
- 2026-10-19 · 5b는 시험 서버가 아니라 **맥(Apple M5, macOS 26.6.2)의 Docker Desktop(VM: CPU 10, 메모리 7.75GiB, aarch64)에서 CPU 이미지로** 실행. GPU 이미지(`vllm/vllm-openai`)는 쓰지 않음. · 사람 결정(선택지 2).
- 2026-10-19 · 엔진 이미지 `vllm/vllm-openai-cpu:v0.31.0` → `vllm/vllm-openai-cpu@sha256:495c84fe30f3572b83a78100a1e8ba423dbb77fc532ecefebe114e6b77327bd9`(RepoDigests, 멀티 아키텍처 인덱스 digest. arm64 매니페스트는 `sha256:045fb648a61c…`). guard.toml `[vllm].image`와 ENV `engine.image_digest`에 기록.
- 2026-10-19 · **소스 commit 일치 확인 방법과 결과**: 일회용 컨테이너(`--network none`)에서 `vllm.__version__ = 0.31.0`, `vllm._version.commit_id = "gdb9527a46"`(setuptools_scm 형식: g + 축약 hash 9자리), 배포 메타데이터 `0.31.0+cpu`, `direct_url.json` = 로컬 wheel 경로(commit 없음), 이미지 라벨에 commit 없음, `/vllm-workspace`에 `.git` 없음. → 축약 hash `db9527a46`이 고정 commit `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 앞 9자리와 일치, **40자리 전체는 확인 불가**. ENV `engine.source_commit_status: ABBREVIATED_MATCH`, `source_commit: db9527a46`, 보고서 limitations에 "축약 hash만 일치, 전체 commit 미확인"으로 표시. 일치로 단정하지 않는다.
- 2026-10-19 · 모델 `facebook/opt-125m` revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`: HF에서 공개·비게이트로 확인 후 vLLM 이미지의 `huggingface_hub`로 일회용 컨테이너에서 `~/guard-models`(HF cache)에 받음(토큰 없음, 388초). 이후 시험은 `HF_HUB_OFFLINE=1` + 읽기 전용 마운트.
- 2026-10-19 · (아래 "inventory 정의"로 범위 갱신됨) `tools/model_inventory.py`: snapshot 디렉토리 기준, 심볼릭 링크를 따라 실제 내용 해시, `[{path, bytes, sha256}]` 경로순 → 정렬 JSON → SHA-256. 결과 **파일 12개, 753,111,181 bytes, inventory_sha256 `d677f3999b13aab7bf9a7d203568ca63bc2d3fdc6a102b5efc2c719fd816ae6e`**. LFS 3개 파일의 sha256은 HF blob 이름과 일치. BoBProject 보고서의 37개와 **다르다**: 우리 cache 트리는 일반 파일 34개(blob·lock·xet 로그 포함) + 심볼릭 링크 15개로 37과도 맞지 않는다. BoB가 무엇을 셌는지(트리 전체? 다른 revision·allow_patterns?) 확인 필요(열린 질문).
- 2026-10-19 · ENV에 선택 필드 `runtime.platform`("CPU, arm64, Docker Desktop on macOS")과 `engine.source_commit_status`·`expected_source_commit` 추가. 비어 있으면 직렬화에서 빠지게 해 기존 객체 해시를 유지(기존 `.guard/` `guard verify` OK). 보고서 limitations에 실행 환경과 commit 확인 결과를 적는다.
- 2026-10-19 · `adapters/vllm.py` `launch_plan`에 `device: gpu|cpu`(gpu → `--gpus all`), CPU면 `VLLM_CPU_KVCACHE_SPACE`(기본 1GiB), `--shm-size 1g`. 엔진 인자는 계속 `start_command`만 만든다(`--model --revision --served-model-name --host 0.0.0.0 --port 8000`, 컨테이너 안 포트, `--api-key` 없음, `VLLM_SERVER_DEV_MODE` 미설정).
- 2026-10-19 · target.json `lab` 플래그: stub 엔진이면 true(→ EXAMPLE), 실제 vLLM이면 false(→ 증거 MEASURED). ENV는 DECLARED. RES `verification_level`은 증거와 적용 조건 출처 중 가장 약한 것이라 **DECLARED**(gateway 사실이 사람이 쓴 값). 5b engine 테스트의 "verification_level == MEASURED" 단언은 제 테스트 오류였고 EVD `data_origin == MEASURED` + RES `DECLARED`로 바로잡았다(판정 규칙 변경 아님).
- 2026-10-19 · direct_route 실행기는 target `engine.ready_timeout`(상한, 기본 600초)까지 신뢰 출발점에서 `/health` 200을 기다리고, 엔진 컨테이너가 죽거나 시간이 넘으면 예외 → ABORTED(INV-15).

**제안(적용하지 않음, 사람 확인 대기)** — 5b에서 실측한 v0.31.0 CPU 등록 경로(개발 모드 꺼짐) 22개 기준:
- 정보성 제외 목록과의 어긋남: `GET /load`(서버 부하 지표), `GET /kv_event_sources`(KV 이벤트 소스 정보)는 정보성·관측 성격이라 INFO에 넣는 것을 제안. `POST /is_scaling_elastic_ep`는 읽기 전용 상태 조회(현재 admin, 비상태변경으로 직통 요청함, exposed에서 200).
- `POST /v1/responses/{response_id}/cancel`은 사용자 객체 상태를 바꾸는 경로라 mutating 분류를 제안(이번엔 리터럴 `{response_id}`로 보내 404, 실제 객체 영향 없음).
- 경로 템플릿(`{response_id}`)은 존재하지 않는 고정 ID로 치환해 보내는 것을 제안(지금은 리터럴 그대로 → 404 → 해당 경로는 판정상 "그 밖의 코드").
- 기능 축: opt-125m은 chat template이 없어 `/v1/chat/completions` 400, `/v1/messages`·`/v1/responses`·`/generative_scoring`은 `{}` 본문이라 400 → Gateway 허용 키도 200이 아니어서 functional은 UNKNOWN. 경로별 무해 요청 본문을 보강하거나 정상 대조 경로를 추론 경로로 좁히는 것을 제안.

### 5b 후속: TCP 연결 단계 추론 (2026-10-19, 사람 승인)
- 2026-10-19 · isolated UNKNOWN(등록된 상태 변경 경로 `/scale_elastic_ep` 미시험)을 **판정 규칙을 그대로 두고** 증거 수집 방식으로 푼다: 직통 (출발점·대상, 키) 조합마다 먼저 TCP 연결만 시도(바이트 미전송). 연결 자체가 timeout/refused/unreachable이면 HTTP를 보내지 않고 그 조합의 모든 등록 경로(상태 변경 포함) 결과를 그 값으로 기록, 증거 파일에 `basis: tcp_connect (inferred)`. 연결되면 상태 변경 경로에는 계속 보내지 않는다(`allow_mutating_routes=false` 유지). SPEC §4.5(관측 정의가 있는 절; 지시의 "§4.4"는 PLAN 절이라 관측 정의가 있는 §4.5를 고쳤다)·§6 INV-03 주석·REQ-01 override(`observation_notes`)를 함께 고침. 팩 revision 3(REQ-01 revision 3), 정책 예시 2개의 팩 revision을 3으로.
- 2026-10-19 · `routes_tested`의 정의를 "직통 시험 범위"로 바로잡음: 모든 (대상, 비허용 키) 조합의 직통 결과가 있는 경로. 이전에는 Gateway 허용 키 결과도 요구했는데, 상태 변경 경로는 Gateway로도 보내지 않으므로 추론 기록이 있어도 시험 범위에 들 수 없었다. Gateway 정상 대조 범위는 판정표의 `gateway_outcomes ⊇ routes_registered`(기능 축)가 따로 본다. 판정표 자체는 변경 없음.
- 2026-10-19 · 출발점이 여럿이고 일부만 연결되면(예: 외부는 막히고 호스트는 연결) 연결된 조합은 HTTP로 시험하므로 상태 변경 경로는 그 조합 결과가 없어 미시험으로 남는다(PASS 불가).

### 기능 축 (2026-10-19, 사람 결정)
- 2026-10-19 · 기능 축은 `/v1/completions` 정상 대조로 시작하되, 경로를 코드에 두지 않고 REQ-01 override의 `functional_probes`(method·path·body·expect) 목록으로 둔다. 판정은 "목록의 모든 항목이 기대대로 통과"(`functional_probe_results` all_in [pass], 계획 목록 ⊆ 결과, 빈 목록은 PASS 아님), 기대 코드가 아닌 HTTP 응답은 fail → FAIL, 응답 없음은 no_response → UNKNOWN. 목록이 늘어도 판정표는 그대로. 본문의 `"{model}"`은 ENV 모델 이름으로 치환.
- 2026-10-19 · 정상 대조(EVD.normal_control)도 같은 표본으로 정한다(전부 pass일 때만 succeeded). 실행기의 `/v1/completions` 하드코딩을 제거.
- 2026-10-19 · 보고서 행에 `functional_scope`("기능 확인 범위: N / 등록 경로 M (표본)")를 표시. 어느 관측을 셀지는 CheckDef `functional_scope`(probed_obs, of_obs)에 두고 코어는 이름을 모른다. 값이 없으면 직렬화에서 빠져 기존 RPT 해시 유지.
- 2026-10-19 · `gateway_outcomes`(등록 경로 전부의 허용 키 Gateway 응답)는 관측·증거로 계속 남기지만 판정표에서는 쓰지 않는다. 팩 revision 4, 정책 예시 팩 revision 4.

### 경로 분류·inventory 정의 (2026-10-19, 사람 승인)
- 2026-10-19 · `adapters/vllm.py` 경로 분류: `GET /load`, `GET /kv_event_sources` → INFO(REQ-01 대상에서 제외). `POST /v1/responses/{response_id}/cancel` → MUTATING(보내지 않음; TCP 연결 실패 시에는 추론 기록). 경로 변수(`{response_id}` 등)는 존재하지 않는 고정 ID `guard-probe-nonexistent-id`로 치환해 보낸다(관측·판정의 경로 키는 openapi 원문 템플릿 그대로).
- ~~2026-10-19 · 모델 inventory 정의: "snapshot 중 vLLM이 읽는 파일만"~~ → 같은 날 아래로 수정.
- 2026-10-19 · **모델 inventory 정의: 비교용 inventory = 마운트한 snapshot 전체, 참고용 = 엔진이 읽은 파일.** 이유: "엔진이 읽는 파일"은 엔진 버전마다 달라질 수 있어, 후보 버전이 다른 가중치 형식을 고르면 차이가 가려진다. (사람 결정)
  - 비교용 → ENV `model.inventory_sha256`, EVD `comparability.model_inventory_sha256`(INV-09): `tools/model_inventory.py`(기본 `--scope all`). opt-125m@27dcfa74: **12 files, 753,111,181 bytes, `d677f3999b13aab7bf9a7d203568ca63bc2d3fdc6a102b5efc2c719fd816ae6e`**.
  - 참고용 → ENV `model.files_read_by_engine`(판정·비교 조건에 쓰지 않음): `--scope vllm` = 설정·토크나이저 파일 + `load_format=auto`가 고르는 가중치 형식 하나(*.safetensors > *.bin > *.pt, index 포함). opt-125m: **7 files, 251,897,335 bytes, `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`**. 비어 있으면 직렬화에서 빠져 기존 ENV 해시 유지.

### REQ-03 (2026-10-19, 사람 결정·진행 중)
- 2026-10-19 · **검증된 안전 프로브**: 상태 변경 경로는 어댑터(`adapters/vllm.py` `SAFE_PROBES`)의 "엔진 버전 + 메서드·경로 + 안전 요청 + 기대 응답(expected_handler_reached) + 근거 소스 위치" 표에 있는 것만 보낸다. 코어에는 없음. 표에 없는 버전·경로는 계속 보내지 않음(미시험 → UNKNOWN).
  - 확인 방법: 대상 이미지 `vllm/vllm-openai-cpu@sha256:495c84fe…`를 `--network none` 일회용 컨테이너로 띄워 `vllm/entrypoints/serve/elastic_ep/api_router.py`(sha256 `2d6805874a86…`)를 직접 읽음. `scale_elastic_ep`(L37-49): 본문 `{}` → `new_data_parallel_size` None → `HTTPException(400, "new_data_parallel_size is required")`, `client.scale_elastic_ep`(L64) 미도달. 선행 `Depends(validate_json_request)`(`serve/utils/api_utils.py` sha256 `ffb5e08c…` L350-356)는 Content-Type만 검사. `is_scaling_elastic_ep`(L83-85): 전역 플래그 읽기만. 사용자 사본의 설명과 일치.
  - 표: `0.31.0 POST /scale_elastic_ep {}` (Content-Type application/json) → 핸들러 도달 시 400 + "new_data_parallel_size is required"; `0.31.0 POST /is_scaling_elastic_ep {}` → 200 + "is_scaling_elastic_ep".
  - 판정: 응답이 표의 기대 응답과 일치하면 실행기가 `handler_reached`(인가 없이 핸들러 도달)로 분류한다. 일반 4xx는 handler_reached가 아니다(그 밖의 코드 → UNKNOWN). 대표값 순위는 2xx와 같은 최상위.
  - 안전 프로브를 보낸 실행에서는 전후로 신뢰 출발점 `/health`와 기능 표본 1회(추론)를 확인해 증거(`safety.before/after`)에 남긴다.
- 2026-10-19 · REQ-03 판정표: PASS = 관리·진단 등록 경로 nonempty·전부 시험·`nonadmin_outcomes`(키 없음·잘못된 키·일반 역할 대표) 전부 401/403. FAIL = 2xx 또는 handler_reached. 기능 축 = `functional_probes`(일반: `POST /v1/completions` 200, 관리: `GET /metrics` 200). 대상 경로 = 어댑터 종류 admin·diagnostic − 정책 `general_allowed_kinds`. 진단 = `/metrics`, `/load`, `/kv_event_sources`; 생존 확인(`/health`, `/ping`, `/version`)·문서(`/openapi.json`, `/docs`, `/redoc`) 제외. 예시 정책: 일반 호출자 = 추론만.
- 2026-10-19 · **REQ-01 판정표에도 같은 해석을 추가**: `fail_when_any`에 `direct_outcomes any_in [handler_reached]`. 근거: 위 결정의 "핸들러의 기대 응답이 오면 인가 없이 핸들러 도달 = FAIL"을 직통 경로에도 같게 적용. 현재 결과는 바뀌지 않음(exposed는 이미 2xx로 FAIL, isolated는 TCP 추론). **사람 확인: 유지.**
- 2026-10-19 · **handler_reached는 검증된 안전 프로브가 있는 경로에서만 발생한다. 일반 4xx를 FAIL로 취급하지 않는다.** 실행기는 요청이 어댑터 표의 안전 프로브이고 응답의 코드와 본문 문구가 표의 `expected_handler_reached`와 둘 다 일치할 때만 handler_reached로 분류한다. 그 밖의 400·404·422 등은 코드 그대로(판정표상 UNKNOWN). 단위 테스트 `tests/test_handler_reached.py`.
- 2026-10-19 · `/v1/responses/{response_id}/cancel`의 종류를 **inference**로 바로잡음(상태 변경 표시 유지, 보내지 않음). 근거: handler가 `vllm/entrypoints/openai/responses/api_router.py` L110-112의 OpenAI 호환 사용자 API. REQ-03(관리·진단) 범위에서 빠지고 REQ-01(inference·admin) 범위에는 그대로 남는다. (사람 결정 (a))
- 2026-10-19 · 일반화: 어댑터 경로 종류 `info` → `liveness`·`docs`·`diagnostic`; CheckDef `route_scope`(include_kinds, exclude_kinds_policy)로 점검별 대상 종류 지정(REQ-01 = inference·admin, 이전과 같은 15경로); 기능 표본 `as_role`; 실행기 설정 `roles`(역할→키 환경변수, 5b 설정 `allowed_key_env`도 호환); 공통 모듈 `executors/http_common.py`; Gateway 프로필 single·role_open·role_enforced; FakeExecutor 점검별 시나리오(`fake.scenarios`). 팩 revision 5.

### Step 0 — 저장소 위생 (2026-10-19, 외부 검토 반영)
- 2026-10-19 · `.gitignore`: `.venv/ __pycache__/ *.pyc .pytest_cache/ .pytest_tmp/ .DS_Store out/test-runs/`. `.guard/`(로컬 작업 저장소)와 `out/`의 나머지는 사람 결정 전까지 무시하지 않는다(PROGRESS "Step 0"의 분류안).
- 2026-10-19 · `.gitattributes`: 기본 `* text=auto eol=lf`. **해시로 보호되는 파일은 `-text`(줄바꿈 변환 금지)**: `packs/**`(pack.yaml의 files_sha256가 파일 바이트 해시), `reference/framework/data/*.json`(변환기 원본), `tools/overrides/*.yaml`(팩 생성 입력), `out/**/*.json|*.jsonl|*.txt`(증거·보고서·inventory), `**/.guard/artifacts/**`·`**/.guard/work/**`(내용 주소 저장 — 파일 이름이 바이트의 sha256). `*.sqlite`는 binary. 근거: Windows 클론에서 CRLF 변환으로 팩 파일 해시가 어긋나 `load_pack`이 실패하고 테스트 수집이 깨진 외부 검토 사례. `docs/automation_matrix.csv`가 CRLF(파이썬 csv 기본값)라 LF로 바꿈(내용 동일).
- 2026-10-19 · 미사용 `httpx` 의존성 제거(코드에서 import하지 않음, `test_inv_14`의 금지 목록 문자열에만 등장). `uv lock --offline`으로 갱신 — httpx·httpcore·idna 제거. SPEC §10 주석도 고침. (CLAUDE.md의 허용 의존성 목록은 사람 문서라 그대로 둠)
- 2026-10-19 · 엔진 테스트 산출물은 실행별 새 폴더 `out/test-runs/<UTC 시각>-<pid>/<이름>/`에만 쓴다(이미 있으면 실패, 삭제·덮어쓰기 없음). `out/5b`는 보존 증거로 테스트가 읽지도 쓰지도 않는다(`PROTECTED` 단언).
- 2026-10-19 · **지원 플랫폼: macOS·Linux.** Windows는 지원하지 않는다. 외부 검토에서 보고된 Windows 실패 5건 중 원인이 분명한 4건은 `skipif(win32)`와 사유를 달았다: 닫힌 루프백 포트가 연결 거부 대신 timeout으로 보고됨 ×3(`test_inv_03`·`test_http_probe` 2건), 심볼릭 링크 생성 권한 ×1(`test_model_inventory`). 경로 표기 1건은 어느 테스트인지 확인하지 못해 알려진 제한으로만 적는다. 이번에는 고치지 않는다.

### Step 1 — 정확성 보강 (2026-10-19, 외부 검토 반영)
- 2026-10-19 · **등록·시험·관측 키를 `"METHOD path"`로 통일**(`http_common.route_key`). 이전에는 경로만 키로 써서(`routes.setdefault(r.path, r)`) 같은 URL에 GET·POST가 있으면 앞 메서드(GET)만 시험하고 PASS가 날 수 있었다 — 가상 세계 재현(`tests/test_method_keys.py`: GET /multi 401, POST /multi 200 → 수정 전 PASS)을 먼저 쓰고 실패를 확인한 뒤 고쳤다. TCP 연결 단계 추론의 근거(`direct_basis`)도 메서드별. 판정표·core·judges 변경 없음(키는 불투명 문자열). 어댑터 요청의 메서드가 등록 메서드와 다르면 실행기가 거부. 과거 증거(`out/5b`의 경로 키)는 그대로 재판정 가능.
- 2026-10-19 · **정책 값 타입 검증**(`core/select.py`): 지원 타입은 지금 팩이 쓰는 것만 `str`, `int`, `bool`, `list[str]`. `select`는 정책에 값이 있으면 타입을 확인하고, null·잘못된 타입·지원하지 않는 타입은 `PolicyValueError`(점검 ID·키·기대 타입 포함)로 **실행 전에** 거부한다. 값이 없으면 기존대로 `NO_POLICY_VALUE` 공백. 범용 정책 언어는 만들지 않는다.
- 2026-10-19 · **rule_table `value_policy`·`subset_of`·`not_subset_of`**(`judges/rule_table.py` 0.3.0): `value_policy: <키>`는 기대값을 정책 값(POL.values[점검])에서 읽는다. 키가 없으면 그 규칙은 판단 불가 → UNKNOWN(이유 "policy value missing"). `value`와 함께 쓰면 오류. `subset_of`(관측 ⊆ 기대, dict는 키 집합)와 FAIL 조건용 부정 `not_subset_of`. 판정기는 계속 순수 함수.
- 2026-10-19 · **REQ-01 허용 주체 ↔ 실행기 역할**: `direct_route`는 시험 시작 전(요청 0건) 설정의 역할 이름이 모두 정책 `allowed_callers`에 있는지 확인하고, 없거나 정책 값이 없거나 `none`·`wrong`(익명)이 허용 주체로 올라 있으면 거부(→ ABORTED → UNKNOWN). 허용 주체로 취급하는 역할 = 설정 `roles`(결과는 `direct_allowed_outcomes`, 판정 미사용), 비허용 = 키 없음·잘못된 키. 실험실 정책 `allowed_callers: [allowed]`(실행기 역할 이름). `examples/policy.yaml`의 `[gateway-sa]`는 FakeExecutor 시연용이라 그대로(가짜 실행기는 이 확인을 하지 않음).
- 2026-10-19 · 정책 값이 결과를 바꾸는 테스트: REQ-01 `allowed_callers`(일치 → 실행·FAIL, 불일치 → 요청 0건 ABORTED), REQ-03 `general_allowed_kinds`(같은 Gateway 구성에서 [inference] → FAIL, [inference, diagnostic] → PASS), `value_policy`(같은 증거에서 허용 목록에 따라 PASS/FAIL). PLT-01 허용 capability는 Step 3.
- 참고: 복잡한 판정이 필요한 점검은 rule_table을 키우지 않고 순수 함수 Judge로 따로 구현한다(외부 의견 수용, 이번에 구현하지 않음).

### Step 2 — 경로 분류 안전 확인 (2026-10-19)
- 2026-10-19 · `POST /v1/chat/completions/batch` **상태 변경 아님(소스 확인)**: `openai/chat_completion/api_router.py`(sha256 `77f858fe0079…`) L84-108 → `chat_completion/batch_serving.py`(sha256 `0daa49681783…`) L114-160, 대화 N개를 한 요청 안에서 동기 생성 후 반환. 파일 전체에 store·save·persist·write·queue·background 경로 없음. 계속 inference(비상태변경)로 보낸다.
- 2026-10-19 · `POST /v1/responses` **상태 변경으로 표시(보내지 않음)**: `responses/protocol.py`(sha256 `5fa24e326726…`) L210 `store` 기본 True, L167 `background`; `responses/serving.py`(sha256 `c701580373b3…`) L155-193 메모리 저장소(response_store·msg_store·event_store, 제거 없음)·background_tasks, L337-344 `VLLM_ENABLE_RESPONSES_API_STORE`가 꺼져 있으면 store 무시, L513-528 저장. 상태 변경 여부가 대상 서버 환경변수에 달려 있고 실행기는 그 설정을 알 수 없으므로 MUTATING(종류는 inference). 안전 프로브는 만들지 않음. 두 판단은 `adapters/vllm.py` `ROUTE_REVIEWS`에 근거 소스 위치와 함께 기록.
- 2026-10-19 · 결과: REQ-01 exposed FAIL(등록 15, 시험 13 — `/v1/responses`·cancel 미전송), isolated PASS(15 전부 TCP 추론 timeout). REQ-03 범위(관리·진단)에는 영향 없음.

### Step 3 — PLT-01 (2026-10-19, 사람 결정·진행 중)
- 2026-10-19 · **허용 목록이 판단 기준이다.** FAIL = 정책 허용 목록 밖 capability·마운트·장치가 **실제 실행 구성**(docker inspect, 컨테이너 안 모든 프로세스의 유효 capability)에 있음. 특권 모드·호스트 namespace 공유(pid·ipc·net·uts)는 허용 목록에 없으므로 있으면 FAIL. `require_non_root: true`면 UID 0 프로세스가 있을 때 FAIL. 호스트 자원(마운트 목적지) sentinel 쓰기 성공도 FAIL. 컨테이너 자체 파일시스템("/") 쓰기 결과는 증거에만(허용 목록 항목이 아님). tmpfs는 호스트 자원이 아니라 마운트 관측에서 제외(증거에만).
- 2026-10-19 · 유효 capability는 `/proc/1/status`만이 아니라 컨테이너 안 **모든 프로세스**(`/proc/[0-9]*/status`)의 CapEff 합집합. 실측: vllm(PID 1)·python·VLLM::EngineCore·VLLM::Worker·exec한 sh 5개 프로세스. exec는 읽기 전용 명령과 sentinel(쓰기 성공 시 즉시 삭제)뿐. `Config.Env`는 기록하지 않는다.
- 2026-10-19 · 실행기 `container_config`: 대상 컨테이너는 설정 `[container_config].container`(없으면 target.engine.container). 기능 표본은 `via: engine`(신뢰 출발점 → 엔진).
- 2026-10-19 · 일반화: CheckDef `functional_probes[].via`(gateway|engine), CheckDef `limitations`(선정 시 보고서 한계에 그대로), rule_table `when_policy`(정책 bool이 true일 때만 규칙 적용), Docker 래퍼 `cap_add/cap_drop/read_only/tmpfs/user/security_opt`·`exec`·`inspect_json`, 실행기 골격 `requires_roles`·`requires_route_scope`, 실험실 엔진 프로필 default·hardened·overprivileged(모델 마운트는 모든 프로필에서 `:ro`, 위반은 빈 sentinel 디렉토리 rw만). 팩 revision 6.
- 2026-10-19 · 실험실 정책(PLT-01): `allowed_capabilities: []`, `allowed_mounts: ["/models:ro"]`, `allowed_devices: []`, `require_non_root: true`, `allowed_exec_writable_paths: ["/guard-cache"]`.
- 2026-10-19 · **hardened = (c)**(사람 결정): `/tmp`는 tmpfs `noexec` 유지, 컴파일 캐시 전용 exec 가능 tmpfs `/guard-cache`. 캐시 경로 환경변수는 실제 기동 오류로 확인: 1차(`TORCHINDUCTOR_CACHE_DIR`만)에서 실패 경로가 계속 `/tmp/.cache/vllm/torch_compile_cache/…/inductor_cache/…main.so` → vLLM 자체 캐시 루트 `VLLM_CACHE_ROOT`(이미지 `vllm/envs.py` sha256 `1d7101800f67…` L706-709, `compilation/backends.py` L1064) 필요. 최종: `VLLM_CACHE_ROOT=/guard-cache/vllm`(실측 3.0M 사용), `TORCHINDUCTOR_CACHE_DIR=/guard-cache/torchinductor`(오류 메시지가 지목, 실측 사용 0 — /tmp로 가지 않게 유지). `TRITON_CACHE_DIR`는 근거 없어(CPU, 디렉토리 생성 안 됨) 넣지 않음. (a) /tmp exec, (b) 컴파일 끄기는 쓰지 않음.
- 2026-10-19 · **쓰기 가능 + 실행 가능 마운트는 그 자체가 권한**: 정책 `allowed_exec_writable_paths(list[str])`, 관측 `exec_writable_mounts` = 컨테이너 `/proc/1/mounts`에서 rw이고 noexec가 아닌 실제 파일시스템(proc·sysfs·cgroup·devpts·mqueue 등 제외) 중 컨테이너 기본 사용자로 쓰기 시도가 성공한 마운트 지점. 파일 마운트(`/etc/hosts` 등)는 디렉토리가 아니라 제외. `subset_of`/`not_subset_of` + `value_policy`로 판정. hardened는 이 목록에 `/guard-cache`가 있어서만 PASS(목록을 비우면 FAIL — 단위 테스트), `/tmp`에 exec를 준 `tmp_exec`는 FAIL.
- 2026-10-19 · 보고서 한계 문장에 정책 값 채우기: CheckDef `limitations`의 `{키}`·`{len:키}`를 보고서가 정책 값으로 채운다(`core/report.py` `_fill_policy`). PLT-01: "실행 가능한 쓰기 경로 1개가 정책으로 허용됨 ["/guard-cache"] — 엔진 컴파일 캐시…". 팩 revision 7.
- 2026-10-19 · out/5b: 덮어써진 작업 트리 실행본(exposed·isolated·inv15, 팩 revision 5 기준)을 `out/archive/5b-after-req03/`로 복사(31파일, 해시 확인, `.gitignore`). 고정 증거는 커밋본(팩 revision 4) — 복원은 사람이 git으로. `.guard/`·`out/` 분류안 승인 → `.gitignore` 반영. Windows 경로 표기 1건은 알려진 제한으로 유지.

### Step 4 — 팩 보존본 (2026-10-19, 사람 승인)
- 2026-10-19 · `guard select`가 읽은 팩을 저장소의 내용 주소 파일로 보존한다(`store/packs.py`): 매니페스트 정렬 JSON 바이트 → `artifacts/<팩 Ref sha256>`(Ref의 sha256이 곧 그 바이트의 해시), 매니페스트의 파일 → `artifacts/<파일 sha256>`. 새 객체 종류·DB 스키마 없음, `guard verify` 그대로 통과.
- 2026-10-19 · `guard judge`·`guard report`는 디스크 팩이 PLAN의 팩 Ref와 다르면 보존본을 읽는다(매니페스트·파일 해시와 id·revision 모두 검증). 보존본도 없으면 지금처럼 중단. **`guard run`은 계속 현재 팩만**(과거 정의로 새 시험을 돌리지 않음). 보존본을 쓴 보고서는 한계에 "팩 보존본 사용(revision N)".
- 2026-10-19 · `guard pack import --pack <폴더> --store <저장소>`: 과거 팩 폴더(예: `git archive <커밋> packs/PACK-BASELINE`)를 해시 검증 후 보존본으로 넣는다.
- 2026-10-19 · models: 폴더·보존본 공용 검증 `build_pack(매니페스트, 파일 바이트)`, `Pack.path` 선택·`Pack.source`(dir|store).
- 2026-10-19 · **커밋본 out/5b(팩 revision 4) 재판정 수동 확인**: `git archive HEAD out/5b/…`와 `git archive 6d9ad83 packs/PACK-BASELINE`(매니페스트 sha256 `0eeebd125e50…` = PLAN의 팩 Ref)를 `out/test-runs/rejudge-5b-20261019T111117Z`에 꺼내 복사본 저장소에 import → judge → report. 원본 `out/5b` 해시 불변. 결과: exposed FAIL/PASS, isolated PASS/PASS, inv15 UNKNOWN/UNKNOWN — **기존 보고서와 판정·검증 수준·기능 확인 범위 동일**. 차이는 (1) 한계 "팩 보존본 사용(revision 4)" 추가 (2) RES 새 revision(@2): 판정 플러그인 버전 0.2.0 → 0.3.0(판정표 params_sha256·판정·이유·누락 관측 동일). 팩 revision 5(작업 트리·archive·`out/5b/req03-*`)는 커밋된 적이 없어 재판정 불가(보고서·증거는 읽을 수 있음).

### A — 수동 증거 입력 (2026-10-19, 사람 승인)
- 2026-10-19 · **사람의 판정 = 신고된 관측, 도구의 판정기가 PASS 관문을 적용해 RES를 만든다.** SPEC §4.5·§7에 명시. 추가 조건: (a) 신고된 PASS는 첨부 증거 1개 이상, 없으면 UNKNOWN (b) 보고서 한계 "수동 입력자는 인증되지 않은 자기 신고". 필수 관측 문장(CheckDef.required_observations_prose)마다 설명이 없어도 PASS → UNKNOWN(양식이 그 칸을 주므로 같은 원칙으로 적용).
- 2026-10-19 · DECLARED 수동 증거가 있어도 official 인정. 보고서 상단에 영역별 "수동 입력 N건 / 자동 측정 M건"(`RPT.evidence_counts`, 미실행 건수도 JSON에).
- 2026-10-19 · 자동 실행기가 있는 점검은 `override_reason`일 때만 수동 증거를 받는다. 대체 규칙(SPEC §4.5): 최신 revision이 판정에 쓰이고, 출처가 다른 이전 COMPLETED revision이 있으면 보고서 행에 "대체됨". **`guard run`이 사람이 입력한 최신 증거 위에 새 revision(NOT_RUN 포함)을 말 없이 만들던 경로를 막음** — 기본은 수동 증거 유지, `--replace-manual`(RunConfig.replace_manual)일 때만 대체하고 그때도 "대체됨" 표시.
- 2026-10-19 · 판정기 선택은 실행기 레지스트리 메타데이터 `EXECUTOR_DEFAULT_JUDGES`(`manual-entry` → `declared`)를 증거 생성 시 `execution.executor.default_judge`로 기록하고, 코어 judge는 그 값만 읽는다(문자열 비교·executors import 없음, INV-14).
- 2026-10-19 · 명령: `guard evidence template --plan …`(선정된 점검 중 자동 실행기가 없는 것의 빈 YAML 양식: 정상 대조 설명·챌린지 단계 문장·필수 관측 설명·충족/미충족/미확인 기준, `--include-automated`), `guard evidence import 파일.yaml`(첨부 경로는 파일 기준, 전부 검증 후 쓰기). `evidence add`는 만들지 않음(사람 승인 범위). 양식 필드는 `result_template.json` 참고(3축 판정, normal_control_result, evidence, limitations, started_at/finished_at).
- 2026-10-19 · 모델: `Execution.operator{by,at}`·`override_reason`, `ExecutorInfo.default_judge`, `CheckRow.evidence_source`·`replaced`, `Report.evidence_counts` — 모두 비어 있으면 직렬화에서 빠짐(기존 해시 유지). 사람이 입력한 증거(operator 있음)는 모델이 MEASURED를 거부.

## 열린 질문
### U1 (2026-10-25 개정, 변경 유형별 시험 선정)
- ~~U1-Q1 — 패치 릴리스도 전체 시험할 것인가?~~ → 2026-10-25 사용자 결정: 패치 릴리스는 필요한 점검만 골라 시험한다(아래 "U1 개정 2").
- U1-Q2 — 엔진 릴리스에서 실험이 닿지 않은 변경 진입점을 판정에 반영할 것인가(그 점검 PASS → UNKNOWN)? 판정 규칙 변경이다. 잠정: 보고서 표시와 수동 시험 대상 등록만.
- U1-Q3 — 어느 점검과도 연결되지 않은 ENV 사실이 바뀌면 전체 시험으로 할 것인가? `fact_triggers`가 비어 있는 동안 환경 변경이 대부분 전체 시험이 된다. 잠정: 전체 시험(보수적), 구현 후 연결 가능한 사실 수를 보고받아 다시 정한다.
- U1-Q4 — LoRA·adapter 변경을 모델 변경 종류에 넣을 것인가? 잠정: 이번 범위 밖.
- U1-Q5 — 이월 PASS를 보호 목적·영역 집계에 포함할 것인가? 잠정: 포함하되 `carried` 표시.
- U1-Q6 — tool·reasoning 출력 파서(1차 실험 105개 파일)는 체크리스트에 대응 점검이 없다. 체크리스트 공백으로 프레임워크 쪽에 알릴 것인가?
- (브랜치) `codex/gua-pre-phase4-cleanup` — PR #22가 병합 없이 닫혔고 main에 없는 커밋 4개(수동 판정 계약 분리·suite 최적화)가 남아 있다. 이후 #24·#25로 나뉘어 반영됐는지, 버려도 되는지 작성자 확인이 필요하다. 확인 전에는 지우지 않는다.

### POL-P1 (2026-10-21 질문 이력 — 완료 항목과 미결 업무를 구분)
- ~~POL-P1-01~~ — M1에서 선택 1(미입력 키 생략·설명 주석)을 채택했다. 아래 M1 결정과 [정책 양식 계약](docs/POLICY_TEMPLATE.md)을 따른다. 질문 당시 원문: `POLICY_PROMPT.md` Phase 2의 값 칸 null 제안은 현재 select의 null 타입 오류 계약과 충돌한다. 선택 1: 미입력 키 생략 또는 설명 주석만 제공하여 기존 NO_POLICY_VALUE/타입 검증 계약 유지. template/문서/회귀 범위로 끝낼 수 있어 최소 변경으로 권고한다. 선택 2: 명시적 미입력 null/표식의 새 계약을 도입하고 로더·선정·타입/의미 오류·직렬화/해시·보고·과거 정책 호환성을 검토한다. core/SPEC/불변식 영향 검토가 필요하다. 키가 선언되지 않은 새 카탈로그 항목 전체에 현재 NO_POLICY_VALUE가 적용되는 것은 아니다.
- POL-P1-02 — P-DRAFT 제안 ID와 결정 단위/공유 표의 범위를 채택할 것인가? `docs/policy_catalog.csv`의 수는 scalar 값 개수가 아니며 표 내부 역할·대상·모델·부하·주기별 셀은 독립이다. 현재 values.<check_id>.<key>는 보존한다. 유사 명칭만으로 기존/신규 경로를 통합하지 않는다. 원문에 유일한 정책 개수가 없어 원문 대비 중복 감소율은 만들지 않았다.
- POL-P1-03 — 각 수치의 적용 자원·단위·peak/누적·단계/요청/전체 범위·모델/장치/부하·기준선/도출 규칙·시작/완료 사건과 승인 역할은 무엇인가? numeric_values_pending 40행은 수치 정책 40개가 아니며 목록 문장과 자원별 분해 후보를 구별한다. CPU/GPU/디스크·단계/누적·취득/추론 retry·신규/세션/사본 기한을 독립으로 검토한다. 유출·타요청 혼입·메모리 안전 오류는 정상 계산 오차로 수용하지 않는다.
- POL-P1-04 — 정책 결정자·실행 담당자·검토자와 실제 정책/팩 확정 역할을 누가 지정할 것인가? 77행은 legacy_q_ids→items.policy_owner로 연결되지만 원문은 전부 “적용 조직의 정책 승인 담당”일 뿐 실제 지정이 아니다. CSV의 권장 역할은 제안이다. 미등록 ENV 후보와 실행 설정/고정 model/runtime 사실의 공급 책임도 별도로 정해야 한다.
- ~~POL-P1-05의 M1 기술 범위~~ — 아래 M1 결정에서 현재 선언 기반 template까지로 확정했고, 후속 정책 등록은 M2 이후 별도 단계로 진행했다. [M1/M2 계약](docs/POLICY_TEMPLATE.md)을 따른다. 기업 실제 값·승인은 계속 미결이다. 질문 당시 원문: Phase 2 최소 범위를 기존 11개 선언의 template와 미입력 주석으로 할지, 검토된 b 후보 중 어느 ID까지 포함할지 결정해야 한다. 신규 목록의 policy_params 등록은 새 실행기/실측 완료를 뜻하지 않는다. 당시 PLT-04/REQ-09는 모의 계약 기록이며 기존 3개 실측과 수준이 달랐다. REQ-09 양수 한도·한도+1 시험 예산·400/413/422 거부 코드를 유지하고 실제 vocab/서비스 한도 일치를 별도로 확인해야 한다. 당시 계획은 docs/policy_catalog.md에 보존한다.
- POL-P1-06 (2026-10-21 추가 참고) — 사용자 지정 가이드의 팀 참조 기준 28개와 기업 확정값·ENV/관측을 구별한다. 외부 기준은 mg-princess/serving-security-framework / cdfea2401007091f8e5452d87d4ff2fbfaf1219f로 고정하고 카탈로그 §14 및 CSV source_refs에 연결했다. gua 기존 77개 ID와 최신 CHK의 연결은 유일 subtest 73개·의미 수동 대조 4개이며 배열 순서/숫자 변환을 사용하지 않았다. 향후 CHK·112개 분기 설명을 팩/selector에 도입할지와 정책/불변식 영향을 별도 검토해야 한다. 이번에는 원문·팩·코드·기업 값·확정자를 변경하지 않았고 Phase 2를 시작하지 않았다.

- ~~(Step 3) PLT-01 hardened 프로필에서 vLLM이 뜨지 않음~~ → 2026-10-19 (c)로 해결(위). 원문: 원인: Docker tmpfs 기본값 `noexec` — torch inductor가 `/tmp/.cache/vllm/torch_compile_cache/…/*.main.so`를 mmap하지 못함("failed to map segment from shared object"). capability·non-root·읽기 전용 루트 때문이 아님. 선택지: (a) `/tmp` tmpfs에 `exec` 허용(`/tmp:rw,exec,size=2g,mode=1777`) — 실행 가능한 쓰기 영역이 생기는 완화 (b) 엔진 컴파일을 끄는 기동 옵션(`--enforce-eager` 또는 컴파일 단계 낮춤, 어댑터 launch_plan 옵션으로) — exec 불필요, 성능 특성이 바뀜 (c) 컴파일 캐시만 별도 tmpfs(exec)로 두고 `/tmp`는 noexec 유지(`TORCHINDUCTOR_CACHE_DIR`·`VLLM_CACHE_ROOT`) — 노출 범위가 가장 좁음. 결정 전까지 hardened 결과 없음.
- ~~(Step 0) `.guard/`·`out/`의 추적 범위~~ → 분류안 승인.
- (Step 0) Windows 경로 표기 실패 1건 — 알려진 제한으로 유지(사람 결정).
- ~~(REQ-03) `/v1/responses/{response_id}/cancel`의 종류~~ → 2026-10-19 (a) inference로 바로잡음(위). 원문: 어댑터가 이 경로를 MUTATING 집합에 넣으면서 종류도 `admin`이 되어 REQ-03(관리·진단) 범위에 들어왔다. 안전 프로브가 없어 보내지 않으므로 미시험 → 실제 vLLM `role_enforced`(안전 구성)에서도 UNKNOWN. 승인된 결정은 "MUTATING(보내지 않음)"이었고 "관리 경로"는 아니었다. 이 경로는 사용자 자신의 응답 객체를 취소하는 추론 API라 종류는 `inference`(상태 변경)가 맞아 보인다. 선택지: (a) 종류를 inference로 바로잡음(mutating 유지) → REQ-03 범위에서 빠짐, REQ-01은 inference·admin을 모두 포함하므로 영향 없음 (b) admin으로 두고 안전 프로브를 찾음(존재하지 않는 ID로 cancel → 404 등, 소스 확인 필요) (c) 그대로 두고 REQ-03 UNKNOWN. 결정 전까지 바꾸지 않는다.
- ~~(5b) REQ-01이 vLLM v0.31.0에서 PASS가 될 수 없음~~ → 2026-10-19 선택지 (b) 변형(TCP 연결 단계 추론)으로 해결, 위 "5b 후속" 참조. 원문: 개발 모드를 꺼도 `POST /scale_elastic_ep`(상태 변경, MUTATING)가 등록된다. 규칙상 상태 변경 경로는 보내지 않으므로(`allow_mutating_routes=false`) `routes_tested ⊉ routes_registered` → isolated 위상에서 직통 48건 전부 timeout + 양성 대조 true인데도 security UNKNOWN. 규칙은 설계대로 동작했다(미시험을 PASS로 올리지 않음, INV-01·11). 선택지: (a) 그대로 둔다 — 그 경로는 사람이 별도 수동 시험(버리는 엔진, PROGRESS 5b전 7번 절차)으로 확인하고 수동 증거를 붙이는 방식을 설계 (b) 상태 변경 경로는 "직통 연결 단계 차단만" 관측하는 별도 요청 형식(예: 연결만 열고 HTTP 요청은 보내지 않는 TCP 도달성 검사)으로 시험해 `routes_tested`에 포함 — 연결 단계 timeout/refused는 handler 실행 없이 관측 가능 (c) 정책 값으로 특정 경로를 REQ-01 범위에서 명시적으로 빼고 보고서 limitations에 표시. 제 의견은 (b)가 원래 의도(직통 도달 불가 확인)를 해치지 않으면서 엔진 상태를 바꾸지 않는다. 결정 전까지 규칙을 바꾸지 않는다.
- ~~기능 축은 `/v1/completions` 정상 대조만으로 판정할지~~ → 2026-10-19 결정: override `functional_probes` 목록(위 "기능 축").
- ~~BoBProject 보고서의 opt-125m 파일 수 37과 이번 12의 차이~~ → 따지지 않음. inventory 정의는 위 "비교용 = snapshot 전체, 참고용 = 엔진이 읽은 파일".
- 실측 정책 확정자, 팩 검토자 — 계속 미지정(정책·팩 DRAFT, official=false).
- D2 `impact` 값(HIGH/MEDIUM/LOW) — 지금은 전부 null.
- 2026-10-23: HTML 보고서를 담당자용 요약 우선 구조로 개편(`report/html.py`만 변경). 상단에 종합 판정·건수·"먼저 볼 것"(미충족·미확인·후퇴), 전체 표·수동 시험·미선정·입력 목록은 접기(JS 없음). JSON·판정·집계 규칙은 그대로.


## 2026-10-19 — 최신 코드 검토 및 업데이트 경로 구현
- 사용자 결정: 명시적 CPU 프로파일로 보안·기능 비교를 지원하고 CPU 성능은 미검증으로 둔다. GPU·driver null을 CPU라고 추정하지 않는다. 선택 필드 cpu는 비어 있으면 직렬화에서 빼 과거 해시를 유지한다.
- 제품 합성 지점은 application.py로 둔다. CLI/core가 Docker를 직접 제어하지 않고 application만 runtime과 코어 파이프라인을 연결한다. 기존 runtime 경계 테스트는 이 명시적 합성 지점만 추가로 허용한다.
- PLT-01은 불완전 /proc 기록을 버리지 않는다. process_scan_complete=false이면 PASS 불가이며, 이미 확인한 UID 0·capability 위반은 보존한다.
- judge/report는 디스크 팩이 없거나 손상돼도 정확한 보존본으로 재판정한다. run의 팩 불일치 거부는 유지한다.
- 비교 해시는 실행 ID/IP/리소스 이름을 제외하고 위상·네트워크 관계·launch/보조 이미지·역할·실행 설정을 포함한다. launch 비교 정보가 없는 외부 target은 설정 미확인으로 비교 불가다.
- update는 side별 설정과 서로 다른 target 경로를 요구한다. external 실제 대상은 관측 버전·이미지 메타데이터가 ENV와 일치해야 한다. managed는 pinned 이미지/모델 inventory 사전 검사 후 순차 기동·실행·정리한다.
- PLT-04 신규 범위는 환경변수 이름·마운트 경로, REQ-09 신규 범위는 정상 token-array 대조와 ID/길이 경계 두 건이다. 실제 엔진 실증 전에는 전체 점검 실측 완료로 집계하지 않는다. 기업 기준은 POL, vocab_size/출처/요청 예산은 실행 설정으로 구분한다.
- 팩 생성 bytes는 UTF-8/LF로 고정한다. 기존 증거의 누락·해시를 재작성해 숨기지 않는다. 과거 exposed/isolated store의 참조 artifact 각 1개 누락은 별도 원본 복구/재실증 과제다.
- 실제 엔진/두 이미지 실증은 Docker daemon·모델 캐시·비교 이미지가 없는 현재 Windows 환경에서는 미수행이다. 신규 코드 검증과 과거 macOS 실행 기록을 구분한다.
- 2026-10-19 · 사용자 작업 방식 변경: 이후 구현은 묶음 완료 후 테스트 → 실패 수정 → 필요한 재검증 순서로 진행한다. 중간의 반복 테스트와 테스트 선행 방식은 사용하지 않는다. 불변식과 완료 전 검증 요구 자체는 유지한다. CLAUDE.md·SPEC §11의 작업 순서를 함께 갱신했다.


## 2026-10-19 — 실증 준비·증거 반출 후속 구현
- 이번 범위는 기존 5개 점검의 실증 준비와 증거 신뢰성이다. 신규 정책 자동화 확대는 실제 사례 검증 후로 둔다. 유료 AWS 자원은 만들지 않는다.
- prepare-update는 조회/입력 검사만 수행한다. ready=true는 실제 엔진 실행이나 보안 PASS가 아니다. hardware 신고값과 실제 계측을 구분한다.
- 증거 묶음은 DB read-only backup과 artifacts/팩 보존본만 포함한다. 원본 바이트와 객체 7종을 유지하며 신규 DB/객체를 추가하지 않는다. 체크섬은 송신자 인증이 아니다.
- probe 정리 실패는 COMPLETED 증거의 cleanup_errors에 보존한다. 빈 필드는 직렬화에서 생략해 기존 해시를 유지하며, 재판정 보고서에도 한계를 남긴다.
- side 안에만 지정된 real 실행기도 공통 update 대상 분리 검사를 받는다. 실행기 양쪽 혼용 및 동일 target을 허용하지 않는다.
- REQ-09의 경계 요청도 토큰 예산에 포함한다. 기업 한도·vocab 출처는 임의로 정하지 않는다.
- 팩 YAML은 선택적인 기존 libyaml SafeLoader를 사용한다. 파일 해시는 매번 검사하고 객체 캐시로 변경/변조 검사를 생략하지 않는다.
- CI는 PR 외에 현재 작업 브랜치 push에서도 실행하도록 연결한다. 실제 원격 결과는 확인 후 별도 기록한다.


## 2026-10-21 — 사용자 요청에 따른 통합 및 수동 입력 정확성 보강
- 사용자가 두 브랜치 통합, 수동 증거 문제·CLI 누락 수정, 구현 묶음 완료 후 테스트·문서화·커밋·푸시를 승인했다. core/models/store 수정은 이 범위에 포함된다. 기존 main/작업 브랜치는 보존하고 통합 브랜치를 사용한다.
- 증거 그룹은 template/import/export/restore 네 명령으로 통합한다. --replace-manual은 명시적 CLI 옵션으로 연결하고 기본 수동 증거 보호를 유지한다.
- 입력은 정확한 bool/null만 받고 중복 check/side를 거부한다. null의 기존 값 유무 추론은 유지한다. 모든 첨부 읽기/초안 검증 후 객체는 한 SQLite 트랜잭션으로 저장한다. 저장 I/O 실패의 미참조 새 blob은 남을 수 있고 기존 증거는 삭제하지 않는다.
- operator/default_judge/CPU/cleanup/비교/출처 필드의 직렬화를 합치며 양쪽 과거 hash를 유지한다. 객체 종류·스키마·의존성을 추가하지 않는다.
- main의 HTTP 분할과 캐시 정리를 보존한다. REQ-09 제한 코드는 보존하되 기본 real 설정 활성화나 실증 완료로 간주하지 않는다. 신규 정책 확대는 하지 않는다.

- HTTP 골든에서 config_sha256 14개만 null로 조정: launch 비교 정보 없는 target을 비교 가능으로 승격하지 않는 update 브랜치 계약 때문이다. 관측/판정/챌린지/로그는 변경하지 않고, 비교 정보가 있으면 hash를 만드는 양성 시험을 함께 둔다.


## 2026-10-21 — 정책 입력 양식 M1 (사용자 승인)
- 사용자 지시로 Phase 2 M1 구현·묶음 후 테스트·커밋/푸시·초안 PR을 승인했다. POLICY_PROMPT의 과거 3개/null 생성/Phase 1 시험·커밋 금지는 이번 M1 범위에 적용하지 않는다. 기존 Phase 1 기록은 보존한다.
- POL-P1-01은 선택 1(값 키 생략+설명 주석)로 M1에 반영한다. confirmed_by/confirmed_at 공란과 values의 null 타입 오류를 구분한다. select/소비자 검증은 변경하지 않는다.
- POL-P1-05의 M1은 현재 선언 기반 template까지만이다. b 정책 등록·팩 revision·새 타입·새 실행기·기업 실제 값·외부 CHK 도입은 제외한다. POL-P1-02/03/04/06의 기업/구조 결정은 미결 상태를 유지한다.
- 설명은 Phase 1의 기존 11개 항목에서 최소 자료만 패키지에 보존하며 입력 팩 Ref 및 타입 일치 시에만 사용한다. 다른 팩은 실제 선언을 생성하고 미확인 설명/담당/소비는 미지정이다. 런타임 docs/reference 의존성과 core/models/judges/팩 수정은 추가하지 않는다.
- 양식은 DECLARED/DRAFT, 빈 scope 전체, 승인 공란을 사용한다. 새 파일 배타 생성·UTF-8/LF·시각/ID 없는 본문을 유지한다. 원문 역할은 실제 담당자 지정/승인으로 표현하지 않는다.


## 2026-10-21 — 정책 M2 (사용자 승인)
- 사용자가 M2 프롬프트의 수동 PASS·팩 내부 출력 수정과 SUP-14/LIF-07 등록, 묶음 구현 후 테스트·문서화·커밋/푸시·초안 PR을 승인했다. declared.py의 필수 키 존재 관문은 이 승인 범위이며 별도 승인을 재요청하지 않는다. core/models 및 다른 자동 판정 규칙은 변경하지 않는다.
- SUP-14.required_checks와 LIF-07.required_readiness_items를 required list[str]로 등록한다. 원소는 기업이 식별할 검사/준비 항목이며 실제 목록·담당·빈 목록의 업무 유효성·복잡한 조건표를 결정한 것이 아니다. 자동 실행기/충족 판정은 없고 NO_EXECUTOR를 유지한다.
- declared 0.2.0은 필수 정책 키 누락 시 신고 PASS만 UNKNOWN으로 보류한다. []/false/0은 키 존재로 보고 FAIL은 유지한다. 과거 필수 정책 누락 수동 PASS의 재판정 결과 변경은 의도적이며 새 RES revision으로 남긴다. 기존 EVD·RES·해시를 수정하지 않는다.
- 양식 출력은 pack.path의 실제 경로 내부(부모 symlink 포함)면 mkdir/쓰기 전에 거부한다. 외부 새 파일 생성·기존 파일 보호는 유지한다.
- 설명 갱신은 tools/build_policy_template_metadata.py로 검토된 카탈로그 경로/분류/타입을 검사한다. 현재 팩 Ref에만 설명을 연결하고 과거/외부 팩은 안전한 미지정 fallback을 유지한다. 범용 registry/타입/런타임 문서 의존성을 추가하지 않는다.
- 팩 revision 10→11, 두 CheckDef 1→2. 77 ID와 기존 5개 자동 연결은 보존한다. 예시/실험실 정책은 pack.revision만 갱신하고 values·scope·승인 공란을 유지한다. 외부 원문 재수입·엔진/운영 시험·유료 자원·새 의존성은 없다.

### M2에서 남긴 팀 결정
- POL-M2-01: 두 필수 목록의 []를 어떤 업무 조건에서 허용할 것인가? 현재는 명시한 값과 키 누락만 구분한다. []의 타당성/완료를 자동 승인하지 않으며, 비어 있지 않아야 한다는 새 의미 검증은 기업 결정 후 별도로 설계한다.
- POL-M2-02: 검사/준비 식별자의 명명·변경 책임, 실제 목록·담당자, 조건부/역할별 항목의 표현과 증거 연결을 누가 확정하는가? 단순 list[str]에 복잡한 허용표를 숨기지 않는다.
- POL-M2-03: 남은 b 10개 중 다음 등록 대상은 무엇이며 자동 관측/판정 연결이 필요한가? 특히 endpoint/format 후보는 기존 REQ-09 범위·시험 예산·정책 적용 의미를 먼저 검토하고, 나머지 수치/복잡 항목과 외부 CHK는 자동 확대하지 않는다.

## 2026-10-21 — 정책 M3 설계 문서 (작성 범위 사용자 승인)

- 사용자는 M2 검토와 M3 설계 문서 작성·검증·문서 커밋/푸시·초안 PR을 지시했다. 이번 승인에는 제품 코드·팩·판정 변경이나 후속 프롬프트 실행을 포함하지 않는다. POLICY_PROMPT의 과거 Phase 1 커밋 금지와 구분한다.
- 확인 사실: fetch 후 M2는 572cef91ff1bf48ffa945f4a0a5fee046e4e0b0e, PR #2/3/4는 open/draft·미병합. codex/gua-policy-m3-design은 M2를 기반으로 한다. 77점검·7점검/13정책 선언·자동 연결5, a13/b10/c165/d115를 유지한다.
- 설계 제안(구현/기업 승인 아님): [POLICY_M3_PLAN](docs/POLICY_M3_PLAN.md)의 /2 수동 양식·정확 Ref/항목/case/첨부 연결, 기존 관측에 보존, 두 점검의 제한적 순수 검증, 구형 증거 재판정 호환을 검토 대상으로 둔다. Report에 RES.reasons가 현재 포함되지 않아 core/report.py의 limitations 전달이 필요하다는 점을 명시한다. 모델/DB 종류 확장보다 기존 객체·참조를 우선한다.
- POL-M2-01/02/03은 여전히 미결이다. POL-M3-ID/EVIDENCE/EXCEPTION/COVERAGE/HISTORY/AXES는 이를 세분화한 미결 제안 ID다. 실제 기업 목록·담당·승인·기한·수치·예외 충분성·조건부 허용표는 만들지 않는다.
- 기술 권고(후속 구현 지시 전 미확정): 빈/모호한 집합의 신규 PASS 보류, 신고 FAIL 보존·UNKNOWN 비승격, SUP-14 원결과와 승인/배포 전이 구분, LIF-07 준비/접수/정상 추론 및 시계 불확실성 구분, 축별 범위 제한. 현재 declared 0.2.0의 동작을 이번에 바꾸지 않는다.
- 다음 후보 제안은 P-DRAFT-SUP-GOV-TOOL과 조건부 P-DRAFT-AUDIT-REQUIRED-RECORDS 두 개다. 전자는 도구 이름만으로 권한 집행을, 후자는 목록만으로 수집 기한/누락을 검증하지 못한다. OBS-01 동일성·실제 사건·기한/시간 기준 결정이 선행한다. 이번과 바로 다음 증거 연결 구현에서 팩에 등록하지 않는다.
- 설계안의 복사용 M4 프롬프트는 사람이 검토해 실행할 제안이다. judges/core 보고 연결의 구체 범위를 그 프롬프트에 적었으며 이번 문서 작성 승인으로 구현 승인을 간주하지 않는다.

## 2026-10-21 — 정책 M4 구현 (사용자 승인)

- 사용자가 직전 M4 프롬프트대로 /2 양식·SUP-14/LIF-07 항목별 판정·보고 연결, 구현 묶음 완료 후 테스트/수정·문서화·커밋/푸시·초안 PR을 명시적으로 지시했다. collectors/manual·전용 보조 함수·declared·core/report의 제한 변경은 이 승인에 포함하며 별도 승인을 반복 요청하지 않는다. core/select·core/judge·models·store 스키마·팩·카탈로그·예시 정책·실행기·의존성은 보존한다.
- M3 기준 23a02ab420501f7fc75cc1fa555bc938a4a793a5에서 codex/gua-policy-evidence-m4로 분기했다. 시작 추적 파일341개의 해시/원격 상태를 ignored out/test-runs/policy-m4-20261021에 보존한다. PR #2~5는 open/draft·미병합이며 base는 M3다.
- 채택한 보수적 기술 계약: 양식 /2·manual-entry/declared0.3.0, 정확 POL/팩 Ref·항목/case·현재 첨부 해시 연결. 빈/모호한 집합의 새 PASS 보류, 신고 FAIL 보존·UNKNOWN 비승격, 항목별 원결과/집행 구분, 동일 정상 묶음 trial/대상 확인, 예외 충분성 자동 수용 없음, SUP security와 LIF security/functional만 제한 검증, 성능 미검증. POL-M3-ID/HISTORY/AXES/COVERAGE의 첫 구현 기술 권고를 이 범위로 채택하며 실제 기업 식별자 체계·대체 방법/역할·운영 승인은 여전히 미결이다.
- 계약 위치는 guard/manual_policy.py의 두 정책 전용 순수 형태 검증과 judges/policy_items.py의 점검별 의미 검증으로 나눈다. collector와 judge가 같은 어휘/형태를 사용하되 판정기가 파일/저장소에 접근하지 않게 한다. 범용 DSL·객체/플러그인 종류는 추가하지 않는다.
- LIF item_state는 접수/차단 당시 상태에 대한 자기 신고다. order_basis/detail은 원본 시각 자동 해석이 아니며 미확인이면 보류한다. 알려진 준비 전 접수는 추론 결과 누락과 무관하게 FAIL을 유지한다. 기능 실패는 필수 항목 전부의 동일 정상 ready 묶음이 확인된 경우만 도출한다. 같은 사건의 모순은 해당 사건을 미확인으로 하며 별개 사건의 위반은 유지한다.
- 현재 RPT 모델에는 RES 상세 이유가 없으므로 선택된 declared RES의 POLICY_ITEMS: 이유만 기존 limitations에 전달한다. item/case·이유·첨부 해시 식별자를 표시하며 원본 경로/locator/basis 본문은 보고하지 않는다. HTML은 기존 escape를 재사용한다. 자기 신고·DRAFT/EXAMPLE·official 조건은 변경하지 않는다.
- 과거 /1을 읽고 대상 정책 선언이 있는 구형 증거 PASS를 새 판정에서 UNKNOWN으로 보류하되 원본 EVD/RES/해시는 바꾸지 않는다. 비대상/선언 없는 정의에는 요구를 소급하지 않는다. M2 테스트의 완전 정책+빈 관측 PASS 기대는 M4의 UNKNOWN 기대/버전으로 갱신하고 키 누락·FAIL 보존 계약은 유지한다.
- 실제 기업 목록·담당/승인·빈 목록의 업무 유효성·예외/조건부 정책·수치/성능·OBS-01 동일성 등은 미결이다. 추가 정책 후보 등록·실제 엔진/두 이미지 시험·유료 자원/운영 요청은 하지 않는다.

## 2026-10-21 — 정책 M5 구현 (사용자 승인)

- 사용자가 M5의 LIF-07 충돌 수정·SUP-01/SUP-02 승인 기록 체계 정책 등록·OBS-11 계획, 구현 묶음 완료 후 테스트/수정·문서화·커밋/푸시·초안 PR을 명시했다. judges/core 보고 연결의 제한 수정은 이 승인에 포함한다. 새 의존성/실행기/모델/저장소·main 직접 푸시·자동 병합은 포함하지 않는다.
- 시작 M4 2632c245bafd3628965e22c4d6dc86f0d1d3c046에서 codex/gua-policy-m5로 분기했다. PR #6 미병합이므로 base는 M4다. 시작 추적 파일344개의 해시와 팩 revision11 바이트는 ignored out/test-runs/policy-m5-20261021에 보존했다.
- LIF-07은 판정에 필요한 사실별로 모순 영향을 제한한다. 같은 사건 수락/차단의 모순은 준비 전 수락 FAIL을 확정하지 못하게 하되 추론 결과·순서 확인 방법의 차이는 독립 확인된 위반을 지우지 않는다. 신규 PASS 확대는 하지 않으며 순서/첨부 불충분·모순 PASS 보류·별개 사건 FAIL·SUP-14 기존 전이 계약은 유지한다.
- approval_record_system은 두 점검별 required str 문맥이다. 키 누락과 공백의 의미를 구별하고 실제 기업 값/권한/승인자는 정하지 않는다. 해당 선언이 없는 과거 팩에는 전제를 소급하지 않는다. 문맥은 기존 limitations와 POLICY_CONTEXT 이유로 보고하고 HTML escape를 재사용한다. scalar를 수동 항목 목록으로 바꾸지 않는다.
- declared 0.4.0, manual-entry 0.3.0·양식 /2·policy-evidence/1 유지. 팩12/DRAFT·SUP01/02 정의2, a14/b9/c165/d115·9점검15경로다. 예시/lab 정책은 팩 revision만 갱신하며 실제 정책값/승인 공란은 보존한다.
- OBS-11은 등록/구현하지 않는다. OBS-01과의 관계, 필수 사건/case, 독립 발생 증거, 기한/시계 불확실성, 누락 표시/경보/종속 판정·운영 대응 결정이 선행한다. 확인된 누락과 그것을 정상 처리하거나 경보를 누락하는 보호 실패를 구별한다. 계획은 docs/POLICY_M5_PLAN.md다.

## 2026-10-21 — 정책 M6 설계 문서 (사용자 승인)

- 사용자가 OBS-11 정책·증거·판정 계약과 다음 구현 프롬프트 작성, 문서 완성 후 검증/수정·커밋/푸시·초안 PR을 지시했다. 이번 범위는 문서4개이며 제품 코드/테스트·SPEC 확정 계약·팩·CSV/설명 JSON·정책·증거를 바꾸지 않는다. POLICY_PROMPT의 과거 Phase 1 커밋 금지와 구분한다.
- fetch 후 M5 e353cd02b2709ab063355d881092ce249fa1b8e9는 로컬/원격0/0, PR #7 open/draft·미병합이다. codex/gua-policy-obs11-m6-design은 M5에서 분기하며 base도M5다. 시작 추적348개 해시·원격/팩/계수는 ignored out/test-runs/policy-m6-20261021에 보존했다.
- 확인 사실: OBS01은 기록의 주체/대상/실행 연결, OBS11은 누락/장애/시간 불확실성을 정상 처리하지 않는지의 검증이다. Q-C11/SC-23-02 공유만으로 정책/점검을 합치지 않는다. OBS11에 연결된12후보 중 목록1개는b, 수치7개는c, 형식/역할/무결성/대응4개는d이며 현재 모두 미등록이다.
- 제안·미결 POL-M6-REL/SHAPE/TIME/EVIDENCE/COVERAGE/ALERT/AXES/HISTORY/OPS: [M6 결정표](docs/POLICY_M6_PLAN.md#9-열린-결정과-실제-차단-범위)에 질문·선택지·권고·필요 역할·영향·차단 범위를 정리했다. 이 기록은 팀의 실제 승인이나 담당자 지정이 아니다.
- 기술 권고(후속 실행 지시 전 미채택): OBS01과 별도 목록·사건별 정수map4개+목록, dict[str,int] 제한 타입 확장, UTC/ms 오차 구간·수집 기한 기준의 별도 식별/경보 예산, /3+기존/1·/2 읽기, 독립 guard-audit-evidence/1, 기존 EVD/RES/RPT 활용. M6에서는 어느 것도 구현하거나 SPEC의 확정 계약으로 바꾸지 않는다.
- 최초 판정 제안은 제한된 신고 근거의 security 상한/확인FAIL이다. 확정 누락 자체·주입 장애 자체는 OBS11 FAIL이 아니며 정상 처리 또는 필요한 경보의 확정 부재를 별도 입증한다. 종속 결과는 신고 서술만 대조하며 실제 다른RES를 수정/삭제/집계 제외하지 않는다. functional/performance의 새 PASS 도출·UNKNOWN 승격·신고FAIL 삭제는 없다.
- 실제 목록·기한/SLA·시계 오차·권한/승인/운영 대응은 미정이다. 기술 프로파일 채택 전 제품 구현은 보류하지만 실제 기업 값 공란은 합성 계약 구현의 일괄 차단 사유가 아니다. 운영 적용에는 실제 근거/값·대응 결정이 추가로 필요하다. 복구 SLA·권한 집행·전자서명/진위 인증·실제 종속RES 전파는 후속이다.
- M7 복사용 프롬프트는 M6 설계 §11에 있으며 사람이 검토 후 실행할 제안이다. 이번에 실행하거나 다른 채팅/에이전트에 전달하지 않는다.

## 2026-10-21 — 정책 M7 구현 (사용자 승인)

- 사용자가 직전 M7 프롬프트의 OBS11 정책·수동/3·판정/보고, 구현/테스트 작성 완료 후 검증·수정·문서화·커밋/푸시·초안PR을 지시했다. 제한 core/select·collectors·judges·report 변경은 이 승인에 포함하며 반복 승인 요청을 하지 않는다. M6 b404a6d847f932edda926c0185572ac315ae7ef1의 clean349파일 기준을 보존하고 codex/gua-policy-obs11-m7에서 분기했다. PR8 미병합이므로 base는M6다.
- 채택한 기술 계약: OBS01과 독립 목록, required_records+사건별 정수map4개, dict[str,int] 얕은 타입, UTC/ms 구간·C 및 t0+C기준 D/A·경계 포함, /3+/1·/2 능력별 읽기, 기존 EVD/RES/RPT 재사용. manual-entry0.4.0/declared0.5.0, guard-audit-evidence/1이며 기존 항목 계약은 보존한다. POL-M6-REL/SHAPE/TIME/COVERAGE/AXES/HISTORY의 최초 기술 권고를 이 범위에서 채택하며 실제 기업 값·동일 목록·운영 승인을 의미하지 않는다.
- 최초프로파일은 대상 필수 누락 모두에 경보가 필요한 제한 범위다. profile_applicability/profile_detail과 주장별 첨부로 적용을 신고하고 불명/unsupported는 새 판정 확정에 사용하지 않는다. POL-M6-EVIDENCE/ALERT/OPS의 실제 독립 근거·필요 경보/수신점·운영 대응·기업 기준은 별도다.
- M6 설계의 구체화: case별 attempted/case_detail/fault_method, 독립 근거·조회 출처/완전성 설명, 기록/경보 각각의 조회 시작·종료·수신점, 종속 시험의 의존 여부·독립FAIL 근거를 새payload 안에 표현한다. 새 객체/필수 정책을 추가하지 않는다. source이름/해시가 다르다는 사실만으로 진위·독립성을 인증하지 않는다.
- 입력은 timezone이 명시된 문자열·밀리초3자리까지만 지원하며 자동 timezone/절삭/반올림이 없다. -00:00 및 잘못된 offset 범위는 지원하지 않는다. 실제오차는0기본값/허용오차로 채우지 않는다. 부재 창은 발생부터 충분히 조회했고 기한이 확실히 지난 근거를 요구한다. 다른 source의 미확인 시각은 독립 확인위반의 반증으로 쓰지 않는다.
- 확인 누락만으로 OBS11 FAIL을 도출하지 않는다. 정상/종속PASS 처리 또는 필요한 경보 확정부재의 전제를 분리한다. 다른 항목/무관정책/추론 부족은 독립위반을 지우지 않는다. 보안 상한만 제한 도출·신고FAIL/UNKNOWN 비승격·기존INV11/CPU제약을 유지한다. 종속 결과는 신고 대조이며 실제RES를 수정/조회/제외하지 않는다.
- 팩13/DRAFT·OBS11정의2·10점검20경로·a19/b8/c161/d115·77ID/303결정595연결/자동연결5. c→a 수치4개는 구조의 소비 구현이며 실제 숫자/도출/승인 완료가 아니다. 다른76점검/원문/기존증거·예시/lab의값/scope/승인은 보존한다. 실제collector·엔진/운영·유료 자원·새의존성/DB/모델/실행기는 없다.

## 2026-10-21 M8 — usage/replay 정책 문맥 등록

- 사용자 요청으로 M8 프롬프트의 required bool 두 결정/네 경로, 분기별 PASS 보류·보고 연결·필요한 judges 변경·검증·커밋/푸시/초안 PR을 채택했다. 초기 POLICY_PROMPT의 코드/커밋 제한은 이번 명시 범위에 적용하지 않는다. 기업 업무값/승인 채택과는 구별한다.
- OBS04 false / OBS05·07·08 true를 검토 문맥으로 사용한다. 비활성/미확인 문맥은 각 축의 신고 PASS를 UNKNOWN으로 제한하고 FAIL을 보존한다. 미지원/비사용을 policy false만으로 확정하지 않으며 ENV 적용성/선정을 바꾸지 않는다.
- 작은 순수 _branch_context를 기존 declared 파일 안에 두고 기존 POLICY_CONTEXT/report 경로를 재사용했다. 공통 코어·모델·저장소·새 실행기·의존성 변경은 필요 없다. 의미 변경은 declared0.6.0에 기록하고 양식/실행기 버전은 유지한다.
- 공유 카탈로그는 점검별 경로에 연결한다. 값 자동 복사·동일성 강제·전역 충돌 해결은 도입하지 않는다. 같은 범위의 상충 정책인지 서로 다른 범위인지 판단하는 업무 검토 한계를 명시한다.
- 미결: 실제 기업 허용 여부·범위·시점·승인/변경·usage 필드/목적지/주기·replay 계정/권한/자료 신뢰·실제 부재 증거·공유 정책 일관성 검토자. true/false 기본값을 만들지 않는다.
- 후속 제안은 INPUT-ENDPOINTS/FORMATS의 계약 설계다. REQ09의 기존 실행 범위와 다른 경로를 먼저 분리하고, M9 설계 후 작은 M10 구현 범위를 검토한다. 이번에는 후보를 등록하거나 실제 엔진을 실행하지 않는다.

## 2026-10-22 M9 — 입력 endpoint/형식 계약 설계(미채택 제안)

- 사용자 지시로 docs/POLICY_M9_PLAN.md·M8 후속 링크·이번 결정/진행 기록만 작성한다. 문서/검증/커밋/푸시/초안 PR은 승인됐고 제품 구현·기업 정책값/승인은 승인되지 않았다. 초기 POLICY_PROMPT의 커밋 제한은 이번 문서 제출 범위와 구분한다.
- 기준 M8 c77636cd4ecf5cc7fada8e053c95a10110c453f2, PR10 open/draft·미병합. M8의 bool 문맥·FAIL 보존·구형 증거/양식·미검증 한계는 유지한다. 기존 Linux CI850/18·설치24명령·앞선 A 재검토98통과는 과거 결과다.
- POL-M9-SCOPE/PAIR(기술제안): REQ09에만 두 required list[str]를 추가하고 CheckDef의 단일 engine 정상 token-array 표본과 일치하는 한 쌍만 처음 지원한다. 복수 목록은 부분 선택하지 않고 요청 전에 중단하며 실제 기업 전체목록을 잘라 맞추지 않는다. Gateway/등록route/음성대조/실제시험범위·text/messages/재사용ID는 구분한다.
- POL-M9-OBS(기술제안): 두 실제 제출범위 관측·기존 fixture·수동 generic 관측/hash를 연결한다. real 선행검사만으로 충분하지 않아 오프라인 rule_table·declared에도 공유 순수 helper의 PASS 상한을 적용한다. 기존 FAIL/연산자/다른 점검은 보존한다. core/report 한 곳에서 rule_table의 안전한 POLICY_CONTEXT도 전달할 필요가 있다. 다른 코어/모델/저장소/비교/어댑터·새 타입/DSL은 변경하지 않는 안이다.
- 위 제안과 M10의 후보 파일·버전·예상 팩15/14점검26경로/a23b4는 현재 구현/승인 상태가 아니다. 실제 M10 실행 지시에서 기술 범위를 채택한 뒤 구현한다. M9 현재 팩14/DRAFT·14점검24경로·a21/b6/c161/d115·77ID/303결정595연결·자동연결5를 유지한다.
- 열린 업무 결정: target/hop/호출자 범위의 실제 전체 허용목록과 endpoint×형식 관계, 승인/변경 역할, 모델/vocab/consumer 근거·정상/금지 시험의 권한·예산·중단 조건. 실제 담당자/수치/목록은 만들지 않는다. 질문이 남아도 작성 가능한 설계·질문·다음 프롬프트는 완성한다.

## 2026-10-22 M10 REQ-09 입력 범위 구현

사용자 실행 지시로 M9 POL-M9-SCOPE/PAIR/OBS를 채택했다. REQ-09 단일 engine POST/token_ids 프로파일만 지원하며 기업 값·승인·실제 시험 대상은 확정하지 않는다. 과거 정의/부분 선언을 구분하는 순수 helper를 실행·두 판정기에서 공유한다. 판정기에서는 PASS만 제한하여 독립 FAIL을 보존한다. 수동 두 관측만 조건부 config hash에 포함하고 core/report의 rule_table POLICY_CONTEXT 전달만 보강한다. body는 기존 model/{model}·prompt 정수[0]·max_tokens 정수1·expect200을 고정하며 새 요청은 추가하지 않는다. 열린 업무 질문: 전체 승인 목록/관계표 필요성, 대상과 hop, 모델/vocab·예산·중단조건, 실제 담당자와 승인.

## 2026-10-22 M11 — 수동 일관성 채택·캐시 설계 제안

- POL-M11-MANUAL-CEILING 채택: 새 M10 REQ09 계약의 수동 보안/기능 PASS에 고정 rule_table PASS를 요구한다. 자동표 FAIL을 수동 FAIL로 복사하지 않고 상충 UNKNOWN, 신고 FAIL/UNKNOWN은 보존한다. 성능은 이번 보완 대상에서 제외한다. declared0.8.0이며 코어/실행기/저장소/수동 hash는 유지한다.
- cache 제안/미등록: PUBLIC-PREFIX는 true/false 모두 보호 검토가 필요하고 비공개 공유 허용으로 확대하지 않는다. M12는 REQ16 required_cache_contexts와 같은 인증 집단 내 문맥별3case 수동 계약을 권고한다. /3 generic 관측의 첨부 hash 귀속을 판정 시 검사하며 별도 payload/양식/4·POL타입·코어 변경을 만들지 않는 안이다.
- M12에서만 채택할 기술 식별자 model_generation/adapter_content/media_identity·schema·PASS상한은 docs/POLICY_M11.md에 있다. 기업 전체 목록/신뢰집단/실제 consumer와 객체/공개분류·담당/승인·대상/권한/예산/중단조건은 미정이다. 기업값을 추정하거나 목록을 지원 범위로 잘라 맞추지 않는다. REQ15신원·REQ17통계·SUP모드/파생위치는 후속이다.

## 2026-10-22 M12 — 캐시 문맥 수동 계약 채택

- 사용자 지시로 POL-M12-CACHE-CONTEXT 채택: M11 제안의 REQ16 한정 정책·/3 generic 증거·순수 PASS 상한과 관련 judges/팩/SPEC/문서 변경·검증·커밋/푸시/초안 PR을 승인받았다. M11 PR13 미병합이므로 codex/gua-policy-cache-m12의 base는 codex/gua-policy-m11 / 06efef4다.
- 프로파일·정확 필드/enum·문맥별3case·첨부 귀속은 [M12](docs/POLICY_M12.md)에 따른다. 새 범용 프레임워크/스키마/양식·실행기는 만들지 않는다. 기술 식별자는 기업의 실제 활성 사실이 아니다.
- 보안의 cache effect 신고와 기능의 inference 신고를 축별로 구분한다. 구조/전제가 유효한 상태의 추론 실패만으로 보안 격리 신고를 자동 반박하지 않는다. 모든 실패/미완결은 새 FAIL 대신 PASS 상한 UNKNOWN이며 기존 신고FAIL/UNKNOWN·CPU를 보존한다.
- 안전한 코드만 report에 전달하고 전용 helper 의존 예외도 신고 FAIL을 지우지 않는다. 미입력/의미 부족과 NO_EXECUTOR는 구분한다. 관측은 EVD hash에 포함하며 기존 config 입력을 늘리지 않는다.
- 미결 업무: 기업 전체 문맥·실제 consumer/객체·인증 집단/신원 근거·첨부 수집/검토·공개 분류·승인 담당·시험 대상/권한/예산/중단조건. PUBLIC-PREFIX·REQ15·REQ17·실제 실행·M13은 후속이다.

## 2026-10-23 M13 — unknown 축별 일관성과 REQ15 설계

- 사용자 지시로 REQ16의 허용 unknown/형태/enum 의미 구분·축별 상한 보완과 관련 judges/테스트/문서/metadata·커밋/푸시/초안PR을 승인받았다. 기업 실제값·REQ15 신규 등록·실제 엔진 시험은 승인되지 않았다.
- M12 55a24b2에서 codex/gua-policy-cache-m13으로 분기한다. PR14가 미병합이므로 base는M12다. fetch한 main1778c15는 .DS_Store 변경뿐이며 이번 작업에 섞지 않는다.
- POL-M13-UNKNOWN-AXES: 구조/격리 근거가 유효하면 inference failed/not_attempted/unknown은 기능만 UNKNOWN, 독립 보안은 기존 PASS 후보를 유지한다. 격리 관련unknown은 두 축 보류, rawFAIL/UNKNOWN은 보존한다. 판정기0.9.1이며 과거 객체/팩/설정 해시는 그대로다.
- 설계 제안: REQ15의 PRIVATE-GROUP 최초 프로파일, PUBLIC-PREFIX bool·전체 격리집단 목록·신원정책 Ref의 명시 POL 전제와 /3 generic 증거를 [M13](docs/POLICY_M13.md)에 정리한다. bool false만으로 공유 관계/승인을 추론하지 않는다. 실제 신규키·카탈로그 분류 변경과 M14 실행은 하지 않는다.
- 미결: 기업 전체집단/분류·신원정책 정본/승인·서비스 자격 뒤 사용자전달·대상경로/신뢰 경계/consumer·담당·시험권한/예산/중단조건. 문서 작성이나 합성 회귀의 차단 조건으로 삼지 않는다.

## 2026-10-23 — 자동화 1차 구현 범위

- 사용자 승인: Windows CPU 제품 기동·기존 5개 보강·REQ10/REQ13/OBS03 자동 실행·구현 완료 후 테스트/수정·문서·커밋/푸시·초안 PR. 과거 POLICY_PROMPT의 양식 전용/커밋 금지와 CLAUDE의 신규 실행기 승인 조건은 이 명시 승인으로 충족한다. 새 의존성은 추가하지 않는다.
- CPU는 보안/기능만 비교하고 성능 UNKNOWN이라는 기존 결정을 유지한다. 실행 자원/모델 경로/옵션은 실행 설정에, 기업 기준은 POL에 분리한다. 기존 설정·7객체·원문/증거/팩 보존본은 유지한다.
- 새 실행기는 관측된 분기만 판정한다. HTTP 거부와 메모리 안전, 4xx와 exception dump, 자기 입력 인용과 비공개 문맥 노출을 구분한다. 미검증 분기는 UNKNOWN, 독립 확인 위반은 FAIL을 보존한다.
- 현재 CPU 기동은 제한된 amd64 snapshot 프로파일이다. Intel GPU 지원·GPU 성능·기업 승인/collector·두 실제 이미지 비교 완료를 뜻하지 않는다. 구체 범위/검증은 docs/AUTOMATION_PHASE1.md에 기록한다.

### 실제 Windows CPU 실행에서 확인한 보완

- cpu-bounded + hardened + vLLM 0.31.0 x86 CPU 실행에서 첫 추론 중 Triton launcher 공유 라이브러리가 `/tmp/.triton/cache`의 noexec 때문에 로드되지 않았다. 캐시 이동 뒤에도 CPU driver의 별도 임시 `.so`가 `/tmp`에서 로드되다 실패했다. 두 실패의 Docker OOMKilled는 false였다. `/tmp` 실행 제한·비root·읽기 전용 루트를 유지하고 이 프로파일의 `TRITON_CACHE_DIR=/guard-cache/triton`, `TMPDIR=/guard-cache`를 이미 존재하는 실행 허용 컴파일 tmpfs로 고정한다. 일반 임시 파일도 이 tmpfs에 올 수 있으며 `/tmp` 자체의 실행 제한을 해제한 것은 아니다. 실제 환경 변수와 권한을 비교 설정에도 함께 기록한다. 다른 프로파일에 이 예외를 전파하지 않는다.
- 제품은 기동 실패 시와 종료 직전에 자신이 소유한 engine/gateway만 진단한다. `managed-diagnostic.json`과 SHA는 별도 실행 진단이며 EVD 첨부나 증거 bundle 자동 포함물이 아니다. 보고서에는 상대 경로와 SHA만 연결한다. 상태 허용 필드, 로그 tail 80줄·명령별 5초·컨테이너별 저장 32KiB, 알려진 키와 표지된 자격 값 정제를 적용한다. 임의 개인 정보 정제나 거대한 단일 로그 줄의 원시 수신 바이트 상한은 보장하지 않는다. 진단 실패가 원래 실행 실패나 확인된 위반을 덮지 않는다.
- Windows 통합 worktree의 공유 editable 환경이 기본 checkout을 가리킨 사례가 있었다. 검증에는 별도 `UV_PROJECT_ENVIRONMENT`와 frozen 설치를 사용하고 `guard.__file__`로 실제 검증 소스를 확인한다. 환경 경로 문제와 제품 실패를 구분해 기록하며 공유 환경을 덮어쓰지 않는다.

## 2026-10-23 — 자동화 신뢰성 보강 계약

- 사용자 명시 승인: REQ11 판정·managed update sentinel·지원 안내의 구현/회귀/문서/커밋/일반push/초안PR. 기존 작업 지침의 관련 judge 변경 승인 조건은 이 지시로 충족된다. 새 정책/실행기·실제 엔진·새 의존성은 추가하지 않는다.
- REQ11 rev3: 현재 collector에 독립 생성 출력 귀속 근거가 없어 security pass rules를 비운다. 입력 projection/ID의 독립 FAIL과 기능 축은 보존하고 수동 PASS도 저장 정의의 rule_table 상한을 따른다. 과거 rev2 팩/결과는 소급 변경하지 않는다.
- sentinel은 새 실행 폴더의 side 전용 빈 경로만 사용한다. 호스트 ready와 정적 지원 상태는 별도로 출력하며 부분 미지원이 다른 점검을 제거하지 않는다. 7객체/core/models/store의 재설계 없이 적용한다.
- 기업 기준/승인·독립 출력 oracle·새 candidate 선정/두 이미지 실제 실증은 후속이며 이번에는 실행하지 않는다. 누적 PR의 main 반영은 원격에서 완료됐음을 별도로 재확인했다.

- 2026-10-24 원격 재확인: PR2~18은 모두 main 병합 완료(최종24f7d90). 코드 동일·.DS_Store만 다른 최신 main으로 fast-forward해 기존 로컬 수정을 보존했다. 이번 초안 PR base는 main이며 GitHub PR 병합/닫기/base변경/rebase/force-push는 수행하지 않는다.

- 최종 검토 보완: OBS03의 유효하지만 미지원인 로그 분기/문맥도 정적 안내에 분리한다. 설정/source inventory·local engine과 required source 연결의 기존 실행 거부를 유지하며 새 dump 수집이나 정책값을 추가하지 않는다.
- 2026-10-24 · 사용자 승인으로 60초 초과는 실패 처리하지 않고 부모 벽시계를 기록하며 PR B와 C를 계속 진행한다. · 기능 검증과 모든 기존 사례 유지는 계속 필수다.

## 2026-10-25 — PR #30·#31 검토 의견 반영 (사용자 지시, Claude)

- REQ-09 길이 원인 프로파일(`vllm-cpu-length-cause/1`)의 이미지 digest·모델·inventory·tmpfs·오류 문장·소스 루트·네이티브 라이브러리·소스 13개 파일 해시와 엔진 버전 "0.32.0"을 `src/guard/adapters/reviewed_profiles/vllm-cpu-length-cause-1.json`으로 옮겼다. 모듈 상수 이름·값은 그대로다(원본 모듈 값과 일치를 확인). 목표는 엔진 패치 릴리스마다 코드를 고치지 않는 것이지만(U1), 지금은 데이터 분리만 완료됐다. 로더가 파일 하나를 고정해 읽고 `adapters/vllm.py`에 `engine_version == "0.32.0"` 조건이 남아 있어, 파일 추가만으로 새 이미지가 선택되지 않는다. 여러 프로파일 선택과 새 이미지 연결은 후속 보강이다(2026-10-25 외부 검토 지적으로 정정).
- 안전 규칙상 허용 범위: REQ-09 원인 수집기는 실행 중인 엔진 컨테이너 안에서 별도 프로세스(`python3 -I`)로 `vllm.platforms`를 import하고 `/proc/1/environ`·`/proc/1/cmdline`·지정 소스·라이브러리 파일을 읽는다. 이는 상태 변경 요청이 아닌 읽기 전용 관측으로 허용한다. 조건: 명시적 옵션(`length_cause_evidence = true`)일 때만, 읽기 전용 루트 파일시스템(`ReadonlyRootfs`)·검토된 프로파일과 일치할 때만, 출력은 불리언·해시·고정 값뿐이며 환경 변수 값·파일 내용·모델 자료는 내보내지 않는다. 다른 점검·실행기로 이 허용을 넓히지 않는다.
- PLT-04 후보 관측의 LSM 한계를 docs/RUNBOOK.md에 적었다. LSM 컨텍스트가 없는 호스트에서는 후보가 모두 unknown, PASS는 UNKNOWN이다. 보고서 limitations 문장으로 넣는 것은 CheckDef 변경(팩 revision 증가)이 필요해 이번에는 하지 않았다.

## 2026-10-24 — U1 업데이트 시험 선정 (사용자 결정, 구현 전)

> 2026-10-25 개정으로 엔진 릴리스 diff 선정·A/B/C 분류·엔진 릴리스 이월은 폐기됐다. 아래는 당시 기록이며 현행은 맨 아래 "U1 개정" 절이다.

- 사용자 결정: update 모드를 원래 설계 네 단계(입력 유지 → 엔진 소스 diff → 필요한 시험만 선정 → 기준·후보 격리 실험)로 나눈다. 설계 정본 [UPDATE_SELECTION.md](docs/UPDATE_SELECTION.md), 구현 지시 [UPDATE_SELECTION_PROMPT.md](UPDATE_SELECTION_PROMPT.md). 0~6을 한 묶음으로 구현한다.
- 사용자 결정: 필요 시험 선정은 시험을 **줄이는** 것이다. 단 규칙 기반으로 추적할 수 없는 속성은 기본(A)으로 항상 시험한다. 기준: 구현이 특정 경로에 모여 있지 않고 코드 전반에 흩어진 속성. 1차 분류 A17·B42·C18(질문·GPU 분류 근거 기준, 점검 원문 전체 대조 전).
- 사용자 결정: 선정된 시험은 지금처럼 기준·후보 두 이미지로 실행·비교한다(INV-09 유지).
- 사용자 결정: 매핑은 나눠 둔다. 엔진 경로 → 변경 종류는 어댑터, 변경 종류 → 점검은 팩(엔진 중립).
- 사용자 결정: diff 입력은 vLLM 소스 저장소의 두 태그 차이이며 도구가 직접 clone한다(네트워크는 이 단계만).
- 사용자 결정: 이월은 이전 결과가 있을 때만 한다. 없으면 전체 시험. INV-05 개정·INV-19 추가.
- 사용자 결정: 적정 깊이는 출력 형식(경로 → 변경 종류 → 점검)으로 정의하고, v0.31.0→v0.32.0 실제 업데이트의 보안 수정과 대조하는 검증 실험으로 확인한다.
- 근거: 체크리스트 원본 mg-princess/serving-security-framework `729216b1`(2026-10-24)의 `LC-CHANGE`·`LC-INTAKE`·`lifecycle_policy.selection_rule`·`evidence_reuse_rule`이 같은 흐름(변경 영향 검토 후 선정, 격리 시험, 증거 재사용 시 판본 확인)을 요구한다. 프레임워크는 선정기를 구현하지 않으며(`selector_support`), A/B/C는 이 도구의 선정 규칙이다. `LC-CHANGE` 후보 51개는 "검토 후보"이며 우리 선정 결과와 일치를 요구하지 않는다.
- 보수적 결정(Claude): A는 17개 그대로 둔다(재분류 제안 3개는 U1-Q1). 이월은 이전 **보안 PASS**만 한다(FAIL·UNKNOWN은 재시험). PLT-01은 `engine.image_digest` 변경을 입력 트리거로 둔다. 간접 영향은 import 역의존 1단계(설정 가능). 미분류 vLLM 소스 파일·UNKNOWN 사실이 하나라도 있으면 B 전체 선정. 표에 없는 의존성 패키지는 SUP-04(A)와 한계 표시로만 다룬다. `--previous-plan`은 자동 탐색하지 않고 명시 입력만 받는다.
- 보수적 결정(Claude): 내부 점검 ID(`REQ-01`)는 유지하고 프레임워크 ID(`CHK-REQ-001`)는 CheckDef `framework_id`로 둔다. 새 체크리스트도 77행·순서·subtest_ids가 이전과 같음을 2026-10-24에 확인했다.

## 2026-10-25 — U1 개정: 변경 유형별 시험 범위 (사용자 결정, 구현 전)

- 사용자 결정: **엔진 정식 릴리스는 체크리스트를 빠짐없이 전부 시험**한다(적용 점검 전체, 기준·후보 비교, 이월 없음). **모델 업데이트·환경 변경 같은 때만 골라서 시험**한다. 2026-10-24의 "엔진 diff로 A/B/C 선정·이월" 결정과 A17/B42/C18 분류는 폐기한다.
- 사용자 결정: vLLM 소스 diff는 계속 만들되 선정에 쓰지 않고 보고서의 바뀐 영역 표시, 도달 확인, 체크리스트 공백 보고에 쓴다. clone은 도구가 직접 한다.
- 사용자 결정: 모델 업데이트는 모델 manifest diff의 변경 종류(가중치·추가 코드·template·tokenizer·아키텍처)와 공통 점검으로 고른다([설계 §4.1](docs/UPDATE_SELECTION.md)).
- 근거: 2026-10-25 Claude 1차 검증 실험(v0.31.0→v0.32.0, scratchpad)에서 정식 릴리스가 변경 경로 2,801개·커밋 840개로 거의 모든 영역을 건드려 diff 선정의 효과가 작았다(폐기 안 기준 56~60/77). v0.32.0 보안 공지 9건 중 2건(GHSA-8397, GHSA-gq5q)은 특정 모델에서만 실행되는 엔진 코드였다 → 모델 변경의 `MODEL_ARCH` 종류에 LIF-03·REQ-09·REQ-10을 넣었다. 미디어 디코딩·역직렬화 공지 2건(GHSA-h3rc, GHSA-6757) → 표시표에서 SUP-12 연결 추가.
- 보수적 결정(Claude): 증분 감사는 새 audit 모드에 `--previous-plan`을 주는 방식으로 하고 `Mode` enum을 늘리지 않는다. 증분 감사는 새 구성 한 쪽만 실행하며 버전 비교를 내지 않는다(INV-09상 모델·설정이 다르면 비교 불가). import 역의존 전파는 하지 않는다(1차 실험에서 공통 모듈 때문에 전체로 번짐). 모델 파일 형식 규칙은 엔진 중립이므로 `collectors/`에 둔다. 이월은 이전 보안 PASS만 한다.

## 2026-10-25 — U1 개정 2: 엔진 패치 릴리스는 골라서 시험 (사용자 결정, 구현 전)

- 사용자 결정: 0.xx.1, 0.xx.2처럼 패치 자리만 올라가는 엔진 업데이트는 필요한 점검만 골라 시험한다. 정식 릴리스(major·minor 변경)는 계속 전체 시험이다. U1-Q1을 닫는다.
- 근거: vLLM 패치 릴리스는 작다(v0.25.1 커밋2·파일4 ~ v0.19.1 커밋19·파일82). 과거 패치 5개에 규칙을 적용하면 25~38/77이 선정됐다([설계 §8](docs/UPDATE_SELECTION.md)).
- 보수적 결정(Claude): 패치 선정은 항상 시험 20개 + diff 변경 종류 + 입력 변경이다. 항상 시험 20개는 2026-10-24 사용자 결정("규칙 기반으로 추적할 수 없는 항목은 기본 포함")의 A 17개에, 1차 실험에서 모델 구현 파일 안에서 생긴 공지 3건(GHSA-8397·gq5q·frvg)의 유형인 LIF-03·REQ-09·REQ-10을 더한 것이다. 미분류 엔진 파일이 있으면 변경 종류 표의 모든 점검(56개)을 고른다. 롤백·rc·dev·버전 확인 불가·같은 버전의 다른 digest는 패치로 보지 않고 전체 시험한다. 패치 이월은 이전 시험 이미지가 이번 기준 이미지와 같을 때만 한다. 선정된 점검은 기준·후보 두 이미지로 비교한다.

## 2026-10-25 — 팀원 개선안 검토 반영과 PR 정리 (사용자 결정)

- 사용자 결정: 팀원 개선안(변경 분석 개발계획)과 U1이 충돌하는 점은 모두 U1(사용자 결정)을 따른다. 시험을 줄이는 것(패치 릴리스·모델·환경·정책 변경의 선정·이월), 경로 단위 분류, 모델·환경 변경 선정, 정식/패치 구분을 유지한다. 개선안의 "기본 점검을 줄이지 않는다"·기존 INV-05 완료 기준은 채택하지 않는다.
- 채택해 [설계](docs/UPDATE_SELECTION.md)·[프롬프트](UPDATE_SELECTION_PROMPT.md)에 반영: (1) CHG 근거 파일을 내용 주소 artifact로 저장하고 `guard verify`·반출·복원 검사에 포함(현재 저장소 검증은 EVD `artifacts`만 확인함을 확인) (2) `prepare-update`·`update_validation` 경로에도 CHG 연결, 준비·실행이 같은 검증 함수 사용, `update_validation` 수락 기준은 넓히지 않음 (3) git 인자 배열·`--no-ext-diff --no-textconv`·원본 바이트 해시·분석 대상 import 금지 (4) 분석 시각·개인 경로·임의 ID를 시험 조건 해시에서 제외 (5) 도달 확인 다섯 단계와 TCP 차단/HTTP/handler 도달 근거 구분 (6) 평가 건수(필수 연결 누락·근거 없는 추가 선정·미분류 누락·미지원 확정·같은 입력 결과 차이)와 규칙 작성 사례/평가 사례 분리 (7) 한 묶음 안의 구현 순서(CHG 제품 연결 → 자동 추출 → 선정·이월 → 도달 확인) (8) 소스·이미지 관계 세 상태(불일치/미확인/확인).
- 후속으로만 기록(이번 범위 아님): 라우트·인증 AST 정밀화, 관계 종류를 제한한 간접 영향 추적, 시험·점검 후보 분류(설계 §8.3).
- 채택하지 않음: 보고서의 "자산 → 위협 가설" 서술 층(U1 범위 밖, 새 정책을 만들지 않는 읽기 전용 문맥으로 나중에 검토 가능). 소스·이미지 불일치 시 입력 거부(U1은 diff를 쓰지 않고 전체 시험으로 되돌린다).
- PR 정리(사용자 지시, Claude 검토 의견대로): #24·#25·#28은 코드 파일이 main과 바이트 단위로 같아(PR #29로 통합) 닫음. #27은 main UPDATE_VALIDATION에 요약·링크가 있어 닫음. #26 정책 입력 폼은 main에 병합하지 않고 `form-prototype` 브랜치에 보존하며 PR은 닫음(다시 열 수 있음). #30·#31은 검토 의견을 반영(원인 프로파일 데이터 파일 분리, LSM 한계·컨테이너 내부 읽기 전용 수집 허용 범위 문서화, 커밋 cbea1e0)한 뒤 사용자 지시로 병합했다(#30 → 55c9a24, #31 → fb0c103). 반영 후 기본 suite 2905 통과·pytest 39.43초/부모 39초, 설치본 95파일 일치, PR CI 통과.
- 원격 브랜치 정리: main에 병합된 브랜치 21개와 닫은 중복 PR 브랜치 5개(`codex/gua-automation-hardening` 포함)를 2026-10-25 사용자가 직접 삭제했다(권한 분류기가 Claude의 삭제를 막음). 남은 원격 브랜치: main, form-prototype, codex/gua-verdict-semantics(#30), codex/gua-cause-and-candidates(#31), codex/gua-pre-phase4-cleanup.

## 2026-10-25 — 외부 검토 반영: CI 복구·이월 계약·현행 안내·개발 효율 원칙 (사용자 지시)

- **CI 복구**: main push CI가 #30 병합(55c9a24)부터 1건 실패했다(2904 통과/1 실패). 원인은 HTML 보고서 요약 우선 개편(6f8b3fe)에서 점검 행의 판정 Ref(`RES-…@revision`) 표시가 빠진 것과 #30이 그 표시를 요구하는 통합 테스트를 추가한 것의 충돌이다. Claude가 #31 작업 브랜치(641f12f 기준)에서만 2905 통과를 확인하고 병합 뒤 main을 확인하지 않은 탓에 놓쳤다. `report/html.py`의 상세 표 판정 칸에 RES의 정확한 ID@revision을 되살렸다. 테스트는 고치지 않았다.
- **이월 조건 정정**: "점검 정의 revision과 팩 revision 비교"는 다른 번호를 비교하는 문구 오류였다. 같은 점검 ID의 CheckDef revision과 팩 보존본의 정의 파일 해시를 비교하도록 고쳤다. 실행기 이름·버전·설정 해시, 어댑터 이름·버전, 고정 시험 자료 해시, 실행 설정 해시를 이월 조건에 더했다. 실행 중에 시험 자료를 만드는 점검(REQ-01·REQ-03 등)은 이월하지 않는다(보수적). 판정기 버전만 바뀌면 엔진 없이 저장 EVD를 재판정해 판단한다. SPEC INV-19를 같은 뜻으로 고쳤다.
- **소스–이미지 관계 미확인**: 불일치와 같이 처리한다. 패치 릴리스의 시험 축소·이월 근거로 쓰지 않고 전체 시험한다.
- **평가 방법**: v0.32.0 보안 공지 9건은 규칙 보강에 썼으므로 "설계 보완·회귀 사례"로 바꿨다. 독립 평가 사례는 구현 후 규칙 파일 해시를 고정한 뒤 처음 적용하는 릴리스의 공지로 둔다.
- **현행 안내**: README·RUNBOOK·automation_matrix·UPDATE_VALIDATION 맨 앞에 2026-10-25 현행 요약을 두고, PR #30 기본 실행(수락 실패) / PR #31 opt-in 실행(수락 성공) / PLT-04 후보 단독 시험 / 두 이미지 update의 PLT-04(후보 `[]`)를 나눠 적었다. 기존 실패 기록은 "PR #30 당시 기록"으로 남겼다.
- **프로파일 설명 정정**: "프로파일 파일만 추가하면 새 이미지 지원"은 과장이었다. 데이터 분리만 완료됐고, 여러 프로파일 선택과 새 이미지 연결은 후속이다.
- **작은 정합성**: 보고서 목업 생성기를 EXAMPLE·공식 아님·`mockup: true`·목업 한계 문장·예시 승인자로 바꿨다. U1 프롬프트의 네트워크 허용 범위에 체크리스트 원본 다운로드를 넣었다. 설계 §3.2의 "엔진 릴리스는 어차피 전체 시험" 문구를 정식/패치로 나눴다.
- **개발 효율 원칙**: CLAUDE.md에 "개발 효율 원칙" 절을 추가했다(실행기는 점검마다 만들지 않음, 소스 확정 뒤 검증·영향 범위 먼저 재검증, 문서만 바뀌면 전체 시험 반복 안 함, 실제 엔진 시험 묶기와 재사용 조건, 작업 유형별 크기, 시간 분리 기록). 승인된 묶음 범위 안의 결정은 다시 묻지 않는다는 문장을 "진행 방식"에 넣었다. POLICY_PROMPT.md는 역사적 Phase 1 프롬프트라 원칙 본문을 옮기지 않고 CLAUDE.md를 가리키는 안내 한 줄만 넣었다. U1(제품이 다시 시험할 점검을 고르는 기능)과 개발 효율 원칙(개발자의 작업 방식)은 별개 문제로 둔다. `codex/gua-pre-phase4-cleanup`(PR #22 닫힘, 병합 안 된 커밋 4개·51파일)은 필요 여부가 불분명해 남겼다(열린 질문).

- 2026-10-25 · 기존 이월 합의의 사전 재판정과 이월 기록 단계를 구분하고, 이월 Ref·승인 범위·정책값·검증 범위 및 실행 조건별 문서 연결을 명확히 했다. · 원본과 출처를 보존하면서 반복 승인·검증과 기본/opt-in 결과 혼동을 줄이는 문구 정리이며 제품 동작·판정·U1 조건의 추가 변경은 아니다.

## 2026-10-25 byul 통합: HTML 보고서 삭제, 화면은 byul platform

- 사용자 결정(서버 없이 여는 보고서 불필요)으로 `src/guard/report/html.py`, `guard report --html`, `guard update`·`guard validate`의 `report.html` 생성, `docs/mockup/report_mockup.html`, `out/**/report.html`을 지웠다. JSON(RPT)만 원본이다.
- 같은 내용(종합 판정·건수·주요 결과·영역/보호 목적·증거 출처·한계·전체 표·수동 시험·미선정·승인/입력 목록)은 byul `platform/`의 검증 결과 화면이 RPT JSON을 읽어 보여준다. 대표 상태·종합 판정·비공식 사유 표시 규칙은 platform 어댑터로 옮겼고, 삭제 전 `render_html`과 24사례 대조로 같음을 확인했다.
- `report/log_integrity.py`와 증거 첨부 `log_integrity_display.html`은 보고서 화면이 아니라 OBS 점검의 증거이므로 그대로 둔다.
- 테스트: HTML 문자열 확인은 같은 값이 RPT JSON에 있는지로 바꾸고(부정 확인도 JSON 기준으로 유지), HTML escape 확인은 platform(텍스트 전용 렌더링)으로 넘겼다. 렌더러 자체를 보던 1개와 HTML/JSON 짝 확인 1사례는 지웠다. 기본 suite 2903 통과.

- 2026-10-25 · U1 입력 patch는 최신 revision의 매핑 재귀 병합·목록/null 교체로 하고 바뀐 ENV 사실의 과거 출처를 상속하지 않는다. 수락 도구는 명시적 previous-store를 읽기 전용으로 준비한 뒤 bundle 복원본에서 실행한다. · append-only와 원본 보존을 유지하는 입력·실행 기술 설정이며 기업 정책값·판정·수락 기준은 바꾸지 않는다.

- 2026-10-26 · GUA U1·3.12는 BYUL JSON 전용 구조 위로 선택 이관하고 RPT→verification-result 생산자는 platform에 유지한다. · HTML 복구와 공용 계약의 임의 변경을 막고 원본/Ref를 보존하기 위함이다.
- 2026-10-26 · BYUL 입력은 기존 공용 스키마·정확한 Ref·팩·유효 기간을 확인한 명시적 오프라인 도구로 받고 원본 bytes와 guard 객체 매핑을 보존한다. · guard 제품 의존성과 내부 모델을 바꾸지 않고 미확인 topology·CPU·승인을 추정하지 않기 위함이다.
- 2026-10-26 · contracts/examples는 Git 원본 bytes를 보존하고 계약 검사를 Windows/Linux에서 실행한다. · CRLF checkout으로 번들·증거 해시가 달라지는 문제를 저장 해시/원본 변경 없이 해결하기 위함이다.
- 2026-10-26 · 사용자는 실제 vLLM 실증을 보류하고 구현·회귀·계약·설치 검증과 PR을 먼저 진행하도록 정했다. · 현재 Docker daemon과 RAM 준비 조건을 충족하지 않아 기존 자원을 변경하지 않기 위함이다.
