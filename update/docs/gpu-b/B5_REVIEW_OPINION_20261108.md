# B5 impact·정책 검토 의견 — 2026-11-08

기준 커밋 `aa3c6ac8f9b7e4a70f8ea76f6f35b89a3b40737c`의 커밋된 A 문서와 PACK-BASELINE@33만 대조했다. **사용자 결정에 필요한 검토 의견이며 영향도 확정·팩 REVIEWED·정책 CONFIRMED·official 판정이 아니다.** 팩·정책·manifest·판정 규칙·과거 원자료/Ref를 바꾸지 않았다. 새 엔진/GPU/AWS/API 실행과 기존 상태 시험 반복은 0이다.

## 1. 출처와 확인 결론

- impact 초안: [사용자 결정 문서 §5](../dev-1/user-decisions-20261108.md#5-정책-확정자팩-검토자영향도)의 77개 표와 그 기준. 문서 제목의 권장안 일괄 승인과 별개로 §5는 impact를 **사용자가 확정할 초안**으로 명시한다.
- 정책 확정: 같은 문서 [§6-1](../dev-1/user-decisions-20261108.md#6-1-확정값-byul-기준-정책-사용자-확정-example이-아니다)의 REQ-13·17, OBS-05, REQ-03, LIF-13은 **BYUL 기준 사용자 확정값이며 EXAMPLE이 아니다**. 확정 방침을 다시 미정으로 취급하지 않는다.
- 반영 상태: §6-1 끝은 verification.values 반영을 **미반영**으로 기록한다. 검토한 커밋의 [A 정책 초안](../gpu-a/policy-a-draft.yaml)은 EXAMPLE/DRAFT이며, 아래 다섯 정책의 A5 반영 완료본·정확 정책 Ref로 제시되지 않았다. 별도 미커밋 파일/새 반환물을 조사하거나 보충하지 않았다.
- [팩](../../packs/PACK-BASELINE/pack.yaml)은 revision33·review.status=DRAFT·reviewer=null이며 77개 CheckDef의 impact는 모두 null이다. 이 값들은 초안 확정 전 의도된 미반영 상태로 유지한다.

33/38/6은 초안의 영향도 분류 개수다. 발견된 취약점 수·보안 PASS 수·실측 성공 수가 아니다. 이번 문서 검토는 기존 lab/limited/actual_local 현황이나 실제 FAIL·UNKNOWN·출처·실행 세대·정확 Ref를 변경하지 않는다.

## 2. 77ID 완전성·분포

| 영역 | HIGH | MEDIUM | LOW | 합계 |
| --- | --- | --- | --- | --- |
| REQ | 12 | 9 | 0 | 21 |
| SUP | 12 | 4 | 0 | 16 |
| LIF | 0 | 12 | 4 | 16 |
| OBS | 4 | 6 | 2 | 12 |
| PLT | 5 | 7 | 0 | 12 |
| 전체 | 33 | 38 | 6 | 77 |

초안 ID 집합은 pack.yaml의 checks 및 실제 77개 정의 파일과 정확히 같다. 중복 0·누락 0·추가 ID 0이다. 아래 표는 원 초안의 분류를 옮긴 것으로 S4가 새 등급을 부여한 것이 아니다.

| 영역 | HIGH ID | MEDIUM ID | LOW ID |
| --- | --- | --- | --- |
| REQ | REQ-01·REQ-02·REQ-03·REQ-04·REQ-05·REQ-11·REQ-12·REQ-14·REQ-15·REQ-16·REQ-19·REQ-20 | REQ-06·REQ-07·REQ-08·REQ-09·REQ-10·REQ-13·REQ-17·REQ-18·REQ-21 | 없음 |
| SUP | SUP-01·SUP-02·SUP-03·SUP-04·SUP-05·SUP-07·SUP-08·SUP-09·SUP-10·SUP-12·SUP-13·SUP-14 | SUP-06·SUP-11·SUP-15·SUP-16 | 없음 |
| LIF | 없음 | LIF-01·LIF-03·LIF-05·LIF-06·LIF-07·LIF-08·LIF-09·LIF-10·LIF-12·LIF-13·LIF-15·LIF-16 | LIF-02·LIF-04·LIF-11·LIF-14 |
| OBS | OBS-01·OBS-03·OBS-08·OBS-10 | OBS-02·OBS-04·OBS-06·OBS-07·OBS-09·OBS-11 | OBS-05·OBS-12 |
| PLT | PLT-01·PLT-04·PLT-07·PLT-08·PLT-10 | PLT-02·PLT-03·PLT-05·PLT-06·PLT-09·PLT-11·PLT-12 | 없음 |

초안은 retired 정의도 포함한 77ID 전체다. 이 기준에서 retired=true인 8개는 LIF-15·OBS-07·OBS-08·OBS-10·OBS-12·SUP-01·SUP-02·SUP-14이다. 영향도 표에 포함됐다고 모두 현재 실행 대상으로 복구하거나 비적용 상태를 PASS로 바꾸지 않는다.

## 3. 우선 검토할 구체 ID와 의견

구조적 누락은 없지만 근거 서술의 범위 차이는 있다. 특히 초안의 “LIF는 모두 가용성·일관성 문제” 설명은 LIF-04·13의 보안 위협 분류와 LIF-16의 비승인 공급물 적용을 전부 포괄하지 못한다. 아래는 **위험의 가정/적용 범위에 관한 의견**이며 실제 결함·공격 가능성·노출이 확인됐다는 의미가 아니다. 어느 등급으로 확정할지는 사용자 판단으로 남긴다.

| ID / 현재 초안 | 정의에 있는 근거 | 사용자 결정에 필요한 검토 의견 |
| --- | --- | --- |
| [LIF-16](../../packs/PACK-BASELINE/checks/LIF-16.yaml) / MEDIUM | 비승인 모델·코드·설정의 복원 적용을 risk_summary가 명시한다. SUP-05(HIGH)의 승인 후 교체 소비와 비교하면 HIGH 기준의 비승인 공급물 실행과 구분 근거가 필요하다. | 승인 밖 공급물의 실제 적용을 포함하는 범위와 단순 복구/잔존 일관성 범위를 구분해 사용자가 영향도 적용 범위를 확정하는 것을 우선 권고. 새 복원/실제 소비를 입증한 의견은 아님. |
| [SUP-06](../../packs/PACK-BASELINE/checks/SUP-06.yaml) / MEDIUM | 초안 설명은 승인 파일의 누락을 근거로 MEDIUM을 두지만 CheckDef는 비승인 모델 세대·승인과 다른 shard를 정상 적재로 인정하는 경우도 포함한다. | 누락에 따른 가용성/무결성과 비승인 모델 소비의 보안 경계를 나누어 SUP-05(HIGH)와 같은 기준으로 설명할 필요. 기존 CPU shard fixture를 GPU 메모리 적재로 확대하지 않음. |
| [SUP-16](../../packs/PACK-BASELINE/checks/SUP-16.yaml) / MEDIUM | 부분 파일 변경권자에 의한 비승인 공급물 소비와 일반 생성 중단이 같은 정의에 들어 있다. 중단/로딩 오류만으로 MEDIUM 근거를 한정하면 공격자 변경 분기가 빠진다. | 일반 중단 결과와 승인 밖 변경 내용 소비를 구분해 영향도의 대표 범위를 확정할 것을 권고. 실제 위반 또는 최종 worker 소비가 확인됐다는 주장은 아님. |
| [REQ-13](../../packs/PACK-BASELINE/checks/REQ-13.yaml) / MEDIUM | 팩은 비허용 내부/system/자격 표식 반환을 FAIL 기준에 포함한다. 초안 HIGH 기준의 직접 자료 노출과 MEDIUM의 조건부 정보 저하 사이에 경계를 적어야 한다. | 허용된 자기 입력/비민감 오류 필드와 다른 주체·비공개 문맥/자격 노출을 구분. Gateway 한정이라는 6-1 결정은 보존하며 엔진 직통의 과거 FAIL을 새로 PASS로 바꾸지 않음. |
| [REQ-21](../../packs/PACK-BASELINE/checks/REQ-21.yaml) / MEDIUM | REQ-19·20은 자료 노출 가능성을 들어 HIGH인 반면 REQ-21은 재기동 조건을 들어 MEDIUM이다. 팩은 REQ-21의 일반 호출자 유발 능력·자료 노출을 별도 확인으로 명시한다. | 재기동 조건만으로 영향을 낮췄는지, 현재 확인 범위가 결과 오염/장애에 한정되는지 근거를 명시할 것을 권고. 자료 노출을 사실로 추가하거나 TP2 미실측을 지우지 않음. |
| [PLT-06](../../packs/PACK-BASELINE/checks/PLT-06.yaml) / MEDIUM | 한 worker 침해가 다른 요청·모델에 영향을 줄 수 있다는 정의다. 동일 신뢰 그룹 내부와 정책상 독립해야 하는 경계가 서로 다른 경우다. | 채택한 격리 요구·공격자 시작 권한·영향 대상의 범위를 영향도 근거에 기록. 단순 동일 UID를 권한 우회로 단정하지 않으며 독립 그룹 영향은 별도 직접 근거 필요. |
| [LIF-04](../../packs/PACK-BASELINE/checks/LIF-04.yaml) / LOW | 초안 LOW의 제한 비용/정리와 달리 팩은 호출자가 연결·버퍼를 점유해 다른 요청 처리 기회를 소모할 위험을 보안 위협으로 분류한다. LIF-03(MEDIUM)의 자원 독점과 비교가 필요하다. | 소유 연결의 제한적 비용인지 다중 사용자 처리 방해까지 포함하는지 경계를 명시할 것을 권고. 자원·기한 위반 여부 자체와 영향도 평가는 별개로 유지. |
| [LIF-13](../../packs/PACK-BASELINE/checks/LIF-13.yaml) / MEDIUM | 비허용 전역 release·승인 밖 fallback 소비를 FAIL에 포함하고 risk_category는 보안 위협이다. 초안의 LIF 전체를 가용성으로 묶은 설명만으로 충분하지 않다. | 6-1의 runtime 제어기/동일 세대/디스크 fallback 금지에 비추어 타 소비자 방해와 승인 밖 공급물 소비를 구분. 기존 참조 영향은 시험 필요라는 원 한계 보존. |

LIF-11·14의 LOW는 현재 정의가 각각 실패 자원/다음 기동, profiler 종료/자원 사용을 다루고 민감정보·비허용 호출을 별도 보안 점검으로 분리한다는 점에서 제한 범위 설명이 가능하다. 단지 LIF라는 영역 이름 때문에 낮추지 말고 그 범위를 근거로 남기는 것을 권고한다. 새로운 자료 노출 효과를 추정해 추가하지 않는다.

## 4. 결정 6-1과 A5 반영 대조

사용자 확정값과 실행용 파일의 존재·타입·실행 결합·정확 Ref는 다른 단계다. 기존 [기업 입력 요청 목록](../dev-1/enterprise-input-requests.md)은 11-06 자료다. §6-1로 이미 정한 다섯 방침을 다시 사용자 미확정 입력으로 요청하지 않고, 남은 **A5 반영/변환 및 실제 실행 결합**을 구분한다.

| ID | 커밋된 사용자 확정값 | 현재 팩 입력/커밋된 반영 상태 | 아직 필요한 A5 반환 |
| --- | --- | --- | --- |
| REQ-13 | allowed_response_fields=[id,object,created,model,choices,usage]; allowed_error_fields=[message,type,code]; forbidden_return_options=[echo,prompt_logprobs,logprobs]; required_targets=Gateway | allowed_response_fields:list[str], allowed_error_fields:list[str], forbidden_return_options:list[str], required_targets:list[str]. A 정책 초안 values에 미포함 | 네 list[str]의 verification.values 매핑, Gateway 대상의 실제 식별자/변환 출처. choices 안 logprobs를 별도 금지 필드로 두지 않는다는 결정 보존. 기존 response_options_tested/response_targets_tested의 eq 규칙·목록 의미를 임의 완화하지 않음. |
| REQ-17 | max AUC 55%; 최소 표본200; 공유 단위 tenant; 공개 prefix 공유 허용 | sensor_environment_sha256:str, sensor_target_sha256:str, sensor_prepared_sha256:str, sensor_manifest_sha256:str, cache_timing_max_auc_percent:int, cache_timing_min_samples:int. EXAMPLE/DRAFT에는 max_auc=75·min_samples=24 및 실행 SHA placeholder가 남아 있어 6-1 반영본 아님 | 55/200 정수 전달, tenant/공개 prefix 설정의 실제 매핑·출처, 현재 실행 결합 SHA. tenant/공개 prefix는 현재 CheckDef에 별도 policy key가 없어 prose 확정을 기존 설정/센서 문맥에 어떻게 연결하는지 명시 필요. |
| OBS-05 | usage_allowed=false; 필드·목적지·경로 목록 없음 | usage_allowed:bool, usage_report_paths:list[str], usage_settings_fresh_process:bool, allowed_usage_fields:list[str], allowed_usage_sinks:list[str]. A 정책 초안 values에 미포함 | usage_allowed=false와 목록 없음의 typed 표현, usage_report_paths/allowed_usage_fields/allowed_usage_sinks 및 fresh-process 입력의 처리. 허용 목록이나 usage_settings_fresh_process=True를 검토자가 임의로 승인/관측한 것으로 만들지 않음. |
| REQ-03 | 일반 사용자: completions·chat 추론과 모델 목록 조회. /metrics를 포함한 관리·진단은 관리자만 | general_allowed_kinds:list[str]. A 정책 초안 values에 미포함 | general_allowed_kinds:list[str]의 기존 작업-kind 식별자 매핑과 실제 관리/진단 route·역할 바인딩. 승인된 기본 방침을 다시 기업 미확정으로 돌리지 않되 실제 route 미관측은 그대로 남김. |
| LIF-13 | release는 runtime 제어기만; 승인된 같은 세대만 재취득; 디스크 fallback 금지 | sensor_environment_sha256:str, sensor_target_sha256:str, sensor_prepared_sha256:str, sensor_manifest_sha256:str. A 정책 초안 values에 미포함 | release 역할·재취득 세대·fallback 정책을 기존 verification.values/실행 계약/센서 문맥에 연결하는 A5 반환. 현재 CheckDef의 policy_params는 sensor_* SHA 네 개뿐이므로 그 SHA를 세 정책 의미의 반영 완료로 간주하지 않음. |

**구체적인 불일치:** `docs/gpu-a/policy-a-draft.yaml`의 REQ-17은 `cache_timing_max_auc_percent: 75`, `cache_timing_min_samples: 24`다. 이 EXAMPLE/DRAFT를 §6-1의 55/200 반영본으로 사용할 수 없다. 기존 75/24 기반 원 결과를 새 기준의 PASS/CONFIRMED 또는 공식 결과로 재라벨하지 않는다.

REQ-13·REQ-03·OBS-05·LIF-13은 이 A 정책 초안의 values에 없다. 이는 사용자 방침 미확정의 증거가 아니라 **검토한 기준의 A5 반영 자료 부재/미제시**다. 다른 정책 객체·미커밋 파일의 전역 부재를 주장하지 않는다. user-decisions 문서 자체도 미반영으로 명시한다.

이 표의 실제 실행 결합 SHA·정책 Ref는 후속 선정 실행/정식 객체가 생성될 때의 인계 항목이다. 지금 A5의 typed 값/변환 반영이나 B의 독립 연결 개발을 막는 선행조건이 아니다. 이미 확정된 §6-1 값을 다시 승인받을 필요는 없다.

REQ-17의 sensor_* SHA, OBS-05의 fresh-process/실제 경로, LIF-13의 네 sensor_* SHA는 각각 실행 결합/준비 입력과 구분해야 한다. 검토자가 임의 SHA·실제 역할·선택 세대·수집 성공을 채우지 않는다. 전달 key가 현재 팩에 없는 정책 prose는 A5가 기존 변환 계약의 전달 위치와 검증 연결을 명시해야 한다. 이 의견 때문에 B가 공통 schema·팩·judge를 대신 수정하지 않는다.

SUP-11 -OO는 같은 결정 문서 §4에서 TF5.17 지원 범위 밖 UNKNOWN으로 정해졌고 문안 반영은 미반영으로 표시한다. A5 지원 범위 표기의 후속으로 구분하며, 다른 정상 대조 모델로 바꾸거나 운영 -OO 금지 정책을 새로 만들지 않는다.

## 5. 사용자 결정과 다음 인계 범위

- 영향도: 77ID 초안 자체는 형식상 누락 없이 검토 가능하다. 위 경계/조건의 설명을 반영해 사용자가 확정할 값이며, S4는 팩 impact를 쓰지 않는다. 확정 뒤 A가 새 팩 판본으로 반영하고 과거 팩·결과를 보존한다.
- 정책: §6-1 다섯 방침의 사용자 확정은 유지한다. A5는 고정 커밋·파일 SHA·타입/변환·선정 실행 결합과 실제 policy Ref를 반환해야 하며, A 원문 파일은 B가 고치지 않는다.
- 팩 검토: B 담당자로 지정된 사실과 pack.review를 실제 REVIEWED로 변경하는 행위는 별개다. 이 문서는 검토 의견만 제공하며 CONFIRMED·REVIEWED·official 변경을 수행하지 않는다.
- 공식 재판정: 작업지시서 J2에 따른 새 정책·새 팩·PLAN/증거의 정확 Ref와 출처 절차는 별도다. impact 확정만으로 보안 PASS나 official이 되지 않으며 EXAMPLE 증거는 공식 근거로 올리지 않는다.
- 이번 의견은 B1/B2의 이미 유효한 연결 검증이나 다른 독립 준비의 추가 승인/수신 ACK gate가 아니다. 새 과거 원자료 검색·동일 상태시험·엔진/GPU/AWS·팩/manifest 수정은 0이다.

## 6. 고정 출처와 문서 정합성

| 출처 | 기준 커밋 bytes SHA256 |
| --- | --- |
| `update/docs/dev-1/user-decisions-20261108.md` | `79666b87b2888e775dfd9615dfa1ae3bee0feb8b1e65cd9a8333445ac11b171e` |
| `update/docs/gpu-a/policy-a-draft.yaml` | `b32c8b33c6e85d6440753984eb5a849453e8eb46d15e736735f17223260ec867` |
| `update/docs/dev-1/enterprise-input-requests.md` | `1a9ca2178251ca856eb76285c60fe92c64eeb648647b3049512861f1ce83e2f8` |
| `update/packs/PACK-BASELINE/pack.yaml` | `154cd52d6f038539e87d42074edbbdb63e5d198c4db22f64d0290975382500ba` |

각 우선 검토 ID의 정의는 위 링크와 기준 커밋에 고정되어 있다. 77개 정의의 경로/SHA, 원 초안 전개 결과, 다섯 정책의 필수 입력 타입은 자기 out의 근거 JSON에 보존한다. 문서 freeze 뒤 77ID 집합·33/38/6·중복/누락·정책값/초안 차이와 보호 파일 불변만 검사하며 pytest·팩 재판정·엔진 시험을 반복하지 않는다.
