# AWS GPU lab runtime 인수 절차

이 절차는 AWS 묶음 위에 runtime 배포를 올린 뒤 “첫 완료 기준” 8가지를 한 번의 GPU 가동 구간에서 확인한다. `tools/k8s_lab_check.py`는 fake engine과 시험 정책을 생성·삭제하는 kind 전용 도구이므로 이 환경에서 사용하지 않는다. 아래 도구는 정책을 적용·변경·삭제하지 않으며, 두 opt-in 사례 외에는 배포 상태를 바꾸지 않는다.

## 준비

AWS 호스트의 `ubuntu` 사용자로 다음을 확인한다.

EC2 Stop/Start 직후에는 GPU 플러그인 복구 전 생성된 엔진 Pod가 `UnexpectedAdmissionError`와 `Failed` 상태로 남을 수 있다. `kubectl --context byul-aws-lab get node -o json`에서 `nvidia.com/gpu` allocatable이 1인지, 현재 엔진 Pod가 Ready인지 확인한 뒤 시험한다. 종료된 Pod는 현재 대상 후보에서 제외되지만 실행 또는 대기 중인 후보가 둘 이상이면 보류한다.

```bash
kubectl --context byul-aws-lab -n serving-lab get deploy,pod
systemctl --user status byul-observation-feed
kubectl --context byul-aws-lab -n serving-lab port-forward deploy/serving-gateway 18080:8080
```

포트포워드는 별도 터미널에서 계속 실행한다. 시험 도구가 포트포워드를 만들거나 종료하지 않는다. 토큰 파일은 Gateway `auth.json`의 원문이 아니라 실제 시험용 token 두 개를 아래처럼 넣고 소유자만 읽게 한다. 값은 명령행에 쓰지 않는다.

```text
RUNTIME_INFERENCE_TOKEN=<inference token>
RUNTIME_VIEWER_TOKEN=<viewer token>
```

```bash
chmod 600 ~/runtime-acceptance.env
test -d /srv/serving/observation
```

엔진 `serving-engine`, Gateway `serving-gateway`, 시험 Pod `other-client`·`test-receiver`, host collector가 준비돼야 한다. Gateway 정책에는 `test-model`과 인증 역할이 반영돼 있어야 한다. 정책 묶음의 `target` 역할에는 context `byul-aws-lab`, namespace `serving-lab`, selector `app=serving-engine`, container `engine`인 대상 등록이 들어 있어야 하며 host collector의 `/srv/serving/observation/target-pods.json`이 Gateway에 읽기 전용으로 보여야 한다.

AWS-01의 엔진 로그 매칭에는 runtime 통합 patch의 `--enable-log-requests`가 필요하고, 함께 추가한 `--enable-request-id-headers`는 응답의 `X-Request-Id` 헤더를 켠다. vLLM 0.31.0 `entrypoints/serve/utils/request_logger.py`의 `log_inputs`는 INFO에 요청 ID·sampling params를, DEBUG에 prompt·토큰 ID를 기록하고 `log_request_body`는 DEBUG에 JSON 본문을 기록한다. 시험 요청은 무해한 고정 문장만 사용한다. 원시 엔진 로그를 증거로 반출한다면 요청 내용이 담길 수 있는 기록으로 취급한다. 인수 도구의 `report.json`·`report.md`에는 엔진 로그 본문 대신 매칭 결과·조회 명령 참조만 넣는다.

## 실행

AWS 묶음 `COST-PLAN.md`에 따라 먼저 dev(g6.2xlarge)에서 기능 연결을 확인하고, 정식 결과는 reference(g6.4xlarge)로 다시 실행한다. 로컬 단위시험과 문서 검토를 끝낸 뒤 GPU 서버에서는 사례를 묶어서 실행한다.

서버에서는 CLI의 패키지 메타데이터가 필요하므로 저장소 `runtime/`에서 `python3 -m venv .venv && .venv/bin/pip install --no-deps -e .`로 준비한다. 정책 묶음은 AWS 묶음 03 이후 `images.lock`의 엔진 digest를 확인해 서버 사본에서 만들고, 값이 채워진 intent를 `enforcement_intent`로 포함한다.

