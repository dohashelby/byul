# LIF09 선정 READY endpoint 연결 초안

상태: 구현 초안. 기능 시험·고정 소스·설치·정확 Ref 검증·확정 전달은 아직 마감하지 않았다. native22는 현재 배포·소비 승인 판본이 아니다. 기존 확정 소비는 PR152 `c39a36b7a6f306b910f0314b0deee10b55390dc4`의 native21까지다.

SC17-01의 원래 요구는 rank별 준비 실패, 정상 canary, worker/model 세대, 실제 수용 gate, 복구 후 관측이다. 다음 endpoint 후보는 그 결합 근거 하나이며 전체 분기를 대신하지 않는다.

초안은 기존 `initialization:kubernetes` 실행기에서 명시 native22를 선택한 경우에만 부모 `make_worker_process` 반환의 reader, 같은 reader의 원 `wait_for_ready/recv`, 자식의 선정 원 `worker_main/send` 사건을 대조한다. 원 native14/19/20/21·B callback/helper 바이트와 과거 팩·정확 Ref는 보존한다. 새 실행기·외부 contracts·core/models/store/judges 변경은 없다.

Linux의 정확 stdlib Connection/FIFO만 endpoint 식별 대상으로 삼는다. 이미 소유한 fd의 fstat 결과를 세션·boot·PID namespace·time namespace·offset domain과 해시해 불투명 token으로 사용한다. 원 fd, kernel device/inode, 주소, payload, handle, peer handles, env/argv와 예외 원문은 기록하지 않는다. 이 token은 실행 소스의 승인 hash가 아니다.

부모 생성·수신 사건은 원 native14 INITIALIZATION의 정확 seq와 payload SHA에, 자식 송신 사건은 원 native21 WORKER_READY_SEND와 main/constructor/Worker generation의 정확 사건에 연결한다. 단순 PID/rank/시각 일치는 결합 근거로 사용하지 않는다. 고유한 생성·SEND RETURN READY·RECV RETURN READY, 동일한 reader 수명과 endpoint 전후 안정성, 정확 process/context/source/image/model 입력이 모두 필요하다. 누락·중복·교체·다른 세션/스레드·시계 불확실성은 결합 불가로 남긴다.

선정 reader instance의 recv만 임시 관측하고 finally 원 속성을 복원한다. class/global Connection, Pipe/Process factory와 원 wire payload는 바꾸지 않는다. 원 호출·반환·예외·close를 보존한다. `get_mp_context()`는 환경 설정을 바꿀 수 있어 관측용으로 추가 호출하지 않는다. 선정 세션 seal은 관측 상태만 닫으며 worker·Pod·GPU를 종료하지 않는다.

Windows PipeConnection은 원 vLLM 수신 코드의 Connection assert에서 recv 전에 중단됐다. HANDLE의 fstat 변환은 수행하지 않는다. Windows에서는 prepare/parser/미지원 처리 범위를 따로 검증해야 한다. fork의 observer 상속은 미검증이며 초기 후보의 긍정 결합은 명시 spawn 범위로 한정한다. native0.32·미검토 extension/elastic/forkserver는 미지원이다.

관측 결과는 `init_ready_endpoint_events`, `init_ready_endpoint_candidates`에 기존 Worker/MP 사실과 별도로 제공할 예정이다. 정확 ENV·target·run·source/image·model revision/inventory와 기존 init_* 정책을 재사용한다. 필요한 제품 검증은 기존 CLI 선정→수집→EVD/RES/RPT→verify→반출/복원→저장 팩 정확 Ref 재판정이다. 기존 정상/위반/누락/수집·첨부·정리 오류의 독립 사실과 FAIL을 보존한다.

실제 producer/worker 인증, 전체 tensor/GPU/model 적용, 모든 rank의 KV cache/compile/warmup, gateway/LB 서비스 접수, 장애 활성·복구 계보는 계속 미검증이다. 함수 반환·READY·HTTP200·endpoint 후보를 해당 의미로 승격하지 않는다. OBS02 U1–U6, 실제 운영 허용 범위와 책임, fresh 시간 승인·소유권·활성 작업·quota·잔여 총비용·ENV/POL·실행 차례·정리는 별도 입력이다. 기존 GPU 창은 종료됐고 추가 Start는 0이다.

사전 근거는 `A1-LIF09-ready-endpoint-source-audit-v1.json`, `A1-LIF09-ready-endpoint-next-contract-v1.json`, `ready-pipe-identity-feasibility/verified-contract-scope-v1.json`에 있다. Windows 소유 stdlib 4사례와 Linux 8사례는 기능 마감 또는 실제 serving 실증이 아니다. 추가 `system_utils.py`는 upstream `db9527a46873454610df6dbedf79a36d6bf1a7f6` blob `ae09ed2dbeb038151c017261363a20f2a09accae`와 과거 승인 이미지 캐시의 원 bytes가 일치한다.
