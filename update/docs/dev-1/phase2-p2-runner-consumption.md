# 2단계 P2: runner 소비 기록

개발 담당 A의 P2 중 REQ-08·09·10을 위한 V1 경로다. `vllm_consumption_callback.py`가
API `InputProcessor`의 입력·옵션 검증, worker `_update_states`의 새 요청 도착,
`_sample` 진입 시 InputBatch의 CPU 배열을 관측한다. GPU kernel 내부 소비를 증명하지 않는다.
SUP-03·08의 추가 모듈·선택 옵션 소비는 별도 후속 범위다. V2 경로의 CPU 준비는 마지막 절에 기록한다.

- `guard prepare-engine-callbacks --runner-consumption`으로만 켠다. 기존 기본 관측 설정은 그대로다.
- vLLM 0.31.0의 검토된 소스·함수 코드가 일치해야 설치한다.
- 원문 프롬프트·응답·stop 문자열·logit bias 값은 기록하지 않는다. 요청은 세션 HMAC,
  옵션은 정수와 개수다. 온도·top_p는 천분율이다.
- top-k 비활성은 SamplingParams의 -1/0과 InputBatch의 vocab_size를 0으로 정규화한다.
  읽지 못한 값은 null이다. allowed-token 개수는 중복 ID를 제외한다.
- `logprobs=-1`의 전체 vocabulary 요청은 API 개수 정규화를 지원하지 않아 null과 진단으로 남긴다.
  logit bias가 있는 요청도 sampler의 해당 관측이 없어 gap으로 남긴다.
- `runner_consumption_record()`가 `guard-gpu-common-input/1`의 `runner-consumption`을 만들고
  같은 계약의 `validate()`로 검사한다. GPU 확인 전에는 지원 목록에 등록하지 않는다.
- 각 API 검증 요청마다 runner·sampler 행이 있어야 한다. 읽지 못한 비교 필드는 gap이다.
  첫 sampler 값과 이후 바뀐 값을 보존하며, 뒤의 누락·잘림이 앞의 위반 후보를 지우지 않는다.
- API 요청 형식 검사에서 InputProcessor에 들어오기 전에 거부된 요청은 이 관측기로 볼 수 없다.
  HTTP 응답 대조와 별도 구분한다.

## Claude 중단점과 인계

2026-11-05 Claude 화면과 실제 `codex/dev1-p1-producers` worktree를 대조했다.
원 변경 9개는 저장소 밖 `byul-dev1-evidence/20261105-codex-handoff/source-before/`에 보존했다.
로컬 main은 GitHub 화면의 `925ca62`로 fast-forward했다. 이 main의 후속 차이는 runtime·platform이며
현재 A의 update 생산자와 겹치지 않는다. 작업 브랜치의 기존 P1 커밋과 미커밋 P2를 이어받았다.

원 run 14의 sampler temperature·top_p가 null이고 top_k는 0이었다. `.get()`만 제공하는
`vllm_lifecycle_sensor.Fields`에 `[]`로 접근해 읽기에 실패한 뒤 top_k를 0으로 채운 결함이다.
원 `k3d-run14/runner-consumption-record.json`은 수정하지 않는다.

첫 수정의 run 15에서는 요청별 온도·top_p·top_k를 읽었다. 제한 요청 뒤의 무제한 요청에서
InputBatch에 남아 있는 마스크를 적용된 허용 목록으로 읽어 잘못된 후보가 하나 나왔다.
`has_allowed_token_ids`의 요청별 적용 여부를 함께 확인하도록 고쳤다. 이 회차의 13개 사건과
원 위반 후보는 `k3d-run15/`에 보존한다. 이 보수는 vLLM의 실제 마스크 선택 의미와 맞춘 것이며
판정의 수락 기준이나 기업 정책을 바꾸지 않는다.

## 검증 결과

