# OBS02 profiler 종료 표현·실제 수집 경계

부모A39/56/4·A1 19/27/2, 공동c9af729와 PR128 확정811ba2880408cb5d8273915b9c1727b3db486e9d, 기존 미커밋/worktree/sources·A2/B 원본·과거팩/Ref/실증을 보존한다. PLT12 실제 사본/처리/재사용, PLT10/11 issuer·session, LIF12 retry 주체 질문은 그대로 대기한다. 이 묶음의 실제 소비자는 A1 OBS02/SC23-05이며 B의 LIF14 전용 수집·판정·service/backend 시험 소유권을 유지한다.

기존 OBS02는 HTTP health/추론/client close와 native 요청 상태만 제공한다. profiler 종료가 없는 이 관측으로 SC23-05를 완료하지 않는다. 원팩31 OBS02의 세 분기·질문/정상/FAIL/UNKNOWN/독립 backend 관측 기준과 다른76개 정의 bytes를 보존한 파생팩을 기존 CLI에 연결한다. profiler가 실제 활성이고 source/주체/정책이 맞는 경우에만 추가 관측을 소비한다.

고정 vLLM0.31 소스에서 Worker.profile → WorkerProfiler.start/stop/_call_start/_call_stop/step/shutdown → TorchProfilerWrapper._start/_stop/_profiler_step 경계를 확인한다. WorkerProfiler._call_stop은 하위 _stop이 예외를 내도 _running=false를 설정하므로 내부 flag나 API200을 실제 backend 수집·산출/전송 종료로 올리지 않는다. TorchProfilerWrapper._stop은 torch stop 뒤 table/출력도 수행하므로 adapter 예외가 곧 실제 torch stop 실패라는 의미도 아니다. 정상·backend stop 실패·stop 뒤 산출 실패를 각각 구분해야 한다.

새 opt-in native15는 기존 native8 bytes를 그대로 기반으로 하여 선정 profiler wrapper의 call/parent/object·process/session/source/code·전후 _active/_running와 제한된 iteration·시각·반환/예외 종류만 관측한다. prefix/path/trace/내용/환경변수/임의 예외 원문을 읽거나 저장하지 않는다. 등록된 profiler API callback과 module 함수가 다르지 않도록 실제 등록 endpoint/dependant 호출을 source 검사 뒤 함께 연결한다. GPU worker의 실제 profiler hook을 Pod exec의 별도 Python 호출로 대체하지 않는다. 관측기는 profiler나 worker를 시작/정지하거나 callback 결과·예외·순서·정리를 변경하지 않는다.

API·worker·wrapper의 실제 call 연결은 같은 process/context/객체의 확정 중첩 관계만 기록한다. 다른 PID의 동일 request hash·가까운 시각으로 RPC/worker 제어를 결합하지 않는다. source gate를 통과한 vLLM adapter 호출·반환/예외와 wrapper flag는 backend의 독립 활동·callback 완료·trace 산출/전송·다음 정상 시작의 증명이 아니다. 새 profiler 객체·주체·세대의 교체/중복/누락/미지원 backend는 각각 UNKNOWN이고 원래 정상/독립 FAIL을 약화하지 않는다.

공개 공통 입력은 고정 native15 prepared/session, 정확 ENV/target/source/image/model·관측 유효창·선정 backend/rank/구성요소다. 기존 OBS02 닫힌 health/정상/client-close 전송을 정상 대조로 재사용하고 새 /start_profile·/stop_profile 요청을 보내지 않는다. 실제 profiler service 시험은 B가 활성 backend/route 접근조건·정상시작/수집/종료·실패 주입/산출·다음시작 및 정책을 구체화한 뒤 별도로 시행한다. 미확정 payload/binding을 지원 계약으로 만들지 않는다.

구현 순서는 source provenance와 native15 hook/schema/parser → 기존 CLI/정제 projection/원기준 파생팩 → 정상/실패/stop후산출실패/지연시작/자동stop/중복·누락/교체·후속수집·첨부 오류/정확Ref 회귀·문서 → 고정 source/hash → 영향/Linux·설치본 → 확정커밋·초안PR·정확CI·인계다. 관련 공통 파일은 A1만 수정하고 기존 native8~14와 모든 A2/B 전용 파일은 보존한다. 큰 미완성 부분을 다른 준비된 개발의 선행조건으로 삼지 않는다.

GPU 승인창 종료·추가Start0·마지막 확인stopped·기존46.66USD/과거사용량·보존120GiB EBS과금·CI결제 차단을 유지한다. 실제 재개에는 새 시간 승인·같은 자원의 fresh소유권/활성작업/quota/잔여총비용·정확ENV/POL·준비된 profiler 소비자와 복구/정리 책임이 필요하다. 이번 소스 개발은 새 기동/기간/비용/권한 승인이 아니다. 전체 OBS02/부모A/전체77개 완료 수를 늘리지 않는다.
