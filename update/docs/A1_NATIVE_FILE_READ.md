# OBS02 SC23-03 native17 eager 파일 읽기 초안

상태: 구현 초안이며 검증·고정 인계 전이다. 부모 A39/56/4와 A1 19/27/2의 완료를 뜻하지 않는다.

기존 `observation_identity:kubernetes` 제품 및 OBS01 선언 수집·POL8·저장팩·Ref 경로에 추가한다. native8과 native16 센서 파일은 원본 bytes를 고정 의존성으로 사용한다. 실제 serving의 적재 전략은 바꾸지 않는다.

대상은 고정 vLLM `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 `weight_utils.safetensors_weights_iterator`에서 명시적 `eager` 분기가 실행하는 binary `open(st_file, "rb") → f.read() → close`다. module 전역 `open`만 감싸며 builtins·프로파일 callback을 바꾸지 않는다. 정확한 활성 generator code·native16 호출/작업·PID를 확인한다. 원본 read 결과와 예외는 decoder에 그대로 전달한다. 별도 helper가 모델 파일을 읽지 않는다.

`prepare-model-read-scope --env ENV --target NATIVE_TARGET --model-manifest REFERENCE_FILE --model-root /models/SNAPSHOT --nonsecret-model-weights [--hash-model-bytes] --output SCOPE`로 참조 범위를 만든다. 모델 파일은 열지 않는다. 기존 `guard.model-manifest/1`의 전체 inventory와 ENV의 모델·revision·inventory를 결합하고 `.safetensors` 목록만 선택한다. 이 선언은 승인 주체나 모델의 신뢰 증거가 아니다.

`prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/17 --model-read-scope SCOPE --output ASSETS`는 해당 세션에 범위를 고정한다. scope 부재·잘못된 판본·MEASURED 선언·비밀 아닌 모델 파일 확인 부재는 출력 예약 전에 거부한다. 실제 파일의 절대경로는 기록하지 않고 정규화 경로 hash로 범위를 선택한다. 내용 hash는 명시적 동의와 기술적 상한 안에서 이미 원본 read가 반환한 buffer에만 적용한다. 원문 데이터·임의 예외·환경변수·argv는 저장하지 않는다.

schema `guard-native-file-read/1`에는 OPEN_RETURN/OPEN_RAISE, READ_RETURN/READ_RAISE, CLOSE_RETURN/CLOSE_RAISE, source/method/observer code·scope hash, loader/root/model/call/작업·불투명 FD 세대·fstat/offset 및 경로 lstat를 기록한다. FD metadata 안정성·경로 일치·전체 FD read 반환·선언 참조 bytes 일치는 각각 관측이다. 구성요소 전체 nofollow, 승인된 파일, 신뢰된 producer/worker, decoder 완료, tensor/backend 적용은 모두 미확인이다. 빠진 close·중단·후속 수집/첨부 오류는 이미 유효한 read 사실을 지우지 않는다.

일반 C 확장 `safe_open`/mmap, torch.load의 실제 FD read, 실제 tensor/rank/shard/worker 역할·승인 생산자 연결은 별도 미완료다. native0.32·기존 source/image/lab gate를 유지한다. CPU EXAMPLE의 synthetic bytes·가짜 decoder 시험을 실제 safetensors/serving/GPU 실증으로 올리지 않는다. 실제 운영 대상의 적재 전략은 아직 확인되지 않았다.

사용자 결정은 승인·검사 권한/신뢰, 허용 모델·파일·이미지·작업·주체, 정책 효력·철회·기한·보존, 실제 운영 대상·관측 범위·책임이다. 기술 계약 초안과 독립 구현은 A1이 맡고 실제 권한 근거가 없으면 판정은 UNKNOWN이다.
