# SUP-12 메시지 해석기 CPU 분기

기준 main `4af4d31bc951bc4a6e8bfefe4381e63e5305075c`, CheckDef SUP-12 revision 3.
공유 coordination 기록에서 B-1의 SUP-10과 겹치지 않는 decoder 분기를 선점했다.
PR47의 응답·로그 수정은 그 PR에 유지하고 이 변경은 독립 브랜치에서 검토·통합할 수 있게 분리한다.
PR47·PR46/49의 소스나 미완성 모듈을 import하지 않는다.

## 공식 시험과 추가 공백

인계 공식 매핑 SERIAL은 `5d35ce3934a7804b298559d2d99eec33792e1eb9`의
`tests/v1/test_serial_utils.py::test_custom_class_serialization_disallowed_without_pickle`이다.
기본 **encoder**가 custom class를 TypeError로 거부하는 범위이며 decoder 공격 바이트 소비는 아니다.
encoder assertion을 새 실행기로 복제하지 않는다. 공식 값은 기업 정책에 등록하지 않는다.

이번 고정 참조는 vLLM v0.31.0 `db9527a46873454610df6dbedf79a36d6bf1a7f6`이다.
태그의 실제 decoder·env getter·공식 encoder 시험 bytes를 `source-pin.json`에 고정했다.
매핑 판본과 태그 판본의 동일성을 가정하지 않으며 설치판은 미정이다.

CHK의 추가 요구는 실제 선택 reader·금지 동작·정상 대조·오류 뒤 상태·증거와 판정 연결이다.
기존 BYUL에는 SUP-12 executor/judge가 없으며 일반 IPC 접근 제한만으로 decoder 안전을 증명하지 않는다.
새 실행기 등록 대신 전용 수집/정책 관측 helper와 실제 참조 실행 도구만 추가했다.

## 실제 CPU 범위

실제 `MsgpackDecoder` 정의·상수·bytestr alias·warning 함수를 원래 AST로 실행한다.
실제 msgspec msgpack decoder가 ext_hook을 호출하며 실제 pickle/cloudpickle 라이브러리를 사용한다.
환경 flag는 고정 envs.py의 원래 getter를 실행해 이 자식 프로세스의 unset/1을 대조하고 복원한다.
Tensor/typed multimodal 경로는 선택하지 않는다. 해당 모듈의 heavy imports와 전체 엔진은 실행하지 않는다.
PIN_MEMORY=False는 이 untyped 분기에서 사용하지 않는 dependency 값이다.
warning_once는 표준 logger의 warning으로 연결하는 로그 전용 adapter다.

정상 native 메시지, pickle/cloudpickle 확장, raw view, 알 수 없는 확장, 손상/잘린 메시지를
설정 두 가지에서 각각 소비한다. 각 사례는 같은 decoder의 정상→도전→후속 정상으로 확인한다.
최종 aux_buffers 참조 해제는 관측하지만 GPU/OS 메모리 회수나 즉시 buffer 수명 전체를 주장하지 않는다.

무해 reducer는 로컬 메모리 카운터만 증가시킨다. 파일·통신·명령 payload를 만들거나 실행하지 않는다.
CPython audit의 pickle.find_class/open/socket.connect/subprocess.Popen/os.system 사건 종류와 개수만
관측하고 args·경로·객체·예외 원문은 저장하지 않는다. OS 전체 tracing이나 모든 부수 동작의 부재가 아니다.
flag enabled에서 callback 실행은 명시 opt-in 대조 관측이며 기본 설정의 결함이라고 보고하지 않는다.

기업의 허용 객체·금지 부수 동작 정책은 미정이다. 도구의 enterprise_assessment는 정책 부족을 남긴다.
별도 example_assessment의 callback 금지는 EXAMPLE 회귀 정책이다. 확인된 금지 동작은 이후 오류/정상
실패로 지우지 않는다. 측정 누락을 0으로 채우지 않고, payload·반환 객체·예외 원문 대신 hash/코드만 남긴다.

## 재현과 연결

