# BYUL UPDATE 입력·보고서 경계

이 문서는 `contracts/README.md`의 현행 계약을 사용하는 경로와 미결 범위를 기록한다.
공용 스키마·플랫폼 생산자·runtime 동작은 바꾸지 않는다. 실험 결과가 아니라 구현 계약이다.

## 입력 변환

BYUL 루트에서 기존 플랫폼 환경과 현재 guard 패키지를 함께 사용한다.

```powershell
uv run --project platform/api --with ./update python update/tools/import_byul_inputs.py --environment <byul-environment.json> --policy <byul-policy.json> --pack update/packs/PACK-BASELINE --output <새 외부 폴더>
```

`--as-of <시간대 있는 RFC3339>`는 import 시 정책 유효 기간을 확인하는 시각이다.
생략하면 현재 UTC를 사용한다. 시험 시각이 달라지면 다시 확인해야 한다.

도구는 기존 플랫폼 `Contracts`와 정규 JSON 해시를 사용한다. 출력은 다음과 같다.

| 파일 | 의미 |
|---|---|
| `source-environment.json`, `source-policy.json` | 입력 원본 bytes. 공백·줄바꿈도 보존 |
| `environment.json`, `policy.json` | 새 ID로 봉인한 guard ENV/POL. 기존 파일 수집기로 읽을 수 있음 |
| `provenance.json` | BYUL 문서 Ref·정규 해시 ↔ guard 객체 Ref, 원본·출력 파일 bytes 해시·크기, 한계 |

`provenance.json`은 로컬 import 이력이며 새 공용 교환 계약이 아니다.
BYUL 정책의 `policy_id/policy_version/sha256`과 guard POL의
`kind/id/revision/sha256`을 별개로 보존한다. 출력 폴더가 이미 있으면 덮어쓰지 않는다.
유효하지 않은 스키마·환경 Ref·대상·팩 revision, 누락된 명시 topology,
미래·만료 정책은 출력 전에 거부한다. 현재 팩에 맞춰 정책 revision을 자동 수정하지 않는다.

이 명령은 엔진·네트워크·객체 저장소·승인·runtime 배포를 실행하지 않는다.
정책 값의 완결성·점검별 타입과 지원 프로파일은 이어지는 기존 select/preflight가 확인한다.
승인 필드는 자기 신고이며 인증된 신원으로 승격하지 않는다.

### 입력 계약의 현재 한계

- environment 계약에 adapter·명시 CPU 프로파일이 없다. 추정하지 않고 미확인으로 둔다.
  `gpu_count=0`만으로 CPU 비교에 필요한 모델·아키텍처·커널·스레드 조건을 만들지 않는다.
- `nodes`·`tp_groups`가 없으면 guard 기본값 1을 채우지 않고 `RUNTIME_TOPOLOGY_UNDECLARED`로 거부한다.
- tokenizer revision·배치 정보·runtime 정책은 guard의 다른 필드로 바꾸지 않고 원본에 보존한다.
- BYUL 정책 유효 기간은 import 시 확인한다. guard POL에는 그 기간을 집행하는 필드가 없으므로
  이후 실행 시각의 자동 집행까지 완료했다고 주장하지 않는다. 실행 전에 원본 정책을 재확인한다.
- 원본 BYUL 정책 Ref는 import manifest에 보존된다. 현재 플랫폼 변환기의
  `byul_policy_ref=None`을 이 도구가 자동 변경하거나 연결됐다고 주장하지 않는다.

## RPT JSON → 플랫폼

원본은 guard RPT JSON이다. `platform/api/src/byul_platform/sources/update.py`가
`byul-verification-result/0.2`로 투영하며 UPDATE HTML 보고서·`--html`을 복원하지 않는다.
관측·판정이 없는 기능·성능은 허용된 `null`을 유지하고, 판정·팩 상태·`official`을 재계산하지 않는다.
OBS 점검의 증거 첨부 HTML은 보고서 UI와 다르므로 유지한다.

현재 생성 RPT를 사용한 교차 회귀는 다음 경계에서 수행한다.

```powershell
uv run --project platform/api --with ./update pytest platform/api/tests/test_update_integration_contracts.py
```

일반 계약 예시 검사와 별도로 생성된 RPT·정확한 Ref·null 보존·플랫폼 GET 조회를 확인한다.
시험 자료는 EXAMPLE이며 실제 vLLM 기동·출력 귀속·기업 환경 실증이 아니다.

## U1 외부 표현의 공동 검토 제안

| 내부 RPT 정보 | 현재 0.2에서 가능한 것 | 남은 표현 공백 |
|---|---|---|
| `triggers` | 원본 RPT와 기존 `raw_checks` 상세에 보존 | 구조화된 선정 트리거 필드 없음 |
| `carried_over`, `previous_result` | 정확한 side RES Ref, 미선정 사유·한계·원본 상세 | 이월 EVD·과거/재판정 시각·이전 결과를 구별하는 필드 없음 |
| `change_summary` | 원본 RPT와 CHG Ref·limitations | 경로 분류·도달 단계·공백의 구조화된 투영 없음 |
| `carry_counts` | 원본 보존, 현재 실행의 증거 건수와 분리 | 선정·실행·이월 건수를 별도 표시하는 계약 필드 없음 |

`CARRIED_OVER` 비교 enum이 허용된다고 위 상세까지 전달되는 것은 아니다.
현재 audit 이월은 비교값을 새로 만들지 않으며 미선정 사유와 원본 참조를 유지한다.
공통 계약에 없는 필드를 추가하거나 우회 API를 만들지 않는다.

계약 담당자·UPDATE·플랫폼 담당자가 필요한 필드·null 의미·Ref·버전·하위 호환을 공동 검토해야 한다.
합의 시에만 schema·examples·플랫폼 변환기·API 타입·표시 회귀를 함께 갱신한다.
보고서 생산 위치와 대표 표시 규칙의 소유는 기존 열린 질문을 유지한다.
이 문서는 확장 제안이며 계약 확장이나 UI 구현 완료 기록이 아니다.
