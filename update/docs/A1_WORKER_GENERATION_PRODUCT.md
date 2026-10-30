# LIF09 Worker 생성 세대·선정 READY 전송

상태: source b88b4a6491a9a0c2908b962b837247c0cea80b55 / reviewed common 062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89의 native21 공통 계측·LIF09 CPU EXAMPLE 제품·설치 검증 마감. 실제 serving/worker/GPU·전체 분기는 미완료다.

SC17-01의 정상·부분 실패·수정 후 기동과 실제 요청 수용 질문을 유지한다. 원 native14/19/20과 B callback/helper bytes를 보존하고 명시 native21으로 기존 initialization:kubernetes 제품에 생성자–Wrapper–Worker 객체와 선정 pipe 사건을 더한다. B/A2 전용 소비·판정은 대신 작성하지 않는다. 기존 OBS02 U1~U6 정책도 미확정이다.

고정 upstream db9527a46873454610df6dbedf79a36d6bf1a7f6의 MPI source11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b, WorkerWrapper source41cdc6fb72d5cb2352093de7fdf35bf829fbb9e097dc6113d4597cd9a88a46aa와 기존 Worker/tracing source를 재사용한다. Wrapper 원 bytes는 역사 image layer manifest와 공식 exact-ref Git blob까지 대조했다. 역사 캐시는 현재 worker/image 인증이 아니다.

제안 profile vllm-native-lifecycle-metadata/21, recording guard-native-lifecycle-events/21, generation/link/ready-send/gap/close 사건 /1이다. main15자산은 기존13 조합에 원 native20과 새 generation contract를 더한다. 기존 prepare-model-read-scope·prepare-lifecycle-sensor를 쓰며 준비 시 모델 읽기·프로세스 기동을 하지 않는다. source/image/lab·vLLM0.31·native0.32 미지원·정확 ENV/target/run/model revision/inventory/session·2MiB/2048행·256호출 한도를 유지한다.

WorkerProc.worker_main static entry, WorkerProc.__init__, WorkerWrapperBase.init_worker의 정확 source/body/바깥 decorator code·closure·span·descriptor를 검토한다. 원 callable을 한 번 호출하고 원 반환·예외·정리를 유지한다. 함수 내부 globals·runtime dependency 전체가 인증되거나 실제 inner body·GPU 완료가 확인됐다고 하지 않는다.

같은 constructor context·PID/start·선정 session에서 실제 Wrapper.worker가 검토된 정확 GPU Worker class/단일 WorkerBase일 때만 opaque token을 연결한다. extension/다른 Worker와 다른 thread·교체·재시작·닫힌 관측은 미결합이다. weak map은 상한과 선정 close에 묶고 constructor 실패 및 worker_main 종료 후 기존 결합을 무효화한다. 이미 수집한 사건은 삭제하지 않는다. PID·시각 중첩만으로 부모와 worker를 묶지 않는다.

generation 사건의 rank/local_rank는 constructor 입력이다. DP가 조정한 실제 device/local rank·rank/shard 배정 인증으로 쓰지 않는다. native21 실행은 새 세대/pipe 관측을 명시한 파생 check가 있어야 하며 옛 저장 팩은 그대로 재판정한다.

READY 관측은 해당 source worker_main이 kwargs에서 선택한 원 stdlib Connection/PipeConnection 한 객체만 사용한다. 전역 Connection을 patch하거나 새 send/close를 수행하지 않는다. proxy는 원 메서드를 한 번 위임하며 send와 close의 CALL/RETURN/RAISE를 별도 호출 token으로 기록한다. 원문 payload/handle·peer handles·FD·주소·env/argv·임의 예외를 저장하지 않고 정확 세 키의 status READY/other만 정제한다. stdlib Python send/close code pin도 판본별로 검토한다.

worker_main은 READY 뒤 busy loop를 계속하므로 함수 RETURN을 READY 선행조건으로 쓰지 않는다. sender 반환은 receiver 확인·MPI 수용·전체 KV cache/compile/warmup·독립 외부 서비스 접수를 증명하지 않는다. constructor/Wrapper/Worker 링크와 READY 전송을 각각 소비하며 실제 producer·역할·rank/shard·신뢰·모델/tensor·GPU·READY delivery·service·전체 scope truth는 false다.

