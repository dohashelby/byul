# 2단계 P2: runner 소비 기록

개발 담당 A의 P2 중 REQ-08·09·10을 위한 V1 경로다. `vllm_consumption_callback.py`가
API `InputProcessor`의 입력·옵션 검증, worker `_update_states`의 새 요청 도착,
`_sample` 진입 시 InputBatch의 CPU 배열을 관측한다. GPU kernel 내부 소비를 증명하지 않는다.
SUP-03·08의 추가 모듈·선택 옵션 소비와 V2 runner는 별도 후속 범위다.

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
