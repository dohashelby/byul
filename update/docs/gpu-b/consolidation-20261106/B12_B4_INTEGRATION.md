# B1·B2 소비자 연결과 B4 TP2 준비 — 2026-11-08

새 A·B 작업지시서의 B1·B2 소비자 연결과 B4 입력 준비를 기존 세션으로 분담했다. A 반환물이 포함된 `origin/main=1a3b8ac6`을 보존해 통합한 `37c284d9`가 이번 개발 기준이다. 미커밋 A1~A4 입력은 적용하지 않았다. 이번 범위에서 AWS/GPU 기동·Docker·모델 실행·다운로드는 하지 않았다.

## 이번 변경

| 구분 | 연결·준비한 범위 | 검증 결과의 경계 |
| --- | --- | --- |
| B1 수명주기·관측·플랫폼 | request-attempt, generation-transition, health-worker-causal, profiler-control, process-device-provider 기록을 기존 B 실행 경로에 선택 입력으로 연결 | 원 생산자 함수의 CPU 입력·고정 fixture로 확인. 원 기록·scope·누락은 기존 선언 관측과 artifact에 보존. 인과·GPU 비용·weights/KV 실제 소비를 새로 확정하지 않음 |
| B1 요청·공급물 | runner V1/V2 → REQ-08·09·10; supply-reads → SUP-03 | 파일 SHA와 독립 scope를 확인한 뒤 원 A validate를 사용. REQ-08의 token 동일성, REQ-09 MM 전체, REQ-10 min_tokens/structured/V2 전체 count, SUP-03 aux 실제 소비는 미확인 유지 |
| B2 PLT-02·LIF-11 | IPC summarize와 실패 그룹 lineage를 B 소비 경로에 보존 | 미등록·후속 worker·전역 GPU 반환 공백 유지. PLT-02 기본 PACK profile 불일치 차단과 기존 DRAFT 파생팩의 소비 검증을 분리. LIF-11 기존 선정 FD 기한 기능 FAIL을 보안 FAIL로 바꾸지 않음 |
| B2 SUP-06 | worker-shard 생산자의 5키 load 사실을 보존하고 기존 boot의 정확한 3키 행으로 투영 | 현재 미등록이므로 후보를 확정 위반으로 올리지 않음. 기존 legacy/native의 독립 FAIL 유지. TP2 미실행 |
| B2 SUP-09·11 / SUP-10 | model-entry/1, plugin-runner/1을 기존 boot 계약에서 별도 평가 | 기존 dynamic 12사례·plugin 10사례 보존. 새 행 하나로 기존 대조를 대체하지 않음. `-OO` 정상 대조 실패는 UNKNOWN |
| B4 LIF-08·11·SUP-06 | 입력 생성기·정상/실패/복구 순서·기존 POL/profile·A6 반환물·시간/반출/정리 계획 | 준비 파일 생성만 수행. 파일 포인터·런타임 결합 미검증, 실행 준비·권한·예산 충족 false |

REQ-12는 작업지시서의 연결 대상이지만 현재 A `request-attempt.KINDS.checks`에 없다. 공통 입력만 명시적으로 거부하고 기존 native 경로는 보존한다. `supply-reads`도 SUP-03만 지원하므로 다른 SUP 항목으로 확대하지 않았다. 등록 8개는 지원 목록의 정확한 SHA 일치이며 생산자 인증이나 실측 등급이 아니다.

새 입력이 없으면 기존 소비 동작을 유지한다. 입력이 누락·미등록·상충하면 원 사실과 독립 FAIL을 보존하면서 PASS를 차단한다. 외부 파일 입력은 EXAMPLE 기본, DECLARED 명시까지만 받는다. EVD의 새 미선언 top-level 키가 정규화 중 소실되지 않도록 기존 선언 필드와 내용주소 artifact에 기록했다. 원 core/models/store/judges/registry/팩·A 생산자와 등록 SHA는 수정하지 않았다.

## CPU 검증