- 직접·계약·불변식 225 통과, 기존 1 제외(pytest 0.89초, 부모 1.088초).
- 인접 관측기 120 통과·1 실패 원본을 보존했다. P1에서 더한 scheduler 159,016 bytes pin이
  기존 정확 목록 기대값에 빠진 실패이며, 인계 전 Linux CI `e94829a`도 같은 1 실패·11,239 통과였다.
  두 SHA·정확 크기를 모두 요구하도록 기대 목록을 갱신한 cache 전체 51 통과(pytest 1.44초, 부모 1.840초).
  원래 기본 크기 거부·한 byte 오차 거부·정확 크기 수락 검증은 유지했다.
- run 16: 정상·제한·top-k 무제한·중복 허용 ID 4개 모두 HTTP200, 잘못된 token ID와 음수 온도는 HTTP400.
  API 검증 4개·runner 도착 4개·sampler 적용 4개·API 거부 1개, 총 13개 사건과 위반 후보 0개다.
  음수 온도는 InputProcessor 전 단계의 거부라 사건으로 포함하지 않았다.
- 남은 계약 gap은 `GPU_INPUT_PRODUCER_UNSUPPORTED`다. CPU 생산 확인이며 GPU 실측·지원 등록·B 소비 연결 완료가 아니다.
- wheel을 체크아웃 밖 새 Python 3.12 환경에 설치해 CLI로 runner 관측 자산을 준비하고 설치 파일과 자산의 바이트 해시를 대조했다.
- 검토 소스 manifest `54189f232f5e0a3795d061ec155a1e2b0df702b16f9b1ccd02b5de7725cc63c4`, 439개 파일.
  PACK-BASELINE@32·B 원본 pin·외부 공용 계약은 그대로다.

공개 요약은 `docs/evidence/dev1-p2-runner-cpu-20261105.json`, 원자료는 저장소 밖 `byul-dev1-evidence/k3d-run{14,15,16}/`와
`20261105-codex-handoff/`에 있다. 최종 전체 suite는 해당 소스의 Linux CI로 별도 확인한다.

## SUP-03 공급 파일 읽기 (2026-11-05)

- 관측(`adapters/vllm_supply_read_callback.py`): 함수를 감싸지 않고 Python audit hook(`sys.addaudithook`)의 `open` 사건만 읽는다. 원 코드와 열기 동작은 바꾸지 않는다.
  - 선언한 공급 루트(`guard prepare-engine-callbacks --supply-root <경로>`, 여러 번 가능) 아래 경로만 남긴다.
  - 프로세스마다 (상대 경로, 읽기/쓰기) 처음 한 번, 열 때의 크기·수정 시각을 남긴다.
  - 내용 해시는 수집 시점에 엔진 컨테이너 안에서 따로 계산한다(`engine_supply_hashes`, 64 MiB 초과는 `too_large`).
- 계약 종류 `supply-reads`(소비 SUP-03):
  - 경로는 승인 목록과 대조할 수 있게 상대 경로의 평문 SHA-256으로 남긴다.
  - 위반 후보: 공급 루트 안 쓰기(`supply_file_written`), 연 뒤 크기 변경(`supply_file_changed_after_open`).
- k3d CPU 확인(run 17, `byul-dev1-evidence/k3d-run17/supply-reads-record.json`, chat 요청 1개):
  - API 서버: `config.json`·`tokenizer_config.json`·`special_tokens_map.json`·`generation_config.json`, chat template 파일
  - EngineCore: `config.json`·`tokenizer_config.json`·`special_tokens_map.json`
  - worker: `pytorch_model.bin`
  - 해시는 가중치(250 MB, `too_large`)를 뺀 다섯 개를 계산했고 쓰기는 0이다. 호스트 원본 SHA와 같다(config.json `54162c9c…`, tokenizer_config.json `d1e4185b…`).
