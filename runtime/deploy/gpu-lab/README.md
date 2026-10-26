# AWS GPU lab — runtime 배포 층 (초안)

AWS 공통 GPU 시험환경 설치 묶음(`aws-handoff-20261026`, 이하 "AWS 묶음") **위에 얹는** runtime 쪽 파일이다. 엔진·namespace·계정·엔진 격리 정책은 AWS 묶음이 만들고, 여기서는 Gateway·관리 수신부·observer 자리·추가 통신 정책·intent·호스트 수집기만 더한다. **아직 어떤 클러스터에도 적용하지 않았다.** 서버에서만 알 수 있는 값은 `REPLACE…`로 비워 두었다.

## AWS 묶음이 이미 만드는 것 (여기서 다시 만들지 않음)

| 항목 | AWS 묶음 파일 |
| --- | --- |
| namespace `serving-lab`, RuntimeClass `nvidia`, SA `engine`·`gateway`·`observer`·`probe`·`artifact-acquire`, observer Pod 조회 Role | `foundation.yaml` |
| `serving-engine` Deployment(UID 2000:0, 공식 비root wrapper, GPU 1, 모델 `/models` 읽기 전용, 캐시 `/cache`, served model `test-model`)·Service 8000 | `engine.yaml.template` (03이 적용) |
| 엔진 ingress는 Gateway→8000만, 엔진 egress 없음 | `engine-isolation.yaml` |
| context 이름 `byul-aws-lab` | `02-cluster.sh` |

## 이 폴더

| 파일 | 내용 |
| --- | --- |
| `kustomization.yaml` | 아래 자원 묶음. `kubectl kustomize deploy/gpu-lab`로 렌더링 |
| `gateway.yaml` | Gateway(SA `gateway`, UID 10000·보조 그룹 10010). 정책 묶음·인증 Secret·관측 기록(읽기 전용)·기록 폴더를 hostPath로 받음 |
| `backend-config.yaml` | vLLM backend 설정(`test-model`) |
| `engine-management-sidecar.patch.yaml` | AWS 엔진 Deployment에 관리 수신부 사이드카(8001)를 더하는 patch |
| `management-service.yaml` | 관리 수신부용 Service `serving-engine-management`(8001) |
| `network-policies.yaml` | observer→8001만 추가 허용(8000은 그대로 Gateway만) |
| `observer.yaml` | observer 자리(SA `observer`, UID 10004) |
| `test-pods.yaml` | 시험 receiver·other-client(SA `probe`, UID 10003) |
| `intent.aws-lab.json` | AWS 값으로 만든 통제 의도(엔진 UID 2000, `/models`, 포트 8000/8001) |
| `host/byul-observation-feed.service` | AWS 호스트에서 수집기를 계속 돌리는 systemd 사용자 서비스 |
| [`ACCEPTANCE.md`](ACCEPTANCE.md) | AWS 첫 완료 기준 8개 사례의 실행·증거·비용 절약 절차 |

UID: 엔진 2000은 AWS 묶음(vLLM 0.31.0 공식 비root 지원)을 따른다(`runtime/docs/결정기록_엔진UID.md`). Gateway 10000·observer 10004·공유 그룹 10010은 겹치지 않게 고른 프로젝트 선택값이다.

## 순서 (AWS 묶음 01→02→03 성공 뒤)

1. **호스트 폴더·그룹 준비** (관리자)
   ```bash
   sudo groupadd -g 10010 serving-observe && sudo usermod -aG serving-observe ubuntu   # 재로그인
   sudo install -d -o ubuntu -g 10010 -m 0750 /srv/serving/observation
   sudo install -d -o 10000 -g 10000 -m 0700 /srv/serving/runtime/data
   sudo install -d -o root -g 10000 -m 0750 /srv/serving/bundles
   ```
   `/srv/serving/runtime`이 없으면 먼저 만든다. `usermod` 뒤에는 이미 떠 있는 systemd 사용자 매니저가 예전 그룹 목록을 유지하므로(다른 SSH·tmux 세션이나 linger가 있으면 재로그인만으로 부족) `sudo systemctl restart user@$(id -u ubuntu)` 후 `systemd-run --user --wait --pipe id -G`에 10010이 보이는지 확인한다. 그룹이 없으면 수집기는 명확한 오류와 함께 종료 코드 2로 멈춘다(재시작 반복 없음).

   정책 묶음은 platform이 `/srv/serving/bundles/<bundle_id>/`에 쓰고 `current`가 그 폴더를 가리키게 한다(`runtime/docs/결정기록_정책묶음_수신.md`). Gateway 컨테이너에는 이 폴더가 `/etc/serving/bundles`로 보이므로 **`current`는 상대 링크**(`ln -sfn <bundle_id> current`)여야 한다. 절대 링크(`/srv/serving/bundles/<id>`)는 컨테이너 안에서 풀리지 않아 Gateway가 시작하지 않는다. 묶음 폴더와 파일은 Gateway(UID 10000)가 읽을 수 있어야 한다(예: 그룹 10000, 폴더 0750·파일 0640). 묶음의 gateway policy는 `allowed_models`에 `test-model`을, `enforcement_intent` 역할에 `intent.aws-lab.json`(값을 채운 것)을 넣는다.
