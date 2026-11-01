# AWS GPU lab — runtime 배포 층 (초안)

AWS 공통 GPU 시험환경 설치 묶음(`aws-handoff-20261026`, 이하 "AWS 묶음") **위에 얹는** runtime 쪽 파일이다. 엔진·namespace·계정·엔진 격리 정책은 AWS 묶음이 만들고, 여기서는 Gateway·관리 수신부·observer 자리·추가 통신 정책·intent·호스트 수집기만 더한다. 2026-11-01 dev에서 통합 층과 AWS-01~03을 실행했다. 저장소의 서버별 값은 `REPLACE…`로 비워 두고 서버 사본에서 채운다.

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
| `target.aws-lab.json` | 정책 묶음의 `target` 역할에 넣을 대상 등록 예시. image digest를 실제 값으로 교체해야 함 |
| `host/byul-observation-feed.service` | AWS 호스트에서 수집기를 계속 돌리는 systemd 사용자 서비스 |
| [`ACCEPTANCE.md`](ACCEPTANCE.md) | AWS 첫 완료 기준 8개 사례의 실행·증거·비용 절약 절차 |
| `policy.aws-lab.json` | 정책 묶음의 gateway policy 역할에 넣을 LAB 시험 정책(아래 "LAB 시험값") |
| `policy-approvals.aws-lab.json` | 위 정책의 LAB 승인 목록(SHA-256 대조). runtime 자체 기록이며 정식 승인 아님 |
| `perf-plan.aws-lab.json` | B0/B1/B2 성능 비교 계획(`tools/perf_compare.py`) |

## LAB 시험값 (2026-10-27, runtime 선정 · 다른 담당 확인 전)

정책/검증·시험 담당의 값을 받기 전에 runtime이 정한 시험 출발값이다. 기업 운영값이나 정식 승인값이 아니다. update 점검(REQ-01·09·10·13, OBS-11)과 같은 시험에서 결과를 이으려면 같은 값을 써야 한다.

| 값 | 설정 | 근거 |
| --- | --- | --- |
| 허용 모델 | `test-model` | AWS 묶음의 served model(Qwen2.5-0.5B-Instruct) |
| 답변 기본/최대 | 128 / 256 토큰 | AWS-03이 최대+1(257)의 403을 확인 |
| 질문+답변 한도 | 2048 토큰 | 엔진 `max model len 2048`과 같게 둬 Gateway 통과 뒤 엔진 거부가 섞이지 않게 함 |
| 본문 크기 / 답변 개수 | 64 KiB / 1 | 기존 값 유지 |
| 비-stream 응답 대기 | 60초 | 최대 256토큰의 GPU 생성 속도를 아직 측정하지 않아 5초 504 오판을 피하는 LAB 시험값(요청 deadline 300초 미만) |
| 정책 기간 | 2026-10-27 ~ 2026-12-31 UTC | 만료 시 503이므로 발표 뒤까지 둠 |
| 요청 옵션 | 추가 옵션 없음(schema 1.1) | 첫 인수 사례 8개에 필요 없음. 옵션 범위 시험은 별도 판본에서 정함 |
| 성능 비교 | 동시 2, 답변 128, 예열 20·일반 100·stream 100, 3블록 | 엔진 `max num seqs 2`, `perf_compare.py` 기본 예산 |

정책 파일을 한 글자라도 고치면 SHA-256이 바뀌어 Gateway가 503으로 막는다. 고칠 때는 `policy_version`을 올리고 `sha256sum policy.aws-lab.json` 값으로 승인 목록을 함께 갱신한다. 성능 계획의 B0/B1/B2 주소는 운영자가 준비하는 Gateway 구성의 자리값이다. 인증이 고정된 이 Gateway로 성능 비교를 실행할 때는 소유자만 읽는 인수용 token 파일에서 inference token을 읽힌다. 옵션을 생략하면 기존처럼 Authorization 헤더를 보내지 않는다.

```bash
.venv/bin/python -B tools/perf_compare.py deploy/gpu-lab/perf-plan.aws-lab.json \
  --token-env-file ~/runtime-acceptance.env \
  --out-dir evidence/perf
```

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

   정책 묶음은 platform이 `/srv/serving/bundles/<bundle_id>/`에 쓰고 `current`가 그 폴더를 가리키게 한다(`runtime/docs/결정기록_정책묶음_수신.md`). Gateway 컨테이너에는 이 폴더가 `/etc/serving/bundles`로 보이므로 **`current`는 상대 링크**(`ln -sfn <bundle_id> current`)여야 한다. 절대 링크(`/srv/serving/bundles/<id>`)는 컨테이너 안에서 풀리지 않아 Gateway가 시작하지 않는다. 묶음 폴더와 파일은 Gateway(UID 10000)가 읽을 수 있어야 한다(예: 그룹 10000, 폴더 0750·파일 0640). 묶음은 03 완료 뒤 서버의 `images.lock`에 기록된 엔진 digest를 확인하고 서버 사본에서 만든다. 묶음의 gateway policy는 `allowed_models`에 `test-model`을, `enforcement_intent` 역할에 `intent.aws-lab.json`(값을 채운 것)을 넣는다. 이 역할의 항목 이름은 `schema_version`이지만 intent 본문은 `schema`를 쓴다. `target` 역할에는 `target.aws-lab.json`의 `REPLACE_IMAGE_DIGEST_HEX`를 실제 엔진 image digest hex로 바꾸고 넣는다. gateway policy의 `target_id`, bundle의 `target_ref`, target 등록의 `target_id`는 모두 `serving-engine-aws-lab`로 맞춘다. 대상 등록의 Kubernetes 값은 context `byul-aws-lab`, namespace `serving-lab`, selector `app=serving-engine`, container `engine`이어야 한다. vLLM 판본은 등록 schema가 허용하므로 이 환경의 `0.31.0`을 선언한다.
