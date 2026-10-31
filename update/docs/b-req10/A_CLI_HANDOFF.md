# CHK-REQ-010 / REQ-10 reference CPU의 A 등록·CLI 연결안

A는 `A_REGISTRATION_PROPOSAL.patch`의 import 한 줄과 등록 한 항목을 검토해
`structured_tools_cpu:reference-cpu`를 선택 등록한다. 이 문서와 patch는 제안이다.
현재 제품 registry·CLI·PACK31은 그대로이며, 검토 기준 소스는 `0675f76`이다.

기존 CLI는 `run_config`로 전체 TOML을 전달하고 registry factory가 전용 설정을
꺼내므로 새 CLI command가 필요 없다. CheckDef의 executor `structured_tools_cpu`와
`[executors]`의 구현명 `reference-cpu`가 합쳐져 제안 등록 키를 선택한다.
기존 PACK31의 REQ-10 numeric sampling override는 유지한다. 같은 REQ-10 ID를 쓰는
reference CPU 검증에는 별도 팩·ENV·POL·PLAN·Store를 사용한다.

## 입력과 결과

외부 전용 팩은 `PACK-B-REQ10-CPU-PROPOSAL@1`, DRAFT이며 정규 Ref SHA256은
`4157e269c2851e25f6c5b6bbbfcd224db13877053fc2dca7b82e45e0ea765ec6`다.
`tools/req10_tools_cpu_pipeline.py`가 생성한 원본이 아래 경로에 있다.

```text
/private/tmp/byul-b-req10-evidence-20261029/final-live-pipeline-1/
  proposal-pack/   # checks/REQ-10.yaml 및 파일 bytes hash manifest
  ENV.json        # ENV-B-REQ10-CPU-REFERENCE, DECLARED, model.id=fixture-model
  POL.json        # DRAFT, 위 pack의 정확 Ref, scope.include=[REQ-10]
```

팩 loader가 manifest의 check bytes를 검증하고 POL이 위 정규 pack Ref를 참조한다.
다른 머신에는 이 세 입력을 함께 복사하고 Ref를 다시 확인한다. 기존 pipeline 실행의
Store는 원본 증거로 보존하며 CLI 연결 시험에는 새 Store를 만든다.

입력 설정은 `source_root`와 `model` 두 개다. source는 Python 3.12 환경에서 실제
vLLM package를 import할 수 있어야 하며 producer가 HEAD·전체 package bytes·index·
ignored/미추적 파일·import 위치·CPU torch를 실행 전후 확인한다.
`model=fixture-model`은 요청 클래스에 넣는 문자열이며 모델 weights를 불러오지 않는다.
아래 설정은 제공한 ENV의 model.id와 같은 문자열을 쓴다. 현재 CPU producer는
설정 model과 ENV model.id의 동일성을 자동 대조하지 않으므로 A가 입력 조합을 확인한다.
HTTP 경로의 ENV 모델 저장소 신원→API 모델명 매핑은 기존 `ctx.api_model` 경계가
처리한다. reference CPU에는 서버/API 대상 신원이 연결되지 않는다.

EVD는 validator 위반, 정상 controls/planned, CPU rows, runner flag의 5개 관측과
정제 첨부 `structured-tools-reference-cpu.json`을 담는다. origin은 DECLARED 상한이며
EXAMPLE ENV의 결과는 EXAMPLE을 유지한다. 기존 core가 EVD→RES→RPT의 정확 Ref를 잇는다.

미수집·source/import/필수 metadata 실패는 `ABORTED/STRUCTURED_TOOLS_CPU_CAPTURE_UNAVAILABLE`와
빈 관측으로 남는다. 첨부 실패는 `ABORTED/ARTIFACT_CAPTURE_INCOMPLETE`이며 앞서 수집한
독립 위반 관측은 보존한다. named-json 수용 위반은 후속 변환 실패와 독립적으로 FAIL에
반영한다. 정상 reference 결과의 보안은 UNKNOWN, 한정 기능은 PASS이며 runner flag는 false다.

## A 반영 후 실행할 CLI 예시

