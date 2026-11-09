# 적용·복귀의 현재 승인과 UPDATE 근거 확인

`serving-runtime bundle apply --deployment-guard /protected/deployment-guard.json ...` 프로필은 현재 외부 승인 목록과 필수 UPDATE 근거를 적용 전과 링크 전환 직전에 확인한다. `--existing` 전환에도 같은 검증을 수행한다. 실패 후 자동 복귀할 때는 이전 묶음을 다시 읽고 현재 guard·승인을 다시 확인한 뒤 링크를 되돌린다. 적용 context/namespace를 승인 target의 Kubernetes 등록과 대조한다. 적용·복귀 후에는 같은 port-forward에서 정확 bundle ID/해시·현재 target 검증·정책 활성·엔진 health를 확인한 뒤 일반 chat canary를 실행한다.

외부 승인 목록이 읽히지 않거나 필수 근거가 없으면 해당 전환을 보류한다. 자동 복귀 대상이 철회·만료됐으면 새 링크를 유지하고 복귀 실패를 보고한다. 상태 조회 불가는 기존과 같이 새 링크를 유지하고 운영자가 확인한다. canary는 해당 target 모델에 최대 8토큰(정책 상한이 더 작으면 그 값)의 일반 요청을 보내고 정상 종료 응답을 확인한다. 기존 token env 파일의 RUNTIME_INFERENCE_TOKEN을 사용하며 canary 결과에는 토큰·응답 원문을 남기지 않고 서버 request ID와 응답 해시를 보존한다. 적용 성공과 정상 복귀의 구조화된 결과를 CLI에 출력한다. 실제 GPU 엔진에서의 canary·파일 소비·정상 복귀 실증은 별도 인수한다.

이 프로필은 기존 `HashListApprovalVerifier`, `load_policy/evaluate_policy`, UPDATE의 `Result/Evidence/Environment/Plan` 모델과 정확한 `Ref`·seal 해시 규칙을 사용한다. 실행 환경에 기존 UPDATE 패키지가 있어야 한다. 설치된 BYUL 패키지 또는 작업 사본의 `update/src`를 사용한다.

보호된 guard 파일은 운영자가 현재 필수 CHK→팩 검사 ID, 팩 Ref, 승인할 정확한 묶음·대상 해시, 유효기간·철회와 허용 근거를 선택해 관리한다. bundle 자체의 자기 신고 승인 파일을 외부 권한자의 승인 근거로 전환하지 않는다. 파일 소유권·writer·변경 절차와 마지막 확인 담당자는 실제 배포에서 인수한다.

```json
{
  "schema_version": "runtime-deployment-guard/1.0",
  "approvals_path": "/protected/current-policy-approvals.json",
  "pack_ref": {"kind": "PACK", "id": "PACK-BASELINE", "revision": 33, "sha256": "팩의 정확한 해시"},
  "required_checks": {"CHK-SUP-003": "SUP-03"},
  "max_evidence_age_s": 3600,
  "records": [{
    "bundle_sha256": "검토한 bundle.json bytes 해시",
    "target_sha256": "묶음 target 역할 파일 bytes 해시",
    "model_inventory_sha256": "시험한 UPDATE 전체 모델 inventory 해시",
    "valid_from": "2026-11-06T00:00:00Z",
    "valid_until": "2026-11-07T00:00:00Z",
    "revoked": false,
    "artifact_root": "/protected/update-export",
    "checks": {
      "CHK-SUP-003": {
        "result_path": "/protected/update-export/res.json",
        "result_ref": {"kind": "RES", "id": "실제 결과 ID", "revision": 1, "sha256": "실제 seal 해시"},
        "evidence_path": "/protected/update-export/evd.json",
        "environment_path": "/protected/update-export/env.json",
        "plan_path": "/protected/update-export/plan.json"
      }
    }
  }]
}
```

위 문서는 구조 예시다. CHK→내부 ID는 고정팩의 실제 `framework_id`·질문·실행 범위를 대조해 채택한다. 필수 목록을 비우거나 팩 검사 하나의 결과를 다른 CHK 전체 완료로 확대하지 않는다.

검증 범위:

- 외부 승인 목록의 정확한 gateway policy bytes·ID·판본·approval_ref·기간, 정책 자체의 유효기간.
- 정확한 bundle/target bytes, 현재 record의 유효기간·철회, 필수 CHK의 누락·중복 연결.
- sealed RES→EVD→ENV/PLAN의 정확 Ref와 지정 PACK Ref, 실제 선택한 검사 ID·side.
- MEASURED security PASS와 누락 관측/예외 없음, 완료 실행·성공 정상 대조·청소 오류 없음.
- 시험 엔진 이름/판본/image digest·모델 ID/revision/전체 inventory hash, 원실행 시작·종료·판정시각과 근거의 최대 나이.
- EVD가 참조한 artifact의 실제 bytes·크기·해시와 지정 artifact_root 내부 경로.

기본 LAB 경로는 기존 동작을 유지하며 guard 프로필은 명시한 경우에 집행된다. UPDATE 이월 결과의 자동 eligibility 재계산은 이 adapter에 포함하지 않으며 직접 실행한 selected PLAN 근거만 받는다. 실행 중 지속적인 외부 철회 감시와 runtime의 외부 승인 hot reload는 후속 연결이다. 이 프로필은 적용·복귀 명령이 외부 승인·근거를 확인하는 경계다.
