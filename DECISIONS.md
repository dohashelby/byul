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
### POL-P1 (2026-10-21, Phase 1 검토 대기 — 결정 아님)
- POL-P1-01 — `POLICY_PROMPT.md` Phase 2의 값 칸 null 제안은 현재 select의 null 타입 오류 계약과 충돌한다. 선택 1: 미입력 키 생략 또는 설명 주석만 제공하여 기존 NO_POLICY_VALUE/타입 검증 계약 유지. template/문서/회귀 범위로 끝낼 수 있어 최소 변경으로 권고한다. 선택 2: 명시적 미입력 null/표식의 새 계약을 도입하고 로더·선정·타입/의미 오류·직렬화/해시·보고·과거 정책 호환성을 검토한다. core/SPEC/불변식 영향 검토가 필요하다. 선택/구현은 하지 않았으며 사람이 결정한다. 키가 선언되지 않은 새 카탈로그 항목 전체에 현재 NO_POLICY_VALUE가 적용되는 것은 아니다.
- POL-P1-02 — P-DRAFT 제안 ID와 결정 단위/공유 표의 범위를 채택할 것인가? `docs/policy_catalog.csv`의 수는 scalar 값 개수가 아니며 표 내부 역할·대상·모델·부하·주기별 셀은 독립이다. 현재 values.<check_id>.<key>는 보존한다. 유사 명칭만으로 기존/신규 경로를 통합하지 않는다. 원문에 유일한 정책 개수가 없어 원문 대비 중복 감소율은 만들지 않았다.
- POL-P1-03 — 각 수치의 적용 자원·단위·peak/누적·단계/요청/전체 범위·모델/장치/부하·기준선/도출 규칙·시작/완료 사건과 승인 역할은 무엇인가? numeric_values_pending 40행은 수치 정책 40개가 아니며 목록 문장과 자원별 분해 후보를 구별한다. CPU/GPU/디스크·단계/누적·취득/추론 retry·신규/세션/사본 기한을 독립으로 검토한다. 유출·타요청 혼입·메모리 안전 오류는 정상 계산 오차로 수용하지 않는다.
- POL-P1-04 — 정책 결정자·실행 담당자·검토자와 실제 정책/팩 확정 역할을 누가 지정할 것인가? 77행은 legacy_q_ids→items.policy_owner로 연결되지만 원문은 전부 “적용 조직의 정책 승인 담당”일 뿐 실제 지정이 아니다. CSV의 권장 역할은 제안이다. 미등록 ENV 후보와 실행 설정/고정 model/runtime 사실의 공급 책임도 별도로 정해야 한다.
- POL-P1-05 — Phase 2 최소 범위를 기존 11개 선언의 template와 미입력 주석으로 할지, 검토된 b 후보 중 어느 ID까지 포함할지 결정해야 한다. 신규 목록의 policy_params 등록은 새 실행기/실측 완료를 뜻하지 않는다. PLT-04/REQ-09는 모의 계약 기록이며 기존 3개 실측과 수준이 다르다. REQ-09 양수 한도·한도+1 시험 예산·400/413/422 거부 코드를 유지하고 실제 vocab/서비스 한도 일치를 별도로 확인해야 한다. 상세 계획·완료 기준·다음 프롬프트는 docs/policy_catalog.md. 이 작업은 Phase 1에서 멈추며 구현·기업 값 확정·커밋·푸시는 수행하지 않는다.
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
