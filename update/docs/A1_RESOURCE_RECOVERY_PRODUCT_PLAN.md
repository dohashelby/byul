# LIF11 실패 그룹 자원 회수·후속 기동 제품 묶음 초안

공동 c9af729·PR125 확정9e969299cc0f4b6dfbf707d161cd75e3539117a7과 기존 미커밋/worktree·sources/·A2/B 원본·과거팩/Ref·실증을 보존한다. 부모 A39/56/4와 A1 19/27/2를 유지한다. 이전 LIF09의 한 선언 단계 관측·Windows174/Linux175·설치217/CLI4/24Ref는 당시 제품 바이트의 CPU/EXAMPLE 근거이고 실제 배포04f7856 wheel과 다르다.

대상은 원팩31 LIF11의 **SC18-02 실행 중 실패**와 **SC17-01 초기화 부분 실패** 두 분기다. 정상은 정상 종료 후 TP process/통신 자원을 회수하고 같은 시험 환경에서 다시 기동·정상 요청을 확인하는 것이다. 실패 전후 PID/rank·queue·IPC/socket·shared memory·GPU·잔존 참조의 선정 목록·소유자·반환 시각을 대조한다. 정한 회수 기한 위반 또는 잔존 때문에 후속 기동/정상 요청이 실패하면 FAIL이고, 목록/소유자/기한/후속 기동이 없으면 UNKNOWN이다. Ray/PP는 실제 선택 시 별도 범위를 유지한다.

이미 고정한 MP source11a1554와 PR124의 rank/failure 관측, PR125의 준비/상위 집계·기존 native8 모델/자원 사실, LIF10 종료·REQ12 요청시도·OBS02 상태·공통 Kubernetes process/IPC 수집을 먼저 재사용한다. A2의 IPC 전용 probe/consumer 원본은 변경하지 않는다. 원팩의 두 분기·정상·FAIL/UNKNOWN·다른76 정의 bytes를 유지하며 기존 CLI/명시 DRAFT팩/registry→EVD/RES/RPT·verify/export/restore·저장팩 정확Ref를 실제 소비자로 삼는다.

검토한 고정 MP `_ensure_worker_termination`은 먼저 종료를 기다리고 남은 process에 terminate/kill을 호출한다. 마지막 kill 뒤에는 이 함수 안에 최종 join/종료 census가 없다. `shutdown`은 death_writer와 response/broadcast queue를 닫지만 반환 그 자체는 실제 process·IPC·GPU 회수나 다음 기동 증명이 아니다. 예외 원문·통신 handle/주소/IPC 메시지를 저장하거나 센서가 poll/terminate/kill/join을 추가 실행하지 않는다.

초기화 실패 경로는 아직 `self.workers`로 확정되지 않은 **unready_workers**의 death_writer를 닫고 `_ensure_worker_termination`을 호출한다. 일반 shutdown의 ready worker 목록만 수집하면 이 분기를 놓친다. exact source/callsite·부모 호출·process 로컬 객체·입력 proc와 rank handle의 관계를 따로 관측해야 한다. 초기화/실행 실패의 근접 시간이나 동일 rank만으로 다른 그룹을 결합하지 않는다. LIF08의 감지/상위 전달과 LIF09의 준비 실패 집계는 그 범위의 기존 증거를 공유하고 회수·후속 기동은 별도로 판정한다.

새 관측의 핵심은 shutdown 호출 수를 늘리는 것이 아니라 **선정 자원과 동일 소유 실행 그룹의 실제 전후 census 및 후속 정상 기동의 대응**이다. 현재 lifecycle capture의 소유 running container·현재 PID/start/lab/source/image 관문을 유지한다. 종료 기록은 안전 파일 주장으로만 보존하고, 컨테이너 종료로 exec가 불가능하면 누락이다. 전체 Pod/환경/argv·다른 process 파일·타 세션 자원·미래 Pod UID를 훑거나 채우지 않는다.

