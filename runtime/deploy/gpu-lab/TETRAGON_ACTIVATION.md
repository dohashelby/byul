# 서울 Tetragon 연결과 DEV08 인수

고정 be07 이후 로컬 준비용이다. 아래 명령은 관리자가 대상·이미지·정책과 실행 창을 확정한 뒤 서울 환경에서 실행한다. 이 변경에서 AWS·kind 정책 적용이나 유료 자원 시작은 수행하지 않았다.

## 로컬 생성과 검토

```bash
cd runtime
PYTHONPATH=src:. python3 -m tools.prepare_tetragon_deployment \
  --intent deploy/gpu-lab/intent.aws-lab.json \
  --sentinel-image REPOSITORY@sha256:REVIEWED_DIGEST \
  --output /NEW/REVIEW/DIRECTORY
```

출력은 기존 `render_manifests`를 사용한 정책, kernel identity를 켠 `intent.json`, 기존 sentinel의 digest 고정 사본, 실제 생성된 정책 이름만 구독하는 stream unit, 파일별 SHA256이다. 원본 intent의 서버별 `REPLACE…`는 실제 검토값으로 채운다. 출력 `intent.json`을 정책 bundle의 enforcement intent로 사용하고 bundle을 기존 승인·적용 절차로 준비한다. 새 kernel identity를 반영한 정책과 이전 bundle을 섞지 않는다.

sentinel은 비특권 프로세스가 가짜 파일 한 바이트를 읽는다. TG-HEALTH-01은 기존 renderer의 Post 정책과 kernel data를 사용하며 확인된 사건은 기존 health receipt 경로로 분리된다. 서비스 살아 있음·다른 보호 정책의 완전 수집은 각각 기존 판정 기준을 따른다.

## 설치 연결

- context는 모두 `byul-aws-lab`, namespace는 `serving-lab`이다. engine/sentinel selector는 `app in (serving-engine,byul-tetragon-sentinel)`이다. 기본 engine selector를 바꾸면 준비 도구가 중단한다.
- 검토한 정책 JSON 각각과 sentinel JSON을 해당 context에 적용한다. 별도 `intent.json`이나 `prepared.json`을 kubectl apply 대상으로 사용하지 않는다. 정책의 이전 name을 바꾸는 교체는 아래 별도 절차를 사용한다.
- 기존 `host/byul-observation-feed.service`는 `--hubble-only`로 Hubble·target·phase만 수집한다. 갱신한 unit을 재로딩·재시작해 legacy Tetragon writer를 종료한 뒤 새 stream을 시작한다. 파일 이름은 기존 `observation/tetragon.jsonl`, heartbeat/gap을 유지해 AWS 인수 도구를 그대로 연결한다.
- `byul-tetragon-identities.service`와 생성된 `byul-tetragon-stream.service`를 기존 ubuntu user service 위치에 설치한다. 둘 다 기존 kubeconfig와 실제 group 10010이 있는 user manager로 실행한다. 출력 디렉터리는 ubuntu 소유/10010 그룹의 0750이며 feed·identity는 0640이다. 원문 arguments/environment는 저장 전에 삭제된다. AWS20의 정확한 `byul-read-probe-<32hex>` 시험 토큰 한 개만 `runtime_probe_id`에 보존한다.
- `gateway.yaml`은 같은 context·identity·state 경로를 전달한다. Gateway data는 기존 UID10000/0700을 유지한다. 새 intent를 포함한 승인 bundle과 이미지를 배포하고 정상 요청·정책 hash를 대조한다.
- `byul-tetragon-status.service`는 **system unit**이다. UID/GID10000, 보조 그룹10010으로 실행해 Gateway의 private observations/health receipts와 그룹 공유 feed/identity를 읽는다. 관리자는 `/etc/serving-runtime/kubeconfig`와 상위 디렉터리에 이 UID의 읽기/탐색 권한을 제공한다(예: root:10000, 파일0640·디렉터리0750). repo의 `/home/ubuntu/byul/runtime`도 해당 UID가 읽고 탐색할 수 있어야 한다. 이 unit을 ubuntu user manager에 설치하지 않는다.
- Gateway가 Tetragon 상태 파일을 사용하도록 설정되면 health receipt는 Gateway 실행 그룹10000에 0640으로 공유한다. 독립 ObservationFeed의 기본 receipt 권한은 0600이다. 임시 파일은 두 경우 모두 0600으로 생성한다.
- status 원본과 복구 이력·시험 보고서는 `/srv/serving/runtime/data` 아래 같은 UID로 보존한다. Gateway 대시보드는 `--public-output /srv/serving/observation/tetragon-status/tetragon-state.json`의 최소 상태 사본을 읽는다. 관리자는 public 출력 폴더를 UID10000 소유·그룹10010·2750으로 준비하고, UID10000 프로세스의 data·kubeconfig 읽기와 public 폴더 쓰기 권한을 시작 전에 확인한다. 호스트 설치기의 `serving-runtime-tetragon-status.service`를 사용하는 환경에서는 이 대체 unit을 함께 시작하지 않는다.

