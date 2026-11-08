# PR232 CI 수정 및 무GPU 후속 — 2026-11-08

게시 기준 `aa3c6ac8`의 push·pull_request CI 원 실패를 확인했다. 이번 변경은 사용자 요청에 따른 CI 정합성 수정이며 기존 실패·SOURCE/Ref·판정·미측정 상태는 보존한다.

## 원 CI 결과와 원인

| 실행 | 기본 시험 원 결과 | 원 로그 |
| --- | --- | --- |
| push run42460705669 / job128669200112 | 40실패·11,581통과·1skip, pytest580.72초 | SHA `4df872a2f01788a729b1350c2d2188851f0f2039d28e48473d9697b22efc6a13` |
| pull_request run42460709611 / job128669208743 | 같은 40개 실패·11,581통과·1skip, pytest788.57초·부모789.618초 | SHA `943546638ee26232987027b2420fb7481deada56ecc12cf07ebb19512c8d02eb` |

40개는 소스 gate 직접 8개·LIF-07 연쇄 1개, REQ-13 adapter/capture 연쇄 30개, 상태 생성물 대조 1개다. 나머지 shared/platform/docker-stub 검사는 성공했다. 원 두 이벤트를 같은 한 번의 실행으로 합산하지 않는다.

1. 이전 단계에서 추가·수정한 B 소비자와 GPU18 코드가 `release/reviewed-source.json`에 반영되지 않았다. 기존 `tools.verify_req10_consumption --write`로 고정된 전체 범위를 재생성했다. 등록된 새 파일 9개·변경 파일22개를 반영했고 삭제·경로 제외·팩 Ref 변경은 없다.
2. SUP-03 소비 연결로 `template_validation.py` bytes가 바뀌었지만 REQ-13 adapter와 원 capture tool의 helper pin이 이전 값이었다. `_token_ids` 함수 AST가 기준37c284d9와 정확히 같음을 확인하고 두 위치의 동일 파일 SHA만 `4e7df592…`로 갱신했다. A HTTP capability SHA·다른 helper pin·함수 출처·소스 거부·판정 알고리즘은 유지한다.
3. `phase1/gpu.yaml` note 변경 뒤 생성 CSV를 갱신하지 않았다. 기존 `tools.checklist_status`로 CSV를 재생성했다. Markdown 생성물은 이미 일치했다. 77ID·기존 state·원 Ref와 판정은 유지한다.

공통 소유 파일에 대한 이번 수정은 **검토된 코드의 정확한 해시 목록과 helper 승인 pin metadata**에 한정된다. 새로운 A 계약·생산자·기본 팩·공통 수집 기능을 B가 구현한 변경이 아니다. 현재 bytes를 실행 중 자동 신뢰하거나 source 검사를 생략하지 않는다. 변경 hash와 고정 diff를 별도 검토한다.

## 직접 검증

원 실패 40개를 포함한 관련 74개 시험은 처음 73통과·1실패(65.56초·부모67.3219초)였다. 추가 실패는 원 CI 실패 목록에 없던 기존 synthetic fixture가 Windows에서 `write_text`로 CRLF를 쓰면서 LF SHA를 기대한 경우다. 실제 A 승인 SHA를 추가하지 않고 fixture의 두 write만 UTF-8 bytes 쓰기로 바꿨으며, 해당 1개가 통과했다(1.55초). assert·기대값·skip·생산 코드의 승인 범위는 바꾸지 않았다.

실행별 JUnit을 이름으로 대조한 해당 고유 74개는 모두 통과했다. 해당 그룹에는 변경 source·ignored shadow·승인되지 않은 helper·다른 policy/세대·후속 캡처 오류의 기존 거부/독립 FAIL 보존 시험도 포함된다. 원 11,581개 전체 suite·모델·GPU를 로컬에서 반복하지 않았다. 원 실패 로그와 첫 로컬 실패는 각 세션의 고정 out에 보존한다. [정확한 수정·검증 자료](ci-fix-validation-20261108.json)를 따른다. 원격 재검사는 이 수정이 포함된 새 head의 CI 결과로 구분한다.

추가 검토에서 기존 생성기가 `sorted(Path)`의 플랫폼별 대소문자 비교를 사용해 Windows와 Linux의 GPU 기록 순서가 달라지는 문제를 확인했다. evidence A/B 및 팩 파일 정렬의 키를 명시적 POSIX 문자열로 고정했다. Windows/Posix Path와 A/B 요약을 실제 생성 로직에 넣은 새 4사례에서 수정 전2실패/2통과를 보존하고, 수정 후 관련 파일21개 시험이 통과했다(3.98초). 최종 고유 시험은 중복을 제외한 **78개 통과**다. CSV77행에서 과거 `gpu_runs`·정확 Ref·등급·상태는 모두 이전과 같고 phase1_note 27행만 갱신됐다.

## 다음 무GPU 작업

- SUP-03 R3는 원 health/guest source와 원90초 실패·후속 Ready·제품 Ref 미생성·정리/정지/비용 관측을 재사용한다. 새 head를 과거 설치 source로 소급하지 않는다. 모델·archive·CRC·빈 Store 재검증은 추가하지 않는다.
- S1은 SUP-15·16의 원 CPU 기록·입력 형식·후속 저장 오류 보존을, S5는 지원 API의 새 파일 mapping·cache/parser 정상/오류/복구 범위를 진행한다. 실제 API 관측·제품 경로 계약 시험·미실행 분기는 각각 기록한다.
- B5의 [impact·확정 정책 검토 의견](../B5_REVIEW_OPINION_20261108.md)을 추가했다. 77ID와 33/38/6은 일치한다. 기존 EXAMPLE 정책의 REQ-17 75/24는 사용자 확정55/200 반영본이 아니며 A5가 실제 typed 값/반환을 제공해야 한다. impact null·DRAFT·CONFIRMED/REVIEWED/official은 임의 변경하지 않았다.

이번 작업에서 GPU/AWS/SSM 기동은 0이다. CPU 결과·임시 입력·소스 해시 갱신을 실제 GPU 검증·기업 승인·43개 전체 완료로 집계하지 않는다.

수정 head `d1d91329a4c1063256c4bfa2776391db68f38742`의 원격 재검사는 push run42463120479·pull_request run42463124207 각각7/7, 합14/14성공으로 종료됐다. 뒤의 [B3 후속](B3_CPU_FOLLOWUP_20261108.md)은 새 소스 head의 CI로 다시 확인하며 이 성공을 복사하지 않는다.
