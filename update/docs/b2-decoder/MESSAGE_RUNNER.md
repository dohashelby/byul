# CHK-REQ-014 / REQ-14 공식 message CPU runner 수정

`b2_message_cpu.py`의 child PYTHONPATH를 자기 update → 자기 update/src → target source
순서로 바꿨다. target의 `tools/__init__.py`가 자기 tools 패키지를 가리던 충돌을 해결한다.
이전 재현은 tests=0/error=1/NOT_PASSED였으며 원본은
`/private/tmp/byul-codec-runner-repro-20261029/summary.json`에 있다.

child harness는 import한 `b2_message_cpu.py`의 파일 identity를 parent의 tool과 대조한다.
parent와 child 모두 `verify_source` 함수가 자기 `b2_decoder_cpu.py`에서 왔는지도 확인한다.
기존 verify_source의 source pin·HEAD·bytes·상태 검사 의미는 유지했고 해당 파일은 수정하지 않았다.
실제 MsgpackEncoder/Decoder·EngineCoreProc·EngineCoreOutputs가 pinned target 내부에서
import됐는지 확인하는 기존 검사도 유지했다.

원문 NAMES·세 시험의 assertion·safe_summary의 3건 성공 조건·실패/skip 보존·stdout privacy는
그대로다. 원문 선택 정의를 실행하며 모듈 mock·Mac 전용 우회·새 skip을 추가하지 않았다.
엔진 constructor·모델·외부 endpoint·GPU를 사용하지 않고 원문 시험의 local inproc ZMQ와
실제 CPU tensor/직렬화·메시지 수명 경로를 실행한다. fixture origin은 EXAMPLE이다.

## 직접 검증

- 전용 계약 **12 passed / pytest 0.08초 / 부모 wall 0.24초**.
  합성 target/tools에 충돌 패키지와 동명 module을 만들고 실제 subprocess에서 자기 tool과
  target의 vllm module이 함께 선택되는지 확인했다. 경로 배열 순서만 비교하는 회귀가 아니다.
  다른 checkout에서 가져온 decoder helper도 거부한다.
- 실제 v0.31.0 source `db9527a46873454610df6dbedf79a36d6bf1a7f6`의 원문 공식 시험은
  **3 passed / failed=0 / error=0 / skipped=0 / pytest 7.8607초 / 부모 wall 9.02초**였다.
  macOS의 기존 exact CPU 환경에서 EngineCoreProc를 포함한 실제 target import가 완료됐다.
  설치·환경 변경은 하지 않았다.

첫 import 충돌 수정의 원본 결과는 `/private/tmp/byul-codec-message-runner-20261029/run-1/summary.json`에 있다.
`real_target_modules_imported=true`, `official_test_assertions_modified=false`와 고정 source
metadata를 확인했다. 당시 저장한 tool bytes SHA256은 다음과 같다.

```text
082455b44ee990acee4974f446b579aca90a8a025ec9904f5811cca3e7320a5a
```

실행 당시 새 output을 사용한 명령은 다음과 같다.

```sh
cd /Users/mgprincess/Desktop/BoB/프로젝트/byul-b-codec-runner/update
/usr/bin/time -p env PYTHONPATH=.:src \
  /private/tmp/byul-req10-target031-cpu-20261029/bin/python \
  -m pytest tests/test_b2_message_cpu.py -q
/usr/bin/time -p env PYTHONPATH=.:src \
  /private/tmp/byul-req10-target031-cpu-20261029/bin/python \
  tools/b2_message_cpu.py --source /private/tmp/byul-req10-target031-20261029 \
  --output /private/tmp/byul-codec-message-runner-20261029/run-1
```

raw pytest stdout/stderr는 child 결과의 메모리 안에서만 처리하고 고정 카운터·시간·source
metadata만 저장한다. 이 수정은 기존 공식 시험 runner의 실행 정확성과 기존 관측의 재사용을
마감하며 제품 serialization 구현·공용 계약·registry·팩·다른 decoder 도구는 변경하지 않는다.


## 최적화 모드의 assertion 제거 차단

추가 검토에서 `PYTHONOPTIMIZE=1`을 child가 상속하면 원문 assertion이 제거돼 잘못된
성공 카운터가 만들어질 수 있음을 확인했다. `run`과 `load_official_tests`는 source 검증보다
먼저 `sys.flags.optimize`를 검사해 고정 ValueError로 거부한다. 선택 AST는 기존 NAMES와
원문 assertion을 유지하고 `optimize=0`으로 명시 컴파일한다.

최종 전용 회귀는 **19 passed / pytest 2.34초 / 부모 wall 2.92초**다.
실제 subprocess의 `-O`·`-OO`·`PYTHONOPTIMIZE=1` 각각에서 두 진입점이 없는 source를
검증·실행하기 전에 고정 오류로 거부하고 output을 만들지 않는지 확인했다. 컴파일 기본값을
2로 둔 합성 선택 정의에서도 세 시험의 `assert False`가 모두 AssertionError를 내는지 확인했다.
기존 성공 3건 조건·실패/skip 보존·stdout privacy 회귀도 유지했다.

새 tool bytes로 원문 공식 CPU 시험을 한 번 재실행했다.
**3 passed / failed=0 / error=0 / skipped=0 / pytest 7.8064초 / 부모 wall 9.15초**다.
원본은 `/private/tmp/byul-codec-message-runner-20261029/run-2/summary.json`이며,
실행 파일 bytes가 아래 기록 SHA256과 같은지 확인했다.

```text
1bfba2cb86e802a034cc2132298db2f98ef806f07cdab17006e1163e31d65d85
```

source·기존 151개 CPU 환경·원문 공식 시험 assertion은 변경하지 않았다.
이번 재실행은 새 output `run-2`를 사용했고 다른 typed decoder 검증은 재사용했다.
