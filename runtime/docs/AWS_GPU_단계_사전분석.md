# AWS GPU 단계 연결 사전 분석 (서버 미사용)

2026-11-02에 저장된 `dev-20261101-dashboard-copy-final/observation/tetragon.jsonl`을 읽기 전용으로 열고, 저장소 밖 `/tmp` 임시 폴더에만 합성 시간표를 만들었다. 원본은 수정하지 않았다. AWS-01~08의 기존 판정·결과는 그대로다. 이 재생은 실제 GPU 단계의 검증이 아니다.

## 단계 연결 전 위험과 재생

`ExecPolicy.classify_exec`는 활성 단계가 있으면 미승인 실행을 `outside_allowlist`로 판정한다. 기존 `events.py`는 모든 Pod의 exec에 엔진 정책을 붙였다. 이번 수정은 Tetragon 원본의 namespace와 `pod_labels`가 intent의 엔진 selector에 맞는 경우에만 엔진 판정을 붙인다. Pod labels가 없으면 귀속을 추정하지 않는다. `TRUSTED_BASES`는 `declared`·`test_controller`뿐이다. 엔진 로그에서 만든 `engine_signal`은 단계 전용 허용을 넓히지 않아 `phase_signal_unverified`가 될 수 있다.

원본은 exec 309건(엔진 272, 그 밖 37), exit 256건, kprobe 2건이다. 새 처리 기준에서 exit는 미지원 건너뜀 256건이다. 기존 checkpoint에 저장된 손상 255건은 고치지 않는다.

| 합성 입력 | 엔진 판정 | 그 밖의 Pod | 설명 |
| --- | --- | --- | --- |
| 시간표 없음 | `phase_unknown` 272 | 엔진 판정 없음 37 | 현재 배포와 같은 입력 부재 |
| 06:00~12:00 UTC 전체를 `inferring`/`declared`로 가정 | `allowed_in_phase` 51, `outside_allowlist` 221 | 엔진 판정 없음 37 | 실제 단계라고 주장하지 않는 상한 영향 분석 |
| 같은 구간을 `engine_signal`로 가정 | `allowed_in_phase` 51, `outside_allowlist` 221 | 엔진 판정 없음 37 | 현 intent의 `phase_binaries`가 세 단계에서 같아 전용 허용이 없음 |

따라서 시간표만 연결하면 엔진 정상 실행에서도 221건의 탐지가 생길 수 있다. 이번 증거에서는 `phase_signal_unverified`가 0건이나, 단계별로 다른 허용 목록을 추가하면 이 판정이 나타날 수 있다. 실제 시간표, 단계 경계, 로그 시계 동기화는 미확인이다.

## 정상 실행 후보와 근거

다음 수는 원본의 엔진 Pod 실행 경로 빈도다. 모두 `phase_unknown` 기록이므로 특정 단계의 승인 근거가 되지 않는다. 현재 intent의 `/usr/bin/python3`·`/usr/bin/python3.12` 및 wrapper는 기존 시작안이고, 이번에 허용 목록을 넓히지 않았다.

| 경로 | 건수 | 제안과 필요한 확인 |
| --- | ---: | --- |
| `/sbin/ldconfig`, `/sbin/ldconfig.real` | 각 42 | 기동 또는 라이브러리 갱신 시점 확인 후 그 단계에 한정 |
| `/usr/bin/nvidia-ctk` | 36 | 컨테이너 준비 시점과 engine container 귀속 확인 후 한정 |
| `/usr/bin/uname` | 12 | 호출 주체·시점 확인 후 한정 |
| `/usr/local/lib/python3.12/dist-packages/triton/backends/nvidia/bin/ptxas` | 35 | GPU 컴파일 구간 확인 후 `compiling` 후보 |
| `/usr/local/cuda/bin/ptxas`, `/usr/local/cuda/bin/nvcc` | 각 6 | 컴파일 구간 확인 후 `compiling` 후보 |
| `/usr/bin/python3` | 45 | 현재 세 단계 허용 시작안의 관측 근거. 단계별 실제 필요성은 미확인 |
| `/usr/local/bin/python`, `/usr/local/bin/vllm` | 각 6 | wrapper·서빙 프로세스와 호출 시점 확인 후 단계 한정 후보 |
| `/usr/local/bin/vllm-nonroot-entrypoint.sh` | 6 | 현재 공통 허용 시작안의 관측 근거 |
| `/usr/bin/id`, `/usr/bin/awk`, `/usr/sbin/ip` | 각 6 | 기동 스크립트 호출과 시점 확인 후 한정 후보 |
| `/proc/self/exe`, `/dev/fd/11` | 각 6 | 동적 경로다. 넓은 허용에 넣지 않고 프로세스·실제 경로를 먼저 확인 |

