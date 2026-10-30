# LIF09 Worker·MP 공통 관측 결합

상태: 구현 초안. 지원 판본·source/hash 고정과 검증 전이며 실제 serving/worker/GPU 실증은 없다.

SC17-01 원질문인 준비 실패를 놓친 요청 수용과 정상→부분 실패→수정 후 기동을 유지한다. 기존 native14 부모 MP 준비 대기와 native19 Worker 메서드·모델 root 관측을 원 bytes 그대로 명시 native20에 조합하고 기존 initialization:kubernetes CLI에 연결한다. 새 collector나 관측 helper만 제공하고 제품 연결을 미루지 않는다.

신규 profile vllm-native-lifecycle-metadata/20, recording guard-native-lifecycle-events/20, projection guard-worker-mp-initialization-projection/1이다. native14/19 사건 schema와 code pin은 그대로다. 기존 journ​al을 다른 header로 재표시하지 않는다. main·Worker19·MP14와 기존 의존 자산은 각각 검토 hash를 확인하며 prepare 시 13자산을 준비한다. source/image/lab와 native0.32 미지원·정확 ENV/target/run/model revision/inventory/session·2MiB/2048행·startup256호출 한도를 유지한다.

LIF09 기존 aware600초 창·정확 prepared/scenario hash·명시 mp/rank/단계·선언 phase·기한 정책을 재사용한다. 모델 scope는 실제 전송에 쓰는 lifecycle target과 결합한다. 기존 before/after 고정 hello1token HTTP 요청과 소유 probe·전후 세대 확인은 바꾸지 않는다. 선정 session만 seal하고 수집·첨부·정리 오류에도 앞선 유효 관측을 보존한다.

init_native_samples/calls/correspondence는 부모 MP 사실이다. 새 init_worker_startup_events/candidates/missing은 Worker 경계 사실을 별도로 제공한다. root FACTORY와 source/code/session/PID/start/call/model token을 유지하지만 iterator 생성·함수 반환을 tensor/GPU 준비로 올리지 않는다. 부모 handle의 PID와 Worker PID가 같거나 구간이 겹쳐도 start/worker assignment·rank/shard·실제 producer 신뢰가 없으면 cross-process lineage는 false다. 별도 부모/worker 기록의 시간 순서는 다른 time namespace·offset·시계 불확실성을 해결하지 않는다.

정상·위반 후보·누락·중복·세대교체·restart·후속 수집/첨부/정리 오류와 exact Ref 왕복을 선정 검증한다. 실제 실패 주입/정상 복구/재배포·서비스 접수·worker/GPU 회수는 새 운영 승인·실제 소비자·정책·소유권이 필요하다. normal_control.succeeded=null, 판정 UNKNOWN, full scope=false를 유지한다. B readiness·A2 전용 소비를 대신 구현하거나 원 판본·pin을 바꾸지 않는다. 원GPU창 종료·추가 Start0·46.66USD/사용량·보존120GiB EBS와 CI 결제 차단을 유지한다.
