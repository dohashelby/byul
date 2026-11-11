# SUP-16 후속 및 B04 실행 요구 — 2026-11-11

입력 정의 대조는 `88b336ed`의 기록이고 운영 상태는 root의 S3 회수·정리와 B pre-seal 검토까지 갱신했다. [실제 관측 정본](../SUP16_ACTUAL_OBSERVATIONS_20261111.md)이 우선한다.

마감 요청에 따라 새 GPU·추가 실측은 배정하지 않는다. 기존 선정 인스턴스는 11월 11일 11:40 KST stopped·IP 없음·SSM0·활성 명령0 확인을 마감했다. 다른2대의 새 상태를 이 선정 조회로 추정하지 않는다. 아래 절차는 향후 재개 시 참조이며 현재 실행 지시가 아니다.

## SUP-16: 원자료 회수와 새 모델 실증을 분리

### 1. 원 GB12 회수 완료와 향후 전송 참조

- 원 ZIP 14,636,464B / 1,971 members를 실제 Session으로 모두 회수했다. 로컬 SHA와 최초 전체 CRC·rawmanifest1970을 대조했고 손상·위험경로·중복0이다.
- 기대 SHA: `2681a23c1545647e4b25711cb4c570336efdc6e7a604332de452c9cc2a663219`.
- 원 실험 boot와 회수용 boot는 다르다. 회수에 성공해도 원 실험을 성공으로 바꾸지 않는다.
- 원 부분 파일·GET ConnectTimeout·읽기 수락 UNKNOWN1·최초 오류는 별도 보존한다.
- R1의 실제 압축 회수는 기존 3MiB 상한으로 실패했다. 같은 encode/GET/decode 묶음을 그대로 반복하지 않는다.
- 실제 성공한 전송은 [Session Manager 스트림 v8](../retained-stream-components-20261111/README.md)의 core ab804/entry cf67/stream8e 결합이다. exact Session Terminated/remote0/native0 뒤1220 frame의 전체 bytes/SHA가 일치했다. 원180초 정지 감시 실패와 후속 실제 정지를 분리 보존한다.
- 기존 source-only packet은 `not_ready=true`, `execution_ready=false`다. 값을 뒤집기만 해서 실행하지 않는다.

| 순서 | 실제 실행 전에/실행 중 확보할 자료 | 종료·실패 처리 |
|---|---|---|
| 선정 | S2가 현재 소유 인스턴스·활성 actor/command·자원 상태와 원 보호창·정리 경로를 확인 | 과거 stopped 기록을 현재 running/접근 가능의 근거로 쓰지 않음 |
| 운영 packet | 새 collector UUID, 새 private 경로·CLI journal, remote_health_output, 정확 source/packet SHA, 현재 기한·기존 비용 범위 | 현재 source-only packet은 운영 승인이 아님. 원 기한·비용 범위를 임의 확대하지 않음 |
| health | 새 boot, 정확 CID/대상, Success/0 및 원 stdout 두 행·health binding을 확인 | 원 실험 boot로 잘못 귀속하지 않음. health 성공을 모델 성공으로 세지 않음 |
| 스트림 준비 | 검토된 진입점이 동일 `runtime.packet` 객체를 원 생성자에 전달하는지와 실제 관리 문서/plugin·권한·RunAs·host/guest clock 결합 | 원 S1의 `SAME_FIXED_DRIVER_PACKET_REQUIRED`는 당시 실패로 보존. v7 CPU를 실제 전송 성공으로 대체하지 않음 |
| 전송 | 최초 인증 전에 만든 원 absolute 900초·수락 관측65초, 실제 guest boot·새 UUID·동일 FD로 source 신원을 결합 | 원 기한을 다시 시작하거나 상한을 늘리지 않음. 수락 UNKNOWN이면 자동 재전송하지 않음 |
| 수신·게시 | 12,000B H/D/F frame·순서·총량·SHA, footer와 EOF 뒤 로컬 FD 재해시·배타 게시 | 로컬 CLI/원격 종료 marker/정확 server session 종료를 확인한 뒤 finish. 기존 partial/원 ZIP은 덮어쓰지 않음 |
| 원본 검증 | 복원 ZIP의 기대 길이·SHA, 최초 전체 CRC, 1,971 members와 원 파일 목록·신원 대조 | 이전 부분 entry CRC를 전체 CRC로 대체하지 않음 |
| 의미 검토 | 원 argv·READY 소비·exec·sitecustomize·install 반환·collector 산출물·Refs를 대조 | 기록 부재 원인이 확인되지 않으면 정확 미확인 지점 유지 |
| 정리 | actor/PG·SSM 종료, 기존 승인된 정상 정지 경로, actual stopped·IP·비용 구간 확인 | wrapper outcome만으로 전체 정리·실청구 확정 주장 금지 |

