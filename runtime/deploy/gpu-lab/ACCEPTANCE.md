# AWS GPU lab runtime 인수 절차

이 절차는 AWS 묶음 위에 runtime 배포를 올린 뒤 “첫 완료 기준” 8가지를 한 번의 GPU 가동 구간에서 확인한다. `tools/k8s_lab_check.py`는 fake engine과 시험 정책을 생성·삭제하는 kind 전용 도구이므로 이 환경에서 사용하지 않는다. 아래 도구는 정책을 적용·변경·삭제하지 않으며, 두 opt-in 사례 외에는 배포 상태를 바꾸지 않는다.

## 준비

AWS 호스트의 `ubuntu` 사용자로 다음을 확인한다.

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

## 실행

AWS 묶음 `COST-PLAN.md`에 따라 먼저 dev(g6.2xlarge)에서 기능 연결을 확인하고, 정식 결과는 reference(g6.4xlarge)로 다시 실행한다. 로컬 단위시험과 문서 검토를 끝낸 뒤 GPU 서버에서는 사례를 묶어서 실행한다.

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

결과를 확인한 뒤 수집 중단과 엔진 교체까지 한 번에 실행한다. AWS-08은 GPU를 반납하고 엔진을 cold-start하므로 항상 마지막이다.

```bash
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env \
  --cases AWS-07,AWS-08 \
  --allow-collector-stop \
  --allow-engine-restart
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
| AWS-01 정상 | 비파괴 | Gateway chat 200, 유효 completion, final `ALLOWED`, 전달 시도 | 요청 상세와 `Received request chatcmpl-<id>` 엔진 로그. vLLM 0.31.0 형식이 맞지 않으면 `unknown` | RT-API-01, RT-ENV-02 |
| AWS-02 인증 거부 | 비파괴 | token 없음/오류가 각각 401 `AUTH_REQUIRED`/`AUTH_INVALID`, final `DENIED`, 미전달 | 두 요청 record와 같은 시간창 엔진 로그 부재. AWS-01에서 양성 로그 형식을 먼저 확인해야 함 | RT-API-02, RT-OPT-04 |
| AWS-03 토큰 상한 | 비파괴 | `/api/policy`의 `output_max_tokens`보다 1 큰 요청이 403 `MAX_TOKENS_EXCEEDED`, 미전달 | 정책 조회값, 요청 상세 record | RT-OPT-01, RT-OPT-04 |
| AWS-04 직통 거부 | 비파괴 | `other-client`→engine:8000 TCP 실패, Gateway 대조 요청 200 | 실제 connect 실패와 Hubble 행 또는 `/api/observations?source=hubble` | RT-API-03, RT-NET-02, RT-TASKNET-02 |
| AWS-05 금지 송신 | 비파괴 | engine→`test-receiver` Pod IP:8080 실패, `other-client`→같은 IP:8080 연결 성공 | kubectl로 확인한 receiver Pod IP, 두 connect 결과, engine→receiver Hubble 관측 | RT-NET-02, RT-TASKNET-01 |
| AWS-06 RO 쓰기 거부 | 비파괴 | `/models` 쓰기 거부, `/cache` 임시 파일 쓰기·자체 삭제 성공 | exec 결과와 TG-ASSET-01/TG-CACHE-01 관측 | RT-SYS-03, RT-ASSET-02, RT-ASSET-05 |
| AWS-07 수집 중단 | 변경(opt-in) | collector stop 뒤 두 heartbeat `stopped`와 stale window 안의 Gateway non-ok, restart 뒤 ok | heartbeat 파일, `/api/collectors`, 실제 관측 timing; gap 행은 보조 증거 | RT-OBS-01, RT-SYS-06 |
| AWS-08 Pod 교체 | 변경(opt-in) | rollout 뒤 Pod UID 변경, Gateway `/api/target`의 새 instance_id와 `instance_changed`(`pod_replaced`) 이력, Gateway와 `engine_info` 회복 | host kubectl 전후 신원, target 이력, 요청 record, collector 상태 | RT-NET-04, RT-PHASE-01 |

AWS-04의 TCP 실패와 AWS-05의 engine→receiver TCP 실패가 각 차단 결과다. AWS-05는 부록 I.4의 팀 소유 시험 Pod `test-receiver`만 대상으로 하며, DNS 영향을 배제하려고 kubectl로 읽은 Pod IP에 직접 연결한다. egress 정책이 없는 `other-client`가 같은 receiver에 연결하지 못하면 `대조 실패: receiver에 닿지 않음`으로 `unknown`이며 절대 통과시키지 않는다. engine→receiver Hubble 행은 필수 연결 증거지만 차단 여부를 결정하지 않는다.

`192.0.2.1`은 비라우팅 TEST-NET-1이고 이 AWS 묶음은 IMDSv2 hop limit 1이므로 external·IMDS connect는 egress 정책이 없어도 실패한다. 두 probe와 해당 Hubble 행은 보고서에 보조 증거로만 남으며, 그 실패만으로는 송신 통제를 아무것도 입증하지 않는다. 운영 대상이나 제3자 대상으로 시험을 확대하지 않는다. Hubble의 `DROPPED`도 관측 verdict일 뿐 독립적인 차단 증명으로 승격하지 않는다. AWS-06에서 read-only mount가 Tetragon hook보다 먼저 거부하면 TG-ASSET-01이 `not_observed`여도 쓰기 거부 자체의 실패는 아니다. 반면 cache 쓰기 관측이 없으면 필요한 연결 근거가 부족하므로 `unknown`이다.

AWS-07은 `systemctl --user stop/start byul-observation-feed`만 수행한다. stop 뒤 `--observation-dir`의 `hubble.heartbeat.json`과 `tetragon.heartbeat.json`이 모두 stop 요청 뒤 갱신된 `stopped`인지 확인하고, 최초 `/api/collectors`에서 읽은 `stale_after_s` 안에 두 `observation_feed_*`가 non-ok가 되는지 기다린다. 보고서에는 stale window와 stop·heartbeat·non-ok·start·recovery의 실제 관측 timing을 남긴다. 정상 SIGTERM 종료는 gap 행을 만들지 않으므로 `*.gaps.jsonl` 신규 행은 보조 증거일 뿐 판정 필수가 아니다. AWS-08은 `kubectl rollout restart deployment/serving-engine`과 ready 대기만 수행한 뒤 `/api/target`에서 교체 전과 다른 instance_id 및 이전 instance_id에 연결된 `instance_changed`·`pod_replaced` 이력을 기다린다. 이때 host snapshot이 stale였으면 보고서의 `snapshot_note`에 남고 통과로 채우지 않는다. 도구는 NetworkPolicy, Cilium/Tetragon 정책, 그 밖의 Deployment spec을 바꾸지 않으며 AWS-06에서 자신이 만든 임시 파일 외에는 삭제하지 않는다.

## 결과 반출과 비용 절약

각 실행은 현재 폴더의 `evidence/aws-acceptance-<UTC>-<pid>/`를 0700으로 만들고 0600의 `report.json`·`report.md`를 쓴다. 명령 argv, HTTP code, request ID, 판정에 사용한 record 필드, 관측 파일·행 또는 API query가 들어가며 token·token hash·prompt·응답 본문은 넣지 않는다.

로컬 준비 → dev 비파괴 6건 → dev 변경 2건 → 필요한 수정과 재시험 → reference 8건 → 증거 반출 → EC2 Stop 순서로 묶는다. 사례 사이에 엔진을 내리지 않고, AWS-08만 마지막에 한 번 cold-start한다. 종료 전에 실행 폴더를 관리자 보관소나 개발 노트북으로 반출하고 파일 권한을 유지한다.

## Known gaps

- Hubble `DROPPED`는 차단 확인 그 자체가 아니다. connect 결과와 정상 Gateway 대조를 함께 읽는다.
- read-only mount 거부가 Tetragon file hook보다 먼저 발생할 수 있어 TG-ASSET-01 미관측과 쓰기 성공을 같은 뜻으로 보지 않는다.
- vLLM v0.31.0 이미지의 실제 요청 ID 로그가 `Received request chatcmpl-<Gateway id>` 형식인지 첫 AWS-01에서 확인해야 한다. 다르면 엔진 수신을 추정하지 않고 `unknown`으로 남긴다.
