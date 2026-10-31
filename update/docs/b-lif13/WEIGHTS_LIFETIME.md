# LIF13 선정 zero-copy 기존 참조

`tools/b_lif13_weights.py`는 원 PACK31 LIF13 rev4의 질문·세 challenge·한 prose를 유지한다. host는 native producer를 실행하거나 일반 kubectl GPU exec를 추가하지 않는다. 원 Store의 정확 ENV/POL/PLAN을 확인하고 선택된 기존 Pod에서 실행할 명령을 준비하거나, 이미 만들어진 native capture를 원 manual EVD→RES→RPT/검증/export-restore로 연결한다.

```sh
cd update
python -m tools.b_lif13_weights --store STORE --plan PLAN --pack packs/PACK-BASELINE \
  --target target.json --config native-config.json --output /fresh-owned/lif13 --entered-by operator \
  --pod-config /owned/native-config.json --pod-source-root /approved/vllm --pod-output /owned/capture.json
```

기본은 RPC·GPU import·daemon release 없는 preflight다. 명시한 Pod 경로는 host 경로와 구분한다. 반환 argv는 기존 Pod 내부의 `python -m tools.b_lif13_weights_native --config ... --source-root ... --output ...` 및 실행 opt-in을 준비할 뿐이다. 해당 모듈·원 source·config의 Pod 내 배치까지 확인했다고 표시하지 않는다. feature 선택·daemon 시작·복구·GPU reset을 수행하지 않는다.

선정 daemon·model/rank·UID/PID/start/boot/ns/GPU UUID, 원 cache key 12필드, owner/recovery owner, 전역 release 및 시험 후 empty 허용이 명시된 `byul-lif13-native-config/1`을 소비한다. mode는 zero_copy이며 bounded read는 1..16 원소다. 실제 소스·선정 image·복구 창이 없으면 실행 단계만 대기한다. CPU by-value를 zero-copy 증거로 대체하지 않는다.

이미 저장한 producer capture를 연결하려면 위 명령에 `--capture capture.json --import-capture`를 추가한다. 새 ENV/POL을 만들지 않고 원 canonical Ref를 소비한다. config SHA·target·세 Ref·db952 원 source commit이 일치해야 한다. 두 owned consumer의 baseline/held-after 안전 HMAC digest·shape·dtype·read 완료와 cleanup을 보존하고 digest 관계를 same/changed/unavailable로 나눈다. 이 관계는 선언된 selected tensor prefix의 부분 관측이며 전체 모델 정상·값 승인이 아니다.

세 결과를 따로 남긴다.

- release actor/daemon rank·role·응답과 기업 release 권한 인증을 구분한다.
- 후속 get_state 응답은 실제 fallback 소비와 구분한다. source의 “Weights were released” 오류만으로 정책 FAIL을 만들지 않는다. fallback·copy는 미수집이다.
- 기존 CUDA IPC 참조 read·전후 digest 관계·owned child 정리와 daemon 복구를 구분한다. 종료 후 empty 허용 선언은 복구 완료 증거가 아니다.

원 client 파일 bytes 대조 주장은 실행 daemon의 source나 target/generation/인가 receipt 인증이 아니다. 외부 receipt 없는 standalone capture는 DECLARED/EXAMPLE로만 보존한다. EVD는 기존 manual 계약대로 DECLARED이며 원 정상 대조·challenge 전체 미수행, 세 축 UNKNOWN을 유지한다. 원문 weight·CPU raw bytes·IPC handle·pointer·socket/entry/key 원문을 artifact에 저장하지 않는다.

원 Store와 입력 bytes는 보존하고 fresh 출력에만 기록한다. 첨부 쓰기 실패 시 같은 정제 facts를 원 manual 노트와 반환 summary에 보존한다. 후속 Store/report 저장까지 실패하면 safe_facts와 단계별 recovery를 담은 오류를 반환하고 CLI는 비정상 종료한다. 수집을 재실행하거나 실패를 저장 성공으로 표시하지 않는다.

