# B passive GPU FD 장치명과 현재 실행 경계

현재 설명은 `3bba3c73a6bee1966b784cd90a9690e3e9d2512a` 기준이다. 다음 장치명 교정의 과거 기준 소스는 `756c292bc5da0481a54c299b634485205eb74a3c`였다. 그 SOURCE와 수신 `_decode`의 기존 `nvidia(?:[0-9]+|ctl|uvm|uvm-tools)` 식은 Linux 실제 `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`의 하이픈을 받지 못하고 `/dev/nvidiauvm` 같은 가짜 철자를 받았다. 첫 GB20 PLT-01/04 관측은 기존 SOURCE SHA `b3ab55f2ff8b34af561b25cf1499e2337d3d8dd0c7b22c2f29bbcfb85ba40322` 그대로 별도 보존해야 한다.

당시 후보는 정확한 하이픈 경로를 SOURCE와 `_decode` 양쪽에 반영했고 embedded SOURCE SHA256은 `9b5908b23f9d38f1205a2dbd2033ad23f2838beef1553feb911023a81d2f4037`이었다. 현재 SOURCE와 decoder는 여기에 `/dev/nvidia-caps/nvidia-cap[0-9]+`도 받으며 현재 SOURCE SHA256은 `c6c0f0703207e73463f02ead1a35610e35f19ca7a307c877f7ae6ecb915f2a8c`다. 실제 MIG 구성·장치 접근·GPU worker/NVML 결합의 실측을 뜻하지 않는다. 기존 O_PATH의 metadata-only read/write=false, 실제 커널 문자 장치 `S_ISCHR` 조건, FD/PID/namespace 안정성 조건, NVML 결합, A v1 flag 및 judge/PACK은 바꾸지 않았다. 장치 FD 발견은 GPU worker 분류나 모든 device 권한의 증명이 아니며 완료 표시는 계속 false다.

당시 Windows Python 3.12 직접 계약에서 B 수신 경로와 인접 container/credential 회귀 57건이 통과했다. 별도 WSL `/usr/bin/python3.12` 직접 SOURCE 시험은 실제 `/dev/null` 문자 장치 FD의 커널 mode/major/minor를 유지하고 readlink 표시만 바꿔 두 실제 하이픈 경로 수집, 하이픈 없는 두 가짜 경로 거부를 확인했다. 같은 실제 경로 표시라도 원본 FD가 일반 EXAMPLE 파일이면 장치로 세지 않았다. 이 시험은 실제 GPU 접근 시험이 아니다.

당시 이 교정 문서를 작성할 때 `reviewed-source.json`은 변경하지 않았다. 현재 3bba는 별도의 고정 통합 snapshot이며, 고정 SOURCE/decoder의 CPU 지원과 live 수집의 가용성은 구분한다. 현재 `collect()`는 A의 공개 등록 계약이 없어 `A_PUBLIC_BOUNDED_PROCESS_METADATA_CONTRACT_PENDING`을 반환하며 `_verify`/`_call`로 실행하지 않는다. SOURCE SHA나 manifest 후보가 있다는 이유로 사람 승인·GPU 검증 완료를 주장하지 않는다. 첫 GB20 관측의 SOURCE와 결과를 소급 수정하지 않는다.
