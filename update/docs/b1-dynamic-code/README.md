# SUP-09/11 로컬 custom-config trust gate CPU 묶음

## 기존 / 공백 / 추가

- 기존: A main4af 및 PR44/48의 template_validation/template_sandbox/template_supply_approval는
  template HTTP/native/type/승인을 다룬다. PR51/52의 target/자격 identity도 재사용 대상이다.
  B-2 PR47/50은 실제 handler/logger/decoder 및 공통 EVD→RES/RPT 회귀를 제공한다.
  PR46은 SUP-08 기본 MM/code_revision gate를 이미 실제 OnlineRenderer로 확인했다.
- 공백: 위 시험은 실제 get_config/AutoConfig의 로컬 auto_map 코드 import/constructor와
  명시한 trust flag의 Python 실행 모드 간 동등성을 확인하지 않는다. 공식
  tests/tools/test_check_trust_remote_code.py 시험은 AST hardcoded-true 검사이고 config 시험은
  주로 builtin config/field 변환이다. 같거나 충분한 부분을 새 실행기로 복제하지 않는다.
- 추가: 같은 loader 사례12개를 SUP-09·11에 재사용하는 최소 fixture·실행/관측·판정 자료.
  SUP-08 guard, 공통 pipeline, native identity producer는 새로 만들지 않는다.

참조 vLLM SHA5d35ce3934a7804b298559d2d99eec33792e1eb9, 격리 의존성 Transformers
5.20.2의 실제 AutoConfig.from_pretrained와 vllm.transformers_utils.config.get_config를
실행한다. model weights·engine·네트워크·GPU·Docker·AWS는 사용하지 않는다.
직접 만든 local config와 무해 Python module은 표식만 쓰며 subprocess/네트워크 호출이 없다.
trust_remote_code=False/True 각각과 Python3.12 normal/-O/-OO를 새 child/cache에서 대조한다.
이는 선택한 라이브러리 코드의 CPU 관측이다. 운영 vLLM의 지원 mode·승인 code 목록을
채택한 것이 아니며 실제 설치판/대상은 null로 유지한다.

명시 False의 거부는 메시지 문자열 대신 실제 resolve_trust_remote_code 예외 경로로
식별한다. 실제 flag/remote/local-code boolean과 import/constructor 표식·선택 모듈 bytes
해시만 기록한다. False인데 실제 import가 관측되면 독립 위반 후보를 보존한다.
SUP-11의 mode 차이는 같은 entry의 정상 baseline 거부·완전한 marker 수집과 대조한다.
다른 누락/오류/후속 실패는 확인한 import를 지우지 않는다. 전체 보안 PASS 규칙은 없다.

각 사례 전후 builtin local GPT2 config를 같은 실제 loader로 읽는다. 이는 loader 정상
대조이며 모델 생성/권한/서비스 생존의 증명이 아니다. child의 검증에는 assert를 쓰지
않아 -O에서 시험 assertion 자체가 제거되는 오판을 피한다. parent는 normal로 실행한다.
허용 True는 시험용 조건이고 임의 코드의 기업 승인 증명이 아니다.

```sh
export VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$BYUL_B1_REPO/update/src:$BYUL_B1_REPO/update:$VLLM_SOURCE"
"$CPU_VENV/bin/python" "$BYUL_B1_REPO/update/tools/b1_dynamic_code_cpu.py" --source-root "$VLLM_SOURCE" --workdir "$FRESH_DYNAMIC_EVIDENCE"
```

제품 lock/공통 코드/상대 파일은 변경하지 않는다. 증거는 별도 B-1 폴더에 보존한다.
source CPU JSON은 정식 ENV/POL/PLAN/EVD/RES/RPT가 아니며, A 공통 등록/CheckDef/CLI
연결은 정확한 인계 후 검증한다. 기업의 승인 코드 digest·실제 설치 loader·선정 모델 및
지원 실행 mode와 최종 모델 code 소비를 받은 뒤 해당 범위를 확정한다.


## 실행 결과와 연결 범위

새 main 876122211fe47d12f8fcbd69dab5883974ace168의 별도 B-1 worktree에 전용 파일만
이식했다. 실제 CPU 두 번째 snapshot에서 12사례와 전후 builtin 대조 각각12건을
완료했다(부모58.80초). 두 loader 모두 False는 실제 trust gate에서 거부되고 marker는
없었다. True는 import/constructor가 관측됐고 선택 모듈 bytes가 검토 fixture와 일치했다.
normal/-O/-OO에서 이 선택 분기의 차이는 없었다. 세 mode의 실제 sys.flags.optimize와
remote/local/trust boolean을 확인했다. 전체 SUP09/11·설치판 보안 PASS는 아니다.