`enforce render`의 Cilium 후보 중 `runtime-receiver-fixture-ingress`는 `test-receiver`를 `artifact-acquire` 송신만 허용한다. AWS-05의 `other-client`→receiver 양성 대조 연결을 막아 결과가 `unknown`이 되므로 **AWS-05 전에는 적용하지 않는다.** `runtime-acquire-egress`는 이번 시험에 `artifact-acquire` Pod가 없어 불필요하다. 이번 dev에는 Tetragon monitor 규칙 `runtime-tg-asset-01`·`runtime-tg-cache-01`·`runtime-tg-secret-01`과 Cilium `runtime-engine-egress`·`runtime-engine-ingress`·`runtime-gateway-egress`·`runtime-observer-0-egress`를 적용했다. AWS-06 전에 TG-CACHE-01을 감시하는 규칙을 적용해야 한다. 없으면 `/cache` 쓰기가 성공해도 `cache_tetragon` 증거가 없어 `unknown`이다.

비파괴 사례부터 확인:

```bash
cd ~/byul/runtime
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab \
  --namespace serving-lab \
  --gateway-url http://127.0.0.1:18080 \
  --token-env-file ~/runtime-acceptance.env \
  --observation-dir /srv/serving/observation \
  --cases AWS-01,AWS-02,AWS-03,AWS-04,AWS-05,AWS-06
```

결과를 확인한 뒤 수집 중단과 엔진 교체까지 한 번에 실행한다. AWS-08은 GPU를 반납하고 엔진을 cold-start하므로 변경 사례 중 마지막이다.

```bash
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env \
  --cases AWS-07,AWS-08 \
  --allow-collector-stop \
  --allow-engine-restart
```

AWS-08의 Pod 교체 뒤에도 네트워크 통제가 유지되는지 AWS-04·05를 다시 실행한다. 이 재실행은 AWS-01의 엔진 로그 형식 확인값이나 `engine_log`에 의존하지 않는다.

```bash
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env \
  --cases AWS-04,AWS-05
```

dev 결과와 수정 사항을 확인한 뒤 AWS 묶음 `COST-PLAN.md` 절차로 reference 환경을 준비하고 정식 실행은 8건을 한 프로세스에 묶는다. 이 도구는 dev/reference를 추정하지 않으므로 실제 host profile은 묶음 증거와 함께 확인한다.

```bash
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env \
  --evidence-dir evidence/reference \
  --allow-collector-stop \
  --allow-engine-restart
```

`--cases`를 생략하면 AWS-01~08을 순서대로 실행하지만 opt-in flag가 없는 AWS-07·08은 `not_run`이다. `not_run`과 `unknown`은 통과로 세지 않으며, 요청한 모든 사례가 `passed`일 때만 overall이 `passed`다.

## 사례와 증거