## 사전 baseline과 감시 시작

정책 load/state/MODE와 spec, 대상 node·Pod/container 세대, kernel 지원과 sentinel 이벤트를 사람이 검토한 뒤 status unit과 같은 UID·환경에서 다음을 한 번 수행한다.

```bash
PYTHONPATH=src:. KUBECONFIG=/etc/serving-runtime/kubeconfig \
python3 -B -m tools.tetragon_status --context byul-aws-lab \
  --output /srv/serving/runtime/data/tetragon-state.json \
  --evidence-dir /srv/serving/runtime/data/tetragon-evidence \
  --capture-baseline /srv/serving/runtime/data/tetragon-baseline.json
```

baseline의 모든 node/name/namespace/MODE/spec SHA256을 검토하고 status system unit을 시작한다. baseline은 관측 상태를 주기적으로 덮어쓰지 않는다. 적용 정책·node를 의도적으로 바꾸면 변경 검토 후 새 baseline을 명시 채택한다. 수집기/identity/정책/복구 이후 새 실제 event의 네 조건과 gap 이력은 기존 RecoveryTracker가 판정한다. `GET /api/tetragon-status`과 기존 정책 화면에서 같은 상태를 확인한다.

## 선로드 교체

`replace_after_load`는 명시 `expected_mode`와 해당 MODE를 한 번 선언한 새 manifest, `verify_effect` callback이 필요하다. 기존 이름은 검증된 상태로 두고 새 이름의 부재를 확인한 뒤 create-only로 load한다. enabled·기대 MODE·정규화한 spec 일치와 live effect verifier의 정확한 True, probe 이후 같은 정책 ID/MODE/spec, 기존 정책 ID 유지가 모두 확인돼야 old name을 지운다. 새 이름이 이미 있으면 덮어쓰지 않는다. 생성 응답의 UID를 확인한 객체만 UID 조건부 DELETE로 정리하며 생성 실패/시간 초과의 객체는 자동 삭제하지 않는다. old 삭제는 처음 확인한 UID·resourceVersion 조건을 함께 사용한다.

callback은 전달받은 새 정책 row의 name/id에 귀속한 실제 대조를 수행한다. 감시 정책은 정상 읽기와 해당 정책의 새 귀속 사건, enforce 정책은 기존 `match_probe_events`·`reassess_record`로 필요 효과를 확인한다. `Sigkill` 종료, 파일 읽기 거부, 정상 서비스·복귀를 각각 보존한다. bool 성공표지만 돌려주는 callback은 실제 인수 근거가 될 수 없다. 검증 실패·예외·MODE/spec 변화에서는 old를 유지하고 새 정책 정리 결과를 반환한다. 이 도구는 기존 단일 agent 경계에 적용하며 다중 node 교체는 별도 인수한다.

## DEV08 전체 범위 원장

|선정 CHK|재사용·이번 준비|남은 서울 인수|
|---|---|---|
|REQ-002|기존 Gateway route 인가·고정 upstream 및 AWS-19|실제 ingress rewrite/prefix/method 설정과 같은 작업의 인가·handler 도달 대조. 미사용 rewrite는 비사용 기록|
|REQ-003|기존 viewer read 인가·관리 sidecar allowlist·observer NetworkPolicy|일반 client/Gateway/observer별 8000/8001 및 허용·거부 path/method와 부수효과 대조|
|REQ-001|기존 Gateway 정상·직통 제한, AWS-04|Gateway 정상200과 비허용 client 직통 거부·Hubble drop·실제 노출 port·Pod 세대, 교체 뒤 재확인|
|PLT-007|기존 Cilium/Hubble·AWS-05/12, forwarded opt-in|동일 receiver의 정상 허용·금지 송신·실제 수신과 정책 합집합. receiver 장애·수집 공백 별도 기록|
|SUP-005|기존 자산/RO·TG-ASSET 및 DEV06/07 연결|승인 hash·mount·경로·변경 후 사용 차단, 현재 승인·복귀는 DEV06/07 통합판에서 검증|
|PLT-001|기존 security context·권한 probe·AWS-06|유효 UID/capabilities·mount·RO errno·host/device·socket의 실제 사용 권한과 정상 추론 대조|
|PLT-004|AWS20 최소 probe 식별·선로드 MODE/spec/효과·수집/배포 연결|최소 자격 주입·비허용 읽기, 커널 지원·선택 방법의 실제 효과·정상 서비스·monitor 복귀. 실제 vLLM/SGLang 주/worker 종료·GPU 회수는 DEV09/10 별도|

A3에는 위 권한/네트워크/효과 대조, A4에는 baseline·agent 교체·stream 중단·silent stall·네 조건 복구·gap 보존을 같은 판본/대상으로 기록한다. AWS20 exec 프로세스 종료의 결과와 실제 엔진 종료를 각각 기록한다. 과거 kind/AWS 성적은 이번 판본의 실측으로 이월하지 않는다.