그 밖의 Pod 37건은 엔진 허용 목록의 근거가 아니다. 원본의 다른 Pod 실행에는 `/usr/local/bin/python` 17건과 `/usr/local/bin/python3` 20건이 있다.

## 엔진 로그 시간표 연결 (2026-11-02 로컬 구현, 서버 미적용)

호스트 관측 수집기는 `app=serving-engine`인 Running Pod가 정확히 하나일 때 UID·이름을 확인한다. 수집기 시작·Pod UID 교체 시 현재 Pod 로그를 한 번 재구성하고, 이후에는 마지막 kubectl 시각을 기준으로 `--since-time` 증분 조회한다. 경계 시각을 1µs 겹쳐 조회하고 줄 해시로 중복을 제거한다. 조회마다 8 MiB 상한과 4초 제한을 유지하며 조회 뒤 UID를 재확인한다. 로그 조회 실패는 entries를 비우되 메모리 커서를 유지해 다음 회차에 재개한다. Pod 교체·대상 부재·다중 Pod에서는 이전 추출 상태를 버린다. 수집은 Hubble·Tetragon·target 스냅샷과 별도 스레드다.

UTF-8 손상, kubectl 시각 누락·오류, 역순 시각은 해당 줄만 건너뛰고 `extraction.skipped_lines`에 사유별 누적 건수를 남긴다. 끝에 개행이 없는 줄은 UTF-8·시각의 중간 잘림 가능성이 있어 버리고 다음 조회에서 다시 읽는다. 8 MiB 상한에 닿으면 재구성이 불완전하므로 `collector.state=incomplete_history`, `extraction.history_incomplete=true`, entries 비움으로 단계 추정을 중단한다. 다음 조회는 잘린 경계 뒤에서 이어가되, 완전하게 관찰한 새 `loading_start` 전에는 현재 단계를 복구하지 않는다. 같은 kubectl 시각 하나에 8 MiB 이상이 몰리거나 로그 회전으로 시작 경계가 이미 사라진 경우는 자동 복구가 제한된다. 이 경우 Pod 교체 또는 새 실행의 표지가 필요하다.

`phase_signals.py`의 엔진 시각 `MM-DD HH:MM:SS`에는 연도·시간대가 없다. `extract_phase_timeline(engine="vllm", timezone_offset="Z")`도 줄 앞에 kubectl RFC3339 시각이 있으면 이를 우선한다. 합성 로그의 kubectl `10:00Z`와 엔진 `19:00`을 넣어 경계가 `10:00Z`임을 시험했다. 따라서 이 연결에서 엔진의 다른 시간대를 UTC로 오독하지 않는다. 타임스탬프 없는 일반 줄은 건너뛴다. `collector.last_log_at`과 쓰기 시각의 차이인 `log_delivery_lag_seconds`는 전달 지연과 무로그 시간까지 포함하며 순수 시계 오차가 아니다. 로그 원문·prompt는 시간표에 쓰지 않는다. 요청 로그의 `request_logger`·`Received request`·`prompt` 줄은 단계 표지로 쓰지 않는 합성 시험을 추가했다. 다른 형식의 요청 로그가 표지와 같을 때의 안전성은 실제 GPU 로그에서 확인해야 한다. 호스트 kubectl은 namespace의 `pods/log` 조회 권한이 필요하고 Gateway는 기존 읽기 전용 관측 마운트만 사용한다. 현재 수집기 엔진 선택은 `vllm` 고정이므로 SGLang으로 바꿀 때 별도 패턴 근거와 설정 전환이 필요하다.

