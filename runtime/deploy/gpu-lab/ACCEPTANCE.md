# AWS GPU lab runtime 인수 절차

## 실행 부모 조건의 의미

두 AWS intent의 선택적 `parent_constraints`는 Tetragon `process_exec.parent.binary`의 **관측 문자열**이 지정된 부모 중 하나일 때만 해당 실행 파일을 기존 상시·단계 허용 목록과 대조한다. 부모가 다르거나 없으면 `outside_allowlist`이며 `parent_check`에 `mismatch` 또는 `unavailable`을 남긴다. 지정되지 않은 실행 파일은 기존 판정을 유지한다. 이 두 필드는 runtime 소유 1.1 intent의 선택적 확장으로, 기존 1.1 문서를 읽는 방식은 유지하고 버전은 올리지 않았다. SGLang의 `suspect_parent_contexts`는 관측된 `nvcc`·`ninja` → `/bin/sh`를 loading/compiling 단계에서만 `suspect_context=compile_toolchain_parent`로 설명한다. 신뢰 단계를 포함해 셸의 `exec_verdict=suspect_binary` 의심 경고는 유지하며, 설명이 없는 경우 `suspect_context=null`이다. `containerd-shim` → 셸(`kubectl exec` 포함)도 계속 `suspect_binary`다. `ExecException`은 해당 실행 파일의 부모 제약을 먼저 만족한 뒤 기존의 신뢰 단계·기간·image·부모 조건을 적용하며, 이 셸 설명 설정은 예외보다 의심 판정이 우선한다.

이 조건은 탐지 판정을 좁히는 관측 규칙이며 현재 Tetragon 커널 차단 규칙은 아니다. 엔진 Python이 장악되면 `ninja` 등을 통해 셸을 띄울 수 있으므로 완전 차단으로 해석하지 않는다. 시작 Python의 `containerd-shim` 부모는 정상 기동과 `kubectl exec`을 구별할 수 없어 제약하지 않았다. vLLM의 미관측 `/usr/bin/python3.12`도 부모 근거가 없어 기존 판정을 유지한다. SGLang에서 관측된 `id`·`awk`·`cat`은 기존 허용 목록에 없으므로 관측만으로 추가하지 않았고, 미관측 정규 표기 `cicc`는 허용 목록에서 제외했다. 추후 차단 단계에서는 별도 검증 후 Tetragon `matchParentBinaries`로 옮기고 parents map 비용을 확인해야 한다. 관측 쌍은 저장소 밖 2026-11-03 두 JSONL의 `engine` 컨테이너에서 추렸으며 SGLang 파일에 섞인 vLLM Pod를 분리했다. 시험 도구의 `containerd-shim` → 셸·Python은 정상 쌍에서 제외했다. 관측은 승인 자체를 뜻하지 않으며, 새 image·단계·부모 관계는 다시 확인해야 한다. 부모 정보가 빠진 정상 실행도 의도대로 `outside_allowlist`가 되므로 AWS-11의 잘못된 경고로 셀 수 있다. 11/3 증거 867건에서는 부모 누락이 0건이었다.

11/4 빈 캐시 첫 기동 뒤 첫 요청에서 나온 Triton `ptxas`는 개발계획서의 추론 중 지연 JIT(1248줄)와 부합한다. SGLang 배포의 `/models`는 읽기 전용이지만 엔진 컨테이너의 `readOnlyRootFilesystem`은 설정되지 않아 루트 파일 시스템 보호 근거로 삼지 않는다. 추론 허용은 관측된 정확한 경로와 `/opt/sglang/bin/python3` 부모에만 한정하며 셸은 계속 의심 경고로 남긴다. 이 좁은 조건이 `id`·`awk`·`cat` 관측만으로 추가하지 않는 원칙과 다른 근거다.

11/4 서버 1회차는 [서버 실행순서표](서버_실행순서표.md)와 읽기 전용 [preflight 도구](../../tools/aws_lab_preflight.py)를 따른다.