원 GB12 회수는 완료했다. 다음 case는 기존 driver의 과거 ConnectTimeout GET 경로를 자동 재사용하지 않고, 새 case positive archive 반환의 실제 파일명/bytes/SHA·CID/target/nonce/boot를 성공한 Session 경계에 결합해야 한다. 이 신규 수집 binding은 별도 로컬 준비이며 미완료 상태다. 실제 archive descriptor는 향후 whole/export 후에만 발급할 runtime 값이다. 현재 새 실측·staging·GPU 기동은 하지 않는다.
컨트롤러가 원 uncertainty barrier에 걸리면 실제 오류·원 intent·대상·actor 종료를 인계한다.
UNKNOWN을 삭제하거나 별도 우회 실행기를 만들지 않는다. 기존 승인된 정리 권한과 원 barrier는 서로 구분한다.

### 2. challenge 기록 부재와 B 관측 준비

R8의 `SUP16_JOIN_ONE_ACTUAL_CANDIDATE_REQUIRED`는 결과다. 원인은 아직 확정되지 않았다.
challenge만 후보0·기록 파일0·collection gap·sealfalse이고 정상 전후는 후보1·기록3·sealtrue다. 정상 전후 PID98 journal도 END=BOUND_REACHED·root_operation_pairs_complete=false이므로 제품 완료가 아니다. root client timeout/-9와 빈 engine 로그를 각각 보존한다.
후속 원문 대조에서 Observer는 writer=None으로 시작하고 첫 사건에서 Writer를 만든다.
따라서 install 미실행·대상 사건 미도달·Writer 생성/쓰기 실패를 구분한다. 파일0만으로 sitecustomize 실패를 단정하지 않는다.

| 관측 지점 | 확인할 실제 근거 | 피해야 할 추정 |
|---|---|---|
| launcher | 실제 PID/start·원 argv, READY 소비 시각·exec 전후 신원과 종료 상태 | manifest/ConfigMap 일치만으로 실제 실행 확인 |
| Python 초기화 | 실제 interpreter·격리 flags, sitecustomize 로드 여부·파일 origin | 빈 stderr를 정상 import로 해석 |
| install | 원 native install 호출·반환/예외의 기록, session/output 결합 | installer의 published=true를 엔진 install 성공으로 해석 |
| 첫 정상 요청 | API→내부 request/attempt→worker 소비의 원 연결 | HTTP200이나 model alias만으로 인과 결합 |
| 기록 종료 | 실제 writer 경로·권한·첫/마지막 seq, seal·기한·원 자식 종료 | timeout을 없애거나 argv를 추측해 바꿔 통과 |

전체 원자료·원2cb를 대조한 [인과·A 반환](../retained-preseal-components-20261111/causal_handoff.md)을 고정했다. [B pre-seal 관측](../retained-preseal-components-20261111/README.md)은 같은 reader120의 snapshot→seal 사이 기존 inventory 원반환·오류를 보존하는 준비다. CPU4·코드핀7·metadata4와 최종 한정 검토를 통과했으며 실제 새 관측값을 과거 R8에 채우지 않는다. 실제 install/첫 emit/Writer 오류는 A 반환이 필요하다.
A 소유 callback 수정이 필요하다고 확인되면 원 오류·고정 source SHA·최소 변경 지점을 반환한다.
향후 새 정상→부분 파일→복구 실증은 위 경계를 고친 고정 변경·정상 대조·현재 실행 입력을 갖춰 재개할 범위다. 현재 마감 범위에서는 배정하지 않는다.
부분 파일 도전은 원본 대신 소유 사본을 사용하고, 각 역할의 request/attempt·boot·generation·output을 독립 기록한다.
최종 종료는 EVD/RES/RPT 반출·정확 Ref 복원·저장 팩 재판정·자원/비용 정리까지다.

