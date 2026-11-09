# B 역할: CPU에서 끝낸 업무와 실제 연결 잔여

기존 시험을 다시 실행하지 않고 세부 업무의 원 완료 조건과 증거를 대조했다. 기준 source7adc와 원 JSON SHA e650을 보존한 [게시용 대조 자료](cpu-subtask-closure-mapping-20261109-v2.publish.json)에10개 근거 묶음의 정확 Ref·source·시각·검증 대역·한계·담당을 연결했다.10은 완료 점검 ID 수나 전체 진척도 분모가 아니다.

| 업무 | CPU에서 완료한 세부 범위 | 계속 남는 범위 |
| --- | --- | --- |
| B1·B2 | 고정 입력의 B 소비자·기존 판정 연결, 독립 FAIL/UNKNOWN·artifact 보존, 대표 factory/Store/judge/RPT CPU3분기, REQ offline 준비 | 새 A 실제 반환의 연결, REQ12 선언/기본 profile·미등록/기능 경계, 실제 native 요청·worker·시간 결합 |
| B3 | 실제 CPU API/파일 writer 한정 관측, boot/paired I/O·artifact 보존, SUP16 역할별 준비 | 현재 실제 worker·역할·입력/정책 연결. full pair에는 한 호출의 두 actual live target·자원 및 지원 구성 필요 |
| B4 | TP2 계획/입력 생성, opt-in 로컬 JSON·실제SHA·scope 검사34사례(기존13+신규21) | A6 하네스와 실제 topology/rank/shard 연결·실제 TP2 runtime |
| B5 | impact77개(33/38/6)와 결정6-1에 대한 검토 의견 | A5 typed 반영본·사용자 확정·새 팩 reviewed/공식 판정 |

CPU 완료 조건에 새 공식 판정이나 모든 미측정 분기의 전수 실험을 덧붙이지 않았다. 위 범위를 B1~B5 전체 완료로 올리지도 않았다. 새 A1~A3 CPU 부분 반환은 존재하므로 과거 미커밋 대기를 현재 상태로 복사하지 않는다.

실제 연결 잔여, GPU1 잔여, TP2 잔여는 서로 중복될 수 있어 합산하지 않는다.

- **실제 연결:** 새 A 반환·계약/profile·B 소비자 연결, full pair의 두 live target, A4/A5 반영 등.
- **GPU1:** 선정 REQ08/09/10의 native 수집·제품 실행과 SUP16의 순차 normal/partial/recovered component 실제 관측.
- **TP2:** LIF08 지정 rank 장애·감지/복구, LIF11 실패그룹·후속세대/자원 반환, SUP06 실제 worker/rank shard.

SUP16 full pair를 단일 GPU 순차 실행의 완료 가능 범위에 포함하거나 TP2로 자동 분류하지 않는다. 과거5개 원자료(LIF08/12/16, OBS02, SUP08)는 사용자 답변 **미확인**을 유지한다.

원43행의36 CPU_DEVELOPMENT_REMAINING·기업 입력 대기1·DONE2(SUP04/PLT09)·측정 잔여4는 해당 원표의 보존 상태다. **현재 남은 코드 작업36건을 뜻하지 않는다.** 한정25 마감 제안을25 일괄 DONE으로 바꾸지 않았고 LAB DONE2를 전체 진행률로 제시하지 않았다. 전체 세부 업무·분기·코드 수정·실행 묶음 총수는 미확정이다.

추가로 확인한 [실제 정상 응답](ACTUAL_GPU_NORMAL_AND_REQ_PREPARE_20261109.md)과 [ID/준비 경계 수정](NATIVE_ID_AND_PRIVATE_PREPARATION_FIX_20261109.md)은 위 기존 대조와 별도 시점이다. 신규 CPU32 및 통합 회귀32는 같은 사례의 다른 source 문맥이며64개로 합산하지 않는다. 실제 GPU source와 PR509파일 통합 source, EXAMPLE·공식 여부·null·FAIL/UNKNOWN·정확 Ref를 계속 구분한다.

공개 자료는 개인 경로39곳을 명시적 alias로 치환했다. [치환 내역](publication-path-alias-changes-v2.json)과 [S4 설명](cpu-subtask-closure-mapping-20261109-v2.publish.md)을 연결하며 원 개인 경로 장부는 게시하지 않는다. 새 행정 대조를 위해 시험·원자료 복원·AWS 실행을 반복하지 않았다.

## 후속 실제 관측

[V13 선정 REQ 실제 결과](ACTUAL_GPU_SELECTED_REQ_20261109.md)는 CPU 완료 목록과 구분한다. EVD3·RES3·공유RPT1 및 독립 복원·재판정이 연결됐지만, EXAMPLE/DRAFT·FAIL·UNKNOWN과 원43개 상태는 유지한다. [SUP-16 신규 CPU 준비와 실제 잔여](SUP16_SEQUENTIAL_PREPARATION_20261110.md)도 실제 실측 완료 수로 합산하지 않는다.