| 사례 | 구분 | 목적·기대 결과 | 연결 증거 | RT |
| --- | --- | --- | --- | --- |
| AWS-01 정상 | 비파괴 | 일반·stream Gateway chat이 200, 일반 응답 유효, stream SSE data와 `[DONE]`, 두 record 모두 final `ALLOWED`·전달 시도 | 요청 상세와 일반 요청의 `Received request chatcmpl-<id>` 엔진 로그. 본문·토큰은 보고서에 넣지 않음. vLLM 0.31.0 형식이 맞지 않으면 `unknown` | RT-API-01, RT-ENV-02 |
| AWS-02 인증 거부 | 비파괴 | token 없음/오류가 각각 401 `AUTH_REQUIRED`/`AUTH_INVALID`, final `DENIED`, 미전달 | 두 요청 record와 같은 시간창 엔진 로그 부재. AWS-01에서 양성 로그 형식을 먼저 확인해야 함 | RT-API-02, RT-OPT-04 |
| AWS-03 토큰 상한 | 비파괴 | 상한값 요청은 200·유효 응답·`ALLOWED`·전달 시도, 상한+1은 403 `MAX_TOKENS_EXCEEDED`·미전달 | 두 record와 상한+1 요청 ID의 같은 시간창 엔진 로그 부재. AWS-01 양성 로그 형식이 미확인이면 `unknown` | RT-OPT-01, RT-OPT-04 |
| AWS-04 직통 거부 | 비파괴 | `other-client`→engine:8000은 timeout errno, Gateway Pod→engine:8000은 직전·직후 연결 성공, Gateway 요청 200 | errno·이름과 해당 흐름의 Hubble `dropped` 행 또는 API 관측 | RT-API-03, RT-NET-02, RT-TASKNET-02 |
| AWS-05 금지 송신 | 비파괴 | engine→`test-receiver` Pod IP:8080은 timeout errno, `other-client`→같은 IP:8080 연결 성공 | kubectl receiver Pod IP, errno·이름, engine→receiver 해당 흐름의 Hubble `dropped` | RT-NET-02, RT-TASKNET-01 |
| AWS-06 RO 쓰기 거부 | 비파괴 | `/models` 쓰기가 `EROFS`, `/cache` 임시 파일 쓰기·자체 삭제 성공 | exec errno·이름과 TG-ASSET-01/TG-CACHE-01 관측. `EPERM`·`EACCES`는 `unknown` | RT-SYS-03, RT-ASSET-02, RT-ASSET-05 |
| AWS-07 수집 중단 | 변경(opt-in) | collector stop 뒤 두 heartbeat `stopped`와 stale window 안 Gateway non-ok, restart 뒤 collector ok 및 유한 창 안 정상 chat 회복 | heartbeat, `/api/collectors`, recovery 요청 record·재시도 timing. 중지 구간 요청과 gap은 보고만 함 | RT-OBS-01, RT-SYS-06 |
| AWS-08 Pod 교체 | 변경(opt-in) | rollout 뒤 Pod UID·Gateway instance 변경, restart 이후 `engine_info` ok 뒤 유한 창 안 정상 chat 회복 | host 신원, target 이력, engine_info, 재시도 횟수·경과·마지막 status/reason | RT-NET-04, RT-PHASE-01 |

AWS-04·05는 `connect_ex`가 반환한 errno와 이름을 기록한다. `EAGAIN`/`EWOULDBLOCK`/`ETIMEDOUT`만 차단 후보로 보고, `ECONNREFUSED` 등 다른 실패나 DNS 예외는 정책 차단 근거가 아니므로 `unknown`이다. AWS-04는 Gateway 이미지가 Python 기반이고 컨테이너 이름이 `gateway`임을 전제로 직통 probe 직전·직후 Gateway Pod 안에서 engine:8000 연결 성공을 확인한다. exec가 불가능하면 `unknown`이다. AWS-05는 팀 소유 `test-receiver` Pod IP만 대상으로 하며, `other-client`가 같은 receiver에 연결하지 못하면 `대조 실패: receiver에 닿지 않음`으로 `unknown`이다. 두 사례 모두 정확한 대상 흐름의 Hubble verdict가 `dropped`여야 하며 `DROPPED` 단독으로는 통과하지 않는다.

AWS-04·05 통과만으로는 기본 NetworkPolicy와 runtime Cilium 규칙 중 어느 쪽이 패킷을 막았는지 구분할 수 없다. runtime 규칙만의 효과를 확인할 때는 기본 정책 `engine-runtime`·`engine-management-observer`의 원본을 절대 경로에 저장하고 spec·labels를 기록한 뒤, 짧은 통제 구간에 두 정책만 제거해 AWS-01·04·05를 재실행한다. 즉시 **절대 경로의 복원용 파일**로 복원하고 현재 객체의 spec·labels를 원본과 대조한다. 복원 스크립트에만 의존하지 말고 실제 적용·대조를 확인하며, 복원 후 AWS-01·04·05·06을 다시 실행한다. dev에서는 약 1분 제거 후 EAGAIN·Hubble dropped로 AWS-01·04·05가 통과했다. 자동 복원은 스크립트 경로 실수로 실패해 다음 명령에서 수동 복원했고 원본 spec·labels 일치를 확인했다. 모든 정책을 제거해 연결이 열리는지 보는 대조 시험은 아직 하지 않았다. 이 runtime 규칙만의 확인은 Pod 교체 전 결과다.

