# 고정 GPU 이미지의 REQ-11 소비 계약

공통 소유자는 개발자 A다. 기존 `request_binding`의 요청 사례·전송·수집·판정·CLI를 재사용한다. 추가 실행기나 GPU 전용 증거 형식은 만들지 않는다. 이 문서의 이미지 소스 검토와 CPU 대역 회귀는 실제 GPU 실행·worker 소비·자원 반납 실증이 아니다.

## 이미지와 소스

공식 `vllm/vllm-openai:v0.31.0`에서 다음을 구분한다.

| 식별 대상 | SHA-256 |
| --- | --- |
| 다중 플랫폼 index | `c1c9f6fd5c109ba7f0546a59f5b2f15fb87f64c77782e90a27b648b42a8e67c3` |
| 지원하는 linux/amd64 manifest | `a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b` |
| image config | `c76d0e2225a4b1cb1e2109ace39639f55e714abd1a7a427acc8b0bbd7f6a83b3` |
| vLLM 설치 layer | `30530efd7061ec4f5a1a5f179219c218fc72c486dd215339940b33f7455eb26b` |

지원 입력은 정확한 `vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b`와 버전 `0.31.0`이다. tag·index·config·다른 저장소나 판본을 이 값으로 추정하지 않는다. 기존 CPU digest·다른 수집기의 CPU 제한은 유지한다.

공식 registry의 index/manifest/config와 설치 layer bytes의 SHA를 확인했다. 설치 layer의 `usr/local/lib/python3.12/dist-packages/vllm`에서 Python 소스2771개를 추출했고, 뒤의12개 layer도 digest와 항목을 확인했다. 필요한 vLLM 소스를 덮어쓰거나 제거하는 항목은 없었다. REQ11 소비 소스3개와 기존 native 프로파일26개의 합집합28파일은 기존 검토 SHA와 모두 같았다. build commit 표시는 `db9527a46873454610df6dbedf79a36d6bf1a7f6`이며, build 표시만으로 파일 일치를 대신하지 않았다.

REQ11 계약은 어댑터에 이미 있는 세 파일의 정확한 SHA/구간을 재사용한다. 이 검토로 native 생산자의 GPU 실행·최종 tensor 귀속·가중치 읽기·GPU 반납을 지원 완료로 올리지 않는다. Kubernetes 보고 `imageID`는 실행에서 별도 수집하며 manifest/config로 바꾸지 않는다.

검토 원자료는 저장소 밖 `guard-output/byul-developer-a-execution-20261028`의 `gpu-image-review.json`, `gpu-installed-source-manifest.json`, `gpu-later-layer-review.json`, `gpu-consumer-source-comparison.json`이다. 원본 이미지의 공식 배포는 [vLLM v0.31.0](https://github.com/vllm-project/vllm/releases/tag/v0.31.0)이다.

## 기동과 제품 경로

GPU target의 `comparison_config.launch.profile`은 `gpu-bounded-v1`을 명시한다. 기존 CPU 계약에는 이 새 필드를 추가하지 않으며 `cpu-bounded-v1`을 유지한다. GPU 프로파일은 기존 요청 수·응답 크기·시간 한도와 고정 template 경계를 사용하는 선언이다. GPU 메모리나 scheduler 예산을 집행했다는 뜻이 아니다.

기존 `guard-text-chat-v1`의 정확한 metadata, regular readOnly 파일 bytes, 실제 Pod command/args, 선택 ENV·namespace/Pod/container/image 세대의 전후 일치를 요구한다. CPU 프로파일·미검토 template·쓰기 가능한 mount로 GPU 요청을 보내지 않는다. 고정 template 경로는 `/guard-assets/chat/guard-text-chat-v1.jinja`다. ConfigMap의 projected symlink를 사용하지 않고 소유 호스트의 고정 regular 파일을 정확 경로에 readOnly mount한다.

기존 [Kubernetes REQ11](KUBERNETES_REQ11.md)의 직접 Pod IPv4 HTTP, JSON/SSE, 모델 매핑, prebound/after_baseline, EVD/RES/RPT·반출/복원·재판정을 그대로 사용한다. `request_binding:real`은0.3.4, `request_binding:kubernetes`는0.1.3이다. 공용 판정과 팩 revision은 변경하지 않는다. 독립 출력 oracle 부재는 security UNKNOWN으로 남기고, 독립 FAIL은 후속 누락·첨부·정리 오류가 있어도 보존한다.

## 실제 실행에서 남은 확인

단일 dev GPU의 실제 imageID·설치 소스·모델 revision/inventory/API alias와 기동 인수를 고정하고, 소유 Python probe에서 JSON/SSE 제품 왕복을 실행한다. 실제 baseline 종료와 candidate 세대 선택, EVD/RES/RPT 검증·반출/복원·재판정, worker 종료·GPU 상태·서버 Stop을 각각 기록한다. CPU 주입 회귀는 EXAMPLE이며 실제 Kubernetes/GPU 결과를 대신하지 않는다.
