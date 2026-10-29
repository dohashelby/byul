# LIF09 초기화·모델 준비 부분 실패 제품 묶음 초안

공동 c9af729와 PR124 확정ab466525410492e40f3d349977a9f48e4c33b84c, 기존 미커밋·worktree·sources/·팩/Ref·A2/B 원본·실제 증거를 보존한다. 부모 A39/56/4와 A1 19/27/2를 유지한다. 직전0261fc9 소스의 Windows620/Linux621·설치212파일/24Ref는 CPU/EXAMPLE 근거이며 실제 배포 wheel04f7856과 다르다.

대상은 원팩31 LIF09/SC17-01이다. 한 구성요소의 설정·worker 준비·모델 적재 실패가 전체 기동/그룹 실패로 반영되고 readiness/서비스 접수를 제한하는지 확인한다. 정상은 모든 필수 준비 단계가 성공한 기동이다. 실패를 수정한 뒤 정상 기동과 비교해야 한다. 실행 중 네 rank fault의 감지는 LIF08, 부분 자원 회수·재기동 자체는 LIF11, readiness 전용 판정은 B LIF07, 상태 표현 대조는 OBS02와 구분한다.

선정 mp의 기존 고정 소스·MP/core 참조·native8~13와 모델 선택/적재 관측을 재사용한다. 첫 검토 지점은 `MultiprocExecutor._init_executor`, `WorkerProc.wait_for_ready/wait_for_response_handle_ready`, worker의 초기화/모델 준비와 기존 native8 load_model 결과다. READY 한 개·예외 문자열·한 rank 결과를 전체 준비 성공/실패로 집계하지 않는다. 원래 준비 함수의 반환·예외·순서와 callback/RPC를 보존하고 model 파일·실제 worker·공유 Pod를 변경하지 않는다.

현재 공통 capture는 running 세대와 현재 PID/start의 물리 결합을 요구한다. 초기화 실패 후 프로세스가 사라졌다는 이유로 이 관문을 완화하지 않는다. 필요하다면 새 명시 프로필에서 **소유 파일의 정제된 종료 기록 주장**과 **현재 살아 있는 프로세스 결합**을 서로 다른 상태로 표현한다. 종료 기록만으로 process_context_verified·producer·image 소비를 true로 올리지 않는다. 이 표현과 소비 판본은 아직 설계 단계이며 현재 지원 계약이 아니다.

Kubernetes 수집은 실제 소유 engine/probe namespace·Pod UID·container/image/restart/start를 유지한다. API 컨테이너는 running이지만 readiness=false인 초기화 관측이 필요하면 그 분기만 명시적인 새 프로필/공개 인터페이스로 추가한다. 기존 reader/프로브의 Ready 관문을 전역 해제하지 않는다. 컨테이너 종료/교체 뒤 exec가 불가능하거나 안정된 원기록 수집 경로가 없으면 누락으로 기록한다. 미래 UID·추정 상태·다른 Pod 로그로 대신하지 않고 기동/reset/재배포를 하지 않는다.

필요 입력은 정확 ENV/target/prepared·선정 backend/필수 rank·모델 inventory/revision/alias·필수 준비 단계와 명시 기한·실패/복귀 정책·상위 집계/실제 접수 관측이다. 모델/단계 한 곳의 제한된 안전 실패 활성·원결과·상위 그룹·readiness·요청 비수용과 수정 후 정상 기동의 대응을 별도로 결합한다. 단순히 다른 두 Pod snapshot이나 near-time 이벤트를 기준판/실패판/복구판으로 이름 붙이지 않는다. 미확정 fault payload·제어 API·archived binding은 현재 지원으로 취급하지 않는다.

소비자는 본인 LIF09의 기존 CLI/명시DRAFT팩/registry와 EVD/RES/RPT·verify·반출/복원·저장팩 정확Ref 재판정이다. 원질문·정상/FAIL/UNKNOWN 기준·한 분기·다른76정의와 독립 FAIL을 보존한다. 실제 구성요소/그룹/서비스 gate의 대응이 없으면 UNKNOWN이고 수집·표현의 부분 연결만 완료 단계로 기록한다. B 전용 readiness 수집/분석은 B가 유지하고 A1은 확정 공통 입력만 제공한다.

순서는 소스/실제 consumer·새 관측과 정책/누락 의미 확정 → 필요한 최소 공통 startup 경계와 전용 소비/팩/등록·회귀/문서 → 소스/hash 고정 → 직접/동일계약·인접·불변식/Linux·설치본 CLI/정확Ref → 확정 커밋/PR/정확HEAD CI/인계다. 정상·한 곳 실패·누락·교체/restart·후속 수집/첨부/부모 정리 실패와 이전 증거를 확인한다. CPU의 작은 준비 실패·소유 임시 파일/child는 EXAMPLE이며 실제 모델/TP/GPU 실증이 아니다. 실제 모델 cache·sources/·A2 전용 모델 파일은 보존한다.

현재는 정본 체크리스트와 소스 경계를 검토한 계획 초안이며 구현·등록·소스 고정·시험·실증을 시작했다고 선언하지 않는다. 원GPU 승인창 종료·추가Start0·마지막 확인stopped·기존46.66USD/보존EBS과금·CI결제 차단을 유지한다. 실제 재개에는 새 시간 승인과 준비된 소비자·정확 ENV/POL/자원/소유권/실행·복구·정리 책임이 필요하다.