공통 IPC 접근 snapshot은 선정 actor의 pathname/fstat/inode·접근 결과를 제공하지만 실제 engine 소유 목록·전체 shared memory 반환이나 다음 기동의 원인 증명이 아니다. actor/probe·실제 worker·node 관측 주체를 구분한다. 필요한 최소 새 census 계약은 선정 owner·namespace·PID/start·자원 ID와 종류·수집 시각·권한·한도·전후 세대·재기동 lineage·오류/누락 의미를 먼저 확정한 뒤 공개 경계로 연결한다. 합의되지 않은 worker PID/자원 파일 manifest·fault/restart callback payload를 현재 지원처럼 쓰지 않는다.

필수 입력은 정확 ENV/POL/target/prepared/source/image/model 전체 inventory/revision/alias, 선택 backend/TP/필수 rank, 자원 종류별 선정 목록·소유자·회수 기한·잔존 허용/참조 정책·실패 활성/기준판/후보판/정상 재기동 lineage·실행/복구/정리 책임이다. 관측한 hash·PID·기한을 승인값으로 자동 사용하지 않는다. missing 정책·정상/장애 실행의 독립 결합이 없으면 전체를 완료로 처리하지 않는다.

순서는 기존 공개 경계와 실제 consumer/정본 두 분기 대조 → 필요한 최소 선정 자원/소유 그룹 수집·명시 입력과 판정 연결 → 정상·위반·누락·교체/restart·후속 수집/첨부/부모 정리 실패 및 소유 CPU 자원 fixture → 구현/회귀/문서 종료·소스 고정 → 변경 영향/필요 불변식/Linux/설치본·정확Ref·커밋/초안PR/정확HEAD CI/인계다. 정상/위반/누락을 전부 UNKNOWN인 helper 성공으로 대신하지 않으며 확인된 독립 FAIL은 후속 오류가 지우지 않게 한다.

CPU fixture는 소유 child·pipe/socket/shared memory·임시 파일만 사용하고 같은 제품 공개 경계의 EXAMPLE 응답으로 검증한다. 실제 worker/cache·공유 Pod·원본 모델·A2/B 파일은 변경하지 않는다. GPU/실제 TP 회수와 실제 후속 모델 기동은 별도 승인/환경의 근거가 필요하다. 원GPU창종료·추가Start0·마지막확인stopped·기존46.66USD·보존120GiB EBS 과금·CI외부결제 차단을 유지한다.

현재는 두 분기·소스 위험·공통 경계의 검토와 계획 초안이다. 새 제품 구현/등록·source freeze·시험·실증·완료 수를 선언하지 않는다. 실제 재개에는 새 시간 승인, 같은 소유 자원/활성작업/quota/남은 총비용·정확 ENV/POL·준비 소비자·전후 실행/복구/정리 책임을 확인해야 한다. 준비된 독립 개발은 이어간다.

- 검증 마감: 정확 source f1bda4d9a0d726644defaaa005437038948bbe21, 검토 고정 공통 소비c0686ab20d5653091754570853472f9880930dc2. 첫 Windows33실패426통과/Linux33실패427통과와1273파일 명세를 보존했다. 새 census clock 정밀도·시험 Namespace/Legacy 보수 후 Windows194통과/기존skip1·deselect1(98.93초/부모99.812초), 동일1273파일 Linux195통과/기존deselect1(29.46초/부모29.718초), 소스-wheel-격리설치222파일 일치·설치본 두 분기4사례/정확24Ref 왕복8.226초를 확인했다. source/hash 고정 뒤 변경0·새skip/제외0·승인값 자동채움0이며 공통330파일/경로제외0·A2선정12/B원본27/native8~14를 보존했다. 바뀌지 않은 인접 통과를 재사용하고 전체suite를 새로 통과했다고 주장하지 않는다. 실제 소유 Linux CPU socket/shm/alias/child census와 두 분기의 무조건 절대 기한 잔존 FAIL/조건부UNKNOWN/누락/재사용/교체·후속 수집/첨부/부모 정리 오류 뒤 독립FAIL 및 정확Ref를 검증했다. 이 근거는 CPU/EXAMPLE이며 실제 serving/TP/GPU·실패 후 전체 회수/후속 정상 기동·부모A39/56/4·A1 19/27/2 전체 완료가 아니다. 원GPU창종료/Start0·기존비용/EBS과금·CI결제 외부차단과 과거실증source04f7856을 유지한다.
