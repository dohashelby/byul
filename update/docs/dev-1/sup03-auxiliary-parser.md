# SUP-03 Python 보조 공급물 실제 읽기 지원 (A측, B 의존 반환 4의 부분 구현, 2026-11-08)

이 문서는 최초 parser 지원 범위의 기록이다. 같은 날 실제 boot·요청의 config 내용 일치와 tokenizer 복사 계보까지 추가한 결과는
`consumption-links-cpu.md`를 따른다. native Rust 읽기·집행의 기존 미확인 경계는 유지한다.

기존 safetensors 전용 `model_read_scope`·native17과 관리 template의 API projection을 유지하고 별도 지원 계약을 제공한다.
**전체 보조 공급물·worker 소비 반환이 완료된 것은 아니다.**

| 반환물 | 범위 |
|---|---|
| 계약 | `guard-auxiliary-parser-input/1`, 생산자 `guard-auxiliary-parser@1`; 관측기·생산자 파일 bytes SHA 결합 |
| 관측기 | `adapters/vllm_auxiliary_read.py`, 세 모듈의 실제 source SHA와 함수 코드 일치 시 module-local `open`만 관측 |
| 수집·소비 | `collectors/auxiliary_supply_producer.py`; `tools/auxiliary_supply_capture.py`; `project()` |
| 대상 source | transformers 5.17.0 `configuration_utils` SHA `02826ac9…`·`tokenization_utils_base` SHA `e7eb23b7…`, vLLM 0.31.0 `chat_utils` SHA `4d442ec6…` |
| 실제 지점 | `PreTrainedConfig._dict_from_json_file`, `PreTrainedTokenizerBase._from_pretrained`, `_load_chat_template` |

## 실제 읽기와 로더 반환

원 로더가 호출한 파일 `read()`의 반환 데이터를 그대로 돌려주고 parser 입력 bytes SHA를 기록한다.
같은 FD의 `pread`로 파일 bytes SHA를 따로 기록하며 inode·크기·mtime·ctime의 읽기 전후/경로 일치를 확인한다.
문자열 개행 변환이 있으면 파일 bytes와 parser 입력 SHA는 달라질 수 있다. 원문 데이터는 결과에 넣지 않는다.
읽기·열기 예외는 원 예외를 유지하고 관측 실패는 원 성공 값을 바꾸지 않는다. `builtins.open`은 수정하지 않는다.

실제 AutoConfig 생성·AutoTokenizer 생성과 encode·vLLM template 로더의 반환 해시 및 그 호출 구간의 읽기 참조를 묶는다.
관측은 공급 허용 목록을 집행하지 않는다. 미입력 목록은 정책 미정이고 명시적인 빈 LAB 목록은 허용 항목 0개다.
수용된 로더에 연결된 안정된 읽기가 명시 목록 밖이면 `auxiliary_parser_read_outside_allowlist` 후보를 남긴다.
후속 수집 오류가 이 후보를 지우지 않는다. 기업 정책 FAIL/PASS를 이 helper가 확정하지 않는다.

## k3d 실제 CPU 확인

기존 CPU 이미지 digest `045fb648a61cfcd32d4b9ca5d0c391013afadae0178d2e23ac292597f2f64036`와 읽기 전용
`/models/opt-125m`·고정 template을 별도 임시 Pod에서 사용했다. 실제 출력은
`tests/fixtures/auxiliary_supply_k3d_20261108.json`, EXAMPLE이다. 새 다운로드·제품 의존성·AWS 작업은 없다.

config 651bytes, tokenizer 설정 685·441bytes, template 347bytes의 읽기를 각각 성공한 로더 결과에 연결했다.
AutoTokenizer가 내부에서 다시 읽은 config 행도 원 기록에 보존했다. 최초 capture는 config 함수의 실제 qualname
`PreTrainedConfig`와 alias `PretrainedConfig`를 혼동해 config 읽기가 빠졌고 오류가 남았다. 원 capture-v1은 외부 산출물에 보존했고
실제 코드 이름을 바로잡은 capture-v2/v3에서 세 parser 역할을 확인했다. 소비 규칙을 완화하지 않았다.

## 확인과 남은 경계

직접·소스 고정 회귀 28개 통과(pytest 0.70초, 부모 0.848초). 실제 행→생산자→소비자, 명시 목록·미입력,
source/session/PID/읽기 불안정/다른 반환 참조/중복·형식 오류, 후속 오류 뒤 후보 보존, 개행별 두 해시,
관측 실패에도 원 read 반환 보존을 확인했다. 전체 suite·Linux CI·GPU 결과는 이번 근거가 아니다.

`parser_scope_complete`는 이 세 Python 로더 구성요소의 수집 범위다. `native_worker_consumption_verified=False`,
생산자 인증 False를 유지한다. Rust tokenizer의 vocab/merges 실제 읽기, admitted worker/PID·rank와 그 공급물의 후속 모델 소비,
캐시 적중·변환 이후 소비, fresh engine boot의 승인 제외·실제 집행은 남아 있다. 기존 `supply-reads`의 inotify 귀속 공백도 해소하지 않았다.
공통 GPU 생산자 등록·B 소비자·기존 SUP-03 CheckDef/판정은 수정하지 않았다.

B는 기존 증거와 별도로 원 parser 입력을 `project()`에 넘기고 정확 source·세션·프로세스를 유지한다.
실제 공급 승인 inventory를 전달할 때 파일 bytes SHA와 parser 입력 SHA를 혼동하지 않는다. worker 소비·인가가 없는 행으로 전체 SUP-03 PASS를 만들지 않는다.
