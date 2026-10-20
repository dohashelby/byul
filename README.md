# serving-runtime
BoB serving-engine security runtime solution

- 개발계획서 v1.4(문서 상태: 개발 기준 설계안)가 전달됐으며, 직접 개발할 모듈의 언어는 Python으로 선택했습니다. 연동 규격·실제 환경 등은 미결입니다.
- Codex는 구현·수정·시험을, Claude는 검수를 담당합니다.

## 런타임 통제 MVP (로컬)

로컬 PC에서 정책 파일을 읽고, 추론 요청을 허용하거나 거부하고, 그 결과와 증거를 브라우저 대시보드에서 조회하는 최소 기능입니다. 실제 추론 엔진 대신 **모의 엔진**을 사용합니다.

```text
시험 클라이언트 → Gateway :8080 → 모의 엔진 :8088
                     ↓                  ↓
               요청 결과 기록       수신 증거 기록
                     └──── 조회 API ────┘
                              ↓
                     브라우저 대시보드 (읽기 전용)
```

이 MVP는 요청 통제·기록·조회 경로만 다룹니다. 실제 GPU 추론, 커널 통제, 네트워크 격리, 인증·TLS는 구현하지 않았으며 이를 입증하지 않습니다. 모의 엔진 포트(8088)로 직접 보내는 요청은 막지 않습니다.

### 준비

WSL Ubuntu의 Python 3.12 기준입니다. 저장소 루트에서 한 번만 실행합니다. 외부 실행 라이브러리 의존성은 없습니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

### 실행

저장소 루트에서 실행합니다. 정책 파일 기본 경로(`examples/mvp/policy.json`)가 현재 폴더 기준이기 때문입니다.

```bash
.venv/bin/python -m serving_runtime mvp
```

`Browser: http://127.0.0.1:8080/`이 출력되면 터미널을 열어 둔 채 Windows 브라우저에서 그 주소를 엽니다. 종료는 `Ctrl+C`입니다. 서버는 `127.0.0.1`에만 열립니다.

| 옵션 | 기본값 | 설명 |
| --- | --- | --- |
| `--policy PATH` | `examples/mvp/policy.json` | 정책 파일. 변경은 재실행으로 적용 |
| `--gateway-port N` | `8080` | Gateway·대시보드 포트 |
| `--engine-port N` | `8088` | 모의 엔진 포트 |
| `--data-dir PATH` | `~/.local/state/serving-runtime/mvp` | 기록 저장 위치 |
| `--relay-timeout 초` | `5` | 모의 엔진 응답 대기 상한 |

### 요청 보내기

다른 터미널에서 보냅니다. 응답 헤더 `X-Request-ID`가 서버가 발급한 요청 ID입니다.

```bash
# 허용: 200
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":64}' \
  http://127.0.0.1:8080/v1/chat/completions

# 출력 한도 초과: 403 정책 거부, 모의 엔진에 전달하지 않음
curl -i -H 'Content-Type: application/json' \
  -d '{"model":"test-model","messages":[{"role":"user","content":"hi"}],"max_tokens":129}' \
  http://127.0.0.1:8080/v1/chat/completions
```

지원하는 요청은 `POST /v1/chat/completions` 하나입니다. `model`(허용 목록), `messages`(role은 system/user/assistant, content는 문자열), `max_tokens`(생략 시 128), `stream`(false만), `n`(1만)을 받습니다.

| 상황 | HTTP | 결과 |
| --- | --- | --- |
| 허용, 모의 엔진 응답 정상 | 200 | 허용 |
| 미허용 모델, `max_tokens` 128 초과 | 403 | 정책 거부 |
| JSON·타입 오류, 미지원 필드·`stream:true` | 400 | 입력 오류 |
| 본문 64KiB 초과 / Content-Type 오류 | 413 / 415 | 입력 오류 |
| 정책 파일 없음·검증 실패 | 503 | 처리 오류 (정책 준비 실패) |
| 기록 저장 불가 | 503 | 처리 오류 (모의 엔진 미호출) |
| 모의 엔진 연결 실패 / 시간 초과 | 502 / 504 | 처리 오류 |

### 대시보드

- **현황**: 결과별 건수, 정책 판본·SHA-256·로딩 시각, 모의 엔진 연결 점검, 기록 저장 상태·손상 줄 수. 네트워크·프로세스·파일·GPU 관측은 "미연결"로 표시합니다.
- **요청 목록**: 결과별 필터. 행을 누르면 상세가 열립니다.
- **상세**: 판정·적용 규칙, 전달 여부, Gateway 기록(시작→최종), 모의 엔진 수신 증거, 증거 JSON 내려받기.
- 3초마다 갱신하며, 서버와 연결이 끊기면 경고 배너를 띄우고 이전 값을 흐리게 표시합니다.

거부 요청의 "미수신 확인"은 Gateway가 중계하지 않았다는 기록과, 같은 실행 중인 모의 엔진의 수신 기록 조회를 함께 대조한 결과입니다. 엔진 조회 실패나 이전 실행의 요청은 "확인 불가"로 표시합니다.

### 기록

`~/.local/state/serving-runtime/mvp/` 아래에 저장합니다. 저장소 밖이므로 커밋되지 않습니다.

- `gateway_records.jsonl`: 요청별 시작·최종 판정 기록
- `engine_receipts.jsonl`: 모의 엔진 수신 기록

프롬프트·응답 원문은 저장하지 않습니다. 실행할 때 이전 기록을 지우지 않으며, 초기화하려면 서버를 끈 뒤 위 폴더를 직접 삭제합니다.

### 시험

```bash
.venv/bin/python -B -m unittest discover -s tests -v
```

HTTP 시험은 로컬 소켓이 필요합니다. 소켓을 막은 샌드박스에서는 해당 시험을 건너뜁니다.

### 문제 해결

- **브라우저에서 연결 거부**: 서버가 실행 중인지 확인합니다. 실행한 터미널을 닫으면 서버도 꺼집니다.
- **포트 충돌 (`Address already in use`)**: 이미 실행 중인 서버를 끄거나 `--gateway-port`·`--engine-port`로 다른 포트를 지정합니다.
- **모든 요청이 503**: 대시보드 정책 카드의 오류 사유를 확인합니다. 저장소 루트가 아닌 곳에서 실행하면 정책 파일을 찾지 못합니다.

### 진행 상태

| 단계 | 내용 | 상태 |
| --- | --- | --- |
| M0 | 실행 골격·포트·브라우저 접속 | 완료 |
| M1 | 정책 로딩·검증, 요청 검사, 모의 엔진 | 완료 |
| M2 | Gateway 중계·요청 ID·양쪽 기록·오류 처리 | 완료 |
| M3 | 조회 API·대시보드 | 완료 (Codex 교차 검수 대기) |
| M4 | 재실행·손상 처리 보완, 시험 실행 명령, 수용 시험 전체 실행 | 미완료 |

스트리밍, 실제 vLLM 연결, 인증, Cilium·Tetragon 통합은 MVP 이후 범위입니다.

## 협업·검수

사용자 요청을 Claude가 정리하고 Codex가 구현·시험합니다. Codex는 작업을 마치면 변경 범위·새 파일·시험 명령과 결과·남은 제약·Git 기준점·커밋 여부를 인계하고, Claude가 대화형 세션에서 직접 검수합니다. 필요한 수정·재시험은 Codex가 맡고 Claude가 다시 검수해 최종 보고합니다.

자동 검수 스크립트는 기본 작업 흐름에서 사용하지 않습니다.
