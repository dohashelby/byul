# A2 native16 전용 소비의 OBS02 제품 연결 초안

A2 확정 9bda8b3d94def8da9ccb1a7d1f723c0a01568af8 / PR140의 collector·시험·문서 세 파일을 원본 bytes로 소비한다. 이 문서는 A1 공통 통합 범위이며 A2의 전용 분석을 재작성하지 않는다.

기존 observation_identity:kubernetes의 정확 native16 prepared 분기에서 공통 parser/capture가 입장을 확인한 packet을 native_weight_iteration.analyze(packet)에 전달한다. 전체 전용 projection은 summary.projection.weight_selection_iteration에, 실제 같은 recording/model·후속 forward에 앞선 사건은 identity_native_models의 configured_model_candidates[*].weight_selection_iteration_candidates에 보존한다. 기존 common weight_iteration과 독립 FAIL·신뢰 false/UNKNOWN을 유지한다. consumer의 오류는 정제 진단과 local gap으로 남기며 앞선 유효 common 사실을 지우지 않는다.

A2 SOURCE_PINS의 원래 metadata f8ced901.../sensor e7494eda...를 유지한다. native16 metadata는 b6dacb4의 원본 bytes로 복원한다. native17은 별도 공통 file_read_metadata.project_iterations(packet)에서 닫힌 native16 journal을 재사용하되 profile17 envelope를 명시적으로 선택한다. 17을 16으로 바꾸거나 gate를 통과시키는 shim은 없다. 기존 native17 FD/read 및 OBS02 기능의 회귀도 함께 확인한다.

정식 제품 연결과 CPU/설치/정확Ref 검증을 마감한 범위만 인계한다. SUP05/06 정식 등록·실제 file/FD/tensor·worker 역할·승인/검사 신뢰·전체 분기 완료를 뜻하지 않는다. native0.32/source/image/lab gate, B c9 원pin, full snapshot 검토 및 운영 비용·소유권 제한을 유지한다.

상태: 고정 source 9997936a2e038dbfcdd11c1c9399f08b276f4906 / 검토 pin 1dab9b2630ee92761d8a79de54e007d1e2f35ea7의 변경 영향 검증과 설치본 인계 범위를 마감했다.

검증은 직접 Win/Linux각19, 인접각90, 동일1370파일불변이다. A2 원본24시험 통과는 변경 없는 원본 소비기/common source에 대해 첫source5e78dfe에서 재사용한다. 설치252파일 일치, A2 native16 기존CLI4/정확24Ref와 native17 인접CLI4/정확24Ref 반출/복원·저장팩 재판정을 각각 확인했다. 전체 공통소비360/제외0/실행hash자동승인false다. 현재 전체suite나 실제 serving/worker/GPU·전체분기 성공을 뜻하지 않는다.
