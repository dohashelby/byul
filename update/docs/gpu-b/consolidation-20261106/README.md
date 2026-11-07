# GPU43 1단계 통합 현황 — 2026-11-06

> **최신 범위(2026-11-07): SUP-03 R2 health 출력 경로 재사용 차단과 후속 정리 검증 중.** 고정 health 출력이 R1에서 이미 생성된 사실을 기동 전 운영 검토에서 놓쳤다. S2는 FRESH_OUTPUT_REQUIRED 종료와 원 정지 절차를 보고했으며 실제 control/후속 중지/비용 구간은 도착 원자료로 검증한다. 새 R3는 health 출력 세대·CloudShell 경로의 source 변경 검토를 완료했지만 실제 배치·당시 자원/비용·전체 운영 결합은 미완료다. 기존 출력·guard·원 실패를 보존하고 제품 완료 수는 올리지 않는다. [시각별 결과](FOLLOWUP_RESULTS.md)를 따른다.

팀원 인계와 1단계 종료 기준의 해석은 [TEAM_HANDOFF.md](TEAM_HANDOFF.md)를 먼저 확인한다. 기존 “42개 마감 가능”은 잠정 행정 해석이며 제품 증거 절차 완료 수로 사용하지 않는다. 아래 통합 시점의 관측과 후속 마감 자료의 시점을 구분한다.

사용자 승인 후 기존 세션에 배정한 후속 업무의 고정·검증 반환은 [FOLLOWUP_RESULTS.md](FOLLOWUP_RESULTS.md)에 추가한다. 진행 중 준비나 검토 접수를 실행 완료로 표시하지 않는다.

이 문서는 세션1~6의 고정 결과와 미반영 구현을 PR에서 검토하기 위한 시점별 인계다. 기존 현황 정본·operator-status·비용 장부·원자료를 대신하지 않는다. 기준 main은 `e5a8ba7589be09fa472c35276e190b117426a0ae`, 실측에 사용한 고정 제품 source는 `3b3ff137b31b830403ccce1997dabf26319bac4f`다. 이 PR의 새 source를 과거 실행 source로 소급하지 않는다.

## 초기 통합 시점의 세션별 확인 범위

| 세션 | 확정된 작업 | 남은 작업 |
| --- | --- | --- |
| 1 — 실행 묶음 만들기 | SUP-16 native17 writer의 LF 포함 해시 계약 수정, 실제 journal 양성·잘린 LF·다른 session 거부 CPU 3/3 및 세션3 검토 완료. SUP-15 cold compile·출력 1회 검증 완료 | SUP-16 실제 writer·제품 연결 확인. SUP-15 warm 45초 alive 원인과 나머지 분기 미완료 |
| 2 — GPU 실측 운영 | 권한·호출 이름·설치 payload 연결 오류 수정. 이전 L4/TP1 적재·HTTP200 확인. 후속 실행의 모델·namespace 정리, 정상stop1회 수락 뒤 19:34:59 KST 완료 원조회에서 소유3대 stopped·공인IP0 확인 | 새 GB09 대신 옛 GB07 선택·원900초 반출 timeout·감독 불확실성 차단은 보존. 원stop180은 기한 내 미확인/exit1이며 후속3대 중지를 별도 관측으로 연결. 최종 비용 마감·GB09 전체 반출/원문 검증은 미확정 |
| 3 — 기존 결과 검증 | 이전 GPU 부분관측·원자료·실행 신원·정리·최종 정지 검증, 새 writer와 운영 연결 변경분 검토 완료 | 후속 실행 archive·native·writer·factory·Ref·정리 검증. 명령 exit0만으로 제품 성공으로 올리지 않음 |
| 4 — 과거6/43ID 대조 | 기존43 index·최신 delta·외부 반환물·과거 원본 미확인 범위 인계 완료 | 새 결과가 도착한 ID의 추가분만 연결. 원자료 재검색·불변 재검 반복 없음 |
| 5 — SUP-05 CPU 준비·실행 | 공개 LLMEngine caller·고정 입력·원90초·실패 전달 구현, create 응답 불명 시 소유 CID 복구/정리 보완. 실제 실패·정리·검증 인계까지 현재 건 마감 | 정상1회 원90초 ready 미도달·wait30 초과. 요청 출력·worker final·native journal/seal 미수집. 새 구현·시험·재시도는 착수하지 않음 |
| 6 — 새 CPU 묶음 검증 | source·종료 계약·정리 11개 모의 계약·packet2/59개 참조와 실제CPU1회 실패/정리/최종index 검증 완료 | 다음 원인 분석·보존 개선의 새 고정 변경분 수신. 현재 배정 필수 검증 잔여 없음 |

