# LIF09 초기화 부분 실패 관측 제품 계약

정본은 PACK31의 LIF09/SC17-01 원질문·정상·부분 실패·수정 후 정상 기동·FAIL/UNKNOWN 기준이다. 부모 A39/56/4와 A1 19/27/2, 공동 c9af729와 PR124, 과거 팩/Ref·실증·A2/B 원본을 유지한다. 이 묶음은 한 선언 단계의 준비/상위 기동 관측 연결이며 전체 LIF09 완료가 아니다.

`initialization:kubernetes` 0.1.0과 `kubernetes-mp-initialization-observation-v1`을 기존 select/run/judge/report/verify/export/restore에 등록한다. `tools/build_kubernetes_initialization_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새 외부 폴더>`는 전체77개 중 LIF09만 명시 DRAFT로 연결하며 다른76개 정의 bytes와 기존 정본을 보존한다. 원래 정상/위반 기준을 관측 성공으로 대체하지 않는다.

`prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/14 --output <새 외부 폴더>`가 native14와 검토된 immutable native8을 묶는다. 기본8과9~13의 bytes·소스/image/lab 관문·native0.32 미지원은 유지한다. 준비 자산을 실제 serving 시작 경계에 설치하는 작업과 CPU 준비 생성은 다르다. 별도 Pod Python 실행을 serving hook으로 표현하지 않는다.

선정 vLLM0.31 mp source db9527a46873454610df6dbedf79a36d6bf1a7f6의 `multiproc_executor.py` bytes11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b를 소비한다. `MultiprocExecutor._init_executor`, `WorkerProc.wait_for_ready`, `WorkerProc.wait_for_response_handle_ready`의 exact code를 확인하고 static descriptor·인자·반환·예외·순서·기존 실패 정리를 보존한다. 단계 입력/반환 rank/PID 목록은64개 이하이며 PID/start/session/call·부모·프로세스 로컬 executor 객체를 결합한다. worker READY와 상위 기동 반환·예외의 같은 프로세스 구간을 대조하며 별도 worker 모델 적재 완료로 올리지 않는다. 기존native8 모델 선택/적재 관측은 그대로 제공한다.

native14의 자체 관측 함수 code 계약은 CPython3.12.14 `986d4162ad54ff844f8ba71daf144548cbd94ed30b658c57518ca8fccf4e57da`, 3.12.3 `24cddf109ec01d401855a8b6076e753829d94b3869652388d05c85f11c0808d4`로 오프라인 검토 고정했다. 실행 중 발견한 digest를 승인값으로 사용하지 않는다. late code 변경과 source 변경·다른 descriptor는 관측 누락이며 원함수 동작을 대체하지 않는다.2048행/2MiB 상한이다.

실행 설정은 `[executors] initialization="kubernetes"`, `[adapter] name="vllm"`, `[initialization] target="..." prepared=".../manifest.json"`이다. 선택 `kubectl_binary`, `max_duration_seconds`만 허용하며 최대300초·각exec5초·각HTTP10초 상한이다. target은 기존 `guard-kubernetes-request-binding-target/1`, 정확 ENV Ref·run·context·소유 engine/probe 세대와 모델 repository/revision/API 별칭을 재사용한다.

14개 필수 정책은 `initialization_contract.POLICY_TYPES`가 정본이다. ENV/target/prepared/scenario hash, aware600초 이하 유효창, backend mp, 필수 rank 집합, phase(normal/partial-failure/recovered-normal), 필수 단계(worker-ready/model-load/admission 모두), 예정 실패 rank/worker-ready 또는 model-load 단계, 준비/복귀 기한을 명시한다. 정확 모델 전체 inventory hash도 ENV에 있어야 한다. 값은 사용자/기업 입력이며 관측값·빈 모델명·추정정책으로 채우지 않는다. scenario/phase 이름은 실제 활성 또는 실행 간 lineage 증명이 아니다.

공개 전송 `KubernetesInitializationHTTP.perform(slot, request_id)`는 before/after만 한 번씩 순서대로 허용하고 같은 UUID의 `byul-lif09-<32hex>-<slot>`만 받는다. raw callable·임의 경로·payload는 거부한다. 고정 POST `/v1/completions`의 모델 별칭, hello, max_tokens1, temperature0, streamfalse 요청만 사용한다. 응답 원문은 정제 전 메모리에만 있으며 outcome·정상 shape와 소유 engine readiness만 남긴다. 원문 오류/민감 필드/예외·traceback·주소는 저장하지 않는다.

이 새 명시 HTTP 프로필만 running engine의 Ready=false를 허용한다. 소유 probe는 별도로 Ready=true를 요구한다. namespace/Pod/container/image/restart/start·run·ENV·전후 세대와 출력·시간 한도는 유지한다. engine Ready 조건이 유일하지 않으면 readiness=null이며 Ready=false/timeout을 안전 접수 제한으로 승격하지 않는다. 기존 HTTP 기본 프로필을 완화하지 않는다.

