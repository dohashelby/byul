# 임시 입력을 사용한 실제 로컬 검증

사용자의 추가 요청에 따라 기존 모의 결과와 별도로, 설치된 코드·기존 소비자·자기 소유 파일/프로세스/IPC에 새 임시 입력을 연결한다. GPU 추가 기동, AWS 조작, 모델 다운로드 없이 수행한다.

**잔여18개 중14개에 새 실제 로컬 실행 근거를 추가했다.** 이는 각 컴포넌트의 명시한 범위이며 14개 질문 전체의 제품 검증 완료를 뜻하지 않는다. LIF-08·LIF-12·LIF-16·OBS-02에는 이번 새 실제 실행이 없다. 기존 모의18/18과 제품 증거가 더 필요한18개 분류는 보존한다.

## 실제 확인 결과

| 실행 범위 | 확인한 결과 | 확대해서 주장하지 않는 범위 |
| --- | --- | --- |
| 원 0.31 renderer·InputProcessor | 새 입력6건: 텍스트·직접 token-array·embedding/mask 수용, 음수 토큰·길이·mask 불일치의 원래 거부 | 재사용 요청 필드의 허용/금지 정책·0.31 엔진/worker 기동·최종 모델 소비 |
| 원 0.31 SamplingParams | 정상 구성과 범위 오류의 원래 거부 | 실제 scheduler/runner/Sampler·V2·구조화 옵션 최종 소비 |
| 원 0.31 SUP-08 사전 정책 함수 | None/빈 값·trust 설정에 따른 무해 JSON 옵션 수락/거부6건 | 실제 MM/media I/O·Hub/code_revision·최종 module 소비 |
| 별도 설치 0.29 CPU 엔진 | 새 tiny GPT2 모델로 정상 텍스트·토큰 배열 요청2건, 각각 출력 토큰2개·finished=true | 0.31/GPU 대상의 성공·운영 모델 적합성 |
| 공급물·OS 컴포넌트 | 최초11개 기록 중10개 조건 충족, host IPC 메타데이터1개 불충족 보존 | plugin hook·live worker 변경·GPU rank 회수 |
| 별도 자기 소유 PID 범위의 IPC | 같은 고정 probe의 메타데이터·연결·peer·정리4조건 충족 | 기존 host 검사 성공·host root·비허용 주체·엔진 IPC |
| SUP-16 기존 파일/순서 API | 새 파일의 정상2건·표준 거부2건, 실제 자식4개 종료 | 전체 writer.run/main·native emitter·worker FILE_READ |

실제 API/프로세스의 입력·반환·원 예외·출력·소스 SHA·종료 근거는 [검증 기록](local-actual-validation-20261108.json)에 원 receipt 해시로 연결한다. 이번 추가 자료는 컴포넌트 실행 근거이며, 원 제품의 정식 EVD/RES/RPT PASS로 계상하지 않는다.

원0.31의6사례는 수용3·표준 거부3이다. 정상 텍스트·직접 token-array는 renderer→InputProcessor, 길이17토큰의 거부는 renderer, 두 embedding 사례는 InputProcessor 직접 호출에서 관측했다. 원 case명 `new_reused_tokens`의 입력은 `prompt_token_ids:[8,9,10]`이며 kv/reused 요청 필드의 정책 검사가 아니다. 이벤트는 호출 위치를 확인하는 근거로 쓰고, 성공/거부는 실제 반환과 원 예외로 구분한다.

CPU 엔진은 LLM 호출 직전→Ready 표기 구간57.308초(생성자 반환 후 설정·프로세스 정보 수집 포함)를 관측했다. CPU 로그의 실효 KV cache는1GiB이며 요청한16MiB 값과 구분한다. worker의 정상 종료 뒤 엔진 내부 관리자가 EngineCore를 강제 종료한 로그를 보존했다. 부모 종료 후 같은 PID/start의 부모·tracker·Core·worker 모두 부재, 자기 소유 임시 경로 제거를 확인했으며 전체가 graceful이었다고 쓰지 않는다.

첫 CPU 엔진 시도의 긴 ZMQ 소켓 경로 오류, 첫 공급물 수집의 BatchEncoding 직렬화 오류와 host IPC 메타데이터 실패는 별도 원자료로 보존했다. 엔진 코드·원 검사·권한·기한을 완화하지 않았다. 원 receipt의 `unsupported_prompt_container`는 실제 OOV 토큰42의 거부였으며, 공개 도구의 향후 라벨만 `oov_prompt_token`으로 바로잡았다. 이 문자열 외 호출·입력·검사·정리 AST는 동일하다.

## 결과를 구분하는 기준

