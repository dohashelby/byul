# B26 PLT-01·PLT-04 실행기 경계

현재 계약 기준은 `3bba3c73a6bee1966b784cd90a9690e3e9d2512a`의 PLT-01 revision 6 및 PLT-04 revision 5, 기존 `container_config:real|kubernetes`, `credential_exposure:real|kubernetes` 등록 경로다. 2026-11-02의 `origin/main` 2f28942 개발 기록에서 바뀐 현재 설명을 반영했으며, 과거 CPU·GPU 자료의 소스나 판정을 바꾸지 않는다. 이후 별도 후보의 변경은 이 문서 범위가 아니다.

## 현재 직접 관측과 판정

| 경로 | 직접 확인하는 것 | 여전히 확인하지 못하는 것 |
| --- | --- | --- |
| Docker PLT-01 | 소유 컨테이너 전후 identity, inspect 선언, 컨테이너 PID의 UID·CapEff, 마운트 대상 sentinel 쓰기 | GPU worker PID/세대·GPU UUID·NVML PID 대응·장치 cgroup 유효 허용·장치 실제 접근·호스트 집행 전체성 |
| Kubernetes PLT-01 | 정확한 transport의 동일 세대 process packet에서 안정 PID·UID·CapEff의 양성 사실 | worker 분류·전체성, 실제 장치/파일 접근, GPU UUID/NVML/cgroup 대응 |
| Docker PLT-04 | 소유 컨테이너의 명시적 금지 mount 겹침, 관측 PID별 환경변수 **이름**과 그 합집합, 후보 경로 메타데이터 | GPU worker 분류·전체성, provider 전달·동적 갱신, 미확인 주체의 후보 접근/실사용 |
| Kubernetes PLT-04 | 동일 세대 PID 이름과 후보 lstat 메타데이터, Pod volumeMount 선언(요약 근거에 한정) | worker별 이름/접근·전체 provider, 런타임 마운트 전체성·Secret 실제 읽기 |

Docker `HostConfig.Devices`와 `DeviceRequests`는 선언이다. GPU가 ENV에 명시되어도 관측한 `devices` 행은 보존하며, PLT-01의 완전성 gate가 있는 경우 알려진 장치 행을 판정에 제공한다. `/dev/mem` 등 기존 정책의 충분한 금지 장치 근거와 root UID·CapEff·금지 mount/쓰기의 독립 위반은 후속 수집·기능·정리 오류로 지우지 않는다. 다만 GPU 유효 접근 및 장치 목록 전체성이 확인된 것은 아니므로 `gpu_effective_device_access_unverified`와 `permission_scan_complete=false`를 유지한다. `declared_devices` 중복 요약은 제거했고 `devices` 원 관측과 선언의 의미는 유지한다.

Docker PLT-04도 GPU라는 이유로 관측한 환경변수 이름을 버리지 않는다. `worker_env_names`에는 기존 주체 수준의 양성 사실을 제공하고 `observed_environment_subjects`에 PID별 이름·읽기 완전성을 보존하되, GPU 환경의 `process_scan_complete=false`와 worker/provider 미확인 상태를 유지한다. 관측한 금지 이름의 기존 FAIL은 보존하며 일부 PID의 관측을 전체 GPU worker의 자격·provider 사용으로 확대하지 않는다. Kubernetes v1의 이름은 아래와 같이 별도 컨테이너 metadata 요약이며, Docker의 이 양성 관측 경로와 구분한다.

Kubernetes process v1 생산자는 `worker_subject_verified=false`, `worker_inventory_complete=false`, `access_tested=false`를 강제한다. B 소비자는 이 필드들을 참으로 재해석하지 않는다. Pod volumeMount 선언만으로 PLT-04 `forbidden_mount_violations`를 채우면 실제 mount 관측을 요구하던 기존 판정 의미가 넓어질 수 있으므로 보류한다. 선언이 없거나 변하면 부재를 증명하지 못한다. 네트워크 기능 대조와 수집 정리 오류는 이미 확인한 위반을 지우지 않는다.

## A 생산자에게 필요한 추가 계약

PLT-01: 동일 대상 세대의 worker별 PID와 start ticks/namespace 매핑, 실제 UID, cgroup 경로와 장치 허용, NVML GPU UUID별 PID와 PID namespace 대응, 무해한 장치 접근 결과, 전후 identity 및 전체성/미지원 진단이 필요하다. NVML PID만으로 파일 장치 권한을 입증하지 않는다.

