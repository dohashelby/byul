# A측 구현 중단점 — 2026-11-08 Codex

작업 위치 `/Users/doha/Desktop/Buyl/byul-dev1-k8s`, 브랜치 `codex/dev1-p1-producers`, HEAD `8402ff89` 위 미커밋 변경이다.
사용자는 **커밋하지 않고 LIF-06·SUP-03·REQ-09·10의 실제 소비 연결 중 GPU 없이 할 수 있는 구현을 계속**하도록 지시했다.
커밋·푸시 0. 원 루트 AGENTS.md는 이 worktree와 `/Users/doha/Desktop/byul`에서 삭제된 상태다. `.claude/`는 수정하지 않았다.
동시에 들어온 사용자 결정 문서 커밋은 보존했다. TP2 비용·2B 다운로드 승인은 해당 EC2 세션에만 적용하며 여기서는 사용하지 않았다.

## 이번 실제 연결

상세 지원 계약·코드·원 참조·한계·재현 도구는 `consumption-links-cpu.md`다. 최초 구성요소 문서와 fixture는 역사 원본으로 유지한다.

- LIF-06: 실제 ID assignment→admission/attempt→전송 payload→worker 상태→모델 forward. 진행 요청의 원 reload_weights 부분 반영 후 실패·명시 복구를 선택 CPU parameter A/B/A/A로 확인했다. 자동 rollback·전체 weights/KV kernel read 증명이 아니다.
- SUP-03: 실제 boot Python read→config 내용→worker HF config, tokenizer loader→실제 cached 복사→요청 renderer 계보. native Rust 읽기·전체 보조물·fresh boot 인가/집행 미확인.
- REQ-10: OPT 실제 worker/model logits·동일 metadata·Sampler, exact allowed 집합·top-k/p·penalty·실제 regex grammar. 7요청16단계. V2·모든 옵션·GPU 미확인.
- REQ-09: 랜덤 소형 Qwen2-VL·합성 tokenizer·dummy weights로 raw RGB/gray→실제 vLLM MM worker/gather→전처리→모델 inputs_embeds→Sampler 4요청8단계. 별도 실제 HF processor/vision/language 4사례에서 token 불일치 거부·정상 복구. 선정 2B pretrained/GPU 미확인.

원 GAP 두 개와 CPU 부분 실패 후보를 보존했다. 선택한 연결·사례 확인을 전체 capture/PASS로 올리지 않는다.
기존 등록 GPU producer8종·B 전용 소비/판정·원 Ref/FAIL·계약·팩33은 유지한다.

## 검증

구현·시험·문서 초안 확정 뒤 1,698파일 snapshot을 고정했다. 최초 관련 검증 416 passed/7 failed/기존 제외1(pytest2.16초/부모2.355초)의 원본을 보존했다.
7건은 손상된 forward가 제외되면 남은 step만으로 요청 연결이 complete가 되는 소비자 결함이었다. 기존 시험·assert를 유지하고 실제 출력 token 수와 forward/sample 수를 대조하도록 제품을 고쳤다.
보수 뒤 동일 관련 직접·인접·기존 producer·센서·worker shard·V1/V2·transition·source gate·불변식 **423 passed/기존 제외1**(pytest2.16초/부모2.331초). 각 검증 중 고정 파일 변경0.
최종 소스 목록466파일, manifest `2697785e449d7793d6f96f3de266a768c1c87346dfe3b7023e2b25c928fa788c`, PACK-BASELINE@33 원 hash 유지.
현황표 --check·diff --check 통과. offline wheel 생성·별도 target 설치 후 guard355파일과 source bytes 일치, 준비 자산26개 및 세 새 실제 fixture의 설치본 소비 결과 일치.
wheel SHA `3d31d261c054aeb5c110bd9f4c280bb9b7d883c994950564e6184c4a898ae11b`. 검증 원본은 `consumption-validation/` 및 `delta/`에 있다.
초기 구성요소 시험 283/28/42의 결과는 각 문서에 보존하며 겹치는 source gate를 고유 개수로 합산하지 않는다.
전체 suite·Linux CI·GPU 시험은 이번 묶음에서 실행하지 않았다.

원 stdout/stderr·callback·실패 시도·source snapshot·pytest 출력의 루트:
`/Users/doha/.codex/visualizations/2026/11/08/01a1b99a-d7c6-79e0-8db2-4cb4b4cc71ed`.
추가 actual captures는 `consumption-links/capture-v10`·`mm-model/capture-v2`·`mm-model/worker-capture-v3`다.
저장소 fixture는 해당 원 JSON의 bytes 복사이며 변형 회귀 사례와 구분한다.

## 자원·후속 경계

다운로드·제품 의존성·AWS 작업 0. `guard-a-links-codex-1108`은 이번 실제 수집에만 쓴 소유 Pod이며 삭제 완료를 최종 inventory에서 확인했다. 기존 serving-engine-dev1-k3d-18·engine-test-probe-dev1-k3d-18 두 Pod는 Running/재시작0이다.
재현은 새 별도 sleep 전용 CPU Pod에서 `tools.consumption_links_capture`·`tools.multimodal_worker_capture`·`tools.multimodal_processor_model_capture`의 필수 인자를 명시한다.
기존 serving Pod에서 엔진을 다시 띄우지 않는다. output/prepared/합성 MM 경로는 덮어쓰지 않는다.

선정 모델 EC2 시험·GPU/KV kernel 계측, native Rust 읽기 귀속 센서, 전체 공급 인가/집행, B 정책/소비자 편입은 각각 남은 경계다.
CPU EXAMPLE을 등록 GPU 근거나 전체 분기 완료로 바꾸지 않는다. 커밋·push·새 비용·다운로드를 자동 진행하지 않는다.

## continuation gate

요청한 네 분기의 기존 Python/V1 소비 경계에서 GPU 없이 실행할 수 있는 실제 연결·부정 사례·원 실패 보존·설치본 소비를 마쳤다.
선정 pretrained 모델은 로컬에 없고 다운로드 승인은 EC2 TP2 세션에 한정된다. V2·KV kernel·전체 GPU weights 소비는 해당 장치/모드가 필요하다.
Rust 파일 읽기는 이 Python module-local open 관측에 나타나지 않아 별도 native syscall/FFI 수집 경계가 필요하며 이번 지원 계약으로 증명하지 않는다.
공급 인가/집행과 B 정책/소비자 변경은 기존 소유권과 정책 입력을 유지한다. 이런 미완료를 GPU 대기 하나로 합치거나 완료/PASS로 올리지 않는다.
현재 이 작업을 이어가는 background 수집·스케줄·agent는 없다. 커밋하지 않은 변경과 원자료를 보존한다.