| 구분 | 실행 대상 | 결과의 의미 |
| --- | --- | --- |
| 기존 모의 18개 | 메모리 상태 모형과 모의 DRAFT 팩 | 정상·위반·누락54사례의 모의 계약 검증 |
| 실제 로컬 컴포넌트 | 설치 라이브러리, 기존 guard API, OS 파일·프로세스·IPC | 명시한 API/입력/프로세스 범위의 실제 실행 관측 |
| 설치 CPU 엔진 프로파일 | 기존 CPU용 vLLM 설치본과 새로 생성한 작은 테스트 모델 | 해당 설치 버전의 실제 CPU 엔진 실행 결과 |
| 선정 제품/GPU 환경 | 원래 대상 버전·실제 worker/rank·정책·전체 소비 | 이 로컬 검증으로 대신하지 않는 후속 검증 |

임시 입력과 예제 정책은 synthetic/EXAMPLE로 표시한다. 실제 API를 실행했다고 해서 기업 정책 승인이나 원래 질문의 모든 분기를 확인한 것으로 세지 않는다. 기존 FAIL/UNKNOWN, 실제 GPU 실행 신원·기한·비용 기록과 25/18 분류는 보존한다.

## 실행 환경의 구분

최초 확인한 Windows venv와 WSL 기본 Python에는 vLLM·torch가 없었다. 추가로 기존 WSL 환경에서 실제 CPU용 vLLM 배포를 확인했다. 기본 인터프리터의 부재를 기계 전체의 부재로 일반화하지 않는다.

- 원래 대상 source checkout: `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 0.31 계열. 기존 원 source/snapshot 검사를 완화하지 않는다.
- 별도 설치 CPU 프로파일: Python3.12.3, vLLM `0.29.1rc1.dev422+gd05da62e9.cpu`, torch `2.13.0+cpu`, transformers `5.17.0`.
- 0.29 설치본의 결과를 0.31 대상의 성공으로 소급하지 않는다. 실제 import 경로·소스 SHA·버전과 입력/출력·예외·종료·정리는 실행 receipt에 남긴다.

0.31 소스 API 경로는 실제로 실행됐지만 `_C_AVX2`·`triton-cpu` 관련 원 경고를 보존한다. 이 결과를 정식0.31 CPU 엔진 설치·worker 실행 완료로 취급하지 않는다.

## 항목별 실제 범위와 잔여

| 항목 | 이번 실제 로컬 관측 | 실제 제품에서 남은 연결 |
| --- | --- | --- |
| REQ-08 | 원0.31 텍스트/직접 token-array admission, 실제 CPU 엔진의 토큰 요청 출력 | 재사용 요청 필드의 정책·원0.31 worker 최종 소비·허용 주체/문맥 |
| REQ-09 | 원0.31 토큰·길이·embedding/mask 검사, 실제 CPU 엔진의 정상 요청 | 선정 MM 모델/processor·입력 계약·worker 소비·정상 대조 |
| REQ-10 | 원0.31 SamplingParams 정상/범위 거부, CPU 추론에 sampling 인자 제출 | 원0.31 scheduler/runner/Sampler·V2·구조화 옵션의 최종 소비 |
| SUP-03 | 실제 정책 검증·Jinja·HF tokenizer/chat template | 승인 공급물 전체 inventory·독립 worker 파일 선택/소비 |
| SUP-05 | 실제 manifest 소비·FD 읽기·safetensors 저장/적재 | 승인 권한·pre-read causal-root·공식 scope·worker/FD 귀속 |
| SUP-06 | 실제 기록 consumer·vLLM safetensors iterator/default loader | 필수 tensor/shard/rank 배정·GPU TP2 적재·누락/복구 |
| SUP-08 | 원0.31 trust-MM 옵션의 사전 수락/거부 함수 | 실제 Hub/code 판본·요청 필드·최종 module 소비·과거 원본 |
| SUP-09 | 실제 vLLM built-in config 정상/표준 거부 | 승인 model-code·model-entry/forward의 실제 소비 |
| SUP-10 | 실제 selector의 명시적 전체 비선택, hook 실행0 | 승인 plugin 선택부터 hook·runner/후처리 연결 |
| SUP-11 | 실제 Python `-I`·`-I -O`의 built-in loader | 원0.31/선정 모델의 지원 모드·기존 `-OO` 문제·worker 소비 |
| SUP-16 | 고정 기존 writer의 read_json/digest/ordering API | 전체 writer·native emitter·worker 읽기·제품 factory |
| PLT-02 | host 부분 관측 및 자기 소유 PID 범위에서 같은 probe4조건 충족 | 실제 엔진 IPC inventory·주체·명령 소비·TP 범위 |
| LIF-06 | 실제 layerwise helper와 표준 torch layer fixture | live 요청·weights/cache/replica 세대·실패/복구 |
| LIF-11 | 실제 정상 자식 종료·pipe capture 정리 | 실패 worker·IPC/SHM/GPU 자원 회수·정상 재기동 |
| LIF-08 | 새 실제 실행 없음 | GPU rank 장애·알림·통신·복구·과거 원자료 |
| LIF-12 | 새 실제 실행 없음 | 실제 retry/attempt/비용 producer·예산 정책·과거 원자료 |
| LIF-16 | 새 실제 실행 없음 | 실제 승인 backup/volume/설정·복원/접수 producer·과거 원자료 |
| OBS-02 | 새 실제 실행 없음 | 실제 API/Core/worker/backend 상태 producer·과거 원자료 |

PLT-02의 원 host 기록은 `IPC_METADATA_UNAVAILABLE/complete=false`이며 원 실행의 숨겨진 예외 자체는 미관측이다. 별도 PID·시각의 재현에서 `/proc/1/fd` PermissionError를 직접 관측했다. 추가 정상 시험은 이미 허용된 unprivileged user/PID/mount namespace에서만 수행했다. host UID1000은 그대로이고 새 namespace의 UID0은 `0 1000 1` 매핑이다. host 권한·sysctl·AppArmor·공유 mount를 바꾸지 않았으며 새 network namespace나 host root를 주장하지 않는다.

## 재현 도구

아래는 Linux/WSL에서 이미 준비된 Python3.12 환경을 사용하는 대표 명령이다. `CPU_PYTHON`은 검증된 별도 설치 CPU 프로파일, `SOURCE_PYTHON`은 원0.31 소스 의존성 환경이다. 도구는 패키지나 모델을 다운로드하지 않으며, 각 출력은 새 경로여야 한다.

```sh
"$CPU_PYTHON" -I -B "$REPO/update/tools/gpu18_local_supply.py" \
  --source-root "$REPO" --output "$OUT/supply"
