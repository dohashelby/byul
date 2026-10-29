# B 기준선 소비 검증 — 2026-10-29

공동 base `c9af729cd1893eda14d0bb222a8fab46c2a6d7b8`, PACK31,
framework `91e84c778f4fc8ee1108eaf0551bf51f2f6a2061`를 유지한다.
원격 PR #67 HEAD와 로컬 착수 HEAD가 일치하고 착수 작업 트리는 깨끗했다.
기준선 CI는 run40723698477의 성공 5개를 재사용한다.
A의 추가 작업은 PR #68 공통 Kubernetes snapshot, #69 SUP-04 전용 소비로
확인했으며 이 변경을 B 기준선으로 자동 반영하지 않았다.
사용자가 명시한 공동기준선 두 파일의 위치는 별도 확인 요청 중이다.

## 기준선 실제 소비

`539ae77`의 고정 CPU 도구에서 실제 요청 클래스 5조합과 기존 EXAMPLE
제품 경로 5사례를 실행했다. 도구 bytes SHA256은
`5cd080e0257d6fbd5878c2b3d0d28ab3f0b9d057b68d9b69249142469c988159`이다.
기록은 `/private/tmp/byul-b-req10-evidence-20261029/baseline-consumption-3/summary.json`이다.
정상/누락/지원 밖은 security UNKNOWN, 합성 위반+후속 실패는 FAIL,
합성 위반+첨부 실패는 ABORTED와 FAIL을 함께 유지했다.
실제 요청 클래스는 정상 4건 수용·named-json 거부였고, 추론 전송 5사례는 EXAMPLE이다.
각 EVD/RES/RPT Ref와 정확 Ref 재판정, store.verify를 확인했다.

첫 시도는 시험 환경의 openai_harmony 의존성 누락으로 import에 실패했다.
격리 환경에 원문 common.txt를 설치한 뒤, 두 번째 시도는 Unicode 경로의
표기 차이로 자체 import 위치 검사에서 거부됐다. 실제 cwd에서 얻은 경로로
PYTHONPATH를 구성한 세 번째 시도가 성공했다. 제품 gate와 원문 소스는 유지했다.
제품 lock·팩·registry 변경은 없다.

## 목표 0.31과 참조 소스 차이

과거 CPU pin `5d35ce3934a7804b298559d2d99eec33792e1eb9`의 git describe는
`v0.33.0rc0-41-g5d35ce393`이다. 목표 0.31 source
`db9527a46873454610df6dbedf79a36d6bf1a7f6`와 구분한다.
두 고정 commit의 원문 함수를 `ast.dump(include_attributes=False)`로 비교했다.

| 함수 | AST 비교 |
| --- | --- |
| ChatCompletionRequest.check_structured_outputs_count | 동일 |
| ChatCompletionRequest.check_tool_usage | 동일 |
| ChatCompletionRequest.to_sampling_params | 차이 있음 |
| validate_xgrammar_grammar | 차이 있음 |

따라서 참조 요청 validator의 근거와 0.31 backend 실제 실행은 별개다.
정식 설치판 producer·배포 이미지 digest·활성 backend와 실제 CPU/GPU 실행은
A와 확정할 후속 연결 조건이다. numeric sampling과 ENV/API 모델 신원 구분은 유지한다.
