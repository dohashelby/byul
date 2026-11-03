# B26 PLT-01·PLT-04 실행기 경계 (2026-11-02)

기준: `origin/main` 2f28942, PLT-01 revision 6 및 PLT-04 revision 5, 기존 `container_config:real|kubernetes`, `credential_exposure:real|kubernetes` 등록 경로. 이 문서는 CPU 계약 보강 기록이며 GPU 실측 결과가 아니다.

## 현재 직접 관측과 판정

| 경로 | 직접 확인하는 것 | 여전히 확인하지 못하는 것 |
| --- | --- | --- |
| Docker PLT-01 | 소유 컨테이너 전후 identity, inspect 선언, 컨테이너 PID의 UID·CapEff, 마운트 대상 sentinel 쓰기 | GPU worker PID/세대·GPU UUID·NVML PID 대응·장치 cgroup 유효 허용·장치 실제 접근·호스트 집행 전체성 |
| Kubernetes PLT-01 | 정확한 transport의 동일 세대 process packet에서 안정 PID·UID·CapEff의 양성 사실 | worker 분류·전체성, 실제 장치/파일 접근, GPU UUID/NVML/cgroup 대응 |
| Docker PLT-04 | 소유 컨테이너의 명시적 금지 mount 겹침, PID 전체 환경변수 **이름** 합집합, 후보 경로 메타데이터 | GPU worker별 환경/자격, provider 전달·동적 갱신, 후보 접근/실사용 |
| Kubernetes PLT-04 | 동일 세대 PID 이름과 후보 lstat 메타데이터, Pod volumeMount 선언(요약 근거에 한정) | worker별 이름/접근·전체 provider, 런타임 마운트 전체성·Secret 실제 읽기 |

Docker `HostConfig.Devices`와 `DeviceRequests`는 선언이다. GPU가 ENV에 명시되면 `devices` 관측과 전체 권한 스캔을 미확인으로 유지하고 원 선언은 요약에만 둔다. 프로세스 UID 0·CapEff 위반, 권한 밖 mount/쓰기 등 독립 양성 사실은 후속 장치·기능 수집 오류와 무관하게 보존한다. Docker GPU의 PID 환경변수 합집합도 `worker_env_names`로 제출하지 않고 요약에 남긴다. CPU 기존 계약의 의미를 이번 GPU 분기에서 변경하지 않았다.

Kubernetes process v1 생산자는 `worker_subject_verified=false`, `worker_inventory_complete=false`, `access_tested=false`를 강제한다. B 소비자는 이 필드들을 참으로 재해석하지 않는다. Pod volumeMount 선언만으로 PLT-04 `forbidden_mount_violations`를 채우면 실제 mount 관측을 요구하던 기존 판정 의미가 넓어질 수 있으므로 보류한다. 선언이 없거나 변하면 부재를 증명하지 못한다. 네트워크 기능 대조와 수집 정리 오류는 이미 확인한 위반을 지우지 않는다.

## A 생산자에게 필요한 추가 계약

PLT-01: 동일 대상 세대의 worker별 PID와 start ticks/namespace 매핑, 실제 UID, cgroup 경로와 장치 허용, NVML GPU UUID별 PID와 PID namespace 대응, 무해한 장치 접근 결과, 전후 identity 및 전체성/미지원 진단이 필요하다. NVML PID만으로 파일 장치 권한을 입증하지 않는다.

PLT-04: 같은 worker 식별/세대와 worker 목록 전체성, worker별 환경 이름, 후보 경로의 **동일 주체** 접근 결과, provider 종류·자격 식별자(원문 제외)·수신자·전달 세대, 동적 provider와 파일/상속 경로의 범위/미지원 진단이 필요하다. 이름·lstat·Pod 선언만으로 접근이나 전달을 확정하지 않는다. 기존 v1 packet에 무단 필드를 추가하면 decoder가 거부하므로 새 명시적 profile/버전 계약이 필요하다.

## 적용 사례 및 검증 범위

정상 GPU 선언만 있는 경우 UNKNOWN, 독립 root/CapEff/쓰기/금지 mount 위반은 FAIL 후보로 남는다. 전후 세대 교체·지원하지 않는 worker/provider 범위는 UNKNOWN이다. 후속 기능 수집이나 정리 오류가 선행 양성 위반을 지우지 않는다. 실제 GPU 환경·정책 허용표·무해 장치/자격 canary·A 신규 생산자 계약이 준비되면 같은 세대/주체의 정상·위반·교체·수집 실패를 실측해야 한다. CPU fixture는 EXAMPLE이며 실측으로 승격하지 않는다.

### 제안할 A→B record (구현 대기, 승인된 스키마 아님)

