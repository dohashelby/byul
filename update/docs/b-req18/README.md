# REQ18 V1 slot mask 진단

원 REQ18 check의 executor/judge는 null, policy_params는 []다. 이 묶음은 shared check·pack·registry를 바꾸지 않고 외부 DRAFT에 제한된 source-path 진단을 연결한다. mask 상속의 독립 불일치에만 FAIL 규칙이 있으며 PASS 규칙은 없다. 정상 관측도 전체 live REQ18 PASS로 집계하지 않는다.

## CPU와 Kubernetes 분리

source CPU는 db9527a의 실제 V1 InputBatch/Sampler를 사용한다. ENV.engine.version은 source-<commit>, model/image는 미설정이고 installed_target=None·GPU 검증=False다. 단독 unrestricted B, condense/reuse B, swap B를 같은 fixed logits·현재 B 제약으로 비교한다. 실제 mask=None이면 hash/count를 null로 남긴다. 할당된 실제 zero row의 실측 hash/count0과 물리 hash를 억지로 같게 만들지 않는다. 두 경로의 effective-unrestricted 의미와 실제 Sampler 출력으로 대조한다. host stale row만 있고 Sampler metadata가 mask=None이면 상속 FAIL을 만들지 않는다.

Kubernetes는 실제 ENV.engine.version=0.31.0·model repository/revision/inventory·API alias·Pod/container/image/restart와 별도 단일 audit PLAN/EVD를 사용한다. CPU ENV/PLAN을 재사용하거나 서로 다른 환경을 update 비교쌍으로 만들지 않는다. CPU fixture의 vocab32·토큰 번호·logits를 배포 대상에 이월하지 않는다.

## 명령과 입력

Python3.12 정상 모드, checkout source와 로컬 guard venv를 사용한다. CPU 실제 수집은 coordinating session이 source를 고정한 뒤 validator가 한 번 수행한다.

```sh
PYTHONPATH=src:. .venv/bin/python -m tools.b_req18_slot cpu \
  --source-root /approved/vllm-db9527a --output /owned/new-req18-cpu --execute
# source CPU의 Store/summary는 output/run/, 외부 DRAFT pack은 output/proposal-pack/

PYTHONPATH=src:. .venv/bin/python -m tools.b_req18_slot prepare-pack --output /owned/new-req18-pack
PYTHONPATH=src:. .venv/bin/python -m tools.b_req18_slot kubernetes \
  --env /owned/ENV.json --policy /owned/POL.json --pack /owned/new-req18-pack \
  --target /owned/direct-pod-target.json --prepared /owned/sensor/manifest.json \
  --manifest /owned/approved-cohort.json --output /owned/new-req18-live
# 승인된 실제 대상에서만 --execute 추가. 현재 A 지원/핀 미확정으로 요청 전 닫힘.
```

POL은 exact DRAFT PACK Ref와 REQ18을 선정하고 새 기업 한도·허용값을 추가하지 않는다. Kubernetes 실행 호스트에는 kubectl이 필요하다. Mac daemon 우회·새 서버·cluster/Pod 기동·GPU 비용/권한 확대는 수행하지 않는다. A 실행 운영·복구 담당자가 승인된 입력과 명령을 인계받는다.

cohort는 B 도구 전용 입력이다. schema=byul-req18-cohort/1, exact environment_ref/target_sha256/session/run_id, owner/recovery_owner, model_inventory_sha256, 실제 vocab_size와 cohort를 요구한다. cohort는 최대4개 순서 그룹, 각 그룹 최대2개 요청이다. 각 요청은 role와 body이며 role은 standalone-b/request-a/request-b/followup-b다. body는 실제 api_model·plain prompt·max_tokens1..4·temperature0·선정 allowed_token_ids만 허용한다. 숫자는 이 안전 probe의 요청 상한이며 기업 수락 기준이 아니다.

선정 하위분기의 B는 unrestricted다. standalone-b와 request-b는 같은 body이고 followup-b가 있으면 역시 같다. request-a에만 operator가 명시한 actual model domain 안의 bounded allowed IDs를 사용할 수 있다. 선언 manifest를 proof로 믿지 않고 native InputBatch ctor의 실제 vocab/config/instance와 대조한 뒤 발송한다. V2·feature 미확인·재사용 미관측은 UNKNOWN이다. Graph/TP/runner/CLI 옵션을 임의 활성화하거나 false inheritance를 만들기 위한 fault injection을 하지 않는다.

## 실제 Kubernetes orchestration과 A 최소 지원

기존 공개 KubernetesHTTP로 ENV/target/API mapping을 preflight한다. 기존 KubernetesLifecycleReader.snapshot/capture_session으로 같은 session과 PID/start/image 세대를 읽는다. source-qualified ctor RETURN에서 V1 InputBatch의 실제 instance token·vocab_size/config를 bootstrap한다. recording_closed=False인 preflight에서도 파일 두 읽기·process/image/source 검증이 성립하는 범위만 사용한다. 현재 이미지 proof/새 source point/profile이 지원되지 않으면 닫힌다. 선언값·MODULE label·HTTP200·빈 metadata로 native 소비를 만들지 않는다.

