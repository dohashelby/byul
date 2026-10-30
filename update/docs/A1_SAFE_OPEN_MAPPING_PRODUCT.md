# OBS02 ordinary safe_open mapping 후보

native18은 기존 source-qualified vLLM0.31 safetensors_weights_iterator의 None/lazy/prefetch ordinary safe_open 호출을 감싼다. eager/torchao/다른 loader를 강제로 바꾸지 않는다. 원 생성·enter/get_tensor/exit의 값과 예외·정리 동작을 그대로 전달하고 모델 내용·Tensor 값·추가 모델 read·GPU copy를 수집하지 않는다.

이미 로드된 safetensors0.8 package/native alias와 원 C method descriptor, 고정 package Python2파일/native binary 파일 bytes를 확인한다. native binary e6f17a9...의 정적 파일 일치와 로드된 메모리·실제 worker attestation은 다르다. C 라이브러리 미입장 시 LIBRARY_UNAVAILABLE만 기록하고 원 함수를 한 번 호출한다. 라이브러리를 import/install하지 않는다.

동일 producer PID/start·recording·scope·context·root/loader/operation/model과 C 경계 전후의 Linux /proc/self/maps를 최소화해 기록한다. 선언된 nonsecret path의 stat device/inode에 해당하는 offset/length/perms/deleted와 세션별 opaque region만 남긴다. raw maps·ASLR 주소/주소 hash·원문 path·env/argv·Tensor·예외 원문은 저장하지 않는다. 비원자적 snapshot·부분/한도·교체/unlink/restart·후속 오류는 별도다. mapping/context 종료는 read·최종 tensor 적용·회수 증명이 아니다.

일곱 준비 asset은 source18·sitecustomize/settings·원base8·원iteration16·원scope17·선언 read-scope다. public prepare-lifecycle-sensor/profile18과 기존 OBS02/POL8/PACK31 project_native→summary/nested model 후보를 같은 묶음에서 연결한다. source/model/loader/recording 및 선택 forward 이전 사건만 귀속하며 전용 소비 오류는 ABORTED/정제 진단으로 남기고 앞선 공통 관측을 보존한다. native17 A2 소비기는 profile17 그대로 유지하며 envelope 변환을 하지 않는다.

기존 native16/17 기본 capture helper에는 공개 2MiB 계약과 달리 작은 기본 reader를 선택하던 경로가 있었다. 같은 extended read protocol과 max_bytes를 사용하도록 맞추며 owner/path/source/image/lab gate는 유지한다. 변경 영향 회귀에 이 경계를 포함한다.

상태: 선정 공통 hook/parser/OBS02 제품 및 CPU/설치/정확 Ref 인계 범위를 마감한다. CPU positive는 정확 vLLM 함수 + EXAMPLE safe_open class + 소유 stdlib mmap이며 C library gate 대역을 명시한다. 실제 safetensors C 실행·Torch 설치·serving/GPU·native0.32는 미검증/미지원이다. 모든 승인/worker/tensor/read/release/inventory truth는 false·판정 UNKNOWN이다. 자료 제공·제품 연결·실제 실증·전체 분기 완료를 구분한다. 원GPU창종료/추가Start0/46.66USD·과거사용량·보존120GiB EBS와 actualwheel04f7856을 유지한다.

고정 검증 source ecbbcaccb06a0f4cf88e75c09e53a7e878d19bb4, 검토 pin eba2440eb63687b5b4237dabc24de0044cb827a1, 동일1407파일 불변이다. 직접 Win/Linux각49, 인접각103, source/wheel/설치261파일 일치, 설치본 public 준비CLI2와 기존 OBS02 CLI4·정확24Ref 반출/복원·저장팩 재판정을 확인했다. Linux 소유 mmap EXAMPLE 입력을 설치본에서 소비해 전후 mapping 후보가 EVD까지 보존됨을 확인했다. 라이브러리 gate/class는 대역이며 실제 C/serving/worker/GPU 검증은0이다. 공통369/제외0/실행hash자동승인false, 원 A2/B41파일과 base8/공통16/17 bytes를 보존했다.

첫 source ae14985의 Windows27fail/21pass, Linux26fail/22pass는 보존했다. 한줄 placeholder에서 code hash를 계산한 뒤 literal을 두줄로 써 co_firstlineno가 바뀐 원인과 Windows os.makedev fixture 오류를 보수했다. 고정 hash와 code gate는 유지했고 로드/독립 compile의 일치를 추가했다. 새 skip/assert 완화는 없다.

region token은 관측된 좌표의 opaque 후보이며 시간 사이의 mapping 존속·세대 동일성을 증명하지 않는다. context/root/loader/operation 좌표는 각각 유지한다. context/FD 종료·파일 삭제·같은 경로 교체로 mapping release를 확정하지 않는다. 이 범위의 제공/제품 연결/CPU 설치 근거와 actual C·권한·최종 tensor·전체 분기/CI5는 별도다.
