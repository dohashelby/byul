# 운영 요청·결과 계약 후보 (미합의)

2026-11-06. 설계 D1~D7 채택, D5에 따른 platform·runtime 내부 형식이다. `contracts/`를 변경하지 않았다. 세 담당자의 합의 전에는 공용 계약·안정 API로 간주하지 않는다. 원 설계 상단의 `contracts/` 초안 위치보다 사용자가 지정한 이 파일 위치를 우선한다.

형식 구현: `platform/api/src/byul_platform/routes/operations.py`, `runtime/src/serving_runtime/operations/`. 식별자는 역할명·토큰 ID만 사용한다. 비밀값·주소·kubeconfig 경로·명령 출력·실명은 결과에 포함하지 않는다.

| API | 인증·의미 |
| --- | --- |
| POST `/api/runtime/operations` | runtime_operator, 요청 등록 |
| POST `/{id}/approve` | approver, 별도 토큰 ID 승인. LAB 자기 승인 예외 |
| GET `?server=server-01` | 에이전트는 자기 서버만, 사람은 역할별 전체/서버 필터 조회 |
| POST `/{id}/claim` | 자기 서버 agent, 본문 `{}`, 원자적 단일 임대 |
| GET `/{id}/bundle` | 임대 중인 자기 서버 agent, 원래 export 코드로 만든 gzip |
| POST `/{id}/result` | 임대 소유 agent, running/최종 결과 |
| POST `/agent-state` | 자기 서버 agent가 준비 조건·현재 상태의 제한된 투영을 보고 |

각 접미사는 `/api/runtime/operations` 아래다. 사람 역할의 새 조회도 토큰을 요구하며, 기존 조회·내보내기는 그대로다.

요청 공통:

```json
{
  "server": "server-01",
  "kind": "bundle.switch",
  "expected": {"bundle_id": "bundle-current", "approval": "verified"},
  "bundle_id": "bundle-previous"
}
```

- `bundle.apply`: `policy_id`, `policy_revision`, `environment_id`, `environment_revision`, 선택 `server_values_override`, `bundle_id`. 원래 내보내기 검증을 통과한 tar.gz를 저장하고 archive/bundle SHA-256을 고정한다. 요청 저장에는 원본 서버 값 대신 해시·정책 판본만 남긴다.
- `tetragon.mode`: `policy_name`, `mode`. expected에 `policy_id`(Tetragon ID), `mode`, `spec_sha256`도 필수. Sigkill 경고 확인은 `confirmation: "ENGINE_RESTART"`. 차단은 최신 에이전트 준비 조건 여섯 항목을 모두 요구한다.
- `requested_by_role`을 보내더라도 권한·감사 역할에 사용하지 않는다. 토큰이 권한의 유일한 출처다.
- 응답은 `id`, 요청자 토큰 ID/역할, 서버, 종류, 고정 payload/expected, 승인 목록, LAB 여부, 생성/만료, 상태, 결과. 임대 응답만 `lease_id`, `lease_until`, `claimed_by`를 제공한다. 일반 조회·결과 응답에서는 lease_id를 제외한다.

상태: 승인 필요 요청은 requested → approved, 즉시 대상은 approved로 등록한다. 실제 실행은 claimed → running → succeeded/failed/rolled_back/unknown. dry-run은 claimed → checked(검증 통과)/failed(검증 실패)로 종결한다. checked 라벨은 '실행 없이 확인 완료'다. 미실행 요청은 24시간 뒤 expired. claimed/running은 기본 600초 임대 만료 시 unknown(`LEASE_EXPIRED`), 재임대·자동 재실행 금지. 만료된 결과 보고도 409, 수동 현황 확인 필요. 기대 상태 불일치는 failed(`STATE_CHANGED`).

결과 예:

```json
{
  "lease_id": "00000000000000000000000000000000",
  "state": "checked",
  "code": "DRY_RUN",
  "started_at": "2026-11-06T00:00:00+00:00",
  "finished_at": "2026-11-06T00:00:01+00:00",
  "before": {"bundle_id": "bundle-current", "approval": "verified"},
  "after": {"bundle_id": "bundle-current", "approval": "verified"},
  "steps": [{"stage": "validation", "state": "passed"}],
  "evidence_sha256": [],
  "dry_run": true
}
```

