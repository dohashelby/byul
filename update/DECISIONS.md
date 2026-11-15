## 2026-11-15 — 결정 14 변경: 기준 판본을 V1 엔진으로 실행

- 사용자 결정: 클로드 대화의 "v1으로 하자. 그게 할 일이 더 없잖아"와 현재 #277 작업 트리에서 V1 전환을 계속하라는 지시. 기준 판본의 upstream 기본 엔진은 V0 그대로지만 MVP의 선택 엔진은 V1(`VLLM_USE_V1=1`)이다. 후보도 V1이다. 기동 선택은 adapter의 판본·정확 이미지 데이터로 두고 공통 실행기/판정에 판본 분기를 넣지 않는다.
- V0 D1~D3 설계와 V0 전용 M-A6-V1~V4는 중단한다. 결정34의 V0 멀티모달 캐시 예외와 결정10의 V0 D4·D5 예외는 과거 실제 V0 구성에만 적용한다. 새 V1 실행의 미구현을 그 예외로 완료 처리하지 않는다. 과거 EVD/RES/RPT와 원자료는 그대로 보존한다.
- M-A6-V5(조건 충족 시 판본표 assert 변경), V6(후보 lifecycle 행 확장)은 이미 승인됐고 유지한다. V5의 V0 `guided_json` 대응을 초기 V1의 제약 소비 근거로 간주하지 않는다.
- 렌더 기록에는 선택 엔진과 profile이 정한 환경변수만 남기고 ENV에는 요청한 구성임을 밝힌다. target 조립은 실제 Pod spec의 정확 변수 값·누락·중복·valueFrom을 대조한다. 비교 조건에는 변수 이름 대신 선택 엔진 의미를 넣는다. 이 대조는 실제 엔진 모드·기능·GPU 관측 자격의 증명이 아니다.
- 초기 V1의 `Scheduler.__init__`는 LoRA 구성을 거부한다. 기존 LoRA 세대·최신 비동기/profiler 옵션과 V2 runner 요구는 판본 데이터의 금지 인자/환경변수 값으로 렌더 단계에서 거부한다. 지정 모델·REQ-16 정책/기준을 바꾸거나 그 분기를 자동 UNKNOWN 승인으로 처리하지 않는다.
- 남은 것: 기준 V1 대상표·함수 의미·정확 핀, CPython 패치별 자기 지문, 지정 모델/멀티모달·옵션 실제 기동과 GPU 등록. V1 선택만으로 기존 GPU 자격을 상속하거나 엔진 내부 실행기를 열지 않는다. 개발 상세는 [판본 적용표 24절](docs/dev-1/ma6-version-applicability.md#24-기준-판본-v1-전환-2026-11-15).

## 2026-11-15 — 사용자 결정 M-A6-V1~V6: J02 두 판본 비교를 위한 판본표

아래 V0 전용 V1~V4는 이후의 결정14 변경으로 중단됐다. V5·V6 승인은 유지한다.

근거: docs/dev-1/ma6-version-applicability.md 19~21절. 여섯 질문 모두 추천안으로 답했다.
- V1 "네 가지 모두 승인": 기준 V0 D1 관측.
  - (a) SWAP 선점은 해제가 아니라 별도 사건 `PREEMPT_SWAP`으로 기록하고, V1 recompute 선점과 같은 판정 사실로 세지 않는다.
  - (b) 캐시 적중은 블록 소유(처음 계산한 요청)를 추적해, 다른 요청이 만든 블록을 재사용한 경우만 센다. 추적이 끊긴 요청은 UNKNOWN이다.
  - (c) `cache_salt` 없음은 관측 공백이 아니라 "식별값에 신원·설정 구분이 없다"는 관측 사실로 둔다. 판정 기준은 바꾸지 않는다.
  - (d) 같은 요청 ID 재제출 거부는 "중복 실행 없음"의 관측으로 기록한다. 다른 ID를 쓰는 재시도는 V1과 같은 시도 추적으로 본다.
- V2 "기능 부재 UNKNOWN": REQ-18 기준 쪽은 V0에 지속 batch slot이 없으므로 결정 9 형식의 원인 있는 UNKNOWN으로 둔다. PASS·비적용으로 바꾸지 않는다.
- V3 "관측 결과로 판정": LIF-08 기준 쪽에서 정지를 주입했을 때 "감지 안 됨"은 실제 엔진 동작이다. 기존 판정 기준에 그대로 넘긴다(FAIL 가능). 판정 기준은 바꾸지 않는다.
- V4 "대상표 추가": REQ-05 기준 쪽은 V0 models 경로(`entrypoints/openai/api_server.show_available_models`·`serving_models`)에 같은 의미의 lifecycle 대상표 행을 더한다.
- V5 "조건 충족 시 assert 변경 허용": REQ-10 `sampling_validation`은 조건이 갖춰진 판본만 연다.
  - 0.7.0: `structured_outputs→guided_json` 대응과 `tool_choice=required` 분기를 실행기가 소비한 뒤.
  - 0.30.0: runner_capture 후보 행을 넣은 뒤.
  - 그때 `test_code_selection_observation.py::test_only_input_validation_kubernetes_executor_is_exposed_on_old_releases`의 판본표 assert를 그 상태에 맞게 고칠 수 있다. 판정 기준은 바꾸지 않는다.
- V6 "진행": 후보 0.30.0 lifecycle 센서 계열이 판본별 정확 행을 여러 개 받게 한다.
  - 대상 20모듈 중 의미가 같은 (a) 16개 행을 넣는다. 검토가 남은 4개(scheduler·default_loader·gpu_model_runner·gpu_input_batch)는 의미 검토 전까지 공백으로 둔다.
  - 센서 15개의 CPython 자기 코드 지문과 생산자 신원을 다시 만든다. 0.31.0 기존 GPU 등록은 상속하지 않는다(0.31.0은 MVP 비교 쌍이 아님).
  - 고정값 검사를 넓히지 않는다. GPU 등록은 별도다.
