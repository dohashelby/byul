# B03 실행 순서·공유 경계 — 준비 판정

이 문서는 새 GPU 실행 명령이나 현재 SUP16의 추가 시작 조건이 아니다. B03 정적 40개/7분류와 S5 v2 실제 입력 준비 결과를 바탕으로 후속 실행 순서를 정한다. CPU 계약 수를 실험·제품 완료 수로 합산하지 않는다. PACK33 DRAFT, 기존 FAIL·UNKNOWN·EXAMPLE·Ref와 LAB10/33은 유지한다.

## 현재 준비 근거

S5 기준 source는 main `7a985052`를 포함한 `4674762373feaca9097c6f4ad09a1ebb4907ed77`이다. source-freeze SHA `740a91d332c473c73b51b341efdc13f8bef677c044f8f2f7cd39eb6b3680e3bd`, public 정적 요약과 정확 40행 정의 SHA를 사용한다. 원 SUP16 실제 source `2cb01e9f`/466파일과 섞지 않는다.

선정 SUP-03·08, REQ-08·09·10·07, SUP-13의 실제 생성 CLI가 normal/challenge JSON·TOML 설정을 만들었다. 추가 CPU에서 `guard.cli._config → application.run_config → registry` 7개를 직접 소비했다. 이는 RunConfig 구성·registry factory 생성이며 실제 application.run·HTTP 호출은 0이다. 기존 6개 정책은 EXAMPLE/DRAFT 범위에서 수용됐고 SUP-13은 필수 정책 누락 진단이다. REQ-07·SUP-13의 실제 backend는 현 SUP-03 전용 Kubernetes factory와 호환되지 않는다. 이를 SUP-03 profile로 바꾸거나 target이 준비됐다고 표시하지 않는다.

실제 OPT config의 vocab_size는 50272다. 고정 image/model/config는 선택 후보이며 모든 40개에서 호환된다는 뜻이 아니다. 현재 runtime UID·boot·Pod/container·PID/start·mount·FD·target/prepared는 미입력이다.

## 실행 순서

| 순서 | 범위 | 먼저 끝낼 작업 | 실행·공유 판정 |
| --- | --- | --- | --- |
| 병행 CPU | 선정 7개 입력, 나머지 33개 요구 필드 | S5 입력 소비·S6 독립 검토, 항목별 정책·target/prepared 결합 | SUP16·AWS·PR CI를 기다리지 않고 자기 출력에서 진행 |
| 1 | SUP-08, REQ-08·09·10 | 각 실제 backend 지원·정상/도전 요청과 수집 기록 대응, current runtime·실제 Ready 고정 | 같은 기존 GPU 인스턴스의 순차 사용 후보. 같은 engine 공유는 아래 조건 충족 후에만 허용 |
| 2 | SUP-03 | 승인 template·읽기 전용 공급물·실제 선택/소비 연결, 정상 대조 | template/mount/config 경계가 달라지면 별도 engine·준비 세대. 순서 1과 무조건 공유하지 않음 |
| 별도 준비 | REQ-07, SUP-13 | 정확 backend/target, SUP-13 forbidden_template_attributes 반환 | 다른 항목을 막지 않음. 호환 factory가 없으면 실제 실행 미준비로 남김 |
| 3 | OBS-02·03, REQ-12 및 플랫폼 5개 | 필요한 실제 관측 생산자·주체·권한·환경 범위와 독립 정책 입력 | 수동 관측은 해당 시험에 귀속. profiler·추가 부하·권한 변경은 독립 대조/정리 필요 |
| 4 | 공급·기동 12개, 수명주기 10개 | 항목별 시작·교체·실패·종료/회복 입력과 정상 대조 | 변경/장애 시험별 engine·세대·소유 자원 분리. 현재 SUP16은 S2 단독 경로로 중복 실행하지 않음 |
| 별도 정본 | SUP-04, PLT-09, PLT-08 | 새 정본팩·정책·대상 반환 후 정확 Ref로 준비 | 과거 LAB 또는 제외 판정으로 새 정본 실측 완료를 만들지 않음 |

