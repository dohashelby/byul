# Windows CPU 제품화와 자동화 1차

기준: `codex/gua-policy-cache-m13` / `714b8bd60ebae3f8bde2bd056f7850ab38325209`.
작업 브랜치: `codex/gua-windows-automation-phase1`. 상위 정책 PR이 미병합이므로 PR의 base는 M13이다.

## 범위와 완료 수준

이번 사용자 승인은 Windows CPU 제품 기동, 기존 5개 점검 보강, REQ-10·REQ-13·OBS-03 자동화와 구현 후 테스트·수정·문서화·커밋·푸시·초안 PR이다. 기존 POLICY_PROMPT의 입력 양식 전용 단계와 커밋 금지는 이번 승인 범위에 적용하지 않는다. 원문 77개 전체 자동화나 기업 시스템·GPU 실증을 완료했다고 주장하지 않는다.

정책 등록, 자동 실행기 연결, 모의 계약 검증, 한 실제 엔진 구성 검증, 정상/위반/미확인 사례 검증, 두 이미지 비교를 별도 상태로 기록한다. 매트릭스 A/B/C는 인프라 분류이며 구현 완료 표시가 아니다.

## Windows CPU 실행

`examples/windows-cpu.toml`은 Linux amd64 Docker와 로컬 snapshot을 사용하는 제한 실행 프로파일이다. 기술 실행 설정은 `[vllm]`과 `[vllm.resources]`, 기업 허용 기준은 POL에 둔다. 경로를 포함한 TOML은 리터럴 문자열 또는 정상적으로 이스케이프한 문자열을 사용한다.

현행 실행에는 프로젝트의 64비트 Python 3.12와 `uv sync --frozen`을 사용한다. 아래 3.11.16 실측은 당시 기록으로 보존하며 호스트 버전 전환이 해당 엔진 시험의 재실행을 뜻하지 않는다. `examples/windows-cpu.toml`을 별도 위치에 복사하고 models_dir를 실제 snapshot 절대 경로로 바꾼다. ENV에는 실제 이미지 repository@sha256, 관측 버전, 모델 revision/inventory, Linux CPU architecture/model/kernel/threads를 기입한다. 준비 검사가 호스트 신원이나 기업 승인을 인증하지는 않는다. 8개 점검의 비공식 합성 기준 예시는 `examples/automation-policy.yaml`이다.

```text
guard prepare-validation --pack packs/PACK-BASELINE --env env.yaml --policy policy.yaml --config windows-cpu.toml
guard validate --pack packs/PACK-BASELINE --env env.yaml --policy policy.yaml --config windows-cpu.toml --store out/test-runs/cpu/store --output out/test-runs/cpu/run
```

준비 검사는 이미지를 받거나 엔진을 시작하지 않는다. 실행은 새 폴더에 증거와 JSON/HTML 보고서를 만들고, 자신이 만든 자원만 정리한다. 기존 컨테이너를 정지·삭제하지 않는다. 엔진은 한 번에 하나만 기동한다. CPU 성능은 UNKNOWN이다. 실제 출처 확인과 기업 정책 승인은 다르며 EXAMPLE/DRAFT는 공식 결과가 아니다.

자원 검사에서 확인하는 것은 Docker 전체 용량이다. 현재 여유 메모리·swap 가용량·다른 컨테이너 부하는 보장하지 않는다. 입력 모델 inventory는 기동 전에 확인하며, 호스트의 외부 변경을 막는 기능은 없다. 모델을 전후 불변이라고 보고하려면 종료 후 inventory를 별도로 다시 대조해야 한다.

## 새 점검의 지원 범위

| 점검 | 실제 자동 수집 | 남은 범위 |
|---|---|---|
| REQ-10 | 선정한 sampling 옵션 경계, 동시에 송신한 정상 요청, 후속 추론과 health | API 관측만으로 V2/runner/worker 최종 소비와 count 제한을 증명하지 않는다. 전체 보안은 해당 근거 부족 시 UNKNOWN |
| REQ-13 | 응답·안전한 오류의 필드, 금지 반환 옵션, engine/Gateway별 결과 | 모델·endpoint가 제공하지 않는 비공개 system 문맥이나 미시험 옵션은 UNKNOWN |
| OBS-03 | 선정한 원본·사본의 실제 Docker 로그, 시간 범위, 무해 표식·필드와 수집 상태 | 단순 4xx는 exception dump가 아니다. 미수집 dump/debug/trace·사본은 UNKNOWN |

