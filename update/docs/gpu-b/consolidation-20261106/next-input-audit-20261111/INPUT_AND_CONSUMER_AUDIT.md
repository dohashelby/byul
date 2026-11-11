# B03 입력과 B02 연결 잔여 — 2026-11-11


운영 최신 상태는 [SUP-16 실제 관측 정본](../SUP16_ACTUAL_OBSERVATIONS_20261111.md)을 따른다. GB12 전체 회수·SHA·최초1971 CRC·정지는 완료했고, B pre-seal은 CPU 준비·한정 검토 완료다. 아래 정의/계약 대조의 과거 source 기준을 새 실측 성공으로 바꾸지 않는다. 새 GPU·추가 실측은 현재 배정하지 않는다.
기준 `88b336ed`·PACK33 DRAFT. 이 문서는 정본 실측 결과가 아닌 고정 소스 대조다.
기존 선정 7개+PLT-01은 재시험하지 않았다. 나머지 **32개**의 요구를 실제 CheckDef에서 추출했다.
정상 probe·도전 단계·정책 타입·필수 관측·판정 조건·factory 원문/행/SHA는 동봉 JSON에 있다.
미정 정책값·현재 target/prepared는 null이다. 요구사항 추출을 실행 입력 완성으로 집계하지 않는다.

## B03 비TP2 40개 입력 요구

