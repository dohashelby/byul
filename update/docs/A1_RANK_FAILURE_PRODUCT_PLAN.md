# LIF08 선정 executor의 rank 실패 감지·전달 제품 묶음 초안

공동 c9af729와 PR123 확정683e48d542065dd0da01223aa04130a86235c9a4, 기존 미커밋·worktree·sources/·팩/Ref·A2/B 원본·실제 증거를 보존한다. 부모 A39/56/4와 A1 19/27/2를 유지한다. PR123의 검증 소스241f9f2·설치207파일/24Ref·Linux740은 그 판본의 CPU/EXAMPLE이며 실제 wheel04f7856과 구분한다.

대상은 원팩31 LIF08/SC18-02다. 선정 executor에서 rank **예외·사망·지연·통신 정지** 네 조건을 각각 구분한다. HTTP timeout이나 EngineDeadError 한 종류로 네 fault를 대신하지 않는다. 정상은 필수 rank의 계산·통신과 선정 output rank/집계 경로의 최종 결과다. 모든 rank가 최종 출력을 반환한다고 가정하지 않는다. 초기화 부분 실패는 LIF09, 실패 뒤 process/IPC/자원 회수·다음 기동은 LIF11, 상태 표현 대조는 OBS02다.

첫 구현은 vLLM0.31의 명시 `mp` executor 관측 경계에 한정한다. 다른 executor·uniproc·ray·native0.32·TP2 실증을 추정하지 않는다. 기존 source/lab/image gate와 native8~12 bytes를 보존하고, 필요한 새 관측은 명시 프로필로만 선택한다. controller·parser·Kubernetes 수집·LIF08 전용 소비·DRAFT팩/registry·기존 CLI를 같은 묶음에서 연결한다. 원팩의 네 fault와 원질문/정상/FAIL/UNKNOWN 기준·다른76정의를 유지한다.

검토할 실제 경계는 `MultiprocExecutor.execute_model/collective_rpc`, `FutureWrapper` 결과/예외, `WorkerProc._execute_worker_rpc/handle_output/enqueue_output`, worker monitor가 실패를 확인한 경로, `EngineCoreProc._handle_client_request/_send_engine_dead`, 실제 client의 실패 인수다. source/code·선정 rank/출력 rank·scheduler/native request·call/session/PID/start를 결합한다. worker 예외 처리와 MQ enqueue, 집계 결과, 상위 접수는 서로 다른 관측이다. 프로세스 사망이나 지연/정지를 예외 문자열로 분류하지 않는다. 원문 예외·traceback·MQ 내용·tensor·출력·주소·자격·Pod env/argv를 보존하지 않는다.

공식 고정 소스는 [vLLM db9527a](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/v1/executor/multiproc_executor.py)다. 캐시된 설치 layer30530efd의 manifest와 MP 파일11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b를 먼저 대조한다. core/core_client의 기존 검토 SHA는 재사용한다. 새 소스·코드 승인은 고정된 오프라인 검토로 확정하며 대상이 실행 중 내는 hash를 자동 승인하지 않는다.

필요 입력은 정확 ENV/target/prepared·선정 mp/rank 집합·output/집계 경로·모델/이미지/소스·aware 유효창·종류별 감지/전달 기한이다. 네 fault 각각의 안전 활성 생산자·실제 발생 시각/시계·대상 rank/프로세스·복구 책임도 별도다. 아직 확정되지 않은 fault payload/제어 API/파일을 현재 지원 계약으로 만들지 않는다. 관측과 외부 fault 활성/발생·감지·상위 전달·요청 실패 결과 사이의 근거가 없으면 누락/UNKNOWN으로 기록한다. 선언된 fault 이름이나 비슷한 시간만으로 단계를 연결하지 않는다.

우선 수동적으로 관측 가능한 실제 선정 메서드의 경계를 기존 제품에서 소비하고, 네 fault의 활성/발생·시계 결합이 준비되지 않은 부분은 명시 미완료로 둔다. 관측 코드가 사용자 callback·RPC 내용/순서·반환·예외·취소를 바꾸거나 worker를 종료하지 않게 한다. 별도 Pod Python을 serving hook으로 올리지 않고 다른 세션·공유 GPU에 장애를 주입하지 않는다.

검증 순서는 구현·회귀/문서 초안 종료 → 고정 소스 → 직접/동일계약·인접·불변식 → 같은 바이트 Linux → 설치본 CLI와 정확6Ref 반출/복원·저장팩 재판정 → 확정 커밋/초안PR/정확HEAD CI/인계다. 원본 메서드 본문의 CPU 의존성 대역과 별도 소유 CPU child 예외/종료/제한 지연/통신 차단은 EXAMPLE 검증으로만 사용한다. 정상·네 조건의 구분·누락/교체/restart·후속수집/첨부/부모정리 실패가 앞선 증거와 독립 FAIL을 지우지 않는지 확인한다.

