# DECISIONS

날짜 · 결정 · 이유 (한 줄씩). SPEC과 참고 문서가 충돌하면 SPEC을 따르고 여기 적는다.

## 결정
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
- 2026-10-19 · `tools/model_inventory.py`: snapshot 디렉토리 기준, 심볼릭 링크를 따라 실제 내용 해시, `[{path, bytes, sha256}]` 경로순 → 정렬 JSON → SHA-256. 결과 **파일 12개, 753,111,181 bytes, inventory_sha256 `d677f3999b13aab7bf9a7d203568ca63bc2d3fdc6a102b5efc2c719fd816ae6e`**. LFS 3개 파일의 sha256은 HF blob 이름과 일치. BoBProject 보고서의 37개와 **다르다**: 우리 cache 트리는 일반 파일 34개(blob·lock·xet 로그 포함) + 심볼릭 링크 15개로 37과도 맞지 않는다. BoB가 무엇을 셌는지(트리 전체? 다른 revision·allow_patterns?) 확인 필요(열린 질문).
- 2026-10-19 · ENV에 선택 필드 `runtime.platform`("CPU, arm64, Docker Desktop on macOS")과 `engine.source_commit_status`·`expected_source_commit` 추가. 비어 있으면 직렬화에서 빠지게 해 기존 객체 해시를 유지(기존 `.guard/` `guard verify` OK). 보고서 limitations에 실행 환경과 commit 확인 결과를 적는다.
- 2026-10-19 · `adapters/vllm.py` `launch_plan`에 `device: gpu|cpu`(gpu → `--gpus all`), CPU면 `VLLM_CPU_KVCACHE_SPACE`(기본 1GiB), `--shm-size 1g`. 엔진 인자는 계속 `start_command`만 만든다(`--model --revision --served-model-name --host 0.0.0.0 --port 8000`, 컨테이너 안 포트, `--api-key` 없음, `VLLM_SERVER_DEV_MODE` 미설정).
- 2026-10-19 · target.json `lab` 플래그: stub 엔진이면 true(→ EXAMPLE), 실제 vLLM이면 false(→ 증거 MEASURED). ENV는 DECLARED. RES `verification_level`은 증거와 적용 조건 출처 중 가장 약한 것이라 **DECLARED**(gateway 사실이 사람이 쓴 값). 5b engine 테스트의 "verification_level == MEASURED" 단언은 제 테스트 오류였고 EVD `data_origin == MEASURED` + RES `DECLARED`로 바로잡았다(판정 규칙 변경 아님).
- 2026-10-19 · direct_route 실행기는 target `engine.ready_timeout`(상한, 기본 600초)까지 신뢰 출발점에서 `/health` 200을 기다리고, 엔진 컨테이너가 죽거나 시간이 넘으면 예외 → ABORTED(INV-15).

**제안(적용하지 않음, 사람 확인 대기)** — 5b에서 실측한 v0.31.0 CPU 등록 경로(개발 모드 꺼짐) 22개 기준:
- 정보성 제외 목록과의 어긋남: `GET /load`(서버 부하 지표), `GET /kv_event_sources`(KV 이벤트 소스 정보)는 정보성·관측 성격이라 INFO에 넣는 것을 제안. `POST /is_scaling_elastic_ep`는 읽기 전용 상태 조회(현재 admin, 비상태변경으로 직통 요청함, exposed에서 200).
- `POST /v1/responses/{response_id}/cancel`은 사용자 객체 상태를 바꾸는 경로라 mutating 분류를 제안(이번엔 리터럴 `{response_id}`로 보내 404, 실제 객체 영향 없음).
- 경로 템플릿(`{response_id}`)은 존재하지 않는 고정 ID로 치환해 보내는 것을 제안(지금은 리터럴 그대로 → 404 → 해당 경로는 판정상 "그 밖의 코드").
- 기능 축: opt-125m은 chat template이 없어 `/v1/chat/completions` 400, `/v1/messages`·`/v1/responses`·`/generative_scoring`은 `{}` 본문이라 400 → Gateway 허용 키도 200이 아니어서 functional은 UNKNOWN. 경로별 무해 요청 본문을 보강하거나 정상 대조 경로를 추론 경로로 좁히는 것을 제안.

## 열린 질문
- **(5b, 멈추고 질문) REQ-01이 vLLM v0.31.0에서 PASS가 될 수 없음**: 개발 모드를 꺼도 `POST /scale_elastic_ep`(상태 변경, MUTATING)가 등록된다. 규칙상 상태 변경 경로는 보내지 않으므로(`allow_mutating_routes=false`) `routes_tested ⊉ routes_registered` → isolated 위상에서 직통 48건 전부 timeout + 양성 대조 true인데도 security UNKNOWN. 규칙은 설계대로 동작했다(미시험을 PASS로 올리지 않음, INV-01·11). 선택지: (a) 그대로 둔다 — 그 경로는 사람이 별도 수동 시험(버리는 엔진, PROGRESS 5b전 7번 절차)으로 확인하고 수동 증거를 붙이는 방식을 설계 (b) 상태 변경 경로는 "직통 연결 단계 차단만" 관측하는 별도 요청 형식(예: 연결만 열고 HTTP 요청은 보내지 않는 TCP 도달성 검사)으로 시험해 `routes_tested`에 포함 — 연결 단계 timeout/refused는 handler 실행 없이 관측 가능 (c) 정책 값으로 특정 경로를 REQ-01 범위에서 명시적으로 빼고 보고서 limitations에 표시. 제 의견은 (b)가 원래 의도(직통 도달 불가 확인)를 해치지 않으면서 엔진 상태를 바꾸지 않는다. 결정 전까지 규칙을 바꾸지 않는다.
- BoBProject 보고서의 opt-125m 파일 수 37과 이번 12(snapshot 기준)의 차이 — BoB의 셈 기준 확인 필요.
- 실측 정책 확정자, 팩 검토자 — 계속 미지정(정책·팩 DRAFT, official=false).
- D2 `impact` 값(HIGH/MEDIUM/LOW) — 지금은 전부 null.