기존 `process_snapshot(candidate_paths)`와 동일한 정확한 `KubernetesHTTP` 인스턴스/target hash를 통해 전달한다. 새 profile·schema·producer program SHA-256을 명시하고 v1의 미지원 필드를 참으로 재활용하지 않는다. B는 profile/schema/source hash·target hash·원격 Pod UID/container ID/restart count·전후 동일성·시간 범위·필수 필드 집합을 확인한 뒤에만 해당 사실을 소비한다. 추가 필드, 알 수 없는 enum, 누락, 형식 오류, 교체, 불완전 캡처는 명시적 미지원/UNKNOWN으로 처리한다. 오류의 원문·비밀값은 EVD로 전달하지 않는다.

| 입력/출력 필드 | 생산자 증거 출처 및 결합 조건 | 미지원 조건 |
| --- | --- | --- |
| `source_generation` (Pod UID, container ID, restart count, image digest, 수집 시작/종료) | Kubernetes API 전후 조회 + 실제 exec 대상 동일성 | 전후 세대 차이·소유 불명 |
| `workers[]` (PID, start ticks, PID/mount namespace inode, UID, 역할/그 근거, cgroup 경로) | 컨테이너 PID 상태·/proc start ticks 및 역할 확인 근거를 같은 세대에 연결. collector PID 제외 | 역할 불명·PID 재사용·일부 worker 누락 |
| `gpu_bindings[]` (GPU UUID, NVML PID, PID namespace 대응, 실제 장치 노드/major:minor) | 호스트/컨테이너 NVML 조회와 동일 시점 worker PID/start ticks·namespace 결합 | NVML 불가, PID 대응 불명, MIG/UUID 불명 |
| `device_access[]` (worker 참조, cgroup 장치 허용/거부, 무해 접근 결과, 대상 노드) | 동일 worker UID/cgroup 주체에서 직접 시행한 읽기 전용/무해 접근과 전후 owner 확인 | DeviceRequests/Devices 선언만 존재, 주체 불일치, probe 정리 오류 |
| `worker_credentials[]` (worker 참조, env 이름, 후보 경로별 접근 `absent/denied/candidate_readable/unknown`, provider 유형·자격 식별 참조·수신자·전달 세대) | 같은 worker의 `/proc` 이름/namespace, 동일 주체 접근, provider 인벤토리/전달 이벤트를 원문 없이 결합 | 부모 env 합집합만 있음, lstat만 있음, provider/동적 갱신 누락 |
| `coverage` (worker·GPU·후보·provider별 complete/unsupported와 정해진 진단 코드) | 실제 총수·수집 범위·세대 전후 동일성을 생산자가 증명 | 일부 누락/불명/시간 구간 미포함 |

기존 PLT-01/04 CheckDef의 관측 이름은 새 생산자에서 온 사실이 실제 질문의 전체 범위를 충족할 때만 채운다. 양성 위반은 확인된 주체/대상별로 보존하되 미수집 범위의 무위반을 추론하지 않는다. 새 record가 미지원이면 기존 v1 경로로 안전하게 UNKNOWN을 유지하고 GPU 완료 표시를 하지 않는다.

## B 소유 직접 보조 수집 계약

`KubernetesHTTP`의 선정 엔진 Pod에 대해 `_verify(capture_snapshot=True)`를 전후로 수행하고, 같은 transport의 deadline·bounded `_call`로 고정 Python 프로그램의 **JSON 결과만** 받는 기존 경로가 있다. B `container_config.py`와 `credential_exposure.py`의 기존 executor 내부에서 이 경로를 호출할 수 있으며 새 executor나 외부 조정 계층은 필요 없다. 다만 `_verify`/`_call`은 현재 내부 메서드이므로, 독립 B 코드가 사용한다면 A와 그 호출 계약·소스 고정 범위를 합의해야 한다. A `kubernetes_process` v1 packet은 PID·start ticks·유효 UID/CapEff·mount namespace inode·환경 **이름**과 후보 lstat를 이미 같은 Pod 세대로 보고한다. v1 schema는 추가 필드를 거부한다. B의 추가 사실은 v1 packet을 수정하지 않고 별도 요약 row에 둔다.

직접 수집 가능한 양성 사실은 다음과 같다. v1의 안정 PID 각각에서 전후 `/proc/<pid>/stat` start ticks, `/proc/<pid>/status`의 `NSpid`/UID, `/proc/<pid>/ns/{pid,mnt}` inode, `/proc/<pid>/cgroup`, `/proc/<pid>/fd`의 symlink 대상·`fstat` 장치 major/minor·`fdinfo` 접근 모드만 읽는다. 비밀 내용/환경값은 읽지 않는다. 같은 프로그램에서 NVML이 제공되면 GPU UUID별 compute PID를 조회하고 **kernel NSpid가 ancestor→container 둘 이상의 값으로 제공되어 NVML PID가 첫 값·관측 PID가 마지막 값에 유일하게 일치할 때만** PID/start ticks에 연결한다. 숫자가 같은 단일 PID는 namespace 대응 증명이 아니므로 `unjoined`다. NVML PID가 호스트 namespace로만 보이거나 MIG/UUID·PID 변환이 불명확해도 `unjoined`로 보존한다. 열린 GPU 장치 FD는 해당 프로세스가 실제 장치 handle을 보유했다는 양성 증거지만, 모든 GPU 장치 권한·장치 cgroup allowlist·모델 worker 신원·접근 시도 성공 전체를 증명하지 않는다. 장치 선언 목록이나 다른 프로세스의 GPU 사용을 worker 권한으로 전환하지 않는다.

