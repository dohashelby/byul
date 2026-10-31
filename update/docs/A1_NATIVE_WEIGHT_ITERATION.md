# OBS02와 가중치 소비의 공통 iterator 계약 초안

대상은 OBS02 SC23-03의 기존 `observation_identity:kubernetes` 제품 경로와 A2 SUP03/05/06의 후속 전용 분석이다. 부모 A39/56/4, A1 19/27/2, A2 20/29/2와 원래 완료 기준을 유지한다. 이 묶음은 원본 B PR130을 통합한 `3c2f1bb`에서 이어간다. 공통 구현·기존 제품 연결과 고정 소스 CPU/설치 검증을 마쳤다. 새 판본의 실제 serving/worker 실증과 전체 분기 완료는 미확인이다.

고정 대상 소스는 vLLM `db9527a46873454610df6dbedf79a36d6bf1a7f6`이다. 보존된 이미지 layer `30530efd7061ec4f5a1a5f179219c218fc72c486dd215339940b33f7455eb26b`의 source manifest와 5파일/21함수를 대조했다. 실제 hook은 `default_loader.py` SHA `d17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9`, `weight_utils.py` SHA `7d2573931b9684147b17018c1f6de318eea1c105556ffea03154201a72418afd`, `models/utils.py` SHA `7171b6347b3cbab213df299fa317094ac6c9b2b397daf6a2ec93ff3a152b0e75`의 다섯 generator 지점이다. 원본 SPDX·copyright를 시험 참고 파일에 보존한다. 이 출처 확인은 현재 실행 중인 이미지·worker 인수 근거가 아니다.

## A1이 구현하는 사건과 결합

별도 opt-in profile `vllm-native-lifecycle-metadata/16`, recording schema `guard-native-lifecycle-events/16`, 사건 `guard-native-weight-iteration/1`을 사용한다. 기존 native8~15 sensor bytes와 판본 의미는 유지한다. native16은 원본 native8 private base SHA를 확인해 로드하고 CPython 3.12.14/3.12.3의 검토된 observer 코드 및 대상 method 코드를 검사한다. 모델 loader의 decorator 반환을 실행 증명으로 취급하지 않고 실제 generator 구간을 대상으로 삼는다.

| 지점 | 관측 의미 | 입증하지 못하는 사실 |
| --- | --- | --- |
| `AutoWeightsLoader._load_module` | 해당 모델 객체에서 생성된 iterator 구간·진행·종결 | 전체 모델 준비, worker/rank 배정, tensor inventory 충족 |
| `DefaultModelLoader.get_all_weights` | 실제 next/send 진행 중 하위 iterator를 소비함 | 모든 checkpoint 파일 읽기·완전한 공급원/분기 coverage |
| `weight_utils.pt_weights_iterator`, `safetensors_weights_iterator` | 함수 본문의 실행과 `(name, tensor)` yield 관측 | 실제 FD/read/mmap 범위, 물리 bytes, tensor 상주/최종 값 |
| `AutoWeightsLoader._load_param` | 고정 본문에서 선택한 parameter callback 반환 후 이름을 yield함 | 동적 callback 구현의 신뢰, 비동기 GPU copy 완료, 최종 저장 상태 |

필수 사건 필드는 `vllm_weight_iteration_metadata.FIELDS`의 닫힌 집합이다. 출처 module/bytes hash/method code hash/observer runtime·code hash, call/parent/root ID, 모델·parameter·yielded tensor 객체의 세션 내 opaque ID, 원 native8 `weight_call_sha256`과 같은 `loader_call_sha256` 및 `loader_model_sha256`, operation/yield 순번, 작업·시각·종결 상태를 포함한다. 원문 파일 경로·tensor 이름·값·요청·응답·env/argv·credential·임의 예외 원문은 포함하지 않는다. tensor 이름은 비밀이 아닌 시험 공급물의 경계 내 hash로만 남기며 credential hash에는 사용하지 않는다.

