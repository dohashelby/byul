# 기존 PLT01/04 정상 대조의 공통 호출

`KubernetesHTTP.normal_requests(network, requests, timeout)`는 기존
`REQUEST_BINDING_PROFILE`의 정확 ENV/모델 매핑·소유 run·엔진/프로브 전후 API
세대·전체 예산과 고정 `stream_probe.py` bytes를 재사용한다. 기존 `__call__`의
REQ11 요청 목록과 REQ10 sampling 계약은 유지한다. 별도 Gateway/전송 계층이나
새 수집 프로그램·CheckDef·정책·판정을 만들지 않는다.

입력은 기존 `functional_requests(ctx, via='engine')`가 현재 PLT01/04에서 생성하는
한 건뿐이다. ID `func|POST /v1/completions`, POST `/v1/completions`, 빈 headers,
`want_body=true`, 정확 API 모델 별칭과 `prompt='ping', max_tokens=1`을 요구한다.
network는 `kubernetes-probe`, timeout은 최대30초다. 임의 route/body/header,
추가 표본·stream·외부 출발점은 원격 I/O 전에 거부한다.

논리 ID의 공백은 기존 수집기 ID 문법에 맞지 않는다. 공개 호출에서 이 정확 ID를
고정 wire ID `functional-control`로 매핑하고, 응답의 ID·method·mode를 대조한 뒤
원래 논리 ID로 돌려준다. 경로·본문·모델·CheckDef 의미는 바꾸지 않는다.
보고에 매핑과 고정 source hash·시각·결과·전후 binding 여부를 남긴다.

완전한 EOF·UTF8·비절단·길이/hash가 확인된 body만 기존 기능 구조 검증에 임시
전달한다. 부분 200은 상태만 보존하고 body를 제공하지 않아 정상 성공이 되지 않는다.
반환 body를 저장하는 API가 아니며 report/로그에는 원문 body·preview·header·stderr·
임의 예외를 넣지 않는다. 후속 세대 교체나 부모 정리 실패는 고정 진단으로 남기고
앞선 process 관측/독립 FAIL은 삭제하지 않는다.

A2 전용 PLT01/04 소비자는 기능 대조에서 이 live 메서드를 호출하고 기존
`functional_results`의 정확 모델 별칭 검증을 재사용한다. 공통 API 제공만으로
전용 실행기 등록·실제 Kubernetes/GPU 검증·worker 권한/자격 전체 완료를 표시하지
않는다. 주입 시험은 EXAMPLE이며 임의 저장 JSON을 측정 생산자로 인증하지 않는다.
