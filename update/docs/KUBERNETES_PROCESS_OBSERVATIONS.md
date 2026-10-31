# 선택 컨테이너 process 관측

PLT01/04의 기존 `container_config`/`credential_exposure` 전용 소비자를 위한
공통 입력이다. A-1은 selector·고정 프로그램·exec/포착·CLI를 제공하고 A-2는
기존 parser/관측·정책·판정의 소비를 연결한다. 이 입력만으로 두 점검의 정식
실행기 전환이나 완료를 표시하지 않는다. Docker inspect facade는 추가하지 않는다.

`KubernetesHTTP.process_snapshot(candidate_paths=None)`는 기존
`REQUEST_BINDING_PROFILE`과 정확 ENV/모델 매핑·context/run·엔진/프로브의
namespace UID/Pod UID/containerID/imageID/restart/start를 재사용한다.
엔진 컨테이너에서 고정 Linux stdlib reader를 exec하고 전후 API 세대를 검증한다.
프로세스 namespace 공유 선언은 거부한다. API 선언과 컨테이너가 보고한 inode는
실제 host-runtime 격리의 독립 증명이 아니다. HTTP나 상태 변경 요청은 보내지 않는다.

프로그램은 reader 자신을 제외한 선택 컨테이너 `/proc`의 PID/start·Name·네 UID·
effective UID·CapEff·mount namespace inode와 환경 **이름**만 읽는다. reader의
`capture_subject`와 관측된 `processes`는 별도다. argv/cgroup 원문·환경 값은
반환하지 않는다. 한 PID는 앞뒤 start ticks가 같아야 안정된 신원으로 표시한다.
PID 목록의 전후 안정성인 `pid_inventory_stable`과 필드 수집 완전성인
`process_metadata_complete`를 구분한다. 각 행은 시각·누락/교체 진단을 보존한다.
최대128 PID·각64 환경 이름·65536바이트 환경 읽기·524288바이트 출력이며,
기존 최대300초 전체 transport 예산을 공유한다. 상한 초과를 완전한 조사로 만들지 않는다.
디렉터리 열거도128개와 초과 확인1개에서 멈춘다. `pid_count_before/after`의129는
실제 총수의 하한이며 `PROCESS_SET_BOUND`와 불완전 flag를 동반한다. 각 PID의
status는 process 단위 metadata이며 모든 thread의 개별 자격을 조사했다고 하지 않는다.

명시 `candidate_paths`는 기존 자격 경로 문법(최대32개/128바이트)을 사용한다.
누락과 명시 빈 목록은 구분한다. 후보와 중간 디렉터리의 링크를 따라가지 않고
lstat metadata·전후 metadata 안정성·경로 체인 hash만 반환한다. 파일 내용은
열지 않고 접근 시험/UID 전환도 하지 않는다. `access_tested`는 항상 false다.
이 metadata는 capture 주체의 namespace에서 읽은 것이며 worker의 파일 접근이나
자격 유효성/허용성·모델/가중치 소비를 증명하지 않는다. API mount는 선언으로 구분한다.

`worker_subject_verified`와 `worker_inventory_complete`는 항상 false다.
별도 검증된 exact-source worker/model 세대 생산자 없이 PID 이름·GPU PID·
동일 UID·exec 성공·Ready·source/image 선언을 실제 worker 귀속으로 올리지 않는다.
native0.32/lifecycle의 기존 lab/source gate는 유지한다. 주입 runner 또는 EXAMPLE
ENV/lab 대상은 EXAMPLE이며 실제 선언으로 상향하지 않는다. 외부 JSON의 verified
필드를 받아들이는 경로도 없다. A-2 전용 실행기는 같은 live 공통 호출을 소비해야 한다.
CLI가 저장한 파일 자체는 새 측정 생산자를 인증하거나 EVD/RES를 자동 생성하지 않는다.

API 후속 교체에서는 먼저 검증한 packet을 `binding_verified_before_after=false`와
함께 남기며 현재 소유 세대에 귀속했다고 주장하지 않는다. 환경 이름이나 mount 후속
누락도 앞선 UID/capability 관측을 지우지 않는다. 잘못된 packet/값/extra 필드는
거부하고 원문 예외·stderr 대신 고정 진단만 남긴다. mount 선언의 후속 변경은 PID
관측과 별도 flag로 제공하므로 전용 소비자가 관측별 의미를 유지해야 한다.

CLI는 `capture-kubernetes-process-snapshot --env ... --target ... --output ...`
이며 기본은 I/O 없는 정적 준비다. `--execute`가 명시됐을 때만 실행한다.
`--candidate-paths`는 목록 JSON 파일이고 `--kubectl-binary`, `--max-duration`은
기존 경계의 binary/전체 예산이다. 출력은 저장소 밖의 새 파일에 배타적으로 쓰며
실패하면 검증된 수집값만 stdout fallback으로 보존한다. 판정은 UNKNOWN이고
실제 cluster/GPU 관측·기존 check 제품 소비는 각각 별도 검증해야 한다.
