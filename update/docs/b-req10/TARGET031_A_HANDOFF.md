# CHK-REQ-010 / REQ-10의 0.31 source CPU 등록 인계

검토 기준은 `f82c37d459cac36e4322de47e2bfb31e3b765088`이다. 기존
`A_CLI_HANDOFF.md`의 0675f76 reference 인계에서 달라진 부분을 정리한다.
A가 검토할 등록 제안이며 제품 registry·CLI·PACK31은 변경하지 않았다.
최종 producer 보수는 `240387cba6dfed35a259bb8baf493341f6ca3b1f`이며,
고정 bytes SHA256은 `1eac6f0fe8067a9ace386ee3d8b3e5bbc728387578c4b1f429e1495dc576cb87`이다.

## 달라진 실행 경계와 입력

`StructuredToolsTarget031CPUExecutor`는 기존 `structured_tools_cpu.py`에 추가됐다.
프로파일은 `structured-tools-vllm-031-cpu-v1`, 고정 source는
`db9527a46873454610df6dbedf79a36d6bf1a7f6`이다. 5d35ce3의 0.33rc reference 진입점은
유지하며 0.31 source CPU는 별도 CheckDef·팩·ENV·POL·PLAN·Store로 실행한다.
PACK31의 기존 numeric sampling override는 유지한다.

0.31 프로파일은 기존 5조합에 각 사례 직후 정상 auto 변환 5건을 추가한다.
정상 controls/planned는 4개 조합과 후속 변환 5건의 총 9개다. 요청 validator,
sampling, xgrammar backend·JSON 변환 진입을 관측한다. 실제 API·runner·서비스 생존은
별도 수집 범위이며 `installed_target=null`, server preprocessing·runner flag는 false다.

설정은 여전히 `source_root`와 `model` 두 개다. 0.31 진입점은 capture 전에
ENV hash 유효성, `ENV.engine.name=vllm`, 비어 있지 않은 설정 model과 `ENV.model.id`의 일치,
`ENV.engine.version=reference-db9527a46873454610df6dbedf79a36d6bf1a7f6`를 검사한다.
ENV 불일치는 `STRUCTURED_TOOLS_TARGET031_ENVIRONMENT_MISMATCH`로 수집 전에 거부한다.
source_root의 정확 HEAD·전체 vllm bytes·index·미추적/ignored 파일과 실제 import 위치를
실행 전후 확인한다. ENV의 source 결합은 위 version 문자열로 수행한다.
다른 engine.name은 capture 이전에 거부하며 회귀와 검토로 확인했다.
HTTP의 ENV 저장소 모델→API 이름 매핑은 기존 `ctx.api_model` 경계에 남는다.

준비한 source는 `/private/tmp/byul-req10-target031-20261029`, Python 3.12 환경은
`/private/tmp/byul-req10-target031-cpu-20261029`다. 원문 의존성 148개에 시험용 pytest를
추가한 격리 환경을 사용한다. 제품 lock은 유지한다. producer는 import 전에
`torch==2.13.0`, `xgrammar==0.2.7`, `transformers>=5.10.4,<5.18.0`을 확인한다.

## A 최소 등록안과 기존 CLI

같은 registry 파일에 아래 import와 factory 한 항목을 추가하는 제안이다.
기존 reference CPU 등록 제안과 별개 선택 키다.

```python
from .structured_tools_cpu import StructuredToolsTarget031CPUExecutor

# EXECUTORS 안에 추가
"structured_tools_target031_cpu:source031": lambda cfg: StructuredToolsTarget031CPUExecutor(
    cfg.get("structured_tools_target031_cpu", {})
),
```

CheckDef.executor는 `structured_tools_target031_cpu`로 연결한다. CLI와
`application.run_config`는 0675f76과 동일하므로 새 command가 필요 없다.
아래 명령은 A가 등록·공통 source를 확정한 뒤 사용할 예시다. TARGET_INPUTS에는
완료된 0.31 pipeline의 `proposal-pack/`, `ENV.json`, `POL.json`을 함께 지정한다.
기존 5d35 reference 팩 `4157e269…`는 이 0.31 입력에 사용하지 않는다.

