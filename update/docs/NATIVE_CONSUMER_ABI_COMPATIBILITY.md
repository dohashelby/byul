# A-2 native 소비기의 고정 ABI 호환

A-1 확정 소스 `3466c502c60475cba517a8feac59dddb693f5170`과 검토된 공통 기준 `b3e3bb054bc281e270a0e7be885951a479d993d0`을 소비한다. 공통 계약은 `CPython3.12.15 native ABI / 1`이다. 이번 전용 변경은 `native_weight_iteration`, `native_model_file_reads`, `native_model_mappings`, `native_worker_model_files`의 source pin 20개 중 변경된 11개 literal에 한정한다. 실행 중 수집한 hash를 승인값으로 사용하지 않는다. 기존 원본 커밋과 이전 소비 근거는 보존한다.

지원 profile은 각각 native16·17·18·19 그대로이며 공개 함수·schema·판정·후보 결합·출력 한도는 바뀌지 않는다. 공통 producer는 CPython 3.12.15의 Linux/x86_64/optimize0만 추가한다. 기존 CPython 3.12.3 POSIX와 3.12.14 Windows를 유지한다. 수집된 명시 runtime의 decoder 검증은 수집 호스트와 구분한다. native0.32 또는 native20·21·22 파일 소비 지원을 추가하지 않는다.

검토 근거는 변경된 공통 source의 공개 인터페이스와 profile/schema 동일성, A-1의 source/compiler 검토 및 판본별 직접·설치 원본 근거다. 전용 4개 모듈의 기존 회귀로 정상·부분·누락·교체·후속 오류와 source/runtime/session 거부를 확인한다. literal 외 실행 AST가 같은지 별도로 확인한다. 원문 환경·비밀값·요청/응답·임의 예외 원문은 기록하지 않는다.

이 전용 커밋은 A-1의 새 full snapshot pin 통합을 필요로 한다. A-1 소유 `test_a1_native31215_abi.py`의 기존 구 pin 거부 시험은 구 literal을 명시적으로 주입하여 거부 경계를 계속 검증하도록 A-1이 연결한다. A-2가 공통 pin이나 이 시험을 바꾸지 않는다. 정식 등록·snapshot 검증과 설치본 CLI의 EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref 검증은 통합 상태와 구분해 기록한다.

후보의 security=UNKNOWN과 실제 worker·공급물 승인·tensor 저장·최종 적재·범위 완료에 대한 false 의미는 유지한다. CPU/EXAMPLE 회귀와 설치 성공은 실제 serving/GPU 실증이나 SUP03/05/06 전체 보안 분기의 완료 근거가 아니다. 부모 A39/56/4 및 A-2 20/29/2 원목표, CI5 미확인, 원 GPU 승인창 종료와 추가 Start 0도 유지한다.