자격 후보에 대해서는 v1 후보 `device+inode`/mount namespace와 동일 프로세스 FD의 `fstat` 및 `fdinfo` 읽기 가능 모드를 대조할 수 있다. 유일하고 안정된 결합이면 그 프로세스에 **읽기 가능 열린 FD가 전달됨**을 말할 수 있다. 실제 bytes read·provider 토큰 수신·서비스 이용은 말할 수 없다. FD가 없다는 사실은 접근 불가를 증명하지 않는다. v1 환경 이름을 PID별로 유지하며 부모 프로세스 합집합을 worker 이름으로 쓰지 않는다. `/proc` 권한 거부, PID 재사용, 프로세스 교체, namespace 불일치, fdinfo 미지원, NVML 미지원은 각 row의 미지원 진단으로 남긴다. 수집 후 정상 HTTP·정리 실패는 앞선 안정 양성 사실을 지우지 않는다.

PLT-01 기존 관측 `root_uid_present` 및 `capabilities_effective`의 **확인된 양성 위반**은 현재 v1 경로에서 이미 보존한다. 새 열린 장치 FD는 기존 `devices`가 기대하는 Docker 선언 `host:container:cgroupPermissions` 형식과 다르고 전체 장치 목록도 아니므로 현재 rule-table 필드로 넣지 않는다. `permission_scan_complete`, `sentinel_results`, `host_namespaces`도 worker 동일 주체의 무해 접근/호스트 기준 없이 참으로 바꾸지 않는다. PLT-04는 Pod volumeMount 선언 겹침을 기존 explicit forbidden mount 정책 위반으로 삼을 수 있는지 의미 확인이 필요하다. 현재 코드는 이를 보류한다. 열린 후보 FD를 `candidate_readable`, v1 PID별 환경 이름을 `worker_env_names`로 승격하려면 **그 PID가 vLLM 모델 worker임을 독립 확인**해야 한다. 따라서 B 직접 수집은 먼저 별도 row와 원문 없는 artifact로 제한하고, 기존 판정의 PASS 범위를 넓히지 않는다.

최소 A→B 신원 인터페이스는 `/21` sealed native worker generation에서 검증된 `{container-local pid, start_ticks, worker class/generation, model binding}` 목록과 `{namespaceUID, PodUID, containerID, imageID, restartCount, container startedAt, ENV ref, sensor session, source SHA, roster completeness, diagnostic codes}`를 내야 한다. PLT request-binding target과 `/21` lifecycle target은 스키마가 달라 SHA가 같을 수 없으므로 **실제 engine binding tuple**로 결합한다. B가 같은 세대의 v1 PID 및 직접 FD/NVML row를 정확히 합칠 때만 worker별 양성 자격/장치 사실을 기존 관측에 반영할 수 있다. 한 worker 누락·교체·다중 PID 대응이면 해당 범위 UNKNOWN이다. 지금은 이 인터페이스가 없고 실GPU source/계약 시험을 하지 않았으므로 이 결합은 미구현이다.

구현은 `executors/kubernetes_gpu_process_observations.py`의 B 전용 private helper와 기존 두 B Kubernetes collector 호출만 추가했다. 원격 Python 프로그램 bytes SHA-256은 리터럴 `b3ab55f2ff8b34af561b25cf1499e2337d3d8dd0c7b22c2f29bbcfb85ba40322`로 고정한다. `O_PATH`가 설정된 FD는 `O_ACCMODE==O_RDONLY`처럼 보여도 실제 read/write를 할 수 없으므로 `metadata_only=true`, `read_capable=false`, `write_capable=false`로 보고한다. A v1 packet/decoder/flags, 기존 rule-table 관측 이름·값, pack/registry/공통 runtime은 수정하지 않았다. 주입 transport 또는 남은 deadline 18초 미만이면 보조 probe를 생략한다. 원격 probe는 8초 자체 alarm, `nvidia-smi` 2초 deadline, 부모 `_call` 10초 budget·262144 stdout bytes 상한을 둔다. 수집 전후 `_verify`는 namespace UID, Pod UID, container ID/image ID/restart/startedAt/Ready를 재확인한다. 교체·PID 재사용·FD 소멸·NVML PID namespace 불일치·후속 오류는 미결합 또는 보수적 진단으로 남긴다. `worker_subject_verified=false`, `worker_inventory_complete=false`, `device_access_tested=false`, `candidate_read_tested=false`, `provider_scope_verified=false`를 유지한다. 기존 root UID·유효 capability의 독립 양성 위반은 보조 수집 실패와 무관하다. 실GPU/worker/PID-NVML 대응은 아직 시험하지 않았다.
