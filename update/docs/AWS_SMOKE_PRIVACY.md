# AWS 최초 인수의 원문 저장 보수

`tools/prepare_aws_smoke.py`는 검토한 2026-10-26 인계의 SHA256SUMS와19파일 bytes를 확인하고 저장소 밖 새 작업 사본만 만든다. 원본·01-host·02-cluster·설치 고정 판본·모델 manifest는 보존한다. 원본 DB·로그나 인계 전체를 Git에 넣지 않는다. 이 도구는 AWS·kubectl·설치·엔진을 실행하지 않으며, 생성 성공은 설치나 GPU 실증이 아니다.

```powershell
uv run python tools/prepare_aws_smoke.py --source "검토한 인계 원본 폴더" --output "저장소 밖의 새 작업 사본 폴더"
```

`SHA256SUMS`는 원본 출처 확인용이다. 수정 사본은 `REVIEWED-SHA256SUMS`로 검증하고, 원본/사본 파일 bytes SHA 대응은 `REVIEWED-CHANGES.json`에서 확인한다. 기존 폴더·입력과 겹치는 출력·다른 인계 판본·수정된 원본은 거부한다.

03-engine-smoke의 실제 변경은 다음 범위다.

- curl의 원문 응답 stdout/파일 저장을 Python3.12 표준 라이브러리 클라이언트의 메모리 검증으로 바꾼다. health200, 정상 model·비어 있지 않은 text/finish·양수 completion usage, 모든 SSE 메시지의 model/error·내용/finish·마지막 DONE을 확인한 뒤 허용한 booleans와 frame 수만 출력한다. 기존 요청의 model·prompt·max_tokens8·일반/SSE 형식과 30/120/120초 상한을 유지한다. 본문은1MiB로 제한하고 원격 프로세스 자체 watchdog을 둔다.
- 고정 digest의 소유 Python 클라이언트 이미지 한 개가 필요하다. 기존3개 이미지 lock에 공식 Python3.12-slim을 추가해 최초 해석 후4개 모두 digest로 고정한다. 제품 의존성·기존 제품 스트림 수집/시간 측정 코드는 바꾸지 않는다.
- Pod/배포/Job/Node/Event의 원문 대신 신원·고정 image·자원 설정·준비 상태·허용된 실패 reason·정규 원문 SHA만 기록한다. env/args/command/annotations/event message·원문 요청/응답·토큰·Secret·예외를 복사하지 않는다. native log는 제한된 수집 bytes SHA/수집 상태만 남긴다. 실제 생성 manifest는 파일 bytes SHA만 기록한다. 정규 API 문서 SHA와 파일 bytes SHA는 구분한다.
- 기존 baseline 검증기는 SHA를 확인한 동일 코드로 메모리 입력에서 실행한다. 기존 sidecar/init/추가 volume/정책 검사를 완화하지 않는다. 첫 인수 전 serving-lab의 기존 배포·Service·Job·정책이 있거나 GPU를 점유한 Pod가 있으면 보류한다. 생성은 create로 처리해 사전 확인 뒤 생긴 다른 자원도 덮어쓰지 않는다. 보류 시에도 확보한 허용 메타데이터와 부분 수집 hash는 보존한다.
- 모든 kubectl 호출은 byul-aws-lab context를 명시한다. 이번 run label을 추가하며 Node label·접근 회수·완료 Job 정리는 UID/resourceVersion을 JSON Patch test로 확인한다. 이름만 지정한 삭제를 하지 않는다. 정상/거부 시험은 같은 명시 소유 role을 사용한다.
- 임시 NetworkPolicy는 UID를 확인해 ingress를 비워 예외를 회수한다. deny-only 객체는 남으며, 같은 클라이언트의 curl timeout28과 엔진 Ready를 다시 확인한다. 완료 Job은 UID를 확인해 TTL0을 요청하고 삭제 상태를 기다린다. [완료 Job TTL](https://kubernetes.io/docs/concepts/workloads/controllers/ttlafterfinished/)과 [kubectl JSON Patch](https://kubernetes.io/docs/tasks/manage-kubernetes-objects/update-api-object-kubectl-patch/)를 사용한다. 기존 사용자 자원의 회수 경로로 사용하지 않는다.
- 증거 폴더와 파일은 새로 만들며 덮어쓰지 않는다. 실패는 고정 진단·원본 스크립트 줄·종료 코드와 허용한 상태로 남긴다. 원문 stderr는 증거로 저장하지 않는다. 실패한 설치를 임의 최신 판본으로 대체하지 않는다.

03의 사본은 최초 구축에만 사용한다. 성공 뒤 엔진은 대표 제품 시험을 위해 남고, deny-only 정책과 모델/EBS도 남는다. 이는 전체 자원 정리 완료가 아니다. 이후 같은 이름의 작업을 재실행하려면 소유 신원·남은 자원을 먼저 확인한다. 기존 클러스터의 기준을 이 스크립트로 덮어쓰지 않는다.

실제 실행 전에는 적용된 GPU quota·us-east-1 AMI/단가·고정 설치 판본의 가용성·총비용과 종료 수단·전용 새 호스트를 확인해야 한다. 정적 검사/EXAMPLE 회귀는 AWS·Kubernetes·GPU 실증으로 올리지 않는다. 정상 생성·SSE·GPU 계산·예외 회수의 실제 결과를 별도로 반출한 뒤 제품의 REQ-10 대표 경로를 확인한다. bootstrap 결과는 checklist 판정이나 runner 소비 증거가 아니다.
