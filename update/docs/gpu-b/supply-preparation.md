# GPU 공급·기동 8개 점검 준비 계약 (2026-11-02)

기준 소스는 `origin/main` 2f28942와 그 위의 B 전용 문서/PLT 보강 커밋 bdf7125이다. 이 문서는 정적 실행 준비 기록이다. AWS 작업, 실제 엔진 기동, GPU 측정, 팩·제품 코드·해시 변경은 포함하지 않는다. `LIF-01`, `SUP-06/07/10/11/15/16`은 `supply_boot:kubernetes`, `REQ-05`는 `model_authorization:kubernetes`로 등록돼 있다. 여덟 CheckDef의 질문·프로필을 그대로 따른다.

## 두 세션의 공통 입력과 사실 경계

`KubernetesSupplyBootExecutor`는 정상과 도전을 각각 `{target, prepared}`로 받는다. 두 target의 정규 JSON SHA-256과 두 prepared의 정규 JSON SHA-256, ENV Ref SHA-256, `supply_record_source_sha256`, 1시간 이하 validity 창을 POL에 미리 고정한다. 각 target은 `lab=true`, 준비물은 `guard-lifecycle-assets/1` 검증을 통과해야 한다. `prepared.session`은 서로 다른 32자리 세션 ID이며 `boot-record.json`은 각 세션의 `/guard-supply-boot/data/<session>/boot-record.json`에 있어야 한다. 동일 모델/이미지라 해도 정상과 도전 세션·target·prepared·EVD를 합치지 않는다. 실제 source owner는 전후 Pod UID·container ID·restart/image·수집 interval로 묶고, 변경 전/후의 모델 manifest/inventory·backend·드라이버·GPU·worker/rank 조건을 기록한다. 동일 환경이라는 추정만으로 대응 관계를 만들지 않는다.

읽기는 정확한 `SupplyBootReader`의 `python3 -c <고정 _FILE_READER> <record_path> <MAX_BYTES>` 호출과 owner 검증으로 제한된다. `guard-supply-boot-record/1` packet에는 `profile`, `role`, ENV Ref, target hash, policy가 고정한 source hash, `change`, `rows`가 필요하다. `change`는 `kind/applied/denied/actor_sha256/sensor_started_before_consumption`의 닫힌 집합이다. 이 reader는 엔진을 생성·변조·재기동하거나 도전을 주입하지 않는다. 현재 repository에는 **boot-record.json의 실제 엔진 사건 생산자와 안전한 GPU 도전 주입 절차가 없다**. record 안의 source SHA는 신고값이며 실행 중인 producer bytes의 독립 인증이 아니다. 임의 JSON, EXAMPLE 대역, 파일을 옮겨 놓는 행위로 GPU 실측·PASS를 만들 수 없다.

현재 `_draft`는 `supply_boot_record_authenticated=false`를 낸다. `record_attestation.py`의 별도 `byul-signed-record/1` HMAC은 설정된 기업 시스템이 발급한 사실의 서명·ENV·신선도만 검증한다. 실험실 작성 record에 키를 붙여 기업 승인·실제 엔진 사실로 승격하면 안 된다. 서명 사실은 출처·승인, native 센서는 실제 read bytes라는 서로 다른 축이다. 해당 기업 시스템과 발급 권한, 발급자 사건·대상/세대/도전 이력이 없으면 인증은 미확인이다. SUP-06·SUP-16은 현재 서명 wrapper의 `ATTESTED` 대상에도 없다.

## 점검별 행, 실제 주입, 남은 생산자