수집기는 `collector.written_at`을 2초 간격으로 원자 교체한다. Gateway는 이 시각이 15초 이상 오래되거나 수집 상태가 `running`이 아니면 열린 구간을 포함한 단계 전체를 `unknown`·`phase_unknown`으로 돌린다. 15초는 기존 관측 수집기/대상 stale 창과 같은 장애 판단 수치이며, 정상 수집 간격 2초와 조회 제한 4초에 여유를 준다. 이 오류만으로 추론 요청을 막지 않는다. `collector` 필드가 없는 옛 시간표는 기존 방식으로 계속 읽는다. 시계가 5초 이상 미래인 쓰기 시각도 오래됨처럼 취급한다.

`engine_signal`은 신뢰 입력으로 승격하지 않았다. 현재 AWS intent는 단계마다 같은 실행 목록이어서 재생에서 `phase_signal_unverified`가 0건이지만, 단계 전용 허용이나 예외 후보가 있으면 이 판정이 나온다. 화면은 `phase_signal_unverified`를 확인 필요로, `suspect_binary`를 탐지로 표시한다. 다른 Pod의 `/bin/sh`도 단계 `unknown`의 `suspect_binary`가 되어 탐지로 보인다. 캐시 실행(`exec_from_cache`)은 엔진 자산의 cache root와 연결된 판정이므로 엔진 selector에 맞는 Pod에만 적용한다. 허용 목록 표시(`allowed_binary`)도 엔진 selector 밖에서는 `null`이다. Pod labels가 없으면 의심 경로만 판정한다.

저장 증거의 엔진 로그 파일 5개를 읽기 전용으로 검사했다. `engine.log`와 `engine-final.log`에서 기존 표지가 각 5건(loading 시작·weights·loading 종료·init 종료·inferring 시작) 일치했고 나머지 3개 파일은 0건이다. 두 파일이 같은 실행의 사본일 수 있어 독립 실행 건수로 합산하지 않는다. 실제 vLLM GPU의 전체 로그 패턴, 특히 GPU 컴파일 표지는 **서버에서 검증 전**이다. 이 일치는 추출 패턴 전체의 GPU 적합성이나 단계 경계의 정확성을 입증하지 않는다.

서버 시험은 **1차 관찰(단계별 실행 기록 수집) → 허용 목록 작성 → 2차 판정** 순서다. 1차는 `tools/aws_lab_acceptance.py inventory --tetragon <읽기 전용 파일> --timeline <시간표>`로 단계·Pod·실행 경로 건수만 뽑는다. 이 명령은 판정을 계산하지 않으며, 시간표 Pod UID 또는 이름과 맞지 않는 실행은 `unknown`으로 둔다. 그 결과와 실제 단계 경계를 검토한 뒤에만 `allowed_engine_binaries`·`phase_binaries` 변경을 결정한다. 현재 목록은 넓히지 않았다. 시간표 연결만 켜면 정상 실행이 다수 `outside_allowlist`가 될 수 있으므로 2차 AWS-11을 1차 자료의 승인 근거로 앞당겨 해석하지 않는다.

AWS-11은 구간 내 유효 단계 실행이 하나도 없으면 경고가 0건이어도 `unknown`이다. 보고서에 유효·미확인 실행 수, `phase_basis`, 시간표 신선도와 쓰기 시각을 기록한다. 의도한 shell 시험 제외와 최근 50행 제한은 유지한다. AWS-09는 계속 엔진 Pod 기록만 근거로 쓴다. AWS-01~08 판정은 바꾸지 않았다.

## 이전 보류 분석

기존 사전 분석은 시간표 출처와 stale 처리가 정해지지 않아 연결을 보류했다. 이번 구현은 호스트 수집·읽기 전용 마운트·15초 만료로 그 두 위험을 처리했다. 파일 없음·손상은 여전히 단계 `unknown`이며, Gateway 관측 API의 최근 50행 제한도 그대로다. 실제 GPU 로그 패턴 확인과 정상 실행 허용 목록은 서버 1차 관찰 후 결정해야 한다.