미확인 분기가 있어도 별도로 확인된 위반은 FAIL을 보존한다. 예시 정책값은 기업 기본값이 아니며 정책 양식의 실제 값·책임자·승인은 사람이 정한다. 도구 밖에서 형성된 기업 신원·승인·자료의 진위를 자동 인증하지 않는다.

`declared/0.10.0`은 신규 3개 실행기와 고정 rule_table이 함께 선언된 정의의 수동 신고 PASS도 관측 판정과 대조한다. 누락·상충·규칙 오류는 해당 PASS만 UNKNOWN으로 보류하며 신고 FAIL/UNKNOWN과 과거 정의를 보존한다. 실행기의 송신·수집 예외도 이미 확인한 위반을 지우지 않는다. 인증키·표지된 자격 값은 상세 증거와 요약 로그 모두 저장 전에 정제하며 원문 캡처 digest와 정제된 파일 해시를 구분한다.

수동 신고에도 같은 제한 프로파일의 정책 의미 검증을 적용한다. 필수 목록의 빈값·중복·미지원 옵션·대상·분기는 신고 PASS를 UNKNOWN으로 보류한다. OBS-03의 필수 normal/error/exception_dump 분기를 빈 목록이나 coverage 플래그로 우회할 수 없다. 선택적으로 비워 둘 수 있는 금지 목록은 허용하며 신고 FAIL/UNKNOWN과 구형 정의는 유지한다.

## 기존 5개 점검 보강

정상·위반·수집 부족, 다른 수집 실패와 함께 존재하는 독립 위반, 증거 반출·복원·재판정을 검증한다. 기존 추론 정상 대조의 HTTP 200만 확인하던 경로를 보강해 실제 응답의 model·choices·finish_reason·usage를 검사한다. 빈 JSON·오류 객체·로그인 HTML은 정상 추론으로 인정하지 않는다. health·metrics 등 비추론 표본은 선언한 상태코드 기준을 유지한다. 후속 수집 예외가 이미 확인한 위반을 지우지 않는다. 기능 PASS는 이 응답 계약의 충족이며 모델 답변의 품질을 인증하지 않는다.

실제 사례 도구는 새 출력 폴더에서 제한된 세 구성으로 제품 명령을 실행한다. 기대 결과와 실제가 다르면 그대로 실패로 남기며 EVD나 판정 규칙을 맞춰 쓰지 않는다. 도구의 기대 대조는 보안 축이고 기능·성능은 기록한다. 최종 검증에서는 정상 대조·기능과 복원본의 재판정을 별도로 확인한다.

REQ-09의 작은 입력 정책을 넘는 요청 수용은 그 시험 정책 위반이며 메모리 손상 증거가 아니다. REQ-01의 격리 PASS는 외부 출발점의 TCP 차단과 신뢰 출발점의 정상 대조를 함께 요구한다. REQ-03은 선정한 등록 경로와 검증된 안전 프로브에 한정된다.

```text
uv run python tools/validate_cpu_cases.py --config windows-cpu.toml --env env.yaml --pack packs/PACK-BASELINE --output out/test-runs/cpu-cases-unique
```

세 구성은 노출·기본 권한·고정 canary·좁은 EXAMPLE 입력 정책, 격리·강화 권한, 같은 강화 구성의 매우 짧은 양수 관측 timeout이다. 각 구성에서 제품 CLI로 5개씩 실행하고 기대/실제 판정·증거 반출/복원·무결성을 기록한다. 매우 짧은 timeout이 언제나 UNKNOWN을 만든다고 가정하지 않고 실제 불일치를 남긴다. 준비/기동/정리 실패면 다음 엔진을 시작하지 않는다. 도구는 추가 컨테이너 삭제 명령을 갖지 않는다.

## B·C 후속 연결 조건

- PLT-03/05: 시험 저장소, 엔진의 실제 SDK/provider chain, 무해 객체와 권한·만료 자격.
- PLT-09/OBS-11: 실제 엔진 전송 기능, collector/sink, 시험 인증서, 수집·경보 장애 접점.
- SUP-01/02/14/LIF-15: 선정한 승인·배포 시스템의 API, 역할별 시험 계정, 변경 가능한 시험 공간과 허용표.
- OBS-06/10/12: 선정한 기록 저장소, 읽기/반출/관리 역할, 원본·사본·백업 조사 범위.
- REQ-15/16: 신원·문맥·key·실제 hit와 소비 요청을 연결할 계측. 지연만으로 캐시 격리를 판정하지 않는다.

