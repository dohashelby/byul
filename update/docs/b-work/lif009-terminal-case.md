# LIF009 pre-init terminal 근거의 독립 검증

`guard.executors.initialization_terminal_case.project_terminal_case(receipt, expected)`는 B LAB collector의 metadata를 기존 activation4(`activation_steps`, `activation_violations`, `activation_missing`, `activation_scope_complete`)로 투영한다. C의 공유 reader/executor와 native stage 계약은 변경하지 않는다. source14 hook/ACK가 생성되기 전 config/model 실패를 실제 Kubernetes container lifecycle 근거로 구분한다.

## 입력과 신뢰 경계

입력은 root/B의 owned collector가 같은 실행에서 확보한 immutable JSON 사본이다. 원 오류 로그는 메모리에서 marker를 확인하고 SHA-256·byte 수·완료 상태·kubectl endpoint만 남긴다. validator는 hash로 생략된 오류 원문을 복원하거나 의미를 재판정하지 않는다. 오류 의미는 고정 collector의 marker receipt와 선택한 fault 선언에 의존한다.

`expected` 필수 키는 `context`, `namespace`, `namespace_uid`, `container`, `probe_container`, `port`, `model`, `failure_marker_sha256`, `failure_kind`다. 마지막 값은 `config` 또는 `model`인 선정 선언이며 native 실행단계를 관측한 값으로 바꾸지 않는다. B의 기존 config/namespace 조회/marker 선택에서 구성한다. 새로운 조직 승인 입력은 요구하지 않는다.

`receipt.context`는 expected context와 같아야 하고 `sequence_scope`는 한 controller 실행을 식별한다. top-level에는 아래 네 구간을 둔다.

| 구간 | 필수 입력 |
|---|---|
| `normal_before` | 아래 admission/recovery와 같은 direct request 전후 snapshot·namespace·probe·sequence |
| `failure` | `pod_before_log`, `pod_after_log`, `log_receipt`, `sequence` |
| `admission` | `namespace_before/after`, `pod_before_request/after_request`, `probe_before_request/after_request`, `direct_request`, `sequence` |
| `recovery` | admission과 동일 shape의 수정 후 새 정상 세대 |

Pod snapshot은 Kubernetes `metadata.name/uid/namespace`, `spec.nodeName/hostNetwork/containers[name,ports]`, `status.podIP/conditions/containerStatuses`를 보존한다. env·args·annotations·status message·원 오류 본문은 이 projection에 전달하지 않는다. probe도 같은 allowlist에서 선택 container의 running ID/start/restart/Ready를 비교한다. hostNetwork Pod는 이 same-Pod-IP proof에서 지원하지 않는다.

각 section의 `sequence`는 snapshot/log/direct_request 키를 controller-local 증가 정수로 매핑한다. 정상 전후·요청 전후를 먼저/나중으로 묶고 `normal_before < failure < admission < recovery`를 확인한다. Kubernetes node의 lifecycle 시각은 해당 attempt의 start≤finish 확인에만 사용하며 controller/remote client의 elapsed와 교차 비교하지 않는다. 기한 충족 값은 이 모듈에서 새로 만들지 않는다.

### terminal attempt와 로그

현재 `state.terminated`이면 로그는 `previous=False`다. 현재 `state.waiting`·`lastState.terminated`이면 `previous=True`를 사용한다. 새 running 상태가 있으면 과거 terminated 오류로 현재 실패를 채우지 않는다. 앞뒤 Pod UID·CID·restart count·start/finish·nonzero exit·ReadyFalse가 같아야 한다. 정상/실패/수정 정상의 Pod UID와 CID는 서로 다르며 recovery에 남아 있던 옛 정상 Pod를 재사용하지 않는다.

`log_receipt`는 `namespace,pod,pod_uid,container,container_id,restart_count,started_at,finished_at,exit_code,previous,returncode,stdout_sha256,stdout_bytes,marker_sha256,marker_observed,capture_complete`와 `endpoint={context,namespace,pod,container,previous}`를 담는다. `returncode=0`, 0보다 큰 byte 수, 비어 있지 않은 stdout hash, marker 일치·관측·capture 완료가 필요하다. CID를 담지 않는 Kubernetes log URL 자체는 앞뒤 snapshot의 동일 attempt와 함께 결속한다.

