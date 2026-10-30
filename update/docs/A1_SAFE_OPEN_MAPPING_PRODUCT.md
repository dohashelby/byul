# OBS02 ordinary safe_open mapping 후보

native18은 기존 source-qualified vLLM0.31 safetensors_weights_iterator의 None/lazy/prefetch ordinary safe_open 호출을 감싼다. eager/torchao/다른 loader를 강제로 바꾸지 않는다. 원 생성·enter/get_tensor/exit의 값과 예외·정리 동작을 그대로 전달하고 모델 내용·Tensor 값·추가 모델 read·GPU copy를 수집하지 않는다.

이미 로드된 safetensors0.8 package/native alias와 원 C method descriptor, 고정 package Python2파일/native binary 파일 bytes를 확인한다. native binary e6f17a9...의 정적 파일 일치와 로드된 메모리·실제 worker attestation은 다르다. C 라이브러리 미입장 시 LIBRARY_UNAVAILABLE만 기록하고 원 함수를 한 번 호출한다. 라이브러리를 import/install하지 않는다.

동일 producer PID/start·recording·scope·context·root/loader/operation/model과 C 경계 전후의 Linux /proc/self/maps를 최소화해 기록한다. 선언된 nonsecret path의 stat device/inode에 해당하는 offset/length/perms/deleted와 세션별 opaque region만 남긴다. raw maps·ASLR 주소/주소 hash·원문 path·env/argv·Tensor·예외 원문은 저장하지 않는다. 비원자적 snapshot·부분/한도·교체/unlink/restart·후속 오류는 별도다. mapping/context 종료는 read·최종 tensor 적용·회수 증명이 아니다.

일곱 준비 asset은 source18·sitecustomize/settings·원base8·원iteration16·원scope17·선언 read-scope다. public prepare-lifecycle-sensor/profile18과 기존 OBS02/POL8/PACK31 project_native→summary/nested model 후보를 같은 묶음에서 연결한다. source/model/loader/recording 및 선택 forward 이전 사건만 귀속하며 전용 소비 오류는 ABORTED/정제 진단으로 남기고 앞선 공통 관측을 보존한다. native17 A2 소비기는 profile17 그대로 유지하며 envelope 변환을 하지 않는다.

기존 native16/17 기본 capture helper에는 공개 2MiB 계약과 달리 작은 기본 reader를 선택하던 경로가 있었다. 같은 extended read protocol과 max_bytes를 사용하도록 맞추며 owner/path/source/image/lab gate는 유지한다. 변경 영향 회귀에 이 경계를 포함한다.

상태: 검증 전 고정 소스. CPU positive는 정확 vLLM 함수 + EXAMPLE safe_open class + 소유 stdlib mmap이며 C library gate 대역을 명시한다. 실제 safetensors C 실행·Torch 설치·serving/GPU·native0.32는 미검증/미지원이다. 모든 승인/worker/tensor/read/release/inventory truth는 false·판정 UNKNOWN이다. 자료 제공·제품 연결·실제 실증·전체 분기 완료를 구분한다. 원GPU창종료/추가Start0/46.66USD·과거사용량·보존120GiB EBS와 actualwheel04f7856을 유지한다.
