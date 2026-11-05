# GPU 담당 43개 1단계 상태 대조 — 2026-11-05

검토 기준선은 `c826fcc5d0d8303c2afc98b4550253a58907f480`이다. 이 문서는 상태·남은 의존성 정리이며 새 GPU 실행 또는 1단계 전체 완료 보고가 아니다. 기존 실행의 고정 소스·Ref·판정과 원자료는 유지했다. 운영 중인 M17과 소스 준비 작업은 변경하지 않았다.

`docs/phase1/gpu.yaml`의 43개 note를 갱신하고 39개의 상태 값을 바꿨다. 기존 `MEASUREMENT_REMAINING` 네 항목은 구체적인 준비 미완료 원인을 덧붙였다. `DONE`은 아래 CPU 범위의 1단계 정리와 나머지 UNKNOWN 보존이며 GPU 실측·전체 질문 완료가 아니다.

| 단계 상태 | 수 | 점검 |
|---|---:|---|
| CPU_DEVELOPMENT_REMAINING | 36 | LIF-01·LIF-02·LIF-03·LIF-04·LIF-05·LIF-06·LIF-07·LIF-08·LIF-09·LIF-10·LIF-11·LIF-12·LIF-14·LIF-16·OBS-02·OBS-03·PLT-01·PLT-02·PLT-03·PLT-04·PLT-05·PLT-06·REQ-05·REQ-08·REQ-09·REQ-10·REQ-12·SUP-03·SUP-06·SUP-07·SUP-08·SUP-09·SUP-10·SUP-11·SUP-12·SUP-15 |
| DONE | 2 | PLT-09·SUP-04 |
| ENTERPRISE_INPUT_WAITING | 1 | PLT-08 |
| MEASUREMENT_REMAINING | 4 | REQ-07·SUP-05·SUP-13·SUP-16 |
| GPU_CONFIRMATION_ONLY | 0 | 없음 |
| NOT_APPLICABLE_IN_TARGET | 0 | 없음 |

- SUP-04: 실제 registry factory CPU 두 사례와 복원·저장 팩 재판정·정리 완료를 재사용했다. EVD/RES EXAMPLE, 빈 목록의 보안 FAIL과 HTTP501 기능 FAIL을 유지했다. package/native/entrypoint/mount·배포 gate는 계측 부재 UNKNOWN이다.
- PLT-09: 격리 CPU의 TLS·평문·평문과 연결 불가 세 사례, Ref/재판정·정리 검증을 재사용했다. EVD/RES EXAMPLE이며 평문 FAIL을 유지했다. 실제 송신 SDK·sink 수신은 생산자 부재 UNKNOWN이다.
- 이 두 항목의 DONE은 CPU에서 확인한 범위와 확인할 수 없는 분기를 닫아 기록했다는 뜻이다. 전체 GPU 검증의 수락 근거로 재사용할 수 없다.
- 나머지 41개는 실제 독립 준비 또는 입력 의존성이 남아 있으므로 완료로 올리지 않았다.

## 증거 재사용과 등급

PLT-01/04 M05의 EVD·verification_level MEASURED, RES data_origin DECLARED, 정책 DRAFT, official=false 및 보안 FAIL을 유지한다. SUP-03/08·REQ-08/09/10의 기존 GPU EVD/verification_level MEASURED와 RES data_origin EXAMPLE도 서로 다른 축으로 보존한다. DRAFT/EXAMPLE 정책 때문에 원 MEASURED 관측을 낮추지 않는다.

REQ-05 M06과 LIF-14 M07은 실제 L4 실행이지만 EVD EXAMPLE·세 축 UNKNOWN이다. LIF-04 M10·LIF-10 M11·REQ-12 M12도 COMPLETED/EXAMPLE·세 축 UNKNOWN이다. LIF-07 M09는 ABORTED/EXAMPLE이며 RES level은 null이다. 원 실행 Ref를 다른 소스/세대의 완료 근거로 합치지 않았다.

이번 변경으로 추가한 실제 GPU 실행 및 GPU MEASURED 결과는 0건이다. 변경 없는 archive CRC·member SHA·복원·재판정은 재실행하지 않았다. 재사용한 로컬 검토는 `PROJECT/guard-output/gpu-stage1-3sessions/reviews/`의 다음 파일이다.

