# SUP-12 typed Tensor/ndarray reader CPU 관측

0.31.0 고정 소스 `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 실제 full
`MsgpackEncoder`/`MsgpackDecoder`를 사용했다. 기존 untyped 14사례와 REQ-14 공식
message lifetime 3개 결과는 재사용한다. 이번 추가 범위는 CPU Tensor/ndarray의
기본 `share_mem=True`, inline/aux frame 입력과 Python reader의 aux 참조 정리다.

기존 `tools/b2_decoder_cpu.py`에 `--profile typed`를 추가했다. 기본값은 `untyped`이며,
기존 verify_source/source_scope/run 함수의 AST와 message helper API는 유지한다.
새 실행기·제품 의존성·공통 registry/CLI/팩·판정기는 추가하지 않았다.

## 수집과 입력

실행 전에 기존 B1 자기 checkout/guard bytes 고정 gate와 B2 source pin을 검증한다.
full 모듈은 기존 strict source archive/bytes/ignored 파일 검사도 적용한 뒤 import한다.
실제 encoder/decoder 클래스의 import 파일이 해당 source의 serial_utils.py인지 확인한다.

원문 `tests/v1/test_serial_utils.py`의 다음 세 함수 정의를 AST 그대로 선택하고 실제
torch/numpy/encoder/decoder를 전달해 먼저 실행한다. 원문 입력·assert를 수정하지 않는다.

- test_dict_serialization
- test_tensor_serialization
- test_numpy_array_serialization

세 정상 대조가 성공한 뒤 작은 float32 2×2 Tensor/ndarray 각각에 아래 12사례를 실행한다.
size_threshold 256/0은 같은 16-byte fixture의 inline/aux 분기를 선택하는 시험 설정이다.
기업의 허용 크기나 한도로 사용하지 않는다.

| 사례 | 소스에서 확인한 입력·분기 |
| --- | --- |
| inline-normal / aux-normal | 실제 encoder가 만든 정상 inline/aux frame |
| dtype-unknown | descriptor dtype 이름 해석 |
| shape-mismatch / shape-infer | descriptor shape의 reshape/view 처리 |
| descriptor-missing-field | dtype·shape·data 3필드 unpack |
| aux-missing / aux-index-outside / aux-index-negative | data 정수의 aux_buffers 인덱스 처리 |
| aux-truncated | 실제 backing frame의 1-byte 잘림 |
| main-truncated | descriptor frame의 1-byte 잘림 |
| empty-frame-list | frame 목록의 첫 frame 접근 |

각 사례는 같은 typed decoder에서 정상 decode → 해당 입력 → 정상 decode 순서로 실행한다.
정상 결과는 실제 값과 torch.equal/np.array_equal로 비교하되 값 자체는 저장하지 않는다.
각 단계의 outcome·예외 종류·decoder.aux_buffers==()를 기록한다. 저장 데이터는 고정
case ID, frame 경계 포함 SHA256, frame bytes/count와 위 관측으로 제한한다.

형상 추론과 음수 aux 인덱스의 수용을 포함해, 명시적인 금지 계약이 없는 수용·예외는
관측으로 보존한다. 새 기업 정책·취약점·보안 PASS 판정은 만들지 않는다.
aux_buffers==()는 decoder의 Python 참조 정리 관측이며, 반환 Tensor/ndarray의 backing
buffer 수명·OS 메모리 반환·GPU/worker 자원 회수와 구분한다.

## 실행 결과 — 2026-10-29

- 직접·인접 회귀: **50 passed**, pytest 0.36초. 기존 untyped 함수 AST 동일성과
  기본 선택, message 요약, typed 예외 뒤 정상 처리·raw 값 미저장·aux 참조 관측 포함.
- 실제 공식 정상 대조: **3개 통과**, 원문 assert 그대로 실행. pytest 전체 모듈 실행과
  구분하며 엔진 repository conftest를 로드하지 않는다.
- 실제 typed **24사례 완료**: decoded 8, exception 16. exception 종류는 AttributeError 1,
  RuntimeError 2, ValidationError 5, IndexError 6, DecodeError 2였다.
- Tensor/ndarray의 inline-normal, aux-normal, shape-infer, aux-index-negative가 decoded였다.
  나머지 16개는 해당 Python 예외를 관측했다. 각 사례 전후 정상 대조 48건 모두 값이
  일치했고, 모든 사례·대조 뒤 decoder aux_buffers가 비었다.
- import부터 전체 typed 수집까지 부모 벽시계 **5.85초**. 기업 정책 UNSET,
  worker_or_gpu_reclamation_verified=false를 유지한다.
- Python 3.12.13, torch 2.13.0, numpy 2.3.5, msgspec 0.22.0, pyzmq 27.2.0.
  원문 CPU 의존성 148개 freeze와 pytest 추가 후 151개 snapshot은 기존 0.31 전용 환경
  자료를 재사용했다. 이번 작업에서 의존성이나 제품 lock을 변경하지 않았다.

결과 원본: `/private/tmp/byul-sup12-typed-cpu-20261029-1.json`.
측정 도구 bytes SHA256:
`0b2c203f68a6f93849b34a6e431d171f0f53db81f6c6be40a9d9c700baf167e1`.
원문 입력·decoded tensor/array·예외 문자열은 결과에 저장하지 않았다.

실행 중 버전 메타데이터 누락, host FFmpeg/opencv AVF class 중복, 읽기 전용 buffer의
Torch view 경고를 관측했다. 패키지·원문 소스·assert를 바꾸지 않고 실행을 완료했다.

```sh
VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="src:.:$VLLM_SOURCE" "$CPU_VENV/bin/python" tools/b2_decoder_cpu.py --profile typed --source "$VLLM_SOURCE" --output "$NEW_EVIDENCE_JSON"
```

모델 파일 reader·fallback·weights daemon peer, OOB tensor provider,
share_mem=False 복사 경로, 실제 모델·worker·GPU 소비는 이번 관측에 포함하지 않는다.
설치판/consumer 신원·기업 정책과 A 제품 연결은 기존 SUP-12 인계 항목으로 남긴다.

동일 151개 의존성 환경에서 기존 기본 untyped 경로도 추가 smoke를 수행했다.
`/private/tmp/byul-sup12-untyped-smoke-20261029-1.json`에 14사례·기존 assertion 통과,
전후 정상 대조·aux 참조 정리, 원문 canary 미저장을 확인했다(0.097초).
msgspec 0.22.0/cloudpickle 3.1.2/pyzmq 27.2.0이며 기존 환경과의 의존성 차이에 대한
직접 확인이다. 기존 untyped 수집 코드와 사례를 바꾸지 않았다.

## 검토 수정과 재검증

기존 공식 함수 helper는 부모 Python의 최적화 설정을 상속해 `-O`에서 원문 assert가
생략될 수 있었다. 공식 AST 컴파일에 `optimize=0`을 명시했고, typed 실행은
`sys.flags.optimize`가 켜져 있으면 source·target 실행 전에 거부한다. 실제 `-O`
subprocess에서 원문의 실패 assert가 AssertionError로 관측되는 회귀와 `-O`/`-OO`
typed CLI의 사전 거부 회귀를 추가했다.

기존 untyped 세 함수의 동일성 검사는 승인 기준 bd491e8에서 지원 Python 3.12로
산출한 AST dump(include_attributes=False) SHA256 세 개를 검토된 fixture로 고정했다.
CI는 이 fixture와 현재 함수 AST를 대조하므로 얕은 checkout에서도 Git 과거 이력을
조회하지 않는다. vLLM source 신원과 파일 bytes는 기존 source pin 경계에서 검증한다.

수정 묶음 직접·관련 회귀 **55 passed**, pytest 1.29초. 소스 고정 후 typed CPU를
한 번 재실행해 원문 공식 정상 대조 3개·24사례·전후 대조 48건을 다시 완료했다.
결과 `/private/tmp/byul-sup12-typed-cpu-20261029-2.json`, 부모 벽시계 **7.35초**,
aux 참조 정리 전부 확인. 새 도구 bytes SHA256은
`110c0fd7090daba0ea22334004161dfec5fefe18dce119379c32508b55f3e8fc`다.
기존 untyped 함수 AST/default 동작과 공식 message 3개 결과는 그대로 재사용한다.

CLI 진입점도 typed/untyped 모두 최적화 모드를 선행 거부하도록 연결했다. 기존
untyped 세 함수와 기본 선택은 유지한다. 두 profile × `-O`/`-OO` subprocess 회귀를
포함한 관련 시험은 **57 passed / pytest 0.96초**다. 최종 소스 고정 뒤 typed 24사례를
한 번 재실행해 공식 대조 3개·전후 대조 48건·aux 참조 정리를 확인했다(**6.47초**).
최종 결과는 `/private/tmp/byul-sup12-typed-cpu-20261029-3.json`, 도구 bytes SHA256은
`a2fe73872fc90b19a708dda2a0eac3fcd52341ad0ada556e2b515062c993d63d`다.
기존 untyped 14사례의 실제 결과는 함수 AST가 같은 앞선 smoke를 재사용한다.