승인 후 공개 transport call로 cohort를 보낸다. role case ID는 req18-slot-<role>, 별도 nonce는 X-Request-Id에만 넣는다. native request ID는 기존 source-qualified InputProcessor.assign_request_id의 request_selection 관측에서 연결하며 serving ID prefix를 추정하지 않는다. 제안된 owned seal_session() 뒤 final capture_session을 수집하고 같은 batch instance의 실제 slot 세대를 분석한다. preflight와 final은 서로 다른 단계이며 source-qualified 관측은 재사용한다.

필요한 A 지원은 명시 SLOT_MASK_PROFILE의 bounded completions/cohort/allowed-ID 전송, metadata.slot_mask parser·source hooks·callback provenance, exact owned session seal, 실제 runtime image proof 및 writer-hook 설치 coverage다. 새 전체 session framework나 REQ11 ID/private transport 우회는 요구하지 않는다. 신규 A keyspace와 signatures는 현재 제안이며 지원 완료로 표시하지 않는다.

native namespace는 B callback guard.adapters.vllm_slot_mask.snapshot_slot_mask의 byul-v1-slot-mask-snapshot/1 반환을 사용한다. InputBatch ctor/add/remove/condense/swap/refresh와 Sampler.forward 실제 CALL/RETURN을 source/module/code hash에 연결한다. A_SOURCE_PINS·POINT_CONTRACTS·callback code provenance가 미설정이면 실제 호출 전에 닫힌다. refresh_metadata는 None을 반환하고 batch_update가 있을 때 self.sampling_metadata를 교체한다. refresh CALL→RETURN의 metadata token 교체·host row·non-None branch·device/dtype/shape는 진단 정황이다. 정확한 source/codehash와 closed record도 CUDA copy/DMA 완료나 stream 인과를 증명하지 않는다. CUDA 소비 predicate와 FAIL에는 사용하지 않는다. 외부 JSON verified 플래그나 callback이 생성하지 않는 copy_verified 플래그는 입력하지 않는다.

## 조건부 진단과 미확인 범위

CPU FAIL에는 per-trial source 검증, 실제 unrestricted B, 동일 fixed logits, slot 재사용, 실제 metadata 인자 동일성·Sampler 정상 반환, 실측 host/metadata mask 불일치와 standalone 대비 실제 출력 차이가 필요하다. 늦은 trial/followup/global postcheck 오류가 앞서 검증된 독립 trial을 소거하지 않도록 보존한다. vacated row의 stale 값만으로 소비 FAIL을 만들지 않는다.

Kubernetes의 CPU 실제 mask 값 경로에서만 “stale host-mask가 동일 Sampler 연산 인자로 전달됨”을 판정한다. CUDA에서는 host row와 metadata/mask opaque handle의 동일성만 진단으로 보존하고, 실제 copy/DMA/stream ordering 문맥이 없어 GPU_ORDERING_CONTEXT_UNAVAILABLE·UNKNOWN으로 남긴다. CPU 실제 값 경로에는 현재 B 제약/실 host row와 실제 Sampler 인자 mask row의 일치, 이전 A row signature, 실제 slot 재사용, refresh의 metadata/mask handle와 Sampler CALL/RETURN의 동일성 및 완결된 writer interval이 필요하다. 같은 descriptor·source branch·metadata 교체가 관측돼도 CUDA 소비 확정에는 사용하지 않는다. mask=None와 allocated zero row를 구분한다. GPU tensor를 CPU로 복사하거나 주소·포인터 원문을 저장하지 않는다. GPU 물리값·DMA 완료·최종 GPU 출력 차이는 미검증이다.

writer interval은 단순 “추가 event 없음”으로 완결성을 추정하지 않는다. 선정 PID/start에서 stable census·recorded files·SEALED recording·비잘림과 source-qualified 실제 writer hook 설치 coverage를 확인한다. 제안 capture.slot_writer_coverage는 schema=byul-v1-slot-hook-coverage/1와 각 writer point의 hook_installed/source/codehash를 A 설치 단계가 검증해 제공해야 한다. 부족하면 UNKNOWN이다. source-copy/DMA 순서·동일 mask의 중간 갱신을 판단할 수 없으면 FAIL을 만들지 않는다.

Kubernetes instance bootstrap만으로 active V1을 추정하지 않는다. actual Sampler RETURN의 기존 A payload.call_chain에 GPUModelRunner.sample_tokens가 있어야 하며 해당 MODULE/source/codehash·설치 hook coverage를 다시 검증한다. CALL에 chain이 없으면 생성하지 않는다. RETURN chain 또는 caller provenance가 없으면 UNKNOWN이다. 이 active V1 caller 조건은 CPU source mode에는 적용하지 않는다. 전체 Graph/TP 구성은 별도 미확인이다.

