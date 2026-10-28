# B-2 응답·로그·기록 보호

기준 main `4af4d31bc951bc4a6e8bfefe4381e63e5305075c`의 별도 브랜치에서 준비한다.
A PR44 `2d21057402af23f76303c39caf53f8832b4f1a3c`는 관찰한 진행본이며 확정 통합 승인으로 보지 않는다.
REQ-07~10·공통 core/models/store/contracts·registry·팩을 수정하지 않는다.

## 첫 묶음: REQ-13 검증 오류

기존 CheckDef revision 4, override, response_content, 대역 회귀를 대조했다.
이미 일반/비공개 system/반환 옵션/검증 오류/후속 정상·누락 시 독립 FAIL 보존을 지원한다.
stream·수신자별 권한·모든 내부 오류의 부재는 미지원이다. A의 response_projection 및
response_content 변경은 원문 미저장과 첨부 보수이므로 재구현하지 않는다.

남은 작은 분기는 **실제 RequestValidationError handler가 `exc.body`의 system 문맥과
오류 항목의 caller 입력을 구분해 반환하는지**, 그리고 같은 오류의 지정 logger 출력이
반환 응답과 다른지를 CPU에서 확인하는 것이다. 오류 경로가 응답 정보 제한을 증명하는 범위다.
입력 수용 정책·REQ-10을 변경하지 않는다.

전용 파일은 `executors/response_error_contract.py`, `tests/test_b2_response_error_contract.py`,
`tools/b2_reference_cpu.py`와 이 문서 폴더다. 기존 JSON/leaf/strict parser를 재사용한다.
원문·예외 원문·토큰을 산출물에 저장하지 않고 해시·합성 표식 종류·누락 코드만 남긴다.
허용 필드와 금지 표식 정책 미입력은 수집을 막지 않으며 정책 부족을 남긴다.
관측된 독립 위반은 잘림·파싱 실패·후속 누락으로 지우지 않는다.

종료 조건은 고정 소스 검증, 실제 handler/오류 모델/지정 logger 실행, 표식 분리,
정상/확인 위반/정책 누락/수집 오류/후속 실패 회귀, A 최소 연결안이다.
참조 CPU 관측은 설치 대상·엔진 추론·기업 정책의 PASS가 아니다.

## 재현

참조 저장소는 저장소 밖 B-2 증거 폴더에 취득한다. 태그뿐 아니라
`reference-source.json`의 정확 commit과 파일 bytes SHA256을 실행 전에 대조한다.
검증용 격리 Python 3.12 환경에서만 다음 시험 의존성을 설치한다.
제품 pyproject/uv.lock에는 추가하지 않는다.

```sh
uv venv --python 3.12 /path/to/b2-evidence/cpu-venv
uv pip install --python /path/to/b2-evidence/cpu-venv/bin/python \
  fastapi==0.115.12 starlette==0.46.2 regex==2024.11.6 pydantic==2.14.1 pyyaml==6.0.3
PYTHONPATH=src /path/to/b2-evidence/cpu-venv/bin/python tools/b2_reference_cpu.py \
  --source /path/to/b2-evidence/vllm-reference --output /path/to/b2-evidence/cpu-observations.json
```

AST로 지정 정의를 원래 위치 그대로 컴파일한다. 실제 handler, 정제 함수,
Pydantic ErrorInfo/ErrorResponse/OpenAIBaseModel, vLLM 예외 클래스, RequestLogger의
log_inputs, NewLineFormatter를 실행한다. engine import/init_logger/추론·전체 FastAPI
라우팅은 실행하지 않는다. 실제 FastAPI Request/RequestValidationError/HTTPException과
Starlette JSONResponse를 사용한다. 입력 오류는 실제 Pydantic TypeAdapter(int)로 생성한다.
요청 body·logger 주입·logger 생성자 생략·고정 formatter 설정·Docker timestamp framing은
합성 경계다. 이를 전체 엔진 실행이나 실제 Docker 수집으로 표시하지 않는다.

## A에게 필요한 연결