이 절차는 AWS 묶음 위에 runtime 배포를 올린 뒤 기존 “첫 완료 기준” 8가지와 추가 준비 사례 AWS-09~11을 확인한다. 기존 AWS-01~08 결과는 다시 판정하지 않는다. `tools/k8s_lab_check.py`는 fake engine과 시험 정책을 생성·삭제하는 kind 전용 도구이므로 이 환경에서 사용하지 않는다. AWS-12~17은 `--cases`로 명시 선택해야 하며, 배포 상태를 바꾸는 사례에는 별도 허용 플래그가 필요하다.

## 준비

AWS 호스트의 `ubuntu` 사용자로 다음을 확인한다.

EC2 Stop/Start 직후에는 GPU 플러그인 복구 전 생성된 엔진 Pod가 `UnexpectedAdmissionError`와 `Failed` 상태로 남을 수 있다. `kubectl --context byul-aws-lab get node -o json`에서 `nvidia.com/gpu` allocatable이 1인지, 현재 엔진 Pod가 Ready인지 확인한 뒤 시험한다. 종료된 Pod는 현재 대상 후보에서 제외되지만 실행 또는 대기 중인 후보가 둘 이상이면 보류한다.

```bash
kubectl --context byul-aws-lab -n serving-lab get deploy,pod
systemctl --user status byul-observation-feed
kubectl --context byul-aws-lab -n serving-lab port-forward deploy/serving-gateway 18080:8080
```

포트포워드는 별도 터미널에서 계속 실행한다. AWS-13·14·15에서 Gateway를 재시작하면 이 사용자 포트포워드는 이전 Pod와 함께 끊긴다. loopback `--gateway-url` 사용 시 도구는 연결 오류를 Gateway 응답으로 세지 않고 같은 로컬 포트에 자체 `kubectl port-forward deploy/serving-gateway`를 다시 연다. 사용자 포트포워드가 아직 포트를 점유하면 도구는 연결 가능할 때까지 제한된 시험 구간에서 재시도한다. 도구 소유 포트포워드는 정상 종료·예외 모두에서 닫으며 사용자 프로세스는 종료하지 않는다. 비 loopback URL에는 포트포워드를 만들지 않는다. 보고서 `port_forward`와 각 사례의 `reconnect_at`은 재연결 시도 시각 및 사용 경로를 기록한다. **도구 종료 뒤 자체 포트포워드는 없고, 재시작으로 끊긴 사용자 포트포워드도 자동 복구되지 않는다.** 다음 사례를 별도 실행할 때는 사용자 터미널의 포트포워드를 종료하고 위 명령으로 새로 연다. 같은 도구 실행에서 이어지는 사례는 도구 소유 포트포워드를 사용한다. 토큰 파일은 Gateway `auth.json`의 원문이 아니라 실제 시험용 token 두 개를 아래처럼 넣고 소유자만 읽게 한다. 값은 명령행에 쓰지 않는다.

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

`--cases`를 생략하면 AWS-01~11을 순서대로 실행하지만 opt-in flag가 없는 AWS-07·08은 `not_run`이다. `not_run`과 `unknown`은 통과로 세지 않으며, 요청한 모든 사례가 `passed`일 때만 overall이 `passed`다.

### 추가 확인 AWS-12~17

다음 명령은 서버의 `runtime/`에서 실행한다. 각 명령은 독립 실행이며 `--cases`로 선택하지 않은 추가 사례는 실행하지 않는다. 변경 사례는 시험 전후의 객체·링크·수집기 상태를 보고서에서 대조한다. 복구·정리 실패는 `failed`이고 즉시 운영자가 상태를 확인해야 한다.

