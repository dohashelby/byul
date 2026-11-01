# OBS02 SC23-03 native17 eager 파일 읽기 초안

상태: 고정 source 51e9a5c0cba266219f92e511f9b461a0358555b2의 CPU·설치본 검증이 마감됐다. 부모 A39/56/4와 A1 19/27/2의 완료를 뜻하지 않는다.

기존 `observation_identity:kubernetes` 제품 및 OBS01 선언 수집·POL8·저장팩·Ref 경로에 추가한다. native8과 native16 센서 파일은 원본 bytes를 고정 의존성으로 사용한다. 실제 serving의 적재 전략은 바꾸지 않는다.

대상은 고정 vLLM `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 `weight_utils.safetensors_weights_iterator`에서 명시적 `eager` 분기가 실행하는 binary `open(st_file, "rb") → f.read() → close`다. module 전역 `open`만 감싸며 builtins·프로파일 callback을 바꾸지 않는다. 정확한 활성 generator code·native16 호출/작업·PID를 확인한다. 원본 read 결과와 예외는 decoder에 그대로 전달한다. 별도 helper가 모델 파일을 읽지 않는다.

`prepare-model-read-scope --env ENV --target NATIVE_TARGET --model-manifest REFERENCE_FILE --model-root /models/SNAPSHOT --nonsecret-model-weights [--hash-model-bytes] --output SCOPE`로 참조 범위를 만든다. 모델 파일은 열지 않는다. 기존 `guard.model-manifest/1`의 전체 inventory와 ENV의 모델·revision·inventory를 결합하고 `.safetensors` 목록만 선택한다. 이 선언은 승인 주체나 모델의 신뢰 증거가 아니다.

`prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/17 --model-read-scope SCOPE --output ASSETS`는 해당 세션에 범위를 고정한다. scope 부재·잘못된 판본·MEASURED 선언·비밀 아닌 모델 파일 확인 부재는 출력 예약 전에 거부한다. 실제 파일의 절대경로는 기록하지 않고 정규화 경로 hash로 범위를 선택한다. 내용 hash는 명시적 동의와 기술적 상한 안에서 이미 원본 read가 반환한 buffer에만 적용한다. 원문 데이터·임의 예외·환경변수·argv는 저장하지 않는다.

schema `guard-native-file-read/1`에는 OPEN_RETURN/OPEN_RAISE, READ_RETURN/READ_RAISE, CLOSE_RETURN/CLOSE_RAISE, source/method/observer code·scope hash, loader/root/model/call/작업·불투명 FD 세대·fstat/offset 및 경로 lstat를 기록한다. FD metadata 안정성·경로 일치·전체 FD read 반환·선언 참조 bytes 일치는 각각 관측이다. 구성요소 전체 nofollow, 승인된 파일, 신뢰된 producer/worker, decoder 완료, tensor/backend 적용은 모두 미확인이다. 빠진 close·중단·후속 수집/첨부 오류는 이미 유효한 read 사실을 지우지 않는다.

일반 C 확장 `safe_open`/mmap, torch.load의 실제 FD read, 실제 tensor/rank/shard/worker 역할·승인 생산자 연결은 별도 미완료다. native0.32·기존 source/image/lab gate를 유지한다. CPU EXAMPLE의 synthetic bytes·가짜 decoder 시험을 실제 safetensors/serving/GPU 실증으로 올리지 않는다. 실제 운영 대상의 적재 전략은 아직 확인되지 않았다.

사용자 결정은 승인·검사 권한/신뢰, 허용 모델·파일·이미지·작업·주체, 정책 효력·철회·기한·보존, 실제 운영 대상·관측 범위·책임이다. 기술 계약 초안과 독립 구현은 A1이 맡고 실제 권한 근거가 없으면 판정은 UNKNOWN이다.

Windows Python3.12의 실제 EXAMPLE 진단에서 경로 stat와 FD fstat의 ctime 값이 다른 표본을 보존했다. 고정 CPython3.12.14의 [경로 구현](https://raw.githubusercontent.com/python/cpython/v3.12.14/Modules/posixmodule.c)은 ctime에 생성 시각을 복사하고, [FD 구현](https://raw.githubusercontent.com/python/cpython/v3.12.14/Python/fileutils.c)은 변경 시각을 반환한다. schema의 `ctime_basis`는 이 기준을 보존한다. 모든 metadata 필드를 비교하므로 Windows 경로/FD 완전 일치는 숫자가 우연히 같더라도 false다. FD metadata 안정성·read bytes·hash와 구분한다. fixture 시각을 조작하거나 ctime을 무시해 성공시키지 않는다. 해당 Windows ctime 의미는 고정 runtime의 소비 계약이며 다른 runtime 지원을 추정하지 않는다.

검증 범위: 직접 Windows/Linux 각38, 인접 각313, 동일1365파일 검증 중 불변. 설치251파일 일치, 공개 준비CLI2종 및 기존 OBS02 CLI4·정확24Ref 반출/복원·저장팩 재판정. 전체 공통소비359파일/제외0/실행hash자동승인false. 현재 전체suite·실제 serving/GPU·전체 분기 완료를 뜻하지 않는다. 최초 실패와 보수·진단은 인계에서 source별로 구분하며 변경 없는 범위의 첫source 검증을 현재 전체suite로 합산하지 않는다.

후속 A2 native16 통합 source 9997936a2e038dbfcdd11c1c9399f08b276f4906는 native16 metadata를 공개 원bytes f8ced901...로 유지한다. native17 공통 투영은 file_read_metadata.project_iterations(packet)에서 명시적 profile17을 선택하고 원 native16 journal을 재사용한다. profile/envelope 변환은 없다. 이 후속 설치252파일과 native17 인접CLI4/정확24Ref는 별도 검증됐으며 앞선 source51e9a5c의251파일 근거와 구분한다.