## 구현 후 검증 기록

검증은 범위 전체 구현·테스트 작성·문서 초안을 마친 뒤 시작한다. 명령·환경·통과/실패/skip/제외와 실제 엔진 결과를 아래에 별도로 기록한다. 저장된 과거 Windows 실증은 이번 코드의 실행 결과로 합산하지 않는다.

### 실행 환경과 결과의 해석

직접 시험 환경은 Windows 11 x64, Intel Core Ultra 7 255H, 호스트 RAM 약 15.4GiB, Docker Desktop의 WSL2 Linux amd64(CPU 16개·전체 RAM 약 7.47GiB)다. Intel GPU는 사용하지 않는다. Python 3.11.16 64비트와 `uv sync --frozen`으로 별도 검증 환경을 설치했다. 공유 editable 환경이 다른 checkout을 가리킨 준비 실패는 엔진 실패와 구분한다.

- 실제 이미지: `vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b`, 관측 버전 `0.31.0`.
- 실제 모델: `facebook/opt-125m`, revision `27dcfa74d334bc871f3234de431e71c6eeba5dd6`, snapshot 7개 파일의 inventory `b11bc2cafab45720afd3d2f19c8250352977bc216efb88fc0afb9d90edc2aea6`.
- 실행 상한: CPU 2개, 스레드 2개, RAM 3GiB, RAM+swap 5GiB, shm 512MiB, PID 256, FP32, context/batch 128, 동시 sequence 1, KV 64MiB. 여유 자원을 보장하는 수치는 아니다.
- 기존 `bob-guard-lab-worker`·`bob-guard-lab-control-plane`은 계속 실행한다. 유료 자원이나 운영 시스템은 사용하지 않는다.

초기 실제 노출 구성은 5개 보안 FAIL을 관측했다. 이어 강화 구성은 준비 health 이후 첫 추론에서 Triton 라이브러리를 `/tmp`의 noexec 경로로 읽다가 실패했다. 캐시 이동 뒤 CPU driver의 별도 임시 `.so`도 같은 제약을 받았다. OOMKilled=false였으며 메모리 부족으로 단정하지 않는다. CPU 강화 프로파일의 `TRITON_CACHE_DIR=/guard-cache/triton`과 `TMPDIR=/guard-cache`를 기존 실행 허용 tmpfs로 고정하고 `/tmp` noexec·비root·읽기 전용 루트를 유지했다. 일반 임시 파일도 이 tmpfs에 생성될 수 있다. 최초 실패 산출물과 진단 SHA는 재시험으로 덮어쓰지 않는다.

`single|baseline|candidate/managed-diagnostic.json`과 SHA는 별도 실행 진단이다. 보고서의 상대 경로·SHA를 따라 함께 보관해야 하며 **EVD 첨부와 evidence bundle에는 자동 포함되지 않는다**. 엔진·Gateway의 본인 소유 label을 확인하고 상태 허용 필드와 종료 전 tail 80줄을 수집한다. 명령별 5초·컨테이너별 저장 로그 32KiB 제한과 알려진 키·표지된 자격 값 정제를 적용한다. 임의 개인 정보와 로그의 진위를 인증하지 않으며, 원시 단일 로그 줄의 수신 바이트 상한은 없다. 진단 실패가 기존 판정이나 실행 실패를 덮지 않는다.

최종 기본 테스트·실제 사례·새 실행기의 직접 결과는 아래 검증 표에 기록한다. 이 문서의 이전 실행 결과를 새 최종 결과에 합산하지 않는다.

### 단일 실제 엔진의 8개 점검

최종 보완 뒤 `guard prepare-validation` → `guard validate` → `guard verify` → `guard evidence export` → `guard evidence restore` → 복원본 `guard verify`를 별도 출력에서 실행했다. 8개 모두 COMPLETED/MEASURED이고 정상 대조 성공, 정리 오류 없음, 원본·복원본 무결성 오류 0개다. JSON·HTML 보고서가 생성됐으며 official=false다. CLI 종료 0은 실행 완료를 뜻하며 정책 전부 PASS를 뜻하지 않는다.