```bash
# AWS-12: 비교 Pod 두 개와 비교 CiliumNetworkPolicy만 임시 적용·제거
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env --cases AWS-12 --allow-compare-policy

# AWS-13: Gateway가 읽는 bundle root에 쓰기 권한이 있는 실행 주체에서만 실행
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env --cases AWS-13 --allow-bundle-switch \
  --bundle-root /srv/serving/bundles

# AWS-14: Gateway 재시작 뒤 503과 10초 안의 정상 200
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env --cases AWS-14 --allow-gateway-restart

# AWS-15: 수집기 정지 중 Gateway 재시작, 끝에 수집기 복구
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env --cases AWS-15 \
  --allow-collector-stop --allow-gateway-restart

# AWS-16: 수집기 정지 15초 초과 뒤 단계 unknown, 일반 요청 200
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env --cases AWS-16 --allow-collector-stop

# AWS-17: 엔진 Pod 교체 뒤 단계 시간표 UID와 새 실행 대조
.venv/bin/python -B tools/aws_lab_acceptance.py --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env --cases AWS-17 --allow-engine-restart
```

AWS-12의 `policy-compare-pods.yaml`·`policy-compare-cilium.yaml`은 기본 kustomization 밖에 있다. 도구가 실행 중인 `other-client` Pod의 실제 image와 `imagePullPolicy`를 읽어 비교 Pod 두 개에 치환한 내용을 stdin으로 적용한다. 읽지 못하면 `unknown`이며 비교 Pod를 만들지 않는다. 사용한 image와 pull 정책은 보고서에 기록한다. 두 Pod는 엔진·Gateway와 다른 라벨을 쓰고 비교 정책은 receiver만 선택한다. 규칙 전 연결 성공, 적용 중 timeout errno(`EAGAIN`/`EWOULDBLOCK`/`ETIMEDOUT`)와 정확한 흐름의 Hubble `dropped`, 제거 뒤 재연결 성공이 모두 있어야 통과한다. 규칙 전부터 막히면 비교 불가 `unknown`이며 `ECONNREFUSED`는 차단 증거가 아니다.

AWS-13은 `current`의 이전 상대 링크를 보존하고 복제한 묶음의 `gateway_policy` 파일에서 길이가 같은 1바이트만 바꾼다. Gateway 재시작에서 SHA-256 오류와 시작 거부를 확인한 뒤 반드시 이전 링크로 돌리고 다시 시작해 정상 200과 인증 거부 401 한 건을 확인한다. 이는 묶음 파일 바이트와 `bundle.json`의 SHA-256 대조이며 서명이나 승인 검증이 아니다. Gateway 호스트 마운트 경로에 쓰기 권한이 없으면 `unknown`이며 권한을 우회하지 않는다.

AWS-14의 `not_before`는 실제 코드에서 Gateway 시작 시각이다. 보고서는 Gateway Pod 시작 시각과 대상 스냅샷 `collected_at`, 응답의 `TARGET_UNAVAILABLE` 및 첫 503부터 200까지의 시간을 남긴다. 10초 초과는 `failed`다. 포트포워드 재연결 사이에 503 구간을 놓치면 503→200 시간을 판정할 수 없어 `unknown`으로 두고 `reconnect_at`을 기록한다. AWS-15·16은 수집기를 `finally`에서 재개하고 collector `ok`까지 확인한다. AWS-16·17은 SGLang에서도 신선한 단계 시간표로 판정하며 근거가 없으면 `unknown`이고 AWS-07·08의 기존 기준에는 영향을 주지 않는다. AWS-17은 원시 Tetragon `process_exec.process.pod.uid`와 정규화 실행 관측을 대조한다. `process_exit`는 지원되지 않는 이벤트로 건너뜀 수가 증가하고 손상 수는 늘지 않아야 하며, 도구가 이 수치를 보조 근거로 기록한다. 관측 화면 표시는 사람이 확인한다.

AWS-09~11은 새 intent를 포함한 정책 묶음을 다시 작성하고 TG-SECRET-01 후보를 재적용한 뒤 엔진을 재시작한 환경에서만 실행한다. 파일 ConfigMap과 마운트 patch도 먼저 적용해야 한다. AWS-11을 선택할 때 AWS-09·10도 함께 선택한다. 도구는 AWS-09·10 직전에 일반 요청·stream·`/cache` 쓰기 대조를 수행하고 두 탐지 시험 뒤 같은 대조를 반복한다. 응답 시간은 참고값이며 판정에 쓰지 않는다. 탐지 시간도 기준 없이 판정하지 않는다.