원 get_state는 모든 CUDA TensorEntry의 IPC sent-counter를 발행한다. native producer는 모든 CUDA entry를 원 rebuild로 한 번 받아 미선정 참조를 정상 drop하고 선정한 한 tensor만 bounded read한다. 전체 값 read·모델 구성 관측으로 확장하지 않는다. payload 제한·timeout·unpickle·부분 rebuild 실패는 release 미시도여도 daemon native handoff에 영향을 남길 수 있다. child 종료·참조 drop과 daemon limbo/counter·allocator 전역 회수를 구분하며 후자는 UNKNOWN이다. 직접 counter를 조작하지 않고 기존 외부 recovery owner와 승인 절차가 필요하다.

baseline·후속 get_state의 received/rebuilt CUDA entry 수와 CPU by-value 수, runtime torch version/reductions 파일 SHA를 선언 facts로 보존한다. original module 검증과 실행 daemon 인증을 구분하며 count 완전성은 daemon counter 회수 완료가 아니다. release dispatch의 real/effective UID도 daemon UID와 별개로 보존하며 UID 일치만으로 기업 release 권한을 승인하지 않는다.

## Linux CPU peer·수신 상한 검증

선정 native script의 `_proc_identity`, `_socket_identity`, `_daemon_epoch`, `_check_peer`, `BoundedSocket`을 실제 Linux 커널과 연결하는 명시적 추가 검증이다. 소유한 표준 라이브러리 AF_UNIX peer 한 개를 만들고 실제 `SO_PEERCRED`·`/proc/<pid>/stat`·boot/PID/mount namespace 및 socket inode를 읽는다. 정상 peer, 잘못된 PID·epoch, 실제 socket 교체와 수신 상한 초과를 구분한다. 기존 37개 EXAMPLE 단위 회귀와 A147 Linux 86개는 회귀 근거로 재사용하며, 실제 peer 확인 근거는 이 별도 명령의 Linux 출력에 연결한다.

입력은 고정 native 파일 한 개와 새 출력 경로뿐이다. native 원본은 `0ae9cc56dcce903d059fab8ff2d2e12f23cef3ba:update/tools/b_lif13_weights_native.py`의 Git blob이며 SHA-256은 `8548dc812acdc421e313c5532d6cd4d6bd214c19d51b79adc96ffb7b7804c4da`다. probe 기준은 `e9b98d4d40d702d0e517cda0116dde55ae5d873f`의 `update/tests/linux_cpu/lif13_peer_probe.py`이며 SHA-256은 `789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d`다. 작업 트리의 newline 변환을 거치지 않고 LF 원 bytes를 준비한다. 이미 선택·승인한 Linux/amd64 시험 환경의 Python 3.12와 표준 라이브러리만 사용하며 새 설치·GPU/모델·daemon 기동은 필요하지 않다.

저장소 루트에서 다음과 같이 입력을 고정한다. 해당 Git 객체와 probe 파일이 있는 체크아웃을 사용하거나, 같은 bytes의 두 파일을 기존 WSL에 전달한다. probe는 `update/tests/linux_cpu/` 경로 구조를 유지하며, 고정 native 파일은 외부 소유 경로에 둔다.

```sh
LIF13_PEER_RUN_DIR=$(mktemp -d /tmp/byul-lif13-peer.XXXXXX)
git show 0ae9cc56dcce903d059fab8ff2d2e12f23cef3ba:update/tools/b_lif13_weights_native.py > "$LIF13_PEER_RUN_DIR/frozen_native.py"
printf '8548dc812acdc421e313c5532d6cd4d6bd214c19d51b79adc96ffb7b7804c4da  %s\n' "$LIF13_PEER_RUN_DIR/frozen_native.py" | sha256sum --check -
printf '789b08ff1acc549e478558fa8317eac074905e675d4cce87deede99f5d528e2d  update/tests/linux_cpu/lif13_peer_probe.py\n' | sha256sum --check -
```

실행 명령은 하나다. 출력 경로는 아직 존재하지 않아야 한다.

```sh
python3.12 -I -S -B update/tests/linux_cpu/lif13_peer_probe.py \
  --native-script "$LIF13_PEER_RUN_DIR/frozen_native.py" \
  --output "$LIF13_PEER_RUN_DIR/proof"
```

