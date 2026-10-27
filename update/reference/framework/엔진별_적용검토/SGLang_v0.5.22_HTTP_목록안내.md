# SGLang v0.5.22 HTTP 정적 접점 목록

확인일: 2026-10-27 · 공식 고정 commit: `baa9befa381511cd4d56f83980951e5a45fffc6d`.

현행 정적 후보 목록은 [v2 JSON](SGLang_v0.5.22_HTTP_정적후보_v2.json)이다. [기존 JSON](SGLang_v0.5.22_HTTP_route목록.json)은 당시 `http_server.py` 상위 수준 decorator 함수 80행을 보존한 제한 목록이며 bytes와 source pin을 유지했다. 기존 형식을 읽는 소비자는 계속 읽을 수 있고, 조건부·포함 router를 확인하는 소비자는 `schema_version=2.0`을 명시적으로 선택한다.

## 수의 의미와 기존 목록 대응

| 범위 | 함수 후보 수 | decorator 등록 선언 수 |
| --- | ---: | ---: |
| 보존한 기존 목록 | 80 | 83 |
| `http_server.py`에서 추가한 조건부 함수 | 3 | 5 |
| 포함 router 2개 모듈에서 추가한 함수 | 3 | 3 |
| v2의 고유 소스 함수 후보 전체 | 86 | 91 |

함수 하나에 복수 decorator 또는 `methods=['POST', 'PUT']`가 있을 수 있다. root 두 함수는 상호 배타 조건을 가진다. 이 표는 소스에서 발견한 함수와 등록 선언을 세며, 실제 API 수와 동시 활성 경로 수는 `null`·미확인으로 남긴다. FastAPI의 기본 문서 경로·자동 method 처리·mount·설치 확장까지 포함한 실제 경로 목록은 선정한 process에서 별도로 수집한다.

v2의 `legacy_inventory.unchanged_rows`는 기존 0~79번째 행을 새 `candidate_id`에 일대일로 연결한다. 기존 함수명·줄 번호·decorator·registration 표현은 모두 동일하다. source 함수 ID와 include 경유 후보 ID를 구분해 반복 포함·동일 함수명·동일 경로를 조용히 합치지 않는다.

## 보완한 소스 접점

아래 링크는 모두 같은 고정 commit이다. 등록 조건은 import/기동 당시의 실제 환경과 대조한다.

