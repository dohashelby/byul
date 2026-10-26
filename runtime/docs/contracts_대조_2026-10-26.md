# contracts 대조 기록 (2026-10-26)

이 문서는 DRAFT인 `contracts/` 0.x에 대한 runtime 측 확인 기록이다. 계약 상태·schema 판본을 확정하거나 다른 담당자의 동의를 대신하지 않는다.

## 방법과 결과

scratch runtime을 mock engine으로 띄워 ALLOWED·DENIED·INVALID·ERROR(`POLICY_APPROVAL_UNVERIFIED`)·401 요청을 만들고, 합성 Hubble·Tetragon feed와 LAB 보고서를 주입했다. platform `LiveRuntimeSource`로 runtime 응답을 계약 형식으로 변환한 뒤 `contracts/schemas/`로 검증했다.

- `runtime-event`: schema 오류 0건
- `runtime-status`: schema 오류 0건
- `runtime-lab-report`: schema 오류 0건
- `runtime-request-detail`: schema 오류 0건
- `revalidation-request`: 아래 재검증 확인에서 생성·변환·schema 검증

## 일치 확인

- Gateway 결과 `ALLOWED`·`DENIED`·`INVALID`·`ENGINE_REJECTED`·`ERROR`와 final 없는 `INCOMPLETE`→`unknown` 변환이 일치했다.
- runtime의 exec 판정 7개(`allowed_in_phase`, `exception`, `outside_allowlist`, `suspect_binary`, `exec_from_cache`, `phase_unknown`, `phase_signal_unverified`)가 모두 계약 변환표에 포함된다.
- Hubble 사건은 verdict와 무관하게 `observed`이고 `enforcer`가 되지 않는다.

## runtime 결정

- Tetragon file_access는 enforce면 `enforce_attempted`다. monitor에서 `TG-ASSET-01`·`TG-ASSET-02`·`TG-SECRET-01`은 보호 경로 접근이므로 `detected`다. `TG-CACHE-01`은 `cache_generation=current`면 `observed`, `other`·`outside`면 `detected`, 값이 없으면 `unknown`이다. 알 수 없는 action·rule도 `unknown`이며 enforcer는 `tetragon`을 유지한다.
- runtime `data_origin=declared`는 manifest 선언값이므로 `DECLARED`, `fake_backend_test`는 시험용 backend 결과이므로 `EXAMPLE`이다. 계약 공통 등급은 세 값을 모두 허용한다.

## runtime 개수 값 전송 확인

runtime은 부모 객체가 있으면 개수 필드를 항상 전송한다. 다만 자산 root가 없거나 디렉터리가 아니어 순회하지 못한 경우의 `extra_file_count`·`extra_symlink_count`·`walk_error_count`, 정책이 없는 경우의 `passthrough_count`·`validate_count`는 기존에 미측정을 0으로 보내던 두 곳이었고 runtime 결정(2026-10-26)으로 `null`로 바꿘다.

contracts는 platform이 변환하는 자산 상세의 `extra_files`·`extra_symlinks`·`walk_errors`와 정책의 `validate_count`에만 `null`을 허용한다. platform도 이 네 필드만 `_int`로 전달하고, runtime이 항상 보내는 나머지 개수에는 `_nonneg`를 그대로 유지한다. 계약에는 `passthrough_count` 필드가 없고 `passthrough_fields`만 있다.

## revalidation-request 확인

scratch runtime을 `examples/mvp/policy.json` 복사본으로 시작한 뒤 같은 data dir에서 같은 `policy_id`·`policy_version`을 유지하고 내용만 `output_max_tokens: 64`로 바꿔 재시작했다. runtime은 `policy_content_changed` trigger의 재검증 요청 1건을 만들었다. 같은 내용을 유지한 채 한 번 더 재시작한 뒤에는 그 요청의 `last_event`가 `condition_cleared`, `status`가 `open`, `current_evidence_present`가 `false`였다.

platform `LiveRuntimeSource` 변환 결과는 `byul-revalidation-request/0.2`였고, `changed_fields`는 `policy.sha256`의 이전 값(`expected`)과 현재 값(`observed`)을 담았다. `event`는 `condition_cleared`, `data_origin`은 `MEASURED`, `origin_detail`은 `local_file`이었다.

- `revalidation-request` schema 오류 0건
- 같은 실행에서 `runtime-status` schema 오류 0건

이번 확인에서 실제로 일으킨 trigger는 `policy_content_changed`뿐이다. `asset_changed`와 `config_drift`는 같은 변환 경로를 쓰지만 이번 scratch 실행에서는 일으키지 않았다.