Gateway 관측 조회는 최근 50행만 반환한다. AWS-11 구간이 이 범위를 넘으면 잘못된 경고 0건을 입증할 수 없어 `unknown`으로 둔다. 단계가 유효한 실행이 구간에 하나도 없거나 모두 `phase_unknown`이면 경고 0건이어도 `unknown`이다. 보고서에는 시간표 신선도·쓰기 시각·단계 출처와 유효/미확인 실행 수를 남긴다. 의도한 shell 시험은 잘못된 경고에서 제외하며 AWS-09 근거는 엔진 Pod 기록으로 한정한다.

### 단계 연결 후 서버 시험 순서

**1차 관찰 → 허용 목록 작성 → 2차 판정** 순서를 지킨다. 1차 관찰에서는 현재 엔진 Pod UID와 로그 시간표 상태·표지 일치 건수, 단계 경계, Tetragon 실행 경로를 수집한다. `tools/aws_lab_acceptance.py inventory --tetragon <tetragon.jsonl> --timeline <phase-timeline.json>`는 읽기 전용으로 단계·Pod·경로별 건수만 출력하고 판정에 쓰지 않는다. 시간표와 Tetragon의 Pod UID 또는 정확한 Pod 이름이 다르면 단계는 `unknown`이다. 단계 전용 실행과 반복 정상 경로의 출처를 검토한 뒤 `allowed_engine_binaries`·`phase_binaries` 변경안을 만든다. **11/3 관찰을 반영한 intent를 포함해 새 묶음을 작성한다.** 변경안을 서버에 적용하고 2차에서 AWS-09~11을 판정한다. 시간표만 먼저 활성화하면 기존 관측 재생 기준 정상 실행 다수가 `outside_allowlist`가 될 수 있다.

`phase_binaries`는 각 단계에서 예상되는 특징적 실행 경로를 구분하는 목록이다. 로그 근거 `engine_signal`은 `TRUSTED_BASES`에 없으므로 컴파일 단계의 `nvcc` 같은 단계 전용 경로도 `allowed_in_phase`가 아닌 `phase_signal_unverified` 경고로 남는다. 이 목록만으로 AWS-11 경고가 제거되지는 않는다.

현재 `phase_signals.py`는 vLLM CPU 로그 발췌의 표지를 사용한다. 저장된 GPU 관련 로그 파일 5개 중 2개에서 각각 5건이 일치했지만 GPU 전용 컴파일 경계와 전체 패턴은 서버에서 검증 전이다. 실제 단계가 잡히지 않으면 허용 목록을 추측하거나 AWS-11을 통과시키지 않는다. 시간표는 호스트 수집기의 별도 스레드가 현재 Pod 로그로 생성하고 Gateway는 읽기 전용 관측 마운트에서 읽는다. Pod가 없거나 여럿, 로그 실패·역순, 수집 중단 후 쓰기 시각 15초 만료는 단계 `unknown`이며 이것만으로 일반 요청을 막지 않는다. `engine_signal`은 신뢰 입력이 아니므로 단계 전용 예외·허용은 `phase_signal_unverified`가 될 수 있다.

```bash
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab \
  --token-env-file ~/runtime-acceptance.env \
  --cases AWS-09,AWS-10,AWS-11
```

## 사례와 증거

