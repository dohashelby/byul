# REQ18 선정 V1 slot mask CPU·native 경계

범위는 vLLM0.31 db9527a의 InputBatch condense/swap/current-mask 및 실제 Sampler.forward다.
공식 `test_condense_clears_stale_allowed_token_ids_mask`·swap 시험의 의미를 재사용하며
공식 pytest 통과 수나 GPU·Kubernetes 결과로 계상하지 않는다. 기존 feasibility5.25초 및
첫 batch-only6.67초 원본은 `/private/tmp/byul-req18-cpu-feasibility-d7czrax7/`와
`/private/tmp/byul-req18-cpu-feasibility-2sjf60w_/`에 보존했다.

CPU API는 `tools.b_req18_slot_cpu.capture_slot_cpu(source_root,workdir,*,source_profile='target031')`다.
기술 fixture는 vocab32·길이16·allowed13·preferred31·greedy0이며 기업 정책이 아니다.
세 핵심 case는 batch1의 단독 unrestricted B, condense에서 restricted A를 옮긴 후 새 B의
실제 slot 재사용, swap의 A제약 보존/B무제한이다. public constructor device=cpu 및 원래
PIN_MEMORY=False를 사용한다. public refresh_metadata 후 실제 Sampler를 호출한다.
단독 B는 실제 mask=None이다. hash/count는 null로 두며 가짜 zero hash를 만들지 않는다.
None(skip)와 allocated zero-row의 동치는 effective_unrestricted의 서로 다른 실제 분기와
동일 logits·현재 제약·실제 출력 hash로 판단한다. 물리 mask hash 동치와 구별한다.

source-invariant mismatch는 확인한 현재 제약과 실제 metadata-mask/CPU sampler 출력의
불일치 진단이다. 원 REQ18 executor/judge null·빈 정책은 바꾸지 않으며 public PASS나 새
기업 기준을 등록하지 않는다. 각 trial source 전후 확인과 후속 정상·source postcheck·보조 쓰기 실패를 구분하며,
이미 확인된 독립 관측을 지우지 않도록 결과를 순차 보존한다.

component pin은 미설정이며 실행 전 닫힌다. B1 helper858666/basec9와 전체 scope를 유지한다.
새 SRC를 거부하는 legacy gate를 완화하지 않는다. root가 고정 native component commit을
명시 승인·연결한 뒤 그 최종 source에서 정식 CPU→EVD 실행을 한 번 수행한다.

## B 내부 native callback 제안

`guard.adapters.vllm_slot_mask.snapshot_slot_mask(point,args,kwargs,*,phase,result=None,
exception=None,binding=None)`는 실제 A source-qualified hook 객체의 read-only 관측이다.
namespace는 `metadata.slot_mask`, schema는 `byul-v1-slot-mask-snapshot/1`을 제안한다.
A 공통 callback/keyspace/source·code·image·process·session provenance pin은 아직 미확정이다.

point는 InputBatch.__init__/add_request/remove_request/condense/swap_states/refresh_metadata,
Sampler.forward이다. ctor RETURN과 실제 batch에서 vocab_size/max_model_len/max_num_reqs/device/
is_pooling_model을 읽고, weak process-local batch token을 instance epoch로 제공한다.
CPU fixture32를 실제 GPU dimension에 이월하지 않는다.

refresh RETURN은 host CPU mask 각 행의 count/hash, request SHA→slot, restricted membership,
실제 sampling_metadata와 mask의 opaque identity/device/shape/dtype를 기록한다. Sampler CALL은
실제 인자의 같은 metadata/mask handle을 산출하며 RETURN CPU 출력은 count/hash만 기록한다.
객체 token은 weak reference와 난수로 만들며 주소·raw pointer를 노출하거나 target을 보유하지 않는다.
GPU tensor는 descriptor만 읽고 `.cpu()`/`.numpy()`/GPU `.tolist()`·수정·zero/copy·새 CUDA context를
호출하지 않는다. CPU mask도 실제 bool Tensor의 bounded row만 읽고 원문은 버린다.

B join은 A가 검증한 동일 source/code·PID/start/session·요청·instance에서 slot 세대 변화,
refresh RETURN→동일 metadata/mask의 Sampler CALL/RETURN과 사이의 상태 갱신 부재를 확인해야 한다.
copy 인과는 승인 source의 실제 branch 조건과 RETURN 관측에서 평가하며 외부 verified flag나
생성되지 않는 source_copy_verified stub를 요구하지 않는다. source 명령 발행과 GPU kernel 결과
확인은 분리한다. GPU 값·실제 결과가 미확인이면 그 범위는 UNKNOWN이다.

