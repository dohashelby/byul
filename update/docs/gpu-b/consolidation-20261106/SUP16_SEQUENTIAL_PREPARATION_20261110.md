# SUP-16 순차 역할 준비와 남은 실제 연결

2026-11-10 마감 기준으로 이번 선정 GPU 실행에서 SUP-16 실제 관측은 없다. normal-before → challenge-partial → normal-recovered 순차 component 준비와 실제 실행을 구분한다. 삭제한 Pod 재생이나 미래 target/prepared 신원 발급으로 전체 pair를 채우지 않는다.

## 최신 R1 전송 실패·정리

[R1 기록](SUP16_R1_TRANSPORT_CLOSEOUT_20261110.md): 원 PUT helper의 `.json` 형식 거부 ValueError와 상위 upload AssertionError를 보존했다. 원격 install/health/whole/model·역할 실행0·제품 Ref0, 원stop180 관측실패/final부재 뒤 별도15:36 EC2-only3stopped/IP0으로 후속 정리했다. S6 최종88c43의44개·CLI138개 불일치0, 기존 최초무결성f081 재사용으로 필수검증 완료이며 새CRC 반복0이다. 현재GPU 중지는 해당 후속 조회 시각의 관측이고 정확 power-off 시각은null이다.

다음은 S5 data-only ZIP/installer 최소delta 한정CPU 준비·S6 변경검토 뒤 원조건 재확인이다. 기존 역할/fullpair 미완료·원보호/개별기한·원실패·13:58 준비검토 이력은 유지하며 새 role/GPU/제품 성공으로 상향하지 않는다.

## R1 이전 13:58 준비·후속 개발/검증 이력

이전의 한 cleanup guard를 action별 원 기한과 종료 검증에 연결했다. 검증된 일반 구성요소는 아래 B 전용 도구로 게시했다. 실행할 때는 정확히 고정한 원 controller/outer/adapter 자산과 실제 입력이 별도로 필요하며, 아래 다섯 파일만으로 AWS 자원이나 실제 proof가 준비되는 것은 아니다.

| 게시 코드 | 검증한 새 범위 |
| --- | --- |
| [phase supervisor](../../../tools/gpu_b_sup16_phase_supervisor.py) | 원 preserve300/quiesce360/archive300 기한, 원 전체 기한 clip, 실제 Linux CLI·자식 회수/reap/terminal. v1의12개와 수정 v2의 새4개를 구분 |
| [action 연결](../../../tools/gpu_b_sup16_actionwise.py) | 각 action의 실제 cleanup_context·mode·deadline·guard/terminal 접점. 기존9개와 완료 receipt 읽기 실패 시 후속 변경 차단 새1개 |
| [독립 aggregate](../../../tools/gpu_b_sup16_aggregate_archive.py) | 원 preflight의 observed.version/flags 자료형, 현재 시스템 Python·원 binary·flags 대조의 새3경계. 축약 CPU fixture를 실제 증명으로 승격하지 않음 |
| [CONFIG 조립](../../../tools/gpu_b_sup16_config_assembly.py) | renderer는 별도 UID1000 출력 형제, bridge owner0, aggregate source_runtime_check. 고정25파일 조립과 실제 UID1000 렌더 경계1개·원CLI3개 |

| [전체 실행 진입점](../../../tools/gpu_b_sup16_whole_entry.py) | 고정14모듈·동일 역할/감독기 연결, 현재 proof 누락/부재/잘못된 SHA의 실행 전 거부, 읽기 전용 replay·명시 실행 인자의 새CPU7개. 현재 GPU proof 양성 대역은 사용하지 않음 |

전체 진입점은 `FrozenCaller(request)`와 기본 inspect·읽기 전용 replay·명시 execute를 제공한다. 전용41파일 설치 계획은 로컬에서 고정했으며 원격 설치하지 않았다. 실제 실행에는 원 transport/start-stop driver admission, 현재 같은 CONFIG의 health/preflight·세 역할 model proof와 원 active timer·실제 신원이 필요하다. 필수 proof를 생성하거나 옛 세대 자료로 대체하지 않는다. 독립 검토 `c2f35ce9…`의 소스 연결23개와 `67b950a9…` 무결성153개 대조에서 불일치0으로 동일 bytes 게시가 적합함을 확인했다. CPU 부모 환경·exit·시간은 인계 보고이고 별도 부모 launch raw는 없으며, 자식 argv/exit/stderr는 원 ZIP에 직접 보존됐다. 정확 검토 SHA는 아래 JSON에 연결했다.

