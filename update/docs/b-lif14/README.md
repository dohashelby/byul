# LIF-14 실제 Torch CPU profiler 종료 관측

새 CPU 관측 컴포넌트는 고정 vLLM 0.31.0 소스
`db9527a46873454610df6dbedf79a36d6bf1a7f6`의 WorkerProfiler,
TorchProfilerWrapper와 실제 ProfilerConfig를 사용한다. torch는 child 실행 시에만
lazy import한다. 제품 의존성·lock·공통 registry/CLI·팩은 변경하지 않는다.

정상과 소유 출력 경로가 regular file인 오류 사례를 별도 child process로 실행한다.
CPU activities, local_rank=1, delay/wait/warmup/max iteration 0, shapes/memory/stack/flops=false,
4×4 CPU 연산과 고정 tag는 유한 기술 fixture다. 각 사례는 실제 start→tag 연산→stop,
같은 wrapper의 next start→tag→stop을 관측한다. 오류 사례의 regular file은 소유 경로와
fixture bytes·symlink 여부를 확인한 뒤 디렉터리로 복원한다.

관측은 wrapper.is_running, 가능한 torch.autograd._profiler_enabled, 실제 stop/trace
handler/export 진입, 정제된 stop warning 종류, tag event 개수와 산출 파일 크기·digest다.
raw trace·표·로그·CPU 값은 제품 첨부에 넣지 않는다. 새 소유 임시 디렉터리에 산출한 뒤
finally에서 실제 wrapper.shutdown과 필요한 소유 native stop을 시도하고 native false와
소유 디렉터리 정리를 확인한다. raw child stdout/stderr는 메모리에서만 처리한다.

사전 실제 CPU 가능성 확인에서는 정상/regular-file 경로 모두 생성자·start가 가능했다.
regular-file 오류는 실제 default trace handler의 디렉터리 생성 단계에서 발생했고,
wrapper.stop은 경고 뒤 None을 반환했다. native 수집은 false이고 tag event는 남았다.
따라서 None을 종료 성공이나 HTTP 200으로 취급하지 않고 export 실패와 수집 상태를
각각 보존한다. 모델·engine·HTTP·GPU·공유 workload는 기동하거나 변경하지 않는다.

판정 제안은 기존 RuleTable의 functional 축에만 wrapper 종료 표시(false)와 수집한
native 수집 상태(true)의 불일치 FAIL을 둔다. native probe는 start 전 false와 start 중
true 양성 대조가 확인된 phase만 종료 판정에 사용하고, 나머지는 collected=false/null로
유지한다. 보안 PASS·기능 PASS 규칙은 없다.
기한 정책은 미설정이고 native probe 미지원은 collected=false/null로 유지한다.
이 관측만으로 전체 LIF-14 완료를 집계하지 않는다.

실행기는 기존 EvidenceDraft 계약을 따른다. 전용 도구는 외부
PACK-B-LIF14-CPU-PROPOSAL/DRAFT와 reference CPU ENV/POL을 만들고 process-local로
등록해 기존 RunConfig→EVD→RES→RPT를 연결한다. 영구 등록과 기업 종료 정책은 A 인계다.

기존 B1 두 파일 overlay gate는 유지한다. 도구는 검토된 새 module commit을
product_component_commit으로 명시 고정한 뒤 기존 verify_snapshot의 전체
PINNED_PATHS 검사를 사용한다. c9 공동 기준 metadata도 별도로 보존한다.
component pin은 검토된 module snapshot commit으로 명시 고정한다. 소스가 바뀌면
이전 pin 검사는 실행을 거부하며, 새 고정 값을 연결한 뒤 실제 pipeline을 검증한다.
검사 helper 자체도 자기 checkout의 파일·함수 경로 신원과 bytes SHA256
858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf,
승인된 정확 scope/base를 검증한다. foreign noop helper와 빈 scope를 거부하며
기존 B1 source·공동 기준은 변경하지 않는다.

검증 전 source 신원 확인을 마친 child의 qualified CPU 관측은 후속 검증 오류와
함께 보존한다. post source 검사는 명시적인 source 불일치와 I/O·Git 검사 불가를
status·예외 타입으로 구분하고 전체 실행은 ABORTED로 남긴다. parent 후속 검사가
실패해도 각 child의 기존 사전·후속 상태를 덮어쓰지 않는다. child 후속 검사가
불완전하면 소유 CPU 과정의 관측은 유지하되 선택된 source pin의 최종 귀속은
미확인으로 표시한다. 사전 신원 미확인·native 미수집을 수집된 false로 만들지 않는다.
독립적인 qualified 종료 불일치는 기존 core/RuleTable을 거쳐 functional FAIL로
남고 source 불일치를 target 소비 PASS로 표현하지 않는다.

