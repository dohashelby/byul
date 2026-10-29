# LIF-14 실제 Torch CPU profiler 종료 관측

새 CPU 관측 컴포넌트는 고정 vLLM 0.31.0 소스
`db9527a46873454610df6dbedf79a36d6bf1a7f6`의 WorkerProfiler,
TorchProfilerWrapper와 실제 ProfilerConfig를 사용한다. torch는 child 실행 시에만
lazy import한다. 제품 의존성·lock·공통 registry/CLI·팩은 변경하지 않는다.

정상과 소유 출력 경로가 regular file인 오류 사례를 별도 child process로 실행한다.
CPU activities, delay/wait/warmup/max iteration 0, shapes/memory/stack/flops=false,
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

직접·기존 RuleTable/core run 회귀는 79 passed(pytest 0.59초)다. source import의
optional torch 경계, native 미수집·export 오류 분리·functional 독립 FAIL,
ENV binding·첨부 실패 보존·미설정 component pin을 검증했다.


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
foreign helper/빈 scope 거부를 포함한다. 새 component pin 연결 전 실제 CPU 측정은
추가하지 않았으며, 후속 실제 결과·정확 Ref는 핀 연결 뒤 갱신한다.