선정 session만 seal/capture한다. 종료된 프로세스의 안전한 소유 파일을 파싱해 보존할 수 있지만 현재 PID/start가 없으면 process_context_verified=false다. 그 주장은 authenticated producer·image 확인·같은 물리 기동·과거 worker 소비 증명이 아니다. 현재 컨테이너가 종료/교체돼 exec가 불가능하면 누락으로 남긴다. 다른 Pod 로그·옛 UID·미래 세대 또는 별도 임의 Python으로 채우지 않는다.

정제 samples/calls/correspondence/missing은 EVD와 probe_summary.json에 연결된다. 같은 프로세스 readiness 실패→상위 RAISE와 실패→RETURN 후보를 구분하지만 independent violation으로 확정하지 않는다. 원인 구성요소·실제 모델 load 실패·전체 필수 rank 준비·진행 작업·독립 서비스 gate·정상/부분실패/수정 후 기동 lineage·생산자 인증이 미확인이다. 모든 최종 축은 UNKNOWN, normal_control.succeeded=null, 전체 완료 false다. 부분 수집·정상 HTTP를 완료로 집계하지 않는다.

정상·한 준비 실패·EOF·수정 후 정상과 누락·교체/restart·소스/판본·정책·후속 seal/수집/첨부/부모 정리 실패를 동일 공개 경계로 CPU 검증한다. 정확 고정 함수의 MQ/config 대역과 소유 pipe·임시 자원은 EXAMPLE이며 serving/TP/GPU 실증이 아니다. 후속 실패가 앞선 안전 기록·성공 첨부·독립 FAIL을 지우지 않아야 한다.

실제 재개에는 현재 소유 자원·ENV/POL·full model/revision/alias·source/image·필수 단계/rank·안전 실패 활성·원결과·상위/접수 관측·수정 후 정상 기동 lineage·복구/정리 책임이 필요하다. LIF08 실행 중 네 fault, LIF11 부분 회수/재기동, B LIF07 readiness 전용 분석은 별도다. 원GPU 승인창 종료·추가Start0·마지막확인stopped·기존46.66USD·보존EBS 과금·CI결제 차단을 유지하며 이 묶음에서 AWS 기동/비용/권한을 변경하지 않는다.

검증 마감: 검증 소스99e7f82efd45dcd18518ae577a0ede0e160a519b, 고정 공통 소비4b0483e48d7da0fc7b0aeaa5ff01e7549177bdf9. 첫 Windows26실패와 named pipe 보수 후 Windows1실패612통과/Linux1실패613통과 로그·1263파일 명세를 보존했다. 유일한 응답ID 불일치 wire 중단 기대를 보수한 후 Windows174통과/기존skip1·deselect1(55.99초/부모56.788초), 동일1263파일 Linux175통과/기존deselect1(16.99초/부모17.296초)을 확인했다. 제품 소스/팩/빌드 변경 없음과 소스-wheel217파일 bytes 동일성을 확인해 기존 인접 통과와 격리 설치217파일/CLI4사례/정확24Ref 왕복7.986초를 재사용했다. 공통 소비325파일/경로제외0/hash자동승인false, A2선정12/B원본27/native8~13을 보존했다. 전체 소스 변경·관문완화·새skip/제외·기대값 완화는 없다. 원GPU창종료/Start0/실제실증미완료·모든 새 판정UNKNOWN/normal succeeded=null·단일 선언 단계만 연결·부모A39 및 전체LIF09 완료false를 유지한다. 기존Linux전체6942는 당시 native10 소스의 이력이며 새 소스 전체 통과로 표시하지 않는다. 확정 PR/정확HEAD CI와 최종 파일 bytes는 A1 인계에 기록한다. 이전 PR124의212파일 설치와 실제 배포04f7856 wheel156파일을 이 새 소스의 검증으로 재사용하지 않는다.

첫 영향 시험의 Windows named pipe는 고정 Linux 함수의 Connection 검사와 맞지 않았다. 시험 소유 실제 pipe/EOF를 위임하는 Connection 어댑터로 OS 경계를 보수하고 고정 소스 assertion은 보존했다. 이후 양 OS에서 유일한 실패는 첫 응답 ID 불일치 때 전송기가 후속 wire 요청을 보내지 않는 기존 거부 동작과 새 시험의2회 전송 기대가 충돌한 것이었다. 시험은 wire1회·두 관측 시도에 승인 outcome/정상 shape 없음·ABORTED/UNKNOWN·안전 native 증거 보존을 함께 검증한다. 제품 코드/관문/정상 기대·기존 사례를 완화하지 않는다.
