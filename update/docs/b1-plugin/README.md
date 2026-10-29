# SUP-10 actual installed plugin CPU branch

참조: vLLM 5d35ce3934a7804b298559d2d99eec33792e1eb9. native 참조
 db9527a46873454610df6dbedf79a36d6bf1a7f6와 plugins/__init__.py,
plugins/io_processors/__init__.py, v1/sample/logits_processor/__init__.py의 bytes는
같다. 전체 소스·의존성·실제 설치판이 같다는 주장은 아니다.

공식 tests/plugins_tests/test_endpoint_plugins.py는 실제 dummy plugin과 일부
대역 discovery/task gating을 다룬다. io processor 시험은 entry_points/config/renderer를
대역으로 주입하고, V2 loader CPU 시험도 fake entry point를 쓴다. 그 assertion을
새 실행기로 복제하는 대신 실제 설치 metadata·import·factory·hook 순서의 공백을 채운다.

본인 이전 미커밋 무해 fixture 초안을 재사용한다. 별도 CPU venv에만 local test
배포물을 설치하며 제품 dependencies/lock·시스템 환경은 변경하지 않는다. 일반/endpoint/
logits/io 각각 실제 loader를 새 child process에서 실행한다. 발견 metadata를 patch하지
않고 실제 import 표식과 함수 표식을 기록한다. 엔진·daemon·네트워크 listener·모델은
기동하지 않는다. endpoint는 FastAPI/TestClient의 in-memory ASGI hook까지만 확인한다.
IO는 plugin_from_init 명시 경로의 factory/constructor까지이며 model-config 선택이나
실제 model I/O는 확인하지 않는다. logits는 실제 V1 loader import/type 로딩까지다.

사례10개: general allowlist/빈 값/unset, endpoint allowlist/빈 값/unset/task 불일치,
logits allowlist, IO allowlist/두 허용 중 하나 선택. 정상 fixture 이름과 challenge 이름은
시험용 식별자이며 기업 승인 목록이 아니다. 일반/endpoint/IO의 source filter 보장은
해당 scope에서 검사하지만 logits에는 VLLM_PLUGINS 보장이 없어 같은 기준을 적용하지
않는다. endpoint task miss의 import/factory 실행은 관측으로 남기고 이를 새 기업 정책
FAIL로 바꾸지 않는다. 실제 task miss 뒤 attach/init/handler 실행은 별도 source 위반이다.

전용 projection과 기존 rule_table fail_when_any를 쓴다. 정상·누락·실행 오류·잘린 event
수집·후속 정상 실패를 구분하고, 확인된 독립 위반은 다른 수집 실패가 지우지 않는다.
보안 PASS 규칙은 없다. 표식 이름/phase만 허용하며 원문·Secret·예외 본문을 기록하지 않는다.
bootstrap에서 이미 fixture가 import되면 target attribution을 UNKNOWN으로 남긴다.
각 사례 뒤 actual general loader와 무해 hook의 후속 정상 대조를 별도 기록한다.
이는 엔진 생성이나 전체 서비스 생존의 증명이 아니다.

```sh
uv pip install --python "$CPU_VENV/bin/python" --no-deps "$BYUL_B1_REPO/update/tests/fixtures/b_plugin_probe"
export VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= VLLM_PLUGINS=
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
export PYTHONPATH="$VLLM_SOURCE:$BYUL_B1_REPO/update/src"
"$CPU_VENV/bin/python" "$BYUL_B1_REPO/update/tools/b1_plugin_cpu.py" --source-root "$VLLM_SOURCE" --workdir "$FRESH_PLUGIN_EVIDENCE"
```

열린 관련 PR을 먼저 검토했다. PR43/46(input admission)과 코드 의존성이 없고,
PR47(response/logger), PR26(template)의 기능도 다르므로 독립 검토/통합 단위다.
제품 등록/CLI/팩·공통 수집 연결은 A 담당이다. A44의 검증 SHA
2d21057402af23f76303c39caf53f8832b4f1a3c와 PACK31/DRAFT Ref는 확인했으나,
B profile 등록·CheckDef 연결·첨부 보존 계약의 인계는 별도로 필요하다.
이 standalone JSON은 아직 정식 ENV/POL/PLAN/EVD/RES/RPT가 아니다.
실제 설치물별 승인·runner/IO 경로·권한은 기업 정책과 실제 대상에서 추가 확인한다.

## 실제 CPU 결과와 남은 범위

10사례+독립 정상 후속10건을 완료했다. 전체 부모 벽시계52.02초,
추가 pytest 계약60 passed(0.34초/부모0.63초). 설치 fixture .py bytes가
검토한 저장소 원문과 일치하고 실행 전후 snapshot도 같음을 확인했다.

- general: 허용 이름만 import/hook, 빈 값은 미로딩, unset은 두 fixture를 import/hook.
- endpoint: 허용 plugin import→factory→attach→init→ASGI handler(200).
  빈 값/unset은 import 없음·fixture route404. task 불일치는 import→factory 후
  selected list에서 제외됐다. attach/init/handler는 호출되지 않았다.
- V1 logits: allowed-name 설정과 무관하게 두 설치 클래스를 import/load했다.
  소스에 해당 필터 보장이 없어 이를 source-filter 위반으로 판정하지 않는다.
- IO: 한 이름 허용 시 해당 factory/constructor만 호출. 둘 다 허용하고 하나를
  선택하면 두 factory가 실행되고 선택한 class만 생성됐다.
- 매 사례 뒤 approved general loader/hook이 정상 동작했다. 이는 CPU loader 대조다.