복원용 파일 생성·검사가 실패하면 정책을 제거하지 않는다. 아래 복원 명령은 사례 실행 여부와 무관하게 수행하고, 적용 결과를 확인한다.

```bash
kubectl --context byul-aws-lab -n serving-lab get networkpolicy engine-runtime engine-management-observer -o json > /tmp/byul-aws-base-networkpolicies-original.json
# 원본의 spec·metadata.labels를 대조용으로 보관하고, 복원용 파일에서는 서버 관리 metadata만 제외
python3 -c 'import json,sys; d=json.load(sys.stdin); json.dump({"apiVersion":"v1","kind":"List","items":[{"apiVersion":i["apiVersion"],"kind":i["kind"],"metadata":{k:v for k,v in i["metadata"].items() if k in ("name","namespace","labels","annotations")},"spec":i["spec"]} for i in d["items"]]},sys.stdout)' < /tmp/byul-aws-base-networkpolicies-original.json > /tmp/byul-aws-base-networkpolicies-restore.json
test -s /tmp/byul-aws-base-networkpolicies-restore.json
# runtime Cilium 규칙이 적용됐는지 확인
kubectl --context byul-aws-lab -n serving-lab delete networkpolicy engine-runtime engine-management-observer
# 위 AWS-01·04·05 명령을 --cases AWS-01,AWS-04,AWS-05로 실행
kubectl --context byul-aws-lab -n serving-lab apply -f /tmp/byul-aws-base-networkpolicies-restore.json
kubectl --context byul-aws-lab -n serving-lab get networkpolicy engine-runtime engine-management-observer -o json
# 두 객체의 spec·metadata.labels가 저장한 원본과 같은지 대조하고 AWS-01·04·05·06 재실행
```

`192.0.2.1`은 비라우팅 TEST-NET-1이고 이 AWS 묶음은 IMDSv2 hop limit 1이므로 external·IMDS connect는 egress 정책이 없어도 실패한다. 두 probe와 Hubble 행은 보고서의 보조 증거일 뿐 판정에서 제외한다. AWS-06의 모델 probe는 `EPERM`·`EACCES`·`EROFS`를 모두 `WRITE_DENIED`로 출력하지만 `EROFS`만 read-only mount 증거로 인정한다. 모델 폴더 소유자 10002와 엔진 UID 2000 때문에 mount가 read-only가 아니어도 `EACCES`가 날 수 있고, `EPERM`은 Tetragon 등 다른 집행일 수 있다. TG-ASSET-01은 RO mount가 hook보다 먼저 거부하면 미관측일 수 있지만, cache 쓰기 관측이 없으면 연결 근거 부족으로 `unknown`이다.

AWS-07은 `systemctl --user stop/start byul-observation-feed`만 수행한다. stop 뒤 heartbeat `stopped`와 `stale_after_s` 안 collector non-ok를 기다리고 중지 구간 정상 요청 1건의 HTTP status·reason_code는 보고서에만 남긴다. start 뒤 collector가 ok가 되면 `min(stale_after_s + 30초, 90초)` 재시도 창 안에 정상 chat 200·`ALLOWED`·전달 시도가 회복돼야 한다. 정상 SIGTERM의 gap 행은 보조다. AWS-08은 rollout과 target instance/history 확인 뒤, restart 이후 `engine_info`가 ok가 될 때까지 요청을 보내지 않는다. 그 뒤 90초 재시도 창에서 정상 chat 회복을 확인하고 끝까지 실패하면 마지막 status·reason_code를 남긴다. host snapshot stale은 `snapshot_note`에만 남으며 통과로 채우지 않는다. 도구는 정책이나 Deployment spec을 바꾸지 않고 AWS-06 임시 파일 외에는 삭제하지 않는다.

## 2026-11-01 dev 실행 기록 (Claude 실행, Codex 교차 검수 전)

