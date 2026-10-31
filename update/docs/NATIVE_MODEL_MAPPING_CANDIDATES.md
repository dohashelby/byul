# native18 선언 파일의 safe_open context 후보

A-2 SUP05/06의 보조 전용 분석이며 공개 함수는 `guard.collectors.native_model_mappings.analyze(packet, prepared)` 0.1.0, 출력은 `guard-native-scoped-mapping-candidates/1`이다. 검토된 공통 d5ed533 / pin eba2440의 정확 native18 admitted packet과 validated prepared만 소비한다. raw capture 입장 검증·selector·전송·sensor·공통 trust 검증을 새로 만들지 않는다. 기존 exact17 소비기는 변경하지 않는다.

공통 `vllm_safe_open_mapping_metadata.project`와 `project_iterations`로 얻은 동일 recording/PID/start·root·loader call·model object·선언 scope/file·context를 결합한다. library 정적 파일 검증과 paired iterator/root 연결이 없는 사건은 정제 이유와 원 source locator로 남긴다. 재적재·같은 경로·같은 model object·다른 recording을 합치지 않는다. mapping 좌표는 비원자적 위치 후보다. 같은 region token의 반복은 연속 존속·세대 동일성·해제 증명이 아니다.

constructor/enter/tensor return/context exit 및 원 오류, snapshot 한도·부분 수집·경로 교체와 recording 종료를 각각 보존한다. 출력 한도를 넘으면 count·완전성 flag와 정확한 `last_source_event`를 유지한다. 선언 reference hash/bytes는 비교 기준 metadata이며 관측한 파일 hash/bytes가 아니다. context가 없는 선언 경로는 후보 공백으로 남긴다. 실제 worker·읽기·per-file 소비·최종 tensor 적용·mapping 해제·승인·inventory·scope truth는 모두 false, security는 UNKNOWN이다. 비밀값·원문 maps/address/path·model bytes·env/argv·예외 원문을 저장하지 않는다.

현재 정식 OBS02는 공통 native18 mapping 후보만 소비한다. 새 전용 분석의 공통 등록은 A-1 소유이며 별도 우회 CLI를 만들지 않는다. 최소 연결은 기존 `project_native`에서 공통 사실 뒤 이 함수를 호출하고, summary와 동일 recording/model/loader/root의 forward 이전 후보에 붙이는 것이다. **한도로 source_events가 잘려도 `last_source_event`를 반드시 비교**한다. 소비 오류는 정제 진단·ABORTED를 남기고 이미 수집한 공통 사실과 독립 FAIL을 보존해야 한다. A-1 reviewed literal pin·등록·설치본 정식 CLI·EVD/RES/RPT·verify·반출/복원·정확 Ref는 해당 확정 통합 후 검증한다.

선정 CPU 시험은 정확 vLLM 함수와 EXAMPLE safe_open class/소유 stdlib mmap을 쓰며 C library gate 대역을 명시한다. 실제 safetensors C·Torch 설치/실행·serving·worker·GPU 검증은 포함하지 않는다. 실제 C 의존 실행 승인과 기업 주체/허용 대상/정책·실제 귀속·정식 SUP05/06 정의/판정·다중 GPU는 별도 미완료 범위다. 기존 비용·시간·소유권과 CI5 미확인 상태를 유지한다.