이 수정 묶음 직접·기존 RuleTable/core 회귀는 **88 passed / pytest 0.84초**다.
parent/child 후속 검사 불가·명시 불일치, core EVD의 독립 FAIL 보존, initial 미확인,
foreign helper/빈 scope 거부를 포함한다.


## 고정 수정본 실제 결과

component `c0af481dad68000d14211f3e03b07e62ab1ee7ea`, producer SHA256
`ff3d4e1d80c779e709a2acfff5a97b19d34c53ce93c052f63649db4842394c3a`로
실행했다. Python 3.12.13·torch 2.13.0 CPU 환경에서 normal과 trace_directory_failure,
각 same-wrapper next phase의 native before=false/during=true/after=false를 수집했다.
오류 phase는 RuntimeError 경고·None 반환·trace 미산출, 소유 경로 복구 뒤 next는
실제 trace 산출을 확인했다. local_rank=1은 source의 rank별 표 출력 분기 입력이며
이 결과를 TP=1 worker rank0·HTTP·GPU 실증으로 사용하지 않는다.

결과는 COMPLETED/DECLARED, 관측 3개·정제 첨부 1개, 부모 9.414초다.
전축 UNKNOWN이며 기업 종료 기한은 null/collected=false다. native 수집 종료와
소유 raw 디렉터리 삭제, Store 오류 0개·exact Ref 재판정 일치·RPT 정확 Ref를 확인했다.
151개 의존성 정합성 검사도 통과했다. 원본과 의존성·재판정 확인은
`/private/tmp/byul-lif14-product-cpu-20261029-2/`에 보존한다. 초기 고정본 기록은
`/private/tmp/byul-lif14-product-cpu-20261029-1/`에 별도로 남긴다.

- PACK `PACK-B-LIF14-CPU-PROPOSAL@1`: `c356d237978a0c8f7f7111b0c3ecb9b7cb13491ba9e5c4946d67ed46ef4f5ac2`
- EVD `EVD-01M636FHQVYKG4ECKM91FZXJE0@1`: `b6173f08f40cf066d24338b0a5e4f15f6a270136d6ba37fa46a1c7a380f6ffc0`
- RES `RES-01M636FTT84D2C83QQAJ4R7NCP@1`: `331c6c14f10f8b2ae3de671585cc522fc153d7dd8727f3f1e7e111ebb4bfe1fb`
- RPT `RPT-01M636FTTAWX27S683W5KSCC0Q@1`: `42d43571851aa4b237f1f901ffa810a2afed628e1aa1e59c37157d8d359e0bc7`

검토 지적 2건은 수정 후 해당 경로 해결 확인을 마쳤다. 공통·불변식 회귀는
134 passed/기존 1 deselected, pytest 0.73초·부모 0.880초다.
wheel/설치 파일 157개 일치, 격리 설치 검증 성공이며 기록은
`/private/tmp/byul-lif14-installed-20261029/summary.json`이다.

## 재실행과 A 연결

이 checkout의 `update`에서 source 전체 이력과 위 고정 component commit이 있는 상태로
실행한다. source-root는 깨끗한 db9527a 전체 source이고 output-root는 새 경로를 사용한다.
CPU 의존성 환경은 실제 기록의 dependencies.freeze.txt와 대조한다.

```sh
PYTHONPATH="$PWD/src:$PWD:/private/tmp/byul-req10-target031-20261029" \
TMPDIR=/private/tmp VLLM_TARGET_DEVICE=cpu CUDA_VISIBLE_DEVICES= VLLM_PLUGINS= \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
/private/tmp/byul-req10-target031-cpu-20261029/bin/python \
  -m tools.lif14_profiler_cpu \
  --source-root /private/tmp/byul-req10-target031-20261029 \
  --output-root /private/tmp/byul-lif14-new-run
```

A 영구 등록 제안은 `profiler_lifecycle_cpu:component-cpu-proposal` 키와
`ProfilerLifecycleCPUExecutor` factory다. 입력은 source_root·owned_parent 두 값이며
관측 JSON 입력을 받지 않는다. DRAFT check/pack·ENV·POL·PLAN은 실제 결과 폴더에 있다.
A가 공통 registry/팩에 반영한 확정 SHA/팩 Ref를 제공하면 해당 승인 delta를 고정해
정식 CLI 소비를 검증한다. 기존 B1 gate의 공통 소스 허용 범위는 그대로 유지한다.

실제 서비스 분기는 A가 확인한 이미지 digest·활성 profiler backend/route·Pod 세대와
공유 run 담당자, 승인된 소유 출력 경로·복구 절차를 입력받아 연결한다. 종료 기한과
미완료 표시는 기업 정책 결정이 남아 있다. 현 직접 Pod HTTP profile의 지원 범위와
lab/source/digest gate를 유지하며, 서비스 시험을 위해 profiler를 임의 활성화하지 않는다.
