# 관리접근 연속 관측

기존 관리 listener가 쓰는 최소 JSONL을 Gateway의 기존 observation collector가 증분 수집한다. `/api/observations?source=management_listener`의 정규화 기록은 Platform의 기존 사건 목록·상세에 표시된다.

관리 listener 실행에 `--record-file /owned/access.jsonl --heartbeat-file /owned/access-heartbeat.json --target-ref engine-a --policy-ref policy-a --environment lab --run-id run-a`를 추가한다. 기존 `--engine-url`, 포트·자격파일 설정을 그대로 사용한다. Gateway `mvp`에는 `--observation-feed management_listener=/owned/access.jsonl --observation-heartbeat management_listener=/owned/access-heartbeat.json --observation-environment lab --observation-run-id run-a`를 추가한다. 부모 경로는 운영자가 소유한 디렉터리로 준비한다. journal과 heartbeat는 같은 UID 또는 root 소유, 그룹·전체 쓰기 금지인 정규 파일이어야 한다.

원 listener 결정인 `allowed`를 보존한다. 엔진 응답이 403이어도 listener가 허용하여 전달했다면 allowed다. 이 필드가 없는 과거 행은 unknown으로 보인다. 행의 UUID, 시간대가 있는 발생 시각, 안전한 scope 참조와 설정 scope 일치를 검증한다. actor는 unattributed로 유지하며 임의 principal 입력을 승격하지 않는다. 정책 판본에 필요한 version/hash가 없는 `policy_ref` 문자열은 native.record에 그대로 남기고 공통 사건의 판본 참조는 null로 둔다.

heartbeat는 유휴 상태에서도 갱신하며 listener 종료·쓰기 실패·갱신 만료는 기존 collector 오류 상태로 나타난다. 쓰기 복구 후 상태는 회복되고 누적 실패 수는 남는다. journal 회전/중복/재시작은 기존 checkpoint와 event_key 경로로 처리한다. 정규화 저장 실패 때 offset을 전진시키지 않고 같은 사건을 재시도한다.

Tetragon producer의 폐기 원인·실제 발생 시각은 기존 collector 상태와 화면에 연결된다. 마지막 원인·시각과 누적 수는 Gateway 재시작·writer 세대 교체 후에도 checkpoint에 남는다. 기존 heartbeat에 새 receipt가 없으면 이전 호환 동작을 유지한다.

이 묶음의 로컬 HTTP 시험은 Gateway/API 연결을 확인한다. 실제 엔진 재기동 및 양 엔진 실증은 별도 인수 항목이다.

## LAB 공유 경로

vLLM sidecar patch와 SGLang engine template은 `/srv/serving/runtime/management-access`를 listener에 RW, Gateway에 RO로 마운트한다. 엔진 container에는 이 journal volume을 추가하지 않는다. 두 Pod가 동일 hostPath를 볼 수 있는 단일 노드 LAB 구성이 전제다. 현재 AWS 실측은 direct host containerd/kubectl이며, 운영자가 해당 노드 host에 다음 디렉터리를 먼저 생성한다. 별도 kind 실험은 호스트 디렉터리가 kind 노드에 보이는지 먼저 확인한다.

```sh
sudo install -d -o 10000 -g 10000 -m 0700 /srv/serving/runtime/management-access
```

파일은 listener가 0600으로 생성한다. rootfs는 계속 RO다. `hostPath: Directory`를 사용하므로 경로가 준비되지 않은 배포는 시작되지 않는다. scope의 REPLACE 값은 원 배포 준비 절차에서 확정한 target/run/environment/policy 참조로 채운다. Gateway의 관측 환경과 listener 환경을 일치시킨다.

이 template 변경은 실제 engine Pod rollout을 동반하므로 실행 중인 8af 엔진 실측 중에는 적용하지 않는다. 새 Gateway/API 묶음과 함께 별도 인수 창에서 적용 후 `/api/observations?source=management_listener`, Platform `/api/runtime/events`의 동일 UUID·scope·허용/거부 및 collector 중단/복구를 확인한다. 현재 원격 배포는 수행하지 않았다.