수명주기·관측·플랫폼은 신규 **53개 통과(45.60초)**다. 실제 생산자 함수 출력·기존 factory·원 정규화·출처 제한·독립 FAIL 보존과 LIF-10 원 팩 및 PLT-02/LIF-11 기존 DRAFT의 EVD→RES→RPT·별도 DB 복원·저장 팩 재판정을 확인했다. 최초 42통과/4실패는 보존했다. JSON list 입력 처리 오류 1개를 수정했고, PLT-02의 3개 실패는 기본 profile 차단을 숨기지 않고 경계별 시험으로 분리했다.

요청·공급물은 v1 **28개 통과(9.49초)**, v2 추가·정정 경계 **3개 통과(3.98초)**다. 원 예외 보존 delta v3의 **3통과/1실패(3.48초)**를 보존했고, 필수 초기값이 빠진 시험 fixture만 정정한 v4 해당 **1개 통과(1.90초)**를 연결했다. 각 실행에는 중복 재확인이 있으므로 합산해 완료 ID나 고유 테스트 수로 쓰지 않는다.

독립 검토에서 REQ-09의 세대/PID 재사용 gap이 있어도 신규 범위 위반이 확정될 수 있는 결함을 확인했다. `28cf69dd` 수정으로 요청별 신원 일치 조건을 신규 위반에 적용했다. 직접 회귀 **5개 통과(3.11초)**로 잘못된 귀속의 신규 확정 차단·기존 독립 FAIL 보존·신원이 유효한 실제 collect 위반 유지를 확인했다. 원 검토 대조와 수정 전 기록은 보존한다.

기존 관련 21파일 회귀는 **651통과/2실패(1089.38초)**였다. 두 실패는 저장소 안의 시험 출력 경로를 `LIFECYCLE_OUTPUT_IN_REPOSITORY`로 거부한 결과다. 보호 규칙과 같은 소스를 유지하고 해당 2개만 저장소 밖에서 재확인해 **2개 통과(12.86초)**했다.

별도 직접 재현에서 주 activation 증거 파일 쓰기 오류가 이미 결합한 FAIL을 소실시키는 문제를 확인했다(원 1실패/7.37초 보존). `69362aef`는 공통 입력을 실제 소비한 경로에 한정해 ABORTED·고정 진단·양쪽 원 위반과 나머지 artifact를 보존한다. 입력 없는 기존 오류 경계도 유지한다. 직접 수정분과 기존 lab 3파일의 **192개 통과(14.02초)**로 원 판정·RPT·별도 DB·저장 팩 재판정까지 확인했다. 앞의 유효한 53개·651개와 전체 21파일을 다시 실행하지 않았다.

이후 검토에서 같은 보호 범위 밖의 디렉터리 생성 오류를 확인했다. 원 1실패/3.71초를 보존하고 `4014364a`에서 mkdir 한 줄을 같은 guard 안으로 옮겼다. 해당 **3개 직접 시험 통과(8.59초)**로 양쪽 FAIL·출처·나머지 artifact·원 판정/보고/복원과 입력 없는 기존 raise를 확인했다. 앞의 192개는 반복하지 않았다.

통합 대표 시험은 실제 등록 `input_validation:real` factory의 run·collect·finish를 거친다. 외부 HTTP 전송만 CPU fixture로 주입했다. 실제 select → run_plan → EVD/내용주소 artifact 저장 → 저장된 원 PACK 재읽기 → judge_plan → RPT 저장을 확인했으며 등록 입력 PASS·미등록 UNKNOWN·미등록이어도 기존 독립 FAIL 보존의 **3분기 통과(8.46초)**다. 모두 EXAMPLE이며 보고서 official=false다. GPU나 실제 배포에 대한 검증으로 집계하지 않는다.

B4 고정 생성기는 **13개 통과(6.15초)**, 독립 검토에서도 같은 13개 통과(7.10초)다. 최초 CLI 시험의 Windows 출력 인코딩 오류(12통과/1실패)는 stdout/stderr와 수신 encoding을 UTF-8로 명시해 수정했다. 독립 재확인 횟수를 새 테스트나 완료 ID에 합산하지 않는다. 원 실패 로그·수정 차이·개별 시험 및 검토의 정확한 SHA는 [이번 검증 근거](b12-b4-validation-20261108.json)를 따른다.