2. **수집기 실행** — byul 저장소를 `~/byul`에 두고 `host/byul-observation-feed.service`의 주석대로 설치한다. `--kube-context byul-aws-lab --reader-group 10010 --target-selector app=serving-engine --target-interval 5`로 돌며, Tetragon 프로세스 인자를 지운 뒤 기록한다. Hubble/Tetragon 상태는 `/srv/serving/observation/*.heartbeat.json`, 대상 Pod는 `/srv/serving/observation/target-pods.json`에 원자 교체로 기록한다. Pod 조회 실패나 4초의 kubectl 제한 초과 때 이전 items를 재사용하지 않고 `state: error`로 바꾼다.

   대상 스냅샷의 타이밍은 **writer 간격 + kubectl timeout + Gateway poll 간격 < Gateway stale window**여야 한다. Gateway 기본값은 poll 5초 / stale 15초이고, 이 서비스는 writer 5초 / kubectl timeout 4초이므로 최악 age는 14초로 15초보다 작다. CLI는 이 기본 조합을 지키기 위해 `--target-interval`의 5초 초과 값을 거부한다.
3. **Gateway 이미지 넣기** — 레지스트리가 없으므로 노드의 containerd에 직접 넣는다.
   ```bash
   # 노트북(runtime/ 폴더). base는 digest로 고정
   docker build -f deploy/gateway/Dockerfile --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> -t serving-gateway:<tag> .
   docker inspect --format '{{.Id}}' serving-gateway:<tag>        # 빌드 기록의 이미지 digest
   docker save -o serving-gateway-<tag>.tar serving-gateway:<tag>
   ssh ubuntu@<host> 'sudo ctr -n k8s.io images import -' < serving-gateway-<tag>.tar
   ```
   매니페스트는 `serving-gateway:<tag>` + `imagePullPolicy: Never`로 참조한다(레지스트리 digest가 없어 tag 참조). 이 실행에서 `docker inspect Id`와 서버의 `sudo ctr -n k8s.io images ls` digest는 manifest digest였고, Pod의 `imageID`는 다른 config digest였다. `ctr images ls` digest는 빌드 기록의 manifest digest와 대조한다. tar의 `manifest.json`에서 `Config` 파일명을 읽어 `tar -xOf serving-gateway-<tag>.tar <Config 파일명> | sha256sum`으로 config digest를 구하고, 이를 `kubectl get pod -o jsonpath='{..imageID}'`의 `sha256:…`와 대조한다. 두 digest를 직접 같다고 판정하지 않는다. `REPLACE_GATEWAY_TAG`를 `<tag>`로 바꾼다.
4. **인증 Secret** — `kubectl --context byul-aws-lab -n serving-lab create secret generic serving-gateway-auth --from-file=auth.json`(토큰 해시만 든 `runtime-auth/1.0`).
5. **적용**
   ```bash
   kubectl --context byul-aws-lab apply -k deploy/gpu-lab
   kubectl --context byul-aws-lab -n serving-lab patch deployment serving-engine \
     --type strategic --patch-file deploy/gpu-lab/engine-management-sidecar.patch.yaml
   ```
   patch는 묶음 `engine.yaml.template`의 엔진 args를 유지하면서 `--enable-log-requests`, `--enable-request-id-headers`를 더한다. 첫 옵션은 AWS-01의 `Received request` 로그 매칭에 필요한 전제이고, 둘째는 응답의 `X-Request-Id` 헤더를 켠다. patch는 엔진 Pod를 다시 만든다(Recreate: GPU 반납 후 재기동·cold start). 시험 시간 계획에 넣는다. vLLM 0.31.0의 `request_logger.py`는 INFO에 요청 ID·sampling params를, DEBUG에는 prompt·토큰 ID·JSON 본문을 남길 수 있다. 시험에는 무해한 고정 문장만 쓰고 원시 엔진 로그를 반출할 때는 요청 내용이 담긴 기록으로 취급한다.
   적용한 이미지 tag·patch·실제 Deployment/Pod imageID·정책과 run ID는 **통합 기록**으로 저장하고, 03의 `evidence/smoke-*`(기반 점검)와 섞지 않는다. 일반 시험 중에는 사이드카를 그대로 둔다. 03을 다시 돌릴 때만 아래 "03 기준 상태와 runtime 통합 층" 절차로 뗐다가 다시 붙인다.
