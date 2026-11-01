# LIF04 느린 수신·buffer 비용 제품 묶음 초안

부모 A39/56/4·A1 19/27/2·공동 c9af729와 기존 source/미커밋/worktree·팩/Ref·A2/B bytes·실증을 보존한다. PR122의774416698e593fdc40aa1b05b9985f1a389a8eab에서 별도 소유 공간으로 이어간다. 검증 소스bef4e0e/설치201파일·CLI24Ref는 그판본의CPU/EXAMPLE이며 실제 배포REQ11 wheel04f7856과 다르다.

다음 대상은 LIF04의 SC07-03(느린 SSE 연결)과 SC07-04(실제 활성 response debug buffering)다. 연결별 전송량·host/buffer 비용·역압력·연결 종료 후 해당 buffer 회수를 관측한다. 내부 요청/KV/참조 종결인 LIF10, debug 허용 필드인 B OBS03, rank 예외/사망/지연/통신정지의 감지·상위전달인 LIF08과 각각 구분한다. LIF08을 요청 timeout 하나로 대체하지 않는다.

기존 stream_probe의 slow-reader-v1은 chunk_bytes1~4096, interval_ms1~250, close_after_reads1~32의 명시 입력과 read_count/read_pause_count/read_observations·client_close/eof/terminal/capture 차이를 제공한다. normal은 기존 SSE EOF 경로다. 처음 한 번 read_delay_ms를 두는 legacy 동작을 지속 수신 지연으로 올리지 않는다. read threshold에서 닫힌 결과를 서버 EOF·완전수집으로 처리하지 않는다. 시간 namespace/source deadline guard·출력/시간 bound·원문 비저장 코드를 재사용한다.

느리게 읽은 시간/바이트·클라이언트 close는 서버 buffer 비용이나 실제 역압력 증명이 아니다. 현재 process snapshot의 주체/UID/PID/start/namespace와 native8~11의 요청/출력 사실을 재사용하되, file-log bytes·프로브 RSS·GPU 등록을 serving 연결별 buffer 값으로 대신하지 않는다. 필요한 buffer producer·실제 handler/connection/request/call/session/PID/start·전후세대·소스/image 결합과 읽기·소비·반환 계측을 별도로 확정한다. 권한을 늘리거나 타인 worker/공유서버를 중단하지 않는다.

선택 normal/slow/slow-close 각각 전후 정상 요청과 하나의 작고 한정된 SSE를 재사용한다. 종료 임계값·read 간격·chunk와 전체 실행/출력 한도는 명시하며 안전한 작은 부하가 실제 buffer에 압력을 주었는지 따로 관측한다. 압력이 없거나 자원 귀속이 불명하면 위반 부재를 PASS로 올리지 않는다. 정책은 정확ENV/target/source/image/model inventory와 alias·run/window·출력 대기/buffer/time 한도·연결 종료 뒤 회수 기한·collector 주체를 요구한다. 실행 중 읽은 hash나 임의 한도를 승인하지 않는다.

debug 분기는 실제 활성 route·debug 설정/수집 주체·소스·허용 정책·메모리 예산이 확정돼야 한다. 미확정 `debug=true` payload·추정 endpoint·profiler 제어를 지원 계약으로 만들지 않는다. 실제 비사용 근거가 없으면 비적용이 아니라 미확인이다. 이 조건이 SSE 준비된 개발의 선행조건이 되지 않게 한다.

구현은 명시 수신 프로필/관측·정책 계약 → 기존 engine/probe 두 Pod·native/stdlib 공개 전송과 소유 계측 → LIF04 전용 분석·명시DRAFT팩/registry·기존CLI → 정상/후보/누락/교체/restart/후속수집·첨부·정리 → 소스고정/영향/Linux/설치/정확Ref/초안PR/정확HEAD CI/인계 순서다. 이 직접 연결에는 Gateway Pod를 요구하지 않는다. 원팩31·다른76정의와 두 분기를 보존한다. 기존 source/lab/image gate·native0.32 미지원·B 전용판정 경계를 유지한다.

제품 선택은 `[executors] stream_buffer="kubernetes"`, `[stream_buffer] target="<정확 대상 JSON>"; prepared="<고정 native12 manifest>"`다(TOML에서는 각 키를 별도 줄에 쓴다). `guard prepare-lifecycle-sensor --profile vllm-native-lifecycle-metadata/12 --output <새 외부 경로>`와 `python tools/build_kubernetes_stream_buffer_pack.py --seed reference/pack-seeds/PACK-BASELINE-31 --output <새 외부 팩>`을 준비한 뒤 기존 select/run/judge/report/verify/evidence export/restore를 사용한다. API는 `KubernetesStreamBufferHTTP(target, env, read_profiles=...)`의 닫힌 `__call__(network, [request], timeout)`이다. 임의 경로/헤더/본문·private 호출은 지원하지 않는다.

