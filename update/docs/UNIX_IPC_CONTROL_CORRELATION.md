# PLT02 정상 대조 응답 대응

기존 `unix_ipc` 소비자가 응답 목록을 위치로만 대응하면 다른 명시 요청 ID의
정상 형식 응답도 `functional_probe_results=pass`로 기록했다. 전용 소비자는
요청·응답 수와 응답 행 형식을 확인하고, ID가 있는 응답은 실제 요청 ID와
정확하게 대응해야 채택한다. 기존 ID 없는 callback 응답 형식은 유지한다.

미대응·누락·추가·잘못된 형식은 `no_response`와 고정 진단
`IPC_NORMAL_CONTROL_RESPONSE_UNCORRELATED`로 남긴다. 앞선 소유 구간에서
확인한 비허용 UID의 연결 위반은 보존하며 `ipc_scope_complete=false`,
전체 기능·성능·보안 PASS 제한은 유지한다. 응답 원문이나 ID 원문을 저장하지
않는다.

이 보수는 기존 소비자의 대응 정합성에 한정한다. Kubernetes selector/exec,
공통 신뢰 검증, 소켓·actor 생산자, 정식 Kubernetes 등록은 추가하지 않는다.
다음 Kubernetes 전환은 A-1의 exact ENV/target/run·전후 세대·선정 소켓과
명시 actor 정책 계약을 기존 `unix_ipc` 판정에서 소비한다. 실제 worker 귀속,
명령 소비·동일 UID의 비허용 프로세스·SHM/TCP·내부 TP 보호는 미검증이다.