PLT-04: 같은 worker 식별/세대와 worker 목록 전체성, worker별 환경 이름, 후보 경로의 **동일 주체** 접근 결과, provider 종류·자격 식별자(원문 제외)·수신자·전달 세대, 동적 provider와 파일/상속 경로의 범위/미지원 진단이 필요하다. 이름·lstat·Pod 선언만으로 접근이나 전달을 확정하지 않는다. 기존 v1 packet에 무단 필드를 추가하면 decoder가 거부하므로 새 명시적 profile/버전 계약이 필요하다.

## 적용 사례 및 검증 범위

정상 GPU 선언만 있는 경우 UNKNOWN, 독립 root/CapEff/쓰기/금지 mount 위반은 FAIL 후보로 남는다. 전후 세대 교체·지원하지 않는 worker/provider 범위는 UNKNOWN이다. 후속 기능 수집이나 정리 오류가 선행 양성 위반을 지우지 않는다. 실제 GPU 환경·제공된 정책·A 공개 생산자 계약과 복원 조건이 갖춰지면 같은 세대/주체의 정상·위반·교체·수집 실패를 실측해야 한다. 현재 공개 metadata 계약을 대신해 장치를 열거나 자격 원문을 읽는 시험을 추가하지 않는다. CPU fixture는 EXAMPLE이며 실측으로 승격하지 않는다.

### 제안할 A→B record (구현 대기, 승인된 스키마 아님)

추가 record는 A가 등록한 고정 프로그램과 공개 메서드를 통해 정확한 ENV/target 및 대상 세대에 연결해야 한다. 현재 공개 계약이 없으므로 아래 표는 전체 질문에 필요한 증거의 제안이며 승인된 스키마나 실행 가능한 API가 아니다. 기존 `process_snapshot(candidate_paths)`의 v1 필드를 확장하거나 비공개 전송 메서드를 호출해 대신하지 않는다. 새 profile·schema·producer program SHA-256을 명시하고 v1의 미지원 필드를 참으로 재활용하지 않는다. B는 profile/schema/source hash·target hash·원격 Pod UID/container ID/restart count·전후 동일성·시간 범위·필수 필드 집합을 확인한 뒤에만 해당 사실을 소비한다. 추가 필드, 알 수 없는 enum, 누락, 형식 오류, 교체, 불완전 캡처는 명시적 미지원/UNKNOWN으로 처리한다. 오류의 원문·비밀값은 EVD로 전달하지 않는다.

| 입력/출력 필드 | 생산자 증거 출처 및 결합 조건 | 미지원 조건 |
| --- | --- | --- |
| `source_generation` (Pod UID, container ID, restart count, image digest, 수집 시작/종료) | Kubernetes API 전후 조회 + 실제 exec 대상 동일성 | 전후 세대 차이·소유 불명 |
| `workers[]` (PID, start ticks, PID/mount namespace inode, UID, 역할/그 근거, cgroup 경로) | 컨테이너 PID 상태·/proc start ticks 및 역할 확인 근거를 같은 세대에 연결. collector PID 제외 | 역할 불명·PID 재사용·일부 worker 누락 |
| `gpu_bindings[]` (GPU UUID, NVML PID, PID namespace 대응, 실제 장치 노드/major:minor) | 호스트/컨테이너 NVML 조회와 동일 시점 worker PID/start ticks·namespace 결합 | NVML 불가, PID 대응 불명, MIG/UUID 불명 |
| `device_access[]` (worker 참조, cgroup 장치 허용/거부, 무해 접근 결과, 대상 노드) | 동일 worker UID/cgroup 주체에서 직접 시행한 읽기 전용/무해 접근과 전후 owner 확인 | DeviceRequests/Devices 선언만 존재, 주체 불일치, probe 정리 오류 |
| `worker_credentials[]` (worker 참조, env 이름, 후보 경로별 접근 `absent/denied/candidate_readable/unknown`, provider 유형·자격 식별 참조·수신자·전달 세대) | 같은 worker의 `/proc` 이름/namespace, 동일 주체 접근, provider 인벤토리/전달 이벤트를 원문 없이 결합 | 부모 env 합집합만 있음, lstat만 있음, provider/동적 갱신 누락 |
| `coverage` (worker·GPU·후보·provider별 complete/unsupported와 정해진 진단 코드) | 실제 총수·수집 범위·세대 전후 동일성을 생산자가 증명 | 일부 누락/불명/시간 구간 미포함 |

기존 PLT-01/04 CheckDef의 관측 이름은 새 생산자에서 온 사실이 실제 질문의 전체 범위를 충족할 때만 채운다. 양성 위반은 확인된 주체/대상별로 보존하되 미수집 범위의 무위반을 추론하지 않는다. 새 record가 미지원이면 기존 v1 경로로 안전하게 UNKNOWN을 유지하고 GPU 완료 표시를 하지 않는다.

