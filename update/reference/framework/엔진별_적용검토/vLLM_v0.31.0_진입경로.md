# vLLM v0.31.0의 선택 진입 경로

확인일: 2026-10-27 · 범위: 공식 고정 소스의 기동 분기와 listener 선언. 실제 설치·기동·권한 효과는 미확인이다.

기준 commit은 `db9527a46873454610df6dbedf79a36d6bf1a7f6`이다. 실제 환경에서는 frontend, protocol, listener, 서비스·작업 목록을 먼저 식별하고 기존 CHK의 적용 범위에 연결한다. 이 문서는 공통 기준의 적용 안내이며 새 엔진 실행기를 제공하지 않는다.

## 고정 소스에서 확인한 분기

| 선택과 위치 | 확인한 동작 | 선정 환경에서 확인할 자료 |
| --- | --- | --- |
| `ServeSubcommand.cmd`, [serve.py:57–65](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/cli/serve.py#L57-L65) | `args.grpc`가 참이면 `serve_grpc(args)`를 실행하고 반환한다. 이후 Rust·HTTP 선택보다 앞선 분기다. [CLI:445–450](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/launchers/cli_args.py#L445-L450)는 `--grpc`와 필요한 `vllm[grpc]`를 선언한다. | 실행 명령·해석한 설정, 설치 wheel·추가 패키지 판본, 선택 frontend |
| 기본 API, [serve.py:145–154](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/cli/serve.py#L145-L154) | 단일 서버는 `run_server`, 다중 서버는 `run_multi_api_server`, multi-port는 `run_dp_supervisor`로 간다. | 실제 process·bind 주소/port·listener 수, HTTP route/method·관리 작업, Gateway 경유·직접 도달 경로 |
| 선택 Rust frontend, [serve.py:62–65](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/cli/serve.py#L62-L65), [serve.py:344–364](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/cli/serve.py#L344-L364) | `VLLM_USE_RUST_FRONTEND`와 경로 설정으로 별도 frontend process manager를 선택한다. | 실행 binary·판본·지원 protocol·등록 API·인증/인가 체인. Python HTTP 목록과의 실제 대응은 미확인 |
| headless, [serve.py:67–75](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/cli/serve.py#L67-L75), [serve.py:181–254](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/cli/serve.py#L181-L254) | API server 수를 0으로 두고 core engine/worker를 기동하는 경로가 있다. 내부 process group·handshake 통신은 별도 조사 대상이다. | API 부재의 기동/노출 근거, 실제 내부 통신 peer·주소·권한·전송 보호 |

## gRPC launcher의 선언과 확인 범위

`serve_grpc`는 외부 `smg_grpc_servicer`의 `VllmEngineServicer`를 생성한다. [grpc_server.py:27–40](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/launchers/grpc_server.py#L27-L40), [84–126](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/launchers/grpc_server.py#L84-L126)에서 다음을 확인했다.

- 외부 descriptor의 `VllmEngine` 서비스, 표준 `grpc.health.v1.Health`, reflection 서비스를 등록한다. 실제 RPC method 목록·내부 인증·객체 인가는 설치한 외부 패키지의 소스/descriptor와 실제 처리 증거가 필요하다.
- `host = args.host or "0.0.0.0"`, `address = f"{host}:{args.port}"`로 주소를 만들고 `server.add_insecure_port(address)`를 호출한다. 이 listener 선언에는 TLS가 적용되지 않는다. Gateway·proxy·network 구간의 보호와 실제 노출은 선정 환경에서 확인한다.
- 단독 module의 `main`은 port 기본값 50051을 선언한다([188–204](https://github.com/vllm-project/vllm/blob/db9527a46873454610df6dbedf79a36d6bf1a7f6/vllm/entrypoints/launchers/grpc_server.py#L188-L204)). `vllm serve --grpc`의 실제 port는 해당 CLI의 해석값으로 기록한다.

실제 추가 패키지 설치, 활성 서비스·method, credential 전달, 인증/인가 효과, network 노출과 모든 transport의 보호는 이번 정적 대조에서 미확인이다.

## 공통 CHK에 연결할 시험 인계

| 기존 CHK | 확인할 범위와 정상 대조 | 필요한 증거·남는 상태 |
| --- | --- | --- |
| CHK-REQ-001 | 실제 각 frontend/protocol의 허용 추론 호출과 자격 누락·잘못된 자격. listener별 Gateway 경유/직접 경로를 선정한다. | frontend·서비스/method·listener·신원/역할·집행점·최종 handler 도달. 지원되지 않거나 도달을 확인하지 못한 범위는 미확인 |
| CHK-REQ-003 | 관리·진단·변경 작업의 허용 역할 정상 처리와 비허용 역할 거부. 다른 protocol로 같은 작업을 제공하면 그 경로도 대응시킨다. | 역할–작업 허용표·실제 method/handler·결과·상태 변경. HTTP의 관리 경로 결과는 그 시험 범위에 귀속 |
| CHK-PLT-008 | 호출자→Gateway→frontend의 각 API 구간과 실제 내부 IPC/TP 구간을 구분한다. 허용 peer/보호 설정에서 정상 호출·정상 내부 작업을 먼저 확인한다. | 구간별 peer·상대 인증·암호화·OS 통제·비허용 peer/설정 거부. API 전송 보호와 내부 통신 보호의 필수 결과를 각각 기록 |

기존 추가 실제시험 목록의 T02(frontend/protocol), T04(전송 구간)에 대응한다. 개발팀은 실제 선택된 환경의 inventory와 지원 상태부터 채우고, 필요한 adapter·계측·무해 시험을 준비한다. HTTP만 시험했으면 HTTP 결과만 기록하며 다른 protocol의 결과는 미확인으로 남긴다. gRPC 비사용은 실제 기동 설정과 listener 조사 근거로 기록한다. 모든 엔진 구현의 완성은 이번 문서 확정의 조건에 추가하지 않는다.

## 소스 식별

아래 SHA256은 GitHub 연결 도구로 재조회한 공식 고정 파일의 UTF-8 bytes와 보관된 동일 commit 사본을 대조한 값이다.

| 파일 | SHA256 |
| --- | --- |
| `vllm/entrypoints/cli/serve.py` | `c182c88347f1eb3440a816986f1c2025a2ab2a13236cd68b0ad0146250d031ff` |
| `vllm/entrypoints/launchers/cli_args.py` | `c26330deb1bffb27b4ddbdc508a02ffd06efd5b201dba7d077d288e3b6ec2a8e` |
| `vllm/entrypoints/launchers/grpc_server.py` | `acc8bf9aa56eecf1ef32b1a7bc13c3fe01f49c991eb368cce8c801c3a592a7d3` |
