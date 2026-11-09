# LIF-06·SUP-03 커밋된 CPU 반환의 B 소비 연결

커밋된 A의 실제 CPU 소비 연결 기록을 기존 B 실행기가 선택 입력으로 읽어 projection·원기록 artifact에 보존하도록 연결했다. 기존 실행기·판정 규칙·core·등록·팩은 유지하고 새 실행기 종류를 만들지 않았다. 역사 기록을 현재 GPU·Pod·worker의 관측으로 합치지 않는다.

## 입력과 동작

기존 `lifecycle_transition`과 Kubernetes SUP-03 경로가 선택적 `cpu_support_input`을 받는다. 입력이 없으면 기존 경로를 유지한다. 입력에는 절대 파일 경로·실제 파일 bytes SHA와 별도 `capture_scope`의 session/pod/prepared SHA가 필요하다. 현재 target의 식별자와 같다고 요구하거나 그렇게 선언하지 않는다.

B helper는 일반 파일·크기8MiB·읽기 전후 FD/경로 신원·bytes SHA·정규 JSON·정확 역사 scope를 검사하고 원 A `consumption_links.project()`를 호출한다. 원 기록과 projection은 `b_cpu_support_record.json`, `b_cpu_support.json` 내용주소 artifact로 보존한다. 정규 CheckDef 외 새 관측을 원 판정 필드로 주입하거나 후보를 독립 FAIL로 자동 승격하지 않는다. EXAMPLE 상한·current_target_binding=false·whole_question_complete=false를 유지한다.

파일이 FIFO인 경우 open 자체가 멈출 수 있는 경계를 발견해 `35bf4bf7`에서 사전 일반파일 검사와 O_NONBLOCK을 추가했다. 실제 FIFO, 일반파일→FIFO 교체, 정상 파일 FD 경계 새3개를 검증했으며 원11개는 재실행하지 않았다. 기존8fb9/실패·원자료·후속 metadata는 보존했다.

## 검증과 원 결과

직접 CPU11개는14.33초/부모15.435초, 새FD3개는7.50초/부모8.431초에 통과했다. 현 판본의 전체14개 suite를 다시 통과했다고 합산하지 않는다. 독립 검토0c8e·15d320, 원 DB 요약 보완5eae는 원 source·seal·artifact·정확Ref·자료형을 대조했다. 빈 요약과 잘못된 verification 키는 원 body를 바꾸지 않은 후속 metadata에서 바로잡았다.

| 항목 | 원 EVD / 판정 수준 | 원 RES | 이번에 확인한 업무 |
| --- | --- | --- | --- |
| LIF-06 | ABORTED·abort_reason=null / verification_level=null | 세 축 UNKNOWN | 역사 A지원 record→기존 B factory→지원 artifact 실소비·Store/판정 연결. 기존 native generation 미연결·activation4필드 미수집은 유지 |
| SUP-03 | COMPLETED / EXAMPLE | 세 축 UNKNOWN | 같은 역사 record→SUP B소비자→지원 artifact·Store/판정 연결. 현재 GPU/aux 전체 소비로 확대하지 않음 |

EVD2·RES2·RPT1의 정확Ref와 원 시각·artifact SHA를 [기계 판독 결과](b-cpu-support-connection-20261110.json)에 연결한다. 공유 RPT는 EXAMPLE/official=false다. CPU fixture의 원 고정 시각은 보존했고 현재 실제 GPU 실행 시각으로 표시하지 않는다. 이 변경으로 두 ID의 전체 LAB 실측 완료를 선언하지 않았다.

## 소스 정합성

최종 source35bf의 새 B helper1개와 기존 executor2개를 통합하고 원 generator로510파일 manifest `e1d3b55e4781567d31ef1498c390120c9d71d5b81db48c6b7e09f74e65a8f580`을 만들었다. 나머지507파일·pack_ref·paths는 이전b74와 같고 A 공통 파일을 수정하지 않았다. 실제 독립검토 `c4e2fa28…`의 시각/검토자 그대로 새 정확 검토 행1개를 추가한 뒤 fresh 프로세스의 B→A gate가 exit0이었다. 이전3개 검토 기록은 보존했다.

기존 B_SOURCE_COMMIT/B_HELPER 상수는 원 b1 helper의 신원용이므로 그대로 유지했다. 현재 전체 제품 source는 새510파일 manifest로 식별한다. 이전 GPU source2cb/466파일을 이 새 통합판의 GPU 검증으로 바꾸지 않는다. 새 PR의 전체 기본 시험은 Linux CI에서 확인한다.
