# LIF-06 V1 worker 모델 호출 관측 (A측, B 의존 반환 3의 부분 구현, 2026-11-08)

이 문서는 최초 dispatch 관측 범위의 기록이다. 같은 날 추가한 실제 admission/worker 연결과 진행 중 reload 부분 실패·명시 복구는
`consumption-links-cpu.md`를 따른다. 이 최초 fixture와 한계를 소급해서 바꾸지 않는다.

요청: `gpu-b/consolidation-20261106/DEPENDENCY_RETURNS.md` 3번. 스케줄러 배정을 실제 weights/KV 소비로 올리지 않고,
요청별 worker 모델 호출의 원 참조를 제공한다. **전체 LIF-06 반환 완료가 아니다.**

| 반환물 | 범위 |
|---|---|
| 관측기 | `adapters/vllm_transition_dispatch_callback.py`; vLLM 0.31.0 V1 `GPUModelRunner._model_forward`, 기존 원 소스 SHA `bb903dff…`와 코드 자격 검사 |
| 준비 | `engine_callbacks.prepare_assets(..., transition_dispatches=True)`, 기본 꺼짐. 기존 production Pod 자산을 덮어쓰지 않음 |
| 생산자 | `collectors/transition_dispatch_producer.py`; 관측기·PID/rank helper·생산자 bytes SHA를 묶는 `guard-engine-callback-transition-dispatch@1` |
| 공통 입력 | 새 종류 `transition-dispatch`, LIF-06 전용. `forward_entered`·`forward_returned`·`forward_failed` |
| 투영 | `projection(record)`: 원 행·worker 바인딩·미확인 사유. 기존 native index에 편입한 행이 아니며 `actual_weights_cache_consumption_verified=False` 유지 |
| 등록 | 미등록. 기존 등록된 `generation-transition`·`request-attempt`의 소스와 등록 SHA는 보존 |

## 관측하는 것

모델 호출 전후의 실제 `InputBatch._req_ids`, `CachedRequestState`의 약한 객체 신원(이 프로세스의 attempt 후보),
모델·parameter 집합·KV backing tensor 집합의 약한 객체 신원을 기록한다. 원 경로·요청 본문·tensor 내용은 수집하지 않는다.
원 콜백 session/process/call·source/entry SHA, worker PID·시작 ticks·rank를 보존한다.

객체 신원은 같은 프로세스·같은 관측 세션 안에서만 비교한다. Python 주소 재사용은 약한 참조로 구분한다.
parameter/KV 객체가 같아도 제자리 내용 변경은 알 수 없다. parameter 집합이 모델 forward에 존재했다는 사실이
모든 parameter의 실제 연산 소비를 증명하지 않는다. KV backing tensor 신원도 요청별 KV 블록 읽기의 증거가 아니다.

공통 검사는 요청·attempt·worker·native call별 CALL/RETURN 또는 CALL/RAISE 쌍, 시각 순서와 원 source/entry/session 참조를 확인한다.
다른 세션·미자격 진입·주입 문맥은 진단으로 남긴다. 같은 attempt에서 dispatch 신원이 달라지면 후보
`dispatch_identity_changed_within_attempt`를 남기며 수집 오류가 지우지 않는다. 후보의 허용 여부는 B의 정책 판정이다.

## 실제 CPU 확인

별도 임시 Pod `guard-a-lif06-codex-1108`에서 기존 읽기 전용 OPT-125m과 이미 있는 CPU 이미지를 사용했다.
image digest `045fb648a61cfcd32d4b9ca5d0c391013afadae0178d2e23ac292597f2f64036`, arm64, Python 3.12.15.
추가 다운로드·AWS 실행은 없다. 두 요청(각 출력 2토큰) 사이 prefix reset을 수행했고 실제 호출 전후 8행을 수집했다.
`tests/fixtures/transition_dispatch_k3d_20261108.json`은 EXAMPLE이다. 이 시나리오는 live weights 교체나 rollback 시험이 아니다.

원 실행에는 공통 GAP `CACHE_CALLBACK_WEAK_OBJECT_IDENTITY_UNAVAILABLE`·`DISTRIBUTED_NON_EXECUTE_RPC_UNSUPPORTED`가 있다.
생산자에 원 GAP도 전달하므로 등록을 가정해도 실제 기록은 complete가 아니다. GAP을 제거한 파생 사례는 시험 안에서만 별도로 구분한다.
최초 실행은 driver의 multiprocessing `__main__` guard 누락으로 시작에 실패했다. 원 stdout/stderr를 보존하고 driver를 보수한 두 번째 실행에서 수집했다.
원자료는 Codex 작업 산출물 폴더의 `lif06/`에 있다. 임시 Pod 삭제 완료와 기존 서빙·probe Pod의 Running 상태를 확인했다.

## 남은 반환 범위와 B 연결

등록된 `generation-transition`은 EngineCore 전환과 scheduler 배정을 제공한다. 이 기록은 별도의 worker dispatch를 제공한다.
둘을 시간이나 alias만으로 합치지 않는다. 실제 승인 요청·attempt의 native 연결, weights bytes와 요청별 KV read,
reload의 부분 실패·복구·atomicity 및 실제 전환 중 진행 요청을 묶는 원 세션 관측은 아직 남아 있다.
기존 `lifecycle_transition.load_generations/project`와 B 판정·`transition_actual_consumption_verified=False`는 수정하지 않았다.

B가 후속 소비자에 연결할 때 공통 봉투의 정확 ENV/target/prepared/session·바인딩을 검증하고 원 native 참조가 실제 수집에 있는지 대조한다.
`projection()`의 사실을 추가 증거로 보존하며 미확인 사유를 유지한다. 생산자 등록과 전체 소비 완료 판정은 이번 반환 범위에 포함하지 않는다.

## 회귀

`tests/test_transition_dispatch.py`: 실제 생산자 행→공통 입력→투영, 미등록·가정 등록, 원 GAP 보존,
source/session/진입 자격/주입 문맥, CALL 쌍 누락·중복·시각 역전·다른 attempt, 실패 호출, 신원 없음,
후속 수집 오류에도 독립 후보 보존, 자산 opt-in을 확인한다. 실행 결과는 PROGRESS에 기록한다.

macOS Python 3.12.13에서 직접·공통 계약·기존 생산자·센서·worker shard·transition·소스 고정·불변식
283개 통과, 기존 제외 1개(pytest 2.11초, 부모 2.452초). 검증 중 고정 파일 변경 0.
전체 suite·Linux CI·새 GPU 실측은 이번 결과에 포함하지 않는다.