init_native_samples/calls/correspondence의 부모 MP와 기존 init_worker_startup_*를 유지하고 init_worker_generation_events/candidates, init_ready_send_observations를 별도 EVD 필드로 연결한다. 기존 파생 팩의 원77개 질문·정상/실패/복구·판정 기준·76개 bytes를 유지하고 LIF09 원판정 UNKNOWN·normal_control.succeeded=null을 보존한다. 생성/READY 관측 helper만으로 전체 분기를 마감하지 않는다.

검증 범위는 정확 source/decorator·원 호출/예외·소유 CPU pipe·열린 main·중복/누락·다른 귀속/thread/세대·후속 계측/수집/첨부/정리 오류와 기존 CLI/증거/보고/verify/반출/복원·저장 팩 정확 Ref이다. source namespace·모델·dependency·PID/start/time·전송을 재배치한 fixture는 CPU EXAMPLE이며 실제 serving 관측으로 대체하지 않는다.

원GPU창 종료·추가 Start0·46.66USD/사용량·보존120GiB EBS·CI 결제 차단과 C/CPU 11의존성 답변 대기를 유지한다. 실제 실패 주입·복구/재배포·worker/GPU 실증은 fresh 시간·소유권·활성작업·quota·잔여비용·ENV/target/POL·실행/복구/정리 책임이 별도로 필요하다.


검증 결과: 양쪽 직접 고유49사례다. source df4a795의44통과와 새 시험 파일 json import 누락5실패를 보존하고, 제품 코드·assert·기대값을 바꾸지 않은 b88b4a6에서 영향5사례를 다시 확인했다. 인접 Windows337통과/기존1skip·1deselected, Linux338통과/기존1deselected이며 source1435파일 전후 변화0이다. 생성/원 pipe 정상·생성자 실패·send 실패·busy loop 실패·close 후 오류, 관측 준비/기록/결합 오류의 원 호출/정리 보존, 다른 thread/종료 세대·선정 close·열린 main, 문맥/정확 link/중복/누락·옛 판본 거부와 기존 CLI 후속 수집/첨부 오류를 확인했다.

source/wheel/Windows 격리 설치270파일 일치, wheelSHA f238da1dac617fb4c23e202ad6fd74682aaf8cf73e4f61094995a4d79c0d6220. Linux 격리 설치의 공개 LIF09 CLI 정상·send 실패·seal 실패·첨부 오류4사례에서 각각6Ref·합계24Ref를 verify/export/restore·저장 팩 정확 Ref 재판정으로 대조했다. Source bytes·namespace·model·PID/start/time·전송은 명시 EXAMPLE이며 소유 stdlib pipe 위임만 실제 CPU I/O다. 실제 vLLM 패키지/serving/worker/GPU·Torch/safetensors 실행과 새 의존성 설치는0이다.

관측 code pin은 직접 source compile 기준 Windows3.12.14의 d61dcbfaf7dfb6862f0933c48f06a5b7735eb8433b8e7416a0a4169a75620881, Linux3.12.3의 b6ef5fd638b33995eaa1217bf56279e2b3e9cf6e67781e60b7e9d9deef75e686이다. vendor 세 point·OTel wrapper와 stdlib send/close code도 판본별 literal로 검토했다. source/실행 입력 hash를 자동 승인하지 않았으며 gate/assert 완화·경로 제외·새 skip이 없다.

원13파일·B bytes·옛14/19/20 및 과거 저장 팩/Ref를 보존했다. collection profile은 기존 v1이고 executor0.3.0·파생 pack3/LIF09rev7이 새 생성/pipe 관측을 명시한다. 실제 producer/worker 역할·부모 cohort와 rank/shard·READY 수신/서비스/전체 준비·fault/repair lineage 및 CI5는 여전히 미확인이다. 전체 판정UNKNOWN, normal_control.succeeded=null과 원GPU창 종료/추가Start0·원예산·EBS과금·C/CPU 의존성 답변 대기를 유지한다.
