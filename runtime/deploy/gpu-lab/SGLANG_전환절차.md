# SGLang v0.5.22 dev 전환 절차 (실행 전 검토본)

이 절차는 GPU 1장인 AWS dev lab에서 **순차 전환**할 때 사용한다. 여기 적힌 명령은 저장소에서 실행하지 않았다. 서버 주소와 비밀값은 증거에 넣지 않는다. 서버에서 적용 전 `kubectl config current-context`가 `byul-aws-lab`인지 확인하고, 현재 Deployment·묶음 ID·imageID와 AWS 묶음이 생성한 vLLM `engine.yaml` 사본(`<saved-vllm-engine.yaml>`)을 별도 증거 폴더에 보관한다. `03-engine-smoke.sh`와 AWS 설치 묶음 원본은 수정하지 않는다.

**서버를 켜기 전**, 연결된 작업 환경에서 `skopeo inspect --format '{{.Digest}}' docker://docker.io/lmsysorg/sglang:v0.5.22`로 레지스트리 digest를 확정하고 반입 tar와 함께 기록한다. 아래 서버 전환에서는 그 고정값 `sha256:<digest>`만 사용한다.

## 1. vLLM 끄기와 이미지 반입

```bash
# 복원용 저장: 서버 전용 필드를 뺀다. 그대로 저장하면 복귀 apply가 resourceVersion 충돌로 실패한다.
kubectl --context byul-aws-lab -n serving-lab get deployment serving-engine -o json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("status",None); m=d["metadata"]; [m.pop(k,None) for k in ("resourceVersion","uid","creationTimestamp","generation","managedFields")]; m.get("annotations",{}).pop("kubectl.kubernetes.io/last-applied-configuration",None); m.get("annotations",{}).pop("deployment.kubernetes.io/revision",None); json.dump(d,sys.stdout,indent=2)' \
  > evidence/vllm-engine-before.json
# 삭제 후 새로 만든다. apply로 덮으면 vLLM 환경변수·patch 잔여물이 섞일 수 있다.
kubectl --context byul-aws-lab -n serving-lab delete deployment serving-engine --wait=true
# 서버 전 확정한 digest를 대조한 tar를 서버의 ctr -n k8s.io images import로 반입
sudo ctr -n k8s.io images import <sglang-이미지.tar>
sudo ctr -n k8s.io images ls | grep sglang
```

서버 사본의 `deploy/gpu-lab-sglang/engine.yaml`, `deploy/gpu-lab-sglang/target.aws-lab.json`, `deploy/gpu-lab-sglang/intent.aws-lab.json`에 **같은 승인 digest**를 채운다. 호스트 `/srv/serving/cache/sglang/<digest>/<model-revision>/dev-profile-01`을 UID 2000, GID 0, mode 0700으로 만들고 `REPLACE_CACHE_PATH`를 그 절대 경로로 바꾼다. CPU·메모리 자리값은 현 dev profile로 채운다. `serving-sglang-admin` Secret은 서버의 소유자만 읽는 파일에서 만든다. 파일 값·생성 명령의 shell 기록이 증거에 들어가지 않게 한다.

```bash
kubectl --context byul-aws-lab -n serving-lab create secret generic serving-sglang-admin --from-file=admin-api-key=<비공개-파일>
# secret 값 출력 없이 이름·key 존재만 확인
kubectl --context byul-aws-lab -n serving-lab get secret serving-sglang-admin -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
```

이미지는 `v0.5.22@sha256:<digest>`로 고정한다. 이미지 기본 명령은 `/bin/bash`이므로 manifest의 셸이 파일 Secret을 읽고 `python3 -m sglang.launch_server`를 실행한다. `SGLANG_RUST_SERVER=0`, 8000, `--enable-metrics`, 로그 레벨 0을 유지한다. Secret의 group-read(0440)와 UID 2000/GID 0으로 읽을 수 있는지 확인한다. 이 이미지에서 UID 2000 부팅에 실패하면 실제 오류를 기록하고 권한·쓰기 경로를 조사한다. root 전환은 이 파일의 자동 절차에 넣지 않았다.

## 2. SGLang 켜기, Gateway·대상·규칙 전환

정책 묶음은 [정책_수동적용_절차.md](정책_수동적용_절차.md)의 승인 판본 → 변환 → 해시·크기 대조 → 검증 → 상대 링크 교체 → Gateway 재시작 순서를 따른다. SGLang도 vLLM과 같은 플랫폼 승인 판본 `aws-lab-policy-001`(승인 번호 `aws-lab-policy-001-v2`, 시험용 자기 신고 승인)의 **같은 정책 파일**을 쓴다. 엔진별 target·intent는 실제 digest와 서버 값으로 새 묶음에 넣고 `bundle verify`를 먼저 실행한다. `target.engine.version`은 생략한다. `image_digest`를 고정하고 Pod 스냅샷의 imageID와 비교한다.

```bash
cd ~/byul/runtime
.venv/bin/python -B -m serving_runtime bundle verify --bundle-dir <새-bundle-폴더>
kubectl --context byul-aws-lab kustomize deploy/gpu-lab-sglang > evidence/sglang-rendered.yaml
kubectl --context byul-aws-lab apply -k deploy/gpu-lab-sglang
kubectl --context byul-aws-lab -n serving-lab rollout status deployment/serving-engine --timeout=25m
# 수집기: host 사용자 service의 SGLang판은 --phase-timeline 없이 실행
cp deploy/gpu-lab-sglang/byul-observation-feed.service ~/.config/systemd/user/byul-observation-feed.service
systemctl --user daemon-reload && systemctl --user restart byul-observation-feed
# intent의 engine=sglang 세대로 규칙 후보 렌더 후 검토·적용
.venv/bin/python -B -m serving_runtime enforce render --intent deploy/gpu-lab-sglang/intent.aws-lab.json --out <규칙-출력-폴더>
# 정책 묶음 current를 검증된 새 상대 링크로 전환한 뒤 Gateway 재시작
kubectl --context byul-aws-lab -n serving-lab rollout restart deployment/serving-gateway
```