| 사례 | 구분 | 목적·기대 결과 | 연결 증거 | RT |
| --- | --- | --- | --- | --- |
| AWS-01 정상 | 비파괴 | 일반·stream Gateway chat이 200, 일반 응답 유효, stream SSE data와 `[DONE]`, 두 record 모두 final `ALLOWED`·전달 시도 | 요청 상세와 일반 요청의 `Received request chatcmpl-<id>` 엔진 로그. 본문·토큰은 보고서에 넣지 않음. vLLM 0.31.0 형식이 맞지 않으면 `unknown` | RT-API-01, RT-ENV-02 |
| AWS-02 인증 거부 | 비파괴 | token 없음/오류가 각각 401 `AUTH_REQUIRED`/`AUTH_INVALID`, final `DENIED`, 미전달 | 두 요청 record와 같은 시간창 엔진 로그 부재. AWS-01에서 양성 로그 형식을 먼저 확인해야 함 | RT-API-02, RT-OPT-04 |
| AWS-03 토큰 상한 | 비파괴 | 상한값 요청은 200·유효 응답·`ALLOWED`·전달 시도, 상한+1은 403 `MAX_TOKENS_EXCEEDED`·미전달 | 두 record와 상한+1 요청 ID의 같은 시간창 엔진 로그 부재. AWS-01 양성 로그 형식이 미확인이면 `unknown` | RT-OPT-01, RT-OPT-04 |
| AWS-04 직통 거부 | 비파괴 | `other-client`→engine:8000은 timeout errno, Gateway Pod→engine:8000은 직전·직후 연결 성공, Gateway 요청 200. 직통 POST /tokenize(합성 코드 선택 본문) 보조 시도 포함, 연결 단계 차단이면 본문 미전송 | errno·이름과 해당 흐름의 Hubble `dropped` 행 또는 API 관측 | RT-API-03, RT-NET-02, RT-TASKNET-02 |
| AWS-05 금지 송신 | 비파괴 | engine→`test-receiver` Pod IP:8080은 timeout errno, `other-client`→같은 IP:8080 연결 성공 | kubectl receiver Pod IP, errno·이름, engine→receiver 해당 흐름의 Hubble `dropped` | RT-NET-02, RT-TASKNET-01 |
| AWS-06 RO 쓰기 거부 | 비파괴 | `/models` 쓰기가 `EROFS`, `/cache` 임시 파일 쓰기·자체 삭제 성공 | exec errno·이름과 TG-ASSET-01/TG-CACHE-01 관측. `EPERM`·`EACCES`는 `unknown` | RT-SYS-03, RT-ASSET-02, RT-ASSET-05 |
| AWS-07 수집 중단 | 변경(opt-in) | collector stop 뒤 두 heartbeat `stopped`와 Gateway collector non-ok, stale 창 15초+여유 뒤 정상 chat 200·대상 미확인, restart 뒤 collector 및 대상 상태 ok와 정상 chat 회복 | heartbeat, `/api/collectors`, `/api/target`, 중지 구간·복구 요청 record·재시도 timing. gap은 보조 | RT-OBS-01, RT-SYS-06 |
| AWS-08 Pod 교체 | 변경(opt-in) | rollout 뒤 Pod UID·Gateway instance 변경, restart 이후 `engine_info` ok 뒤 유한 창 안 정상 chat 회복 | host 신원, target 이력, engine_info, 재시도 횟수·경과·마지막 status/reason | RT-NET-04, RT-PHASE-01 |
| AWS-09 실행 탐지 | 비파괴 | 엔진에서 `/bin/sh -c true`와 PATH 탐색 `sh -c true`를 실행. 절대 경로 실행이 엔진 Pod에 귀속되고 `suspect_binary` | 실행 시작 이후의 엔진 Pod·실행 경로·시각·판정. 다른 Pod의 같은 경로는 제외. 인자는 기록되지 않음 | RT-SYS-01, RT-SYS-02 |
| AWS-10 보호 파일 읽기 | 비파괴 | 엔진 Python이 가짜 파일을 읽고 TG-SECRET-01이 동일 경로·엔진 Pod·read·monitor를 기록 | 파일 내용은 보고하지 않음. 읽기 차단은 기준 아님 | RT-SYS-05 |
| AWS-11 정상 동작 대조 | 비파괴 | AWS-09·10 전후 일반·stream 요청과 `/cache` 쓰기가 정상이며 cache 세대 current·비정상 분류 없음, 잘못된 경고 0건 | 구간 내 `outside_allowlist`·`phase_signal_unverified`·`exec_from_cache` 목록과 건수, 응답·탐지 시간. 의도한 shell probe는 잘못된 경고에서 제외 | RT-API-01, RT-SYS-02, RT-ASSET-05 |
| AWS-12 규칙 전후 비교 | 변경(opt-in) | 규칙 전 연결, 적용 중 timeout+dropped, 제거 뒤 재연결 | 비교 Pod·정책만 적용·제거하고 잔존 객체 확인 | RT-NET-02 |
| AWS-13 잘못된 묶음 되돌리기 | 변경(opt-in) | 해시 불일치 Gateway 시작 거부, 이전 묶음 복구 뒤 200·401 거부 | SHA-256 오류 로그, 상대 링크 복구, 요청 기록 | RT-OPT-04 |
| AWS-14 재시작 직후 503 | 변경(opt-in) | `TARGET_UNAVAILABLE` 503 관측, 첫 503 뒤 10초 안에 200 | Pod 시작 시각·스냅샷 수집 시각·응답 열 | RT-OBS-01 |
| AWS-15 수집 중단 중 재시작 | 변경(opt-in) | 정지 중 503, 재개 뒤 200 | 수집기 재개·collector ok 필수 | RT-OBS-01 |
| AWS-16 단계 만료 | 변경(opt-in) | 15초 초과 뒤 런타임 단계 판정 경로가 unknown, 일반 요청 200 | 시간표 쓰기 시각·런타임 만료 함수·수집기 복구. 정지 중 새 Tetragon 이벤트는 수집되지 않음 | RT-PHASE-01 |
| AWS-17 Pod 단계 초기화 | 변경(opt-in) | 새 UID 시간표, 이전 단계가 새 실행에 미부착 | 원시 `process.pod.uid`, 정규화 실행, process_exit 집계 | RT-PHASE-01 |