## 수량과 실증의 의미

- 담당은 GPU 대상33·CPU 대상10, 합계43ID다. 담당 분류와 실제 관측 환경은 다르다.
- 기존 수량 인계는 제품 경로 근거25ID(부분·중단 포함, GPU 환경14/CPU 환경11), 기타 컴포넌트·준비진단13ID, 과거 보고만5ID다. 자료 가용성38은 실증 완료38을 뜻하지 않는다.
- 후속 SUP-16 관측은 실제 L4·TP1 모델 적재·정상 HTTP200(prompt6/completion1)과 native 파일 읽기250,500,968바이트를 확인했다. native 분석 `security=UNKNOWN`, 미확인8·worker/producer/전체 범위 한계, ENV `EXAMPLE`, engine commit `None/UNVERIFIED`는 유지한다. 제품 factory·EVD/RES/RPT가 없는 이전 실행을 formal GPU `MEASURED`나 완료 ID로 올리지 않는다.
- 새 관측 명령의 Success/0은 위 한계를 자동 해소하지 않는다. 새 원자료 검증 전에는 성공률·제품 완료 수를 갱신하지 않는다.
- 과거 GPU 보고5(`REQ-08/09/10`, `SUP-03/08`), 최초 과거6(`LIF-06/08/12/16`, `OBS-02`, `SUP-08`), 가용성의 과거 보고만5는 서로 다른 집합이다. 원본 미확인과 새 CPU/GPU 관측을 구분한다.
- 원질문 전체 확인 완료0, 적용 분기 전체 열거 false다. 분기 총수·완료 분기 수·코드 수정 총건수·미래 실행 묶음 수는 미확정이며 43ID 수와 합산하지 않는다.

## PR에 모은 구현

1. 기존 source의 미반영7개 commit: profiler stop/recovery 관측, 보호 입력 밖 준비 경로, Python3.12.14 contract pin, read-only mount 이전 data 경로 확보, PLT-06 offline Docker 준비·DRAFT 정책, 내부 network/수입 helper 고정, SUP-04 실제 모델 CPU 준비 도구.
2. `implementation-snapshots/`: SUP-16 writer, SUP-15 관측/소비, LIF-06 컴포넌트, SUP-05 caller/worker의 고정 원 바이트. 세션1의 16개 후보 전체는 index에 참조하고 host 결합·실제 설정은 로컬에 보존한다. 공개 검토용 구현 참조이며 새 제품 CLI나 실행 준비 완료 패킷이 아니다. 모델·이미지·native 자산·spec·helper·ENV/target/prepared·정확 배정은 원 고정 패킷에서 별도 결합해야 한다. 자동 실행하지 않는다.
3. `evidence-index.json`: 기존43 행을 잃지 않는 source pointer·SHA와 최신 delta 연결. project-relative 포인터의 원자료는 로컬 보존 자료이며 이 PR에 원 archive·복원DB·raw 로그를 올리지 않는다.

`reviewed-source.json`의 변경은 새 통합 tree의 실제 bytes를 기존 생성기로 다시 고정한 것이다. 과거 manifest의 승인·운영 사용 이력을 변경하거나 새 source의 실환경 승인을 자동 부여하지 않는다. core·공통 enum·판정 의미·기업 승인값·기존 기한을 완화하지 않는다.

통합 회귀에서 LIF-14 DRAFT builder의 baseline32 고정이 현재 main의 baseline33을 거부하는 호환 문제를 확인했다. 두 판의 LIF-14 질문·judge YAML은 동일하며, 정확한 `(revision, SHA)` 32/33만 받도록 보완했다. PACK 원본은 바꾸지 않고 미래34를 거부하는 회귀를 추가했다.

검증 결과는 [VALIDATION.md](VALIDATION.md), 다음 작업과 완료 조건은 [NEXT_PLAN.md](NEXT_PLAN.md)를 따른다. 원자료와 정확한 검토 source SHA는 evidence index에 연결한다.

위 표와 초기 통합 기록은 당시 관측 범위다. 이후 GB09 전체 회수·운영 마감과 새 사용자 지시에 따른 SUP-03 재개는 [시각별 후속 결과](FOLLOWUP_RESULTS.md)에 연결했다. 원 전송 timeout·stop180 실패는 보존한다. 현재 허용된 다음 작업은 검토된 고정 패킷과 자원·예산·보호·정리 조건에 따른 SUP-03 선정 분기이며, 다른 신규 묶음이나 기한·권한 확대를 이 문서로 승인하지 않는다.
