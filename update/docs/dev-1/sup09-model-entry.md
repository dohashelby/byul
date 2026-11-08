# SUP-09·11 model-entry 지원 계약 (개발 담당, B 의존 반환 1, 2026-11-08)

B 요청(`gpu-b/consolidation-20261106/DEPENDENCY_RETURNS.md` 1번): 기존 autoconfig·vllm-config 12사례의 import·constructor와 독립 FAIL을
유지하면서, 실제 선택된 model class·source bytes·trust·mode·constructor/forward·정상 대조를 구분하는 지원 계약. 실제 forward 사건을
기존 import로 이름만 바꾸지 말 것. 원 loader/model이 만든 실제 기록을 원/새 consumer에 각각 넣어 확인할 것.

## 반환물

| 항목 | 내용 |
|---|---|
| 계약 | `guard-model-entry/1` — `executors/dynamic_model_entry.py` (`fixtures`·`identity`·`clean_events`·`project`·`evaluate`) |
| 실제 생산자 | 같은 모듈의 `RUNNER`(컨테이너 안 `python3 -c`)와 무해 사용자 모델 fixture(`config.json`·`configuration_b2probe.py`·`modeling_b2probe.py`) |
| 수집 도구 | `tools/model_entry_capture.py` — 지정 엔진 Pod 컨테이너에서 생산자를 돌리고 행·투영을 쓴다(EXAMPLE) |
| 기존 계약 | `dynamic_code_cpu`(v1 12사례)와 `supply_boot_contract._dynamic`은 바꾸지 않았다 |

## 사례와 단계

- 진입점: `AutoConfig.from_pretrained(trust_remote_code)` → `AutoModelForCausalLM.from_config(trust_remote_code)` → forward(입력 3토큰).
  vLLM이 remote code 모델을 만들 때 쓰는 transformers 진입점이다. vLLM 엔진 기동·worker forward는 아니다.
- 사례 6개: trust(False/True) × 모드(normal/-O/-OO), 사례마다 새 자식 프로세스·새 HF 모듈 캐시, 오프라인.
- 단계 표식(사용자 모델이 직접 남김): configuration·modeling `import`, modeling `constructor`, modeling `forward`.
- 선택 사실: 만들어진 모델 객체 클래스의 정의 파일 bytes SHA-256과 fixture bytes 일치, 클래스 이름 SHA-256.
- 정상 대조: 사례 앞뒤 내장 GPT2(사용자 코드 없음) 생성·forward.

## 판정 재료

- SUP-09 `model_entry_confirmed_violations`: trust=False 사례에서 사용자 modeling 코드의 import·생성·실행.
- SUP-11 `model_entry_mode_violations`: normal trust=False가 trust gate에서 거부됐는데 같은 진입점의 -O/-OO trust=False에서 import.
- `complete`: 6사례 모두, 수집 완료·정상 대조 통과, trust=True는 수용·source 일치·import/constructor/forward 모두.
- 형식이 틀린 행은 오류로만 세고 위반을 만들지 않는다. forward가 없으면 실행 소비를 주장하지 않는다.

## 실제 생산자 결과 (k3d vLLM 0.31.0 CPU 이미지, transformers 5.17.0·torch 2.13.0+cpu, arm64, EXAMPLE)

- trust=False 세 모드 모두 trust gate 거부(`resolve_trust_remote_code`), 사용자 코드 표식 0 → 위반 없음.
- trust=True 세 모드 모두 사용자 클래스 선택(source 일치)·import/constructor/forward 관측.
- -OO에서 내장 GPT2 정상 대조가 실패한다(B가 기록한 TF5.17 GPT2 -OO 문제와 같음). 그래서 -OO 두 사례가 오류로 남고 `complete=false`.
  지침대로 정상 대조를 다른 모델로 바꾸지 않았다. -OO 지원 범위는 지원 라이브러리 담당이 정한다.
- 원 행: `tests/fixtures/model_entry_k3d_20261108.json`.

## 확인

`tests/test_dynamic_model_entry.py` 16개: fixture 신원, 실제 행의 거부·선택·단계, -OO 정상 대조 실패의 미완 보존, 대조 통과 시 complete,
SUP-09 위반(신뢰 안 된 실행)·SUP-11 위반(normal 거부 확인 시에만), trust=True의 forward·source·단계·수집 요구, 형식 오류는 위반 아님, v1 계약 불변.
공급 boot 관련 기존 시험 7개 로컬 실패는 변경 없는 HEAD에서도 같다(로컬 환경).

## B 연결 방법(제안)

`supply_boot_contract`의 SUP-09·11 평가에 `dynamic_model_entry.evaluate(rows, check_id)`를 별도 분기로 더한다(행의 `contract`로 구분).
v1 행은 기존 `_dynamic` 그대로. GPU 이미지·실제 모델의 trust 정책과 기업 코드 승인은 B·운영 입력이다.
