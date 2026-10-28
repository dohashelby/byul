# B-1 CPU 결과와 잔여 조건 — 2026-10-28

참조 vLLM SHA: 5d35ce3934a7804b298559d2d99eec33792e1eb9.
PR43과 그 실제 CPU 증거는 보존한다. 이번 변경은 PR46의 B-1 전용 worktree에 있다.
제품 dependency/lock, 공통 core/models/store/contracts, A/B-2 파일은 변경하지 않았다.

| 구분 | 실제 확인 범위 | 결과 |
| --- | --- | --- |
| REQ-10 구조 제한 | 요청 클래스와 실제 xgrammar validator/Grammar 변환 | 15사례+후속15건, 4.24초. 깊이 63/64 수용, 65 거부·변환 비진입. tag 정상 변환/깊이 거부 확인 |
| REQ-07/08/09 입력 | 실제 request 변환 메서드→HfRenderer→InputProcessor | 26사례+후속26건, 도구4.19초/부모5.21초. template/kwargs 길이, 재사용 token ID/길이/절단, mask/offset 확인 |
| REQ-10 sampling | 네 실제 요청 클래스, SamplingParams/BeamSearchParams, 실제 tokenizer | 18사례+후속18건, 도구4.15초/부모5.19초. stop/n/beam/bad_words cap4/5와 정규화 확인 |
| REQ-09 serialized embeds | 실제 safe_load_prompt_embeds, 실제 ModelConfig, 직접 생성한 작은 tensor | 10사례+후속10건, 도구4.15초/부모5.19초. shape/width/dtype/feature 검사와 float64→float32/squeeze 확인 |
| 공식 assertion | 고정 원문 AST 노드, 실제 CPU fixture로 setup 이식 | 입력/stop/beam/정규화 20 passed, pytest4.47초/부모6.11초. 깊이 노드2 passed, pytest4.21초/부모5.54초 |
| 대역·인접·불변식 | BYUL 실행/관측/판정 계약 | 375 passed·1 deselected(기존 표지), pytest51.90초/부모52.05초. 후속 전용 수정 뒤 직접119 passed, pytest1.10초/부모1.24초 |

실제 CPU 유한 사례는 총69개이며 정상 후속69건을 별도로 수행했다. API 요청 수와
다르다. 실제 API 요청0, 엔진 기동0, Docker/GPU/모델 weights 다운로드/AWS 사용0이다.
실제 참조 경계에서 확인된 위반은 없었다. 누락·미실행 범위를 포함한 전체 CHK의
보안 PASS로 집계하지 않는다. runner_consumption_verified=false를 유지한다.

## 실제 수용·거부의 해석

- 재사용 token과 text를 함께 준 경우 token 경로가 수용됐다. 상위 API 노출·허용
  주체/문맥·승인 정책을 측정하지 않았으므로 이것만으로 우회 FAIL을 만들지 않는다.
- 명시한 left/right 절단의 최종 admission token 해시가 예상 변환과 일치했다.
  해당 기업의 절단 허용 정책을 채택한 것이 아니다.
- 내부 EngineInput은 1차원/hidden-width 불일치 embedding을 admission에서 수용했다.
  반면 실제 공개 payload 로더는 해당 shape/width를 거부했다. 두 경로를 합쳐
  공개 우회나 실제 모델 소비/오염을 주장하지 않는다.
- 정상 kwargs suffix15는 수용, suffix16은 렌더 후 입력 길이에서 거부됐다.
  template override API 접근 통제나 공급 template 안전성은 별도다.
- description/anyOf 크기 표본의 변환 성공은 크기·복잡도·CPU 비용 한도 충족이 아니다.
  해당 정책/예산이 없어 이 범위는 UNKNOWN이다.

## 실패와 수정

최초 embedding 묶음은 dataclasses.replace로 이미 정규화된 ModelConfig를 복제하는
setup 단계에서 ValidationError가 났다. 실제 ModelConfig 생성자로 비활성 설정을
새로 구성하도록 수정했다. 판정/시험 기대를 완화하지 않았다. 첫 실패 결과와
snapshot은 보존하고 새 snapshot의 세 CPU 묶음을 모두 다시 검증했다.
검증 명령 준비 중 잘못 지정한 파일 경로는 tests 미실행으로 기록돼 있으며,
실제 파일명 확인 후 정상 명령을 수행했다. 이를 회귀 실패나 skip으로 세지 않는다.

## 구체적인 외부 차단과 재개 조건

