# B-1 REQ-10 제안 연결의 오프라인 검증

기존 / 공백 / 추가: main8761222는 A 공통 실행·첨부 보존·판정·RPT와 PR43/46/49,
B2 PR47 제품 fixture 검증을 포함한다. generic core/저장/반출 회귀를 새로 구현하지 않는다.
공백은 REQ10 structured-tools의 전용 관측이 core 정규화에서 유지되고 기존 rule_table로
판정되는 연결이다. 전용 도구는 기존 StructuredToolsExecutor와 run/select/judge/report를
재사용하고 테스트 프로세스에서만 registry와 제안 CheckDef를 연결한다.

운영 PACK31·sampling-api-v1·registry·공통 코드·제품 lock은 변경하지 않는다.
외부 workdir에만 PACK-B1-EXAMPLE-STRUCTURED/DRAFT fixture를 만들고 기존 pack
loader/hash/저장/정확 Ref 재판정을 사용한다. 이것은 통합 팩의 생성·배포·등록이 아니며
A에게 전달할 최소 CheckDef 연결의 EXAMPLE 검증이다. source CPU 판본과 설치판을
구분하고 실제 CPU 결과도 EXAMPLE inference transport를 MEASURED로 바꾸지 않는다.

사례는 정상, synthetic 위반+후속 실패, validator 미수집, 미지원, synthetic 위반+첨부
저장 실패다. 정상의 security UNKNOWN/한정 functional PASS, 미수집/미지원 UNKNOWN,
독립 FAIL 보존을 기존 judge_params로 확인한다. 실제 참조 코드에서 위반을 관측한 것으로
synthetic 사례를 보고하지 않는다. runner_consumption_verified=false다. 미지원은 이
fixture 설정이며 설치판 기능 비적용의 증명이 아니다.

실제 클래스는 vLLM5d35ce3934a7804b298559d2d99eec33792e1eb9의
ChatCompletionRequest 및 VLLMValidationError를 직접 import한다. full source import와
B-1 자체 product import 위치를 확인한다. model weights/엔진/HTTP listener/GPU/Docker/
AWS는 사용하지 않는다. 설치판의 최종 consumer/정책 확인은 이 CPU 연결 시험과 다르다.

```sh
export VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$BYUL_B1_REPO/update:$BYUL_B1_REPO/update/src:$VLLM_SOURCE"
"$CPU_VENV/bin/python" "$BYUL_B1_REPO/update/tools/b1_integration_cpu.py" --source "$VLLM_SOURCE" --output "$NEW_EVIDENCE"
```

코어는 main876122211fe47d12f8fcbd69dab5883974ace168와 동일한 bytes를 요구한다.
도구/회귀는 정상 Python에서 실행한다. 시험 의존성은 격리 CPU env이고 제품 lock은 불변이다.
첫 회귀5실패는 도구가 Plan에 없는 pack 속성을 읽은 연결 실수였다. 실제 plan.inputs.pack을
읽도록 수정했고 기대 판정/시험을 완화하지 않았다. 수정 후 직접·기존 회귀25 passed,
pytest32.06초. 초기 잘못된 shell 경로의 no-tests와 실패 진단은 외부 evidence에 보존한다.

정식 연결 마감에는 A가 profile 선택·CheckDef/registry/CLI·제품 팩 인계를 결정하고,
이 최소 연결안으로 정확 통합 소스에서 재검증해야 한다. 설치 target·승인 정책이 필요한
실제 환경 검증은 별도다. 이 도구가 돌아간 사실만으로 제품 자동연결을 완료했다고 하지 않는다.


실제 요청 클래스5조합을 재확인한 CPU 도구와 EXAMPLE 제품 경로5사례가 완료됐다.
이5조합은 PR43의 시험을 재사용한 것이며 신규 사례 수로 집계하지 않는다. 생성된
EVD/RES/RPT·정확 Ref 재판정/Store 검증을 확인했다. 원문 없는 결과는 저장소 밖
byul-b1-integration-evidence-20261028/run-2/summary.json과 각 case store에 있다.
명령 준비 때 vendor tests가 앞선 PYTHONPATH로 tests.builders import가 실패한 시도는
attempt-2.json에 남겼고, own update를 앞에 두어 source 변경 없이 재실행했다.
완료 상태: 대역 회귀/실제 코드 CPU/EXAMPLE 제안 제품 경로 검증 완료.
운영 profile/CLI 자동연결과 설치판 실측은 완료가 아니다.