AWS-04·05는 `connect_ex`가 반환한 errno와 이름을 기록한다. `EAGAIN`/`EWOULDBLOCK`/`ETIMEDOUT`만 차단 후보로 보고, `ECONNREFUSED` 등 다른 실패나 DNS 예외는 정책 차단 근거가 아니므로 `unknown`이다. AWS-04의 보조 시도는 필수 통과 항목을 추가하지 않으며, 두 직통 시도 중 하나라도 CONNECTED이면 `failed`, 보조 시도의 다른 errno·실행 오류는 `unknown`으로 처리한다. 두 시도는 같은 other-client→engine:8000 흐름이므로 기존 Hubble 대조를 공유한다. AWS-04는 Gateway 이미지가 Python 기반이고 컨테이너 이름이 `gateway`임을 전제로 직통 probe 직전·직후 Gateway Pod 안에서 engine:8000 연결 성공을 확인한다. exec가 불가능하면 `unknown`이다. AWS-05는 팀 소유 `test-receiver` Pod IP만 대상으로 하며, `other-client`가 같은 receiver에 연결하지 못하면 `대조 실패: receiver에 닿지 않음`으로 `unknown`이다. 두 사례 모두 정확한 대상 흐름의 Hubble verdict가 `dropped`여야 하며 `DROPPED` 단독으로는 통과하지 않는다.

AWS-04·05 통과만으로는 규칙 전 연결 가능 여부를 입증하지 못한다. 2026-11-01 dev에서는 기본 NetworkPolicy 두 개를 약 1분간 제거해 runtime 규칙 아래 AWS-01·04·05를 확인했고 복원 후 spec·labels 일치를 확인했다. 자동 복원은 경로 실수로 실패해 수동 복원했다. 이 이력은 규칙이 전혀 없을 때의 연결 성공 증거가 아니다. 새 비교는 AWS-12의 별도 Pod·정책으로 수행하며 기본 엔진 통제를 제거하지 않는다.

