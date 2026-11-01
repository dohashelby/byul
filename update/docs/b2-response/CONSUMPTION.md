# CHK-REQ-013 / REQ-13의 현재 소스 소비

기존 B2의 두 fixture 사례를 공동 제품 경로에서 다시 실행하는 도구의 소비 기준을
`7acd3d0` exact HEAD에서 `fc3c4c174ef9dd4b53bc966b75ec4e131bdf1f2a` ancestor로 갱신했다.
기존 구현·선정·판정·팩·registry·CLI는 유지한다.

공통 제품은 `tools.b1_integration_cpu.verify_tool_checkout`를 재사용해 c9af729 baseline,
PACK31·framework91e84c7, 승인된 두 B1 제품 파일의 고정 SHA256을 확인한다.
재사용 gate, B2 response helper, reference helper, manifest, fixtures 두 파일은 fc3 snapshot과
명시 SHA256을 함께 확인한다. 제품과 fixture 함수의 실제 파일 identity도 확인한다.
0.31 source는 `db9527a46873454610df6dbedf79a36d6bf1a7f6` exact HEAD와 전체 vllm snapshot을
검사하며 기존 reference manifest도 재사용한다. 실행 뒤 제품·source를 다시 검사한다.

실제로 실행하는 것은 원문 AST에서 선택한 validation handler·오류 모델 정의다.
FastAPI/Pydantic/Starlette는 준비된 0.31 CPU 환경을 재사용하고, 요청 body·의존 객체와
정상/옵션 추론 전송은 EXAMPLE이다. 엔진 package 전체 import·HTTP routing·모델 생성·
추론·서버 생존의 실측은 별도 경계다. 이번 범위는 unmapped fixture의 model.id=`test`다.

기존 두 사례는 정상 EXAMPLE PASS와 합성 추가 응답 필드+후속 실패의 독립 FAIL이다.
원문 표식·예외 미저장, EVD/RES/RPT, 저장한 정확 pack Ref 재판정의 동일 RES Ref,
Store 무결성을 확인한다. 실제 handler의 위반을 관측했다고 합성 추가 필드를 보고하지 않는다.
기업 허용 정책을 추가하거나 기존 판정 기준을 변경하지 않는다.

## 검증 기록

순수 gate 회귀는 합성 Git 저장소와 fixture 상수를 사용하며 CI checkout 이력에 의존하지 않는다.
최종 직접 회귀는 **20 passed / pytest 2.12초 / 부모 wall 2.34초**다. 공통 gate 재사용,
고정 helper bytes, dirty/index-only/assume-unchanged, source shadow·symlink,
정확 HEAD, 다른 checkout의 같은 내용 import 거부와 alias 경로 identity를 확인했다.

실제 두 사례는 **통과 / 부모 wall 2.41초**다. 원본은
`/private/tmp/byul-b-req13-consumption-20261029/run-1/summary.json`과 각 case Store다.
정상 `normal_error_path`는 EXAMPLE/PASS·정상 대조 성공, 합성 위반
`confirmed_field_and_after_failure`는 EXAMPLE/FAIL·정상 대조 실패를 유지했다.
두 사례 모두 정확 pack Ref 재판정의 RES Ref가 같고 store.verify 오류는 0개이며
표식·예외 원문이 남지 않았다. EVD/RES/RPT의 정확 Ref는 summary에 기록했다.

실행한 tool bytes SHA256은
`2d7785676d1b4e512b27f6aa6b14771514cf246de4b21f511199e30aa26fa815`다.
summary의 runtime source/helper hash 전부와 실행 후 현재 파일 bytes의 일치도 확인했다.
준비된 `/private/tmp/byul-req10-target031-cpu-20261029`의 기존 환경을 재사용했다.
설치 metadata는 Pydantic 2.14.1, FastAPI 0.136.3, Starlette 1.10.0, regex 2026.9.29다.
과거 2026-10-28 CPU 기록의 dependency 버전과 구분한다.

실행 당시 새 output을 사용한 명령은 다음과 같다.

```sh
cd /Users/mgprincess/Desktop/BoB/프로젝트/byul-b-req13/update
/usr/bin/time -p env PYTHONPATH=src:. \
  /private/tmp/byul-req10-target031-cpu-20261029/bin/python \
  -m pytest tests/test_b2_integration_pin.py -q
/usr/bin/time -p env PYTHONPATH=.:src \
  /private/tmp/byul-req10-target031-cpu-20261029/bin/python \
  tools/b2_integration_cpu.py --source /private/tmp/byul-req10-target031-20261029 \
  --output /private/tmp/byul-b-req13-consumption-20261029/run-1
```

기존 제품·helper 계약과 과거 CPU 검증 결과는 재사용했으며 전체 suite·upstream 시험을
다시 실행하지 않았다. 이 변경은 소비 gate와 전용 회귀·검증 기록에 한정한다.

## A 공통 모델 신원 인계

현재 ResponseContentExecutor는 요청과 정상 응답 검증에 ENV.model.id를 사용한다.
유효 model_mapping의 API alias 소비는 A의 공통 소스 보수 항목이다. 기존 unmapped 두 사례의
성공 범위를 API alias·Kubernetes·일반 endpoint 검증으로 확대하지 않는다.
별도 alias 재현 원본은 `/private/tmp/byul-req13-alias-evidence-20261029/summary.json`,
[A 단일 작성 보수 요청](https://github.com/dohashelby/byul/pull/67#issuecomment-6488881229)에 있다.
해당 보수의 source가 확정되면 명시적인 공동/B2 소비 pin을 갱신하고 alias 경계에서 재검증한다.
이번 도구·테스트에는 alias 보수나 그 별도 회귀를 추가하지 않았다.
