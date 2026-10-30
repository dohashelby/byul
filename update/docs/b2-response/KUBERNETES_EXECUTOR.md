# REQ13 제품 Kubernetes 오류 필드 실행기

`guard.executors.kubernetes_response_error.KubernetesResponseErrorExecutor`는
`response_content`의 기존 네 정책 필수값을 유지하며 제품 adapter의 실제 수집·투영을 재사용한다.
선택 실행기의 `allowed_error_fields=[]`는 수집한 모든 오류 필드를 금지한다.
미지정·None은 필수값 누락으로 거부하며, 네 정책의 list[str]·중복·공백 검사와
나머지 정책의 빈 목록·지원 대상·옵션 제한은 유지한다.
구성은 target, kubectl_binary(기본 kubectl), request_timeout(기본10초),
max_duration_seconds(기본60초)이다. target의 환경 Ref와 승인 expected_target_sha256,
공개 전송의 소유 대상·이미지·API model alias 검증을 거친다. repository ID와 API alias는 구분한다.
선정 vLLM0.31.0 및 validation-error 전용 공개 프로파일만 지원한다.
현재 설치 A 소스/profile 미지원은 수집 전 닫힌다.

정식 CheckDef의 원 required observations·POL·PASS 규칙을 유지하고, 승인된 조건부 오류
필드 FAIL 규칙이 있는지 요청 전에 검사한다. 해당 규칙 미지원이면
REQ13_RULE_UNSUPPORTED/ABORTED로 요청0건을 남긴다. registry·CheckDef 변경과 공통 명령
등록은 A 작성 범위이며 이번 파일들은 해당 등록이나 실제 K8 실행을 수행하지 않는다.

실제 /tokenize 전후 대조와 무해 형식 오류 응답 한 종류만 다룬다. 수집된 필드 SHA와 실제
allowed_error_fields의 차집합을 정제 adapter가 검증하고 response_violations 추가 관측으로
전달한다. 전체 coverage는 false, 원 성공/옵션/비공개/추론 기능 관측은 null 미수집이다.
HTTP 오류 응답 관측과 native handler·모델·GPU 소비는 별도 범위다.
정상 대조는 수집·binding·완전성이 확인된 성공/실패만 True/False로 기록하며 나머지는 None이다.
후속 정상·cleanup·첨부 실패는 이미 확인한 독립 위반을 지우지 않는다.
오류 challenge의 시도 여부는 primary 요청 요약의 attempted 값으로 기록한다.
증거 작업 디렉터리는 필요하면 생성하며, 기존 첨부 파일은 덮어쓰지 않는다.
첨부에는 안전한 hash/count/enum만 기록하고 본문·field key·token·Secret·예외 문장은 저장하지 않는다.

전용 unit은 EXAMPLE 대상/전송 대역과 메모리상 CheckDef 규칙을 사용한다.
기존 whole PASS 유지·partial FAIL/UNKNOWN·정상3값·후기 오류 보존·요청전 gate를 검증한다.
실제 K8/네트워크/기존 CPU 시험은 실행하지 않는다.

## 통합 상태 — 2026-10-30

빈 오류 허용 목록은 명시 deny-all로 보존하고 원4POL의 누락·None·타입 검사를 유지한다. 공통 runner가 전달하는 새 중첩 workdir을 실행기가 생성하며, 첨부 xb 저장 실패에도 독립 FAIL을 보존한다. challenge 시도는 실제 primary 요청 요약에서 읽는다. 직접 정책 영향143개 및 마지막 전용71개, 통합 core/첨부 영향77개가 통과했고 집중검토 지적은 수정 확인됐다. 중복 시험을 성과 수로 합산하지 않는다.

A108의 확정 HTTP 소스를 변경하지 않고 실제 stdlib 자식·loopback으로 EXAMPLE 200→400→200, 필드 hash 위반1개와 정리를 확인했다. 이는 실제 Kubernetes·모델 추론 결과와 구분한다. 기존 adapter19개와 실제 HTTP 소비 증거는 adapter bytes가 같아 재사용했다.

A 공통 변경은 source-cpu 등록과 함께 묶어 요청한다. REQ13은 response_content:kubernetes-error-fields 등록, 기존 내부 _runtime_environ/_kubernetes_target_sha256 전달, 단독 실행에서 선정 target hash 공급이 필요하다. CheckDef의 기존 필수 관측·네 POL·PASS 규칙을 유지하고 coverage=false에서만 optional response_violations nonempty를 확인하는 조건부 FAIL 규칙을 제안한다. 공통 수정 반영 후 정확 통합 소스로 공통 명령→EVD/RES/RPT·Store/복원을 검증해야 한다. 현재 이 최종 경로와 실제 Kubernetes 실행은 0회다.