`192.0.2.1`은 비라우팅 TEST-NET-1이고 이 AWS 묶음은 IMDSv2 hop limit 1이므로 external·IMDS connect는 egress 정책이 없어도 실패한다. 두 probe와 Hubble 행은 보고서의 보조 증거일 뿐 판정에서 제외한다. AWS-06의 모델 probe는 `EPERM`·`EACCES`·`EROFS`를 모두 `WRITE_DENIED`로 출력하지만 `EROFS`만 read-only mount 증거로 인정한다. 모델 폴더 소유자 10002와 엔진 UID 2000 때문에 mount가 read-only가 아니어도 `EACCES`가 날 수 있고, `EPERM`은 Tetragon 등 다른 집행일 수 있다. TG-ASSET-01은 RO mount가 hook보다 먼저 거부하면 미관측일 수 있지만, cache 쓰기 관측이 없으면 연결 근거 부족으로 `unknown`이다.

AWS-07은 `systemctl --user stop/start byul-observation-feed`만 수행한다. stop 뒤 heartbeat `stopped`와 Gateway collector non-ok를 확인하고, target Pod 스냅샷을 포함한 stale 창보다 2초 더 기다려 정상 요청 1건의 HTTP 200 및 `/api/target.verification.state=unavailable`(gateway_hold 없음)을 판정한다. 503은 실패다. start 뒤 collector와 target verification이 ok로 돌아오고 `min(stale_after_s + 30초, 90초)` 재시도 창 안에 정상 chat 200·`ALLOWED`·전달 시도가 회복돼야 한다. 정상 SIGTERM의 gap 행은 보조다. **이 기준은 AWS 재시험 전**이며 적용에는 Gateway 이미지 재빌드가 필요하다. AWS-08은 rollout과 target instance/history 확인 뒤, restart 이후 `engine_info`가 ok가 될 때까지 요청을 보내지 않는다. 그 뒤 90초 재시도 창에서 정상 chat 회복을 확인하고 끝까지 실패하면 마지막 status·reason_code를 남긴다. host snapshot stale은 `snapshot_note`에만 남으며 통과로 채우지 않는다. AWS-01~11의 도구 경로는 정책이나 Deployment spec을 바꾸지 않고 AWS-06 임시 파일 외에는 삭제하지 않는다.

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

위 AWS-07 통과는 이전 판정 기준이다. 수정한 stale 이후 200·대상 미확인·복구 후 대상 ok 기준은 AWS에서 아직 실행하지 않았다. 새 코드 사용 전 Gateway 이미지를 재빌드해야 한다.

## 결과 반출과 비용 절약

각 실행은 현재 폴더의 `evidence/aws-acceptance-<UTC>-<pid>/`를 0700으로 만들고 0600의 `report.json`·`report.md`를 쓴다. 명령 argv, HTTP code, request ID, 판정에 사용한 record 필드, 관측 파일·행 또는 API query가 들어가며 token·token hash·prompt·응답 본문은 넣지 않는다.

다음 실행부터 `report.md`는 한국어 머리말·시험 결과·판정 기준·아는 한계로 쓴다. 결과 표는 각 시험의 수집값을 짧게 보여 주고, 미통과 때는 확인되지 않았거나 어긋난 필수 facts 값을 적는다. 판정 기준과 한계는 이번 실행에 포함된 시험만 표시한다. 통과는 해당 환경의 1회 결과이며 기능 완성을 뜻하지 않는다. 기존 보고서는 바꾸지 않고 `report.json`의 필드 구조도 유지한다.

로컬 준비 → dev 비파괴 6건 → dev 변경 2건 → Pod 교체 뒤 AWS-04·05 재실행 → 필요한 수정과 재시험 → reference 8건 및 같은 재실행 → 증거 반출 → EC2 Stop 순서로 묶는다. 사례 사이에 엔진을 내리지 않고, AWS-08에서만 한 번 cold-start한다. 종료 전에 `report.json`·`report.md` 등 증거 파일의 존재를 확인하고, 실행 폴더를 관리자 보관소나 개발 노트북으로 반출한 뒤 파일 권한을 유지하며 Stop한다.

