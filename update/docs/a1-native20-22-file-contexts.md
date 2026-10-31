# native20~22 공통 파일 투영 / 1

대상은 OBS02 SC23-03의 공통 사실·모델 귀속과 A2 요청 SUP05/Q-R05-04/SC10-01·SUP06/Q-R05-14/SC10-04·SUP06/Q-C13/SC16-03이다. A2 원본 전용 소비·분석·판정과 source pin 갱신은 A2가 맡는다. 이 묶음은 기존 OBS02 정식 실행기에서 같은 공통 자료를 소비하도록 연결한다. 승인·검사 생산자, 실제 worker 귀속과 tensor 소비는 별도 미연결 범위다.

## 공개 인터페이스

`guard.adapters.vllm_generation_file_metadata.project_file_contexts(packet, prepared)`

- 공개 판본 `0.1.0`, schema `guard-native-generation-file-contexts/1`.
- 닫힌 profile 집합: `vllm-native-lifecycle-metadata/20`, `/21`, `/22`. 기존 native19 공개 `project_iterations(packet)`·`project_mappings(packet, prepared)`는 기본 profile19 필터와 반환 의미를 유지한다. 새 packet의 profile/schema를 바꾸거나 native19로 relabel하지 않는다.
- 입력은 기존 `capture_session`이 source/code/image/lab/ENV·대상·run·세션·프로세스/파일 결합을 검사한 packet과 **동일한** `load_prepared` 결과다. 임의 JSON을 신뢰 입력으로 승인하는 API가 아니다. 기존 reader/prepare의 한도를 재사용하고 recording 수는 기존 `lifecycle_session.MAX_FILES`와 같은 32 이하로 제한한다. 시간·수집 한도는 기존 제품 정책/reader가 담당한다.
- 공개 함수는 명시 profile/schema/session, PID/start 형식과 준비된 nonsecret scope의 정확 ENV Ref·target/model/revision/inventory/manifest bytes 해시를 검사한다. 실제 ENV·target과 scope의 결합 및 센서 자산 검토는 기존 `validate_binding`·reader·정식 OBS02 preflight가 담당한다. 새 승인값을 런타임 관측 해시에서 만들지 않는다.

## 반환과 귀속

`recordings`는 누락된 항목도 원 인덱스를 보존한다. 자료가 있는 항목은 PID/start, image/process 결합, 동일 process generation 중복과 각 투영 단계 상태를 제공한다. `projected`는 함수 단계가 반환됐다는 뜻이다. 전체 수집·정책·worker 검증 완료가 아니다. 같은 PID/start가 여러 recording에 나타나거나 process 결합이 부족하면 자료는 남기되 결합 기반 후보는 비활성화한다. 기존 Kubernetes reader의 API 이미지 결합과 별도의 node/runtime 이미지 검증을 분리한다. node/runtime 검증 false는 프로세스 관측을 지우지 않으며, 파일·root 후보의 `image_binding_verified`를 false로 유지한다.

`startup_generation`은 기존 해당 profile의 공개 MP/startup/generation/endpoint 투영을 재사용한다. native22의 전체 recording 간 endpoint 후보 계산도 보존한다. 각 recording의 독립 부분 투영을 별도로 유지하여 한 recording의 오류가 다른 관측을 지우지 않게 한다. 공통 worker/startup journal의 파일·root 사실을 별도 `model_candidates`로 제공하며 PID/start·세션 결합을 공통 모델 연결에서 사용한다. 기존 endpoint 투영의 더 강한 node/runtime 이미지 조건과 그 false 값은 원 의미대로 보존한다. 파일·root 후보를 검증된 endpoint/worker로 승격하지 않는다.

`weight_iteration`은 기존 공통 journal을 재사용한다. root FACTORY·model/loader/call·terminal/close와 원 source 이벤트를 유지한다. source locator 목록의 기존 64개 한도와 별도로 **전체** 사실의 최소·최대 Ref인 `first_source_event`·`last_source_event`를 제공한다. 잘린 목록의 마지막 항목을 전체 상한으로 사용하지 않는다.

`safe_open_mappings`도 기존 정제 journal을 재사용한다. 세션에 묶인 file/scope/context/operation/source 정보와 recording/PID/start/image 결합, context의 첫/마지막 mapping Ref를 반환한다. 원 maps·주소·파일 내용·텐서 값·예외 원문을 추가하지 않는다. mapping·파일 선택·함수 반환을 실제 read/tensor/backend 적용이나 승인으로 승격하지 않는다.

`scope_binding`은 기존 준비된 ENV/target/model revision/inventory/manifest bytes와 자료 출처·nonsecret 선언의 식별값이다. 정책 승인 기록이나 실제 신뢰 증거가 아니다. worker/승인/최종 tensor/전체 coverage의 truth 필드는 false를 유지한다.

## 오류와 제품 소비

지원하지 않는 profile과 잘못된 외부 입력·scope는 닫힌 `GENERATION_FILE_CONTEXT_*` 오류 코드로 거부한다. recording 내부의 profile/schema/session·결합 오류, 누락·교체/restart·중복 및 단계별 parser 오류는 원 인덱스와 정제 진단으로 남긴다. 다른 recording의 유효 사실과 같은 recording의 독립적으로 성공한 단계는 보존한다. 각 부분 missing/diagnostics도 함께 소비해야 하며, 빈 목록을 완전한 0회 관측으로 해석하지 않는다.

기존 OBS02 실행기는 `/20`~`/22`를 명시적으로 허용하고 공개 투영을 report/EVD의 기존 configured model 경로와 연결한다. 같은 recording·PID/start·model·root FACTORY와 startup 시작/종료/링크, loader context와 **전체** weight 상한이 모두 해당 forward 이전인 경우에만 공통 후보를 첨부한다. 후속 수집·첨부 오류는 ABORTED/UNKNOWN과 정제 진단으로 남기고 앞선 유효 관측을 보존한다. 전용 SUP05/06 분석이나 기업 정책 판정은 구현하지 않는다.

기존 CLI `select → run → judge → report → verify → evidence export/restore → 저장팩 정확 Ref 재판정`을 사용한다. 새 bypass CLI·외부 contracts·core/models/store/judges·루트 runtime 변경은 없다. 기존 source/image/lab gate, native0.32 미지원, 원 GPU 승인창 종료와 Start0·비용/소유권 제한을 유지한다.

## 검증 계획과 미완료

초안 종료 후 소스와 bytes hash를 고정한다. 직접 검증은 세 profile의 정상·부분·누락·다른 귀속·중복·restart·잘못된 metadata·전체 상한·scope 거부와 입력 불변을 확인한다. 제품 검증은 세 profile의 정상·부분·후속 수집·첨부·다른 모델 사례를 기존 CLI·정확 Ref 왕복으로 확인한다. 기존 native19 공통 계약과 관련 parser/등록/불변식을 인접 검증한다. 설치본·정확 HEAD CI5와 A2/B 소비 검토 pin은 각각 별도로 인계한다.

EXAMPLE 계측 fixture는 고정된 원본 native 함수 본문을 사용하지만 serving·Torch·C decoder·worker·권한은 모의 namespace다. library facade는 실제 관측기 code admission 후에만 대체하며 EXAMPLE로 기록한다. 실제 serving/backend/GPU·신뢰 생산자·승인·전체 분기 완료로 집계하지 않는다. 새 SDK/provider/취득 stage/resource 계측은 후속 독립 묶음이며 LIF12 요청 retry를 취득으로 바꾸지 않는다.
