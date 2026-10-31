# 선택 컨테이너 process의 기존 제품 연결

A2 확정 `c4fdbe4`의 전용5파일 bytes를 보존하고 기존 `container_config:kubernetes`는
PLT01의 `collect_kubernetes_process_permissions`를 소비한다. 새
`credential_exposure:kubernetes`는 A2의 `KubernetesCredentialExposureExecutor`를
등록한다. 기존 SUP04 API-only 경로와 PACK31·기존 정책/판정은 유지한다.

두 모드는 기존 정확 ENV/target·모델 별칭·엔진/프로브 전후 세대와 고정
`process_snapshot()`/`normal_requests()`를 사용한다. 설정은 기존 guard.toml의
실행기 section(`container_config` 또는 `credential_exposure`)에서 target을 지정하고
`[executors]`의 해당 구현을 `kubernetes`로 선택한다. 임의 JSON capture를 import해
측정 생산자로 채택하는 경로나 별도 CPU 제품은 없다.

공통 dispatcher는 PLT01의 permission_scan_complete 관문이 있는 정의와 필수 정책
타입을 I/O 전에 검증한다. 기존 core의 정책 타입 검사를 재사용하며 금지/허용 값을
생성하지 않는다. target hash가 준비 시점과 달라지면 원격 수집 전에 거부한다.

PLT01은 신원이 안정된 선택 컨테이너 PID의 알려진 UID/cap 사실만 기존 판정으로
보낸다. 알려진 root/cap 위반은 이후 정상 대조의 거부·누락·교체·첨부/정리 실패에
지워지지 않는다. root를 못 봤다고 전체 worker가 non-root라고 하지 않으며 process/
permission 완전성은 false다. 실제 process mount·device·host 격리·무해 접근은 미검증이다.

PLT04는 일반 process 환경 이름과 후보 lstat를 worker 환경이나 자격 접근으로
올리지 않는다. candidate 결과는 unknown이며 명시 빈 목록도 후보를 시험하지 않은
제한 범위다. 정상 completions 응답은 기존 구조 검증에만 사용한다. 수집 범위가
불완전하면 기존 INV11/판정의 PASS 상한을 유지한다.

최종 EVD 출처는 기존 draft·공통 live 기록·전송 경계의 가장 약한 출처를 따른다.
lab=false나 기본 runner 슬롯만으로 EXAMPLE ENV/관측을 MEASURED로 승격하지 않는다.
첨부 실패의 ABORTED/DECLARED 상한과 독립 FAIL도 유지한다. 부모 정리 오류는 기존
execution.cleanup_errors와 CLI exit1로 구분하며 그 사실만으로 앞선 응답을 지우지 않는다.

선정→run→EVD/RES/RPT→verify→반출/복원·같은 시각 저장팩 재판정을 기존 CLI로
검증한다. source/wheel/격리 설치판의 일치와 실제 Kubernetes/GPU의 새 소유 세대
실증은 별도다. source-bound model worker·자격 유효성·native0.32·TP2 및 전체 A 범위
완료를 이 부분 연결로 표시하지 않는다.
