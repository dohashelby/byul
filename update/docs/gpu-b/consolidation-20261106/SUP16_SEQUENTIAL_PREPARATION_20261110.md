# SUP-16 순차 역할 준비와 남은 실제 연결

2026-11-10 마감 기준으로 이번 선정 GPU 실행에서 SUP-16 실제 관측은 없다. normal-before → challenge-partial → normal-recovered 순차 component 준비와 실제 실행을 구분한다. 삭제한 Pod 재생이나 미래 target/prepared 신원 발급으로 전체 pair를 채우지 않는다.

## 이번 준비

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
3. 원 phase 감독기와 실제 PG 종료·reap·terminal proof. 특히 outer caller의 한 cleanup guard 안에 preserve/quiesce/archive를 합친 상태로는 원 각300/360/300초 조건을 검증했다고 할 수 없다. action별 enter/exit/terminal 및 cleanup state 연결이 남는다.
4. 독립 aggregate의 실제 CONFIG/root/health/system Python proof와 별도 producer 호출. 생성일반식이나 마지막 role 결과 복사로 채우지 않는다.
5. 기존1GPU·TP1 자원 점유 부재, 원 비용/보호 기한과 단계별 잔여, 역할 전환마다 원자료 반출·정확 UID/RV 정리·NotFound·GPU 프로세스0 확인.

기존 normal Ready/직접health는 Pod600초와 원phase잔여를 사용한다. 원asset90·capture120·health10/request15·총보호타이머를 늘리거나 새로 시작하지 않는다. 부분파일 challenge에는 정상 Ready를 양성 전제로 강제하지 않으며 원 오류/부분 기록을 보존한다.

## 담당과 인계

세션2가 실제 envelope·phase감독기·자원·비용·AWS 운영을 단독 담당한다. 세션1의 outer와 세션5의 CONFIG, 총괄의 aggregate는 위 고정 준비 자산으로 인계하고, 세션6이 새 변경과 실제 증거를 구분해 검토한다. 준비 원자료는 로컬 운영 인계의 exact SHA 자산이며 개인 경로·원 비밀 설정은 PR에 게시하지 않는다. 실제 역할 실행·full pair·새 EVD/RES/RPT가 없는 상태를 유지한다.

전체 supply_boot paired factory는 같은 호출에서 두 live target/prepared를 요구하는 별도 잔여다. 순차 component를 완료해도 전체 pair PASS가 되지 않는다. 이 조건을 TP2와 자동으로 동일시하지 않는다. 기존 TP2 rank/shard/장애복구 범위와 과거 Mac 원자료5개 확보도 별도 잔여다.

세션2의 미결합 준비 마감 기록은 `S2-SUP16-unbound-preparation-closeout-v1.json`/SHA `d12c9f166d81fea3c067b9c1f12a2fa51eb60c71f55e2b5e0b9721a6c4598f6f`에 고정했다. 새 phase 감독기 delta의 실제 코드는 아직 작성하지 않았으며 새 GPU 기동·실측·미래 proof 발급은 없다. 이 잔여를 완료 처리하지 않는다.