6. **세부 통제 정책** — `serving-runtime enforce render --intent deploy/gpu-lab/intent.aws-lab.json --out <dir>`로 Cilium·Tetragon 후보를 만들어 검토 후 적용한다. 이 intent의 빈 `engine_egress_allow`는 DNS 허용 없이 Cilium의 deny-all egress 규칙으로 렌더돼 AWS 묶음의 "엔진 egress 없음"과 맞는다. AWS 인수 확인은 [`ACCEPTANCE.md`](ACCEPTANCE.md)의 `tools/aws_lab_acceptance.py`를 사용한다. kind 전용 `tools/k8s_lab_check.py`는 fake engine과 관리 정책을 생성·삭제하므로 AWS 엔진에 사용하지 않는다.
7. 접속: 서버에서 `kubectl --context byul-aws-lab -n serving-lab port-forward deploy/serving-gateway 8080`, 노트북에서 SSH 터널(`ssh -L 18081:127.0.0.1:8080 ubuntu@<host>`). 노트북의 개발 서버(`byul-dev`)가 8080을 쓰므로 로컬 포트는 다른 번호(예: 18081)를 쓴다.

## 03 기준 상태와 runtime 통합 층 (AWS 묶음 방침)

AWS 묶음의 03(`03-engine-smoke.sh`)은 **엔진 컨테이너만 있는 기준 상태**를 점검한다. runtime의 사이드카·관리 조회 정책·Cilium/Tetragon 통제는 엔진 템플릿(`engine.yaml.template`)에 넣지 않고, 이 폴더의 patch와 `apply -k`로 **별도 통합 층**으로 붙인다. 03은 남은 추가 컨테이너·init·volume과 추가 NetworkPolicy·TracingPolicy를 발견하면 중단하며 다른 팀 설정을 자동 삭제하지 않는다. `scale 0`이나 `apply`만으로는 patch로 붙인 사이드카가 지워지지 않는다.

03 재검증이 필요할 때의 순서 (`check_baseline.py`는 엔진 args를 검사하지 않으므로 직접 복원한다):

1. 통합 증거(Gateway 기록·인수 시험 `evidence/`·통합 기록)를 먼저 반출한다.
2. 아래 회수 목록을 실제로 적용한 자원 이름 그대로 회수한다.
3. 엔진을 `kubectl --context byul-aws-lab -n serving-lab scale deployment/serving-engine --replicas=0` 하고 Pod 종료를 확인한다.
4. 묶음 안내대로 03을 실행하고 `evidence/smoke-*`를 기반 점검 결과로 보관한다.
5. 위 5·6단계로 통합 층을 다시 붙이고 통합 기록을 새로 저장한다.

| 회수 대상 | 회수 방법 |
| --- | --- |
| 사이드카 컨테이너 `management-listener`·엔진 로그 args | `engine-management-sidecar.patch.yaml` 주석의 단일 strategic patch 명령으로 사이드카를 삭제하고 묶음의 엔진 args 15개 항목을 복원 |
| NetworkPolicy `engine-management-observer` | `kubectl --context byul-aws-lab -n serving-lab delete networkpolicy engine-management-observer` |
| 6단계에서 적용한 CiliumNetworkPolicy·TracingPolicy | 적용할 때 기록한 정확한 이름으로만 삭제 |
| Service `serving-engine-management` 등 나머지 runtime 자원 | 03 검사 대상이 아니면 유지 가능. 전부 내릴 때는 `kubectl --context byul-aws-lab delete -k deploy/gpu-lab` |

사이드카만 떼는 것으로 03 기준 복원이 끝나지 않는다. 회수 후 실제 Deployment의 엔진 args가 묶음 `engine.yaml.template`과 같은지도 확인한다. 회수와 재적용 모두 적용 기준 commit과 회수 목록을 함께 기록한다.

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

- reference 환경의 실제 AWS 적용·실행, Gateway의 hostPath 읽기 권한(10010 그룹)·SELinux/AppArmor 영향
- host가 쓴 `target-pods.json`을 Gateway가 읽고 rollout 뒤 `instance_changed`(`pod_replaced`)를 남기는지
- 사이드카가 nvidia RuntimeClass Pod 안에서 정상 기동하는지
- 관리 수신부 우회(같은 Pod의 loopback 공유) 시험 — AWS 묶음 README도 별도 시험 대상으로 둠
- observer 수집 프로그램, Gateway의 클러스터 노출 방식(현재 127.0.0.1 bind)
- SGLang 전환(AWS 묶음 범위는 vLLM만)