별도 실제 CLI 결합3개에서 context/action 순서, quiesce exit7의 첫 예외 보존·terminal 확인 뒤 archive, FD 미종료의 다음 archive 차단을 확인했다. 이 결합은 supervisor6c958/action9cff 기준이며 action8439의 새 receipt 읽기 오류 경계는 별도1개로 검증했다. 원 Kube/currentproof cleanup_context를 CPU에서 위조하거나 실행했다고 하지 않는다.

새 독립 검토는 감독기 v2 `eecae451…`, CLI 결합 `8ff8c5fd…`, action/aggregate 수정 `edc3f0e0…`, CONFIG·렌더 `d3677f01…`이며 각 고정 범위에서 불일치0이다. [전체 파일 SHA·검증 범위·잔여 JSON](sup16-operating-preparation-20261110.json)에 정확 참조를 연결했다. source-free controller 원본·실 CONFIG·개인 경로·secret·CPU 원자료는 공개하지 않았다. 이전 고정본·실패·제한은 보존한다.

이전 브라우저/컴퓨터 자동화 연결 장애는 세션2의 새 연결로 해소됐다. 2026-11-10 13:58:26~33 KST 조회에서 소유3대 stopped·IP/EIP0·활성SSM0, EBS440GiB를 확인했다. 해당 원 조회 SHA는 `42a18713a7c95cac9cdca469fcb39b0df8055b8899a59a3b100403cca616a83a`다. 장부 기반 보수 추정 $77.03839263213317과 다음 실행/정리 포함 $83.24839263213316은 기존 $95 실행 한도 안이며 확정 청구액은 아니다.

설치기의 FIFO 입력 대기 결함은 수정판 SHA `7311f63618c8b6938c3ac8d07a39cc15b28fcab098e6f23b59a3a33fdc7766a4`로 수정했고 세션6 검토 `9070fc334f4002e37cf9af2112ea9e733ddf23d659ac341384ab08dc0099e826`가 해당 변경16개 대조에서 불일치0을 확인했다. 새 운영 드라이버와 현재 proof 연결은 진행 중이며 원격 설치·기동·현재 역할 실측 완료를 뜻하지 않는다. 실제 staging·현재 boot/모델/역할 proof가 아직 검증되지 않아 **NOT_RUNTIME_READY**를 유지한다. 기존 보호/단계 기한·예산은 늘리지 않았고 full pair는 별도다.

## 이전 준비 기록

아래는 후속 수정 전 고정 준비를 보존한 기록이다. 특히 단일 cleanup guard·기존 렌더 출력·과거 aggregate의 자료형 한계는 위 새 구성요소에서 수정한 범위와 구분한다.



| 담당 | 자산 | 이번 고유 CPU 검증 | 상태 |
| --- | --- | --- | --- |
| 세션1 | outer lifecycle caller v2, SHA84df302b544dd14fca67096ea0dd1992523cc7362842f2c8c80411380847e5f8,21611B | 8개·exit0·부모1.156초 | frame 반환 후 Ready 결합 실패 정리·guard 진입 오류 뒤 terminal 확인·불확실 후속 변경 차단 |
| 세션5 | pure CONFIG assembler v1, SHA6a205b1ab03ae487738668ab1307a3984ee5b0570729b9d3d38f84490399c67c,13942B | 6개·exit0·실행0.017초/부모0.238099초 | 고정 bytes·자산핀·role/alias를 실제 d094 canonical과84df constructor에 결합, 운영 callback 호출 없음 |
| 총괄 | 독립 aggregate archive callback v1, SHAa62909e97391c49c342a58e148d6b79b1d75b79431516c1454e797a456dbe262,6014B | 7개·exit0·내부0.134779초/부모1.015초 | 원028 archive·읽기 함수 재사용, 기존health표현1곳만새HEALTH로결합, 별도root/config/proof와callback |