독립 검토는 요청·공급물 `28cf69dd`, 수명주기 최종 `4014364a`, B4 고정 도구·문서 및 통합 뒤 소스 핀 변경을 각각 확인했다. 해당 소비자 변경 범위의 잔여 필수 코드 수정은 없다. 아래 실제 입력·미등록·기본 팩 매핑·GPU/TP2 미측정은 별도로 남아 있다.

## TP2 준비 도구

저장소 루트에서 다음 명령으로 실행 없이 새 준비 디렉터리를 만든다. 기존 디렉터리는 덮어쓰지 않는다.

```text
python update/tools/b12_tp2_inputs.py --output <새-디렉터리>
python update/tools/b12_tp2_inputs.py --output <다른-새-디렉터리> --inputs <고정-입력.json>
```

입력 `guard-b-tp2-inputs/1`의 각 항목은 `runtime_refs`, 기존 `policy_values`, `controls.normal/failure/recovery`, `planned_upper_seconds`를 받는다. 포인터 `{path,file_sha256}`는 파일 bytes의 선언값이다. 도구는 포인터 파일을 열거나 GPU 신원과 결합하지 않는다. 비어 있는 값은 null·사유·담당으로 남는다. 출력은 ID별 계획 3개와 입력 template·선언 입력·manifest, 총 6파일이다.

반환 argv는 자리표시자가 남은 계획 문자열이다. select/run/judge/report는 같은 CONFIG·Store를 사용하고, 실행 전에 실제 PLAN이 의도한 점검만 선정했는지 확인해야 한다. `checks` 3개가 실행 제한 옵션은 아니다. 실행 옵션은 없다. A6 하네스 적용 위치·정책 key·시나리오·시간 가정·반출/정리는 [TP2 계획의 B 절](../../dev-1/tp2-run-plan.md)을 따른다.

## 남은 실제 연결과 반환물

| 담당 | 필요한 다음 반환·실행 | 현재 미확인 |
| --- | --- | --- |
| A 공통 담당 | REQ-12 및 OBS-02 복합 경로의 profiler 대상 선언, PLT-02 기본 CheckDef/profile 정합, LIF-11 기능 축·PLT/OBS 상세 관측 spec 매핑 | 현재 계약의 미선언 분기는 gap으로 남김. 상세·축의 실제 diff가 필요 |
| A1~A4 담당 | 커밋된 transition-dispatch, aux parser 소비, MM/최종 sampling 소비, /1 호환 /2 요약 계약·fixture·정확 source | 현재 /1·4096 이벤트 한도 유지. 다른 request/attempt/worker/generation/phase 요약 혼합 금지 |
| A 생산자·A6 | LIF-08 rank-fault 구현/신원, IPC·실패 그룹·worker-shard 실제 TP2 확인과 지원 등록 | 종류 선언·기동 성공만으로 생산자 구현/등록을 대신할 수 없음 |
| A6·운영 담당 | 실제 하네스 bytes/argv, TP2 ENV/POL/target/prepared·rank/shard roster·normal/failure/recovery·원 시각/ack | 이번 준비는 GPU 실행·비용·할당량·기업 정책의 승인 아님. 시간·단가는 과거 계획 가정 |
| B 실행·검증 담당 | 위 실제 입력을 현재 소비자에 결합하고 원 EVD/RES/RPT·반출·복원/재판정·소유 정리 확인 | 실제 GPU/TP2 결과는 아직 없음. 현재 기록의 독립 FAIL/UNKNOWN 유지 |

새 지시서의 lab DONE **2개/잔여41개**, 기존 한정 마감 **25개/제품 증거 보완18개**, 이전 실제 로컬 추가 관측 **14 ID**는 서로 다른 축으로 유지한다. 이번 소비자 코드·CPU 시험·준비 파일로 lab DONE이나 전체 질문 완료 수를 늘리지 않는다. 기준 시점의 등록·반환 대조는 [B1/B2 수신 표](../b12-readiness-20261108.md), 소비자별 인터페이스는 [수명주기·관측·플랫폼 인계](../preparation/B12_COMMON_INPUT_CONSUMPTION.md)와 [요청·공급물 인계](../../b-work/B12_S5_CONSUMERS_20261108.md)를 따른다. 원 로그·DB는 각 세션의 로컬 고정 경로에 보존하며 PR에는 코드·결과·정확한 SHA/참조를 반영한다.
