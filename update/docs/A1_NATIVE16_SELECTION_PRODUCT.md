# A2 native16 전용 소비의 OBS02 제품 연결 초안

A2 확정 9bda8b3d94def8da9ccb1a7d1f723c0a01568af8 / PR140의 collector·시험·문서 세 파일을 원본 bytes로 소비한다. 이 문서는 A1 공통 통합 범위이며 A2의 전용 분석을 재작성하지 않는다.

기존 observation_identity:kubernetes의 정확 native16 prepared 분기에서 공통 parser/capture가 입장을 확인한 packet을 native_weight_iteration.analyze(packet)에 전달한다. 전체 전용 projection은 summary.projection.weight_selection_iteration에, 실제 같은 recording/model·후속 forward에 앞선 사건은 identity_native_models의 configured_model_candidates[*].weight_selection_iteration_candidates에 보존한다. 기존 common weight_iteration과 독립 FAIL·신뢰 false/UNKNOWN을 유지한다. consumer의 오류는 정제 진단과 local gap으로 남기며 앞선 유효 common 사실을 지우지 않는다.

A2 SOURCE_PINS의 원래 metadata f8ced901.../sensor e7494eda...를 유지한다. native16 metadata는 b6dacb4의 원본 bytes로 복원한다. native17은 별도 공통 file_read_metadata.project_iterations(packet)에서 닫힌 native16 journal을 재사용하되 profile17 envelope를 명시적으로 선택한다. 17을 16으로 바꾸거나 gate를 통과시키는 shim은 없다. 기존 native17 FD/read 및 OBS02 기능의 회귀도 함께 확인한다.

정식 제품 연결과 CPU/설치/정확Ref 검증을 마감한 범위만 인계한다. SUP05/06 정식 등록·실제 file/FD/tensor·worker 역할·승인/검사 신뢰·전체 분기 완료를 뜻하지 않는다. native0.32/source/image/lab gate, B c9 원pin, full snapshot 검토 및 운영 비용·소유권 제한을 유지한다.

상태: source 고정 전 구현·회귀 초안. 검증은 고정 소스에서 실행하며 최초 실패와 보수를 분리해 보존한다.
