# OBS02 SC23-03 기록·실제 대상 결합 후보

부모 A39/56/4·A1 19/27/2와 OBS02 원3분기·정상/FAIL/UNKNOWN·다른76개 정의 bytes를 유지한다. `observation_identity:kubernetes`0.1.0, collection `kubernetes-observation-identity-candidates-v1`, 내부 정제 투영 `guard-observation-identity-candidates/1`이다. 기존 OBS01 기록과 native8 configured-object 관측을 기존 CLI의 EVD/RES/RPT·verify·반출/복원·저장팩 정확 Ref 재판정에 연결하는 DRAFT다. 실제 승인·검사 생산자와 모델 소비/worker 세대의 독립 결합은 아직 미연결이다.

| A1 공통 설계·구현 | 사용자 결정·실제 연결 |
| --- | --- |
| 기존 OBS01 `/1` schema·enum·필드·최대64사건/65536bytes와 소유 고정 파일 reader 재사용 | 실제 승인·검사 주체, 기록 발생원·저장소·신뢰 근거 |
| 정확 ENV/target/run·source/image·model/revision/inventory와 process/worker 세대 관측을 출처별 보존 | 허용 caller/action/model·revision·파일·image, 퇴역 대상과 승인 의미 |
| 기존 load 경계·model_selection·파일 선택/bytes 관측의 제한을 유지하는 결합 투영 | 어떤 실제 검사·소비 사건을 완료로 인정할지, 독립 생산자/worker 접점 |
| 동일 recording/PID/start/clock·모델 객체 및 요청 결합 후보, 누락/중복/귀속 오류/교체·restart·후속 수집 처리 | 기업 정책 판본·효력/철회/cache·시계 오차·기한·보존 예외 |
| 기존 CLI/팩·판정·보고·정제 증거/정확 Ref 연결과 CPU·설치 회귀 | 실제 관측 대상·운영 범위·실행/복구/정리 책임 및 필요한 권한 |

기존 native8~15, OBS01 `/1`, A2/B 전용 bytes·B c9 기본 pin·외부 contracts·core/models/store/judges·루트 runtime을 보존한다. 기존 source/image/lab gate 위에서 lab=true/vLLM0.31/native8만 지원한다. native0.32, 다른 프로필과 실제 운영 생산자는 지원하지 않는다. 별도 Pod exec Python을 serving hook으로 취급하지 않으며 전자서명·감사 outbox·Gateway·새 의존성을 만들지 않는다.

공통 준비는 기존 `guard prepare-lifecycle-sensor --output <새폴더> --profile vllm-native-lifecycle-metadata/8`이다. `tools/build_kubernetes_observation_identity_pack.py --seed packs/PACK-BASELINE --output <새팩>`이 PACK31 정확 Ref에서 `PACK-KUBERNETES-OBSERVATION-IDENTITY` revision1/OBS02 check7을 만든다. 원3분기·질문·정상/위반/미확인 기준과 다른76개 정의 bytes는 같다.

```toml
[executors]
observation_identity = "kubernetes"
[adapter]
name = "vllm"
[observation_identity]
target = "/owned/target.json"
prepared = "/owned/prepared/manifest.json"
max_duration_seconds = 120
```

정책 입력 POL8은 기존 `observation_state_environment_sha256`, `observation_state_target_sha256`, `observation_state_validity_start/end`, `observation_state_required_components`와 `identity_prepared_sha256`, `identity_record_session`, `identity_record_source_sha256`이다. 앞의5값은 기존 정확 ENV/target·aware 최대600초 창·명시 구성요소 계약이다. 추가3값은 검토한 prepared 문서 SHA, 명시32lowerhex 기록 session과 예상 producer source **주장** SHA다. 관측 중 읽은 해시를 승인값으로 채우지 않는다. 이 기술 입력은 기업 허용표/승인 주체·정책 판본·효력/철회/기한을 채택하지 않는다.

실제 HTTP 전송은 기존 OBS02의 health 전후·1token 정상 JSON 전후·8token SSE 첫1byte 소유 client 종료의 닫힌5표본이다. OBS02 요청 ID를 그대로 쓰며 OBS01 ID나 다른 private 경로를 사용하지 않는다. 승인·검사·모델 적재·권한·Secret·Pod 변경 요청은 보내지 않는다. 기록은 기존 `/guard-observation/data/<명시 session>/required-events.json` 한 파일에서 실행 전·첫 정상 표본 뒤·마지막 native 수집 뒤 최대3회 수집한다. 각 읽기는 기존 nofollow/fstat/전후 파일 신원·namespace/Pod/container/image/restart/start와 5초 helper 상한을 재사용한다. 두 수집기의 기존 최대300초 설정 범위를 넓히지 않는다.

