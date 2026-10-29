# 자격·마운트 수집의 부분 실패

`credential_exposure/0.5.2`는 기존 PLT-04의 `forbidden_mount_violations`
관측과 판정 계약을 사용한다. 유효한 bind/volume 목적지와 명시 금지 경로의
포함 관계가 확인되면, 다른 마운트 행의 형식 오류 때문에 이 위반을 버리지 않는다.
부분 목록에 위반이 없으면 빈 안전 목록으로 기록하지 않고 미수집으로 남긴다.

`MOUNT_INVENTORY_INCOMPLETE`는 목록의 누락·잘못된 행을 표시한다. 이 경우
자격 후보의 부재·접근 거부를 확정하지 않는다. 후보의 가림 판단과 metadata
수집에는 여전히 완전한 마운트 목록과 기존 주체·세대 확인이 필요하다.
목적지의 NUL·비정규 경로는 유효한 마운트로 사용하지 않는다.

정상 대조 요청 준비·수집이 실패해도 먼저 확보한 마운트·환경변수 이름 관측은
보존한다. 요청 준비가 실패하면 정상 대조의 `attempted`는 false이며, 결과는
`no_response`다. 예외 원문은 기록하지 않는다. 후속 첨부 저장 실패에서도 기존
공통 증거 경계가 보존한 관측과 독립 FAIL을 유지한다.

## 현재 엔진 소스의 신원과 세대

지원하는 모든 새 live 수집은 [A-1의 공통 계약](A1_TARGET_BINDING.md)의
`engine_identity`를 소비한다. seal이 유효한 ENV와 정확히 일치하는 target의
`environment_ref`, 명시적인 `run_id`, `engine.container`, ENV의 pin 이미지가
필요하다. 이전 target의 누락값을 현재 ENV에서 자동으로 채우지 않는다.
처음부터 입력이 다르거나 실제 Config.Image·guard.run·컨테이너 신원이 맞지
않으면 고정 오류 `CREDENTIAL_TARGET_UNVERIFIED`로 수집을 중단한다.
안정된 잘못된 instance의 metadata를 자격 부재나 접근 거부로 채택하지 않는다.

최초 검증한 owner tuple을 한 세대의 기준으로 두고 마운트·프로세스 환경·자격
후보·정상 대조 구간마다 전후 신원을 대조한다. 마운트 목록 자체의 신원도 같은
tuple과 일치해야 한다. 뒤 구간에서 restart·container replacement·image ID
변화를 발견하면 해당 구간의 이름/후보/정상 응답을 채택하지 않는다. 앞서 별도
구간에서 결합된 마운트 위반과 환경변수 이름은 지우지 않는다. 완전하지 않은
마운트 목록의 유효한 금지 행을 보존하는 기존 계약도 유지한다.

마운트 snapshot과 프로세스·후보 reader는 최초 검증한 불변 container ID로
호출한다. 관측 사이에 이름이 잠시 다른 컨테이너로 재지정됐다 돌아와도 다른
세대의 metadata를 읽지 않도록 한다. 전후 target 이름/소유 신원 대조도 유지한다.

첨부의 `source_binding`은 `docker-engine-source-intervals/1`, 정확 ENV Ref,
최초 owner tuple의 SHA-256, 구간별 신원 확인 여부만 기록한다. inspect 전체,
Config.Env·StartedAt 원문·자격 내용·예외 원문은 저장하지 않는다. 입력 검증
실패·관측 누락과 단순 빈 안전 목록을 구분하며 기업 정책·CheckDef·판정·PACK31은
변경하지 않는다. CPU fixture는 명시적인 EXAMPLE ENV/pin/run으로 만들고,
나중 inventory 오류는 최초 신원 확인 뒤 해당 관측 구간에 주입한다.

