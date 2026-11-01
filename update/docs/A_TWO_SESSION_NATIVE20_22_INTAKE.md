# A39 인수 후 native20~22 최소 통합

이 문서는 과거 `A1_GENERATION_DEDICATED_LOCAL_PREPARATION.md`의 당시 작업 상태와 구분하는 현재 인계다. PR171은 병합됐으며 main920의 CI41275191055는5검사 모두 성공했다. 과거 B15 실패는 현재 main의 잔여가 아니다.

`adcde856375a4de43d4a5d789c00de875e43e321`은 main920 위로 수락된 local0af의 공통·전용12파일을 원 bytes로 소비한다. A-2 원본4파일과 기존17개 의존 pin을 보존한다. B의 공개 fixture/tool 보수와 문서는 되돌리지 않는다. 전체5개 pin root의394 Git blob이 기존 검토716c29a와 정확히 같다. 따라서 A측 소비 literal을 이 새 ancestry의 adcde856으로 고정한다. 실행 시 읽은 해시를 승인하거나 검사 경로를 제외하지 않는다. 원 B helper858과 c9 기본값은 그대로다.

기존 OBS02 `observation_identity:kubernetes`에서 native20/21/22 공통 투영과 전용 분석을 소비한다. 공통·전용 attach의 전후 실패를 분리하고, 반환된 분석·앞선 관측·후속 gap을 보존한다. 신규 profile·전송·팩·코어 모델·판정은 추가하지 않는다. native0.32와 실제 승인/검사 주체·tensor/worker/backend 소비 미연결,21/22 paired span unavailable은 유지한다. OBS02 권한 주체는 사용자 답변대로 미정이다.

제품394 source 및 guard286 bytes가 기존 고정0af와 같으므로 기존 수락된 Windows/Linux 설치286파일·CLI4사례/24Ref, source30사례/180Ref 등의 범위별 근거를 이월한다. 과거135사례 중129통과·6실패와 후속6+94=100통과 기록을 단일135전체 성공으로 합치지 않는다. Linux3.12.15의57사례도 해당 runtime 범위로만 보존한다. 새 변경 영향 검증은 A측 pin literal/ancestry와 source/index/shadow 거부 경계에 한정한다.

현재 B 승인 목록은 공개 PR171의 gate00979cf6/common df6255e 조합을 유지한다. 이번 A측 gate와 adcde856은 새 조합이며 B 소유자가 검토·전달하기 전 B `verify_source()`가 거부하는 것이 정상이다. B allowlist·B fixture·기존 A/B 수락 기대값을 임의 변경하거나 새 skip으로 전체 CI를 통과시키지 않는다. 전체 CI5와 정식 합류는 새 B 승인 전달물 소비 뒤 마감한다. 과거 PR171의 성공을 이 후보 전체 CI 성공으로 소급하지 않는다.

첫 PLT01/04/08 배포는 별도 main920/CF2 wheel로 고정돼 있다. 2026-11-01 첫 Start1회가 InsufficientInstanceCapacity로 거부됐고 stopped를 재확인했다. 자동 재시도·새 자원·IAM·지역 확대는 하지 않는다. 새109.82USD 공동 예산과 과거46.66USD 이력·120GiB 유지비는 각각 보존한다. 이 native 묶음을 첫 PLT 실증의 선행조건으로 만들지 않는다.
