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
현재 component pin 미설정 시 실제 도구 실행은 거부한다. module 소스 고정 commit을
주세션에 인계한 뒤 정식 핀 값을 연결하고 실제 pipeline을 검증한다.

직접·기존 RuleTable/core run 회귀는 79 passed(pytest 0.59초)다. source import의
optional torch 경계, native 미수집·export 오류 분리·functional 독립 FAIL,
ENV binding·첨부 실패 보존·미설정 component pin을 검증했다.