```sh
uv venv --python 3.12 /path/to/b2-evidence/decoder-venv
uv pip install --python /path/to/b2-evidence/decoder-venv/bin/python \
  msgspec==0.19.0 cloudpickle==3.1.2 pyzmq==26.4.0
PYTHONPATH=src /path/to/b2-evidence/decoder-venv/bin/python tools/b2_decoder_cpu.py \
  --source /path/to/vllm-reference --output /path/to/new-decoder-evidence.json
uv run pytest -q tests/test_serialization_decode.py
```

수집 입력: 선택된 실제 decoder, 단일 frame·정상 fixture, 제한된 effect counter, 알려진 거부 예외.
출력: 도전 수행/거부/오류·정상 전후·관측한 effect delta·gap·fixture hash. 원문은 없다.
정책 입력: 명시한 forbidden_effects. 미입력은 UNKNOWN 자료이고 빈 목록은 명시적으로 다른 의미다.
`override-proposal.yaml`은 기존 rule_table을 재사용하는 **미적용 제안**이며 전체 점검 PASS 규칙은 없다.
실제 reader 신원/target/policy와 연결된 독립 위반만 FAIL에 사용할 수 있다.

A 연결 요청: 고정 통합 SHA·등록/팩/CLI API, 실제 consumer와 decoder 대응·관측 신원·정책 Ref가 필요하다.
그 소스에서 실행→EVD→RES/RPT를 확인해야 제품 연결을 마감한다. 공통 파일은 수정하지 않는다.
자동 파일 형식 선택/fallback·weights daemon peer/UID·typed tensor·실제 모델 reader는 남은 별도 분기다.
계측/생산자/정책이 없어 이를 “실제 실행만 남음”으로 표시하지 않는다.

## 같은 직렬화 기능의 REQ-14 공식 시험 재사용

다음 묶음의 main/A/B/다른 CHK 대조는 `REUSE_REVIEW.md`에 짧게 기록했다.
공식 encoder 거부, payload buffer 재사용, caller 참조 해제 뒤 zero-copy frame 수명 시험을
원래 입력·assertion으로 실행한다. 실제 vLLM codec·EngineCoreOutputs·EngineCoreProc 모듈과
CPU Torch/NumPy/PyZMQ를 import한다. target 클래스·sender 메서드·타입 schema를 대역으로 교체하지 않는다.
source pin·import 경로·CPU-only Torch를 확인하고 upstream conftest/engine fixture는 불러오지 않는다.
엔진 생성자는 선택 시험에서 호출하지 않으며 소유한 로컬 inproc context는 원래 시험이 정리한다.

새 실행기/판정기 대신 `b2_message_cpu.py`의 최소 시험 adapter만 추가한다. 수집 실패/skip/
후속 process 실패는 성공으로 보고하지 않으며, 먼저 얻은 카운터는 보존하고 누락은 null이다.
raw pytest 진단과 request/output/Tensor 값은 메모리에서만 처리한다.
로그에는 검증된 카운터·시간·소스 metadata만 저장한다.

이 시험은 단일 프로세스의 실제 codec/transport 동작이다. 침해 worker·peer 권한·NCCL·PD 전달·
실제 GPU 소비나 REQ-14 전체를 증명하지 않는다. fixture의 규모는 기업 정책·예산이 아니다.
REF 판본과 설치판의 호환성은 별도다. 제품 의존성/uv.lock에는 시험 패키지를 넣지 않는다.

```sh
uv venv --python 3.12 /path/to/b2-evidence/message-venv
uv pip install --python /path/to/b2-evidence/message-venv/bin/python \
  torch==2.14.0+cpu --index-url https://download.pytorch.org/whl/cpu
uv pip install --python /path/to/b2-evidence/message-venv/bin/python \
  -r docs/b2-decoder/message-cpu-requirements.txt
PYTHONPATH=src:. /path/to/b2-evidence/message-venv/bin/python tools/b2_message_cpu.py \
  --source /path/to/pinned-full-vllm-source --output /path/to/new-message-evidence
```

명세의 requirements는 이 CPU 검증 환경의 고정 기록이며 제품 설치 목록이 아니다.
직접·간접 test dependency를 별도 가상환경에 설치했고 기존 시스템/다른 세션 환경은 바꾸지 않았다.
