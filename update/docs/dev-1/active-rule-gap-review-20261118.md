# 활성 점검의 빈 판정 규칙 검토

2026-11-18에 origin/main을 fetch한 결과는 `86d2e957`였다. 활성 PACK-BASELINE 39의
manifest에 등록된 77개 YAML 중 retired 8개를 제외한 활성 69개 ID를 확인했다. 명시적으로 빈 판정 축은
5개 점검의 9개 축이며 요청 목록과 차이가 없다.

| YAML | 축 | 상태 · 분류 | 판정에 필요한 근거 |
|---|---|---|---|
| [SUP-03](../../packs/PACK-BASELINE/checks/SUP-03.yaml) | 보안 | 관측 필요 · 실제 관측 증거 부족 | 승인 목록과 결합한 실제 소비 목록 및 범위 완결, 또는 승인 밖 대상의 독립적인 사용 입증이 필요하다. |
| [SUP-03](../../packs/PACK-BASELINE/checks/SUP-03.yaml) | 기능 | 관측 필요 · 실제 관측 증거 부족 | 승인된 필수 공급물을 실제 사용하여 정상 기동과 요청 처리가 이뤄졌다는 결합 관측이 필요하다. |
| [SUP-13](../../packs/PACK-BASELINE/checks/SUP-13.yaml) | 기능 | 관측 필요 · 실제 관측 증거 부족 | template_sandbox의 검토된 고정 템플릿은 일부 정상 렌더 대조만 한다. 해당 소비 범위와 허용 템플릿의 정상 렌더 및 결과를 연결해야 한다. |
| [SUP-13](../../packs/PACK-BASELINE/checks/SUP-13.yaml) | 성능 | 관측 필요 · 실제 관측 증거 부족 | 점검이 처리 상한을 요구한다. 기업별 승인 예산과 renderer에 귀속된 시간·자원 측정이 필요하다. |
| [PLT-02](../../packs/PACK-BASELINE/checks/PLT-02.yaml) | 기능 | 관측 필요 · 실제 관측 증거 부족 | unix-ipc-access-v1은 비허용 UID의 Unix 소켓 연결 일부를 관측한다. 승인 프로세스의 채널별 정상 IPC 소비와 TP 요청 성공의 결합이 필요하다. |
| [PLT-07](../../packs/PACK-BASELINE/checks/PLT-07.yaml) | 기능 | 관측 필요 · 실제 관측 증거 부족 | 공급물 취득·서빙·관측의 필요한 승인 송신을 유효 정책 및 최종 연결 결과와 결합해야 한다. |
| [PLT-07](../../packs/PACK-BASELINE/checks/PLT-07.yaml) | 성능 | 범위 밖 · 현 정의의 평가 범위 밖 | 정책과 수락 기준은 목적지·작업의 허용 여부와 정상 송신 유지다. 지연이나 처리량의 독립 수락 기준이 없다. 기존 UNKNOWN을 유지한다. |
| [PLT-08](../../packs/PACK-BASELINE/checks/PLT-08.yaml) | 기능 | 관측 필요 · 실제 관측 증거 부족 | 소유 API의 단일 HTTP 응답으로 배포 경로의 승인 요청·스트림 또는 승인 rank 간 TP 정상 동작을 확정할 수 없다. |
| [PLT-08](../../packs/PACK-BASELINE/checks/PLT-08.yaml) | 성능 | 범위 밖 · 현 정의의 평가 범위 밖 | 정의는 구간 보호와 정상 API·TP 동작을 평가한다. 재사용하는 LIF-04 스트림의 시간 관측에 PLT-08 성능 수락 기준이 없다. 기존 UNKNOWN을 유지한다. |

판정 규칙 구현 누락으로 분류한 축은 0개다. 기능 미선언 46개와 성능 미선언 66개는
명시적 빈 규칙과 구분했다. 예를 들어 SUP-03의 승인된 소비와 PLT-02의 IPC 접근
판정에는 독립 성능 기준이 없으며, SUP-08의 코드 판본 선택에도 성능 수락 기준이 없다.
SUP-03·PLT-02·SUP-08의 성능 규칙은 추가하지 않았다. 미선언은 구현 의도를
확인하기 전에는 적용 제외 판정으로 해석할 수 없다. 미선언 기능 축 중 기능 표본 또는
기능 전용 필수 관측을 선언한 점검은 없었다. 미선언 성능 축에서 자원 관측 이름이
검색된 PLT-06의 `worker_shared_resources`는 vLLM worker가 공유하는 자원의
접근 및 격리 범위를 확인하는 보안 관측이며, 성능 예산이나 측정값이 아니다.