직접·인접 rule_table 회귀65 passed(pytest0.35초, 부모0.535초). 정상/확인 위반/누락/
오류/후속 실패와 positive source-match 누락을 포함한다. 첫 CPU snapshot도12사례를
통과했으나 positive 수집 누락의 완료 gate를 보강한 뒤 새 main snapshot에서 재실행했다.
첫 실행89.16초/두 번째58.80초는 child12개 로딩을 포함한 부모 벽시계다. API 요청0,
엔진 기동0이며 fixture constructor는 모델 constructor가 아니다. 제품 lock은 불변이다.

대역 회귀: 완료. 실제 코드 CPU 검증: 위 한정 범위 완료. 제품 연결: 미등록/미검증.
실제 환경 검증: 설치 loader·승인 코드 digest/정책·지원 mode·최종 소비 미확정.
A 최소 연결안은 SUP09/11의 전용 observation key와 judge_params를 선택한 collection
profile/CheckDef에 연결하는 것이다. 입력은 고정 source+격리 CPU profile이고 출력은
scope/사례/표식 enum/hash/완전성이다. 미수집은 UNKNOWN이며 관측한 독립 FAIL은
다른 누락으로 지우지 않는다. 공통 등록/CLI/팩을 변경하지 않았다.

증거는 저장소 밖 byul-b1-dynamic-evidence-20261028의 run-1/2/result.json,
cpu-assertions-1/2.json, snapshot-1/2.json, contracts-1/2.txt와 cpu-install 기록에 있다.
재개 첫 행동은 실제 설치 코드/정책과 profile 등록 계약을 고정해 기존 실행→EVD→RES/RPT
경로를 검증하는 것이다. 기존 CPU 결과를 설치 대상 실측으로 소급하지 않는다.

## SUP-09·011 target031 CPU 연결 준비 (2026-10-29)

기존 12사례와 전·후 builtin 대조 각 12회를 하나의 `capture()`로 수집한다.
기본 프로파일 `reference5d`는 기존 5d35ce3이며, 명시적인 `target031`은
v0.31.0 db9527a46873454610df6dbedf79a36d6bf1a7f6이다. 두 `get_config`
함수 AST SHA256은 `18fab20b836ee6402aa12ecf8eba294c45a3f7bd033a315ea8395ac43a0eb942`로
같다. Transformers와 Torch 버전은 각 CPU 환경의 실제 값으로 기록한다.

수집 전·후 전체 source bytes/commit, 고정 3파일 fixture manifest, 제품 component와
검사 helper의 파일·함수 origin을 검증한다. 부모는 normal Python이며 child의
normal/`-O`/`-OO`는 필수 사례로 유지하고 실제 `sys.flags.optimize`를 기록한다.
각 child는 새 소유 HF cache를 사용하며 `VLLM_PLUGINS=''`로 격리한다.
수집 ID와 UTC 시작·종료 시각을 두 점검 EVD에 공유하며 별도 재실행하지 않는다.
명시적인 source/fixture 불일치행은 producer의 판정 입력에서 제외하고,
정제 가능한 관측과 검증된 다른 행은 보존한다. 후속 검사 불가 및 보조
`result.json` 저장 실패는 incomplete와 예외 타입만 남기며 기존 행을 보존한다.

범위는 DECLARED 로컬 CPU custom-config loader이며 installed target은 null,
runner hook은 미검증이다. 최종 target CPU 결과는 아래에 기록한다.

사례별 디렉터리 생성·fixture 복사 실패는 해당 사례의 준비 실패와 child 미실행으로
기록하고 다음 사례를 계속한다. 앞서 검증된 정상 baseline과 import 위반 행,
실제 수집 ID·시각은 보존하며 capture는 incomplete로 반환한다.

선정 0.31 소스와 해당 CPU 환경을 지정한 정식 소비 제안 실행 명령은 다음과 같다.
부모는 normal로 실행하고 도구가 필수 `-O`·`-OO` child를 생성한다.

```sh
cd "$BYUL_B1_REPO/update"
PYTHONPATH="$BYUL_B1_REPO/update/src:$BYUL_B1_REPO/update:$VLLM_SOURCE" \
  "$CPU_VENV/bin/python" -m tools.b1_dynamic_code_integration_cpu \
  --source-profile target031 --source-root "$VLLM_SOURCE" \
  --output-root "$FRESH_DYNAMIC_PRODUCT_EVIDENCE"
```


## target031 최종 실제 결과 (2026-10-29)

고정 제품 snapshot `7f196917a06649f9cea7b90c636ff9a2b103c4fe`에서
`target031`을 직접 실행했다. 수집 1회의 기존 12행을 두 EVD가 공유하고 전·후 builtin
대조는 각 12회 완료했다. trust=false 6건은 실제 trust gate에서 거부되어 표식이
없었고 true 6건은 import·constructor 및 선택 모듈의 검토 fixture SHA를 확인했다.
normal·`-O`·`-OO` 각각 4건의 실제 optimize 값을 기록했다. 모든 child와 부모의
source·fixture·component 전후 검증, 보조 저장이 완료됐다. 두 EVD는 같은 ENV·PLAN
exact Ref, 수집 ID·실제 UTC 창·정제 행을 공유한다.

