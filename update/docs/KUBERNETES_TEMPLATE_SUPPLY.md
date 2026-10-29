# SUP-03 Kubernetes 전용 소비

`KubernetesTemplateSupplyExecutor`는 기존 `fixed-template-supply-v1`의 template
bytes·승인 목록 비교·API 입력 projection을 기존 Kubernetes 경계로 전환한다.
공통 소비 기준은 `16d97ded1932804e42c3af2d2319f8fcfec4c5ee`이며 공동 base는
`c9af729cd1893eda14d0bb222a8fab46c2a6d7b8`이다.

이 전용 묶음은 공통 registry를 수정하지 않는다. A-1이 확정 커밋을 소비하고
`template_validation:kubernetes`를 이 클래스에 등록한 뒤 정식 제품 연결을
수락한다. 제안 등록을 주입한 CPU 시험은 실제 설치판 등록·클러스터/GPU 실증의
근거가 아니다. 기존 Docker 실행·저장 증거·팩31·판정기는 유지한다.

입력은 기존 `guard-kubernetes-request-binding-target/1`, 정확 ENV Ref,
`guard.model-request-mapping/1` 또는 기존 repository/revision/API alias 매핑,
명시 namespace/Pod/container 세대·소유 run이다. 별도 target·kubectl wrapper나
새 정책키를 추가하지 않는다. 설정은 `[template_validation]`의 `target`,
`kubectl_binary`, 기존 요청/관측 상한만 받으며 native capture·임의 프로브·
상태 변경·역할 자격 설정을 받지 않는다. 이 실행기는 설치·pull·Pod 생성이나
삭제·엔진 재기동을 하지 않는다.

공통 `KubernetesHTTP.template_snapshot()`의 고정 읽기로 전후 template bytes와
선택된 API 세대를 대조한다. 동일 공통 요청 인터페이스의 `tokenize-A`, `before`
correlation ID로 `/tokenize`와 고정 정상 chat을 각각 한 번 보낸다. 본문의 model과
응답 대조에는 검증된 `Ctx.api_model`을 사용하며 ENV repository/revision/전체
inventory를 API alias로 덮어쓰지 않는다. 다른 served-model-name이나 기동 옵션은
선택 불일치로 처리한다.

`allowed_template_sha256`는 명시 목록이며 빈 목록도 그대로 소비한다. 관측에서
승인값을 보충하지 않는다. 전후 신원이 안정된 경우의 승인 일치/불일치와 엔진이
보고한 정확 token ID 대응은 관측이다. `template_consumer_verified`와
`supply_scope_complete`는 false로 유지하고 원문 challenge는 시도했다고 표시하지
않는다. 기존 보안·기능·성능 판정은 UNKNOWN이다. 승인 목록 불일치도 실제 worker
소비 FAIL로 바꾸지 않는다.

readonly는 선택된 `volumeMount` 선언이다. 프로브가 읽은 현재 template 파일은
worker의 open/read/load, 기동 시 cache bytes, config/tokenizer/secondary sources
소비를 증명하지 않는다. imageID도 node runtime RepoDigest/source 계보가 아니다.
전후 표본은 전체 구간 관측이나 생산자 인증을 증명하지 않는다. native 0.32·
lifecycle의 기존 lab/source gate를 우회하지 않는다.

누락·잘못된 매핑·소유자/파일 교체·응답 상관 실패에서는 관측을 보수적으로
보류한다. 후속 HTTP 실패가 안정된 파일의 승인 목록 비교를 지우지 않는다.
첨부 실패는 기존 코어의 ABORTED와 부분 근거 보존을 따른다. 응답 본문·token ID·
환경/Secret 값·Pod 원문·호스트 경로·예외 원문은 증거에 보존하지 않는다.
주입 runner는 `lab=false`/ENV MEASURED 선언에도 EXAMPLE 상한을 유지한다.
정적 설정만으로 현재 파일/Pod 신원을 이월하지 않는다.