- `M05-M08-existing-v1.md`, `M06M07-existing-v1.md`
- `S2-M09-VERIFY-01-result-v1.md`, `S2-M10-VERIFY-01-result-v1.md`
- `S2-M11-VERIFY-01-result-v1.md`, `S2-M12-VERIFY-01-result-v1.md`
- `SUP04-cpu-v1.md`, `PLT09-cpu-v1.md`, `PLT06-next-v1-dependencies.md`
- 그 밖의 과거 GB 결과는 원 `PROJECT/worktrees/byul-gpu-b-validation-20261102/out/gpu-b/RUN_SUMMARY.json`을 참조한다. 옛 26개 기록을 현재 43개 소유 목록으로 대체하지 않았다.

현재 생성 도구의 CSV `gpu_runs.data_origin`은 A 요약에서 RES의 출처를 가져온다. 이를 EVD 출처로 해석하지 않는다. 생성 도구와 개발 담당 26개 상태 파일은 수정하지 않았다. 정확한 EVD/RES와 등급별 발췌는 동반 `docs/evidence/gpu-b-phase1-status-20261105.json`에 기록한다.

## #196 생산자 의존 14개

`collectors/gpu_common_inputs.py`의 `SUPPORTED_PRODUCERS={}`를 확인했다. 실행기/소비자에 이 모듈을 연결한 호출은 기준선에서 찾지 못했다. 아래 상태는 계약만 준비된 상태이며 GPU 패킷 ready를 뜻하지 않는다. 해당 분기는 `#196 생산자 대기`로 남긴다. validate 결과의 gaps/complete/supported/violation_candidates를 실제 판정에 잇는 일은 GPU 소유 파일의 후속 작업이다. 생산자 생성·등록은 개발 담당 범위다.

| 점검 | 종류 | 남은 직접 관측 |
|---|---|---|
| LIF-03 | request-attempt | 요청·단계별 예산, KV/GPU 비용·한도 집행 |
| LIF-05 | generation-transition | 진행 요청과 worker·weights·cache 세대 연결 |
| LIF-06 | generation-transition | 부분 교체·혼합 소비와 동일 faulty 세대 외부 접수 |
| LIF-07 | generation-transition | rank·모델·backend 준비와 실제 service gate |
| LIF-08 | rank-fault | 주입/해제 ack·선택 rank·탐지·복구 |
| LIF-09 | generation-transition | 부분 초기화 실패와 해당 세대 접수·차단 |
| LIF-10 | request-attempt | cancel/close/timeout의 Core·worker·참조·GPU 회수 |
| LIF-11 | failed-group-resources | 실패 그룹별 PID/IPC/SHM/NCCL/device 전후·반환 |
| LIF-12 | request-attempt | 새 attempt 자동 retry와 비용·KV·GPU 중복 소비 |
| LIF-14 | profiler-control | backend stop/실패·worker RPC·출력 완료·다음 수집 |
| OBS-02 | health-worker-causal | API health와 같은 worker 세대 상태·접수 인과 |
| OBS-03 | profiler-control | profiler 제어·trace/debug 필드와 실제 worker 수집 인과 |
| PLT-01 | process-device-provider | GPU worker·NVML·namespace·실제 장치 접근 |
| PLT-04 | process-device-provider | 동일 worker의 자격 접근·provider 전달·갱신 |

생산자가 없는 현재 자료만으로 계약 필드와 실제 GPU 생산자 출력의 불일치는 확정할 수 없다. 확정된 필드 변경 요청은 0건이다. 실제 생산자가 도착하면 ENV Ref·target/prepared·session·worker 세대를 대조하고, 표현할 수 없는 관측이 확인되는 경우 필드·원본 위치·이유를 개발 담당/사용자에게 요청한다. 계약·지원 목록·정책을 임의로 수정하지 않는다.

LIF-04의 실제 M10 transport_kind=unsupported는 위 7종과 별개의 ASGI/socket 관측 의존이다. REQ-12의 client_receipt_verified=false도 기존 native11 output link와 실제 클라이언트 수신의 차이이며 LIF-12 자동 retry로 재분류하지 않는다. SUP 계열의 boot-record·실제 GPU loader/cache/plugin 생산자 공백도 #196의 14개로 잘못 합치지 않았다.

## 다음 순서와 실행 전 필요한 자료

