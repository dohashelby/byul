# TP2 실측 계획 (REQ-14·REQ-21, 개발 담당, 2026-11-06)

## 구성

- 사용자 결정(2026-11-06): 2장 구성은 개발 담당이 정한다. 서울에 L4 2장 유형이 없어 **g6.12xlarge**(L4 4장, vCPU 48, 메모리 192 GiB, 콘솔 표시 $5.65823/h, 2a·2c·2d)에서 2장만 쓴다.
- 지금까지와 같은 L4·드라이버 580.178.04·vLLM 0.31.0 이미지(`a4a4c043…`)·모델(opt-125m)이라 단일 L4 결과와 비교할 수 있다.
- 기존 인스턴스 `i-04f510eaa76c2bc89`(2c)를 stopped 상태에서 유형만 바꿔 시작한다. 같은 EBS의 kubeadm 클러스터·이미지·모델을 그대로 쓰고, 끝나면 g6.2xlarge로 되돌린다.
- 할당량: Running On-Demand G and VT instances(L-DB2E81BA) vCPU 8 → 48 요청(2026-11-06 15:50경, 승인 대기). 승인 전에는 시작할 수 없다.

## 순서

1. 할당량 승인 확인. 비용 상한은 사용자 승인(2026-11-08): **A+B 한 세션 EC2 $20**(EBS 별도). A만이면 1.5~2시간·약 $8.5~$11. 범위·조건은 `user-decisions-20261108.md` 1·2절
   (REQ-16 Qwen2-VL-2B-Instruct를 EC2 안에서 내려받아 같은 세션에서 실행, B 분기 입력이 세션 전에 없으면 A 범위만).
2. 콘솔: 인스턴스 유형 변경(g6.2xlarge → g6.12xlarge) → 시작 → SSH 원본 확인.
3. 서버: 커밋 소스 묶음 배치(SHA 대조), 하네스 `harness-tp2.tgz` 배치, `uv sync --frozen`.
4. `BYUL_RUN_DATE=<날짜> python a-tp2.py facts`: GPU 수·`nvidia-smi topo -m`·P2P 읽기·노드 allocatable을 기록한다. device plugin이 4장을 보고하는지 확인한다.
5. `a-tp2.py req14`: TP2 엔진(콜백 센서, `--tensor-parallel-size 2`, GPU 2·shm 4Gi·메모리 24Gi) → `req14-normal` 4사례.
6. `a-tp2.py req21`: 같은 설정 + `restartPolicy: Always` → before → `engine_restart` → after. 실행 상한 900초(엔진 재기동 대기 300초 포함).
7. 증거 반출(SHA 대조) → 소유 namespace 삭제·GPU 0MiB → EC2 중지 → 유형을 g6.2xlarge로 되돌림 → stopped·비용 장부.

## 미리 알려 둘 위험

- REQ-21은 vLLM custom all-reduce 통신기를 관측한다. 이 통신기는 GPU 사이 P2P가 있어야 켜진다. L4는 PCIe 연결이라 P2P가 없으면 통신기가 꺼지고 `tensor_parallel_communicator_inactive`로 UNKNOWN이 된다. 4단계의 P2P 기록으로 원인을 남기며 판정을 바꾸지 않는다.
- REQ-14의 TP 중간 전송 계보는 rank별 실행·출력 rank 대조까지이며, 가중치 실제 소비는 미관측으로 남는다(기존 메모).

## 준비 점검 (CPU)

- 하네스: `byul-dev1-evidence/tp2-prep/harness/` (11-06 하네스 + `a-tp2.py`, 날짜는 실행 때 환경변수).
- 11-05 GPU env·target으로 REQ-14(`req14-normal`)·REQ-21(재기동 사례·lab 선언) 입력 생성과 `guard select` 통과(팩33).
- REQ-21도 LIF-13처럼 실행 안에서 엔진을 띄우므로 실행기·입력 도구의 900초 상한에 넣었다(`LONG_LAB_CHECKS`). 전송의 900초는 소유 lab 콜백 대상에서만 받는다.