## B04: 실제 반환이 필요한 TP2 입력

기존 9개 제어 계획의 파일/ID/역할 점검은 재실행하지 않았다. 현재 `INCOMPLETE`를 보존한다.
native24의 TP1 mp-worker/RPC 지원을 코드 부재로 표현하지 않는다. TP2 peer/rank 제어와 공통 생산자 반환은 별도다.

| 점검·case | 정상 대조 | 장애 입력·확인 | 복구 확인 | 반환 담당 |
|---|---|---|---|---|
| LIF-08 exception | rank0/1 worker 신원·canary | 선택 rank 원 함수 예외 적용 ACK, detected/client_delivered 원 사건 | fault 해제 ACK·새 정상 canary | A: TP2 rank-fault 생산자/제어. B: case 결합. S2: 실제 배정 |
| LIF-08 death | 동일 정상 대조 | 정확 PID/start·rank에 대한 단일 종료와 상위 전파 | 재기동 세대·rank roster·canary | 동일 |
| LIF-08 delay | 동일 정상 대조 | 고정 hold와 실제 시작/해제 시각; 원 native 최대값 유지 | 해제 ACK·요청 결과·잔류 actor0 | 동일 |
| LIF-08 communication-stall | 실제 통신 경로·peer 정상 | 실제 peer/통신 정지 지점·ACK. SIGSTOP과 통신 장애를 동일시하지 않음 | 통신 해제·양 rank 정상 | A: 지원 제어/peer·수집 계약 |
| LIF-11 runtime-failure | group별 PID/IPC/shm/NCCL/device census | 실제 trigger·실패 전후 소유 자원, 정책 기한 기준 | 해제된 자원 ID·새 generation 정상, 소유 잔류0 | A: failed-group record. B: 소비. 정책 권한자: 기한 |
| LIF-11 partial-initialization | 완전 기동 대조 | rank 일부 초기화 실패의 실제 지점·이미 확보한 자원 | 부분 자원 정리·새 전체 기동 대조 | 동일 |
| SUP-06 missing-selected-shard | rank별 required/assigned/actual loaded | 도전용 사본에서 선택 shard 누락·해당 rank 준비 거부 | 원 고정 사본의 새 세대 정상 적재 | A: 배정/적재 생산자. S2: 모델 실체. B: 역할별 target/prepared |

### 반환 필드와 수락 조건

- A: 커밋·파일 SHA·schema·투영 함수·fixture·지원 밖 범위, 실제 rank0/1 의미와 peer/trigger ACK.
- S2: boot·Pod/container·worker PID/start·rank roster, 모델 revision/inventory와 shard별 required/assigned, 실제 부모/phase 기한·정리 예약.
- 정책 권한자: LIF-08 감지·통지 기한, LIF-11 회수/재기동 기한. `response_ms`로 임의 대체하지 않는다.
- B: 정상/장애/복구 각각의 target·prepared·정책·세대·원 사건 Ref를 결합하고 혼입을 검사한다.
- J01: A02 `/4`·A03 TP2 생산자·A04 PACK34, B04 실입력과 CI·할당량·비용 조건을 확인한다.
- 기존 LIF-08/11/SUP-06의 900/600/900초는 계획 슬롯이다. 개별 수집120초·native/HTTP 제한·정리 여유와 별개이며 실시간 보장을 뜻하지 않는다.

## 우선 실행 순서

1. GPU 없이 B03의 실제 소유 입력을 채우고 `/1~ /3` 범위 내 B02 연결을 유지한다.
2. SUP-16은 회수 작업과 새 제품 실측을 분리해 S2가 단독 운영한다. 다른 GPU 시험과 중복 기동하지 않는다.
3. A 반환이 필요한 `/4`·REQ-12 계약·TP2는 해당 분기만 보류한다.
4. PACK34 정본 실측과 0.32 비교는 현재 팩33/과거 Ref를 덮어쓰지 않고 별도 PLAN으로 실행한다.
