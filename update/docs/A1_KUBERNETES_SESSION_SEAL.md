# 선택 session 종료 제어 /1

공개 `KubernetesLifecycleReader.seal_session() -> {"sealed": bool}`는 선정 prepared session
하나의 제어와 기록 종결 확인을 제공한다. B PR95 e317a42109b30c59edd10a0f91c9d39b3847ad9f의
호출 ABI에 맞춘다. 기존 constructor의 exact ENV/lab=true/engine 세대 gate는 그대로다.
native0.32·source/image 미지원 상태를 해제하지 않는다.

고정 stdlib SESSION_SEALER만 선택 engine 컨테이너에서 실행한다. 인자는32lowerhex session
하나이며 경로·PID·명령·Python 본문을 호출자에게 받지 않는다. 기존5초 소유 exec watchdog과
namespace/Pod/container/image/restart/start의 전후 검증을 사용한다. serving process·worker·
Pod·다른 session에 signal/kill/delete를 보내지 않는다.

프로그램은 `/guard-lifecycle/data/<session>/SEALED`만 O_EXCL/O_NOFOLLOW로 생성한다.
디렉터리의 소유 UID·private mode, 모든 경로 component의 nofollow, 빈 regular marker의
소유 UID/mode/nlink=1·경로 identity를 확인한다. 같은 소유 marker에 한한 반복 호출은 가능하며
기존 파일을 truncate/수정/삭제하거나 디렉터리를 만들지 않는다.

marker 생성만으로 sealed=true를 반환하지 않는다. 최대32개 선정 session recording의
끝부분 최대16384bytes만 읽고 실제 terminal END 형식과 사유, private regular/nlink=1,
전후 fd/path identity와 두 번의 안정된 census를 확인한다. 빈 session·미종결·알 수 없는 파일·
늦은 쓰기·경로 교체·parent cleanup 미확인은 false다. 확인은 helper 내부 최대4초이며
실제 시간이 부족하면 false가 될 수 있다. marker는 이미 생성됐을 수 있어 false를
제어 미전송으로 해석하지 않는다. 이후 capture_session으로 기존 성공 증거를 계속 수집한다.

종결 tail 확인은 전체 hash chain/source/module/code/PID/start/실제 요청 귀속을 검증하지 않는다.
B는 기존 capture_session과 parser·source/code/image·stable census를 확인한 뒤 소비해야 한다.
BOUND_REACHED/CONTROL_UNAVAILABLE는 기록 종료일 수 있으나 수집 완결성과 보안 판정은 별도다.
API sealed=true도 전체 serving process·GPU/worker 회수·다른 session 종결 증명이 아니다.
B의 owned_session_closed는 이 선정 recording session 범위로만 소비해야 한다.

소유 engine 전후 세대·pipe·control protocol가 맞지 않으면 고정 false와 collection_gap을 남긴다.
원문 recording tail/예외/stdout/stderr는 report나 artifact에 저장하지 않는다. 기존 읽기에서
이미 확보한 관측을 후속 parent cleanup gap 때문에 지우지 않는다.

이 경계는 Online 전체 완료가 아니다. ONLINE_INPUT_PROFILE, 실제 serving hook→B callback,
metadata.online_input/parser, hook/callback provenance, request/call/session/PID/start 결합은
별도 고정 묶음이 필요하다. 실제 ENV/POL/실행/복구·정리 소유자, source/image 인수 및 GPU는
미실증이며 서버 stopped·추가Start중단을 유지한다.
