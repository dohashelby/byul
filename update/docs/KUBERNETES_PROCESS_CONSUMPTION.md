# PLT01/04의 공통 process 입력 소비

전용 분석은 `KubernetesHTTP.process_snapshot(candidate_paths=None)`의 live 결과만
소비한다. 파일·딕셔너리·비슷한 속성을 가진 객체를 실제 process 근거로 받지 않는다.
고정 프로그램·정확 ENV/target/run/Pod/컨테이너 세대·source bound·오류 정제는
A-1 공통 경계의 책임이다. 전용 소비는 그 판본의 공개 입력을 재사용하며 Docker
형식으로 변환하거나 kubectl/프로세스 reader를 복제하지 않는다.

현재 `kubernetes-owned-process-observations-v1`은 수집기 자신을 제외한 선택
컨테이너 PID namespace의 metadata다. 수집기 UID와 보고된 process UID는 다른
주체다. PID 목록 안정성·개별 PID/start 안정성·필드 수집 완전성도 구분한다.
이 판본의 worker 귀속과 전체 worker inventory는 미확인이다.

## 현재 관측의 의미

PLT01은 전후 owner가 확인되고 capture 주체가 안정된 구간에서, 개별 PID/start가
안정된 process의 유효 UID와 capability를 소비한다. 확인한 UID 0이나 허용 밖
capability는 기존 양성 위반 관측으로 남길 수 있다. 다른 process의 후속 필드
누락이 이 사실을 빈 목록으로 바꾸지 않는다. 반대로 확인한 root가 없다는 사실을
모든 worker가 non-root라는 증명으로 쓰지 않는다.

전체 권한 scan과 worker scan은 미완료다. Pod 선언으로 실제 mount·device 권한,
privileged/host namespace 집행이나 실행 가능한 쓰기 영역을 채우지 않는다.
무해 파일·장치 접근과 sentinel도 이 reader가 수행하지 않는다. 기존 정책과
PLT01의 전체 PASS 관문은 유지한다.

PLT04는 컨테이너가 보고한 환경 이름과 명시 경로의 lstat metadata를 첨부에
구분해 남긴다. worker 귀속이 없는 이름을 `worker_env_names`로, Pod volumeMount를
현재 process mount 위반으로 채택하지 않는다. lstat 부재·mode는 worker의
mount/root/LSM 주체나 읽기 권한을 입증하지 않으므로 실제 자격 후보 결과는
`unknown`이다. 명시적인 빈 후보 목록은 경로를 시험하지 않은 제한 범위이며,
전체 worker/자격 검증 완료를 뜻하지 않는다. 값·파일 내용·자격 유효성은 수집하지 않는다.

## 정상 대조와 연결 단계

두 CheckDef의 기존 정상 대조 `POST /v1/completions`는 고정 공통 커밋
`f333fff4ec4eba3b11178ec50d326dc1d3b76457`의 `normal_requests`로 실행한다.
기존 `functional_requests`와 `functional_results`의 정확 모델 별칭·구조 검증을
재사용한다. 공통 전송이 정확 논리 ID와 wire ID만 대응시키며 경로·본문은
유지한다. 정상 성공·거부·응답 누락·불완전·모델 불일치를 기존 의미로 구분한다.
후속 정상 대조의 세대 교체·정리 실패가 앞서 결합된 UID/capability 사실이나
독립 FAIL을 지우지 않는다. 응답 본문은 검증 동안만 사용하고 저장하지 않는다.

PLT01의 기존 `container_config:kubernetes` 공통 dispatcher 분기 추가와
PLT04의 `credential_exposure:kubernetes` 등록은 A-1의 변경이다. PLT04 전용 클래스는
기존 공통 Kubernetes 실행 골격을 상속하고 수집/분석만 제공한다. 전용 CPU 시험의
등록 반영안은 정식 공통 등록 수락과 구분한다. 공통 dispatcher·registry 파일은
A-2가 직접 변경하지 않는다.

같은 select/run/judge/report/verify와 반출·복원·저장 팩의 정확 Ref 재판정을
사용한다. runner 주입은 target의 `lab=false` 선언에도 EXAMPLE이다. 공통 process
기록의 출처도 최종 증거에 적용해 EXAMPLE ENV가 MEASURED로 승격되지 않게 한다.
PLT01 공통 dispatcher에도 이 출처 제한을 반영하도록 A-1에 인계한다. 정적 설정의
재사용과 새 실제 수집을 구분하며, metadata 소비 성공을 PLT01/04 전체 보안 완료로
집계하지 않는다. 실제 source/model worker 분류·접근과 전체 범위는 후속 계약 및
실증이 필요하며 정상 대조의 CPU 주입 성공도 실제 추론 실행과 구분한다.
