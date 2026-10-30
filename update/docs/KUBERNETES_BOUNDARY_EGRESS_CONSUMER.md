# PLT07 Kubernetes 전용 소비 v1

이 묶음은 명시 `all_processes_in_container_uid_netns` 정책의 한 TCP connect 사건만 소비한다. 실제 소비 위치는 기존 PLT07 `forbidden_egress_successes`, `egress_trials`, `egress_scope_complete=false`와 기존 rule_table의 독립 FAIL이다. 전체5분기·취득/SDK·cache·usage·telemetry·worker-only·기업 인가·전체 egress PASS는 미완료다.

A1 확정 `25963372389d58ab4caf517436af6fc3ae24c191`의 `KubernetesHTTP.egress_snapshot(policy_values, *, receiver_target, actor_spec, connect_timeout, total_timeout)`을 사용한다. capability profile은 `kubernetes-owned-explicit-egress-v1`, schema는 `guard-kubernetes-egress-snapshot/1`이다. lab=true/vLLM0.31/CPython3.12 EXAMPLE 경계를 유지한다. source/program/helper/file-reader는 검토된 literal pin과 대조하고 ENV/target/model mapping·정책13키·명시 actor uid/gid/groups·독립 receiver binding을 그대로 소비한다. native0.32/production 관문을 열지 않는다.

공개 전용 함수는 `validate_inputs(check, values, *, actor_spec, connect_timeout, total_timeout)`과 `collect_kubernetes_boundary_egress(ctx, *, receiver_target, actor_spec, connect_timeout, total_timeout)`이다. 반환은 기존 HTTP 전용 소비와 같은 observations/normal/challenges/summary의4튜플이다. 공통 wrapper는 명시 receiver target을 제한된 strict JSON으로 읽어 전달하고, `ctx._kubernetes_egress_cleanup_errors`와 summary/report의 기술 공백을 실행 상태와 cleanup에 반영해야 한다. 기술 공백을 ABORTED로 기록하더라도 이미 확인된 독립 FAIL은 유지한다.

전용 모듈은 기존 `boundary_egress._sender/_records/_when`의 엄격한 parser와 connect/receipt 의미를 재사용한다. 원본 boundary_egress.py·egress_probe.py·egress_receiver.py bytes와 Docker 실행기·POL11키는 변경하지 않는다. 공통은 boundary_egress.py 전체를 고정하므로 분석 함수를 추출하기 위해 해당 원본을 바꾸지도 않는다. 공통 kubectl·selector·binding·transport 수집기는 복제하지 않는다.

검증할 사건에는 source/receiver API 전후 세대, sender helper의 전후 PID/start/UID4/GID4/groups/namespace/boot/time-domain 일치, packet의 실제 sender 신원 일치, receiver helper와 실제 receiver PID 구분, 전후 receiver command hash/listener/process와 startup 신원, 수신 파일 device/inode 및 행 prefix, marker SHA와 양방향 endpoint·32bytes receipt가 필요하다. receiver metadata reader는 매 호출 새 프로세스이므로 두 reader의 PID를 같다고 요구하지 않는다. 각 호출 내부의 신원과 호출 간 namespace/boot/time-domain은 확인한다. source와 receiver의 network namespace를 같게 만들지 않는다.

공통 envelope의 file은 이미 검증된 typed rows/file identity/capture SHA/bytes다. raw JSONL로 다시 디코딩하거나 정렬된 typed rows에서 원본 bytes/hash를 만들어 승인하지 않는다. 정확 필드·행 한도·개별 기존 parser·원본 nofollow/file-source pin을 대조한다. raw file/marker/환경/argv/요청/응답/임의 예외 원문은 저장하지 않는다.

공통 외곽 시각은 초 정밀도이므로 전용 소비기는 공개 메서드 호출 직전/직후의 aware 고정밀 operator window를 기록한다. connected→marker sent→독립 receipt가 그 창과 명시 POL 유효창 안에 순서대로 존재해야 한다. 시각을 반올림하거나 정책창을 넓히지 않는다. 실제 노드 clock authority/오차는 이 EXAMPLE 입력으로 증명하지 않는다.

후속 ACK mismatch/timeout, sender 최종 clock 기록 누락, 수신 pipe 종료·부모 cleanup·report 실패는 필수 사건 근거가 남아 있으면 독립 FAIL을 지우지 않는다. 필요한 receiver receipt/전후 writer·actor 자격·API 세대·source/provenance가 없거나 교체되면 UNKNOWN이다. 거부/timeout은 명시 접속 시도의 관측이며 기업 차단 PASS나 정상 업무 대조가 아니다. normal은 항상 attempted=false/succeeded=null이다.

정식 `boundary_egress:kubernetes` 등록·별도 명시팩/POL13키·검토 snapshot pin·실제 CLI EVD/RES/RPT→verify→반출/복원→저장팩 정확 Ref는 A1 소유 후속 단계다. helper/CPU/설치 검증으로 이 경계를 마감하지 않는다. A2는 확정 전용 커밋·원본3파일 hash·직접/인접/설치 근거를 인계하고, A1 통합 확정 후 제품 경로와 정확 Ref를 소비 검증한다.

원GPU창은 종료됐고 추가 자동 Start는0이다. 실제 재개에는 새 시간 승인·현재 A1 소유/실행 차례·fresh ENV/source target/receiver target/POL/actor·quota/잔여 총비용·복구/정리 책임이 필요하다. CI5는 정확 HEAD 성공 전 미확인이다. 부모A39/56/4·A2 20/29/2와 기존 제한 실증·Ref를 보존한다.

직접 회귀62사례를 고정 소스에서 통과했다. 기존 공통·Docker/판정·probe 인접208사례는 원본 bytes가 같은 최초 실행의 통과 근거를 재사용한다. 최초8실패는 공통 시험 fixture가 sender IP와 API Pod IP를 별개로 선언한 데서 발생했고, 전용 fixture의 Pod IP·URL·연결된 target SHA를 일관되게 보수했다. 중간 URL 불일치60실패도 보존했으며 제품 관문·판정 기대값·skip을 완화하지 않았다. 이를 단일 최종 소스의 전체 suite 통과로 합산하지 않는다.
