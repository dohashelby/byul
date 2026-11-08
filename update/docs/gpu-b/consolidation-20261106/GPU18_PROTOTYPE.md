# 잔여 18개 항목의 모의 연동 1차 버전

사용자가 2026-11-08 승인한 임시 데이터·모의 연동 범위다. GPU, AWS, Docker, 모델 다운로드 없이 CPU 메모리 상태 모형을 실행한다. 모델·worker·IPC·TP2·공급물·과거 기록의 실제 연동은 아래 반환물을 받은 뒤 별도로 검증한다.

## 실행

Python 3.12와 프로젝트의 기존 의존성을 사용한다. 저장소의 `update` 디렉토리에서 실행한다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
python -m guard.gpu18_prototype --output out/gpu18-prototype-first
```

출력은 새 디렉토리 또는 빈 디렉토리여야 한다. 기존 결과를 덮어쓰지 않는다. 실행에는 클라우드 인증정보·모델·네트워크 접근이 필요 없다.

18개 항목 각각에서 정상·통제 위반·핵심 관측 누락을 실행한다. 관측은 모형의 producer→consumer 상태에서 계산하며, 판정은 기존 `rule_table` 및 PASS 관문이 수행한다. 성공 판정을 반환값에 직접 채우지 않는다.

기존 `select → run → judge → report`, append-only Store, 저장 팩 고정, bundle 반출·복원, 원래 `as_of`와 정확 Ref를 사용한 재판정·보고서 동등성 검사를 거친다. 전용 실행기는 실행 구간에만 등록하고 종료·예외 시 제거한다. 공통 core·models·store·registry 소스·기존 팩은 바꾸지 않는다.

## 모의 계약과 원래 판정의 구분

- 모든 ENV/POL/PLAN/EVD/RES/RPT는 `EXAMPLE`, 정책과 모의 팩은 `DRAFT`, 보고서는 `official=false`다. 성능·운영 승인·실제 GPU 검증을 의미하지 않는다.
- 원래 PACK-BASELINE33의 SUP-03·PLT-02에는 정상 PASS 규칙이 부족하고, REQ-10에는 위반 FAIL 규칙이 없다. 원래 규칙의 결과는 별도 `source-pack-example-*.json`에 보존한다.
- 세 항목의 모의 계약에만 전용 판정표를 적용한 `PACK-GPU18-PROTOTYPE`을 별도로 생성한다. `derivation.json`에 원래 팩 Ref와 규칙 차이를 해시 고정해 저장한다. 이는 제품 판정표 보완이나 정책 승인으로 취급하지 않는다.
- 과거 원자료 5개는 새 합성 사례로 시연한다. 과거 원본을 확보·복구했다고 표시하지 않는다.
- 기존 실증의 FAIL/UNKNOWN과 **25개 한정 마감 제안 / 18개 제품 증거 추가 필요** 분류는 유지한다. 모의 18개 완성 수를 실제 통과 수에 합산하지 않는다.

## 산출물과 인계

통합 실행 결과는 정상 **PASS 18**, 위반 **FAIL 18**, 누락 **UNKNOWN 18**이다. 같은 합성 관측에 원팩33을 적용하면 정상은 PASS16/UNKNOWN2, 위반은 FAIL16/UNKNOWN2, 누락은 UNKNOWN18이다. 두 판정을 혼합하지 않는다.

독립 검토 **14 passed**, 기존 CLI·bundle·INV-02·INV-11 회귀 **38 passed**를 확인했다. 독립 검토는 프로세스·네트워크 호출 차단(시도0), 성공/저장 실패 시 registry 복원, 기존 출력 보존, 실제 객체/팩/Ref, 변조3종 거부, 복원 재판정과 CLI를 포함한다. [기계 판독 검증 기록](gpu18-prototype-validation-20261108.json)에 실행별 Ref·번들 SHA·실제 연동 요구를 고정했다.

| 경로 | 내용 |
| --- | --- |
| `summary.json` | 18개 범위·시나리오별 판정·정확 Ref·원팩 대조·실제 연동 필요 사항 |
| `reports/prototype-*.json` | 모의 계약 DRAFT 팩의 EVD/RES 기반 보고서 3개 |
| `reports/source-pack-example-*.json` | 원래 PACK-BASELINE 판정표로 같은 합성 사례를 판정한 보고서 3개 |
| `prototype-pack/` | 원래 팩과 구별되는 모의 계약 팩과 규칙 차이 |
| `store/` | 합성 이벤트·내용 주소 artifact·append-only 객체 저장소 |
| `evidence.zip`, `restored/` | 실제 반출·복원 및 저장 팩 재판정 대상 |

실제 연동은 [담당별 입력 반환표](DEPENDENCY_RETURNS.md)의 18ID/22분기와 함께 확인한다. 모의 관측을 실제 시스템 센서에 연결할 때에도 EXAMPLE 기록을 덮어쓰지 않고 새 실행·정확 Ref로 분리한다.

세션1은 공통 준비 10개, 세션5는 CPU 2개, 세션4는 과거 원자료 대상 5개의 새 합성 사례, 세션2는 SUP-16의 로컬 모형, 세션6은 독립 검토를 맡는다. 루트는 공통 실행·저장·반출·복원·문서·PR을 통합한다.

| 항목 | 1차 모의 구현 | 실제 연동에서 더 필요한 것 |
| --- | --- | --- |
| REQ-08 | 재사용 토큰 필드 검사·제거·최종 소비 대조 | 실제 endpoint/InputProcessor/worker 소비 기록과 허용 정책 |
| REQ-09 | token-array 타입·범위·길이 검사와 소비 | 실제 모델/vocab·지원 입력 계약·거부/소비 및 정상 요청 관측 |
| REQ-10 | sampling 옵션 거부와 최종 소비 대조 | 실제 runner/worker/Sampler 관측과 정책·제품 판정 계약 |
| SUP-03 | 승인 템플릿 해시와 선택·소비 대조 | 승인 공급물 전체 inventory·독립 worker 파일 소비·request 연결 |
| SUP-05 | 승인 후 메모리 binding 교체와 소비 대조 | 실제 loader/FD causal-root·고정 승인 목록·worker/decoder 소비 |
| SUP-06 | rank별 tensor 배정·누락과 준비 상태 | 실제 TP2 환경·모델 shard/tensor 배정·rank별 적재/소비 |
| SUP-08 | 요청의 code 선택 토큰·거부·소비 | 과거 원자료 및 실제 model/code provenance·module 소비 |
| SUP-09 | 승인/비승인 코드 식별값의 허용 검사 | 실제 loader trust 설정·공급물 코드 bytes·소비 producer |
| SUP-10 | 선택/비선택 plugin 식별값의 허용 검사 | 실제 entry point·autoload·runner/후처리 연결 기록 |
| SUP-11 | 승인 탐색 경로와 비승인 경로 대조 | 실제 지원 Python 모드·환경·loader/module 소비 기록 |
| SUP-16 | 생성 중단·조각 회수·SHA·완료본 소비 gate | 실제 native build/toolchain·회수 receipt·worker FILE_READ |
| PLT-02 | 허용/비허용 주체의 IPC 명령 소비 | 실제 IPC/namespace/owner/peer 신원·OS 접근·TP 요청 관측 |
| LIF-06 | 변경 전·중·후 요청의 세대 일관성 | 실제 live/LoRA/rolling 기능·weights/cache/replica 관측 |
| LIF-08 | rank 장애별 감지·통지의 논리 시계 | 과거 원자료 및 실제 rank별 신원·장애·기한 관측 |
| LIF-11 | worker 실패 후 자원 회수·재기동 | 실제 TP rank·PID/socket/SHM 소유·기한 내 회수/정상 재기동 |
| LIF-12 | 재시도·중복 작업·누적 비용 한도 | 과거 원자료 및 실제 retry 주체·attempt·잔존 작업·비용 기록 |
| LIF-16 | 승인 snapshot 복원·검증·세대 선택 | 과거 원자료 및 실제 백업/volume/자격참조·복구/접수 gate |
| OBS-02 | API/로그 상태와 독립 worker 상태 대조 | 과거 원자료 및 같은 실행 세대의 실제 worker/backend 관측 |

실제 연동에 필요한 입력이 도착하면 해당 모형의 consumer 경계를 실제 센서에 연결하고 새 실행으로 검증한다. 원팩의 규칙 공백·기업 승인·GPU/TP2 결과와 과거 원자료 미확인은 이 1차 모형으로 해소되지 않는다.