기본 `deploy/gpu-lab/kustomization.yaml`과 vLLM 파일은 변경하지 않는다. `deploy/gpu-lab-sglang/kustomization.yaml`은 Gateway backend 인자를 바꾸고 단계 시간표 인자를 제거한다. 중간 서버에는 `/metrics`, `/ready`, `/v1/models`만 `--allow-path`로 지정한다. 실제 Pod의 인자·환경변수 **이름**·imageID·실행 계정·포트·마운트·Secret 읽기 가능 여부를 조회해 증거로 남긴다(환경변수 값과 Secret 값 제외).

## 3. 직접 확인과 시험

```bash
kubectl --context byul-aws-lab -n serving-lab get pods -l app=serving-engine -o json > evidence/sglang-pods.json
kubectl --context byul-aws-lab -n serving-lab exec deploy/serving-engine -c engine -- id
kubectl --context byul-aws-lab -n serving-lab exec deploy/serving-engine -c engine -- python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:8000/ready").status)'
kubectl --context byul-aws-lab -n serving-lab exec deploy/serving-engine -c engine -- python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/v1/models")); print([(x["id"],x.get("max_model_len")) for x in d["data"]])'
# 사람이 한 번만 호출하고 version만 기록. 원문은 실행 설정을 포함하므로 저장하지 않음.
kubectl --context byul-aws-lab -n serving-lab exec deploy/serving-engine -c engine -- python3 -c 'import json,urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/server_info")); print(d.get("version"))'
```

Pod 표준 출력 JSON 로그의 `request.received`와 `request.finished`에서 정상 chat·completions, 일반·stream의 `headers.x-request-id`를 각각 확인한다. 로그에 `text`·`input_ids`·프롬프트가 보이면 증거에 필드 이름만 적고 원문 반출을 중단한다. `/ready` 조회만으로 생성 로그가 생기지 않는지 대조한다. 관리 경로(`/update_weights_from_disk` 등)는 **GET처럼 상태를 바꾸지 않는 호출**에서 접근 불가를 확인한다. 관리 키를 넣은 변경 호출은 하지 않는다. 다른 Pod에서 8000과 관리 경로로 직접 접속할 수 없는지도 확인한다.

Gateway 포워드와 토큰 파일이 준비된 시험 세션에서 다음을 vLLM과 SGLang에 같은 목록으로 실행한다. 기본 실행은 AWS-01~11이며 AWS-12~19는 `--cases`로 명시한다. AWS-07·08·12~17의 변경 사례는 각각 도구가 요구하는 `--allow-*` 옵션을 의도적으로 부여한다. SGLang AWS-11·16·17은 단계 수집이 없어 `unknown`으로 기록한다. AWS-18은 `usage.completion_tokens`가 없으면 `unknown`, stream `[DONE]` 누락은 잘림으로 기록한다. 이 사례는 Pod 내부의 `/tokenize`를 stdin으로 호출해 입력+답변 경계를 찾으며 찾지 못하면 `unknown`이다. 토큰 계산 실패의 의도적 주입은 가짜 엔진 시험만 수행한다. AWS-19는 정상 요청 ID가 같은 로그 구간에 없으면 거부 미전달을 인정하지 않는다.

```bash
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab --engine sglang --token-env-file <비공개-토큰-파일> --cases AWS-01,AWS-02,AWS-03,AWS-18,AWS-19
# 나머지 AWS-04~17은 ACCEPTANCE.md의 사전 조건과 허용 플래그를 확인한 뒤 선택
```

## 4. vLLM 복귀와 증거 반출

```bash
kubectl --context byul-aws-lab -n serving-lab delete deployment serving-engine --wait=true
kubectl --context byul-aws-lab apply -k deploy/gpu-lab
# 저장본에 사이드카와 로그 옵션이 포함되어 있어 patch를 다시 적용하지 않는다.
kubectl --context byul-aws-lab -n serving-lab apply -f evidence/vllm-engine-before.json
kubectl --context byul-aws-lab -n serving-lab rollout status deployment/serving-engine --timeout=25m
cp deploy/gpu-lab/host/byul-observation-feed.service ~/.config/systemd/user/byul-observation-feed.service
systemctl --user daemon-reload && systemctl --user restart byul-observation-feed
# 검증된 이전 vLLM 묶음으로 상대 링크를 되돌린 뒤 Gateway 재시작
kubectl --context byul-aws-lab -n serving-lab rollout restart deployment/serving-gateway
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab --engine vllm --token-env-file <비공개-토큰-파일> --cases AWS-01,AWS-02
```

복귀 뒤 정상 1건과 거부 1건, 대상 digest·포트·단계 수집 재개를 확인한다. `evidence/`의 보고서·Pod 설정·로그 필드 목록·요청 목록 해시·정책 묶음 해시와 각 판정 사유를 반출한다. 원시 Secret, 토큰, 서버 주소, 프롬프트 내용은 반출하지 않는다. 중단 시에도 이전 vLLM 묶음과 Deployment를 복구하고 정상·거부 각 1건을 확인한다.