저장된 EVD/RES/RPT·정확 Ref의 재판정에는 collector를 호출하지 않는다. 이전 정의의
`exposed_credential_paths`로 새로 수집할 때도 같은 신원·구간 검증과 불변 ID 호출을
적용하고, 원래 다섯 관측 필드명과 마운트 포함 관계의 의미를 유지한다. 이 구 live
경로는 candidate probe를 새로 실행하지 않는다. 필드명을 보존하는 것과 새 live
수집의 source 결합을 생략하는 것은 다르다. 과거 증거에 소급해 신원 확인을
덧붙이지 않는다. Kubernetes의 credential 수집
확장·실제 worker 귀속·자격 사용·생산자 인증·실제 파일/tensor 소비·전 구간
무누락을 입증하지 않으며 PLT-04 전체 완료를 의미하지 않는다.

이는 기존 제한 분기의 결함 보수다. 새 실행기·정책·팩·등록을 추가하지 않는다.
마운트 금지 위반은 실제 자격 파일의 존재·내용·접근·서비스 사용 증명이 아니다.
과거 저장 정의의 `exposed_credential_paths` 경로는 변경하지 않는다.
시험의 Docker·HTTP 대역 결과는 EXAMPLE이며 실제 엔진·worker 집행 실증과 구분한다.

자격 후보 metadata는 기존 bounded `strict_json`으로 읽는다. 주체 확인값·경로·상태
등의 중복 JSON 키, NaN/Infinity 상수, 잘못된 UTF-8·기존 decoder의 중첩 해석 실패는
`CANDIDATE_METADATA_UNAVAILABLE`과 후보 `unknown`으로 남긴다. 모순되는 값의
마지막 항목을 택해 부재·접근 가능·주체 일치를 확정하지 않는다. 이런 패킷 오류도
독립적으로 확보한 금지 마운트·환경변수 이름 위반을 지우지 않는다.

정상 패킷과 기존 schema/profile·후보 상태의 의미는 그대로다. 중복 키 보수는
0.4.2에서 도입됐으며 0.5.2는 구 필드명을 포함한 새 live 수집 모두에 위 신원·구간
계약의 소비와 불변 ID 호출을 적용한다. 과거 EVD는
고치지 않는다.

## 프로세스 환경 이름과 정상 응답의 결합

환경 이름 reader의 PID 표기는 양의 32비트 PID여야 하며 한 scan에서 중복되지
않아야 한다. 각 프로세스 구간은 자기 완료 표식을 가져야 한다. 잘못된 PID,
누락·중복 표식, 완료 뒤의 자료, 오류 종료는 완전한 환경 목록을 입증하지 않는다.
이 경우 `process_scan_complete=false`와 `PROCESS_ENVIRONMENT_INCOMPLETE`를
기록한다. 이름을 하나도 확인하지 못한 부분 scan은 빈 안전 목록으로 남기지 않는다.
다음 구간이 실패해도 앞서 올바른 PID 구간에서 확인한 이름과 독립 위반은 보존한다.

`PID`, `PIDFILE`, `PID_CONFIG`, `GUARD_READ_SECRET` 같은 유효한 환경 이름은
접두어만 보고 reader의 제어 표식으로 제거하지 않는다. 실제 값·잘못된 행·reader의
오류 문자열은 반환하거나 첨부하지 않는다. 이 구문 검사는 worker 귀속이나
PID/start 안정성을 증명하지 않으며, Kubernetes의 공통 process 생산자가 제공할
대상·세대·주체·구간 계약을 대신하지 않는다.

정상 대조는 요청과 응답의 개수가 같아야 한다. 응답에 요청 ID를 제공하면 순서별
정확한 ID도 같아야 하며, 불일치·일부 누락이 있는 HTTP 200 응답은 `no_response`다.
기존 응답 주입 계약의 ID 없는 순서형 결과는 계속 지원한다. Kubernetes의 고정
공통 생산자는 자체의 버전별 엄격한 ID 결합 계약을 유지한다. 뒤 정상 대조의 상관
실패가 앞선 마운트 금지·프로세스 환경 이름의 독립 FAIL을 지우지 않는다.
기존 CheckDef·정책·판정 및 과거 저장 Ref의 의미는 유지한다.
