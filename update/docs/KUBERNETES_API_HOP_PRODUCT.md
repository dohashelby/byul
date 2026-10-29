# PLT08 한 구간의 정식 제품 연결

기존 `guard select/run/judge/report/verify/evidence export/restore`가 읽는 별도
팩 `PACK-KUBERNETES-API-HOP@1`을 사용한다. A2 확정 `6c92cb6` 전용4파일은 그대로
소비하며 `[executors] api_transport="kubernetes"`와 `[api_transport] target`을
기존 guard.toml에 지정한다. 별도 CPU 실행기나 수입 capture는 없다.

`python tools/build_kubernetes_api_pack.py --seed packs/PACK-BASELINE --output <새 폴더>`는
검토된 PACK31 정확 Ref와 manifest를 검증한 snapshot에서 생성한다. 원래 PACK31과
Docker 정책/저장 Ref는 유지하고 77개 ID·76개 정의 bytes·PLT08 질문/기준/판정도
보존한다. PLT08 revision6의 수집 profile·9개 필수 POL 형식·제한만 versioning한다.
새 팩의 Ref를 POL에 지정하고 생성 출력의 정확 Ref를 보존한다. 출력은 DRAFT이며
정책값을 채우거나 읽은 해시를 승인하지 않는다.

필수 POL은 `api_hop_scope`, `api_environment_sha256`, `api_target_sha256`,
`api_binding_sha256`, `api_ip`, `api_port`, `plaintext_forbidden`,
`validity_start`, `validity_end`다. 각각 명시 구간, ENV Ref 문서 해시, 전체 target의
정규 해시, context/run/engine+probe 전체 binding의 정규 해시, 실제 IPv4/port,
명시 bool, timezone이 있는 시작/끝 시각을 뜻한다. Docker ID를 채워 넣지 않는다.
선정의 누락·타입 검사와 실행 직전 실제 결합 검사는 함께 유지한다.

공통 body-free 기록의 request는 고정 source의 logical/wire ID, POST와 route를
뜻한다. subjects는 양쪽 세대 확인 뒤의 선택 probe 컨테이너→선택 engine endpoint다.
HTTP status 관측은 별도 bool이다. timeout/refusal은 전달 증명이 아니며 worker나
배포 caller의 인증으로 취급하지 않는다. 교체/신원 불일치로 결합이 없으면 request와
subjects도 비어 있다. 뒤늦은 정책 만료는 앞서 확인한 응답·주체 사실을 보존하되
유효한 위반 trial로 채택하지 않는다.

명시 금지 평문의 완전한 정상 응답만 기존 독립 FAIL로 보낸다. 허용·거부·누락·불완전은
전체 PASS가 되지 않는다. 출처 상한·INV11·후속 첨부/정리 오류를 유지한다. API 전송
보호와 내부 IPC/TP 보호 필수2결과는 각각 미완료다. TLS/CA·Service/Gateway·실제
worker·배포 caller·TP/다중 GPU와 기업 정책 실증은 이 한 구간으로 대체하지 않는다.

CPU/설치본 CLI의 정확 Ref 왕복과 실제 환경 실행은 구분한다. 기존 소유 EC2의 Start가
용량 부족이면 그 실제 단계만 미완료로 남기고 원래 시간/비용·보존·Stop 책임을 유지한다.
