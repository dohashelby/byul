# 프레임워크 기준 동기화 — 2026-10-27

[프레임워크91e84c7](https://github.com/mg-princess/serving-security-framework/commit/91e84c778f4fc8ee1108eaf0551bf51f2f6a2061)를 BYUL main `3e40def8`의 별도 검토 사본에 반영해 PACK-BASELINE29/DRAFT를 생성했다.

## 변경 범위

- 앞선53항목의 중복·책임 경계 정리와 이번 REQ-09 입력 정합성, REQ-10 구조화 출력·도구 조합, LIF-03 미디어 예산 보강을 함께 가져왔다. 기존112개 분기를 보존하고 실제 지원 조건의4개를 더해116개다. CHK77개·36요구·9필수 결과·45필드 양식은 유지한다.
- 기존 기준·인계 원문20개와 [공식시험 대조표](../reference/framework/개발전달/공식시험_대조표.md)·[매핑 JSON](../reference/framework/개발전달/공식시험_매핑.json)2개만 수록했다. 원본 그대로 가져오고 [UPSTREAM](../reference/framework/UPSTREAM.json)의 고정 판본·해시를 확인했다. 공유본·HTML·상위 검사 패키지·전체 엔진 목록은 상위 저장소에서 읽는다.
- 기존 생성기로 정의47개의 revision을 각각1 올렸고30개는 바이트를 유지했다. 예제 정책5개의 팩 참조와 생성 정책 설명·현황표를 갱신했다. 실행기·판정기·typed 관측·정책 값/선언과 runtime·platform·contracts 소스는 그대로다.

내용판본은 `assessment_registry.revision=2026-10-27-input-hardening`이며 파일은 SHA256과 Git commit으로 식별한다. schema0.2·원작성일·기존 시나리오 판본은 보존한다. CheckDef와 팩의 revision은 기존 생성 규칙을 따르며 과거 입력·평가의 판본을 바꾸지 않는다.

## 실행 범위와 개발팀 후속

정본48개 원필드의 보강과 실제 생성 정의의 실행 프로파일을 구분한다. REQ-09의 override는 기존 token-array 두 단계와 typed 관측·judge를 유지한다. 추가 입력의 선정·실행 계획·관측·범위별 결과 연결은 후속이다.

REQ-10 실행기는 현재 sampling-api-v1의 temperature·top_p·min_tokens를 시험한다. 실행기가 정의의 단계를 모두 attempted로 표시하므로, 새 schema/tool 단계까지 수행했다고 기록하지 않도록 override에 PACK28의 정상 대조와 실제3단계를 명시했다. limitations에도 이 범위를 남겼다. 실행기·judge 코드는 바꾸지 않았다. 새 분기의 미실행/UNKNOWN과 현행 제한 프로파일의 결과를 구분하고, 선정·typed 관측·judge·RES 범위를 연결해 해당 범위의 정상·위반·미수집 조건을 확인해야 완료로 처리한다.

LIF-03 미디어 예산은 실제 미디어를 제공할 때 준비한다. 공식 pytest 실행과 vLLM0.31.0 호환, 실제 엔진/GPU/AWS 시험은 이번 작업에서 확인하지 않았다. 공식 부분 시험의 재사용 방식과 기존 main/PR34의 생산자·수집기 활용은 대조표에서 확인한다.

참고한 PR34의 고정 head는 `6f1f964d`, 대상은 `codex/byul-update-http-tls-transport`다. 보고서의 PR34 PACK30 변경은 가져오지 않았다. 이번 사본은 main의 PACK28→29다. 작업 중 추가된 main3e40의 runtime37파일은 그대로 보존했고 update 트리는 고정 구현 검토 기준 c4d2와 같다. 실제 게시 전에는 사용자의 확인을 받고 최신 main과 참고 PR의 변경 여부를 다시 대조한다.

## 보존과 검증

기존 PACK21·27·CPU·golden 원본은 보존했다. PACK28은 이번에 c4d2의80개 원파일로 새 역사 보존본을 만들었다(3e40의 update 트리와 동일). 기존27→28의38개 변경 회귀를 이 보존본에서 유지하고,28→29의47개 변경을 현재 생성 폴더와 대조한다. 현재 PACK29의 중복 ZIP은 만들지 않았다.

최종 로컬 회귀는1,092개 통과(14.56초)다. 정의 생성·팩/구판 보존·선정·정책·제한 실행 프로파일·기대 출력의 관련19개 시험 파일을 확인했다. 원문22개와 생성 팩 해시, 기존 실행·판정·정책/관측 선언을 대조했다. 프레임워크 자체 검사는 상위 저장소의54개 단위 검사·Q12개·합성25반례 범위이며 BYUL 실행기의 새 분기 구현이나 실제 엔진 실측으로 합산하지 않는다.