1. 새 M17 원 archive `GB1017-original-evidence.zip`(source4e, v5.3/operator-v2-probe-uid, SHA `42a89cc6175113838a30dd408e6b32f6f617a09842104e22635f01255c079ff6`, 553814bytes)의 231 CRC와 230 member bytes/SHA를 확인했다. `controller/product/001-materialize-live-profiler-inputs.stderr`는 `M17-materialize-inputs.py:10`의 Git HEAD 조회가 dubious ownership/128로 실패했음을 보존한다. 상위 receipt는 exit1·child/PG terminal이며, archive에 trial/store·pipeline-result가 없다. 제품 판정은 부여하지 않았다. 이는 상태 갱신에 필요한 무결성·실패 위치 확인이며 전체 신원/세대·종결 검증은 아니다. 최종 control·stopped receipt는 별도 검증이 필요하고 현재 AWS 상태는 주장하지 않는다. 준비 담당이 실행 UID와 저장소 소유 조건을 일치시키는 고정 수정·CPU 계약을 제공해야 한다. 글로벌 safe.directory·권한을 이 대화에서 바꾸지 않았다.
2. 해당 운영 작업을 변경하지 않고 다음 단일 GPU 패킷을 준비한다. 새 관측·고정 커밋·이미지/센서·target/prepared/ENV·파생 DRAFT 팩·정상 대조·종료/복구가 필요하다. 기존 정상 응답의 단순 반복이나 생산자 없는 fixture는 새 관측으로 세지 않는다.
3. SUP-05/16은 실제로 서로 다른 정상/변경 또는 부분 파일 두 세대의 고정 패킷이 필요하다. SUP-13은 기존 pinned source/index preflight 거부 원인을 해결한 CPU 고정 입력이 먼저다. REQ-07은 현재 CPU 고정 프로파일의 지원 범위 안에서 준비하고 GPU 호환성을 임의로 주장하지 않는다.
4. TP2(LIF-08·11, SUP-06 및 다중 rank 관련 부분)는 사용자 새 비용 승인 뒤에만 실행한다. 현재 대화에서 TP2 승인·새 인스턴스·개발 담당 namespace 운영을 수행하지 않았다.

## 시간·비용 추정의 범위

기존 비용 장부의 2026-11-05T04:59:44.027188Z 기록을 읽기 전용으로 참조했다. 당시 단일 GPU compute $0.9896/h + IPv4 $0.005/h + 보존 EBS 전체 $0.055748/h = $1.050348/h였다. 이 시점 누적 추정은 $43.2621734773, $5 여유 제외 잔여 추정은 $51.7378265227이었다. 이후 M17 실행비·EBS 비용은 이 숫자에 아직 반영되지 않으므로 현재 잔액으로 사용하지 않는다.

| 조건부 계획 | 시간 가정 | 기존 장부 단가로 계산한 직접 비용 |
|---|---|---:|
| 다음 고정 단일 GPU 묶음 1회 | 준비가 끝난 뒤 1~2시간 계획 가정 | 약 $1.05~2.10 |
| 승인 뒤 TP2 묶음 1회 | 두 GPU compute $1.9792/h, IPv4 $0.01/h, 전체 보존 EBS $0.055748/h; 1~2시간 계획 가정 | 약 $2.04~4.09 |

이 계산은 새 견적·실청구·실행 약속이 아니다. 네트워크 등 추가비용·기존 원가 보호 조건을 대체하지 않는다. 기존 다음 묶음 최악 비용 $5.62 가정과 정리 reserve/원 기한을 축소하지 않았다. 다음 실행 때는 운영 담당의 새 비용·현재 자원 receipt가 필요하다.

43개 전체 완료 시간·총 GPU 비용은 생산자 구현/등록, 실제 패킷 수와 도전/복구 기동 횟수가 정해지지 않아 산정할 수 없다. 생산자 대기 시간을 GPU 시간으로 추정하거나 GPU를 켠 채 대기하도록 계획하지 않았다. 로컬 Docker Desktop Linux engine named pipe도 없어 기존 CPU 실행 도구를 바로 재실행할 수 없다. Docker/서비스 설정은 변경하지 않았다. 이번 상태·문서 변경 자체의 새 AWS/GPU 실행 비용은 0이다.

## 검증

`uv run python -m tools.checklist_status`와 `uv run python -m tools.checklist_status --check`를 수행했다. 상태 집합·43개 소유 ID·개발 26개 bytes 불변·생성 CSV의 GPU 담당 상태/note만 변경됨을 확인했다. 정본 PACK32/manifest/제품 소스 변경은 없다. 문서 변경이므로 전체 suite·wheel·기존 GPU 시험을 반복하지 않았다. Linux CI 전체 통과를 새로 주장하지 않는다.