2. **수집기 실행** — byul 저장소를 `~/byul`에 두고 `host/byul-observation-feed.service`의 주석대로 설치한다. `--kube-context byul-aws-lab --reader-group 10010`으로 돌며, Tetragon 프로세스 인자를 지운 뒤 기록한다. 상태는 `/srv/serving/observation/*.heartbeat.json`.
3. **Gateway 이미지 넣기** — 레지스트리가 없으므로 노드의 containerd에 직접 넣는다.
   ```bash
   # 노트북(runtime/ 폴더). base는 digest로 고정
   docker build -f deploy/gateway/Dockerfile --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> -t serving-gateway:<tag> .
   docker inspect --format '{{.Id}}' serving-gateway:<tag>        # 기록해 둘 image ID
   docker save serving-gateway:<tag> | ssh ubuntu@<host> 'sudo ctr -n k8s.io images import -'
   ```
   매니페스트는 `serving-gateway:<tag>` + `imagePullPolicy: Never`로 참조한다(레지스트리 digest가 없어 tag 참조). 대신 배포 뒤 `kubectl get pod -o jsonpath='{..imageID}'`가 위 image ID와 같은지 증거로 남긴다. `REPLACE_GATEWAY_TAG`를 `<tag>`로 바꾼다.
4. **인증 Secret** — `kubectl --context byul-aws-lab -n serving-lab create secret generic serving-gateway-auth --from-file=auth.json`(토큰 해시만 든 `runtime-auth/1.0`).
5. **적용**
   ```bash
   kubectl --context byul-aws-lab apply -k deploy/gpu-lab
   kubectl --context byul-aws-lab -n serving-lab patch deployment serving-engine \
     --type strategic --patch-file deploy/gpu-lab/engine-management-sidecar.patch.yaml
   ```
   patch는 엔진 Pod를 다시 만든다(Recreate: GPU 반납 후 재기동·cold start). 시험 시간 계획에 넣는다.
   AWS 묶음의 03은 client-side `kubectl apply`를 쓰고 `kubectl patch`는 last-applied 기록을 바꾸지 않으므로, 03을 다시 돌려도 **사이드카가 남을 것으로 본다**(클러스터 미검증). 그러면 03의 smoke 증거에 runtime 사이드카가 섞이므로, 03을 다시 돌리기 전에 patch 파일 주석의 명령으로 사이드카를 떼고 03이 끝나면 다시 붙인다. 근본 해결은 템플릿 반영(아래 표).
6. **세부 통제 정책** — `serving-runtime enforce render --intent deploy/gpu-lab/intent.aws-lab.json --out <dir>`로 Cilium·Tetragon 후보를 만들어 검토 후 적용한다. AWS 인수 확인은 [`ACCEPTANCE.md`](ACCEPTANCE.md)의 `tools/aws_lab_acceptance.py`를 사용한다. kind 전용 `tools/k8s_lab_check.py`는 fake engine과 관리 정책을 생성·삭제하므로 AWS 엔진에 사용하지 않는다.
7. 접속: 서버에서 `kubectl --context byul-aws-lab -n serving-lab port-forward deploy/serving-gateway 8080`, 노트북에서 SSH 터널(`ssh -L 18081:127.0.0.1:8080 ubuntu@<host>`). 노트북의 개발 서버(`byul-dev`)가 8080을 쓰므로 로컬 포트는 다른 번호(예: 18081)를 쓴다.

## AWS 묶음과 맞춰야 할 것 (환경 담당에게 요청)