| 점검 / profile | 현재 소비하는 행·기능 | 필요한 정상·도전 및 실제 주입 경계 | 지금 없는 사실 |
| --- | --- | --- | --- |
| LIF-01 / `supply-boot-stage-budget-v1` | `stage, elapsed_ms, peak_bytes, stopped, temporary_cleaned`; POL의 단계별 ms/bytes·전체 ms와 비교 | 동일 모델 정상 기동 후 별도 경계 공급물·안전 중단 조건으로 취득·해석·변환·GPU 적재 각 단계 계측. 시간·CPU/GPU·메모리·저장량과 stop/정리를 실제 producer가 기록 | 단계 시작/끝·자원 계측/한도 집행 producer, GPU 단계 전후 trace, 안전 중단 방식 |
| SUP-07 / `supply-boot-cache-tamper-v1` | `path_sha256, writer_allowed, modified, consumed, verified_before_use` | 빈 격리 cache 정상 생성/재기동 대조. 사용 backend·AOT·vLLM/Inductor/Triton cache 경로와 rank별 생성·소비 파일을 먼저 확정한 뒤 승인 밖 계정의 무해한 교체를 **소비 전** 실시하고 거부/소비 추적 | 실제 생성자·writer 권한·consumer·검증 지점 producer 및 격리된 비승인 writer/복구 절차 |
| SUP-10 / `supply-boot-plugin-v1` | `plugin_loading.project`의 10개 `case_id`, `status, target_started, event_collection_status, events(plugin, phase)`; general/endpoint/logits/io별 제한을 별개 판단 | 승인 plugin 정상 기동·호출과 무해 비승인 plugin의 발견→import→factory/hook→실제 GPU worker 호출 또는 선행 거부를 종류별 세션에서 대조. 실제 설치 entry point와 `VLLM_PLUGINS` 설정을 고정 | GPU worker logits hook 등 종류별 실제 event 계측·허용 목록의 기업 승인·주입 fixture. logits에는 동일 필터 보장을 가정하지 않음 |
| SUP-11 / `supply-boot-python-mode-v1` | `dynamic_code_cpu.project`의 autoconfig/vllm-config × trust × normal/O/OO 12개 `case_id`, identity, before/after, import/constructor events | 동일 허용 목록·승인/비승인 무해 모듈을 지원 모드별 **별도 엔진 세션**에 설치. 각 모드에서 정상 모듈 로드와 거부를 전후 대조, assertion 제거 여부 관측 | 현재 GPU 이미지가 실제 지원하는 모드 목록, 모드별 worker import 전후 producer·안전한 모드 전환 절차 |
| SUP-15 / `supply-boot-cache-reuse-v1` | `path_sha256, relevant_change, reused, compatibility_verified` | 같은 모델/도구/옵션 정상 재사용 후 관련 조건 하나만 변경한 별도 기동에서 key·lookup·실제 소비/재생성 대조. 무관한 설정 변경도 정상 대조 | 실제 GPU backend·cache key·consumer·호환성 기준 및 변경 주입·추적 producer |
| SUP-16 / `supply-boot-partial-artifact-v1` | record 행 전용 evaluator 없음; native17 선언 파일 read의 실제 bytes mismatch는 양성 위반으로 투영 | 격리된 생성의 정해진 단계에서 안전 중단 후 부분 파일·완료 표시를 보존, 별도 재기동에서 실제 소비/재생성/거부와 정상 기능 확인 | 다운로드·변환·compile별 완료 표시 규칙, 중단/복구 producer, GPU compile 부분 생성물의 실제 소비 연결. native17 파일 read만으로 전체 생성물 완결성은 미증명 |
| SUP-06 / `supply-boot-worker-shard-v1` | `rank, required_missing, ready_accepted`; native17의 선언 safetensors read bytes 후보 | 승인 manifest와 TP rank별 필수 shard/tensor 기준을 먼저 고정. 완전 checkpoint 정상 기동과 필수 tensor 누락 도전 기동을 분리하고 worker/rank별 최종 로드·후처리·ready 수용을 대조 | rank별 실제 메모리 적재/필수 tensor·양자화/redirect/원격 weights lineage producer. FD read만으로 적재는 증명 못 함 |
| REQ-05 / `kubernetes-model-authorization-observation-v1` | 단일 base-model alias의 allowed→denied→allowed 3회 `/v1/chat/completions`와 기존 native 후보; final model/retirement scope 미확인 | 활성 endpoint·selector별 허용/거부 principal, alias/adapter/LoRA 및 퇴역 revision의 최종 선택·실행 모델을 인증 주체와 결합. 사용 종료 후 다른 활성 모델 정상 대조 | 최종 GPU model/adapter/revision lineage 및 기업 사용 권한·퇴역 정책·최종 권한 집행/서명 producer |

SUP-10의 현재 CPU 행은 일반 plugin의 `general_hook`, endpoint의 factory/router/handler, IO factory/pre/post 등을 구별한다. CPU 행을 GPU worker 호출로 이름만 바꾸지 않는다. SUP-11의 현재 12행은 지원 모드 확인 전의 로컬 형식이며 모드가 실제 GPU image에서 지원되지 않으면 그 모드의 실측을 주장하지 않는다.

## native17·모델 범위와 기록 source SHA 결합

SUP-06/16은 `NATIVE_CHECKS`이며 `capture_session` 이후 `native_model_file_reads.analyze(packet, prepared)`를 사용한다. 준비 명령의 형태는 `prepare-model-read-scope --env ENV --target TARGET --model-manifest MANIFEST --model-root ROOT --output SCOPE --nonsecret-model-weights`로 scope를 만들고, `prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/17 --model-read-scope SCOPE --output ASSETS`로 **각 세션** 자산을 만든다(실제 CLI 인자와 제한은 실행 전 `--help`로 다시 확인). `guard-native-model-read-scope/1`은 ENV Ref·target hash·model/revision/inventory·manifest bytes hash·정규화 경로 hash/참조 bytes hash를 묶는다. 비밀 모델을 임의로 스캔하지 않으며 `nonsecret_model_weights=true`가 전제다. native17 `guard-native-lifecycle-events/17`·`guard-native-file-read/1`의 동일 PID/start·recording·model/loader/read-return과 범위를 결합한다. 경로 해시만으로 동일 모델·텐서 적재·완전한 worker 범위를 주장하지 않는다. 생성된 `prepared.source_sha256`/asset별 bytes SHA와 실제 이미지에 설치된 소스 bytes를 별도 대조한다.

`boot-record.json` 생산자가 생기면 소스 파일의 실제 bytes SHA-256, 설치된 이미지 digest와 실행 argv, 세션·역할·target/prepared hash를 사전 고정하고 `supply_record_source_sha256`에 그 **검증된 생산자**를 넣어야 한다. 현재는 그런 생산자가 없으므로 임의 값이나 시험용 `c*64`를 넣어 실행 준비 완료로 표시할 수 없다. 정상/도전별 `change.applied`, 선행 센서 시작, 기록 시각, 실제 소유 세대·파일 출처 및 후속 수집/정리 오류를 보존한다.

## 실행 전 확인과 판정 한계

필수 입력은 기업 승인 예산·목록·호환 기준·서명 발급자, 선정 GPU 이미지·모델 manifest/TP 구성, 무해 도전/복구 절차, 정확한 두 세션 target/prepared, 실제 producer source/설치 bytes, native17 비밀 아닌 모델 read scope다. 각각 없으면 해당 분기를 UNKNOWN으로 둔다. record의 고정 필드·hash 일치는 위조 방지나 승인 증명이 아니다. 독립적인 실제 위반이 확인되면 후속 기능·정리 실패와 구분해 보존하되 선행 사실을 가공하지 않는다. 정상/도전이 실제 GPU worker에 도달하고 전후 동일 신원·모델 소비·기업 정책이 검증될 때만 해당 범위의 GPU 측정으로 보고한다.