시각은 시간대 포함, 결과에 비밀 자유문자열을 허용하지 않는다. stage는 validation/link/restart/status/set-mode/rollback, 단계 state는 passed/failed/unknown/skipped다. 오류 코드는 제한된 열거형이다. dry-run 검증 통과는 checked/DRY_RUN(`dry_run: true`), 검증 실패는 failed와 해당 사유 코드로 종결한다. checked는 dry-run의 DRY_RUN 코드에만 허용한다. 종결 요청은 다시 승인·임대·실행할 수 없으며, 실제 실행에는 사람이 같은 내용으로 새 요청을 등록하고 필요한 승인을 받아야 한다.

감사는 파일 잠금으로 직렬화한 순번 event 파일에 추가하고 fsync한다. 변경·삭제하는 상태 파일은 없다. 실행 중 연결 단절이면 임대 만료 후 unknown으로 보존한다. 이 저장 방식은 한 호스트의 로컬 파일시스템용이며 NFS·다중 플랫폼 호스트·외부 변조 방지 감사 보관은 별도 합의가 필요하다.

합의 대상: 버전·스키마 명칭, 상태/오류 코드, 토큰 ID 발급·회전, 영구 DB/감사 보존, agent-state 형식·신뢰 경계, 검토 근거 생성/서명, 다중 노드 전환, 결과 지연 수동 조정. 현재 구현에는 취소·재시도·자동 상태 조정 API가 없다.

## 2026-11-06 kind 지적·화면 보완의 호환 필드

- Result에 선택 `reason_code`(기본 null)를 추가했다. 허용값: BUNDLE_ROLES_MISSING, BUNDLE_INVALID, CURRENT_LINK_INVALID, STATUS_UNREACHABLE, STATUS_MISMATCH. 기존 code는 유지한다. 자유 형식 메시지/경로를 받지 않는다.
- agent-state에 선택 `bundles: [{bundle_id, sha256}]`(기본 [], 최대 256)를 추가했다. 현재 검증된 대상과 context/namespace가 일치하는 서버 내 4역할 묶음만 ID/해시로 보고한다. 경로는 보고하지 않는다. 이전 에이전트 본문도 허용한다.
- 목록 응답에 토큰 원문/해시를 제외한 `actor`(역할 ID/역할, 에이전트면 server 포함), 아직 조회하지 않은 등록 서버의 미확인 상태를 추가했다. 에이전트 조회는 자기 서버의 상태만 반환한다. `POST /agent-state` 보고는 기존처럼 목록 응답 agents에 투영한다.
- 요청에 `claimed_at`/`running_at`을 기록한다. 이전 저장 이벤트에 없는 필드는 화면에 미확인으로 표시한다.

## 차단 능력 판정 근거

설계 §6의 capability는 `available`이면 정적 근거로 통과하고, `unknown`이면 evidence 검사와 같은 동일 노드·커널·방식·정책 spec SHA-256의 real `combined` 시험 passed 근거가 있을 때만 통과한다. `unavailable`은 실측 근거가 있어도 우선 거부한다. evidence를 비롯한 나머지 체크는 독립적으로 모두 요구한다.

agent-state의 각 policies 항목에 선택 `capability_basis: static | live_trial | null`과 `capability_reason: static_unavailable | evidence_required | capability_missing | null`을 추가한다(기본 null). 통과 시 basis와 reason=null, 거부 시 basis=null과 사유를 보고한다. 여러 노드 중 실측이 필요하면 live_trial로 표시하고, 하나라도 불가하면 static_unavailable을 우선한다. API는 이 투영을 보존하고 웹은 근거·거부 사유를 표시하며, 양쪽 모두 최신 보고의 동일 6개 checks로 전환을 허용한다. 기존 보고와 호환하며 공용 contracts는 변경하지 않는다.