고정 source는 1 MiB 이하 일반 파일이어야 한다. 읽기는 `O_NONBLOCK`·`O_NOFOLLOW`로 열어 FIFO·symlink 교체 시 대기를 막고, FD와 경로의 device/inode/size/mtime_ns/ctime_ns를 읽기 전후 대조한다. 종료 시 native와 probe를 각각 재검사하며, 한 파일의 실패가 다른 파일의 검사·앞선 단계·정리 결과를 지우지 않는다. source 전후가 다르면 `FAILED`·종료 코드 1을 남긴다.

실행 조건은 Linux/x86_64, 최적화가 꺼진 Python 3.12(`sys.flags.optimize == 0`), 읽을 수 있는 `/proc` 및 `SO_PEERCRED`, 소유 임시 폴더에서의 AF_UNIX socket 생성이다. probe가 만든 짧은 socket 경로와 0700 폴더·0600 socket만 사용한다. 기존 vLLM·서비스 socket이나 운영 자료를 입력하지 않는다. 잘못된 peer/epoch와 inode 교체는 원 native 검사 함수의 거부로, 큰 frame은 payload 수신·fixture decoder 진입 전에 원 `BoundedSocket`이 거부하는지 확인한다.

출력은 새 폴더의 `proof.json` 한 개이며 schema는 `byul-lif13-linux-peer-probe/1`이다. 실제 실행 argv·Python/OS·고정 native 전후 SHA와 다음 세 단계의 관측·거부 결과를 남긴다.

1. `peer_identity_and_mismatch`: 실제 peer 신원 확인과 잘못된 PID·epoch 거부.
2. `oversize_before_payload_decoder`: payload 수신·fixture decoder 진입 전 상한 거부.
3. `socket_replacement_epoch`: 실제 socket inode 교체 뒤 세대 검사 거부.

지원 Linux에서 세 단계와 정리를 확인한 경우에만 `VERIFIED_LINUX_KERNEL_FIXTURE`·종료 코드 0을 반환한다. 기술 검증 실패는 `FAILED`·종료 코드 1로 앞서 얻은 사실과 정리 오류를 보존한다. 지원 밖 환경은 `UNSUPPORTED`·종료 코드 2와 미실행 개수를 남긴다. `-O`·`-OO` 또는 `PYTHONOPTIMIZE`로 최적화가 켜진 입력은 `PYTHON_OPTIMIZATION_UNSUPPORTED`로 닫히며, kernel case·소유 peer 생성·고정 native 모듈 실행은 0이다. `source_python_module_executed`는 고정 Python script 모듈의 적재 여부다. 고정 source 읽기·SHA·새 출력 경로 등 입력 검증에서 먼저 닫히면 proof 생성 전 stderr의 `FAILED`·종료 코드 1이 남을 수 있다. 이 상태는 별도 kernel fixture의 결과이며 원 LIF13 정책 판정을 채우지 않는다.

정리는 `owned_sockets_closed`, `owned_peer_reaped`, `owned_streams_closed`, fixture 폴더 생성·제거, socket 경로 제거, 강제 종료 여부와 오류로 나누어 보존한다. 8-byte fixture frame은 수신 상한을 검사하기 위한 별도 입력이다. 원 vLLM `recv_msg`·pickle·get_state·release, 실제 CUDA IPC·가중치 참조·모델 복구는 이 명령에서 미수집이며 기존 세 결과와 전체 LIF13은 UNKNOWN을 유지한다.

현재 실제 Linux 실측은 대기다. 기존 WSL에서 위 한 명령을 실행한 `proof.json`·종료 코드와 원 argv를 인계받아 해당 플랫폼 결과를 확인한다. 본문의 `/tmp` 준비 명령은 Linux용이다. macOS guard 입력·출력은 `/private/tmp` 같은 symlink 없는 실제 소유 경로를 사용한다. macOS에서는 Linux 경로를 우회하지 않고 `UNSUPPORTED`와 종료 코드 2를 남기는 범위 및 syntax 확인만 사용한다. 이 경우 native import·peer·AF_UNIX·GPU 실행을 성공으로 집계하지 않는다. 도구가 소유한 자식·socket·stream·임시 폴더를 finally에서 정리하며, 결과와 고정 입력은 검토용으로 보존한다. daemon 복구·다른 프로세스 종료·GPU reset·새 AWS 자원은 수행하지 않는다.