g6.2xlarge·vLLM 0.31.0에서의 기능 연결 확인 1회이며 정식 평가가 아니다. Gateway `serving-gateway:rt-894dfe7`(코드 `894dfe7`), 묶음 `bundle-aws-lab-dev-894dfe7`의 네 역할(`gateway_policy`·`policy_approvals`·`target`·`enforcement_intent`)을 적용했다. 패킷 차단 주체는 Cilium이고 `/models`의 EROFS는 Kubernetes 읽기 전용 mount의 결과다. runtime은 규칙을 생성·확인·기록한다.

| 단계 | 결과 | 보고서 |
| --- | --- | --- |
| 초기 | AWS-01~03 통과 | `aws-acceptance-20261101T073229Z-9585` |
| 기본 NetworkPolicy | AWS-04·05 통과, AWS-06 `unknown`(TG-CACHE-01 없음) | `aws-acceptance-20261101T074054Z-11030` |
| Tetragon monitor 3개 적용 | AWS-06 통과(`/models` EROFS, `/cache` TG-CACHE-01 기록) | `aws-acceptance-20261101T075604Z-13453` |
| Cilium 4개 적용·기본 정책 잠시 제거·복원 | AWS-01·04·05 각각 통과; 복원 뒤 AWS-01·04·05·06 통과 | `aws-acceptance-20261101T075648Z-13909`·`aws-acceptance-20261101T075733Z-14397`·`aws-acceptance-20261101T075802Z-14966` |
| 수집 중단·Pod 교체·교체 뒤 재검사 | AWS-07·08 통과, 교체 뒤 기본 정책+runtime 규칙에서 AWS-04·05 통과 | `aws-acceptance-20261101T113730Z-6730`·`aws-acceptance-20261101T114125Z-8766` |

AWS-07은 stop/start 뒤 각각 약 4초에 feed error/ok, 복구 요청 200이었다. 중지 중 약 4초의 요청 1건은 200이었으나 판정 대상이 아니었다. stale 15초 뒤 503 hold는 이번에 관측하지 않았고 정상 종료라 gap 행도 없다. AWS-08은 Pod UID·instance 변경과 `instance_changed`/`pod_replaced` 이력, 복구 요청 200을 확인했다. 최종 증거는 `~/.local/state/serving-runtime/aws-lab/evidence/dev-20261101-stage6`, 요청·관측 화면 복사본은 `dev-20261101-dashboard-copy-final`이다. EC2 stopped를 확인했으며 컴퓨트는 약 1.5시간(추정)이다.

## 결과 반출과 비용 절약

각 실행은 현재 폴더의 `evidence/aws-acceptance-<UTC>-<pid>/`를 0700으로 만들고 0600의 `report.json`·`report.md`를 쓴다. 명령 argv, HTTP code, request ID, 판정에 사용한 record 필드, 관측 파일·행 또는 API query가 들어가며 token·token hash·prompt·응답 본문은 넣지 않는다.

로컬 준비 → dev 비파괴 6건 → dev 변경 2건 → Pod 교체 뒤 AWS-04·05 재실행 → 필요한 수정과 재시험 → reference 8건 및 같은 재실행 → 증거 반출 → EC2 Stop 순서로 묶는다. 사례 사이에 엔진을 내리지 않고, AWS-08에서만 한 번 cold-start한다. 종료 전에 `report.json`·`report.md` 등 증거 파일의 존재를 확인하고, 실행 폴더를 관리자 보관소나 개발 노트북으로 반출한 뒤 파일 권한을 유지하며 Stop한다.

## Known gaps

- dev에서 `connect_ex`의 `EAGAIN`과 Hubble `dropped`를 확인했다. reference에서는 다시 확인하며 다른 errno이면 도구는 보수적으로 `unknown`으로 둔다.
- Hubble `DROPPED`는 단독 차단 증명이 아니며, 시간 초과와 허용 경로 대조를 함께 확인한다.
- AWS-06은 `EROFS`만 RO mount 증거로 쓴다. Tetragon 등이 `EPERM`을 내거나 UID/소유권 때문에 `EACCES`가 나면 RO 여부는 `unknown`이다.
- dev AWS-01에서는 vLLM 0.31.0의 `Received request chatcmpl-<Gateway id>` 로그를 확인했다. reference에서도 형식을 다시 대조하며 다르면 엔진 수신을 추정하지 않고 `unknown`으로 남긴다.