|점검|실행기|기존 선정 입력 준비|정책 입력 키|실제 실행에 필요한 범위|
|---|---|---|---|---|
|PLT-08|`api_transport`|추가 구체화 대상|api_hop_scope, api_container_id, api_network_id, api_endpoint_id, api_ip, api_port, plaintext_forbidden, validity_start, validity_end|기업 TLS 입력 부재 사유와 정본 실행의 UNKNOWN 기록|
|SUP-03|`template_validation`|있음(한정 범위)|allowed_template_sha256|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|SUP-08|`input_validation`|있음(한정 범위)|code_selection_fields, code_selection_allowed, rejection_codes|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|REQ-08|`input_validation`|있음(한정 범위)|reused_token_field, reused_token_allowed, rejection_codes|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|REQ-09|`input_validation`|있음(한정 범위)|max_input_tokens, rejection_codes, allowed_endpoints, allowed_input_formats|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|REQ-10|`sampling_validation`|있음(한정 범위)|allowed_sampling_options, rejection_codes|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|REQ-07|`template_validation`|있음(한정 범위)|allowed_template_sha256, max_rendered_input_tokens, request_template_override_allowed|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|SUP-13|`template_validation`|있음(한정 범위)|forbidden_template_attributes|정상/거부 request와 native 기록 대응, processor/sampler/renderer 소비, tokenizer/template 고정값|
|LIF-03|`stream_buffer`|추가 구체화 대상|buffer_environment_sha256, buffer_target_sha256, buffer_prepared_sha256, buffer_validity_start, buffer_validity_end, buffer_max_write_bytes, buffer_max_process_rss_bytes, buffer_max_send_wait_ms, buffer_release_deadline_ms, buffer_read_chunk_bytes, buffer_read_interval_ms, buffer_read_max_reads, buffer_close_chunk_bytes, buffer_close_interval_ms, buffer_close_max_reads, buffer_debug_mode|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-04|`stream_buffer`|추가 구체화 대상|buffer_environment_sha256, buffer_target_sha256, buffer_prepared_sha256, buffer_validity_start, buffer_validity_end, buffer_max_write_bytes, buffer_max_process_rss_bytes, buffer_max_send_wait_ms, buffer_release_deadline_ms, buffer_read_chunk_bytes, buffer_read_interval_ms, buffer_read_max_reads, buffer_close_chunk_bytes, buffer_close_interval_ms, buffer_close_max_reads, buffer_debug_mode|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-05|`lifecycle_transition`|추가 구체화 대상|transition_inputs_sha256, transition_before_at, transition_during_at, transition_after_at, transition_recovered_at, transition_before_generation, transition_during_generation, transition_after_generation, transition_recovered_generation|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-06|`lifecycle_transition`|추가 구체화 대상|transition_inputs_sha256, transition_before_at, transition_during_at, transition_after_at, transition_recovered_at, transition_before_generation, transition_during_generation, transition_after_generation, transition_recovered_generation|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-07|`initialization`|추가 구체화 대상|init_environment_sha256, init_target_sha256, init_prepared_sha256, init_validity_start, init_validity_end, init_executor_backend, init_expected_ranks, init_scenario_sha256, init_phase, init_required_stages, init_failure_rank, init_failure_stage, init_preparation_deadline_ms, init_recovery_deadline_ms, required_readiness_items|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-09|`initialization`|추가 구체화 대상|init_environment_sha256, init_target_sha256, init_prepared_sha256, init_validity_start, init_validity_end, init_executor_backend, init_expected_ranks, init_scenario_sha256, init_phase, init_required_stages, init_failure_rank, init_failure_stage, init_preparation_deadline_ms, init_recovery_deadline_ms|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-10|`request_termination`|추가 구체화 대상|termination_environment_sha256, termination_target_sha256, termination_validity_start, termination_validity_end, termination_max_delay_ms, termination_required_scopes|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-12|`request_termination`|추가 구체화 대상|retry_environment_sha256, retry_target_sha256, retry_scope_sha256, retry_policy_sha256, retry_record_source_sha256, retry_session, retry_validity_start, retry_validity_end, retry_policy_mode, retry_max_record_delay_ms|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-14|`profiler_observation`|추가 구체화 대상|profiler_environment_sha256, profiler_target_sha256, profiler_prepared_sha256, profiler_validity_start, profiler_validity_end, profiler_backend_kind, profiler_required_ranks, profiler_case, profiler_control_scope, profiler_control_owner_ref|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|LIF-16|`lifecycle_transition`|추가 구체화 대상|transition_inputs_sha256, transition_before_at, transition_during_at, transition_after_at, transition_recovered_at, transition_before_generation, transition_during_generation, transition_after_generation, transition_recovered_generation|전·중·후 request/attempt, worker PID/start·세대, trigger ACK, 정상 canary, phase/정리 기한|
|OBS-02|`observation_state`|추가 구체화 대상|observation_state_environment_sha256, observation_state_validity_start, observation_state_validity_end, observation_state_required_components, identity_prepared_sha256, identity_record_session, identity_record_source_sha256, profiler_prepared_sha256, profiler_backend_kind, profiler_required_ranks, identity_target_sha256, profiler_target_sha256|API/worker/profiler별 session·인과 ID, 원 trace/출력의 시작·종료·누락·사본 범위|
|OBS-03|`log_analysis`|추가 구체화 대상|allowed_log_fields, forbidden_log_fields, forbidden_marker_contexts, required_sources, required_branches|API/worker/profiler별 session·인과 ID, 원 trace/출력의 시작·종료·누락·사본 범위|
|REQ-12|`request_termination`|추가 구체화 대상|attempt_environment_sha256, attempt_target_sha256, attempt_validity_start, attempt_validity_end|API/worker/profiler별 session·인과 ID, 원 trace/출력의 시작·종료·누락·사본 범위|
|PLT-01|`container_config`|있음(한정 범위)|allowed_capabilities, allowed_mounts, allowed_devices, require_non_root, allowed_exec_writable_paths|허용/비허용 actor, UID/GID, namespace/cgroup, mount/device/IPC 실체와 실제 접근 결과|
|PLT-02|`unix_ipc`|추가 구체화 대상|protected_unix_paths, allowed_ipc_uids|허용/비허용 actor, UID/GID, namespace/cgroup, mount/device/IPC 실체와 실제 접근 결과|
|PLT-04|`credential_exposure`|추가 구체화 대상|forbidden_env_names, forbidden_mount_paths, credential_candidate_paths|허용/비허용 actor, UID/GID, namespace/cgroup, mount/device/IPC 실체와 실제 접근 결과|
|PLT-06|`unix_ipc`|추가 구체화 대상|worker_isolation_requirement, worker_trust_group_markers|허용/비허용 actor, UID/GID, namespace/cgroup, mount/device/IPC 실체와 실제 접근 결과|
|REQ-05|`model_authorization`|추가 구체화 대상|model_environment_sha256, model_target_sha256, allowed_principal_sha256, denied_principal_sha256, selected_model_grants, retired_model_revisions, model_validity_start, model_validity_end|허용/비허용 actor, UID/GID, namespace/cgroup, mount/device/IPC 실체와 실제 접근 결과|
|SUP-04|`container_config`|추가 구체화 대상|approved_image_digests|새 PACK34/POL과 현재 target/prepared, 과거 lab와 정확 Ref 구분|
|PLT-09|`api_transport`|추가 구체화 대상|record_transport_segments, record_tls_required_segments|새 PACK34/POL과 현재 target/prepared, 과거 lab와 정확 Ref 구분|
|SUP-07|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-09|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-10|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-11|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-12|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end, supply_allowed_readers|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-15|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|PLT-03|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end, supply_allowed_objects|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|PLT-05|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end, supply_allowed_identities|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|LIF-01|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end, supply_stage_budget_ms, supply_stage_budget_bytes, supply_total_budget_ms|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|LIF-02|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end, supply_max_attempts, supply_max_fetch_bytes|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-05|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|
|SUP-16|`supply_boot`|추가 구체화 대상|supply_environment_sha256, supply_normal_target_sha256, supply_challenge_target_sha256, supply_normal_prepared_sha256, supply_challenge_prepared_sha256, supply_record_source_sha256, supply_validity_start, supply_validity_end|승인 집합, image/model/file 고정값, 실제 읽기·소비, 기동 세대, 도전용 소유 사본·복구|

