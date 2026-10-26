# SUP-13 고정 공급 template의 타입 참조 분기

팩30/DRAFT의 SUP-13 revision3은 기존 `template_validation:real` 실행기를 재사용한다. 원문의 허용하지 않은 참조 실행 기준 중 명시 POL로 금지한 `__class__` 속성 참조 한 분기만 연결한다. 관리 고정 fixture와 검토된 vLLM0.31.0 CPU image만 지원하며 이번 검증은 EXAMPLE 대역이다. 실제 엔진·GPU·Kubernetes 실증은 수행하지 않았다.

`forbidden_template_attributes`는 독립 POL의 필수 목록이다. 이 프로파일은 `["__class__"]` 또는 명시적 빈 목록만 받는다. 제품 template에는 값을 넣지 않으며 빈 목록은 전체 허용이나 PASS가 아니다. 다른 속성은 지원 밖 오류로 요청 전에 거부한다. REQ-07·SUP-03 정책값을 복사하지 않는다.

사용 시 adapter의 `chat_template_profile=guard-type-boundary-v1`을 명시적으로 선택한다. 기본 `guard-text-chat-v1`의 bytes·동작과 기존 프로파일 선택은 유지한다. 새 fixture의 정수 `(1).__class__.__name__`은 파일·프로세스·네트워크 접근이나 임의 Jinja를 실행하지 않는다. 고유 marker는 challenge 메시지에 없으며 정상 경로는 기존 user 메시지 형식을 사용한다. 자원 한도를 검사하는 부하 시험이 아니다.

실행 설정은 `template_validation: {target: /absolute/target.json, request_timeout: 3}`을 사용한다. 소유 target에는 기존 comparison launch와 명시 고정 template 메타데이터, 실제 ENV와 같은 모델/판본/digest, readonly bind, guard.run, 소유 bridge와 실제 endpoint, 이미 취득한 digest 고정 probe_image가 필요하다. 새 fixture를 선택한 대상은 별도 시험 대상이며 실행기가 기존 대상을 교체·재기동하지 않는다. 요청 override·이미지 pull·모델 다운로드·환경 기동을 수행하지 않는다. probe는 자기 run의 임시 컨테이너만 정리한다.

최대6요청·각 출력1token·총관측4096token·전체60초(설정 가능한 상한300초), 요청timeout은 기본3초/상한30초다. 이 기술 상한은 기업의 처리 예산이나 FAIL 한도가 아니다. 기존 프로파일의 예산 설정을 그대로 공유하지 않는다. 모든 HTTP 요청 전후 container/image/StartedAt·선택 파일 hash·readonly·image RepoDigests/원래 entrypoint와 소유 network/IP/endpoint를 대조한다. hostname 대신 확인한 IP로 요청하고 probe는 pull을 금지한다.

1. 정상 렌더 텍스트와 속성 참조 성공 텍스트를 각각 평문 `/tokenize` 기준으로 얻는다. 두 token 배열이 같으면 판정 근거가 없다.
2. 정상 메시지의 chat `/tokenize`와 정상 추론의 prompt token projection이 정상 기준과 같은지 확인한다.
3. challenge 메시지의 `/tokenize` projection이 구별된 참조 성공 기준과 같고 해당 속성이 POL에서 금지됐으면 독립 FAIL이다.
4. 뒤의 정상 추론을 확인한다. 이후 미수집/세대 변경은 이미 세대 결합된 참조 위반을 지우지 않는다.

HTTP400·일반 오류·sandbox 이름·개수만 일치·다른 image/모델/파일/endpoint·세대 변경·정상 대조 실패는 위반 또는 제한 집행 증명이 아니다. 모든 PASS 규칙은 비어 있고 `template_sandbox_scope_complete=false`다. API 입력 projection은 같은 엔진의 렌더 경로 관측이며 독립 worker file-open·생성 출력 귀속·전체 공급물 소비 인증이 아니다. 다른 객체/호출·모든 처리 모드·자원 한도는 남아 있다. GPU image는 별도 검토 없이는 지원하지 않는다.

Artifact에는 정책/fixture hash·허용한 소유 신원·요청별 상태·고정 진단·부분 관측만 남긴다. 원문 요청/응답·token ID·Secret·host mount Source·inspect/예외 원문은 저장하지 않는다. 기존 PLAN→EVD→rule_table→RES·Store 검증 경계를 사용하고 수집 대역은 EXAMPLE로 제한한다. 후행 수집 실패 시 기존 독립 FAIL을 보존한다.

기준 문서는 [Jinja sandbox의 속성 접근 제한](https://jinja.palletsprojects.com/en/stable/sandbox/)과 [vLLM0.31.0 chat protocol](https://docs.vllm.ai/en/v0.31.0/api/vllm/entrypoints/openai/chat_completion/protocol/)이다. 문서의 기능 존재를 실제 집행 증거로 사용하지 않는다. 로컬 승인 의존성에는 Jinja가 없어 실제 renderer의 fixture 실행은 미검증이다. 임의 새 의존성을 추가하지 않으며 실제 엔진 인수 때 별도로 확인한다.
