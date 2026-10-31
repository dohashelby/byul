# native19 startup 호출과 선언 파일 context 후보

A-2 SUP05/06의 전용 분석이다. 공개 `guard.collectors.native_worker_model_files.analyze(packet,prepared)` 0.1.0은 정확 native19 admitted packet과 validated prepared를 받아 `guard-native-startup-file-context-candidates/1`을 반환한다. 기준은 A-1 2814662 / reviewed pin bc1f0b3이다. 공개 startup metadata의 `project`, `project_iterations`, `project_mappings`를 사용한다. 공통 selector·수집기·raw 입장 검증·B callback/helper를 복제하지 않는다. 기존 exact17/18 소비기를 변환하거나 판본을 확장하지 않는다.

정확 root FACTORY seq/payload hash에 연결된 유일 startup 후보와 동일 recording/PID/start·loader/model·선언 scope/file·safe_open context를 결합한다. PID나 시간 겹침만으로 귀속하지 않는다. 바깥 startup 호출 안에 있는 paired mapping 사건만 연결하며 callback 실패·부분 recording·없는 다른 capture는 별도로 보존한다. 관측한 원 호출 반환과 callback pair는 구분한다. startup/worker라는 메서드 이름이 실제 worker 역할·내부 body 진입·GPU 준비나 READY를 증명하지 않는다.

원 startup 호출/terminal, root/link, mapping 첫/마지막 사건과 한도가 있는 source locator를 제공한다. mapping 좌표 존재·tensor 경계 반환·context exit는 read·최종 tensor·mapping 존속/해제·producer 신뢰·승인으로 올리지 않는다. 모든 truth는 false이며 security는 UNKNOWN이다. 선언 reference SHA/bytes는 원본 파일을 읽거나 비교한 결과가 아니다. 원문 파일/주소/maps/env/argv/요청/응답/예외를 저장하지 않는다.

A-1 최소 연결은 기존 OBS02의 explicit native19 branch에서 공통 startup/mapping 사실 뒤 전용 함수를 호출하고 summary 및 동일 model/root/context에 붙이는 것이다. recording/model/root/loader/startup call을 대조하고 **startup_terminal_event와 last_mapping_event를 포함한 모든 경계가 선택 forward 이전인지 확인**한다. 잘린 source 목록만 검사하지 않는다. 전용 오류는 정제 진단과 ABORTED를 남기며 앞선 공통 사실·독립 FAIL을 보존한다. 공유 코드·등록·팩·literal pin은 A-1만 작성한다.

직접·인접·설치 공개 함수 검증 뒤에도 신규 정식 CLI·EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref는 A-1 확정 등록 후 확인한다. 실제 C library·serving·worker·승인·최종 tensor·다중 GPU 및 정식 SUP05/06 보안 판정은 미완료다. CPU fixture는 원 vLLM source body와 EXAMPLE dependencies/class 및 명시 library gate 대역이다. native20~22는 자동 지원하지 않는다. CI5·기존 시간/비용/소유권·추가 Start0 경계를 유지한다.
