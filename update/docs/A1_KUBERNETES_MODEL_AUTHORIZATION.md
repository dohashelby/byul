# REQ05 소유 probe와 native 관측 제품 연결 /1

이 판본은 REQ05 세 분기(SC-02-01·SC-02-04·SC-16-01)와 필수 결과
'모델별 사용 권한', '사용 종료 모델 실행 중단'을 유지한다.
`model_authorization:kubernetes`는 한 선정 base-model alias에 명시 probe 자격으로
요청하고 현재 native session의 요청·모델 객체 관측을 같은 EVD/RES/RPT에 연결한다.
최종 모델 정체·주체 귀속이 아직 미확인이므로 모든 판정 축은 UNKNOWN이다.
기록된 configured-policy conflict·configured-retired-use를 확인된 보안 FAIL로 쓰지 않는다.

공개 HTTP profile은 `MODEL_AUTHORIZATION_PROFILE` =
`kubernetes-owned-model-authorization-http-v1`이다. 기존 KubernetesHTTP 공개 constructor,
transport("kubernetes-probe",[request],timeout), report()를 재사용한다. vllm0.31.0의 정확
ENV/model mapping/직접 IPv4 Pod/engine+probe 전후 세대만 지원한다. 기존 profile의 요청 제한을
완화하거나 REQ11/REQ13 ID를 사용하지 않는다.

각 요청은 POST /v1/chat/completions·mode=json이고, body는 검증된 api_model,
messages=[{role:user,content:hello}], max_tokens=1, stream=false만 허용한다.
id는 req05-model-before/challenge/after, X-Request-Id는
byul-req05-model-<32lowerhex>-before/challenge/after다.
Authorization은 명시한 Bearer 자격 하나다. before/after는 같은 자격, challenge는 다른
자격이어야 하며 같은 token·각 case 한 번·before 최초/after 최종을 유지한다.
한 호출 한 요청, 전체<=300초·각<=120초와 기존 probe/출력 상한·소유 정리를 재사용한다.
raw 자격·header/body/response/예외는 report에 저장하지 않는다.

정식 guard.toml 설정:
```toml
[executors]
model_authorization = "kubernetes"
[adapter]
name = "vllm"
[model_authorization]
target = "/owned/http-target.json"
prepared = "/owned/prepared/manifest.json"
max_duration_seconds = 120
[model_authorization.roles]
allowed = "OWNED_ALLOWED_KEY"
denied = "OWNED_DENIED_KEY"
```
이 이름의 서로 다른 실제 자격은 운영자가 환경변수로 제공한다. 실제 주체↔자격 매핑과 기업
허용표는 독립 준비 입력이다. runtime 기록의 authenticated/ALLOWED 선언만으로 신뢰하지 않는다.
환경변수 값·자격 해시를 증거로 저장하지 않는다.

`python -m tools.build_kubernetes_model_authorization_pack --seed packs/PACK-BASELINE --output /owned/new-pack`
으로 PACK31 exact Ref에서 REQ05만 바꾸는 명시 DRAFT 팩을 만든다.
기존77개 질문/판정 기준과 다른76개 정의 bytes·과거 Ref는 보존한다.
POL.scope.include에 REQ-05를 선정하고 아래8개 필수 값을 제공한다.

| POL key | 형식 |
| --- | --- |
| model_environment_sha256 | seal된 ENV Ref 문서 SHA256 |
| model_target_sha256 | 전체 target canonical JSON SHA256 |
| allowed_principal_sha256 | 승인된 정상 probe 주체의 UTF8 식별자 SHA256 |
| denied_principal_sha256 | 선정 모델에 비허용인 다른 probe 주체 SHA256 |
| selected_model_grants | list[str], 각 principalSHA:modelRepositorySHA:modelRevisionSHA |
| retired_model_revisions | list[str], 각 modelRepositorySHA:modelRevisionSHA:timezone포함퇴역시각 |
| model_validity_start/end | timezone 포함 유효창, 최대600초 |

정상 주체에게 선정 ENV.model.id/revision의 grant가 있어야 하며 비허용 주체에게는 없어야 한다.
해시는 UTF8 원 식별자/명시 revision의 SHA256이며 API alias·객체 hash로 대체하지 않는다.
퇴역 목록 []는 그 시험 미선정이고, 누락은 필수 입력 미준비다. 정책·기한·승인값은 생성하지 않는다.
수집 중 읽은 모델/객체/파일 hash를 승인값으로 사용하지 않는다.

기존 prepared native session과 source/lab/image gate를 재사용한다. 현재 lifecycle target의
lab=true를 해제하지 않으며 native0.32를 지원하지 않는다. 실제 lab=false는 HTTP 실행 전에
native preflight 미지원으로 닫힌다. 실제 대상을 lab=true로 위장하지 않는다.
before의 정상 HTTP 구조를 확인하지 못하면 challenge를 생략하고 after만 시도한다.
각 요청의 source/binding/응답·유효창 hash/enum을 남기고, 선택 session을 seal한 뒤
기존 capture_session을 한 번 수집한다. seal 실패 후에도 수집을 시도한다.

기존 request_index의 ASGI→native request 연결과 같은 process의 BaseModelLoader 반환 객체→
GPUModelRunner._model_forward 객체 연결을 찾아 configured model/revision을 투영한다.
요청/주체/모델/file/tensor의 실제 인과관계를 자동 인증하지 않는다. 기존 index의
final_model_verified/actual_consumption_verified/weights_revision_verified=false는 보존한다.
configured 경로·mount 선언·모델 표시명만으로 repository/revision 정체를 확정하지 않는다.
Kubernetes image_check=False·process/source 미확인·누락·중복은 정확히 미확인이다.

절차 실행 성공과 모델 권한/퇴역 보안 성공은 다르다. alias/LoRA/대체 endpoint, 퇴역 전후의
실제 모델·현재 승인 alias 재사용, 다른 활성 모델 정상대조, 기존 요청 종결/회수는 별도
준비/실증이 필요하다. 이전 REQ11/CPU/설치 증거를 이 소스의 실제 검증으로 소급하지 않는다.
후속 수집/첨부/cleanup 실패는 앞서 관측한 정제 metadata를 지우지 않는다.
실제 producer/source/image/model/principal/POL 결합을 확보하기 전에는 REQ05 전체·두 필수 결과
완료로 기록하지 않는다. GPU stopped·추가Start중단과 CI 결제 차단을 유지한다.