## B 보조 metadata의 현재 실행 경계

`executors/kubernetes_gpu_process_observations.py`의 `collect(record, transport)`는 A의 공개 bounded process metadata 계약이 없으므로 원격 프로그램을 실행하지 않는다. 현재 반환 사유는 `A_PUBLIC_BOUNDED_PROCESS_METADATA_CONTRACT_PENDING`이며, `B_GPU_PROCESS_PUBLIC_CONTRACT_UNAVAILABLE`와 입력/candidate 진단을 보존한다. `_verify`·`_call`·transport의 private binding/deadline/clock을 사용하지 않고, 다른 소비자가 읽는 `model_snapshots`도 늘리지 않는다. 두 Kubernetes collector는 기존 process-v1과 필수 정상 대조를 먼저 수행한 뒤 이 보조 가용성 결과를 별도 요약에 남긴다. 필수 수집을 선택 GPU metadata보다 뒤로 미루지 않는다.

고정 `SOURCE`와 `_decode`는 CPU 계약으로 검증할 수 있다. 현재 SOURCE SHA256은 `c6c0f0703207e73463f02ead1a35610e35f19ca7a307c877f7ae6ecb915f2a8c`다. `/proc` PID/start ticks·유효 UID·namespace·cgroup, 문자 장치 FD의 major/minor와 `fdinfo` 접근 모드, 승인 후보의 dev/inode/mount namespace, NVML PID/UUID의 유일한 NSpid ancestor→container 결합을 다룬다. `O_PATH`는 `metadata_only=true`, read/write 불가다. 단순히 숫자 PID가 같다는 이유로 NVML을 결합하지 않는다. `PID_REUSED_OR_SCOPE_CHANGED`를 일반 `PID_READ_UNAVAILABLE`로 덮지 않으며, 안정된 다른 PID의 양성 행은 부분 오류와 함께 보존한다. 이 SOURCE/decoder 지원은 현재 live 실행이나 실제 GPU/MIG 접근을 검증했다는 뜻이 아니다.

`symlink_not_followed`인 Secret/projected 후보는 조용히 제외하지 않고 `unresolved_credential_candidates`와 `CREDENTIAL_CANDIDATE_SYMLINK_UNRESOLVED`로 남긴다. O_NOFOLLOW를 완화하거나 Secret·환경변수 값·임의 링크 대상 내용을 읽지 않는다. 공개 계약이 제공될 때에도 같은 주체·세대의 metadata-only 결합과 오류·예산·정리 결과를 전달해야 하며, 승인되지 않은 임의 source/argv/proc_root를 받는 우회 실행은 요청하지 않는다.

PLT-01 v1의 확인된 root UID·유효 capability는 양성 위반으로 보존한다. GPU FD 목록은 기존 Docker 선언형 `devices` 관측 형식이나 전체 장치 권한을 대신하지 않는다. Kubernetes PLT-04의 v1 환경명·lstat·Pod volumeMount 선언도 source-bound worker 자격 접근과 같지 않으므로 `worker_env_names`와 `forbidden_mount_violations`를 미수집으로 두고 후보별 `unknown` 및 요약을 보존한다. Docker에서 실제 관측한 주체의 금지 이름을 보존하는 현재 경로와 혼동하지 않는다.

전체 worker별 주장에는 `/21` 등 native 세대 기록과 현재 process-v1의 안정 PID/start·실제 engine binding tuple·ENV/target·session 및 worker 목록 완전성을 연결하는 추가 근거가 필요하다. 서로 다른 target 스키마의 SHA를 같다고 가정하지 않는다. 열린 FD의 전달은 실제 bytes read/provider 사용이 아니며, FD 부재는 접근 불가를 증명하지 않는다. 이 연결과 공개 전송 계약이 없으면 해당 범위는 UNKNOWN이고, 확인한 독립 FAIL은 유지한다.

과거 개발 단계의 `_verify(capture_snapshot=True)`·bounded `_call` 보조 probe와 `b3ab55f2ff8b34af561b25cf1499e2337d3d8dd0c7b22c2f29bbcfb85ba40322`/`9b5908b23f9d38f1205a2dbd2033ad23f2838beef1553feb911023a81d2f4037` SOURCE, 18초 잔여 예산·8초 alarm·10초 호출 예산은 당시 구현/시험의 기록이다. 3bba의 현재 live 실행 경로가 아니며 과거 자료의 원 SOURCE나 결과를 재작성하지 않는다.
