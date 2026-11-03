# 담당 A 엔진 준비 점검표

GPU를 켠 뒤 시험 전에 채운다. 세대(`AWS_RUNBOOK.md`의 G-A1~G-A4)마다 이 표를 따로 복사해 쓰고 결과를 섞지 않는다. "고정값"은 팀 AWS 인계 묶음(`aws-handoff-20261026`)과 첫 실증 기준이며, 다르면 새 세대로 기록한다.

| 항목 | 고정값·요구 | 확인 방법 | 기록 |
|---|---|---|---|
| 엔진 이미지 | 인계 `images.lock` 첫 줄. 첫 실증은 `vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b` (0.31.0, linux/amd64) | Pod `status.containerStatuses[].imageID`. manifest digest와 config digest를 구분 | 두 digest가 다르면 새 이미지 세대 |
| 컨테이너 Python | 3.12.3 | Pod 안 `python3 -VV` | |
| 모델 | `Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775`, inventory `b52d654e…8245e` | 마운트 inventory 해시 재계산 | |
| API 별칭 | `test-model`(인계 템플릿). 첫 실증은 `byul-gpu-lab` | `GET /v1/models`. 요청·target·비교에 이 별칭만 쓴다 | |
| runner 세대 | V1 센서 세대(G-A1·G-A2·G-A4)는 `VLLM_USE_V2_MODEL_RUNNER=0`, G-A3은 기본 V2. 인계 템플릿에 `--enforce-eager`가 없어 CUDA Graph가 기본으로 켜짐 | Pod env와 기동 로그 | eager 여부 |
| 엔진 옵션 | 인계 템플릿 `--max-model-len 2048 --max-num-seqs 2 --gpu-memory-utilization 0.70`, 센서 세대는 `--enable-prefix-caching` 추가 | 기동 인자, `render.json` | block size, `max_model_len`(REQ-17은 1,600 이상) |
| 스케줄링 | 기본은 async(0.31.0 자동). REQ-14 동기 세대만 `--no-async-scheduling` | 기동 로그의 async scheduling 표시 | 세대별 async 여부. 두 세대의 REQ-14 결과를 섞지 않는다 |
| target 경로 | `topology=kubernetes-owned-direct`, `trusted_url` 호스트 = 엔진 Pod IP | target 파일과 `kubectl get pod -o wide` | Pod 이름·UID·IP |
| Pod 설정 | `enableServiceLinks: false`, 라벨 `guard.run`=run ID(덧씌우기) | Pod spec | |
| 클러스터·네트워크 | kubeadm·Cilium. 엔진 들어오는 요청은 Gateway와 시험 예외(`app: engine-test-probe`)만 | `kubectl get networkpolicy -n serving-lab` | 예외 적용·회수 시각, 회수 뒤 접근 거부 대조 |
| 실행 UID/GID | 실제 값 확인. 첫 실증 root 세대의 PLT-01 FAIL은 지우지 않는다 | Pod 안 `id` | |
| 센서 자산 | `guard prepare-engine-callbacks --output <폴더> --lab-faults` | 자산 `manifest.json`의 session, 준비 해시 | |
| 센서 마운트 | `/guard-lifecycle/engine-callbacks` 읽기 전용(host `/srv/byul-a/<run>/engine-callbacks`), `/guard-lifecycle/data` 쓰기(host `/srv/byul-a/<run>/lifecycle-data`, 2000:0, 0700), `PYTHONPATH`에 자산 폴더 | Pod spec, `mount` | |
| 센서 파일 권한 | 자산·기록 폴더 owner/group·mode가 실행 UID/GID와 맞음 | `ls -ln` (REQ-14 G27이 이 불일치로 실패) | |
| 센서 설치 | 대상 모듈이 모두 verified | 기동 뒤 콜백 기록의 GAP 행 | `ENGINE_CALLBACK_INSTALL_UNAVAILABLE` 등 공백 코드 |
| REQ-14 대상 파일 | `vllm/v1/worker/gpu_model_runner.py`의 SHA가 `bb903dff…` | Pod 안 `stat -c %s`, `sha256sum` | 바이트 수(`REQ-14_REJECTION.md` 1번 후보에 쓴다) |
| GPU | 장수, 모델, 드라이버, CUDA | `nvidia-smi -q`의 해당 필드 | GPU UUID는 세대 식별에만 쓴다 |

## 세대 기록

| 세대 이름 | runner | Graph | Pod UID | 시작(KST) | 종료(KST) | 쓴 점검 |
|---|---|---|---|---|---|---|
| | | | | | | |