## B02 전체 20개 대조

기록 저장·완결성 제한과 확정 위반 관측을 구분한다. 수명주기·관측·플랫폼 묶음 15개 중 14개는 관측/gap 단계이고 REQ-12는 계약 미선언 거부다. 이를 모두 B 코드 결함으로 판정하지 않는다.
REQ-12는 공통 계약 미선언으로 소비가 거부된다. 신원/실제 소비가 없는 후보를 FAIL로 만들거나 범위를 좁혀 PASS를 만드는 수정은 하지 않았다.

|점검|공통 기록 종류|현재 연결|기존 판정에 전달하는 관측/한계|
|---|---|---|---|
|LIF-03|request-attempt|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-05|generation-transition|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-06|generation-transition|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-07|generation-transition|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-09|generation-transition|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-10|request-attempt|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-11|failed-group-resources|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-12|request-attempt|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|LIF-14|profiler-control|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|REQ-12|request-attempt|A 계약에 점검 ID 미선언: 소비 거부|판정 관측 전달 전 거부|
|OBS-02|health-worker-causal|관측 사실·gaps·완결성 제한|`activation_scope_complete`|
|OBS-03|profiler-control|관측 사실·gaps·완결성 제한|`log_coverage_complete`|
|PLT-01|process-device-provider|관측 사실·gaps·완결성 제한|`process_scan_complete`|
|PLT-02|ipc-ownership|관측 사실·gaps·완결성 제한|`ipc_scope_complete`|
|PLT-04|process-device-provider|관측 사실·gaps·완결성 제한|`process_scan_complete`|
|REQ-08|runner-consumption|기존 판정 관측 연결|`reused_input_violations`|
|REQ-09|runner-consumption|기존 판정 관측 연결|`confirmed_input_violations`|
|REQ-10|runner-consumption|기존 판정 관측 연결|`runner_consumption_verified`|
|SUP-03|supply-reads|보조 기록·소비 확인 미완료|`artifact only; actual_consumer_verified=False`|
|SUP-06|worker-shard-load|기존 판정 관측 연결|`worker_shards violations/missing → existing supply boot consumer`|

## REQ-07·SUP-13 backend 확인

두 점검 모두 `template_validation:real`과 `:kubernetes` factory가 있다. 실제 생성자와 기존 정책 검사를 통해 Kubernetes 쪽은 SUP-03 전용이라 `KUBERNETES_TEMPLATE_PROFILE_UNSUPPORTED`로 거부됨을 확인했다.
따라서 SUP-03 Kubernetes target을 복사하지 않고 소유 Docker target을 준비한다. 실제 Docker 접근/엔진 호출은 하지 않았다.
필수 결합: engine.container·trusted_url·run_id, image digest, 실제 model/revision, readonly template·argv·전후 identity, probe image/network.
REQ-07은 정책의 token 경계/override 허용값을 받아 정상→경계→초과→override→정상 대조를 준비한다.
SUP-13은 권한자가 `forbidden_template_attributes`를 반환해야 한다. 현재 제한 프로필 지원은 `__class__`이고, 이를 기업 허용값으로 자동 입력하지 않는다.
복구는 기존 템플릿 덮어쓰기 대신 도전 전용 사본/실행 세대를 회수하고 원 고정 템플릿과 정상 canary를 확인한다.
