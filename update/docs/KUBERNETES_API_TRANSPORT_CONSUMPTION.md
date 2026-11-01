# PLT08 한 구간의 Kubernetes 소비

전용 `api_transport`는 공통 `e8e915ba061ec2f80e682319daac98d5bc7840ef`의
`KubernetesHTTP.api_hop_snapshot(policy_values, timeout)`만 live 호출한다.
새 kubectl·HTTP 수집기나 저장 capture 입력 경로를 만들지 않는다. 기존 공통
Kubernetes 실행 골격과 기존 EVD/RuleTable 판정 의미를 재사용한다.

정확 ENV Ref·target·engine/probe 전체 binding hash·IPv4/port·명시 금지와 유효창이
결합되고, 고정 HTTP source의 완전한 구조 검증 정상 응답과 전후 세대가 확인된
한 구간만 `forbidden_plaintext_successes`의 양성 사실로 남긴다. 이 관측을 기존
판정기가 독립 FAIL로 평가한다. 정책 허용·거부·누락·부분 200·잘못된 모델과
구간 내 세대 교체는 정상 성공이나 보안 PASS가 아니다. 후속 유효창 만료에는
초기 응답 사실을 보존하되 유효한 금지 위반으로 채택하지 않는다.

부모 정리 공백과 첨부 실패는 관측된 앞선 사실과 구분한다. 첨부 실패는 기존
ABORTED 처리와 출처 하향을 따르며 독립 양성 사실을 지우지 않는다. runner 주입과
예제 ENV는 `lab=false`에서도 EXAMPLE을 보존한다. 본문·preview·header·환경/argv·
임의 오류 원문은 저장하지 않는다.

프로파일은 `explicit-owned-kubernetes-api-plaintext-v1`이다. 대응 CheckDef의
`collection_profile`이 이 값일 때만 전용 실행기가 허용된다. 공통 normalizer의
9개 flat 정책 필드를 그대로 사용하며 Docker ID·기본 금지·예외를 자동 생성하지
않는다. 기존 PACK31/PLT08 revision5는 이 신규 프로파일이 아니므로 거부한다.
기존 Docker 실행기·정규화·저장 Ref와 판정은 유지한다.

A-1의 versioned 정책/CheckDef·팩·registry 정식 연결은 별도 인계 사항이다.
전용 직접 시험은 같은 실제 클래스와 공통 runner의 응답 주입을 사용하고,
제안된 profile의 unit 소비 확인을 정식 CLI 선택/등록과 구분한다. 새 정책에 기존
Docker 필수값을 dummy로 채우거나 select 관문을 우회하지 않는다. 정식 연결을
받으면 기존 CLI→EVD/RES/RPT→verify→반출/복원·정확 Ref 재판정을 이어간다.

`api_transport_scope_complete=false`와 내부 IPC·TP 미완료를 유지한다. API 전송
보호와 내부 IPC·TP 보호의 두 필수 결과를 각각 추적한다. 이 한 구간은 실제 배포
caller/Gateway/Service·TLS/CA/peer 인증·암호화·다중 GPU/TP 또는 모델 worker 소비를
증명하지 않으며 실제 기업 정책의 승인도 대신하지 않는다.
