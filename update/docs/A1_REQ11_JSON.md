# REQ-11 모호한 JSON 응답 보수

기준은 0b82eea5f74622e23504aa2599681e6db22ce374, PACK31, 프레임워크91e84c7이다. 고정 EXAMPLE 응답의 `prompt_token_ids`를 중복시키면 기존 파서는 마지막 키만 사용했다. foreign→expected 순서는 귀속 검증됨으로, expected→foreign 순서는 타 요청 입력 소비 위반으로 처리돼 같은 모호한 packet의 키 순서가 근거와 위반을 결정했다. 실제 엔진 결과가 아니라 저장소 밖 `duplicate-response-before.json`에 보존한 CPU 재현이다.

실행기0.3.1은 기존 `owned_capture.strict_json`으로 일반 JSON 응답·tokenizer 응답·SSE data frame의 중복 키와 비정상 JSON을 거부한다. 일반 응답의 기존 크기/형식 검사를 먼저 사용하고 개별 SSE frame에 새로 더 작은 크기 제한을 넣지 않는다. `content_common.py`와 다른 담당자의 실행기는 변경하지 않는다.

모호한 단일 packet은 소비 입력·정상 응답·타 요청의 자료로 확정하지 않고 gap/미수집으로 남긴다. 앞서 또는 다른 연결의 유효 frame에서 독립적으로 확보한 위반은 이후 모호한 frame·응답 실패·첨부 저장 실패로 지우지 않는다. 원문/예외 내용은 진단·로그·EVD에 추가하지 않는다. decoder 오류는 고정 gap 문자열로 처리하며 저장 projection은 기존과 같이 hash·수집 상태만 남긴다.

정상 packet의 기존 관측·판정·요청·fixture 지문·시간/overlap 계산은 유지한다. 반환 생성 출력의 독립 oracle이 없으므로 REQ-11 보안 PASS를 새로 열지 않는다. 새 기업 정책·CheckDef·팩 개정·registry·판정기·core/models/store·공용 contracts·센서 profile1~7 변경은 없다. 과거 저장 EVD/Ref를 현재 파서로 다시 해석하지 않고 기존 판정 입력으로 재생한다.

CPU 회귀는 동일/서로 다른 중복 키, 순서 반전, 중첩 중복, JSON/SSE, tokenizer, 기존 크기 상한, 비밀 canary 비저장, 다른 유효 frame의 독립 위반, 이후 수집/첨부 실패, 제품 EVD/RPT 저장·복원·정확 Ref 재판정을 확인한다. 실제 vLLM/Gateway/GPU·Docker daemon 기동·이미지 pull·모델 다운로드·AWS 실행은 없다.

