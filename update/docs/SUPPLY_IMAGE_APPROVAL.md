# SUP-04 실행 이미지 승인 부분관측

> 현행 팩31의 공통 container_config 실행기 버전은 0.6.2이다. 새 live 이미지 관측은 sealed ENV와 정확한 target ENV Ref를 먼저 확인한다. SUP-04 이미지 승인 프로파일·정의·판정은 유지한다. 아래 팩24·0.5.0은 최초 구현 당시 기록이다.

팩24의 SUP-04는 기존 `container_config` 실행기의
`collection_profile: running-image-approval-v1`을 재사용한다.
새 실행기 종류·core 판정·공용 스키마·플랫폼 변환기를 추가하지 않는다.

정책 `values.SUP-04.approved_image_digests`는 명시적인
`repository@sha256:<64자리 소문자 hex>` 목록이다. 기업 승인값은 입력하지 않으며
관측한 이미지나 다른 점검의 정책에서 값을 복사하지 않는다.
미입력은 `NO_POLICY_VALUE`, 명시적 빈 목록은 승인한 이미지 없음이다.

수집 순서:

1. ENV seal과 target.environment_ref의 kind/id/revision/sha256 전체가 현재 ENV와 정확히 같은지 확인하고 소유 run_id·엔진 컨테이너·pinned 이미지를 대조한다.
2. 소유 라벨·실행 상태·컨테이너 ID·실제 image ID·기동 시각을 투영한다.
3. 실제 실행 image ID로 Docker image metadata를 조회하고 pinned RepoDigest를 연결한다.
4. 재 inspect로 위 수집 구간의 identity가 같은지 확인한다.
5. 승인 여부를 관측한 뒤 기존 정상 추론 대조를 수행한다.

BYUL environment의 bare `sha256:<64>`는 명시적인 target의 pinned 참조와
manifest digest가 같고 실제 Docker RepoDigest 연결까지 확인된 경우에만 허용한다.
repository를 ENV에서 추정하거나 local image ID를 manifest digest로 바꾸지 않는다.
ENV Ref가 없거나 다르면 `RUNNING_IMAGE_TARGET_UNVERIFIED`를 기록하고 Docker
inspect·이미지 조회·정상 추론을 수행하지 않는다. 같은 이미지라도 다른 ENV의
신원을 빌리지 않으며, 누락 Ref를 현재 ENV에서 자동 채우지 않는다.

고정된 비승인 실행 이미지가 확인되면 보안 FAIL이다. 이후 정상 대조 수집 오류로
그 근거를 지우지 않는다. tag-only·image ID/manifest 혼동·소유자/정체성 불일치·수집 실패는
UNKNOWN이다. daemon 원문·환경변수·기동 명령·호스트 경로·예외 문자열은 저장하지 않는다.

승인 이미지와 일치해도 package·native library·entrypoint·추가 mount 전체는
확인하지 않았으므로 보안 PASS 규칙은 비어 있다. 원문 challenge의 미시험 분기를
완료로 표시하지 않는다. 정상 추론 관측이 pass여도 전체 기능 PASS 관문은 보류될 수 있다.
성능과 기업 승인 인증도 미검증이다.

기존 PLT-01의 수집·sentinel·판정은 그대로이고, 이 프로파일은 sentinel이나
패키지 명령을 실행하지 않는다. 실행기 버전은 프로파일과 무관하게 정적
`container_config/0.6.2`이므로 정상·ABORTED EVD에 같은 버전을 기록한다.
과거 `0.4.0` EVD는 그대로 보존하고 버전 차이가 있으면 U1은 다시 선정한다.
SUP-04의 동적 이미지 식별 자료를 PLT-01의 sentinel fixture로 예측하지 않는다.
기존 registry 종류는 11종이고 연결된 점검은 12개다.

이번 실제 vLLM 실증은 사용자 요청으로 보류한다. 작성한 Docker/HTTP 대역 회귀는
EXAMPLE이며 실제 실행 이미지나 공급물 보호가 검증됐다는 뜻이 아니다.
테스트 결과·검증 snapshot은 PROGRESS와 PR checks, 77개 분기 상태는
[추적표](CHECKLIST_VALIDATION_STATUS.md)를 따른다.

실제 실증을 재개하면 동일 모델·pinned 이미지·소유 실험에서 승인 일치와 비승인
정책 사례를 구분하고, 부분 관측·원본 EVD·저장 팩 재판정·플랫폼 계약 투영을 확인한다.
전체 공급물 PASS를 얻으려고 package/entrypoint challenge를 삭제하지 않는다.