"$CPU_PYTHON" -I -B "$REPO/update/tools/gpu18_local_supply_namespace.py" \
  --source-root "$REPO" --output "$OUT/ipc-owned-scope"
"$CPU_PYTHON" -I -B "$REPO/update/tools/gpu18_local_input_engine.py" \
  --output-dir "$OUT/cpu-engine"
"$SOURCE_PYTHON" -I -B "$REPO/update/tools/gpu18_local_input_context.py" \
  --guard-root "$REPO/update" --vllm-source "$VLLM031" --output-dir "$OUT/source-context"
"$SOURCE_PYTHON" -I -B "$REPO/update/tools/gpu18_local_input_mm_kwargs.py" \
  --source-root "$VLLM031" --source-sha256 67ede411b35a8344ddb87bfaaf1a1abea8acd39a4d23655e34032015505167c0 \
  --output-dir "$OUT/mm-policy"
```

현재 host 공급물 도구의 전체 종료1은 IPC 메타데이터 불완전1건을 반영한다. 성공으로 바꾸지 않고 별도 자기 소유 namespace 결과와 함께 읽는다. namespace가 허용되지 않는 환경에서는 거부 사실을 남기며 host 정책을 변경하지 않는다.

SUP-16의 이번 대상 함수는 [기존 공개 writer snapshot](implementation-snapshots/sup16-write_normal_boot.py.txt)과 SHA `2485ba44ce8398f833477b79a04c86624cc254316b566f399a4bc9b5f085e4bd`가 일치한다. 새 입력 생성은 전용 harness가 수행했으며 이 snapshot의 전체 writer 실행을 주장하지 않는다.

## 남은 작업의 분류

GPU가 없다는 이유로 모든 미확인 항목을 GPU 대기로 분류하지 않는다.

- GPU/rank 관련 선정 분기: SUP-06의 GPU TP2 shard 적재, LIF-08의 GPU rank 장애, LIF-11의 GPU worker 자원 회수 등은 해당 환경 관측이 남는다.
- 실제 엔진/생산자 연결: LIF-12·LIF-16·OBS-02는 GPU가 본질적 필수 조건은 아니지만, 현재 원 질문에 맞는 실제 retry/restore/state 생산자·환경·정책 연결이 필요하다. guard bundle 복원을 서빙 엔진의 복원 증거로 세지 않는다.
- 공급물·코드·플러그인: 실제 loader/renderer/worker의 선택·소비, 지원 Python 모드, 승인 inventory와 명시한 입력 범위가 필요하다. 단순 import나 예제 기록 parser의 수락을 전체 소비로 확대하지 않는다.
- 과거 원자료: LIF-08·12·16, OBS-02, SUP-08의 과거 원본 미확인은 그대로다. 새 로컬 시험은 과거 원본 복구를 의미하지 않는다.

기존 모의 산출물·실행 방법은 [GPU18_PROTOTYPE.md](GPU18_PROTOTYPE.md), 원 제품 입력 요구는 [DEPENDENCY_RETURNS.md](DEPENDENCY_RETURNS.md)를 따른다.