기록의 ENV Ref/target/session/source 주장은 기존 OBS01 validator로 확인한다. `APPROVAL`과 `ARTIFACT_CONSUMED`의 request·approval fingerprint·run/target/source·서로 같은 policy 주장을 **선언 간 일관성**으로만 대조한다. 같은 사건은 canonical 사건 bytes가 같을 때 중복 제거하고, 다른 사건·seq의 중복 approval/consumption은 모호함으로 남긴다. OBS01 `/1`에는 검사 enum이나 승인 모델·전체 inventory 필드가 없으므로 임의로 추가하거나 ADMIN_ACTION을 검사 완료로 바꾸지 않는다.

native 수집에서는 기존 `project_configured_models`를 재사용한다. 같은 recording의 BaseModelLoader.load_model RETURN 객체와 GPUModelRunner._model_forward 객체, 현재 요청 assignment와 source-bound process/image·recording closure를 별도로 대조한다. PID/start·boot/namespace/시계 문맥, capture bytes SHA·loader/forward seq와 기록된 module source/code를 정제 locator에 남긴다. 다른 PID/clock의 같은 hash·가까운 시각으로 RPC를 합치지 않는다. ENV inventory는 **ENV 선언값**으로 표시하며 실제 파일/tensor inventory 검증으로 올리지 않는다. A2 파일 선택/bytes 관측과 실제 tensor 소비의 차이도 유지한다.

현재 출력은 `identity_record_collections`, `identity_declared_records`, `identity_native_models`, `identity_binding_candidates`, `identity_conflict_candidates`, `identity_missing`이다. source bytes SHA/사건 seq/event fingerprint 및 native capture SHA/seq/context locator는 부모 EVD의 정확 Ref·첨부 bytes와 함께 재판정한다. 자체 EVD Ref를 첨부 안에 순환 참조하지 않는다. 원문 요청/응답·자격/자격 hash·Pod env/argv·임의 예외문은 저장하지 않는다.

producer_authenticated/approval_authority_verified/approval_model_scope_verified/inspection_verified/actual_consumption_verified/record_worker_generation_verified/record_time_order_verified/enterprise_policy_adopted/scope_complete는 모두 false다. OBS01 `/1` 사건에는 worker 세대와 producer 시각 필드가 없으므로 native의 물리 문맥·수집기 시계를 그 사건의 실제 주체·시각으로 올리지 않는다. 선언 pair 일치, 함수 반환, 선택 파일이나 bytes hash, HTTP200으로 실제 승인·최종 tensor 소비를 확인하지 않는다. 모든 판정축 UNKNOWN, 정상 대조 succeeded=null이다. 기술 수집이 COMPLETED여도 원 분기의 PASS나 전체 완료가 아니다. 잘못된 귀속·중복·정책 주장 불일치는 confirmed_violation=false 후보로 남는다. 누락·부분 수집·교체/restart·기한/시계 불확실성·seal/첨부/정리 오류와 앞선 관측·기존 독립 FAIL은 구분한다.

첫 공통 묶음은 기존 관측/reader/parser·중간 수집 hook·정식 제품 소비·팩·직접/인접/불변식·설치/Ref 검증이다. 새 실제 승인·검사 생산자 설치, 인증, inspection schema 또는 actual worker/tensor hook 확장은 후속 별도 묶음이다. 사용자가 확정할 값은 실제 승인/검사 주체와 신뢰 근거, 허용 대상/행위/주체, 정책 판본·효력/철회/기한/보존 예외, 실제 배포/관측 범위다. 기술 계약 문서를 사용자에게 다시 만들도록 요구하지 않는다.

현재 구현·회귀·문서 초안을 작성한 단계다. 고정 검증·설치본·확정 커밋/PR·실제 실증은 아직 주장하지 않는다. 원GPU창종료/Start0·기존46.66USD/과거사용량·보존120GiB EBS 과금·CI결제 차단과 실제04f7856 wheel의 과거 근거를 유지한다. 새로운 기동·기간·비용·IAM 승인은 없다.