## SUP-03 관측의 경계

| 수준 | 확인한 범위 |
|---|---|
| 존재·식별 | 제품 프로파일의 고정 템플릿 snapshot은 현재 bytes 해시, 소유권, 읽기 전용 마운트, 기동 인자 및 전후 인스턴스를 결합한다. 안정된 식별값을 SUP-03의 독립 승인 목록과 비교한다. |
| 열기 | 이 제품 프로파일은 엔진 worker의 file-open을 직접 기록하지 않는다. 별도 Python 계측은 고정 소스의 선택된 로더가 연 FD를 관측한다. 파일 열기만으로 읽기나 소비를 판정할 수 없다. |
| 읽기 | 별도 k3d 기록은 Python config·tokenizer 설정·template 로더의 FD 읽기, parser 입력 해시 및 성공한 반환을 연결했다. Rust tokenizer의 vocab/merges와 추가 공급원 전체 읽기 귀속은 남아 있다. |
| 실제 사용 | 별도 boot 실측은 config 내용과 worker HF config 일치, cached tokenizer 복사 및 요청 renderer 객체 계보를 일부 확인했다. 제품 프로파일의 /tokenize와 추론 prompt_token_ids 대조는 API 입력 일치 관측이다. worker의 로드 시점 bytes·캐시·선택 공급원 전체 소비를 귀속하지 못한다. |

[제품 실행기](../../src/guard/executors/template_supply_approval.py)는 YAML의 8개 키를 emit한다.
`selected_template_sha256`와 `selected_template_approved`는 안정된 snapshot에서만 수집된다.
`template_identity_verified`와 `input_projection_exact_match`는 식별과 입력 일치를 뜻한다.
`template_consumer_verified=false`와 `supply_scope_complete=false`는 현재 구현의 관측 한계다.
`functional_probes_planned` 및 `functional_probe_results`는 단일 정상 요청 표본이며,
계획 키는 항상 emit되며 결과의 수집 표시는 요청을 실제 시도했을 때만 참이다. 비승인 해시가 마운트됐다는 사실만으로 FAIL을 만들 수 없다.

별도 파서와 부분 소비 실측의 출처는 [파서 읽기 기록](sup03-auxiliary-parser.md) 및
[부팅·요청 소비 기록](consumption-links-cpu.md)이다. 이 기록은 현재 제품 실행의
판정 입력에 합쳐지지 않는다. 승인 범위 내 실제 소비가 완결되고 정상 동작까지
결합되면 해당 축의 PASS, 비승인 자료 사용이 독립적으로 입증되면 보안 FAIL의 근거가 된다.
관측 누락·불완전한 범위에서는 UNKNOWN을 유지한다.

팩 YAML·정책·해시·검토 기록은 기존 판본을 유지한다. 판정 규칙을 바꾼 축이
없으므로 신규 규칙의 PASS·FAIL·UNKNOWN 경계 시험은 해당 사항이 없다.


## 보고서 확인

11월 14일 보존 RPT와 Guard SQLite를 읽기 전용으로 재생했다. RPT의 PLAN→ENV와
RES→EVD를 ID·판본·해시로 대조했다. 보안 판정은 기준 vLLM 0.7.0 PASS, 후보
vLLM 0.30.0 FAIL이다. 후보의 EVD에서 실제 모듈 로드 기록과 저장 모듈의
해시 일치를 확인했다. 기준에는 동일 종류의 로드 근거가 없어 판정과 근거 공백을 표시했다.
이 검증은 과거 GPU 측정의 화면 재생이며 이번 작업에서 엔진을 새로 기동하지 않았다.

플랫폼 생성 보고서는 실행 객체 저장소를 사용한다. 외부 RPT는 설정한
`UPDATE_REPORTS_DIR` 아래의 `objects/store.sqlite`에서 정확한 참조를 확인한다.
RPT만 있거나 해당 판본의 참조를 확인할 수 없는 경우에 화면은 기록된 판정을 표시하며
확인되지 않은 엔진 판본과 한국어 근거를 채우지 않는다.
