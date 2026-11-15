# fresh ASGI 선택과 native 요청 입력의 세션 HMAC 연결

2026-11-15 사용자 “후속 profile·HMAC 키 자산 승인”의 구현이다. 기준 V1 후속 native profile /28을 추가하며 기존 /1~ /27·원본 센서·ABI/OS 정책은 보존한다. 판본/이미지는 작업지시서0.1절과 adapter 데이터를 따른다. 공통 GPU /1·/4·판정/수락·정책·실행기 활성화·GPU 등록은 바꾸지 않는다. M-B4는 보관한다.

## 연결하는 원 근거

| 관측 | 실제 확인 | 주장하지 않는 범위 |
|---|---|---|
| ASGI invocation | 허용 경로/메서드의 같은 scope·프로세스·살아 있는 invocation과 별도 nonce | 헤더 신원 인증·다른 요청의 권한 |
| OpenAIServing._base_request_id | 정확 create_completion 원 code/globals·같은 raw_request/scope·engine 객체 안에서 선택한 ID | 반환 ID만으로 worker 소비 |
| OpenAIServing._log_inputs | 같은 원 handler가 고른 전체 native ID. 실제 인자와 handler 지역 변수의 같은 객체, 앞선 기본 선택 HMAC·engine 객체 | 로그 본문/프롬프트/params 읽기·로그 저장 성공 |
| Processor.process_inputs | 정확 AsyncLLM.add_request 원 code/globals/self·processor 객체·인자 ID와 앞선 전체 선택 ID의 일치 | sampler 제약 소비·worker/RPC/TP2 그룹·GPU 해제 |

선택 callback·native 입력의 CALL/RETURN/RAISE와 ASGI 종료 순서를 파서가 대조한다. ContextVar·접두사·문자열만으로 연결하지 않는다. 종료 context·다른 source/호출/객체·API가 고르지 않은 ID는 fresh 근거가 아니다. 부족한 CALL/종료/선택·반복 입력은 공백으로 남긴다. native_owner_verified는 원 호출의 engine/processor 객체 대응이다. worker_connection_verified·producer_authenticated·question_scope_complete는 false다.

## 키와 기존 자격 보존

- request-binding-key.json: 닫힌 guard-lifecycle-request-key/1·prepared session·32바이트 무작위 키만 둔다. 생성부터0600·owner·정규 파일·NOFOLLOW·256바이트·읽기 중 신원·세션·정확 SHA를 검증한다. 키는 반환/공개 요약/기록에 넣지 않는다.
- 키 SHA를 넣어 정확히 렌더한 관측기 bytes와 manifest를 재대조한다. 키/권한/session·관측기/기반 source 변조는 거부한다. 실제 기동 전 파일 소유권도 별도 확인한다.
- 새 요청/ASGI 지문은 domain을 분리한 세션 HMAC이다. 과거 SHA와 값/의미를 합치거나 소급 재작성하지 않는다. property·prompt·params·토큰·출력을 관측하지 않고 원 인자/반환·예외·ASGI receive/send 횟수를 보존한다.
- 기존 template 뒤에 같은 관측 확장을 조합한다. 판본별 센서 복사·핀 허용 범위 확대를 하지 않는다. /27 행·기존 sensor/ABI 파일은 main bytes와 일치한다.

## 직접 검증과 남은 범위

태그/실제 설치 대조 bytes의 handler·선택·generate/add_request·processor 함수 본문을 AST 그대로 실행하고 협력 객체만 EXAMPLE로 구성했다. 실제 vLLM/모델을 import·기동하지 않았다.

- 직접 영향/기존 lifecycle·파서·중립성·경로243개 통과(pytest3.71초·부모4.167초).
- 승인된 기준 이미지 추출 CPython3.12.8로 관련61개 통과(pytest1.52초·부모1.731초). CI의 별도3.12.8 job에도 새 직접 시험을 연결했다.
- 헤더 부재·세션 분리·streaming·ASGI 종료 후 task·다른 code/객체/source/선택 ID·property 비실행·동일 원 예외·재생/과장·비밀 자산 오류를 확인했다.
- 최초 새 시험의 협력 객체 이름 누락 수정 뒤 재검증했다. 기존 assert·기한·skip은 바꾸지 않았다. 로컬 전체 pytest·새 PR CI는 미실행이다.

worker/RPC·캐시 단위/소유권·runner 실제 소비·기동/해제의 파생 의미, 현재 ENV/target·GPU/J01/J02·등록·J03은 미완료다. 관측 파일/CPU 시험을 실측·PASS·1차 완성으로 세지 않는다.
