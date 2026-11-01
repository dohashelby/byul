# REQ-02 등록 handler 부분 생산자

기준은 공동 제품 소스 8de64a6d994f7bd80831e00cb85f2d85712b240f, 프레임워크 91e84c7, vLLM 소스 db9527a46873454610df6dbedf79a36d6bf1a7f6이다. 전체 REQ-02 자동 판정과 실제 대상 실증은 미완료다.

## 이번 구현

기존 standalone 센서와 소유 세션 수집을 재사용한다. profile8/events8은 기존 source25개에 고정 completion api_router 소스1개를 더해26개를 대조한다. GET /v1/models의 module-level show_available_models와 POST /v1/completions의 create_completion 등록 callback을 연결한다. CompletionRequest는 조회하지 않고 두 번째 인자 또는 raw_request 키워드의 ASGI scope만 사용한다. 이전 helper lookup이나 serving method 반환을 등록 handler 실행으로 승격하지 않는다.

CheckedLoader의 기존 소스·실행 코드 게이트 후에만 센서를 설치한다. decorator가 이미 endpoint를 보관하므로 module 전역 함수와 함께 각각 유일한 `GET /v1/models` 또는 `POST /v1/completions` route의 `endpoint` 및 `dependant.call`이 같은 원함수인 경우 두 보관 참조도 연결한다. 누락·중복·경로/메서드/호출 대상 불일치·지원하지 않는 저장 형식은 원래 등록을 변경하지 않는다. 이후 include_router가 endpoint를 복사하는 경우에도 동일 wrapper를 사용한다. 호출 당시 scope의 endpoint·route.endpoint·dependant.call이 wrapper와 모두 같아야 `registered_route_observed=true`다. 등록과 실행의 동일성은 선언 관측이며 FastAPI 전체 판본/변형이나 생산자 신뢰 증명이 아니다.

새 `route_handler` metadata는 기존 `route_authentication`의 필드/의미를 바꾸지 않는다. CALL·RETURN·RAISE, 진입/handler 시점의 경로·root_path·method 지문, 등록 참조 일치, 정상 반환 여부만 남긴다. 요청·응답 본문, Authorization, 경로 원문, ModelList, 예외 원문을 저장하거나 추가 ASGI receive/send를 호출하지 않는다. 반환 객체·예외·서명은 기존대로 유지한다.

`capture-lifecycle-session`의 기존 안전한 파일 수집 결과에 `route_handler_index`를 추가한다. session·profile·물리 프로세스/namespace·소유 컨테이너 문맥·개별 ASGI invocation을 묶고 실제 행 참조를 남긴다. 같은 헤더의 다른 invocation을 합치지 않는다. 인증 bool, handler 호출 전의 bool, 경로 변화는 서로 분리한 관측이다. 미봉인·BOUND_REACHED·미수집 파일·모듈 불검증·색인 상한은 완전한 관측으로 표시하지 않는다. 앞서 남은 원본 행과 독립 관측은 보존한다.

profile7의 LF/CRLF 고정 asset·source25개·기존 GET handler와 profile1~6 판독을 보존한다. 새 completion 지점을 과거 profile7에 소급 허용하지 않는다. profile6의 기존 지점·SELECTION·가중치 call frame과 profile1~5 판독도 유지한다.

## 검증 범위와 남은 조건

CPU 시험은 고정 vLLM 인증·helper·serving·endpoint 함수 본문과 작은 EXAMPLE router를 조합한다. 정상/잘못된 키/누락, prefix, 인증 후 내부 경로 변경, 등록 참조 누락/중복/변경, 예외, 중복 헤더, 과거 profile, 잘못된 typed metadata, 부분 기록, 제품 CLI 저장 흐름을 검증한다. Completion API의 고정 helper/endpoint 본문도 실행하며, 등록과 decorators는 EXAMPLE router로 대체한 범위를 명시한다. JSON·native error·streaming 반환과 인자·예외·취소 객체를 보존하고 추가 body/stream 소비를 하지 않는다. 실제 FastAPI/vLLM·Gateway·GPU 실증으로 표시하지 않는다.

vLLM의 고정 `requirements/test/cpu.txt`가 지정한 FastAPI0.136.3의
[공식 routing 소스](https://github.com/fastapi/fastapi/blob/0.136.3/fastapi/routing.py)에서
dependant.call 호출과 include_router의 endpoint 복사 경로도 정적으로 대조했다.
설치판·실제 호출 검증을 대체하지 않으며 새로운 의존성은 추가하지 않는다.

모든 결과는 EXAMPLE 또는 DECLARED 상한과 security UNKNOWN을 유지한다. 신뢰 principal·승인된 Gateway 변환·외부 요청 ledger·선정 API의 기업 허용표·handler 전체 범위·관측 누락의 부재는 확인되지 않았다. 따라서 REQ-02 CheckDef의 executor/judge는 null, PACK31과 등록 개수는 그대로다. 등록 callback 생산자가 추가됐다는 사실만으로 점검 완료를 집계하지 않는다.

실제 대상에서는 기존 `prepare-lifecycle-observations`로 profile8 asset를 준비하고 소유 시험 환경에 검토 후 장착한다. 승인된 정상/거부/변환 요청을 한 담당자가 실행한 뒤 기존 `capture-lifecycle-session --execute`로 봉인된 기록을 수집한다. 이 세션에서는 실제 엔진 기동·이미지 pull·모델 다운로드·AWS 사용을 하지 않는다.


등록 경로 자체가 include_router(prefix=...)로 바뀌면 고정 /v1/models 또는 /v1/completions 참조 조건을 벗어나므로 등록 관측은 false다. 승인된 변환 여부를 추정하지 않으며 전체 판정에 사용하지 않는다. root_path와 등록 경로 prefix는 구분한다.
