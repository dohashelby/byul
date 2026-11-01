# REQ01/03 선택 Pod 경로 소비

`direct_route:kubernetes`와 `role_route:kubernetes`는 기존 수집기·안전 프로브·
역할 환경변수·정책·판정·CLI를 재사용한다. Kubernetes 경계는 기존 정확
ENV/모델 별칭 매핑과 `guard-kubernetes-request-binding-target/1`을 유지한다.
기존 `kubernetes.engine`과 `kubernetes.probe`는 엔진/신뢰 프로브다. 별도
`kubernetes_routes.external_probe`와 `kubernetes_routes.gateway`는 같은 형식의
namespace UID·Pod UID·containerID·imageID·restart·start·node·IP를 모두 요구한다.
네 Pod는 서로 달라야 하며 명시 context/run과 전후 Ready 세대를 확인한다.

`origins.external.network`는 `kubernetes-external-probe`이고 기존 trusted는
`kubernetes-probe`다. `gateway.url`은 선택 Gateway Pod의 정확 IPv4 HTTP 주소다.
`direct_targets`는 선택 엔진의 정확 주소를 가진 external 항목 한 개(REQ01 필수)
또는 빈 목록(REQ03)만 받는다. 이 외 Service/DNS·TLS·host/다른 endpoint를 받지 않는다.
출발점의 신뢰/비신뢰 의미와 역할→환경변수·principal 정책은 운영자가 명시한다.
이름이나 Pod label만으로 인터넷 외부 대역·기업 principal·Gateway 변환표를
증명하지 않는다. 실제 기업 입력과 해당 출발점 구성이 준비되기 전 최종 실증은 대기다.

기존 HTTP probe의 고정 bytes를 그대로 실행한다. 키는 stdin에만 전달하며 요청/
응답 본문·헤더·예외·Pod 원문은 증거로 저장하지 않는다. 고정 안전 요청을 고르는
기존 adapter와 `allow_mutating=False`를 유지한다. 임의 mutating 설정·새 HTTP
프로그램·source/lifecycle gate 해제는 없다. 정상 기능 대조는 검증된 API 별칭을
사용하며 repository/revision/inventory의 의미를 보존한다.

API 세대/응답 상관/수집 누락은 `probe_unavailable`이며 차단 근거가 아니다.
TCP timeout/refused/unreachable은 connect 단계에서 관측된 경우만 그대로 소비한다.
한 batch는 최대64요청/65536바이트 입력이며 기존 최대300초 전체 예산을 공유한다.
각 socket timeout의 합계로 별도 종료 관문을 만들지 않는다. 상한/포착 실패는
완료된 차단 결과로 바꾸지 않는다.
후속 양성 대조/정리 실패는 먼저 안정된 batch의 독립 FAIL 관측을 지우지 않는다.
비밀값을 제거한 선택 세대·프로그램 hash·batch 결합/누락을 별도 첨부한다.
runner 주입은 실제 ENV/lab=false 선언에도 EXAMPLE이며 정적 설정을 실행 증거로
이월하지 않는다. 선택한 두 출발점에 대한 범위이며 전체 네트워크/핸들러 내부
실행·권한 변환·worker/GPU 소비를 증명하지 않는다.

이번 범위는 단일 ENV의 기존 select/run/judge/report/verify와 정확 Ref 왕복이다.
기존 KubernetesTransition은 request_binding 비교만 지원한다. REQ01/03 두 이미지
guard update 지원이나 실제 Gateway가 있는 환경으로 확대했다고 표시하지 않는다.
