# 엔진 수신 기록과 GPU 지표 (15개 목록 11·12번)

> Claude가 구현함(Codex 사용 한도로 중단돼 이어받음). **Codex 교차 검수 전**이며 실서버 확인 전이다.

## 1. 엔진 수신 기록(11번)

| 근거 | 켜는 법 | 기록 위치 | 의미 |
| --- | --- | --- | --- |
| 응답 ID 대조 | 항상 켜짐(외부 backend) | 요청 기록 `delivery.backend_receipt` | 엔진 응답 `id`가 `X-Request-ID`, `chatcmpl-<ID>` 또는 `cmpl-<ID>`와 같으면 `engine_response_id_matched`, 다르면 `..._mismatch`, 없으면 `..._absent` |
| 엔진 로그 대조 | 선택. 수집기 서비스를 켜고 Gateway에 `--engine-receipts` 인자 추가 | 요청 상세 `engine_receipts`·`engine_live`·`evidence_state` | 로그에서 요청 ID를 찾으면 `received`. 찾지 못하면 "로그에서 못 봄"으로만 쓰고 **미수신으로 단정하지 않는다** |

- 응답 ID 일치는 엔진이 요청 ID를 받아 썼다는 약한 근거다. 엔진 측 기록이 아니므로 `evidence_state`는 `gateway_only`로 둔다.
- 로그 수집기(`tools/engine_receipts.py`)는 로그 줄을 메모리에서만 훑어 32자리 요청 ID와 시각만 남기고 줄은 버린다.
  - 저장하지 않는 것: 프롬프트·응답·로그 원문·Pod/노드 이름.
  - 수집이 실패하면 구간을 새로 시작한다. 그래서 공백 구간의 "미발견"은 주장하지 않는다.
- `not_observed`는 세 조건을 모두 만족할 때만 쓴다.
  - 수집이 정상이고 최신이다(60초 이내).
  - 연속 수집 구간이 요청 시각을 덮는다.
  - 요청 뒤 5초 이상 지나 수집했다.
  - 이 중 하나라도 아니면 `unavailable`이다.
- **아직 확인하지 않은 것**
  - 실제 vLLM 판이 `X-Request-Id`를 응답 `id`와 로그에 쓰는지 확인하지 않았다(설정에 따라 요청 로그가 꺼져 있을 수 있다). 서버에서 응답 `id`와 로그 형식을 직접 확인한 뒤에 켠다.
  - SGLang이 전달된 ID를 쓰는지도 확인하지 않았다.

### 켜는 순서 (서버)

1. 요청 1건을 보내고, 응답 `id`와 엔진 로그에 Gateway 요청 ID가 그대로 나오는지 확인한다. 이때 로그 원문은 화면에서만 확인하고 저장하지 않는다.
2. `deploy/agent/serving-runtime-engine-receipts.service.example`에서 context·namespace·엔진 라벨을 바꿔 설치한다. 설치기가 `/srv/serving/observation/engine-receipts`(runtime-agent, 관측 GID, 2750)를 준비한다.
3. Gateway 인자에 `--engine-receipts /srv/serving/observation/engine-receipts/engine-receipts.json`을 추가하고 재배포한다.
4. 요청 상세에서 다음을 확인한다.
   - "엔진 로그에서 요청 ID 확인"으로 표시되는지
   - 수집기를 멈추면 "확인 불가"로 바뀌는지

## 2. GPU 지표(12번)

| 단계 | 내용 |
| --- | --- |
| 수집 | `serving-runtime-gpu-status.service`가 10초마다 `nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw`를 실행한다. 결과는 `/srv/serving/observation/gpu-status/gpu-state.json`(0640)에 원자적으로 쓴다 |
| 남기는 값 | index, 제품명, 사용률, 메모리 사용/전체(MiB), 온도, 전력. UUID·serial·bus ID·프로세스 목록·원문은 남기지 않는다(서버 식별 정보 최소화) |
| Gateway | `--gpu-state` → `/api/gpu-status`. 상태는 `ok`/`stale`(45초 초과·수집 중단)/`error`(이유 코드)/`missing`/`not_configured` |
| 플랫폼 | `/api/runtime/gpu-status`, 현황 화면의 GPU 카드와 "GPU 관측" 표시. 예시 모드에서는 수치를 만들지 않는다 |

공유 계약(`contracts/`)은 바꾸지 않았다. GPU 응답은 플랫폼 전용 응답이다.

### 서버 확인

1. 설치기를 다시 실행한다.
2. `systemctl status serving-runtime-gpu-status`로 서비스를 확인한다. 공개 파일은 `stat`으로 0640·관측 GID를 확인한다.
3. Gateway를 재배포한 뒤 `/api/gpu-status`가 `ok`이고 값이 `nvidia-smi`와 맞는지 비교한다.
4. 서비스를 멈춘 뒤 45초가 지나면 `stale`이 되고, 화면에 "현재 값 아님" 경고가 뜨는지 확인한다.