### 직접 요청과 정상 복구

`direct_request`는 `url`(정확한 해당 Pod IP/port의 HTTP origin), `method=POST`, `path=/v1/chat/completions`, `request_id_sha256`, `transport=http.client.HTTPConnection`, `used_proxy=False`, `followed_redirects=False`, `http`를 담는다. Service 주소나 다른 IP/port·proxy·redirect는 쓰지 않는다. `body_sha256`을 어느 구간에서 제공하면 세 구간 모두 같은 값을 제공해야 한다. 모두 미수집인 기존 producer는 선정된 normal request 생성 코드·정상 대조의 범위에서 소비하며 body hash를 만들지 않는다.

`http`는 기존 CLIENT의 `outcome,sha256,bytes,complete,model,normal_shape_verified,connection_established,request_sent,error_stage,error_kind,errno,connect_refused`를 사용한다.

- 초기화 거부는 연결·송신이 확인된 완료 `5xx`, 또는 `outcome=error`·connect 단계·producer enum `connection-refused`(실제 ConnectionRefusedError 분류)·errno111/61·connect_refused true·연결/송신 false·0byte/empty hash/complete false다.
- `4xx`, 다른 `2xx/3xx`, timeout, network-unreachable, exec 실패, 부분 응답은 이 성공 조건에 합치지 않는다.
- 전/후 정상은 `200`, 완료, shape 검증 true, expected model, 실제 nonempty 응답 hash/byte 수와 연결·송신 확인을 요구한다.

조건 미충족은 고정 `LIF09_TERMINAL_CASE_*` missing과 scope false다. 증거 부족을 새 보안 FAIL로 만들지 않는다. 이 모듈은 native stage event/ACK를 생성하지 않으며 각 step의 `native_stage_ack_observed=False`를 유지한다. 같은 세대의 upper boot failure/비수용/수정 정상이라는 단일 terminal case의 scope 완료와 전체 rank·GPU 정리·조직 보안 판정은 별도다.

## SUP003 필수 subfile 누락과 실행 공유

1. root/B가 승인한 모델의 일반 파일 **새 사본**과 새 immutable LAB 선언을 준비한다. 원모델/active cache 변경·삭제0을 유지하며 원 inode를 공유한 hardlink를 수정하지 않는다.
2. 원 승인 manifest와 사본을 기존 `assets.check`/`runtime_asset_check.py`로 대조하고 새 사본의 필수 `config.json` 한 개만 누락한다. missing1과 사본 경로·inventory/target/intent/launch hash를 보존한다.
3. faulty manifest가 이 사본을 실제 `/models`로 선택하도록 B 단일 actuator가 적용한다. marker는 실제 초기화 오류의 고정 선택 표식이다. 기존 source14가 시작되지 않아도 kubelet terminal CID/nonzero/start/finish·같은 attempt의 로그 receipt를 수집한다.
4. 같은 bad Pod주소 direct request와 ReadyFalse·namespace/probe/Pod 전후를 수집한다. 다른 Service 정상 응답은 bad Pod의 거부 근거에 사용하지 않는다. 원 잘못된 파일 hash만으로 실제 use-refusal을 닫지 않는다.
5. good 새 사본/manifest로 복원한 후 fresh Pod/CID의 같은 normal 요청·응답을 수집한다. `normal_before`, `failure`, `admission`, `recovery`의 원자료를 이 함수에 넣고 B가 기존 activation4 primary에 결속한다.
6. 원 failure와 수정 후 normal을 각각 보존한다. 양 엔진 중 선택한 실제 단일 config/model failure 하나의 기능 인수로 기록하며 native 초기화 단계 전체와 자동 worker 복구(LIF011 제외)를 추가 완료 조건으로 만들지 않는다.

CPU fixture는 `update/tests/test_initialization_terminal_case.py`다. 공개 EXAMPLE 입력은 실제 KubePod/엔진 인수 자료로 사용하지 않는다. 새로운 source 파일의 reviewed-source manifest 갱신은 root 담당이다.
