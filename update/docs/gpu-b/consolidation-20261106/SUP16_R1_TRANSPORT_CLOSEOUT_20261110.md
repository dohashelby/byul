# SUP-16 R1 기동·전송 거부·후속 소유 정리 — 2026-11-10

기존 단일 GPU 인스턴스의 R1 운영 시도는 `.json` 업로드 형식이 재사용 PUT helper 계약과 맞지 않아 원격 init/SSM PUT 전에 중단됐다. 설치·health·wholecaller·모델/worker·역할/제품 실행은 0이다. 이 기록은 transport 실패와 소유 정리 검증이며 SUP-16 제품 PASS나 LAB 완료 추가 근거가 아니다.

## 원 타임라인

아래는 2026-11-10 KST다. 감독 앵커와 실제 AWS CLI 구간을 분리하며 앵커를 실제 action 수락 시각으로 바꾸지 않는다.

| 시각(KST) | 구분·원 관측 |
| --- | --- |
| 15:25:54.329316 | start intent 감독 앵커. compute/IP 구간의 보수 시작점 |
| 15:25:58.311780~15:25:59.927949 | 실제 start CLI 구간·exit0·시작 수락. 앵커와 별도 |
| 15:28:54.966157 | 전송 자식 terminal exit1·reaped·PG terminal·cleanup 오류0. helper는 `.json` 형식 거부 ValueError, 상위 driver는 빈 메시지 upload AssertionError를 별도로 보존 |
| 15:28:58.518766 / 15:28:58.519061 | 원 stop intent 감독 앵커 / 기존 stop-response 기록 앵커. 실제 stop CLI 시각이 아님 |
| 15:29:00.680143~15:29:01.782944 | 실제 normal stop CLI 1회·exit0·stopping 수락 |
| 15:32:04.957959 | execute exit1·reaped·PG terminal·cleanup 오류0. 원180초 stopped 관측 실패·supervisor final 부재 보존 |
| 15:33:13.831620~15:33:19.899537 | 원 full8의 각 query 구간. 선택 EC2 stopping·다른2대 stopped, 해당 SSM Online·활성 명령0·EIP0·EBS440GiB. 각 서비스의 조회 시각은 서로 다름 |
| 15:36:41.813243~15:36:42.773418 | 후속 EC2-only 조회·exit0·추가 mutation0: 소유3대 stopped·공인 IP0. 앞 full8/SSM과 같은 시각의 전체 조회로 합치지 않음 |

정확한 power-off 전이 시각은 **null(미관측)**이다. 실제 start1·normal stop1·SSM send0을 확인했으며 후속 정리 시 current_jobs0이다. 이 값은 설치·health·모델/역할 실행 성공을 뜻하지 않는다.

## 실패·검증 범위

원 PUT helper는 `.py`·`.zip`·`.tar.gz`·`.whl`를 받으며 `.json`은 거부한다. 원 helper ValueError와 driver AssertionError·exit1, 원 guard·기한·실패를 그대로 보존한다. 새 입력/재사용 helper의 형식 계약 대조 누락은 원 검토 정정으로 남기고 이전 기록을 덮어쓰지 않는다.

원 제어 ZIP은 612153bytes·318members, SHA `c7e22460f0e7d0663424bfdace80b7d640936abe0e98094eb97b567ecc042b70`다. S6 최초 무결성 cache `f08102b3bd2998a1203190927df730dceb1ac6dccc35519efe473d571f665c1a`의 318 CRC 오류0·원본11동일 완료 보고를 재사용했다. 새 CRC 반복은 하지 않았다.

S2 원 요약 SHA `3769f3593369421c9ce0b34463752548f6ed8af1dfe2411a36b0a0d6a298e328`의 당시 검토 pending은 원문으로 보존한다. 후속 S6 최종 독립 검토 SHA `88c43b8a9858e8829b854d31169e5ff9b74649b0540dbc109c501f2787edabd1`는44개 대조·불일치0이며 CLI68개 request/stdout/stderr 연결138개 대조도 불일치0이다. 원 제품 EVD/RES/RPT·정확 Ref가 생기지 않아 복원/저장팩 재판정 대상은 없다. 이를 복원 성공으로 표기하지 않는다.

## 비용·후속

원 start intent부터 후속 stopped 관측까지의 compute/IP 보수 구간을 반영한 누적 추정은 **$77.34701794825**, 예비 $5를 제외한 기존 실행 한도 잔여는 **$17.65298205175**다. S6 원 계산77.34701794824997/17.65298205175003과 부동소수 끝자리 표기 차이이며 새 비용 계산이 아니다. 확정 청구가 아니고 EBS440GiB 저장비는 계속된다. 후속 EC2-only로 앞 full8의 SSM/EIP/EBS를 현재 조회한 것으로 바꾸지 않는다.

final operator freeze SHA는 `d0b7feab87fa0504153af928d041e47dea49b172379576895b2e2a9fbfe96249`다. 소프트웨어 기준선 `c16f4991`의510/8ff·해당 head CI14성공 기록과 기존 V13 실제 GPU466/2cb는 별도다. R1은 제품 설치에 미도달했으므로 이 두 기준선을 새 R1 제품/GPU 관측으로 환산하지 않는다. LAB10/33·43행 전체·원 Ref/FAIL/UNKNOWN/EXAMPLE/DRAFT/official 값은 그대로다.

다음은 S5의 정상 data-only ZIP과 installer 최소 delta 한정 CPU 준비·S6 해당 변경 검토, 이후 S2의 원 조건 재확인이다. 현재는 R1 후속 stopped 관측으로 중지 상태를 확인했으며 runtime 준비 완료나 새 역할 실측을 선반영하지 않는다. 원 attempt·실패·180초 관측 실패·final 부재는 보존한다. [현재 준비 한계](SUP16_SEQUENTIAL_PREPARATION_20261110.md)와 [현재 LAB 업무표](LAB43_CURRENT_20261110.md)를 함께 본다. 이 문서는 새 source 변경·새 실행 gate·새 승인을 추가하지 않는다.
