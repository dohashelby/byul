# OBS02 native18 전용 context 후보의 제품 연결

A-1은 A-2 PR162 확정 0adedb5dd57a988450e0a69c0dacd4947ffdefb1의 전용 세 파일을 원본 bytes로 소비한다. 공개 분석은 `guard.collectors.native_model_mappings.analyze(packet, prepared)` 0.1.0, schema는 `guard-native-scoped-mapping-candidates/1`이다. 기존 `observation_identity:kubernetes` 0.3.0, OBS-02 선택·실행·판정·보고·증거 경로에만 붙인다. native18 prepared와 닫힌 기존 정책·소스·이미지·lab gate를 유지하며 native17/19와 B 소유 구현을 대신 변경하지 않는다.

summary의 `projection.scoped_mapping_contexts`는 원 전용 분석이다. EVD `identity_native_models` 안의 `configured_model_candidates[].scoped_mapping_context_candidates`는 동일 recording/PID/start/model object와 이미 관측된 weight iteration의 loader call/root call/factory/마지막 seq가 일치하고 forward 이전인 후보만 포함한다. 잘린 source_events만 비교하지 않고 first/last locator와 root 마지막 seq를 함께 비교한다. 같은 경로·해시·모델 object나 시간 근접만으로 다른 세대를 합치지 않는다.

전용 소비와 첨부는 공통 mapping 관측 뒤 별도 경계에서 수행한다. 첨부 목록을 모두 계산한 후 반영하며 소비 실패는 `NATIVE_MODEL_MAPPING_CONSUMER_UNAVAILABLE`와 ABORTED를 남긴다. 앞선 공통 weight/mapping 사실과 독립 FAIL을 보존한다. 공통 소비 자체가 실패했다면 전용 함수를 다시 호출해 관측을 되살리지 않는다. 원문 예외·파일·주소·env/argv·요청/응답은 저장하지 않는다.

선정 검증은 전용 분석의 정상·오류·누락·교차 세대·출력 한도와 A-1의 전후 root/마지막 locator·원자적 첨부, 기존 OBS02 CLI의 정상·부분·후속 기록/첨부·전용 소비 오류 및 saved pack 정확 Ref 왕복이다. 기존 교체/restart·정책/소스/image/lab/version 거부와 독립 FAIL 보존은 같은 계약의 인접 회귀로 확인한다. 고정 소스와 결과는 A-1 인계에 별도 기록한다. 이 문서는 검증 결과를 미리 통과로 선언하지 않는다.

EXAMPLE safe_open 대역과 실제 C CPU 구성요소 근거는 구분한다. PR164의 원래 실패를 보존한 5개 원 통과 + alias namespace 보수 1개 통과를 재사용하며 실제 C packet을 새 제품 수집으로 소급하지 않는다. 실제 worker/serving/backend/GPU, 승인·검사 생산자/정책, 전체 inventory·최종 tensor 적용·mapping 연속성/해제와 SUP05/06 전체 분기는 미완료다. 모든 관련 truth flag는 false, security는 UNKNOWN이다.

공통 소스가 바뀌므로 A측 전체 고정 소비 기준을 검토 후 새 커밋에 고정한다. B160의 기존 A152/A156/A158 승인 쌍·원본 c9 pin·helper·전용 파일은 보존한다. 새 A 공통 기준은 B가 검토·고정하기 전 거부되는 것이 정상이며 실행 중 읽은 hash를 승인하거나 검사 경로를 제외하지 않는다. CI5는 정확 HEAD 성공 전 미확인이고 결제 차단을 코드 실패와 구분한다. AWS/GPU/추가 Start·비용·권한 변경은 없다.