- 한계(중요): 같은 실행에서 tokenizer가 쓴 `vocab.json`·`merges.txt`는 보이지 않았다. Rust `tokenizers` 같은 native 코드는 Python `open`을 거치지 않기 때문이다.
  - 그래서 기록에 `SUPPLY_NATIVE_READS_UNOBSERVED` 진단을 항상 붙인다. 이 기록만으로는 공급 범위 완료가 되지 않는다.
  - native 읽기 관측(fanotify, 또는 프로세스별 열린 fd 목록 대조)이 더해져야 SUP-03을 닫을 수 있다. 2단계 후속 항목이다.

### native 읽기: inotify (2026-11-05)

- 검토한 후보:
  - fanotify: `CAP_SYS_ADMIN` 필요 → 제외
  - LD_PRELOAD: 공유 라이브러리 빌드와 로딩 변경, 직접 syscall 누락 → 제외
  - ptrace/strace: `CAP_SYS_PTRACE`, 엔진 교란 → 제외
  - `/proc/<pid>/fd` 폴링: 짧은 열기 누락 → 제외
  - inotify: 특권 없음 → 채택
  - runtime Tetragon 파일 열기 정책: 프로세스 귀속이 되는 독립 근거 → GPU lab에서 runtime 담당과 합의해 더할 후보
- 실험(run 17 엔진 컨테이너, 같은 tokenizer 로딩):
  - Python audit hook은 `config.json`·`tokenizer_config.json`·`special_tokens_map.json`만 봤다.
  - inotify는 여기에 더해 `vocab.json`·`merges.txt`를 봤다. Rust `tokenizers`가 native로 여는 파일이다.
- 구현(`SupplyWatcher`, `guard prepare-engine-callbacks --supply-root … --supply-watch`):
  - ctypes만으로 inotify를 쓴다.
  - Pod 안에서 세션 폴더 잠금 파일을 먼저 만든 프로세스 하나만 지킨다.
  - 폴더 루트는 하위 폴더까지 지킨다. 파일 루트는 파일 자체를 지킨다. 파일 하나를 bind mount하면 부모 폴더 감시로는 사건이 오지 않았다(run 18 첫 시도, 원자료는 `k3d-run18/*-attempt1*`).
  - 큐 넘침은 `SUPPLY_INOTIFY_OVERFLOW` gap이다.
  - 행은 계약의 `file_observed`(state `inotify_unattributed`)다.
- k3d CPU 확인(run 18, `byul-dev1-evidence/k3d-run18/supply-reads-record.json`):
  - inotify가 `vocab.json`·`merges.txt`를 포함해 모델 폴더의 모든 사용 파일과 chat template을 봤다.
  - 해시는 가중치(`too_large`)를 뺀 7개다. 위반 후보는 0이다.
- 남은 진단: `SUPPLY_NATIVE_READ_PROCESS_UNATTRIBUTED`.
  - inotify는 연 프로세스를 알려 주지 않는다. 같은 노드의 다른 Pod가 같은 hostPath 파일을 열어도 섞일 수 있다.
  - 그래서 이 기록만으로는 complete가 되지 않는다. 모델 폴더의 Pod 전용 마운트 확인이나 Tetragon 같은 독립 귀속 근거가 더해져야 닫힌다.

### 마운트 전용성 확인 (2026-11-05)

- 방향(사용자 결정): inotify 사건의 프로세스 귀속 대신, 공급 루트 볼륨이 소유 엔진 Pod 전용인지 Kubernetes API로 확인한다. 전용이면 그 사건을 이 Pod의 읽기로 좁힌다. SUP-03은 Pod 안의 프로세스 구분이 필요 없다.
- 구현(`runtime/kubernetes_mount_scope.py`, `KubernetesHTTP.supply_mount_exclusivity`): 엔진 Pod와 클러스터 전체 Pod 목록을 읽어 루트마다 판단한다.
  - hostPath: 같은 노드에서 겹치는 호스트 경로(상위·하위 포함)를 마운트한 다른 Pod가 없어야 한다.
  - PVC: 같은 namespace에 같은 claim을 쓰는 다른 Pod가 없어야 한다.
  - emptyDir·configMap·secret·projected·downwardAPI: Pod 안 사본이라 같은 Pod의 다른 컨테이너만 본다.
  - 모든 경우 같은 Pod의 다른 컨테이너가 같은 볼륨을 마운트하면 공유다. 끝난 Pod는 세지 않는다.
  - 그 밖의 볼륨 종류와 마운트를 못 찾은 루트는 확인 불가다.