두 점검은 COMPLETED·DECLARED이고 security/functional/performance는 모두 UNKNOWN이다.
Store 오류 0건, 저장 PACK exact Ref를 통한 재판정 Ref 동일, 정제 첨부 2개의
bytes·SHA 일치를 확인했다. 수집은 72.66초, 전체 파이프라인은 73.99초였다.
기업 승인 목록·지원 mode·실제 설치 loader와 외부 runner 연결은 A 잔여다.
installed target은 null, runner hook·영구 등록은 false를 유지한다.

- 수집 ID: `dynamic-cpu-32ae9433b81549969c52ff44faa662f3`
- UTC 수집 창: `2026-10-29T08:08:45.368431+00:00` ~ `2026-10-29T08:09:57.525357+00:00`
- collector SHA256: `5c74ada591ae00df75c52cf7eb920dce586aec9d16e547846391e35bce10b269`
- producer SHA256: `cd771e9cfb699fd858c814889071941b281b8ef5172e0b08807ec3ad2333b284`

|객체|ID|revision|SHA256|
|---|---|---|---|
|ENV|`ENV-B-DYNAMIC-CPU-TARGET031`|1|`0421ad08a404a4fdbf80267f6626cdcd7505a29079e402f3a04032a0cea4539c`|
|PLAN|`PLAN-01M63B8J4X5KN8R9T96VNVCQFW`|1|`b38c795b0045e3d74b79eed7a459de1a20308a2ba4701abadc720b51f35d8bb1`|
|PACK|`PACK-B-SUP09-11-DYNAMIC-CPU-TARGET031`|1|`5eff45a15ddcfb5c73a9adbbccf7e79d9092a26551bc8cb1ce39f2f771e49b51`|
|EVD SUP-09|`EVD-01M63B8J4YCEM33KW6Q6HTM4MQ`|1|`e247778c9e294079ce360040019d4157ab15bbaace4d75a66a21b076f6a41611`|
|EVD SUP-11|`EVD-01M63BAT4GFS98Y2DXKEWFJB1F`|1|`4dbec3187d040cd1814f75a2a16b18f697bf944f429bead0dd7e88f6783edc60`|
|RES SUP-09|`RES-01M63BATCP4D5QJSJQX5ZA41VK`|1|`4ca45d9ce701819e3b62ceade3569505004d0df871f5175f9c7a8c8eb5140545`|
|RES SUP-11|`RES-01M63BATCQ2QPANG7YAEP351P0`|1|`831af42d1ed03db8e7a76c97edae0b75b026b6a0b1848f817d30528f64f49f68`|
|RPT|`RPT-01M63BATCVDC7FJRHZF7WKCKT0`|1|`d6b60ffeeaaa00af5da9319687d062126df6615883d5db2727cf368d183ab3c9`|

실제 명령(`update/` cwd, normal 부모)은 다음과 같다.

```sh
env PYTHONPATH=src:.:/private/tmp/byul-req10-target031-20261029 \
  VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 VLLM_LOGGING_LEVEL=CRITICAL \
  /private/tmp/byul-req10-target031-cpu-20261029/bin/python \
  -m tools.b1_dynamic_code_integration_cpu --source-profile target031 \
  --source-root /private/tmp/byul-req10-target031-20261029 \
  --output-root /private/tmp/byul-sup09-11-product-target031-20261029-2
```

안전 요약·정확 Ref·정제 첨부는 위 `-2` 출력의 `summary.json`, `postverify.json`,
`store/`에 보존했다. 최초 `-1` 실제 결과도 보존했다. Python 3.12.13,
Torch 2.13.0, Transformers 5.17.0, xgrammar 0.2.7 환경의 152개 의존성은 이전
SUP10의 freeze와 버전 차이 0건이다. `reused-evidence.json`은 원본 152개 freeze와
정적 정합 자료의 경로·SHA를 참조하며 재설치·중복 freeze를 수행하지 않았다.

reference5d의 기존 12사례·전후 24대조 원본은 유지했다. 두 source의 `get_config`와
두 CPU 환경의 `resolve_trust_remote_code` 함수 AST가 각각 동일하다는 정적 근거로
기존 trust/mode loader 분기를 재사용했다. 새 전후 검증·공유 EVD 연결의 정상 경로는
선정 target031에서 직접 확인했다. 서로 다른 의존성 조합의 관측을 판본만 바뀐
업데이트 비교쌍으로 사용하지 않는다. 기존 12입력 재사용은 새 12시험 개발 수치에
추가하지 않는다.

직접 collector 회귀 35개(0.51초)는 준비 단계 mkdir·copytree 실패 뒤에도 검증된
normal 거부·O import의 SUP09·SUP11 독립 FAIL을 보존하는 분기를 포함한다.
원문 예외·config 본문·custom Python 원문은 제품 첨부에 저장하지 않았다.
사례별 cache와 표식은 새 소유 출력 root에 보존했으며 child는 모두 종료됐다.
