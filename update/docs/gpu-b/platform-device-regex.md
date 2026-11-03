# B passive GPU FD 장치명 교정 후보

기준 소스는 `756c292bc5da0481a54c299b634485205eb74a3c`이다. 그 SOURCE와 수신 `_decode`의 기존 `nvidia(?:[0-9]+|ctl|uvm|uvm-tools)` 식은 Linux 실제 `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools`의 하이픈을 받지 못하고 `/dev/nvidiauvm` 같은 가짜 철자를 받았다. 첫 GB20 PLT-01/04 관측은 기존 SOURCE SHA `b3ab55f2ff8b34af561b25cf1499e2337d3d8dd0c7b22c2f29bbcfb85ba40322` 그대로 별도 보존해야 한다.

이번 후보는 정확한 하이픈 경로만 SOURCE와 `_decode` 양쪽에 반영했다. 변경된 embedded SOURCE SHA256은 `9b5908b23f9d38f1205a2dbd2033ad23f2838beef1553feb911023a81d2f4037`이다. 기존 O_PATH의 metadata-only read/write=false, 실제 커널 문자 장치 `S_ISCHR` 조건, FD/PID/namespace 안정성 조건, NVML 결합, A v1 flag 및 judge/PACK은 바꾸지 않았다. 장치 FD 발견은 GPU worker 분류나 모든 device 권한의 증명이 아니며 완료 표시는 계속 false다.

Windows Python 3.12 직접 계약에서 B 수신 경로와 인접 container/credential 회귀 57건이 통과했다. 별도 WSL `/usr/bin/python3.12` 직접 SOURCE 시험은 실제 `/dev/null` 문자 장치 FD의 커널 mode/major/minor를 유지하고 readlink 표시만 바꿔 두 실제 하이픈 경로 수집, 하이픈 없는 두 가짜 경로 거부를 확인했다. 같은 실제 경로 표시라도 원본 FD가 일반 EXAMPLE 파일이면 장치로 세지 않았다. 이 시험은 실제 GPU 접근 시험이 아니다.

`reviewed-source.json`은 변경하지 않았다. 이 코드를 새 제품 스냅샷으로 채택할 때는 변경된 B 소스 bytes에 대한 외부 사람 검토와 manifest 후보 갱신, 새 고정 소스 직접시험이 필요하다. 첫 GB20 관측의 SOURCE와 결과를 소급 수정하지 않는다.
