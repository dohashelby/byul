# GPU 1장 두 번째 실제 실행 — ZIP 전달 성공, 기존 Git 입력에서 설치 중단

고정 제품 source는 `b85dac21bfe380eaa7f06a7fe2b451a2e168daa2`, tree는 `d1a4d8fa7edecd6b18c3cebc348c318ade9bfc12`, reviewed-source manifest는 `2e400a46d78465144770afff4de54210cceb5ec8f58539666a11281f83e9126c`다. 같은 source의 전체 CI14/14 성공과 이 실제 실행 결과를 구분한다. 문서 추가 head는 실제 실행 source를 바꾸지 않는다.

## 실제 도달 범위

| 2026-11-09 KST | 관측 |
| --- | --- |
| 17:51:37 | 기존 노드 start-intent anchor. 뒤이은 CLI 응답에서 stopped→pending 수락 1회 확인 |
| 17:52:46~47 | running·SSM Online·계정 활성 명령 0 확인 |
| 17:53:26 | 고정 execute 1회 제출, PID/PG357·start ticks88406 |
| 17:53:32 | health 성공: L4 1장·CPU8·MemTotal31,619,244KiB·기존 GPU 프로세스0. 이번 boot의 원 2시간 보호 타이머 활성, reset 없음 |
| 17:58:02 | 소스 ZIP 2,037,510bytes 전송·SHA 검증·root 압축 해제 뒤 source-install이 Failed1로 종료 |
| 17:58:06 | 정상 stop-intent anchor |
| 17:58:10 | 원 CLI receipt의 정상 stop 수락. 응답의 상태는 running→stopping |
| 18:01:13 | 원 execute exit1·child reap·process group terminal·cleanup 오류0. 원 stop180 관측 실패와 supervisor final 부재 보존 |
| 18:07:39 | 실행 세션의 후속 EC2-only 조회에서 소유 3대 stopped·공인 IP0 확인. 정확한 off 전이 시점은 미확인 |

첫 실행에서 실패했던 source 파일명 계약은 실제로 통과했다. 이번 실패는 원 installer `2a4d24575f964dce9f3d2728491f4a6fabb5c0e6650d1cd8e5dbff2866b7c14e`의 `guarded(original / '.git', True)`에서 발생한 `SOURCE_PATH_GROUP_OTHER_WRITABLE`이다. 해당 CID는 `571c3c04-e296-4958-a571-68758e00757f`이며, 실행 0.055초·stdout 없음으로 기록됐다.

오류는 실제 path·종류·숫자 mode를 출력하지 않았다. 특정 mode나 `.git`의 현재 파일 종류를 실패 당시 실측값으로 단정하지 않는다. 과거 생성 코드에는 retained 경로를 `git worktree add --detach`로 만든 기록이 있고, 기존 source-owner 검증기는 gitfile을 허용한다. 신규 installer가 `.git`을 디렉터리로만 요구한 계약 차이는 후속 수정 대상으로 분리했다.

Git clone·bundle fetch·새 source checkout·offline uv·새 runtime·모델 실행·Ready·REQ 제품 수집·SUP16 역할 관측에는 도달하지 않았다. EVD/RES/RPT 생성·반출·복원·저장 팩 재판정도 이번 실행의 수행 결과가 아니다. 호스트 health와 전송 성공을 제품 PASS로, 설치 실패를 제품 보안 FAIL로 집계하지 않는다.

## 증거와 후속 검증

초기 기동·실행·health 원본 ZIP은 16,089bytes·14members, SHA-256 `d7a4db37351283a81da6a2f2dd857d4ddf3dcf7e53f099b647223bebb9c849b8`이다. 로컬 byte hash·CRC·member 목록을 대조했다. 독립 초기 검토는 100개 대조 불일치0이며, 최종 검토 SHA는 `ecd9b9f26c66e5f12116294254e641ac4aeccd372a1506dd27256d8bf0e9a95c`다. 이전 boot·PID·CID와 구분한다.

최종 control 원자료는 조각별 전송 후 같은 bytes로 재결합해 회수했다. ZIP은 13,507,647bytes·2,507members(원자료2,506개와 manifest), SHA-256 `13b5d73224724b4ed4174be446ed4efbad302c9c04ac649be3cd1417128b8cdf`다. 로컬 전체 hash·CRC·각 원자료의 길이/SHA·목록 완전성을 확인했으며, 중복·누락·불일치0이다. 원 control 창은 17:51:35~18:06:05이고, start1·SSM190·stop1의 원 수락 기록을 포함한다. ZIP 전송과 압축 해제 성공, source-install 원 실패 및 execute357의 종료도 원 JSON과 대조했다. nested archive의 과거 검증은 반복하지 않았다.

18:07:39의 최종 EC2-only 원 JSON은 위 control 창 뒤의 별도 자료이며 SHA-256은 `c80ef7742484ea5c351b1934995c0b64ee417be5544c9396a4dc5ab91f3a6b89`다. 해당 응답의 소유 3대 상태와 공인 IP 부재를 로컬에서 직접 대조했다. 현재 실행 작업은0이다. 누적 보수 장부 추정은 이 시각 기준 **$73.87834874028293**이며, 기동 전 장부에 원 start-intent부터 후속 stopped 관측까지 compute·IP 비용과 EBS 저장비를 더한 독립 계산도 일치한다. 확정 청구가 아니며, compute/IP 구간은 닫혔지만 EBS440GiB의 $0.055748/h 저장 비용은 계속된다.

최종 독립 검토는 source 실패·원 실행 종료·stop180 실패·후속 stopped·비용의 114개 대조 불일치0으로 완료됐다. 검토 SHA-256은 `7b78700235d84592285c06433f1ca53c0a3cd354191e77b4a0cafd7788d5ea24`다. 이전 integrity 293개 대조는 재사용했으며, 원 closeout 작성 당시의 `pending=true`를 과거 원본에서 수정하지 않고 이 후속 완료와 연결한다.

후속은 기존 cache를 변경하지 않고, 기존 source-owner가 허용한 Git 디렉터리·gitfile 입력을 구분해 새 보호 복사본에 연결하는 최소 수정이다. 새 실행 root·정확 commit/tree·2,160개 파일 핀·manifest·offline uv·실제 import origin 검사는 유지한다. 이미 전달한 ZIP·bundle·pins는 현재 FD·SHA를 다시 확인해 재사용하며, 같은 입력을 다시 전송하거나 같은 실패 조건으로 재기동하는 것을 기본 절차로 삼지 않는다.

원 43개 상태·FAIL/UNKNOWN·Ref는 변경하지 않았다. REQ-08·09·10 실제 요청 대응과 SUP16 역할별 실측은 계속 남아 있다.
