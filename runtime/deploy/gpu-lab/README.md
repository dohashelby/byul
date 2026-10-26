# 정식 GPU lab 배포 파일 (초안)

계획서 v1.4 §3.5(환경 기준), 부록 I.4(배치·권한), I.7(G0~G7)과 환경·배포 구성안을 따른 Kubernetes manifest 묶음이다. **아직 어떤 클러스터에도 적용하지 않았다.** 서버에서만 알 수 있는 값은 `REPLACE…`로 비워 두었고, 이 값을 지어내지 않았다.

## 구성

```
deploy/gpu-lab/
├─ base/                 엔진과 무관한 공통 자원
│  ├─ namespace.yaml     serving-lab, Pod Security "restricted"
│  ├─ storage.yaml       /srv/serving/{models,cache,runtime,bundles} local PV·PVC
│  ├─ serviceaccounts.yaml  역할별 계정(observer만 토큰 장착)
│  ├─ observer-rbac.yaml Pod 조회 전용 Role
│  ├─ gateway.yaml       serving-gateway (정책 묶음·인증 설정으로 시작)
│  ├─ observer.yaml      observer 자리(정책 대상 식별용)
│  ├─ acquire-job.yaml   모델 반입 Job(정지 상태, 명령 미정)
│  └─ test-pods.yaml     시험 receiver·other-client
└─ overlays/
   ├─ vllm/              vllm/vllm-openai v0.31.0 + 관리 수신부 사이드카
   └─ sglang/            lmsysorg/sglang v0.5.21 + 관리 수신부 사이드카
```

두 엔진은 같은 GPU에서 **하나씩만** 올린다. 엔진을 바꿀 때는 이전 overlay를 지운 뒤 다른 overlay를 적용한다.

## 역할과 권한

| 역할 | 계정 | UID | 볼륨 | 비고 |
| --- | --- | --- | --- | --- |
| Gateway | serving-gateway | 10000(프로젝트 선택값) | 기록 쓰기, 정책 묶음 읽기 전용, 인증 Secret | 토큰 미장착, 127.0.0.1 bind라 port-forward로 접근 |
| 엔진 | serving-engine | 10001(I.4) | 모델 읽기 전용, 자기 캐시 세대만 쓰기 | GPU 1, 오프라인 변수, 토큰 미장착 |
| 관리 수신부 | 엔진 Pod 사이드카 | 10000 | 없음 | 엔진 127.0.0.1:8000의 읽기 경로만 8001로 중계 |
| 모델 반입 | artifact-acquire | 10002(I.4) | 모델 쓰기 | 승인 출처만 송신(Cilium acquire 정책) |
| observer | serving-observer | 10004(프로젝트 선택값) | 없음 | serving-lab Pod 조회만 |
| 시험 대상 | serving-probe | 10003(I.4) | 없음 | 등록된 허용·거부 시험에만 사용 |

계획서 I.4는 10001·10002·10003만 정한다. Gateway 10000과 observer 10004는 겹치지 않게 고른 프로젝트 선택값이다.

## 적용 전에 채울 값

| 자리 | 채우는 시점 | 확인 방법 |
| --- | --- | --- |
| `REPLACE_NODE_NAME` | G0 | `kubectl get nodes` |
| 이미지 digest 4종(`REPLACE_WITH_…_DIGEST`), Gateway 레지스트리 | G2 | 받은 이미지의 digest. `docs/외부기능_활용목록.md`에도 기록 |
| `REPLACE_MODEL_DIR` | G2~G3 | 반입한 모델·토크나이저 디렉터리(revision 고정) |
| `REPLACE_IMAGE_ID`·`REPLACE_MODEL_REVISION` | G3 | 캐시 세대 경로(§4.9)와 intent `assets.cache_generation` |
| 모델 반입 명령(`acquire-job.yaml`) | 반입 경로 결정 후 | Hugging Face 직접 / 내부 보관소 |
| 엔진 실행 인자 | G2 | 선정 판본 이미지의 실제 entrypoint·옵션 |

관리자는 적용 전에 `/srv/serving/…` 디렉터리와 소유자를 `storage.yaml` 주석대로 만든다. 정책은 platform이 만든 묶음을 `/srv/serving/bundles/current/`에 두고, 인증 설정은 Secret으로 만든다(토큰 해시만 넣음).

## 확인 방법

```bash
# 렌더링만 한다(클러스터에 연결하지 않음).
kubectl kustomize deploy/gpu-lab/overlays/vllm
kubectl kustomize deploy/gpu-lab/overlays/sglang

# Gateway 이미지(runtime/ 폴더에서). base 이미지는 digest로 고정한다.
# 설치 단계가 없어 --network none으로도 빌드된다(2026-10-26 로컬 확인 후 이미지 삭제).
docker build -f deploy/gateway/Dockerfile \
  --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> -t serving-gateway:<version> .
```

적용(`kubectl apply -k`)은 G0~G2를 마치고 위 값을 모두 채운 뒤, 대상 context를 `--context`로 명시해서 한다. 통신·행위 정책은 이 묶음이 아니라 `serving-runtime enforce render`로 intent에서 만든다.

## 확인하지 않은 것

- 실제 클러스터 적용, Pod Security "restricted" 통과 여부
- vLLM·SGLang 판본별 실행 인자, `readOnlyRootFilesystem`에서의 기동(쓰기 경로가 더 필요할 수 있음)
- local PV의 디렉터리 소유자·권한이 실제로 의도대로인지(G0·G3에서 `enforce cache-check`로 확인)
- observer 수집 프로그램(현재는 정책 대상 식별용 자리)
- Gateway를 클러스터 Service로 노출하는 방식(현재 127.0.0.1 bind, port-forward 전제)