`FACTORY`는 generator 객체 생성이다. `STEP_BEGIN` 뒤 `STEP_YIELD`, `STEP_STOP`, `STEP_RAISE`, `STEP_CLOSED`를 구분하고 NEXT/SEND/THROW/CLOSE를 기록한다. 닫힌 generator와 끝까지 소진된 generator는 다르다. 생성 후 미소비/미시작 close, 부분 소비 후 close, callback 오류를 모델 소비 완료로 올리지 않는다. 수집 오류가 원래 next/send/throw/close의 반환·예외를 바꾸지 않도록 하고 출력 bound 이후 원래 작업은 계속한다.

`root_load_sha256`은 이 generator 구간의 후보 ID다. 전체 모델 load 세대나 실제 worker 세대의 인증된 ID가 아니다. `loader_call_sha256`은 동일 private native8의 `DefaultModelLoader.load_weights` frame이 실제로 있는 경우만 재사용한다. frame·모델 객체가 없거나 다른 객체이면 결합을 만들지 않는다. ContextVar는 각 iterator operation 동안만 유지하고 yield 뒤 해제한다. 다른 PID로 상속된 binding은 관측에 재사용하지 않는다. 파일 선택 관측의 기존 `weight_call`과 모델 객체가 같은 프로세스/세션에 있을 때만 후속 A2 분석이 대조할 수 있다.

ENV Ref, target/run/session, namespace/Pod/container/image/restart/start는 기존 prepared·Kubernetes lifecycle 수집 경계를 재사용한다. 실제 process PID/start/boot/pid namespace/time namespace와 같은 recording 내 sequence를 검증한다. ENV의 model revision/inventory, API alias, 설정된 모델 객체, 실제 파일 읽기와 tensor 적용은 별개로 남긴다. 강한 node image 검사를 완화하지 않고 native0.32는 지원하지 않는다.

## 제품 소비와 오류

기존 `observation_identity:kubernetes`에 native16을 명시적으로 추가한다. 기존 OBS01 기록 수집, POL8, 정상/연결 종료/후속 정상 HTTP 절차, CLI 선정·실행·EVD·RES·RPT·verify·반출/복원·저장팩 정확 Ref 재판정을 재사용한다. 새 CPU 제품이나 새 Store 객체를 만들지 않는다. native16의 검증된 step journal을 같은 process/model 객체와 연결한 `identity_native_models[*].configured_model_candidates[*].weight_iteration_candidates` 및 보고서의 `weight_iteration`에 소비한다. native8 입력에는 새 후보를 덧붙이지 않는다.

누락 factory/operation 짝, 미종결 recording·iterator, bound·부분 수집은 missing으로 보고한다. 중복 operation, 불일치 call/root/모델·source/code·시각·판본, 추가 민감 필드는 거부한다. 다른 프로세스·모델의 후보를 합치지 않는다. 앞서 확인한 paired yield/callback-return 관측은 후기 오류와 전체 coverage 불확실성에서 분리한다. 후속 수집·첨부·정리 오류는 기존 ABORTED/UNKNOWN 및 독립 FAIL 보존 경로를 따른다.

기존 `identity_*verified` 플래그와 새 file-read/tensor-storage/callback-implementation/worker-assignment/producer-authentication 진실값은 모두 false다. 정상 대조 성공은 null, 판정 세 축은 UNKNOWN이다. 반환·파일 hash·선언 승인 기록을 실제 승인·최종 tensor 소비로 승격하지 않는다. A2의 `native_weight_files` 전용 소비는 원본 profile9 범위를 유지하며, 새 native16 전용 join·판정은 A2가 검토한 확정 전달물로 받아 통합한다.

## 정책 결정과 남은 생산자

사용자는 승인·검사 권한 주체와 신뢰 근거, 허용 모델/파일/이미지/작업/호출 주체, 정책 판본·효력·철회·기한·보존 예외, 실제 운영 대상·허용 범위를 확정한다. 기술 schema 설계를 사용자에게 요구하지 않는다. 정책 미확정을 공통 코드 개발 전체의 차단 사유로 삼지 않는다.