- 기록:
  - 수집 전후 두 번 모두 모든 루트가 전용이면 `file_observed`가 `inotify_pod_exclusive`가 되고 귀속 진단이 빠진다.
  - 공유면 `SUPPLY_MOUNT_SHARED`, 확인 불가나 미실행이면 `SUPPLY_MOUNT_EXCLUSIVITY_UNVERIFIED`와 `SUPPLY_NATIVE_READ_PROCESS_UNATTRIBUTED`가 붙는다.
  - 결과에는 원 경로·Pod 이름 대신 종류와 개수만 남긴다.
- 범위 밖: 노드의 Kubernetes 밖 프로세스(호스트 데몬 등)는 API로 보이지 않는다. GPU lab에서는 노드가 lab 전용인지 운영 기록으로 따로 확인한다.
- k3d CPU 확인(run 18):
  - 엔진만 있을 때: 전후 모두 두 루트가 `exclusive_host_path`였고 귀속 진단이 없다. 원자료는 `supply-reads-exclusive-final.json`이다.
  - 같은 노드에 `/srv/models`를 마운트한 Pod를 하나 더 띄웠을 때: 두 루트 모두 `shared_host_path`(공유 Pod 1)였고 `SUPPLY_MOUNT_SHARED`가 붙었다. 원자료는 `supply-reads-shared.json`이다.
- 함께 고친 결함:
  - Pod 목록 읽기 한도를 공용 pipe 한도(16 MiB)로 맞췄다.
  - 기록 읽기·해시 수집 프로그램을 `python3 -I -S`로 돌린다. 그전에는 엔진 PYTHONPATH의 센서를 수집 프로그램이 불러와, 자기 파일 열기가 공급 읽기로 기록됐다(callbacks-263·275·287, `SUPPLY_PROCESS_START_UNAVAILABLE`).
- 남은 gap: `SUPPLY_FILE_NOT_HASHED`는 가중치(250 MB, 64 MiB 한도 초과)다. 가중치 bytes 대조는 native weight 읽기(SUP-06·16) 쪽 근거를 쓴다.

## GPU 확인(2026-11-05, AWS g6.2xlarge L4)

- 세대 `v1-producers`(V1 runner), 세션 `a-p1-20261105-3`. 생산자 P1과 같은 엔진·요청에서 함께 수집했다(`phase2-p1-producers.md` GPU 절).
- runner-consumption: 요청 14개 모두 `api_validated`·`runner_received`·`sampler_applied`로 이어졌고 `source_ended` 참이다.
  - 남은 gap: `GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias`. logit bias는 V1 InputBatch의 CPU 배열에 요청별 값으로 남지 않아 sampler 단계에서 읽지 못한다(한계, 판정을 완화하지 않음).
  - 세션 -2에서는 다른 형식의 요청 태그 때문에 API 행과 runner 행이 이어지지 않았다. 센서가 `byul-cb-<32 hex>` 태그만 지문으로 쓰는 설계를 GPU에서 다시 확인한 결과다.
- SUP-03 supply-reads: 공급 파일 5개(경로는 지문만 남김. 크기 659·7,305·7,031,645·242 bytes와 가중치 988,097,824 bytes)를 audit hook과 inotify 모두에서 관측했다. 수집 전후 `/models` 마운트가 엔진 Pod 전용이라 `inotify_pod_exclusive`다. 가중치 988 MB는 해시 한도(64 MiB) 초과로 `SUPPLY_FILE_NOT_HASHED`다.
- runner-consumption과 supply-reads 생산자는 같은 날 `SUPPORTED_PRODUCERS`에 등록했다(V1 runner 경로 기준). 남은 gap은 등록 뒤에도 그대로 남는다.

