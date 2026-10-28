# 고정 참조 요청 클래스 CPU 검증 — 2026-10-28

이 결과는 참조 vLLM 소스의 요청 객체 검증 결과다. 최종 설치 대상은 미정이며,
제품 자동 연결·실제 backend/grammar·모델 제출·runner/GPU/배포 검증과 구분한다.

| 구분 | 결과 |
| --- | --- |
| 대역 회귀 | 기존 PR43의 영향/인접/불변식 207 passed, 1 deselected 보존 |
| 실제 코드 CPU | 고정 소스의 실제 ChatCompletionRequest.model_validate: 정상 4건 수용, 금지 1건 거부 |
| 공식 CPU 시험 | 원문 시험 9 passed, pytest 4.02초 / 부모 벽시계 6.66초 |
| 제품 연결 | A 공동 검증 SHA·등록·CLI·팩·첨부 보존 인계 대기 |
| 실제 환경 | 설치판/API/backend 미정. 엔진·GPU·Docker·모델·AWS 사용 없음 |

참조 SHA: `5d35ce3934a7804b298559d2d99eec33792e1eb9`.
실제 클래스: `vllm.entrypoints.openai.chat_completion.protocol.ChatCompletionRequest`.
실제 거부 예외: `vllm.exceptions.VLLMValidationError`.
프로토콜 파일 bytes SHA256:
`e112a3c4c23e7a2e82a1964b39102d7209621f81f37810e741cf963cd50e1ec1`.
공식 시험 파일 bytes SHA256:
`957a0e8e6d2670d9dcccf213f9fa90a2962d997b4c690eced1194007044e6ccf`.

B 입력의 결과: none/auto/required/named-only는 accepted, named-json은 rejected.
실제 도구 실행의 측정 구간(소스 확인 후 import+호출+결과 구성)은 25.32초다.
이 수치를 API 지연이나 처리 비용 한도로 해석하지 않는다.
정상 입력 및 거부는 요청 클래스 경계까지 확인했다. 후속 변환 미도달과 서비스
전체 생존을 확인하지 않았고 runner_consumption_verified는 false를 유지한다.

## 환경과 재현

저장소 밖 별도 CPU 가상환경을 만들었다. Python 3.12.14, torch 2.14.0+cpu,
pydantic 2.14.1, transformers 5.20.2, openai 3.40.0을 실제 사용했다.
참조 requirements/common.txt와 pytest/typer를 시험 환경에 설치했다.
전체 148개 설치 패키지는 `cpu-requirements.freeze.txt`에 보존했고 uv pip check도 통과했다.
제품 pyproject.toml/uv.lock과 시스템 Python 패키지는 변경하지 않았다.

현재 컴퓨터의 기록 폴더는 저장소의 형제 폴더 `byul-b-evidence-20261028/`다.
여기의 vllm-reference는 원문 git checkout, cpu-venv는 시험 전용 환경이다.
원문 소스를 수정하거나 검증기를 이식/대역으로 바꾸지 않았다.
아래 변수는 재현하는 컴퓨터의 본인 폴더로 지정한다.

```sh
git clone --filter=blob:none --no-checkout https://github.com/vllm-project/vllm.git "$VLLM_SOURCE"
git -C "$VLLM_SOURCE" checkout --detach 5d35ce3934a7804b298559d2d99eec33792e1eb9
uv venv --python 3.12 "$CPU_VENV"
uv pip install --python "$CPU_VENV/bin/python" 'torch==2.14.0+cpu' --index-url https://download.pytorch.org/whl/cpu
uv pip install --python "$CPU_VENV/bin/python" --torch-backend cpu -r "$VLLM_SOURCE/requirements/common.txt" pytest typer
export VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$VLLM_SOURCE:$BYUL_REPO/update/src"
"$CPU_VENV/bin/python" "$BYUL_REPO/update/tools/req10_request_class.py" --source-root "$VLLM_SOURCE" --model fixture-model --output "$NEW_EVIDENCE_JSON"
```

공식 시험은 위 고정 소스의 `tests/tool_use/test_chat_completion_request_validations.py`를
격리 시험 폴더에 bytes 그대로 복사하고, 그 폴더를 대상으로 다음 명령을 실행했다.
엔진용 repository conftest만 로드하지 않으며 원문 사례/예외/assert는 유지했다.

```sh
"$CPU_VENV/bin/python" -m pytest --noconftest -p no:cacheprovider --tb=no -q "$CPU_TESTS/test_chat_completion_request_validations.py"
```

실제 import 파일이 참조 checkout 경로인지, B 모듈이 본인 저장소에서 import되는지,
원문 git vllm 경로가 깨끗한지, torch.version.cuda가 null인지 확인했다.
세부 출처는 `cpu-provenance.json`, 실제 도구 결과는 `request-class-attempt-1.json`,
공식 시험 결과는 `official-cpu-tests-1.txt`에 보존한다. 설치 로그도 저장소 밖에 둔다.

## 준비 중 관측과 한계

첫 common 의존성 설치는 CPU torch 설치와 환경 잠금이 겹쳐 완료 전 중단했다.
CPU torch 설치 완료 후 --torch-backend cpu로 순차 재실행하여 성공했다.
요청 클래스 실행에는 차단이나 실패가 없었다.

소스 checkout에는 생성 파일 vllm._version과 설치된 vllm 배포 메타데이터가 없어
버전 경고가 났다. 버전 문자열 대신 git SHA·실제 import 경로·파일 bytes 해시를
검증했다. triton.backends를 import할 수 없다는 경고도 있었으나 요청 클래스와
공식 시험은 통과했다. 이를 CPU 엔진 실행 가능성의 증명으로 사용하지 않는다.
모델/토크나이저 로딩, HTTP 서버, grammar 컴파일, GPU 커널은 실행하지 않았다.

A 인계는 이 CPU 결과의 선행 조건이 아니다. A의 확정 소스를 받은 뒤에는 본인
변경과 차이를 검토해 정확한 통합 소스에서 CLI→실행→EVD→RES/RPT를 검증한다.
현재는 명시적 공동 SHA 인계가 없으며, 원격 브랜치의 존재를 인계로 간주하지 않는다.
