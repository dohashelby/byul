# A2 native16 선택·진행 후보 결합

이 전용 분석은 SUP03/05/06의 후속 파일·적재 분석을 위해 같은 recording의 native8 파일 선택 metadata와 native16 iterator 진행을 결합한다. 확정 공통 b6dacb4, 검토 snapshot c8c4b26 및 `guard-weight-iteration-projection/1`을 소비한다. 기존 native_weight_files/profile9 bytes와 해석은 바꾸지 않는다. 새 공통 수집기·selector·parser·CLI·registry·팩을 작성하지 않는다.

공개 함수는 `guard.collectors.native_weight_iteration.analyze(packet)`이다. 입력은 A1의 기존 `capture_session` 경로에서 prepared/ENV/target/source/code/session/clock/hashchain/process binding을 이미 검증한 normalized packet이다. 임의 JSON을 신뢰 관측으로 반입하는 도구가 아니다. 이 함수는 새 profile16만 명시적으로 받고, 고정 metadata/sensor bytes를 literal과 대조하며, 공통 journal/project 및 returned_weight_selection을 재사용한다.

판본 0.1.1은 `capture_session`이 개별 읽기 실패 때 내보내는 `capture=None`과 두 binding=false 행을 불완전 수집으로 남긴다. 다른 recording의 유효 paired 사실과 원래 recording 번호·source locator는 보존한다. 누락 행에서 관측을 만들거나 전체 수집 완료를 주장하지 않으며, binding=true인 모순 행과 malformed/unsupported 입력은 여전히 거부한다.

같은 recording·loader call·model object에 속하고 선택 뒤에 관측된 paired yield만 후보로 결합한다. 서로 다른 recording/PID·call·model을 이어 붙이지 않는다. root_load는 전체 모델 세대가 아닌 generator 구간 ID다. 한 load call의 여러 선택은 후보의 모호성으로 남기며 파일별 실제 진행을 추정하지 않는다. 경로 hash·locators·후보 개수는 제한하고, prefix/부분 진행과 coverage를 구분한다.

native16 proxy 반환에서는 legacy `factory_returned_generator`가 false일 수 있다. 원값을 그대로 남기고, 실제 진행은 공통 paired step에서 따로 읽는다. 값을 true로 바꾸거나 flag를 삭제하지 않는다. callback의 반환 후 이름 yield는 callback 구현·최종 tensor 저장·GPU 완료의 증명이 아니다.

반환 schema는 `guard-native-weight-selection-iteration/1`이다. source locator·같은 call 후보·선택 path SHA·관측된 yield/callback 수만 반환한다. security=UNKNOWN, per_file_consumption_verified 및 worker/file-read/tensor-storage/callback-implementation/producer truth·scope_complete는 항상 false다. 승인 manifest/POL·worker FD/bytes·required tensor/rank/shard 및 실제 적재·거부가 별도 확정되기 전에는 SUP05/06의 PASS/FAIL이나 전체 분기를 마감하지 않는다.

정식 연결은 A1이 기존 OBS02의 검증된 packet 소비 지점에서 이 함수를 호출해 별도 `weight_selection_iteration` 보고 projection으로 붙이는 최소 공통 인계를 따른다. 기존 공통 `weight_iteration`의 paired facts와 missing은 그대로 보존한다. A2는 source pin·전용 분석·직접 회귀를 제공하며 A1 공통 파일을 수정하지 않는다. SUP05/06은 원래 정의·분기·미완료 상태를 유지한다. 분석 helper·CPU EXAMPLE을 실제 serving/worker·FD·tensor 실증으로 대체하지 않는다. 원목표 A39/56/4·A2 20/29/2, CI5 미확인과 원GPU창 종료/Start0도 유지한다.