## V2 runner CPU 준비(2026-11-05)

- `adapters/vllm_consumption_v2_callback.py`는 vLLM 0.31.0 V2 `GPUModelRunner.add_requests`·`sample`의 고정 소스 SHA와 함수 코드를 확인해 감싼다. API 관측은 기존 V1 InputProcessor 관측기를 재사용한다.
- `SamplingStates`·logits processor의 CPU 배열을 `input_batch.idx_mapping_np` 자리로 읽어 온도·top_p·top_k·logprobs·허용 token/금지어/logit bias 개수를 남긴다. GPU kernel 내부 소비나 token ID 목록의 동일성을 증명하지 않는다.
- V2의 `LogitBiasState.num_logit_bias` 개수도 기록하지만, 공통 계약의 V1 한계 gap은 그대로 유지한다. 비교 의미 변경은 B 합의 과제 Q2다(`phase2-contract-questions.md`).
- `collectors/runner_consumption_v2_producer.py`는 V2 행을 공통 runner-consumption 형식으로 변환한다. V1 runner 행이 섞이면 버리고 진단을 남긴다. 생산자 신원은 V1과 다른 이름·소스 SHA이며 GPU 확인 전에는 등록하지 않는다.
- 기존 `--runner-consumption` 설정으로 V2 모듈을 관측한다. 준비 자산에 새 모듈을 포함하고 Kubernetes 읽기 필터도 V2 schema를 받는다. `--lab-kernel-checks`는 같은 sample 지점을 쓰므로 그 세션에서는 V2 소비 관측을 설치하지 않고 gap을 남긴다.
- GPU 확인용 `v2-producers` 세대는 V2 runner를 명시한다. 시험은 실제 고정 V2 model_runner.py 함수와 CPU 대역 상태 배열로 수행하며, GPU 엔진 기동 시험과 구분한다. 비정상 sampler 개수(NaN·무한대·비정수·범위 초과)는 null로 남기고 다른 관측은 보존한다.
- 남은 일: 고정 이미지의 실제 V2 GPU 경로에서 API→runner→sampler 결합·읽기 종료를 확인한 뒤 지원 등록 검토. SUP-08 멀티모달 소비와 A 담당 GPU 실측은 별도 미완료다.
- 최종 CPU 검증은 P1과 묶어 직접·공통 계약·불변식 289 통과/기존 1 제외(pytest 1.46초, 부모 1.634초)다. wheel 빌드와 체크아웃 밖 격리 설치 검증을 통과했고, 설치본에서 새 모듈 4개 import·콜백 자산 21개 해시·V2 관측기 bytes 일치를 확인했다. 제품 소스는 검증 동안 불변이다. 전체 기본 suite는 이 소스의 Linux CI로 확인한다. 원자료는 `byul-dev1-evidence/20261105-codex-takeover/`에 둔다.
- 첫 전체 CI `3979a2c`는 11,287 통과·9실패·기존 skip1(pytest 585.26초, 부모 586.347초)였고, 실패 9개는 공통 소스 고정 목록에 새 4파일/변경 3파일이 반영되지 않은 동일 원인이다. 검토한 실제 소스의 해시 목록(`release/reviewed-source.json`)을 445파일로 갱신했으며 SHA는 `f56ee035749d900537926262d7c37db3e9547e451f5815b48e312977ff630fdc`다. gate 코드·원 B pin·범위·변조 거부·제품 bytes는 그대로다. 해당 gate/제품·신규 생산자·불변식 253 통과/기존1제외(pytest 14.97초, 부모 15.118초)를 확인했고 수정 커밋에서 전체 CI를 재실행한다. 실패 원본은 `ci-failure.log`에 보존했다.
- V2 runner 경로는 아직 관측하지 않았다.