## 저장·복구·정리

단일 scoped capture를 EVD/RES/RPT와 Store에 연결하고 exactRef 재판정·Store 검증·bundle export/restore·복원 Store 재판정을 수행한다. output은 새 경로이며 기존 증거를 삭제하지 않는다. 독립 FAIL은 후속/첨부/cleanup 실패로 사라지지 않는다. 관측 artifact에는 bool/count/enum/hash만 남기고 raw request·토큰 배열·예외 본문·원문 pointer를 남기지 않는다.

finally에서 owned session seal과 기존 probe watchdog/cleanup report를 확인한다. engine Pod 삭제·공유 GPU kill을 하지 않으며 cleanup 미확인은 증거와 운영자 복구 인계에 남긴다. source gate는 신규 native module의 고정 snapshot으로 coordinating session이 component/helper/collector literal pin을 갱신한 뒤 사용한다. 기존 기준선 불일치를 무시하거나 임시 skip하지 않는다. 고정 component는 ee1ff38421dc710685e3ebaa670b1e083991e9b5, gate SHA256은 5f9e45cb57979970e821857a2a26de06509622f5b681d8d6fb0938b254dbf7e2, collector SHA256은 e0fd82cba3189964e00d1049a76882cd711eb08077c21e666528c7c045b1490f, callback SHA256은 659d615f133cdc96ef54163678753a60e6b8ee51aa0e78e0560e0d308f7f50da다. A profile/keyspace/caller code pin은 미확정 None으로 닫혀 있다.

현재 실제 Kubernetes는 미실행이다. B 단위 EXAMPLE과 source CPU 결과는 실제 Kubernetes 완료로 집계하지 않는다. 승인된 A profile/target/provenance·cohort 실행·판정·EVD/복원·소유 cleanup의 실제 확인이 끝나야 live 묶음을 마감한다.

수정된 경계: 실제 실행 본체는 기존 raw source-qualified assign_request_id의 request_selection 기록으로 request map을 만들고, 공통 반환에 없는 slot_request_index를 읽지 않는다. writer의 CALL/RETURN은 선정 PID 전체에서 point/batch/call ID로 짝지으며 open/orphan/duplicate/RAISE 및 refresh CALL부터 Sampler RETURN을 가로지르는 writer는 UNKNOWN이다. CPU source formal 결과는 재실행하지 않는다. CUDA ordering 지원에는 A의 실제 stream·copy 완료·actor/thread 문맥과 관련 writer pair의 검증 가능한 수집이 필요하며, GPU readback/동기화/새 stream/context 생성이나 미emit verified bool로 대체하지 않는다.

이번 fix-only 검증은 EXAMPLE 전용 40 passed(pytest 0.51초, 부모 벽시계 0.667초)다. 기존 formal source CPU 1회 결과를 재사용했고 실제 CPU/Kubernetes 추가 실행은 없다.

## 최종 로컬 검증 (2026-10-29)

고정 코드 c8bea9520b8177d9cbe8af963da3153c58650cf6에서 기본 suite 6514 passed, 기존 19 deselected다. pytest 128.69초·부모 벽시계 129.097초이며 소스 변경 없이 실행했다. native/CPU 직접 회귀31개와 producer fix-only40개가 포함된다. 고정 변경의 집중 검토와 지적사항 확인을 완료했다.

실제 source CPU 수집은 bcfa8f2에서 1회, 3 trials·42 native snapshots·수집8.782초·전체명령11.50초다. 모든 trial 전후 source guard, 후속 대조, Store verify(오류0), 정확 Ref 재판정, export/restore를 통과했다. EVD COMPLETED/DECLARED, 3개 판정 축 UNKNOWN이다. 후속 producer 수정은 Kubernetes 함수5개에 한정되며 CPU 핵심3개 함수 AST 동일성으로 실제 CPU 결과를 재사용했다. wheel159개 guard 파일 바이트 일치와 격리 import도 재사용한다.

EVD: EVD-01M63SN83QZ5CG88PKC03FRKQC@1 (sha256 5e64af9a9b1a26f250a172763d707dc8e3bdedb28e08202d5c31ec622f175eac). RES: RES-01M63SNJC64G64KW00WJJM8M6Z (sha256 b1e8a3e96968343b5d8ec95b82124e3184d1a8b7afe63ab3b8b0406c2d4eea23). RPT: RPT-01M63SNJC7SEV31QRNG0Y5DV9Z (sha256 18891cd91900cbc8cde7da779a55f11d9bff34732f6c3c642af13d543e5b4eca).

영속 증거는 저장소 옆 byul-b-req18-slot-evidence-20261029에 보존한다. 실제 Kubernetes/GPU 요청은 0회다. A profile·native keyspace·hook/code/image pin·writer coverage·owned seal 및 CUDA copy/stream 완료 관측이 남아 있다. 따라서 현재 상태는 B 로컬 구현·검증 완료와 실제 대상 연결 대기다.
