# REQ18 B 진행 기록

- 한 일: 실제 V1 InputBatch/Sampler CPU 수집, read-only native callback, Kubernetes cohort/수집/판정/Store·복원/owned 정리 경로 구현. 공통 A 파일과 팩은 수정하지 않았다.
- 검증: 고정 코드 c8bea952, 6514 passed/기존19 deselected; pytest128.69초/부모129.097초. 실제 CPU 수집1회·3 trials·42 snapshots, Store오류0·정확Ref·복원 통과. 집중 검토 지적사항 수정 확인 완료.
- 다음: 초안 PR·최종 head CI 확인·A 최소 연결 인계. CPU 결과와 wheel159파일 검증은 재사용한다.
- 막힌 것: 실제 Kubernetes profile/native hook·source/image/code pin·writer coverage·seal 연결과 CUDA ordering 계측. 실제 GPU/K8 요청0회. 공용 체크 정식 등록·판정은 A 소유다.