| 점검 | 보안 | 기능 | 성능 | 실제 관측과 한계 |
|---|---|---|---|---|
| REQ-01 | PASS | PASS | UNKNOWN | 격리 망의 비허용 출발점 TCP 차단 + 신뢰/허용 경로 정상 대조 |
| REQ-03 | PASS | PASS | UNKNOWN | 선정한 경로·역할과 검증된 안전 관리 프로브 범위 |
| PLT-01 | PASS | PASS | UNKNOWN | 강화 권한·읽기 전용 마운트·실제 추론 확인 |
| PLT-04 | PASS | PASS | UNKNOWN | 시험에서 선정한 자격 환경 이름·마운트 범위 |
| REQ-09 | PASS | PASS | UNKNOWN | token_ids 경계·허용 endpoint/형식·정상 추론 확인 |
| REQ-10 | UNKNOWN | PASS | UNKNOWN | temperature/top_p/min_tokens 잘못된 값 모두 400, 동시·후속 정상 표본 10개 성공, health 3개 200. 실제 runner 소비 검증 없음 |
| REQ-13 | FAIL | UNKNOWN | UNKNOWN | echo/logprobs/prompt_logprobs/token ID 실제 반환 및 시험 허용표 밖 필드 확인. return_prompt_text의 200만으로 효과를 단정하지 않음 |
| OBS-03 | UNKNOWN | PASS | UNKNOWN | stdout의 구조화 필드·요청과 연결된 normal/error/exception_dump·private system 미확인 |

REQ-13은 정상 기능 표본 4개가 모두 성공했다. 기능 UNKNOWN은 completions-only 모델에서 비공개 system 챌린지를 실행하지 못해 코어 INV-11이 PASS를 보류한 결과이며 정상 추론 실패가 아니다. 확인된 보안 위반은 이 미확인 분기와 별개로 FAIL을 보존한다. 이 FAIL은 EXAMPLE/DRAFT 시험 정책과 해당 엔진 구성의 차이이며 vLLM 전체의 취약점 판정이나 기업 정책 승인으로 확대하지 않는다.

세 새 실행기의 한계를 해소하려면 REQ-10의 실제 V2/runner/worker 소비 계측, REQ-13의 비공개 system 문맥을 지원하는 모델/endpoint, OBS-03의 요청과 연결되는 구조화 원본·사본 및 제어된 예외 dump 근거가 필요하다. 현재 결과를 PASS로 만들기 위해 미확인 분기를 삭제하거나 예시 정책을 바꾸지 않았다.

### 기존 5개 실제 사례 행렬

최종 사례는 새 `cpu-cases-r4` 출력에서 엔진 3개를 순차 기동했다. 각 구성의 5개 점검은 모두 COMPLETED/MEASURED이고 JSON·HTML·원본 verify·반출·복원·복원본 verify와 객체 동일성을 확인했다.

| 실제 구성 | 보안 결과 | 기능 결과 | 원래 보안 기대와의 대조 |
|---|---|---|---|
| 노출·기본 권한·고정 canary·입력 정책 8 | FAIL 5 | PASS 5 | 5/5 일치 |
| 격리·강화 권한·입력 정책 127 | PASS 5 | PASS 5 | 5/5 일치 |
| 강화 구성·매우 짧은 양수 timeout | UNKNOWN 4, PLT-01 PASS | UNKNOWN 4, PLT-01 PASS | 4/5 일치 |

15개 모두 성능 UNKNOWN이다. **행렬 명령은 원래 기대 14/15 일치로 종료 1이며, 이를 전부 통과로 표시하지 않는다.** PLT-01은 짧은 timeout에도 정상 대조와 필요한 관측을 충분히 얻어 PASS였다. 짧은 timeout이 반드시 수집 실패를 만든다는 시험 가정의 반례이며 제품의 잘못된 PASS가 아니다. 원래 expected-actual·summary·EVD·RES는 수정하지 않는다. 후속 읽기 전용 검증은 이 한 건의 정상 대조·필수 관측·챌린지와 저장 증거 재판정을 별도로 확인한다. 실제 UNKNOWN을 강제하기 위한 정책/관측 변조나 재실행을 하지 않았다. 5개 각각의 결정적인 수집 실패 경로는 합성 I/O 계약 테스트에서도 별도로 검증한다.

### 기본 테스트와 경계 보완