실제 엔진 기동0, 외부 HTTP0. in-memory ASGI 요청은3건이며 네트워크 listener는 없다.
표식 처리 중 실제 예외/수집 누락은 없고, 참조 source-filter 위반도 확인되지 않았다.
기업의 승인 설치물·group별 정책·실제 도달 runner/IO·신원/권한·생성 정상 대조는
이 CPU 시험으로 확인하지 않았다. 설치 대상은 null, 전체 SUP-10 보안은 UNKNOWN이다.

세부 증거는 저장소 형제 byul-b1-plugin-evidence-20261028의 run-1/result.json,
각 case JSON/event marker, cpu-assertions-1.json, contracts-1.txt, snapshot-1.json,
fixture-install.txt/cpu-install.txt에 보존한다. source 원문과 제품 lock은 수정하지 않았다.
이것은 검토 가능한 독립 묶음이며 PR43/46에 대한 runtime 의존성은 없다.

A 최소 연결 요청: SUP-10의 이 scoped CPU profile을 선택하는 등록/CLI·CheckDef의
plugin_cpu_phases/plugin_loading_confirmed_violations/완전성 관측과 기존 rule_table을
연결하는 변경안이다. 미수집/오류는 해당 case UNKNOWN, 독립 FAIL은 다른 오류와
무관하게 보존하고 full security PASS 규칙은 추가하지 않는다. 실제 설치 범위와
기업 승인 정책을 받은 뒤 설치 inventory와 결과를 대응하고, A 확정 소스에서
실행→EVD→RES/RPT와 첨부 저장 실패 회귀를 수행한다. 현재 standalone JSON은
정식 제품 객체가 아니고 실제 설치판의 MEASURED로 소급하지 않는다.

## 0.31 source 선정과 직접 collector API

기존 10사례·후속 10건·ASGI 3건은 같은 corpus로 재사용한다. 새 10개 시험 개발로
집계하지 않는다. 기본 reference5d는 기존 5d35ce3이며, target031은 정확히
`db9527a46873454610df6dbedf79a36d6bf1a7f6`를 선택한다. HEAD나 설치 버전을 보고
source를 자동 선택하지 않는다.

`tools.b1_plugin_cpu.capture(source_root, workdir, source_profile='reference5d')`는
source·fixture를 검증한 뒤 실제 child 실행으로 얻은 dict를 직접 반환한다. 기존 CLI는
이 API를 사용하며 `--source-profile reference5d|target031`을 받는다. 외부 결과 JSON을
수집값으로 주입하는 진입점은 제공하지 않는다. 정식 소비 도구는 이 반환 record를
기존 project/judge와 EvidenceDraft→core 객체 흐름에 연결한다. 영구 제품 등록과
기업 설치물 승인·권한·실제 worker/IO 소비는 A 경계로 유지한다.

검증은 다음 고정 범위를 사용한다.

- 공통 guard/packs/lock/framework는 component commit
  `ac8ffbadbb85dbbfe7c96fa36785ae9546e2c8c7`의 전체 snapshot으로 확인한다.
- 기존 LIF-14 검증 함수를 재사용하기 전에 해당 도구의 own 파일·함수 경로와 bytes
  `1af43f4ae68271b494a8528cd1190763612735bb963ec00f0a1f4992fb802308`을 확인한다.
  그 함수가 기존 B1 helper의 own 신원·고정 bytes·scope 검사를 수행한다.
- fixture는 검토된 pyproject SHA256, Python 파일 6개의 정확한 set/hash,
  entrypoint 표를 상수로 둔다. 기대값을 runtime Git 이력이나 현재 파일로 재승인하지 않는다.
  실제 실행에서는 fixture source 전체 snapshot의 untracked/ignored shadow도 검사한다.
- 설치 배포물의 .py set/bytes, 이름·버전, 네 group의 실제 entrypoint inventory,
  로드된 fixture 모듈의 설치 경로를 확인한다. 실제 vLLM 전체 source bytes와 import 경로도
  고정 source별로 확인한다. target031은 기존 정확 CPU 의존성 확인을 재사용한다.
- 최적화 모드 실행은 source·target 실행 전에 거부한다. collector 자체 bytes도 사례
  실행 전에 기록하고 후속 검사에서 일치를 확인한다. 후속 검증 오류는 incomplete와
  검사 상태·예외 타입으로 남기며 앞선 정제 event·독립 위반을 보존한다.

source 차이는 기존 source filter의 의미와 분리한다. 두 source의 group/general/endpoint
loader, V1 logits loader, IO 선택 함수, endpoint init_state는 AST가 동일하다.
endpoint attach는 0.31의 단순 attach와 5d의 같은 path/method route 교체·OpenAPI 재설정이
다르다. 기존 corpus의 고유 fixture route는 이 충돌 동작을 시험하지 않는다.
route 충돌·OpenAPI 교체·model config 기반 IO 선택·실제 runner token/IO pre/post 소비는
이번 CPU 관측으로 확인하지 않는다. installed_target=null, runner_hook_verified=false와
보안 PASS 규칙 부재를 유지한다.

```sh
# workdir은 새 외부 디렉터리. local tools/src를 source보다 먼저 선택한다.
VLLM_TARGET_DEVICE=cpu VLLM_LOGGING_LEVEL=CRITICAL CUDA_VISIBLE_DEVICES= HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH="src:.:$VLLM_SOURCE" "$CPU_VENV/bin/python" tools/b1_plugin_cpu.py --source-profile target031 --source-root "$VLLM_SOURCE" --workdir "$FRESH_PLUGIN_EVIDENCE"
```