| 함수·위치 | 등록 선언과 조건 | 권한 선언·실제 확인 상태 |
| --- | --- | --- |
| `_dumper_control_handler`, [http_server.py:891–915](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L891-L915) | `DUMPER_SERVER_PORT == 'reuse'`일 때 POST `/dumper/{method}`. `{method}`는 경로 변수다. | `ADMIN_OPTIONAL` 선언. 실제 환경 변수·등록·접근·handler 효과 미확인 |
| `ollama_root`, [2046–2053](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2046-L2053) | `SGLANG_OLLAMA_ROOT_ROUTE`의 조회값이 `None`이 아닐 때 해당 동적 경로에 GET·HEAD 선언 | 별도 `auth_level` decorator 없음. 유효 경로·middleware·접근 효과 미확인 |
| `sglang_root`, [2055–2061](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2055-L2061) | 위 조건의 `else`에서 GET·HEAD `/`. `ollama_root`와 상호 배타 | 별도 `auth_level` decorator 없음. 실제 등록·접근 효과 미확인 |
| `get_loads`, [v1_loads.py:96–102](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/v1_loads.py#L96-L102) | GET `/v1/loads`. [http_server.py:503–507](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L503-L507)의 module-level `include_router`로 포함 | 별도 `auth_level` decorator 없음. 실제 middleware·정상 metrics 반환·접근 효과 미확인 |
| `scale_elastic_ep`, [elastic_ep.py:15–46](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/elastic_ep.py#L15-L46) | POST `/scale_elastic_ep`. [http_server.py:510–523](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L510-L523)의 module-level 포함 | `ADMIN_OPTIONAL`. handler는 `elastic_ep_backend is None`이면 404를 반환한다. 실제 활성·권한·변경 효과 미확인 |
| `is_scaling_elastic_ep`, [elastic_ep.py:76–88](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/elastic_ep.py#L76-L88) | GET `/is_scaling_elastic_ep`. 같은 module-level 포함. import/include 구문에는 기능 활성 조건이 없다. | `ADMIN_OPTIONAL`. backend 미선정 시 404 분기가 있다. 실제 등록·접근 효과 미확인 |

등록 단계의 include 조건과 handler 안의 기능 조건은 각각 기록한다. v2는 include 위치·import alias·조건·prefix 표현을 `include_router_sites`와 각 후보의 `include_chain`에 보존한다. root 변수와 `os.environ.get(...)` 등 동적 경로는 평가하지 않고 원문 표현을 남긴다.

권한 선언은 실제 집행 증거와 함께 읽는다. [auth.py:38–58](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/auth.py#L38-L58)는 일치 route의 등급을 찾고, [auth.py:128–151](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/utils/auth.py#L128-L151)는 key 조합에 따른 `ADMIN_OPTIONAL`·일반 처리를 선언한다. [http_server.py:2656–2684](https://github.com/sgl-project/sglang/blob/baa9befa381511cd4d56f83980951e5a45fffc6d/python/sglang/srt/entrypoints/http_server.py#L2656-L2684)에 단일 tokenizer 경로의 middleware 설치 조건이 있다. 후보마다 `effective_auth_status=unverified`를 유지하며 일반 키·관리 키·키 없음·실제 tokenizer 구성의 효과는 시험으로 확인한다.

## 재생성과 정적 검사

Python 표준 라이브러리만 사용한다. 고정 upstream checkout은 별도로 준비하고 세 파일의 commit/bytes를 확인한다. 추출기는 엔진을 import하거나 실행하지 않고 `source_files`의 SHA256을 대조한 뒤 AST를 읽는다.

```sh
python3 engine_sglang_inventory.py --source-root /path/to/sglang-fixed-checkout --check
python3 engine_sglang_inventory.py --validate-only
python3 -m unittest discover -s tests -p test_engine_sglang_inventory.py -v
```

`--source-root ... --check`는 지정한 고정 소스 세 파일을 다시 AST 분석하고 저장 v2와 bytes까지 대조한다(`source_ast_regeneration`). `--validate-only`는 저장 v2의 구조·고정 hash·구판 대응·조건·미확인 상태를 검사한다(`stored_inventory_validation`). 이 명령은 upstream 소스를 다시 읽지 않는다.

재생성 파일을 검토할 때는 첫 명령의 `--check`를 빼고 실행한다. 기본 출력은 현행 v2 JSON이다. 세 입력 파일은 `python/sglang/srt/entrypoints/{http_server,v1_loads,elastic_ep}.py`이며 각 공식 URL과 SHA256은 v2 `source_files`에 기록했다. 기존 80행 파일은 도구가 읽기만 한다.

전용 fixture는 if/else·중첩 조건·동적 경로·경로 변수·HEAD·복수 method·포함 router/alias/prefix·중복 함수명/경로·권한 선언·해석하지 못하는 등록을 확인한다. artifact 변형 검사는 조건부/포함 후보 삭제, ID 중복, root 조건 평탄화, HEAD 소실, 고정 hash 변경, 구판 대응 누락, 실제 활성·권한 확인으로의 상태 변경을 거부한다.

정적 추출 범위는 FastAPI/APIRouter의 HTTP decorator와 로컬 `from ... import router`로 해석되는 `include_router`다. 지원하지 않는 제어 흐름·등록 호출·include는 `unresolved_static_sites`에 남겨 수동 검토한다. 이번 고정 입력에서 미해결 site는 0개다. AST 분석은 실제 runtime 등록·권한 효과의 증거를 제공하지 않는다.

## 개발·시험 인계

기존 CHK-REQ-001/003과 CHK-PLT-008을 유지한다. 실제 선택된 listener의 route/method·권한 선언·key/역할 정책과 정상 호출을 대조하고, 비허용 접근의 실제 거부/handler 도달을 기록한다. dump·상태 변경·진단 등 활성 작업은 해당 CHK의 기존 분기에 연결한다. 기존 추가 실제시험 T33은 조건부 root/dumper·포함 router의 실등록 대조에 해당한다.

미선정 환경의 이미지·의존성·환경 변수·tokenizer 구성·등록/OpenAPI 목록·역할별 처리·관리 변경 효과와 SGLang adapter 지원은 미확인이다. 프레임워크 담당은 목록·적용 분기·증거 조건을 검토하고, 개발·시험 담당은 선정 환경의 수집·계측·실행을 맡는다.