최종 명령은 별도 frozen 64비트 Python 환경에서 `python -X utf8 -m pytest -q --basetemp=.review-tests-phase1-final-r3 --junitxml=<외부산출물>/pytest-final-r3.xml --durations=15`이다. **1639 passed / 0 failed / 5 skipped / 18 deselected, 171.47초**였다. skip 5개는 기존 Windows 루프백 차이 3개·symlink 권한 2개, 기본 제외 18개는 Docker/engine 테스트다. 실제 엔진 시험은 위 별도 CLI 결과이며 이 기본 통과 수에 합산하지 않는다. Windows 기본 실행의 1분 목표는 충족하지 못했다. 지원 Linux CI는 커밋 후 별도 확인한다.

구현·테스트·문서 초안 전체 작성 뒤 검증을 시작했다. 최초 기본 실행의 15개 실패는 팩/버전/개수의 이전 기대와 정상 대조 fixture 구성 등이었고 테스트 의미를 완화하지 않고 고쳤다. 마지막 보완 중 전체 1638 passed/1 failed에서는 INV-14가 판정기의 실행기 모듈 import를 발견했다. 순수 목록 검증 함수를 `guard/automation_policy.py`로 옮기고 기존 실행기 import는 재수출로 호환시켰다. 함수 전후 바이트 SHA는 `98d9b2344ca2a15ee8b61115316cbd0a75ef16178e53da317ea4474c37cbd652`로 동일하고, 내용·오류·반환값과 실제 실행 의미는 바뀌지 않았다. 집중 경계/수동 상한 123개와 최종 전체를 다시 통과했다. 코어·모델·저장소 파일과 INV-14 테스트를 완화하거나 수정하지 않았다. 중간·집중 실행의 통과 수를 최종 전체 수에 더하지 않는다.

### 배포본·정합성·보존

`uv build --wheel` 후 별도 새 venv에 frozen 의존성과 wheel을 설치했다. 저장소 밖 cwd·Python `-I`·PYTHONPATH/PYTHONHOME 차단에서 **69개 패키지 파일이 소스=wheel=설치본으로 일치**했고, CLI 16명령은 정상 15개 exit 0·의도한 입력 오류 1개 exit 1로 기대와 일치했다. 정책 양식 38경로·신규 3개 import/서명·합성 EVD 판정·JSON/HTML·verify를 확인했다. 합성 설치 검증은 EXAMPLE이며 실제 엔진 결과와 합산하지 않는다. 최종 Windows wheel SHA-256은 `7b3186e946457b47228e7d1c6f53470e6b8b6c947ca85eaa094ad0c252a7dfaa`다.

팩17/DRAFT/77개 ID의 해시는 `1adbb9e624136a8985e72ca31f6606ebff5d5fdd34ad641c7903a27ec0e0b4d2`다. 다른 74개 CheckDef·기존 out/reference·core/models/store 등 보호 파일 176개의 바이트를 보존했다. 임시 팩 재생성 2회와 정책 metadata 18점검·38경로, 카탈로그 기존 303행 원문 prefix+새 투영 11행, 자동화 매트릭스의 기존 10필드·A47/B14/C16이 일치했다. CPU 모델 snapshot의 실행 전후 inventory와 기존 5개 컨테이너의 ID·상태도 모두 동일했다. 새로 만든 실행 자원은 정리했다.

15개 실제 EVD는 복원 store에서 저장 팩만으로 재판정하고 같은 시각으로 반복했다. 결과 의미 동일·멱등·원래 객체/Ref/첨부/보고서 보존과 원본 store 바이트 불변을 확인했다. 새 RES가 0개인 것은 기존 결과와 동일해 새 객체가 필요 없었다는 뜻이다. 예외가 없는 POL을 확인한 뒤 PLAN.created_at를 고정 판단 시각으로 사용했으며, 최초 실행의 판단 시각 자체는 별도 저장되지 않았다는 한계가 있다.

[선별 실증 요약 JSON](evidence/automation-phase1-20261023.json)에 실제 환경 신원, EVD/RES Ref, 원래 행렬의 14/15 불일치 상태, 재판정/보존 검사, 기본 테스트와 배포본 해시를 기록했다. 이 JSON은 전체 증거 bundle이나 독립 인증이 아니다. 전체 모델·DB·원본/정제 로그·정확한 로컬 명령·bundle은 저장소 밖 이번 phase output에 보존하며 요약에는 상대 경로·원본 요약 파일의 SHA만 싣는다. 다른 컴퓨터에 해당 원본이 자동 제공되는 것은 아니다.
