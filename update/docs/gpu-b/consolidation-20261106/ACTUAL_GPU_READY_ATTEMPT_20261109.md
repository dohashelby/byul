# GPU 1장 첫 실제 기동 — host health 확인, source 전송에서 중단

이 기록은 실제 실행 결과다. REQ-08·09·10 제품 수집이나 SUP-16 역할별 관측을 실행한 결과가 아니다. 검토된 runtime·Ready 전용 snapshot `0729e7b0e067f9504d0899f3f0670adb49a697f6` / source manifest `d486961cfb4563bca4221032fa06c59a6ad9be000a4e10efe4cefd4544105039`를 사용했다. 제품 수집용 보존 수정 snapshot `b85dac21bfe380eaa7f06a7fe2b451a2e168daa2`는 별도로 CI14/14를 통과했다.

## 실제 관측

| 2026-11-09 KST | 관측 |
| --- | --- |
| 16:33:34 | 기존 서울 노드 1대의 start-intent와 stopped→pending 양성 응답 |
| 16:37:36 | 실제 running·SSM Online·계정 활성 명령 0 확인 |
| 16:44:27 | 고정 execute 1회 제출: PID/PG545, start ticks618341 |
| 16:44:34 | health Success: L4 1장, CPU8, MemTotal31,619,244KiB, 실제 boot ID 확인. 원 2시간 보호 타이머는 16:33:55 활성, 수동 reset 없음 |
| 16:44:50 | 준비 payload PUT 후 source bundle PUT가 전송 helper의 이름 계약에서 거부됨. 해당 source PUT의 AWS API 호출 0 |
| 16:44:55 | 정상 stop 1회 양성 응답 running→stopping |
| 16:48:03 | 원 stop180 관측 실패를 보존한 execute exit1, child reap·process group terminal·cleanup 오류 0 |
| 16:49:01~02 | full7 조회: 대상 stopping, 활성 SSM0·EIP0·EBS440GiB |
| 16:52:09 | 후속 EC2-only 조회: 소유 3대 모두 stopped·공인 IP0. 정확한 off 전이 시점은 미확인 |

실제 start1·SSM SendCommand7·정상 stop1이며 미해결 제어 작업은 0이다. source 설치·새 venv·엔진 실행·Ready·선정 제품 수집·EVD/RES/RPT 생성에는 도달하지 않았다. 호스트 health 성공을 엔진·제품 PASS로 집계하지 않는다. 전송 실패도 제품 보안 FAIL로 승격하지 않는다.

## 확인한 오류와 후속

원 전송 helper `51ccda6e…`의 `put_name`은 `.py/.zip/.tar.gz/.whl`만 허용하고, local source basename과 remote 이름에 같은 규칙을 적용한다. 새 호출자가 사용한 `source.bundle`과 이어서 전송할 `source-pins.json`이 이 계약에 맞지 않았다. 사전 fullpacket 검토에서 실제 upload→원 helper 소비 계약 대조가 누락됐음을 후속 기록으로 남겼다. 원 실패·코드·감독 기록은 수정하지 않는다.

후속은 두 원본을 실제 정상 ZIP에 포장하고, 정확 member·bytes·SHA·CRC를 확인해 기존 installer 입력 쓰기에 연결하는 최소 변경이다. 원 helper 허용 규칙을 넓히거나 raw bundle의 확장자만 바꾸지 않는다. 현 실행에 수정 코드를 삽입하거나 같은 실패 조건으로 재기동하지 않았다. 다음 실행은 이 차이의 직접 계약 검증과 고정 검토·새 자원/예산 관측 뒤 진행한다.

## 보존과 비용

원 control archive는 2,701,733bytes, 427members(원자료426개와 manifest), SHA-256 `ad76278da500fff14938089c9735e694412be6e18ce66473a323a41377355a6a`다. start/poststart/execute metadata archive는 16,643bytes·14members, SHA-256 `cd90a840648131277de95bc48c3c5a6a051d4b6278e48179ccdb11d449b52b4b`다. byte hash·CRC·manifest와 실제 수락/실패/종료 연결을 대조했다.

독립 control 검토는 836개 대조 불일치0, 비용·closeout 후속은 54개 대조 불일치0이다. 검토 파일 SHA는 각각 `1e09f6f544b2bcfda51d7207405537ed6ec462cddcac8f1252dac096bdad58cd`, `c6fc48da60019f823b8c3791b63fe746dbfa230133f9c274a2994c6cc3a75da8`이다. 실제 제품 객체가 없으므로 Store 반출·복원·저장 팩 재판정은 이 실행의 수행 결과가 아니다.

16:52:09 관측까지 누적 보수 장부 추정은 **$73.48570091113785**이며 확정 청구가 아니다. 이번 compute·공인 IP 구간은 닫았고, 보존 EBS440GiB의 $0.055748/h 저장 비용은 계속된다. 원 stop180 실패와 후속 stopped 관측을 구분한다. resize 시점의 과거 `physical_guest_GPU_unobserved=true`나 검토 전 `pending=true` 필드를 현재 health·후속 검토 완료와 혼합하지 않는다.

원 43개 상태·FAIL/UNKNOWN·Ref는 변경하지 않았다. 이번 사용자 목표인 REQ-08·09·10 실제 수집 및 SUP-16 순차 역할 관측은 계속 남아 있다.
