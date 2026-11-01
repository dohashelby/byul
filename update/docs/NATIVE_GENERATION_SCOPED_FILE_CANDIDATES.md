# A-2 exact native20~22 전용 파일 후보

확정 공통 `69b27f84015d7384bbcef4df27cd7d43d1f5eb4a`의 공개 `guard.adapters.vllm_generation_file_metadata.project_file_contexts(packet, prepared)` 판본 0.1.0, schema `guard-native-generation-file-contexts/1`을 소비한다. native20·21·22만 명시적으로 받는다. 기존 source/code/runtime/session·ENV/target/run·image/lab·nonsecret scope의 검증을 마친 packet과 동일한 prepared가 전제다. 임의 JSON 반입을 신뢰 관측으로 승인하는 API가 아니다.

공개 전용 함수는 `guard.collectors.native_generation_model_files.analyze(packet, prepared)`이며 반환 schema는 `guard-native-generation-scoped-file-candidates/1`, 판본은 0.1.0이다. 검토된 공통 17파일 literal을 확인하고 기존 A-2 native19의 선언 파일 참조 검증과 local startup/root/model/loader/context 결합을 재사용한다. native19 공개 동작·profile·오류 코드는 유지한다. 공통 journal/selector/전송/reader를 복제하거나 새 profile을19로 재라벨하지 않는다.

원 recording·PID/start·root FACTORY/링크·model/loader/context와 선언 파일 참조가 일치하는 후보만 결합한다. 프로세스 결합과 node/runtime 이미지 결합은 별도다. 이미지 flag=false가 유효 프로세스 관측을 지우거나 실제 worker/이미지 승인으로 바뀌지 않는다. 같은 PID/start 중복·교체·잘못된 소스는 결합하지 않는다. 독립 단계의 유효 관측 수, 원 인덱스, 부분/누락·정제 진단을 유지한다.

공통21/22의 현재 공개 결과에는 startup span의 RETURN/callback pair 필드가 없다. `startup_pair_facts_available=false`와 missing을 기록하고 해당 두 사실을 false(미확인)로 남긴다.20에서 공개 span이 있으면 원값을 쓴다. callback payload를 다시 짝짓거나 공통 private journal을 호출하지 않는다. A-1 최소 공개 사실 요청을 별도로 유지한다. 이 제한을 다른 유효 root/file 후보나 전용 개발 전체의 차단 사유로 만들지 않는다.

`attach_configured_models(trials, analysis)`는 A-1의 기존 공통 model/root 첨부가 선행된 위치에서 사용한다. 원 `generation_file_context_candidates`가 동일한 startup/root/loader를 제공해야 하며, 전체 weight 상한·root 종료·마지막 mapping이 해당 forward보다 앞서야 한다. 잘린 locator 목록 끝을 전체 상한으로 사용하지 않는다. 전용 결과는 `generation_scoped_file_context_candidates`에 붙인다.

security=UNKNOWN, 실제 worker·인가·최종 tensor 적용·mapping 연속성·전체 inventory/scope truth는 false다. source-qualified Python 반환이나 local generation 후보를 실제 운영 worker/READY/기업 승인으로 승격하지 않는다. MEASURED scope 표지도 별도의 권한이나 완료 근거가 아니다. 원문 maps·주소·파일/텐서 값·환경·요청/응답·예외 원문은 저장하지 않는다.

A-1이 기존 OBS02의 공통 투영·model 연결 뒤 전용 analyze/attach 두 호출을 정식 연결하고 reviewed full snapshot pin을 고정해야 제품 연결을 마감한다. 등록 전에는 우회 CLI를 만들지 않는다. 설치 bytes와 직접·인접 회귀는 정식 제품의 EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref 및 필요한 실제 검증과 구분한다. 부모 A39/56/4, A-2 20/29/2, 필수 CI5 미완료와 원 GPU 창 종료/Start0·비용/소유권 제한을 유지한다.