세 수치는 서로 다른 준비 코드의 CPU 경계 수이며 체크 ID 완료21개 또는 실제 GPU 시험21개가 아니다. 예제 proof·실행환경 doubles는 EXAMPLE이다. 기존 CPU·모델 시험이나 과거 archive 전체 CRC를 반복하지 않았다.

aggregate 준비는 원FD/owner/nlink·root700·512MiB·secret settings 제외·새exclusive archive600/owner1000·SHA receipt를 유지한다. root0 operator/CODE/health와 UID1000 runtime을 구분하고 마지막 role의 context/state는 받지 않는다. 원메서드에 별도 testzip은 없으며 CPU 예제의 ZIP readback CRC와 실제 반출 integrity를 구분한다. callback 자체가 실행 기한이나 실제 긍정 proof를 생성하지 않는다.

## 실제 실행 전 세션2가 결합할 입력

1. 현재 source2cb/V4·같은 실행 boot/model/정상 대조를 증명하는 root600 파일 pointer와 정확 SHA, 실제 CONFIG와 준비 입력. 종료한 REQ 세대 proof를 다음 세대의 현재 자료로 사용하지 않는다.
2. 세 역할의 실제 CONFIG/frame·code/runtime/root·서로 다른 archive alias, 원asset/prefix-copy 핀. 현재 UID·container·PID는 실제 생성 후 측정한다.
3. 로컬 action별 원기한·enter/exit/terminal·cleanup state의 연결은 완료했다. 원격의 실제 CLI·PG 종료/reap·terminal과 cleanup proof를 수집해 검증해야 하며, 이전 한 cleanup guard만의 기록으로 이를 대신하지 않는다.
4. 독립 aggregate의 실제 CONFIG/root/health/system Python proof와 별도 producer 호출. 생성일반식이나 마지막 role 결과 복사로 채우지 않는다.
5. 기존1GPU·TP1 자원 점유 부재, 원 비용/보호 기한과 단계별 잔여, 역할 전환마다 원자료 반출·정확 UID/RV 정리·NotFound·GPU 프로세스0 확인.

기존 normal Ready/직접health는 Pod600초와 원phase잔여를 사용한다. 원asset90·capture120·health10/request15·총보호타이머를 늘리거나 새로 시작하지 않는다. 부분파일 challenge에는 정상 Ready를 양성 전제로 강제하지 않으며 원 오류/부분 기록을 보존한다.

## 담당과 인계

세션2가 실제 envelope·phase감독기·자원·비용·AWS 운영을 단독 담당한다. 세션1의 outer와 세션5의 CONFIG, 총괄의 aggregate는 위 고정 준비 자산으로 인계하고, 세션6이 새 변경과 실제 증거를 구분해 검토한다. 준비 원자료는 로컬 운영 인계의 exact SHA 자산이며 개인 경로·원 비밀 설정은 PR에 게시하지 않는다. 실제 역할 실행·full pair·새 EVD/RES/RPT가 없는 상태를 유지한다.

전체 supply_boot paired factory는 같은 호출에서 두 live target/prepared를 요구하는 별도 잔여다. 순차 component를 완료해도 전체 pair PASS가 되지 않는다. 이 조건을 TP2와 자동으로 동일시하지 않는다. 기존 TP2 rank/shard/장애복구 범위와 과거 Mac 원자료5개 확보도 별도 잔여다.

세션2의 미결합 준비 마감 기록은 `S2-SUP16-unbound-preparation-closeout-v1.json`/SHA `d12c9f166d81fea3c067b9c1f12a2fa51eb60c71f55e2b5e0b9721a6c4598f6f`에 고정했다. 이는 당시 미구현 상태의 역사 기록이며, phase 감독기·action·CONFIG·전체 진입점의 후속 구현은 위에 구분했다. 새 GPU 기동·실측·미래 proof 발급은 없고 실제 운영 잔여를 완료 처리하지 않는다.
