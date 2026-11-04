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
# 11/3 서버 확인: 태그+digest 이름(docker.io/lmsysorg/sglang:v0.5.22@sha256:...)으로만 있으면 kubelet이 찾지 못해 ErrImageNeverPull.
# kubelet은 digest만 있는 이름으로 찾으므로 같은 digest를 이 이름으로도 등록한다(내용이 이미 있으면 수 초).
sudo ctr -n k8s.io images pull docker.io/lmsysorg/sglang@sha256:<digest>
sudo ctr -n k8s.io images ls | grep sglang   # 두 이름이 모두 보여야 한다
```

서버 사본의 `deploy/gpu-lab-sglang/engine.yaml`, `deploy/gpu-lab-sglang/target.aws-lab.json`, `deploy/gpu-lab-sglang/intent.aws-lab.json`에 **같은 승인 digest**를 채운다. 호스트 `/srv/serving/cache/sglang/<digest>/<model-revision>/dev-profile-01`을 UID 2000, GID 0, mode 0700으로 만들고 `REPLACE_CACHE_PATH`를 그 절대 경로로 바꾼다. CPU·메모리 자리값은 현 dev profile로 채운다. **캐시 폴더 안의 하위 폴더도 미리 만든다.** 엔진은 `TMPDIR=/cache/tmp` 등 하위 폴더가 있다고 가정하며, 없으면 첫 커널 컴파일에서 `nvcc fatal: Could not open output file '/cache/tmp/...'`로 기동이 실패한다(11/3 서버 확인). `for d in tmp home config xdg hf inductor triton cuda pycache; do sudo install -d -o 2000 -g 0 -m 0700 <캐시경로>/$d; done`. 처음 기동(캐시가 빈 상태)에서는 커널 JIT 컴파일 때문에 `nvcc`·`ninja`가 `/bin/sh`를 여러 번 실행해 의심 실행 기록이 남는다. 캐시가 만들어진 뒤의 재시작에서는 나오지 않았다. `serving-sglang-admin` Secret의 `config.yaml`에는 관리 키만 넣는다. 서버의 소유자만 읽는 비공개 파일로 만들고 키 값·설정 원문·생성 명령의 shell 기록을 증거에 넣지 않는다.

서버의 비공개 `config.yaml`에는 `admin-api-key`의 실제 값만 YAML 문자열로 적는다. 엔진의 나머지 옵션(`--context-length 2112` 포함)은 검토 가능한 `engine.yaml`의 `args`에 둔다. 이 파일은 저장소·ConfigMap·증거 폴더 밖에 두고 소유자만 읽게 한다. 키 값은 명령 인자에 쓰지 않는다.

`--config`는 `/proc` cmdline 노출과 시작 셸만 없앤다. SGLang v0.5.22는 시작 로그의 `server_args`와 키 없이 조회되는 `/server_info`에 관리 키 값을 계속 노출한다(11/3 서버 관찰). 엔진 8000 직통 차단(AWS-04)을 유지하고, 로그·증거 반출 전에는 관리 키 값이 들어 있는지 검사해 가린다.

```yaml
admin-api-key: <비공개-관리-키>
```

```bash
kubectl --context byul-aws-lab -n serving-lab create secret generic serving-sglang-admin --from-file=config.yaml=<비공개-config.yaml> --dry-run=client -o yaml | kubectl --context byul-aws-lab -n serving-lab apply -f -
# 값 출력 없이 키 이름만 확인한다.
kubectl --context byul-aws-lab -n serving-lab get secret serving-sglang-admin -o go-template='{{range $k,$v := .data}}{{$k}}{{"\n"}}{{end}}'
```

기존 Secret에 이전 방식의 `admin-api-key` 데이터 키가 남아 있으면 `kubectl --context byul-aws-lab -n serving-lab patch secret serving-sglang-admin --type=json -p='[{"op":"remove","path":"/data/admin-api-key"}]'`로 제거하고, 위 조회에서 `config.yaml`만 남았는지 확인한다. 키 값은 출력하지 않는다.

SGLang v0.5.22 태그의 `python/sglang/srt/server_args.py`는 `--config`를 등록하고 `utils/server_args_config_parser.py`에서 YAML을 읽어 CLI 옵션으로 합친다. 이미지 안의 코드와 UID 2000/GID 0의 Secret 읽기·실제 기동은 다음 서버 회차에서 확인한다. `SGLANG_RUST_SERVER=0`, 포트 8000, metrics·로그 설정을 유지한다. `--config`가 실패하면 오류 시각과 키를 가린 메시지를 보존하고 이전 셸 시작 방식으로 되돌린다. 기준점 `2f5f461`의 `runtime/deploy/gpu-lab-sglang/engine.yaml`에서 셸 시작 부분을 가져오되, 서버별 image digest·캐시·자원 값을 채운 복구 사본을 먼저 준비한다. 비공개 키 파일에서 이전 `admin-api-key` Secret을 다시 만든 뒤 아래 명령으로 복원한다.

```bash
cp <서버별-값을-채운-이전-shell-engine.yaml> deploy/gpu-lab-sglang/engine.yaml
kubectl --context byul-aws-lab -n serving-lab create secret generic serving-sglang-admin --from-file=admin-api-key=<비공개-키-파일> --dry-run=client -o yaml | kubectl --context byul-aws-lab -n serving-lab apply -f -
kubectl --context byul-aws-lab apply -k deploy/gpu-lab-sglang
kubectl --context byul-aws-lab -n serving-lab rollout status deployment/serving-engine --timeout=25m
```

복구 시 Secret에 `config.yaml` 데이터 키가 남아 있으면 같은 방식으로 `/data/config.yaml`을 제거하고 `admin-api-key`만 남았는지 확인한다. 이전 방식은 `/bin/sh -ec`와 `cat`으로 키를 프로세스 인자에 넣으므로, 복귀가 성공해도 키 노출과 시작 셸 의심 경고를 알려진 제약으로 기록한다. Secret 값이나 서버 로그 원문을 반출하지 않는다.

## 2. SGLang 켜기, Gateway·대상·규칙 전환

정책 묶음은 [정책_수동적용_절차.md](정책_수동적용_절차.md)의 승인 판본 → 변환 → 해시·크기 대조 → 검증 → 상대 링크 교체 → Gateway 재시작 순서를 따른다. SGLang도 vLLM과 같은 플랫폼 승인 판본 `aws-lab-policy-001`(승인 번호 `aws-lab-policy-001-v2`, 시험용 자기 신고 승인)의 **같은 정책 파일**을 쓴다. 엔진별 target·intent는 실제 digest와 서버 값으로 새 묶음에 넣고 `bundle verify`를 먼저 실행한다. `target.engine.version`은 생략한다. `image_digest`를 고정하고 Pod 스냅샷의 imageID와 비교한다.

11/4 관찰을 반영한 SGLang intent는 추론 단계의 정확한 Triton `ptxas` 경로 하나를 `/opt/sglang/bin/python3` 부모 조건으로 추가한다. 서버에는 아직 재확인하지 않았다. 다음 빈 캐시 첫 기동에서 캡처 종료(15:57:27Z) 뒤 준비 완료(15:57:38Z) 전 셸 9건의 compiling·`compile_toolchain_parent` 설명과 계속되는 `suspect_binary` 경고, 준비 뒤 첫 요청의 `ptxas` 4건 경로·부모·허용 판정, 다른 셸 경고를 다시 대조한다. 준비 표지가 없으면 캡처 뒤 15초(관찰 간격 11초와 여유 4초)까지만 compiling이며, 캡처도 없으면 startupProbe 상한 20분까지만 열린다. 이후 단계는 `unknown`이다.

호스트 수집기와 Gateway 이미지는 새 `collector_state` 형식을 읽는 **같은 커밋**으로 함께 갱신한다. 수집기만 갱신하면 기존 Gateway가 `compile_open_from`·`compile_cap_at`·`capture_end_at`을 거부해 SGLang 단계가 모두 `unknown`이 되고 AWS-11·16·17도 `unknown`이 예상된다.

intent 바이트가 바뀌므로 서버별 `enforcement_intent` 파일을 새 묶음에 넣고 `bundle.json`의 해당 `files[]` 항목 `sha256`·`bytes`를 다시 계산한다. 새 묶음 ID로 로컬·서버 `bundle verify`를 수행하고 사람이 승인 판본·후보 규칙을 검토한 뒤 `current` 상대 링크와 Gateway를 전환한다. Gateway 정책 파일 바이트가 그대로면 `policy-approvals.aws-lab.json`의 `entries[]` 중 `policy_id=aws-lab-policy-001`, `policy_version=1`, `approval_ref=aws-lab-policy-001-v2`인 정책 해시 항목을 바꿀 이유는 없다. 정책 파일을 바꾸는 경우 그 승인 항목의 ID·판본·참조·해시를 사람이 다시 대조한다. 승인 기록은 자동으로 갱신하지 않는다.

`engine.yaml`은 `/ready` startup/readiness probe를 사용하고 Gateway backend는 `serving-engine` Service로 연결한다. Gateway 대상 대조는 Pod 실행·digest와 모델 정보는 확인하지만 컨테이너 `ready`를 직접 admission 조건으로 검사하지 않는다. 따라서 준비 전 엔진 미도달은 Service의 Ready Endpoint 제외 동작에 의존하며, Endpoint 반영 지연·직통 접속까지 이 코드만으로 배제하지 못한다. 다음 서버 회차에서 준비 전 Gateway 요청의 503 또는 연결 실패와 엔진 요청 로그 부재, Service Endpoint 상태를 함께 기록한다.

```bash
cd ~/byul/runtime
.venv/bin/python -B -m serving_runtime bundle verify --bundle-dir <새-bundle-폴더>
kubectl --context byul-aws-lab kustomize deploy/gpu-lab-sglang > evidence/sglang-rendered.yaml
kubectl --context byul-aws-lab apply -k deploy/gpu-lab-sglang
kubectl --context byul-aws-lab -n serving-lab rollout status deployment/serving-engine --timeout=25m
# 수집기: SGLang 단계 시간표와 엔진 종류를 켠다
cp deploy/gpu-lab-sglang/byul-observation-feed.service ~/.config/systemd/user/byul-observation-feed.service
systemctl --user daemon-reload && systemctl --user restart byul-observation-feed
# intent의 engine=sglang 세대로 규칙 후보 렌더 후 검토·적용
.venv/bin/python -B -m serving_runtime enforce render --intent deploy/gpu-lab-sglang/intent.aws-lab.json --out <규칙-출력-폴더>
# 정책 묶음 current를 검증된 새 상대 링크로 전환한 뒤 Gateway 재시작
kubectl --context byul-aws-lab -n serving-lab rollout restart deployment/serving-gateway
```

기본 `deploy/gpu-lab/kustomization.yaml`과 vLLM 파일은 변경하지 않는다. `deploy/gpu-lab-sglang/kustomization.yaml`은 Gateway backend만 SGLang으로 바꾸며 단계 시간표 인자를 유지한다. 중간 서버에는 `/metrics`, `/ready`, `/v1/models`만 `--allow-path`로 지정한다. 실제 Pod의 인자·환경변수 **이름**·imageID·실행 계정·포트·마운트·Secret 읽기 가능 여부를 조회해 증거로 남긴다(환경변수 값과 Secret 값 제외).

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

Gateway 포워드와 토큰 파일이 준비된 시험 세션에서 다음을 vLLM과 SGLang에 같은 목록으로 실행한다. 기본 실행은 AWS-01~11이며 AWS-12~19는 `--cases`로 명시한다. AWS-07·08·12~17의 변경 사례는 각각 도구가 요구하는 `--allow-*` 옵션을 의도적으로 부여한다. SGLang AWS-11·16·17도 현재 Pod UID의 신선한 단계 시간표가 있으면 판정한다. 근거가 없으면 `unknown`이다. AWS-18은 `usage.completion_tokens`가 없으면 `unknown`, stream `[DONE]` 누락은 잘림으로 기록한다. 이 사례는 Pod 내부의 `/tokenize`를 stdin으로 호출해 입력+답변 경계를 찾으며 찾지 못하면 `unknown`이다. 토큰 계산 실패의 의도적 주입은 가짜 엔진 시험만 수행한다. AWS-19는 정상 요청 ID가 같은 로그 구간에 없으면 거부 미전달을 인정하지 않는다.

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