| 남은 범위 | 근거/차단 | 재개 첫 행동 |
| --- | --- | --- |
| 제품 CLI→EVD→RES/RPT | A의 명시적 공동 검증 SHA·등록/팩·정제/첨부 보존 계약 미인계 | A_HANDOFF.md의 최소 입력을 받아 전용 diff 이식 검토 후 정확 소스에서 연결 회귀 |
| REQ-10 기업 크기/복잡도·비용 | 제출한 기업 한도/예산과 실측 대상 backend가 없음 | 승인 기준·단위·대상 backend를 고정해 작은 경계 입력/계측 선정 |
| REQ-10 V2 allowed IDs/logit_bias1024, stop128 | 실제 RequestState 기본/dense CPU 생성 두 번 모두 UvaBuffer의 torch.zeros(pin_memory=True)에서 RuntimeError. CPU torch2.14.0+cpu의 단독 pin_memory 생성도 실패 | 소유·승인된 지원 accelerator/UVA 환경과 V2 활성/최종 소비 관측을 고정. 원문 test_gpu_logit_bias의 기존 GPU 조건을 유지해 실행 |
| REQ-08 공개 경로/신원·문맥 | SDK 내부 재사용 경로만 실행; 실제 endpoint·주체·허용/금지 정책·최종 소비 미정 | API 노출·승인 문맥과 정책을 확인하고 기존 수집/전송에 정상·거부·최종 소비 연결 |
| REQ-09 모델 consumer | 로더/admission 이후 worker·모델 연산을 수행하지 않음 | 설치판/실제 모델의 dtype·embedding 규칙 및 모델 제출·소비 관측 확정 |
| REQ-07 template/kwargs·fallback 실제 설정 | 고정 HF text fixture만 확인. 설치판의 override 허용·parser/멀티모달/fallback 활성 여부 미정 | 실제 선택 template digest·지원 입력·설정과 native 관측을 A에게 받아 기존 template 실행 확장 |

이것은 B-1 전체 CHK 완료나 모든 backend·모델 조합의 완료 선언이 아니다.
현재 고정 참조와 준비된 입력에 대해 독립적으로 실행 가능한 CPU 범위를 처리했고,
남은 범위는 위 실제 외부 조건 또는 accelerator 실행 범위에 의존한다. 새 정책·
가짜 engine·대역을 추가해 그 조건을 대신 증명하지 않는다.

## 증거 위치

기존 증거: 저장소 형제 byul-b-evidence-20261028의 구조 제한/PR43 기록.
B-1 증거: byul-b1-evidence-20261028의 final-input-2, final-sampling-2,
final-embeddings-2/result.json, final-cpu-commands-2.json, input-official-1.txt,
b1-final-code-snapshot-3.json, b1-final-contracts-1b.txt/3.txt,
v2-cpu-attempt-1.json/2.json, v2-pin-memory-check.json.
입력은 정규 객체 해시, 소스/설정은 파일 bytes 해시이며 서로 혼용하지 않는다.
생성 fixture 설정의 DECLARED와 실제 참조 코드 실행 결과를 구분한다.
공식 AST 시험은 원문 assertion/parametrize를 유지했지만 실제 fixture로 setup을
바꾼 이식이며 원문 전체 suite·실제 엔진 시험과 구분한다.

## 후속: 실제 OnlineRenderer core 요청 gate

같은 입력 기능의 미완료 CPU 지점을 찾아 PR46에 모았다. `--suite online`의 16사례와
후속 정상16건을 실제 OnlineRenderer.render_chat/preprocess_chat 및 InputProcessor로
실행했다(도구4.34초). 전용/기존 직접 회귀128 passed, pytest1.13초/부모1.27초.

재사용 ID의 정상/빈 list/bool/음수/OOV/길이 초과를 대조하고, echo일 때 전달 ID를
무시해 일반 메시지로 렌더한 뒤 admission되는 것을 확인했다. 실제 request와 sampling
metadata에서 재사용 ID 키가 제거됐으며 저장본에는 해시/길이/유무만 남겼다.
trust_request_chat_template=false의 직접/nested override는 native typed ErrorResponse
code400으로 거부됐고 preprocess에 진입하지 않았다. 허용한 template/kwargs 길이
15/16 경계도 확인했다. 기본 trust_request_mm_kwargs=false에서 code_revision과
media kwargs는 실제 guard에서 거부됐다. opt-in의 text-only 수용은 별도 관측이며
실제 멀티모달 processor·코드 판본·승인 대응은 미확인이다.

최초 online 수집기는 SamplingParams에 없는 직접 kv_transfer_params 속성을 읽어
normal/후속 metadata 수집에서 execution_error가 났다. 원문 소스의 실제 표현
extra_args.kv_transfer_params로 수정하고 새 snapshot으로 16사례/후속을 재실행했다.
초기 native 거부와 실패 증거는 online-run-1에 보존하며 통과로 소급하지 않는다.
도구는 사례/후속 execution_error를 completed_with_case_errors로 구분하고 성공으로
종료하지 않도록 했다. 이 값은 standalone 도구 진단이며 공용 ExecStatus가 아니다.

증거: online-run-2/result.json, online-contracts-2.txt, online-snapshot-2.json,
online-attempt-1-diagnosis.json. HTTP listener·engine-aware model/identity 검사·실제
worker/모델 제출은 실행하지 않았다. API/설치판의 신원·승인·최종 소비 공백은 유지한다.
기존 69사례와 이번16사례는 서로 다른 CPU 분기이며 전체 CHK 완료로 합산하지 않는다.