한 실행의 같은 UUID 아래 normal-before JSON → normal SSE → slow SSE → slow-close SSE → normal-after JSON 다섯 요청만 지원한다. 고정 `/v1/completions`, hello, temperature0, JSON1token/SSE8token이며 probe 시간은 요청10초·전체최대300초, 출력2MiB·본문128KiB다. HTTP profile `kubernetes-owned-stream-buffer-http-v1`, collection profile `kubernetes-stream-buffer-observation-v1`의 POL16개는 `stream_buffer_contract.POLICY_TYPES`가 정본이다. 정확 ENV/target/prepared hash, aware600초 이하 유효창, 쓰기버퍼/전체process RSS/send대기/회수기한 네 예산, slow/close 각각 chunk·간격·최대read 여섯 입력, debug_mode=unverified를 명시한다. 회수기한은 정책 선언으로 보존하며 서로 다른 시계의 client close와 대조하지 않는다.

native12는 immutable base8을 private로 읽으며 기본8·9~11 bytes와 설정을 유지한다. 실제 Uvicorn `RequestResponseCycle.run_asgi/send`, `FlowControl.drain`, `Protocol.connection_lost`만 전체 소스와 메서드 실행 code를 검토해 감싼다. [공식 고정 소스](https://github.com/Kludex/uvicorn/tree/3eb9a9ab9af69005c687728097461c1fd2a95db9/uvicorn/protocols/http)의 세 파일 SHA는 sensor.SOURCE_ADDITIONS에 리터럴로 고정한다. 참조 판본0.54.0이 실제 선택 이미지에 포함됐는지는 확인되지 않았다. 다른 판본을 실행 중 읽어 승인하지 않는다. CPython3.12.3/3.12.14 optimize0 자기 code 계약도 오프라인 검토 후 리터럴로 고정한다.

실제 await/call 직전·직후 시간을 관측 I/O와 구분하며 drain을 같은 요청/연결/PID/start/session의 부모 send 구간에 결합한다. 선택 asyncio selector TCP의 실제 bytearray 길이 또는 deque의 bytes/bytearray/memoryview 최대1024조각 길이 합과 Linux own socket TIOCOUTQ/전후 inode를 읽는다. deque는 설치 Linux 표준 라이브러리의 실제 get_write_buffer_size 구현과 대조했으며 이를 모두 bytes 한 조각으로 가정하지 않는다. 전체process RSS는 연결별 배타 비용이 아니다. uvloop/다른 transport/한도 초과/지원 OS가 아닌 kernel queue는 null이고0으로 채우지 않는다. socket 주소·본문·헤더·원문 예외를 보존하지 않는다. weak transport256개·요청8개/연결·2048행/2MiB를 넘으면 관측 공백이다. 추가 recv/send·worker 종료·별도 Pod Python을 실제 hook으로 위장하지 않는다.

제품은 관측 가능한 send/drain/close·PID/start·세대 범위만 연결한다. 예산초과는 확인되지 않은 후보이고, 정상 대조의 succeeded=null·세 축 UNKNOWN·수집된 producer/inventory/release/scope=false다. lab=false 등 I/O 전 구성 관문에서 거부되면 기존 공통 실행기의 미수집 value=null/collected=false를 유지한다. 미수집을 false 관측으로 채우지 않는다. 전체 serving buffer 인벤토리·연결 종료 시계/회수·실제 debug는 미완료다. 관측 helper나 등록만으로 두 분기 완료를 기록하지 않는다. 실제 source/principal/worker/메모리 비용/기업 예산이 없으면 UNKNOWN이고 독립 FAIL은 보존한다. 소유 Linux 소켓 시험과 고정 실제 메서드 본문 시험도 CPU/EXAMPLE이며 실제 vLLM/Kubernetes/GPU 실증이 아니다.

원GPU창종료·추가Start0·마지막확인stopped·46.66USD 기존상한·CI결제차단을 유지한다. 새 실제 기동/기간/비용/IAM 승인은 없으며 fresh ENV/POL/자원·소유권/차례·관측/정리 책임과 새 시간 승인 없이는 실제 실증을 재개하지 않는다. 준비된 독립 개발은 계속한다.

- 제품 검증 마감: 정확241f9f21abbc98cb84cbeb90e9cff0ca22ff881f/검토pinbb66d60의Windows 192 passed, 1 skipped, 1 deselected in 53.37s·부모54.262초,동일1240bytes Linux 740 passed, 1 deselected in 69.79s (0:01:09)·부모70.167초,source/wheel/격리설치207파일일치·설치CLI4/정확24Ref왕복7.998초를확인했다. 첫수집오류·685통과/41실패와각보수로그·Linux734통과/1실패를보존했다. 새시험의선택의존성/원본class code·연결컨텍스트·미수집null가정을보수하고Windows stat/fstat의동일API ctime안정성과Linux실제deque buffer를제품에서보수했다. Linux소유TCP의실제buffer/outq/inode/RSS/abort뒤표본과고정Uvicorn메서드의실제send/drain부모귀속·예외보존,같은stdlib실제HTTP5요청·누락/교체/restart/후속오류·원문비저장을검증했다. 전체serving buffer회수·debug/실제Kubernetes/vLLM/GPU실증은아니다. 검증중변경/새제외/관문제외/관측hash자동승인없음,B315전체pin·B원c9/A2선정12/B원본27/native8~11/원팩31을보존했다. 기존Linux전체6942는이전native10소스근거로유지하고변하지않은전체시험은반복하지않았다. 모든새판정UNKNOWN·정상null·수집된신뢰/전체범위false이며전체LIF04/부모A완료는추가하지않는다. 원GPU창종료/Start0/마지막stopped·CI외부차단을유지한다.