```sh
set -euo pipefail
BYUL_UPDATE=/absolute/path/to/A-checkout/update
CPU_PY=/private/tmp/byul-req10-target031-cpu-20261029/bin/python
SOURCE_ROOT=/private/tmp/byul-req10-target031-20261029
TARGET_INPUTS=/private/tmp/byul-target031-product-core-20261029-1
RUN_ROOT=/private/tmp/byul-a-req10-target031-cli-new
mkdir "$RUN_ROOT"
cd "$BYUL_UPDATE"
BYUL_UPDATE="$(pwd -P)"
export PYTHONPATH="$BYUL_UPDATE/src:$SOURCE_ROOT"
export VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
cat > "$RUN_ROOT/guard.toml" <<TOML
[executors]
structured_tools_target031_cpu = "source031"

[structured_tools_target031_cpu]
source_root = "$SOURCE_ROOT"
model = "fixture-model"
TOML

"$CPU_PY" -m guard.cli select --pack "$TARGET_INPUTS/proposal-pack" \
  --env "$TARGET_INPUTS/ENV.json" --policy "$TARGET_INPUTS/POL.json" \
  --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store" > "$RUN_ROOT/select.log"
read -r PLAN_REF _ < "$RUN_ROOT/select.log"
"$CPU_PY" -m guard.cli run --plan "$PLAN_REF" --pack "$TARGET_INPUTS/proposal-pack" \
  --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store"
"$CPU_PY" -m guard.cli judge --plan "$PLAN_REF" --pack "$TARGET_INPUTS/proposal-pack" \
  --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store"
"$CPU_PY" -m guard.cli report --plan "$PLAN_REF" --pack "$TARGET_INPUTS/proposal-pack" \
  --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store" --json "$RUN_ROOT/RPT.json"
"$CPU_PY" -m guard.cli verify --config "$RUN_ROOT/guard.toml" --store "$RUN_ROOT/store"
```

외부 팩 ID는 `PACK-B-REQ10-031-CPU-PROPOSAL@1`, DRAFT다. pipeline은 별도
`ENV-B-REQ10-031-CPU-REFERENCE`, `POL-B-REQ10-031-CPU-PROPOSAL`을 만든다.
실제 원본은 `/private/tmp/byul-target031-product-core-20261029-1/`에 있다.
정확 Ref는 다음과 같다.

- PACK `PACK-B-REQ10-031-CPU-PROPOSAL@1`, SHA256
  `27da82f96dd8b5d5e4f9ccc7efb45a8be9c9a9d118294fddf9f1a7617745f7d1`
- EVD `EVD-01M631YV2BYFDZM3CTPVFPNVS2@1`, SHA256
  `efaf3defe320300b9beab71038b866bbcc0974dd59bdb7bbfdbbfb3ecbd64344`

`summary.json`과 `postverify-1.json`에서 COMPLETED/DECLARED, 관측 5개·첨부 1개,
후속 정상 변환 5건 수용·controls 9개 PASS, 보안 UNKNOWN·기능 PASS를 확인했다.
Store verify 오류는 0개이고 정확 Ref 재판정의 RES Ref가 같으며 RPT에 해당 EVD/RES가
포함됐다. 이는 담당자가 수행한 실행 기록이며 이 문서 작업에서 재실행하지 않았다.
위 Ref는 원본으로 유지하고 최종 source 보수 후 새 실행의 Ref를 별도로 기록한다.

최종 보수 source의 실제 재실행은
`/private/tmp/byul-target031-product-evidence-20261029/final-core/`에 있다.
PACK Ref는 위와 같고, EVD `EVD-01M6322BPP3BFC6W2J7S8F043S@1`의 SHA256은
`568a52cd277817f23980429ae40677adc6fa45565c6c0b226efbbcdc6002f70f`다.
COMPLETED/DECLARED·관측5·첨부1·정상 controls9개·후속5건을 확인했고,
`postverify.json`의 Store 오류0·정확 Ref 재판정 일치·RPT Ref 연결을 확인했다.
최종 영향/불변식 회귀319통과·기존제외1(14.04초), 집중 검토 지적 해결 확인을 완료했다.

출력은 수집 관측 5개, CPU rows 5건과 각 후속 정상 변환, 정제 첨부
`structured-tools-vllm-031-cpu.json`, 기존 EVD→RES→RPT다. origin은 DECLARED 상한이며
EXAMPLE ENV는 EXAMPLE을 유지한다. 수집 실패는 ABORTED/빈 관측이고, 첨부 실패는
앞서 수집한 독립 위반을 보존한다. named-json 수용은 후속 오류와 독립적으로 FAIL에
반영한다. 정상 9개 대조가 모두 수용돼야 기능 PASS가 가능하며 보안은 UNKNOWN을 유지한다.
CLI `run`의 종료 코드와 함께 EVD 상태·관측·첨부를 확인한다.

## source gate와 남은 A 연결

현재 B 도구는 c9af729 공동 baseline에 승인된 두 B 제품 파일의 고정 SHA256을
적용한다. A의 registry 추가는 이 gate에서 거부된다. B/A가 등록을 반영한 공통 commit과
registry bytes를 확정한 뒤 명시적인 소비 기준을 다시 고정하고 그 source에서 검증한다.
자동 해시 승인이나 common 경로 제외로 통과시키지 않는다. 이번 문서 작업에서는
다른 담당자가 진행 중인 실제 수집·engine/API 요청·전체 시험을 반복하지 않았다.

0.31 source CPU의 완료 범위는 고정 source와 준비한 CPU 의존성에서의 직접 호출이다.
실제 배포 대상의 서버 전처리·runner 소비·서비스 생존 완료에는 해당 대상 증거를 연결한다.