[PR44 인계 요청](https://github.com/dohashelby/byul/pull/44#issuecomment-6461922025).
response_content.py 소유권 인계, 확정 SHA·팩 Ref·검증 범위·안전한 첨부 API를 기다린다.
helper 입력은 보존 전 메모리의 `{outcome, body, body_bytes}`, 합성 표식, 명시 정책이다.
출력은 반환 응답과 지정 로그 각각의 관측·누락·위반 목록이다.
응답 helper는 별도 logger 관측으로 REQ-13을 실패 처리하지 않는다.
최소 연결은 기존 safe_error 결과에서 helper를 호출하고 기존 observation과 대조하는 것이다.
새 실행기/공통 객체/판정 의미 변경은 제안하지 않는다. 반환 필드 경로 해시를 기존
response_fields_seen 대신 넣지 않는다. 기존 판정에 필요한 실제 경로는 메모리에서 대조한다.
정확 통합 SHA를 받으면 같은 소스에서 실행→EVD→RES/RPT와 정제/첨부 회귀를 검증한다.

## 후속 준비 순서

OBS-03: 같은 CPU 도구에서 실제 RequestLogger.log_inputs를 INFO/DEBUG로 각각 실행한다.
합성 private prompt 표식이 지정 logger에 남는지를 구분하며 기업 허용 정책은 UNSET이다.
OBS-09: 실제 NewLineFormatter 출력은 기존 parse_native_info로 소비한다.
기본/줄바꿈/CR 식별자를 대조하되 source/pid가 실제 엔진 신원임을 주장하지 않는다.

OBS-06/07/08/10/11/12의 기존 log_evidence·capture 분석을 우선 재사용한다.
실제 저장소/IAM/replay consumer/retention이 없는 경우 준비된 관측 계약과 남은 외부 조건을
`storage-handoff.md`에 기록한다. 로컬 해시 확인으로 생산자 진위·권한 집행을 주장하지 않는다.

## PR47 후속 CPU 마감

공식 `test_validation_exception_handler.py`는 param fallback·내부 loc 제거·endpoint 경로
비반환·오류 개수/입력 크기 제한을, `test_error_sanitization.py`는 주소/경로 정제와
안전한 문구 보존을 이미 확인한다. 해당 클래스/함수의 원래 assertion을 고정 bytes로 실행한다.
별도 API 모듈의 소스 import 확인 클래스는 이번 handler CPU 범위에 포함하지 않는다.
새 skip이나 기존 기대값 변경은 없다. 전체 upstream suite 실행이라고 표시하지 않는다.

우리 CHK의 추가 요구는 수신자별 허용 범위·지정 로그·정상/누락 의미·증거 원문 미보존이다.
기업 정책과 실제 target은 여전히 미정이다. 고정 fixture의 필드/상한을 기업 정책에 등록하지 않는다.
PR47의 출력 logger 공백은 실제 RequestLogger.log_outputs를 INFO/DEBUG 및 정상/stream
delta/stream complete로 실행해 수집한다. 합성 생성 문자열을 직접 인자로 주며 실제 모델이
해당 문자열을 생성하거나 stream 요청을 처리했다고 주장하지 않는다.

관측 helper는 중복 키·후속 잘림 전에 수신한 완전한 JSON 문자열 token의 표식을 보존한다.
문자열의 JSON escape 한 단계만 해석하고, 중복 키/불완전 구조는 필드 관측에 사용하지 않는다.
거부된 구조에서 독립 위반은 유지하고, 재귀적 escape 해석으로 새 위반을 추정하지 않는다.

공식 시험은 같은 격리 CPU 환경에 `pytest==9.1.1 pytest-asyncio==1.3.0`을 설치한 뒤
기존 도구의 `--upstream-tests /path/to/new-external-directory` 옵션으로 실행한다.
생성 harness는 upstream conftest/engine import 대신 기존 dependency adapter만 사용한다.
원래 시험 코드는 compile하여 실행하며 pytest raw diagnostics는 메모리에서만 처리한다.
지속 산출물은 고정 시험 목록·카운터·시간뿐이다. 실제 제품 연결은 계속 A 인계 대기다.

## A 고정 소스의 제품 경로 검증

PR48이 최종 통합 head와 CI 성공으로 명시한 `7acd3d02f01cda6910a9e7c062f36611dcac5d37`을
별도 검증 checkout에 고정하고 B-2의 새 파일만 적용한다. 기존 A 파일은 교체하지 않는다.
`b2_integration_cpu.py`는 정확 A HEAD와 tracked 파일 불변을 확인한 뒤 기존 ResponseContentExecutor,
run/judge/report/store를 그대로 재사용한다. 실제 참조 validation handler의 오류 응답을 수집 경계에
공급하고 helper 관측과 기존 REQ-13 필드 판정을 대조한다.

정상/옵션·추론 응답은 EXAMPLE transport이며 실제 엔진 성공으로 승격하지 않는다.
EXAMPLE PASS 사례와 확인된 추가 응답 필드 + 후속 실패의 독립 FAIL 사례를 각각 EVD→RES/RPT로
검증한다. 저장 Ref/해시·store.verify·합성 표식/예외 원문 미포함도 확인한다.
이 시험은 새 product executor 등록이나 전체 실제 엔진 소비의 증명이 아니다.
response_content 파일 소유권·운영 정책·실제 target 연결은 여전히 별도 인계가 필요하다.

재현은 A 고정 checkout + B-2-only overlay에서 앞의 CPU 가상환경과 `PYTHONPATH=src:.`를 사용한다.
`tools/b2_integration_cpu.py --source <pinned-vllm> --output <new-external-directory>`로 실행한다.