| 항목 | 지금 | 요청 | 이유 |
| --- | --- | --- | --- |
| 관리 수신부 | patch로 붙임. 03 재적용 때 남아 smoke 증거에 섞일 수 있음 | `engine.yaml.template`에 사이드카 컨테이너 추가 | 떼고 붙이는 절차 없이 환경 증거와 runtime 구성을 일치시키려고 |
| 엔진 접근 정책 | observer 8001 경로 없음 | `network-policies.yaml`의 정책을 함께 적용 | 관측 조회 경로 |

엔진 캐시는 묶음 구조(현재 세대 폴더만 컨테이너 `/cache`에 마운트)를 그대로 쓴다. intent에 `assets.cache_mount_path: "/cache"`를 두어 Tetragon 캐시 규칙(TG-CACHE-01)이 `/cache`를 감시하고, 호스트 쪽 `cache_root`(`/srv/serving/cache`)와 세대 경로는 03이 만드는 `/srv/serving/cache/vllm/<digest hex>/<revision>/<profile>-profile-01`과 같게 둔다. 엔진은 다른 세대를 볼 수 없으므로 컨테이너 안 쓰기는 모두 "현재 세대", 그 밖은 "캐시 밖"으로 분류된다. 호스트에서 `serving-runtime enforce cache-check --intent deploy/gpu-lab/intent.aws-lab.json`으로 세대·권한을 점검한다.

**주의 — CACHE-02(다른 세대 쓰기)의 근거가 바뀐다.** 이 구성에서는 runtime 사건으로 다른 세대 쓰기를 탐지하지 않는다. "엔진 Pod에는 현재 세대 폴더만 붙는다"는 배치 사실이 그 근거이며, runtime은 이 사실을 스스로 확인하지 않는다. 만약 `/cache`에 cache root 전체가 붙으면 다른 세대 쓰기도 "현재 세대"로 분류되어 조용히 놓친다. 그래서 배포·03 실행·프로파일 변경 뒤마다 아래를 확인해 증거로 남긴다.
```bash
kubectl --context byul-aws-lab -n serving-lab get deploy serving-engine -o jsonpath='{range .spec.template.spec.volumes[?(@.name=="cache")]}{.hostPath.path}{"\n"}{end}{range .spec.template.spec.containers[?(@.name=="engine")].volumeMounts[?(@.name=="cache")]}{.mountPath}{"\n"}{end}'
# 첫 줄 = intent의 cache_root/<engine>/<image_id>/<model_revision>/<compile_profile>, 둘째 줄 = /cache 여야 한다
```

## intent에서 서버 확인 후 채울 값

- `REPLACE_IMAGE_DIGEST_HEX`, `REPLACE_PROFILE`(dev/reference): AWS 묶음 03이 정한 캐시 경로와 같게(`images.lock`과 `LAB_PROFILE`). **dev↔reference를 바꾸면 intent의 `compile_profile`도 바꾼다**(안 바꾸면 cache-check가 `current_generation_missing`, 이전 프로파일 세대는 `other_generations_present`로 보임).
- `allowed_engine_binaries`·`phase_binaries`: 공식 이미지의 실제 실행 파일. 지금 값(`/usr/local/bin/vllm-nonroot-entrypoint.sh`, `/usr/bin/python3`)은 시작안이며 첫 Tetragon 관측으로 확정한다.
- `enforce render`의 `pod-security-candidate.json`은 엔진 그룹을 UID와 같게(2000) 쓰고 캐시 환경변수도 `VLLM_CACHE_ROOT=/cache` 하나만 둔다. AWS 묶음은 GID 0이고 `VLLM_CACHE_ROOT=/cache/vllm`, `HOME=/cache/home` 등 하위 폴더를 쓰므로 이 후보 파일을 그대로 적용하지 않는다(참고용).

## 확인 방법과 확인하지 않은 것

```bash
kubectl kustomize deploy/gpu-lab                          # 렌더링만
serving-runtime enforce render --intent deploy/gpu-lab/intent.aws-lab.json --out /tmp/render
```

- 실제 AWS 적용·실행, Gateway의 hostPath 읽기 권한(10010 그룹)·SELinux/AppArmor 영향
- 사이드카가 nvidia RuntimeClass Pod 안에서 정상 기동하는지
- 관리 수신부 우회(같은 Pod의 loopback 공유) 시험 — AWS 묶음 README도 별도 시험 대상으로 둠
- observer 수집 프로그램, Gateway의 클러스터 노출 방식(현재 127.0.0.1 bind)
- SGLang 전환(AWS 묶음 범위는 vLLM만)
