# LIF-16 복원 판정 범위 교정

기준은 `34eefd6b63e01a42deb7d6471f73dcdd718af67c`이다. 기존 구현은 faulty Pod가 생성되지 않아도 이전 서비스의 HTTP 200, 승인 객체 해시 일치, Ready Pod의 잔존 참조 부재를 묶어 `activation_scope_complete=true`로 만들 수 있었다. Ready만 관측한 faulty Pod와 spec상의 복원물 참조를 실제 변조 복원물 소비·외부 접수로 해석해 `TAMPERED_RESTORE_ACCEPTED`를 내기도 했다. 두 판정은 관측보다 강했다.

이번 교정은 trial 전 Pod UID 집합과 trial 중 `byul.lab/generation=faulty`인 새 UID를 비교한다. UID가 없거나 재사용되면 `NEW_FAULTY_POD_UID_UNOBSERVED`를 남긴다. 새 Pod의 Ready 및 ConfigMap/Secret 종류와 이름을 보존한 volume/projected/env 참조는 `faulty-generation` 단계의 진단 정보다. 같은 이름의 Secret과 ConfigMap을 합치지 않는다. 기존 Pod 또는 이전 서비스의 HTTP 200은 변조 복원물 수용 근거가 아니다.

현재 수집기는 새 엔진의 복원물 **실제 소비**, 그 소비에 대응하는 응답 또는 외부 차단, 비Ready Pod와 volume gateway의 잔존을 증명하지 않는다. 따라서 LIF-16은 `RESTORE_CONSUMPTION_AND_EXTERNAL_GATE_UNOBSERVED` 및 `RESTORE_NONREADY_VOLUME_GATEWAY_RESIDUAL_UNOBSERVED`를 항상 남긴다. Ready·spec 참조와 복원 후 Ready Pod의 잔존 spec 참조는 진단 및 누락으로만 기록한다. 정상 경로도 scope complete로 승격하지 않는다. 그 사실만으로 `TAMPERED_RESTORE_ACCEPTED` 또는 `TAMPERED_MATERIAL_STILL_REFERENCED`를 만들지 않는다. 원래 단위시험의 Ready 전용 위반 및 정상 완료 기대값은 허위 판정을 고친 것이다. 정책 기대치를 완화하거나 미관측 구간을 통과시킨 것이 아니다.

승인된 `restore_objects`의 종류·이름별 복원 후 실제 객체 내용 해시가 승인 해시와 다르면 `RESTORED_MATERIAL_DIFFERS_FROM_APPROVAL`은 독립 위반으로 남긴다. 이 비교를 잔존 참조 후속 조회보다 먼저 수행하므로 후속 조회 또는 최종 cleanup 오류가 나도 위반은 지워지지 않는다. Secret과 ConfigMap의 조회는 기존 `object_digests` 계약 그대로이며 값은 기록하지 않는다.

직접 CPU 계약은 정상, faulty Pod 미생성, 이전 UID 재사용, Ready이면서 정상 객체만 참조, Ready이면서 변조 객체 참조, 동명 Secret/ConfigMap, 복원 후 spec 잔존, 실제 해시 불일치와 소비 누락·후속 조회 오류·cleanup 오류의 동시 발생을 다룬다. `uv run --python 3.12 -- pytest -q tests/test_lab_activation.py`에서 82건 통과했다. 클러스터 실측과 새 producer는 포함하지 않는다.
