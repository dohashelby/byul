# 결정 기록: AWS lab의 vLLM 엔진 UID

- 날짜: 2026-10-26
- 범위: AWS 공통 GPU lab의 vLLM v0.31.0 엔진 계정 (runtime 측 결정, 사용자 확인)

## 결정

vLLM 엔진은 **UID 2000 / GID 0**과 공식 비root wrapper(`/usr/local/bin/vllm-nonroot-entrypoint.sh`)로 실행한다. AWS 설치 묶음(`aws-handoff-20261026`의 `engine.yaml.template`)과 같다. runtime intent(`deploy/gpu-lab/intent.aws-lab.json`)의 `assets.engine_uid`도 2000으로 둔다.

## 계획서와의 차이

개발계획서 v1.4 부록 I.4와 §4.9 표는 엔진 UID를 10001로 적었다. 이 값은 "엔진·모델 반입(10002)·시험(10003) 계정을 나누고 엔진은 자기 캐시만 쓴다"는 원칙을 보이는 예시이며, 같은 표가 "GPU device·읽기 그룹·volume 권한 확인"을 조건으로 둔다. 계획서 원문은 고치지 않고, 원칙(계정 분리, 엔진은 캐시만 쓰기, 모델 읽기 전용)은 그대로 지킨다.

## 이유

- vLLM 0.31.0 공식 문서의 비root 실행 방식이 이 UID/GID와 wrapper를 전제한다([docs/deployment/docker.md#run-as-a-non-root-user](https://github.com/vllm-project/vllm/blob/v0.31.0/docs/deployment/docker.md#run-as-a-non-root-user)). AWS 묶음의 독립 검토도 임의 UID는 공식 지원 범위 밖이라고 지적했다.
- 실제로 설치할 환경이 이미 이 값으로 캐시·홈 경로 소유권을 준비한다.

## 남은 확인

- GID 0(root 그룹)이므로 모델·정책·기록 폴더가 root 그룹 쓰기를 허용하지 않는지 G0·G3에서 확인한다(AWS 묶음은 모델 소유자 10002, 읽기 전용 mount).
- SGLang 이미지는 다른 규칙일 수 있어 전환 때 따로 확인한다.
- `enforce render`의 Pod 보안 후보 파일은 엔진 그룹을 UID와 같게 쓰므로 AWS 환경에 그대로 적용하지 않는다.