worker FD/open/read/mmap identity·byte 범위, 실제 torch/safetensors 구현과 tensor storage·backend 적용 완료, required tensor inventory·rank/shard/TP coverage, cross-process worker 배정, 실제 취득/cache/retry/usage/telemetry 생산자와 승인·검사 신뢰 연결은 미완료다. 이번 hook 하나로 해결하지 않는다. 실제 실행에는 새 승인 시간·소유권·활성 작업·quota·잔여 총비용·fresh ENV/target/POL·실행 순서·복구/정리 책임이 필요하다. 원GPU창 종료, 추가 Start0, 총46.66USD·과거 사용량·보존120GiB EBS 비용을 유지한다.

검증은 source/hash 고정 후 직접/동일 계약·인접/불변식·양쪽 OS·설치본 CLI/정확 Ref 범위를 나눠 기록한다. 기존 B Windows POSIX fixture 실패와 CI 결제 차단은 별개로 보존한다. helper/profile/등록, 제품 연결, 실제 실증, 전체 분기 완료를 구분하며 결과와 정확 커밋은 고정 인계에 기록한다.

## 고정 소스와 검증 범위

검증 source `f1d41491c80a0f51b6464393ef8146080bbce67f`, A측 검토 checkpoint `c8c4b26b017cedda7cbc982226b1bb89a7e78be7`의1358파일을 고정했다. 직접 시험 Windows36통과(112.172초), Linux36통과(23.523초), 인접 회귀 Windows `635 passed in 991.34s (0:16:31)`(부모993.944초)·Linux `635 passed in 222.87s (0:03:42)`(부모223.83초)로 구분한다. 현재 전체 suite 통과를 주장하지 않는다. 변경없는 불변식은 첫source20dae43의Linux125/Windows124통과·기존skip1을 재사용했으며 source pin9시험은 새source에서 다시 확인했다.

격리 Windows 설치 source/wheel/installed248파일일치(41.603초), 설치 CLI4·정확24Ref 왕복·저장팩 재판정(29.963초), 전체 공통 소비356/제외0/자동승인false를 확인했다. 설치 CLI는 normal/선언대상충돌/후속기록오류/첨부오류에서 source-bound iterator 후보와 load-call을 소비하지만 실제 승인·FD읽기·tensor 적용·worker배정은 확인하지 않는다. 기존 B 원본23, native8~15 센서 등103파일 bytes와 native1~7 고정표를 보존했다. A1 소유 profile 인식표 한 파일의 native16 추가는 전체 소비 검사에 그대로 포함했다.

첫source20dae43의 Windows5실패/155통과·기존skip1, Linux27실패/134통과를 보존했다. 정적 컴파일 도구가 OS별 bytecode cache를 공유해 Linux observer 고정값을 잘못 산출했고, 예제 native 기록 삽입이 원NATIVE 시각 필드를 이동하지 않았다. 두 명령 종료·소스불변을 확인한 뒤 컴파일 cache를 분리하고 새fixture의 기존 시각 규약을 보수했다. observer 함수 로직·parser gate·assert·기존 B pin은 완화하지 않았다. 올바른 코드 hash는 CPython3.12.14 `b30ec8ba3447ba218cb37838a55af0d28893a5e4518e7421de119e22ff7426f9`, CPython3.12.3 `e93012cf2ef2f74defbb675b7aa2df03c37c243b84b09b42588371b926419626`이다.

공통 recording 상한은2048행/2MiB다. 상한·누락·미종결·부분 소비에서는 이미 확인된 짝지은 사건과 전체 coverage를 나누고, 이후 model/request 결합이 없으면 후보를 만들지 않는다. 이 상한 안의 관측을 전체 모델·tensor inventory 또는 실제 worker 완료로 올리지 않는다. 새 판본 준비는 `guard prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/16 --output NEW_EXTERNAL_DIRECTORY`이며, 실제 설치·기동을 수행하는 명령이 아니다. 이미 승인된 ENV/target/prepared/POL을 명시해 기존 identity registry와 CLI를 사용한다. CI5는 정확 최종 HEAD 성공 전 미확인이다.

설치본 공개 prepare CLI도 별도 통과(9.674초)했고 원 native8 base bytes/새sensor bytes/manifest를 확인했다. 엔진·네트워크 호출을 금지한 준비 검사이며 실제 기동·실증은 수행하지 않았다.