## Known gaps

- dev에서 `connect_ex`의 `EAGAIN`과 Hubble `dropped`를 확인했다. reference에서는 다시 확인하며 다른 errno이면 도구는 보수적으로 `unknown`으로 둔다.
- Hubble `DROPPED`는 단독 차단 증명이 아니며, 시간 초과와 허용 경로 대조를 함께 확인한다.
- AWS-06은 `EROFS`만 RO mount 증거로 쓴다. Tetragon 등이 `EPERM`을 내거나 UID/소유권 때문에 `EACCES`가 나면 RO 여부는 `unknown`이다.
- dev AWS-01에서는 vLLM 0.31.0의 `Received request chatcmpl-<Gateway id>` 로그를 확인했다. reference에서도 형식을 다시 대조하며 다르면 엔진 수신을 추정하지 않고 `unknown`으로 남긴다.

## SGLang v0.5.22 선택 사례 (로컬 코드·가짜 시험, 서버 미실행)

`tools/aws_lab_acceptance.py --engine {vllm,sglang}`의 기본값은 `vllm`이다. `--cases`를 생략하면 기존과 같이 AWS-01~11만 선택한다. AWS-12~19는 명시 선택한다. 두 엔진에 같은 고정 chat·completions 요청 목록과 SHA-256을 기록한다. AWS-18은 각 경로의 일반·stream 응답, `[DONE]`과 잘림, `usage.completion_tokens`에 따른 답변 길이, 엔진 로그 수신을 검사한다. `usage`가 없으면 단어 수로 보완하지 않고 `unknown`이다. AWS-19는 미지원 12개 경로와 4개 필드를 일부러 보내 HTTP 코드·기록·엔진 미전달을 검사한다. 같은 구간의 정상 ID가 엔진 로그에 없으면 거부 ID 부재만으로 통과시키지 않는다. 이 새 사례는 서버에서 실제 실행한 적이 없다.

SGLang 로그는 `request.received`/`request.finished` JSON의 `headers.x-request-id`로 대조한다. `--log-requests-level 0`이어도 프롬프트 관련 필드가 나오면 보고서에 필드 이름을 표시한다. 엔진 Pod의 실제 command·args·환경변수 **이름**·imageID를 기록하되 Secret 값은 기록하지 않는다. SGLang도 `--phase-timeline --phase-engine sglang`으로 단계 시간표를 수집한다. AWS-11·16·17은 현재 Pod UID의 신선한 시간표와 실행 근거로 판정하며, 근거가 없으면 `unknown`이다. 로그 근거는 v0.5.22 GPU 캐시 있는 기동이며 첫 기동 JIT 컴파일 위치는 미확인이다. `engine_signal`은 trusted 단계가 아니다. AWS-14·15의 대상 판단은 버전 미선언과 Pod image digest 수집에 의존한다. SGLang 전환 명령과 정책 묶음 순서는 [SGLANG_전환절차.md](SGLANG_전환절차.md)를 따른다.

AWS-18은 엔진 Pod 안에서 `/tokenize`를 읽기 전용으로 호출해 짧은 답변 길이로 입력+답변 합이 한도와 같아지는 프롬프트를 찾는다. 그 요청의 200·usage와 1토큰 초과 요청의 `CONTEXT_LIMIT_EXCEEDED`·생성 미전달을 대조한다. 정확한 경계 프롬프트나 토큰화 결과를 못 얻으면 `unknown`이다. 토큰 계산 실패를 의도적으로 주입하는 시험은 가짜 엔진 단위 시험에서만 확인하며 실제 엔진에서는 미확인으로 남긴다. 실제 SGLang 결과가 코드 예상과 다르면 결과를 수정해 꾸미지 않고 사유를 인계한다.