기술 수집 상한은8개 요청행·vocab262144개·weak object128개다. 이는 기업/서비스 한도가 아니며
초과는 unavailable이다. token/request/mask/logits 원문과 주소·예외 문장은 산출물에 저장하지 않는다.
V1/V2·기능·Graph·TP를 강제로 활성화하지 않으며 실제 대상의 선정 조건이 없으면 미지원으로 남긴다.


CPU result의 mask_comparison_basis는 effective_unrestricted_branches다. mask_matches_standalone은
None(skip)와 실제 allocated zero-row의 의미 동치이고, 물리 mask 해시 동치가 아니다.
physical_mask_hash_matches_standalone은 비교할 실제 두 해시가 있을 때만 bool이며 나머지는 null이다.
metadata_mask_present/status 및 host_mask_status가 absent일 때 hash/count는 null을 유지한다.
condense 직후·새 B 추가 전의 retired row hash/count도 실제 CPU tensor에서 별도로 기록한다.

condense/swap의 CALL/RETURN은 동일 batch token·binding.call_id_sha256·point로 연결한다.
관련 CALL의 request SHA/slot을 보존하고 RETURN의 실제 vacated index만 bounded CPU row로 읽는다.
CALL 누락·중복·다른 call ID는 unavailable이며 비활성 범위의 행을 clear로 가정하지 않는다.
add_request RETURN은 실제 assigned_slot_index와 CachedRequestState.sampling_params의
argument_constraint status/restricted/count/hash를 제공한다. Sampler metadata의 mask는
없음(absent), 필드 누락/읽기 불가(unavailable), 실값 수집(collected)을 구별한다.

callback은 A의 standalone asset export에 맞춰 단일 파일 stdlib로 고정했다. 기존 B의
_get/_sha/_binding helper는 같은 AST로 inline했으며 원본 파일은 수정하지 않았다.
`python -I -S`에서 guard/Torch 패키지 import 없이 standalone load를 확인했다.

## 직접 검증

EXAMPLE 단위 회귀21개는0.56초에 통과했다. 실물 native 경계는
`/private/tmp/byul-req18-native-final-u_z90cvv/summary.json`에 보존했다(5.31초).
원본0.31 CPU 객체에서 standalone maskNone, 실제 allocated zero B-row, condense후 vacated2의
실masked_count0, add-request의 실제None제약/새slot2, refresh→Sampler CALL/RETURN handle 일치를
확인했다. 실제 sampling mask는 device=cpu, dtype=torch.bool, shape=[3,32]이며 CPU rows와
실Sampler 반환 count/hash가 수집됐다. 단순opaquehandle 일치만 검사한 결과가 아니다.
실제shape 요약은 actual-shape-summary.json, helper AST동일성과 단일파일변경범위는
packaging-impact.json에 기록했다. 직전 ac91/1b39 native 경계 결과와 feasibility 원본을 보존한다.
최종 packaging 변경은 import경계만 검증했고 정식 CPU capture/EVD는 아직 실행하지 않았다.


argument_constraint의 정확한 키는 status/restricted/count/sha256이다. 실제 None 제약은
status=collected, restricted=false, count=0(지정된 ID 없음), sha256=canonical null hash다.
실제 목록은 해당 count/hash로 구별한다. 이 count와 mask 미할당의 null count는 다른 관측이다.
원시 요청 ID는 SHA만 반환하며, assigned_slot_index는 실제 add_request 반환값이다.
행 source_validation은 각 trial의 pre/post 검사를 유지하고 어느 trial의 post가 미수집이면
전체 capture는 incomplete로 남긴다. 앞서 확인한 독립 mismatch를 지우지 않는다.

## 관측 누락 보완

add_request의 실제 sampling_params 객체와 allowed_token_ids 필드에 각각 sentinel을 적용한다.
params 누락·None·필드 누락·읽기 실패는 argument_constraint.status=unavailable이며,
실제 필드가 존재하고 값이 None인 경우만 수집된 unrestricted로 기록한다. 실제 목록은
별도 count/hash를 유지한다. 후속 대조의 출력 hash가 같아도 mask/metadata 수집이 없으면
완료로 처리하지 않는다. followup.collected=true와 출력 동치를 함께 요구하며, 이전
확인된 trial mismatch는 incomplete 상태에서도 보존한다. 이 보완은 영향 unit으로 검증하고
기존 실제 5.31초 경계와 feasibility 자료를 재실행하지 않는다.