관측·플랫폼 범위와 공급·수명주기 범위의 준비는 앞 순서의 GPU 성공을 기다리지 않는다. 표는 GPU를 사용할 때의 충돌 회피 순서다. source·target·정상 대조가 고정된 독립 항목은 준비 완료 순서로 운영자가 배치할 수 있다.

## 상태별 공유 조건

| 상태·자원 | 공유 조건 | 분리해야 하는 경우 |
| --- | --- | --- |
| 기존 GPU 인스턴스 | S2 단일 운영자·기존 승인 범위·현재 소유 프로세스와 종료 확인 | 동시 변경 시험/복구와 충돌할 때. 새 인스턴스 생성으로 해결하지 않음 |
| 모델 원본 | 같은 inventory/config SHA의 불변 읽기 전용 원본 | 누락·교체·부분 파일 도전은 전용 소유 사본 |
| engine/worker | 동일 source/image/model/config/정책 유효범위, 같은 실제 신원, 순차 요청, 이전 요청·자식 종료, 상태 변화 없음이 관측된 경우 | restart, 교체, 초기화 실패, worker 장애, 취소/장애 후 미회복, template/mount/config 변경 |
| cache | 불변 입력과 효과를 확인하고 시험 목적이 cache 상태를 요구하지 않을 때 | cache 초기/오염/교체 자체가 시험 대상이거나 격리 미확인 |
| namespace·mount·FD·IPC | 해당 시험의 정확 신원과 소유 범위를 별도로 기록 | 권한·접근 주체·mount/subPath·FD·IPC 실패를 시험할 때 |
| DB·EVD/RES·출력 | 시험별 run/request/attempt·source·prepared·target·관측 연결을 보존 | 다른 시험의 결과·Ref·실패 원문을 덮어쓰기/정상 대조로 재사용하지 않음 |

정상→도전→정상 복구의 각 요청·세대를 기록한다. HTTP Ready는 engine 추론·worker·capture 완료와 별개다. 같은 이미지나 같은 모델이라는 이유만으로 공유를 확정하지 않는다. 실제 state-independence는 아직 미검증이며 기본값은 engine 공유 미확정이다.

## 시간·담당·반환

B03 GPU 소요시간과 전체 상한은 미확정(null)이다. 사전검증의 runner elapsed 2.008초와 추가 CLI 소비 1.769초를 GPU 시간으로 환산하지 않는다. runner elapsed와 기록된 UTC 시작/종료 차이는 별개다. 항목별 기존 기한·collector 제한·정리 예약과 실제 시작 앵커가 연결된 뒤에만 실행 묶음의 시간을 산정한다. 기존 SUP16의 2시간·phase·전송900초·정지관측180초는 원 운영 경로의 값이며 B03 전체로 복사하지 않는다. 새 예산 재확인을 현재 SUP16 재개의 gate로 추가하지 않는다.

B04의 계획 슬롯 900/600/900초(합2400초)는 TP2 준비표의 값이다. 정책 유효창·수집기 제한·native hold·실제 부모 기한과 구분하며 B03 시간 또는 TP2 실행 가능 보장으로 사용하지 않는다.

S5는 정확 입력 생성·CPU 사전검증, S2는 실제 운영과 current 신원·request/collection 대응, S6는 새 변경과 증거 독립 검토, root는 순서·공유 판정·PR 단일 통합을 맡는다. 정책 권한자는 SUP-13 등 누락 정책값을 반환한다. A는 실제 필요한 공통 producer/자격·정본팩·TP2 반환을 맡는다. 현재 없는 정책·PACK34 Ref·현재 신원을 만들어 빈칸을 채우지 않는다.

이 문서 작성은 제품 코드 변경·GPU 실행·새 EVD/RES/RPT 발급이 아니다. S5 고정본·S4 B04 준비와 각각 독립 검토 후 공개 범위를 PR에 통합한다.