아래 명령은 A가 제안 patch를 반영하고 공통 source를 확정한 뒤 실행한다.
`select/run/judge/report/verify --help`를 실제로 확인했다. 확인한 CLI와 `application.py`는
`0675f76`과 동일하다. 제안 patch의 `git apply --check`, shell 구문, TOML 구조, 외부 팩
bytes/Ref·POL 참조·model fixture를 확인했다. CLI의 팩 preflight와 동일한 select 입력은
REQ-10 한 항목을 gap 없이 선정했다. 등록 patch 적용과 아래 live CLI 실행은 A 반영 후 검증 항목이다.

`BYUL_UPDATE`는 A가 확정한 checkout의 update 경로, `CPU_PY`는 준비한 격리 CPU Python이다.
`PYTHONPATH`에는 그 checkout의 src와 참조 source를 넣어 동일 프로세스에서 사용한다.

```sh
set -euo pipefail
BYUL_UPDATE=/absolute/path/to/A-checkout/update
CPU_PY=/private/tmp/byul-req10-cpu-20261029/bin/python
SOURCE_ROOT=/private/tmp/byul-req10-source-20261029
PROPOSAL_ROOT=/private/tmp/byul-b-req10-evidence-20261029/final-live-pipeline-1
RUN_ROOT=/private/tmp/byul-a-req10-cli-new
mkdir "$RUN_ROOT"
cd "$BYUL_UPDATE"
# Use the physical checkout path for imports.
BYUL_UPDATE="$(pwd -P)"
export PYTHONPATH="$BYUL_UPDATE/src:$SOURCE_ROOT"
export VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
cat > "$RUN_ROOT/guard.toml" <<TOML
[executors]
structured_tools_cpu = "reference-cpu"

[structured_tools_cpu]
source_root = "$SOURCE_ROOT"
model = "fixture-model"
TOML

"$CPU_PY" -m guard.cli select \
  --pack "$PROPOSAL_ROOT/proposal-pack" --env "$PROPOSAL_ROOT/ENV.json" \
  --policy "$PROPOSAL_ROOT/POL.json" --config "$RUN_ROOT/guard.toml" \
  --store "$RUN_ROOT/store" > "$RUN_ROOT/select.log"
read -r PLAN_REF _ < "$RUN_ROOT/select.log"
"$CPU_PY" -m guard.cli run --plan "$PLAN_REF" \
  --pack "$PROPOSAL_ROOT/proposal-pack" --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store"
"$CPU_PY" -m guard.cli judge --plan "$PLAN_REF" \
  --pack "$PROPOSAL_ROOT/proposal-pack" --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store"
"$CPU_PY" -m guard.cli report --plan "$PLAN_REF" \
  --pack "$PROPOSAL_ROOT/proposal-pack" --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store" \
  --json "$RUN_ROOT/RPT.json"
"$CPU_PY" -m guard.cli verify --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store"
```

`[executors]`는 구현 선택표이고, 형제 table `[structured_tools_cpu]`가 factory에 전달할
설정 객체다. adapter/엔진 기동 설정은 이 CPU 경로에 필요 없다. CLI `run`은 ABORTED도
출력하므로 종료 코드와 함께 EVD 상태·관측 5개·첨부 1개를 확인한다. `verify`는 Store의
hash·Ref·첨부 무결성을 검사한다. RPT는 DRAFT 입력에 따른 `official=false`를 유지한다.

## 공통 source 확정과 대상 구분

A가 registry를 반영하면 현재 B 도구의 공동 source gate도 다시 고정해야 한다.
현 gate는 c9af729 baseline과 승인 B 두 파일의 고정 SHA256 overlay만 허용하므로
추가 registry 변경을 거부한다. B/A가 확정한 공통 source commit·registry bytes를 검토해
명시적인 새 소비 기준을 정하고, 해당 source에서 CLI·직접 CPU·EVD/RES/RPT를 재검증한다.
실행 시점의 파일 해시를 자동 승인하거나 공통 경로 검사를 제외하지 않는다.

참조 `5d35ce3934a7804b298559d2d99eec33792e1eb9`의 describe는
`v0.33.0rc0-41-g5d35ce393`이다. 이 producer는 해당 reference의 요청→sampling→
xgrammar 변환을 관측한다. 설치 대상 0.31.0의 source pin
`db9527a46873454610df6dbedf79a36d6bf1a7f6`과 그 실행환경·backend 지원은 별도로
검증·기록한다. reference CPU 결과는 reference 범위로 집계하고,
0.31.0 대상의 서버 전처리·runner 소비·서비스 생존 실측에는 대상별 증거를 연결한다.