제품 선택은 `[executors] rank_failure="kubernetes"`와 별도 `[rank_failure]`의 `target`, `prepared` 키다. `guard prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/13 --output <새 외부 경로>`와 `python tools/build_kubernetes_rank_failure_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새 외부 팩>`을 준비한 뒤 기존 select/run/judge/report/verify/evidence export/restore를 사용한다. 공개 전송은 `KubernetesRankFailureHTTP.perform(slot, request_id)`이며 임의 body/path/header를 받는 raw 호출은 거부한다. collection profile은 `kubernetes-mp-rank-failure-observation-v1`, HTTP profile은 `kubernetes-owned-mp-rank-failure-http-v1`, event schema는 `guard-native-rank-failure/1`이다.

POL16개는 `rank_failure_contract.POLICY_TYPES`가 정본이다. 정확 ENV/target/prepared hash·aware600초 이하 창·backend=mp·명시 rank 문자열 목록/선정 output rank·각 fault의 감지/통지 ms 기한을 요구한다. rank0~63 최대64개만 지원하며 관측한 rank를 입력 정책으로 승인하지 않는다. 선언 rank/output/world size와 source 표식의 차이는 미확정 후보로 기록한다. 실제 rank/group·fault·시계/기한과 상위 인수를 검증한 판정은 아니다.

요청은 같은 UUID 아래 normal-before → observe-exception → observe-death → observe-delay → observe-communication-stall → normal-after의 고정 JSON hello1token/temperature0/streamfalse뿐이다. 정상 전후 구조가 없으면 관측 단계는 건너뛰거나 실행을 미완료로 기록한다. 두 소유 engine/probe Pod 전후 세대·모델 별칭을 확인하며 요청10초/전체최대300초·출력2MiB/본문128KiB 한도를 재사용한다. native는 객체256개/recording2048행·2MiB의 weak 관측으로 제한한다. Future의 반환과 실제 결과/예외를 분리하고 비동기 worker 출력 context가 사라지면 연관을 추정하지 않는다.

현재는 native13/controller·닫힌 metadata/parser·Kubernetes reader/전송·LIF08 소비·DRAFT팩/registry와 회귀/문서 초안을 작성한 단계다. 프로필13 자기 함수의 code를 오프라인으로만 검토해 CPython3.12.3/3.12.14 optimize0 리터럴을 고정했다. 실행 대상의 hash를 읽어 승인하지 않는다. 기능 시험·source/hash 고정·소비 pin·설치 검증 전이며 공개 검증 완료나 네 fault 완료를 선언하지 않는다. 새 fault 제어 payload/생산자 계약은 구현하지 않았다. 실제 재개에는 새 시간 승인과 정확 ENV/POL/자원/소유권·실행/복구/정리 책임이 필요하다. 원GPU 승인창 종료·추가Start0·마지막 확인stopped·기존46.66USD·보존EBS과금·CI결제 차단을 유지한다.

- 검증 마감: 정확 소스0261fc95ab31ace84c89a7ae621871faf43c083b, 공통 소비 pin5c1504b6c3e26f5efdbe24b995907c9e941b05b0에서 Windows 620 passed, 1 skipped, 1 deselected in 212.21s (0:03:32)(부모 213.367초), 동일 1,253개 파일 바이트의 Linux 621 passed, 1 deselected in 69.27s (0:01:09)(부모 69.689초), 소스/wheel/격리 설치212파일 일치, 설치본 CLI4사례·정확24 Ref 왕복 8.131초를 확인했다. 검증 중 소스 변경·새 제외·관문 제외·관측 hash 자동 승인은 없었다. 전체 공통 소비320파일과 A2 선정12/B 원본27/native8~12·원팩31을 보존했다. 고정 실제 MP/core 메서드 code·선정 output rank/Future 결과·worker 예외 객체·소유 CPU child의 네 설정/정리와 실제 사망 monitor→core 표식, closed HTTP6요청 및 누락/교체/후속 오류를 검증했다. 이 근거는 CPU/EXAMPLE 관측 제품 연결의 검증이며 실제 serving/TP/GPU·네 fault 활성/발생/기한·client 인수·필수 rank 정상 완료의 증명이 아니다. 모든 새 판정은 UNKNOWN이고 정상 succeeded=null, 생산자/전체 범위는 false다. 이전 Linux 전체6942는 당시 native10 소스 근거로 유지했다. 실제 전체 LIF08·부모 A 완료 수는 늘리지 않았다. 결과 문서 두 개만 갱신하고 확정 커밋/초안PR/정확HEAD CI/인계를 마감한다.
