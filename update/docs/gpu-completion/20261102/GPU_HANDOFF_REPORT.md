# 기존 실행기와 체크리스트 GPU 실증 인계 보고서

기준 시각: **2026-11-02 19:01 KST**  
대상 저장소: https://github.com/dohashelby/byul  
대상 업무: 기존 GPU 후보 40개에 대한 실제 실행 검증과 오류 수정 현황 인계

## 1 현재 진행량과 해석

**GPU 후보 40개 중 8개에서 제한된 분기의 실제 관측을 확보했다. REQ-14와 REQ-17은 실제 요청을 실행했지만 수집·대조 조건이 충족되지 않아 부분 실측 집계에 넣지 않았다. 나머지 30개에는 이번 업무에서 수행한 GPU 점검 실행 증거가 없다. 원 질문의 모든 적용 분기를 완료한 점검은 아직 0개다.**

| 구분 | 점검 수 | 내용 |
| --- | --- | --- |
| GPU 후보 전체 | 40 | 기존 확정 목록 유지 |
| 부분 실측으로 집계 | 8 | REQ-09, REQ-10, SUP-03, PLT-01, PLT-04, OBS-03, REQ-16, REQ-18 |
| 실제 실행했으나 실측 집계 미포함 | 2 | REQ-14와 REQ-17의 수집·대조 미완료 |
| 이번 업무의 GPU 점검 실행 증거 없음 | 30 | 40개 전체 현황표 참조 |
| 부분 실측 집계에 포함되지 않은 합계 | 32 | 위 2개와 30개를 합한 수. 전부 미시도라는 뜻은 아님 |
| 원 질문의 모든 적용 분기 완료 | 0 | 부분 실측 8개에도 남은 분기가 있음 |
| 별도 보조 시험 | REQ-11 | JSON·SSE 8사례. 40개 분모에 포함하지 않음 |
| 제품 실행 증거 묶음 | 29 | 최초 3 + PACK31 후속 17 + PACK32 후속 9. 중단·오류·수정·복구 재시험 포함 |

여기서 부분 실측은 실제 GPU 엔진이 있는 환경에서 해당 점검의 일부 사실을 직접 관측했다는 의미다. 모든 행에 GPU kernel·tensor 최종 소비 증거가 있다는 의미는 아니다. API·프로세스·컨테이너 수준의 관측 범위와 GPU 내부 관측 범위를 아래에서 구분한다. CPU 계약, EXAMPLE, 대역, factory 생성, 일반 CUDA 환경 진단은 점검 실측 수에 넣지 않았다.

모든 기업 정책 판정을 완료한 상태도 아니다. 시험 정책은 기술 시험용 DRAFT/DECLARED이고 `confirmed_by=null`이며, 공식 승인자나 승인 기록을 만들지 않았다. 원 RES의 `PASS`, `FAIL`, `UNKNOWN`은 해당 시험 정책·프로파일의 결과다. 이를 시스템 전체의 보안 결론이나 운영 수락으로 확대하지 않는다.

## 2 사용한 실제 환경과 소스 기준

| 항목 | 확인 내용 |
| --- | --- |
| AWS 구성 | 서울 ap-northeast-2 / EC2 g6.2xlarge 1대 / NVIDIA L4 1장 / 단일 노드 |
| GPU 및 엔진 | 드라이버 595.112.07 / vLLM 0.31.0 / Torch 2.13.0+cu130 / CUDA runtime 13.0 |
| 실행 환경 | Ubuntu 24.04.5 / 제품·컨테이너 Python 3.12.3 / K3s 및 별도 Docker real 세션 |
| 요청 모델 | Qwen/Qwen2.5-0.5B-Instruct / 검증된 API 별칭 byul-gpu-lab |
| 첫 묶음과 PACK31 후속 | V2 Model Runner / TP1 / eager / CUDA Graph 비활성 / max_model_len 128 / max_num_seqs 2 / GPU util 0.35 / half |
| PACK32 callback 묶음 | VLLM_USE_V2_MODEL_RUNNER=0으로 V1 명시 / MP backend / TP1 / eager / prefix caching 활성 / max_model_len 1024 / max_num_seqs 4 / GPU util 0.4 / half |
| 구성 분리 | 서로 다른 runner·Pod·센서 세대의 결과를 별도 ENV/target/prepared/Ref로 보존 |
| 접근 경계 | 추론 API와 Kubernetes API 외부 비공개. 전용 SSH 키와 출발 IP /32의 단일 SSH 규칙 사용. IAM 역할 추가 없음 |

고정 공급물 식별자:

- 이미지: `vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b`
- 모델 revision: `7ae557604adf67be50417f59c2c2f167def9a775`
- 모델 마운트 inventory: `b52d654ef0bf6bf214e055f791b4e492206b9bd1380cb93d75bc4a092638245e`
- 검증한 추론용 마운트 파일: 7개, 999,586,347 bytes. upstream 저장소의 모든 파일을 받은 것은 아님.
- 고정 chat template SHA256: `e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e`

| 단계 | 실측·검증 기준 | 보존 원칙 |
| --- | --- | --- |
| PACK31 | main c33f29a → 제품 보수 8c7770a / 테스트 fixture e28cd8a / 보고 a1f2e0a | 당시 pack과 source 기준으로 결과 보존 |
| PACK32 준비·초기 callback | 병합 main 1174da6 + 기존 GPU 보수 이식 + Windows 보수 0b6b644 | G27·G28 원 실패와 정상 결과 보존 |
| PACK32 callback 보수 재시험 | 제품 b54148d / 보고·중지 확인 9ff4858 | G29 새 센서·Pod·ENV·정책 세대에서 재시험 |

- PACK31 Ref: `PACK-BASELINE@31` / SHA256 `54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac`
- PACK32 Ref: `PACK-BASELINE@32` / SHA256 `1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd`
- 현재 로컬 브랜치: `codex/gpu-completion-20261102`. 본 작업에서 push·PR 생성·병합·새 source 승인을 수행하지 않았다.
- PACK31의 미연결 사유를 최신 PACK32의 현재 구현 상태로 재사용하지 않는다. 연결이 추가된 사실과 GPU 실측은 별개다.

## 3 부분 실측 8개의 수행 내용과 남은 분기

아래 축 표기는 `S/F/P = security/functional/performance` 순서의 원 RES 값이다. 서로 다른 팩·backend·세대의 값을 합성한 판정이 아니다.

| 점검 | 실제 실행기와 backend | 수행 시험과 관측 | 원 RES 축 값 | 남은 범위 |
| --- | --- | --- | --- | --- |
| REQ-09 | input_validation real 0.7.1 | 범위 밖 token ID·길이 도전의 HTTP 400, 전후 정상 추론 대조 | UNKNOWN/PASS/UNKNOWN | 거부 지점과 후속 runner 소비 연결, embedding·mask·offset 등 적용 입력 |
| REQ-10 | sampling_validation kubernetes 0.2.2 | temperature=-1, top_p=0, min_tokens=3 거부와 정상·병행·후속 대조 | UNKNOWN/PASS/UNKNOWN | 요청별 최종 GPU sampler 설정·소비, count·schema·tool 등 적용 옵션 |
| SUP-03 | template_validation real 0.4.1 및 Kubernetes | template 신원·입력 투영·정상 대조 | UNKNOWN/UNKNOWN/UNKNOWN | worker 최종 소비, 공급물 전체 승인 범위, 비승인 공급물 거부 |
| PLT-01 | container_config real 0.6.3 및 Kubernetes | root·쓰기 등 DRAFT 기준 위반 관측과 독립 FAIL 보존 | FAIL/UNKNOWN/UNKNOWN（real 수정 후） | 유효 GPU 장치 접근 권한과 host 집행 범위 |
| PLT-04 | credential_exposure real 0.5.2 / kubernetes 0.1.1 | 정상 추론, 공개 시험 표식 주입 세대, 제거·복구 세대 | 표식 FAIL/PASS/UNKNOWN；복구 PASS/PASS/UNKNOWN | 실제 GPU worker 현재 자격·provider 사용, 원격 rank 범위 |
| OBS-03 | log_analysis real 0.3.1 | 정상 → 무해 오류 → 정상, 관련 엔진 로그 수집 | UNKNOWN/PASS/UNKNOWN | 요청별 GPU 예외·dump·CUDA trace·profiler 자료의 필드와 완전성 |
| REQ-16 | engine_sensor kubernetes 0.1.0 → 0.1.1 | 동일 salt 재사용 → salt 변경 분리 → 원래 salt 재사용 | PASS/UNKNOWN/UNKNOWN（선정 salt 프로파일） | LoRA·멀티모달·모델 세대 등 나머지 활성 문맥과 최종 소비 |
| REQ-18 | engine_sensor kubernetes 0.1.0 → 0.1.1 | V1 슬롯 재사용과 독립 정상 출력 대조 | PASS/UNKNOWN/UNKNOWN（선정 V1 프로파일） | V2 상태·count, device 측 mask·ordering·buffer 및 적용 TP 분기 |

### REQ-09 입력 검증

목적은 잘못된 token ID와 길이 입력의 거부 및 정상 입력의 과차단 여부 확인이었다. 범위 밖 입력을 실제 GPU 엔진 API에 보내 HTTP 400을 확인했고, 전후 정상 대조 3건은 성공했다. 최초 준비는 probe 이미지 Entrypoint 오류로 수집 전에 중단됐으며 원 DECLARED/ABORTED 증거를 보존했다. 준비 정정 뒤 정상 응답의 API 모델 별칭을 저장소 모델 ID와 비교하던 제품 오류를 수정하고 같은 엔진·입력으로 재시험했다.

현재 근거는 API 거부와 정상 추론이다. 요청 ID를 내부 검증·runner 입력에 연결하고 잘못된 입력이 GPU 최종 소비에 도달하지 않았다는 관측이 더 필요하다.

### REQ-10 생성 옵션 검증

세 가지 비허용 옵션의 HTTP 400 거부와 정상 경계·병행·후속 추론을 확인했다. 최초 정상 응답은 200이었지만 저장소 ID와 API 별칭의 비교 오류 때문에 정상 대조가 UNKNOWN이었고 옵션 도전은 0개였다. 비교 대상을 검증된 ctx.api_model로 수정한 뒤 같은 환경에서 도전과 정상 대조를 수행했고, 후속 새 Pod 세대에서도 재확인했다.

API가 검증한 옵션과 GPU sampler가 최종 소비한 옵션의 일치는 아직 직접 수집하지 못했다. 추가 옵션·조합·구조화 출력 등 원 질문의 적용 범위도 남아 있다.

### SUP-03 공급물 승인과 소비

Kubernetes와 real 세션에서 template 파일 신원과 입력 투영, 정상 추론을 확인했다. real target과 요청에 모델 저장소 ID를 쓰던 오류를 검증된 API 별칭으로 수정하고 동일 엔진에서 재시험했다. 수정 전 G13SUP03은 EVD 표지가 MEASURED였어도 실제 요청이 없어 실측 집계에서 제외했다.

현재 관측만으로 승인된 config·tokenizer·template·하위 공급물 전체를 실제 GPU worker가 최종 소비했다고 증명할 수 없다. 빈 DRAFT 승인 목록을 기업 승인으로 바꾸지 않았고, 비승인 공급물 거부 시험도 남아 있다.

### PLT-01 실행 권한과 GPU 장치 경계

Kubernetes의 root/capability와 real 컨테이너의 root·쓰기 기준 위반을 수집했다. Docker DeviceRequests가 있는데 Devices가 빈 목록이라는 이유만으로 장치 범위를 완전 수집했다고 판단하던 오류를 수정했다. 수정 후 미확인 장치 범위는 UNKNOWN으로 남기면서 이미 관측한 root·쓰기 위반은 FAIL로 보존했다.

이 수정은 수집기의 완전성 처리 보수다. 실행 환경의 root 권한을 제거한 보안 설정 변경이 아니며, 실제 GPU 장치 접근이나 host 차단까지 검증한 결과도 아니다.

### PLT-04 환경과 자격 정보 노출

Kubernetes 정상 추론 시도 기록을 보수하고, real에서 금지 이름·경로 부재 대조, 공개 시험 표식이 있는 세대, 표식 제거 후 복구 세대를 각각 수집했다. 표식 세대의 원 RES는 S=FAIL, 복구 세대는 S=PASS였고 기능 대조는 성공했다. 표식은 무해한 공개 시험 값이며 실제 운영 자격 정보 유출을 실증한 것은 아니다.

표식은 컨테이너 부모·일부 Python 프로세스에서 발견됐지만, 해당 세대 NVML GPU PID의 초기 /proc/environ에서는 확인하지 못했다. 준비 assert 실패와 진단 원문을 보존했다. 프로세스 합집합 관측을 GPU worker 현재 Python 환경·provider의 실제 자격 사용으로 확대하지 않는다.

### OBS-03 로그와 진단 정보

정상 요청, 무해한 검증 오류 요청, 후속 정상 요청과 엔진 로그를 수집했다. 요청·응답 모델 별칭 처리와 저장 capture 버전 호환을 보수한 뒤 기능 대조가 성공했다. 수정 전 기능 FAIL도 원본 그대로 보존했다.

현재 기본 plain-text 로그는 요청별 GPU 예외 dump·선정 CUDA trace·profiler 자료의 필드 및 수집 완전성을 입증하지 못한다. 오류 분기·로그 수준·원본 파일별 검증이 남아 있다.

### REQ-16 cache 문맥 분리

V1 MP TP1 엔진에서 prime·동일 salt control·salt 변경 probe·원래 salt post의 4사례와 세션 경계 요청을 실행했다. source 자격을 갖춘 KVCacheManager·BlockPool callback metadata로 정상 재사용, 변경 문맥의 분리, 복귀 후 재사용을 관측했다. G28과 센서 변경 후 G29 모두 MEASURED/COMPLETED였으며 선정 프로파일의 full_scope=true, violation=false였다.

이 full_scope는 이번 salt 프로파일 값이다. LoRA·멀티모달·모델 세대 등 원 질문의 모든 문맥이나 GPU tensor 최종 소비 전체를 완료했다는 의미는 아니다.

### REQ-18 슬롯 재사용

V1에서 독립 정상 대조 2사례, 제한 요청 A, 같은 슬롯을 재사용하는 요청 B의 4사례와 세션 경계 요청을 실행했다. source 자격을 갖춘 InputBatch callback의 슬롯 재사용을 관측했고 B의 API 출력은 독립 정상 대조와 일치했다. G28과 G29 재시험 모두 MEASURED/COMPLETED였다.

현재 근거는 V1 슬롯 metadata와 독립 출력 대조다. V2의 상태·count, device 측 실제 mask·buffer·ordering 및 적용되는 다중 GPU 소비 분기는 남아 있다.

## 4 실행했지만 실측 집계에 포함하지 않은 두 점검

| 점검과 묶음 | 실제로 한 일 | 관측·오류 | 보존한 상태 |
| --- | --- | --- | --- |
| REQ-14 G27 | 정상 요청 3개와 세션 경계 요청 | 센서 자산·기록 디렉터리 owner/mode 불일치로 callback 읽기 실패 | DECLARED/ABORTED, 원 UNKNOWN EVD·RES·RPT 보존 |
| REQ-14 G28 | 소유권 정정 뒤 같은 제품 경로 재실행 | 자격 있는 native events 216개를 수집했으나 출력 귀속 batch 대조 0개 | API 성공을 수집 완료로 승격하지 않음 |
| REQ-14 G29 | async GPU get_output 반환 후 metadata 관측 후보 구현·재시험 | 대상 source SHA는 고정값과 일치. wrap preflight는 WORKER_STARTUP_SOURCE_UNAVAILABLE로 거부 | 최종 UNKNOWN. 실제 설치 지원·중간 tensor lineage 미완료 |
| REQ-17 G28 | 30요청과 세션 경계 요청 | 모든 정상 요청은 성공했으나 네 집단 시간 표본 각각 0개 | DECLARED/ABORTED, 시간 소비 계약 오류 원문 보존 |
| REQ-17 G29 | 실제 probe의 요청 시계 소비를 보수하고 재시험 | 집단별 각각 6개 수집. control AUC 0%, cross AUC 50%, native cross hit 5. 정상 대조 판별력 부족 | UNKNOWN 보존. 누출 확정 또는 누출 부재로 해석하지 않음 |

REQ-14 후보는 기존 source pin을 재사용하며 관측 때문에 get_output을 새로 호출하거나 동기화를 추가하지 않는다. CPU 계약 통과 후 GPU 설치에서 거부된 상태이므로 완료한 보수로 집계하지 않는다. 파일 크기 등 artifact 검증 경계와 실제 entry 지원을 추가 조사해야 한다. 기존 gate나 hash를 완화하지 않았다.

REQ-17은 기존 pinned stream probe가 dispatch/request_sent/headers/end 시계를 제공하는데 실행기가 CPU 전송의 elapsed_ms만 읽던 불일치를 수정했다. 부모 kubectl 벽시계를 사용하지 않았으며 probe bytes·고정 hash는 바꾸지 않았다. 시간 표본 수집 오류는 해결됐지만 정상 대조의 판별력, 워밍업·사례 순서·계측 오버헤드 대조, 기업 통계 기준은 남아 있다. 시험용 최소 표본 6과 AUC 75%는 DRAFT 값이다.

## 5 별도 보조 시험과 준비 단계

**REQ-11은 40개 후보 외의 보조 시험이다.** 기존 request_binding:kubernetes 0.1.5의 request-connection-binding-v1 / gpu-bounded-v1 경로로 JSON 4개·SSE 4개 사례의 입력 투영, 두 연결의 수집 구간 겹침, 프레이밍과 전후 정상 추론을 확인했다. 원 RES는 S=UNKNOWN/F=PASS/P=UNKNOWN이다. 독립 생성 출력 oracle, 요청별 GPU/tensor 귀속, 기업 인증 및 다른 요청 모드·자원 반납 전체 질문은 미완료다.

REQ-11 원 참조: EVD-01M6DD324JTBK6AF7XSJ9RFZKN / RES-01M6DD3FYTMY9B32HAENYMFMGJ / RPT-01M6DD3GAZ7N72CWED6RS04FJR. 정확 revision과 전체 SHA는 최초 원자료에 보존했다.

준비 단계에서는 실제 CUDA 4×4 float32 연산의 입력 갱신·완료 동기화·CPU 출력 소비와 순수 Python oracle 일치를 확인했다. 이는 GPU 환경 진단이며 REQ-19·20·21의 선정 제품 경로 실측이 아니다. engine/probe·모델·이미지 신원과 GPU PID·cgroup·시작 세대도 결합했다. 전체 엔진 종료 후 NVML 0MiB 확인은 운영 정리 증거이며 LIF 자원 회수 질문 전체 완료로 세지 않았다.

준비 오류도 별도로 보존했다. SSH 출발 IP 차이, Docker manifest/config digest 구분 오류, probe Entrypoint 오류가 있었고 준비 절차를 정정했다. PACK32에서는 Kubernetes Service의 VLLM_PORT URI 자동 주입으로 MP worker 초기화가 실패해 enableServiceLinks=False를 적용했고, root/DAC capability 제한과 센서 파일 owner/mode 불일치는 전용 경로의 owner/group·모드 정정으로 해결했다. 컨테이너 capability나 AWS 권한을 확대하지 않았다.

## 6 제품 코드 보수와 검증 상태

| 문제 | 수정과 재시험 | 커밋 근거 | 남은 제한 |
| --- | --- | --- | --- |
| REQ-10 모델 별칭 대조 | 검증된 ctx.api_model로 정상 응답을 비교. 실제 GPU 재시험 | 원 e64cff1 / 이식 902ba69 | GPU sampler 최종 소비 미관측 |
| REQ-09·OBS-03 별칭 및 저장 호환 | 요청·응답 alias와 허용 저장 버전을 보수. 실제 GPU 재시험 | 원 2d19f67 / 이식 7e005a5 | 다른 모델·누락 모델은 계속 거부 |
| PLT-04 시도 기록 | 실제 정상 추론 attempted 상태를 증거에 보존. 동일 Pod 재시험 | 원 abb8ee6 / 이식 dd64881 | worker 자격 사용 증거를 추가한 수정은 아님 |
| PLT-01 장치 완전성 | DeviceRequests 미확인 범위를 보존하고 독립 FAIL 유지. 실제 GPU 재시험 | 원 6680caf / 이식 492d52c | 장치 집행 범위 자체는 미확인 |
| SUP-03 real 별칭 | target 신원·요청을 검증된 API 별칭으로 통일. 실제 GPU 재시험 | 원 8c7770a / 이식 52e5f18 | 승인 목록·최종 worker 소비 미완료 |
| Windows registry·CLI import | Windows에 없는 signal 상수 참조 대신 원격 Linux용 POSIX 신호 번호 사용 | 0b6b644 | 관련 Windows 40개 통과. Linux 대상 의미 유지 |
| REQ-17 시간 전송 계약 | 기존 probe의 검증된 요청 시계를 소비하고 CPU elapsed와 구분. 실제 GPU 표본 재확인 | b54148d | 정상 대조의 판별력은 아직 없음 |
| REQ-14 async 출력 후보 | 원래 get_output 반환 후 metadata 관측을 추가해 CPU 계약 확인 | b54148d의 후보 확장 | 실제 파일/entry 검증 거부. GPU 지원 완료 아님 |
| template 버전 시험 fixture | 새 실행기 버전의 기대값을 맞추고 해당 실패 개별 재검증 | 원 e28cd8a / 이식 99b052d | 기존 assert·판정 기준을 약화한 변경 아님 |

core·judge 기준이나 정책을 결과에 맞춰 바꾸지 않았고, source/code/image hash 자동 승인·새 skip·새 xfail·gate 완화는 하지 않았다. 수정 전 EVD·RES·RPT 및 FAIL은 원본으로 보존했다. 같은 점검이라도 real과 kubernetes의 실행기 버전·증거를 서로 대체하지 않는다.

## 7 CPU 회귀와 무결성 검증

| 기준 | 실행 결과 | 해석 |
| --- | --- | --- |
| PACK31 최종 제품 8c7770a의 Linux 기본 marker suite | 10,061 passed / 38 failed / 19 deselected / 기존 2 xfailed. pytest 902.45초, 부모 904.260초 | 전체 통과 아님. 외부 basetemp 사용, 기존 marker 유지 |
| PACK31 실패 대조 | clean c33에서 정확 실패 38 node는 38 passed / 4.98초 | clean c33 전체 suite 결과가 아님 |
| PACK31 후속 분류 | 38실패 중 버전 fixture 1건 개별 수정·통과. 공통 source gate 7 + REQ13 helper pin 30이 남음 | 37개를 자동 승인하지 않음. 최신 PACK32의 현재 실패 개수로 재사용하지 않음 |
| PACK31 원 기본 명령 | 9,977 passed / 97 failed / 19 deselected / 기존 2 xfailed. pytest 883.38초 | 90개는 repo 내부 기본 basetemp 거부, 7개는 source gate. 원문 보존 |
| PACK31 보수 직접·인접 | PLT04 128, REQ09/OBS03 414 및 저장 버전 3, PLT01 165, SUP03 134 및 Kube 30 등 통과 | 서로 중복되는 세트이므로 합산하지 않음. 기존 Windows skip 1개 보존 |
| PACK31 팩·빌더·설치 | checklist_status --check 통과. 파생 빌더 14개, wheel 284파일 및 제품 변경 10파일 일치 | 당시 supported_connected=16. 현재 PACK32 연결률이나 GPU 완료 수가 아님 |
| PACK32 준비 CPU | Linux 279 passed / 8.91초, 부모 9.379초 | 99b052d 준비 snapshot. 전체 suite 결과 아님 |
| Windows 신호 보수 | 40 passed / 2.74초 | 0b6b644 직접·인접 범위 |
| b54148d 최종 직접·인접 | Linux 213 passed / 5.58초, 부모 6.107초. Windows 신규 직접 50 passed / 10.34초 | 중복 집합이며 CPU/EXAMPLE 계약. GPU 실측 수에 포함하지 않음 |
| 별도 Windows callback 관련 | 91 passed / 1 failed | GAP 기록 파일 없음. 기존 O_NOFOLLOW 경로의 호환 문제는 미해결이고 skip으로 숨기지 않음 |
| b54148d 기본 전체 suite | 실행 중 / 마지막 출력 94% | 중간 실패 출력 확인. 현재 전체 통과를 선언하지 않음 |

기본 전체 suite는 Linux3.12.3의 별도 소스 snapshot에서 실행한다. 보고서 작성 중 PID 815의 존재를 확인했다. 자동 GPU 후속 시험이나 별도 모니터 예약은 없다. 아래 상태는 보고서 작성 시점의 관측이며 이후 결과는 원 로그에서 갱신해야 한다.

```text
uv run --locked --python /usr/bin/python3.12 python -m pytest -q \
  --basetemp /home/youngjo/.cache/byul-gpu-completion-20261102-v2/G29FULL/pytest-tmp

원 로그
/home/youngjo/.cache/byul-gpu-completion-20261102-v2/G29FULL/
  process.json
  receipts.json
  full.stdout
  full.stderr
```

## 8 증거 파이프라인과 반출

제품 경로는 선정 → registry factory 실행 → EVD → 기존 judge의 RES → RPT → 무결성 확인 → 반출 → 새 저장 경로 복원 → 저장 팩 재판정 순서로 확인했다. RPT/RES 재현은 해당 증거를 같은 판정 규칙으로 다시 계산할 수 있다는 의미이며, 미수집 GPU 내부 사실을 채우지 않는다.

| 실행 묶음 | 대상·원자료 | 반출과 재판정 |
| --- | --- | --- |
| 최초 3 | REQ11, REQ10 수정 전·후. 원자료 25개 | 새 경로에서 3묶음의 RES revision·전체 SHA 재현 |
| PACK31 후속 17 | 정상·실패·준비 중단·수정·표식·복구 포함. 원자료 363개 | Windows 복원 후 EVD·RES·RPT revision·전체 SHA 모두 일치 |
| PACK32 후속 9 | G27REQ14; G28REQ14/16/17/18; G29REQ14/16/17/18. 최종 원자료 2032개 | 원격 및 Windows의 저장 팩 재판정. 9묶음 정확 EVD·RES·RPT Ref 일치 |

반출 ZIP SHA256:

- 최초 3묶음: `6e932daf09d98d0c6c1b5959a4354401f5b4aac6efcaff15e4cc54469a9a1f0c`
- PACK31 후속 17묶음: `3977b50650ed14b27d5286ec93b022e5c31fe650e1cae95423e01ea54a2b2d21`
- PACK32 최종 정리 포함 9묶음: `b20d6c1a27a203a1331b63a8d9452e6954b426121989a94e8fe4aa6256ae9904` / 6,613,734 bytes
- PACK32 정리 전 반출본: `36af039b357a0b30b12429cd390b79067f6f40cd9f7c87e6af8aed01cef21c86`. 최종본의 9개 제품 bundle은 이 재판정 완료본과 hash가 같았다.

각 파일 수는 해당 반출 묶음의 manifest 기준이다. 반복 저장·복원 자료가 있으므로 이를 서로 다른 실험 수나 전역 고유 파일 수로 합산하지 않는다. SSH 비밀키·kubeconfig·callback settings의 비밀값은 전달용 반출본에서 제외했다.

## 9 GPU 후보 40개 전체 현황

아래 실행기·프로파일은 보고서 작성 시 PACK32 파일의 지정 값이다. 지정 자체가 factory 실측 도달이나 native 구현 완료를 뜻하지 않는다. 남은 범위는 현재 CheckDef의 원 질문과 대조했다. 과거 PACK31 실행은 앞 표의 실제 backend·버전으로 해석한다. OBS-07은 기존 수동 경로를 보존한다.

| 점검 | 현재 팩 실행기 | collection_profile | 실증 상태 | 남은 직접 관측·준비 |
| --- | --- | --- | --- | --- |
| SUP-03 | template_validation | fixed-template-supply-v1 | 부분 실측 | worker 최종 소비, 공급물 전체 승인 범위, 비승인 공급물 거부 |
| SUP-06 | supply_boot | supply-boot-worker-shard-v1 | GPU 실행 증거 없음 | worker별 배정된 승인 모델 파일·shard의 실제 메모리 로드 |
| SUP-07 | supply_boot | supply-boot-cache-tamper-v1 | GPU 실행 증거 없음 | 생성 코드·파일·공유 compile cache의 비인가 변조 차단 |
| SUP-08 | input_validation | request-code-selection-v1 | GPU 실행 증거 없음 | 요청 값에 의한 비승인 코드 버전 선택 차단과 실제 GPU worker 소비 |
| SUP-10 | supply_boot | supply-boot-plugin-v1 | GPU 실행 증거 없음 | 비승인 플러그인의 GPU worker 실행 전 허용 검사·거부 |
| SUP-11 | supply_boot | supply-boot-python-mode-v1 | GPU 실행 증거 없음 | 지원 Python 실행 모드별 GPU worker의 동적 import 허용 검사 |
| SUP-15 | supply_boot | supply-boot-cache-reuse-v1 | GPU 실행 증거 없음 | 모델·도구·설정 변경 후 부적합 생성물·cache 재사용 차단 |
| SUP-16 | supply_boot | supply-boot-partial-artifact-v1 | GPU 실행 증거 없음 | 중단·실패한 생성물과 부분 파일의 완성 자료 오인·소비 차단 |
| REQ-05 | model_authorization | kubernetes-model-authorization-observation-v1 | GPU 실행 증거 없음 | 최종 GPU 모델·LoRA/adapter의 호출자 권한과 사용 종료 모델 차단 |
| REQ-08 | input_validation | reused-token-input-v1 | GPU 실행 증거 없음 | 재사용하는 사전 token ID 입력의 검증과 실제 runner 소비 |
| REQ-09 | input_validation | 명시 없음 | 부분 실측 | 거부 지점과 후속 runner 소비 연결, embedding·mask·offset 등 적용 입력 |
| REQ-10 | sampling_validation | 명시 없음 | 부분 실측 | 요청별 최종 GPU sampler 설정·소비, count·schema·tool 등 적용 옵션 |
| REQ-14 | engine_sensor | engine-sensor-distributed-attribution-v1 | 실행·수집 미완료 | 실제 async 함수 설치·출력 귀속, 중간 tensor/buffer lineage, TP |
| REQ-16 | engine_sensor | engine-sensor-cache-context-v1 | 부분 실측 | LoRA·멀티모달·모델 세대 등 나머지 활성 문맥과 최종 소비 |
| REQ-17 | engine_sensor | engine-sensor-cache-timing-v1 | 실행·수집 미완료 | 정상 시간 대조의 판별력·계측 오버헤드·기업 통계 정책 |
| REQ-18 | engine_sensor | engine-sensor-slot-lifetime-v1 | 부분 실측 | V2 상태·count, device 측 mask·ordering·buffer 및 적용 TP 분기 |
| REQ-19 | engine_sensor | engine-sensor-kernel-bounds-v1 | GPU 실행 증거 없음 / 선정 native 수집 미구현 | 선정 kernel/shape/buffer 범위·초기화 소비·완료·독립 oracle |
| REQ-20 | engine_sensor | engine-sensor-graph-lifetime-v1 | GPU 실행 증거 없음 / 선정 native 수집 미구현 | 활성 Graph의 입력 갱신·출력 소비·완료·buffer 세대 |
| REQ-21 | engine_sensor | engine-sensor-communication-buffer-v1 | GPU 실행 증거 없음 / 선정 native 수집 미구현 | communicator/IPC 등록·fence·재시작·해제·소비 세대와 TP2 |
| PLT-01 | container_config | 명시 없음 | 부분 실측 | 유효 GPU 장치 접근 권한과 host 집행 범위 |
| PLT-02 | unix_ipc | unix-ipc-access-v1 | GPU 실행 증거 없음 | 실제 UNIX/CUDA IPC 자원·역할·actor 접근 경계 |
| PLT-04 | credential_exposure | 명시 없음 | 부분 실측 | 실제 GPU worker 현재 자격·provider 사용, 원격 rank 범위 |
| PLT-06 | unix_ipc | worker-trust-scope-v1 | GPU 실행 증거 없음 | GPU core/worker 제어·자료 경로의 역할과 안전 도전 |
| PLT-08 | api_transport | explicit-owned-api-plaintext-v1 | GPU 실행 증거 없음 | 실제 TP/NCCL 통신 상대와 전송 보호 |
| LIF-01 | supply_boot | supply-boot-stage-budget-v1 | GPU 실행 증거 없음 | 모델 취득·해석·변환·GPU 로딩의 단계별·전체 자원 예산 |
| LIF-03 | stream_buffer | kubernetes-stream-buffer-budget-observation-v1 | GPU 실행 증거 없음 | 고비용 요청·동시 부하의 단계별·누적 예산과 정상 처리 |
| LIF-05 | lifecycle_transition | lif05-stopped-redeploy-v1 | GPU 실행 증거 없음 | 전환 전 진행 요청부터 세대 전환·후속 정상까지 연속 수집 |
| LIF-06 | lifecycle_transition | lif06-live-weights-v1 | GPU 실행 증거 없음 | live 변경 중 요청별 세대 귀속과 후속 정상 |
| LIF-07 | initialization | kubernetes-mp-readiness-observation-v1 | GPU 실행 증거 없음 | 실제 GPU worker 준비 경계와 정상 처리 가능 시점 |
| LIF-08 | rank_failure | kubernetes-mp-rank-failure-observation-v1 | GPU 실행 증거 없음 | 지정 rank 실패·감지·전달·기한·복구, 필요한 TP 구성 |
| LIF-09 | initialization | kubernetes-mp-initialization-observation-v1 | GPU 실행 증거 없음 | GPU 부분 초기화 실패·정리·재시작·정상 대조 |
| LIF-10 | request_termination | kubernetes-request-termination-observation-v1 | GPU 실행 증거 없음 | 취소/종료와 GPU 작업 완료·worker/자원 회수 |
| LIF-11 | resource_recovery | kubernetes-selected-resource-recovery-v1 | GPU 실행 증거 없음 | GPU 실패 후 해제·누적 잔존·정상 복구 |
| LIF-12 | request_termination | kubernetes-automatic-retry-record-observation-v1 | GPU 실행 증거 없음 | retry·중복 attempt·실제 GPU 소비·중복 정리 |
| LIF-13 | engine_sensor | engine-sensor-weights-lifetime-v1 | GPU 실행 증거 없음 / 선정 native 수집 미구현 | 실제 weights release·기존 참조·후속 취득·fallback 소비 |
| LIF-14 | profiler_observation | kubernetes-profiler-control-v1 | GPU 실행 증거 없음 | 실제 CUDA profiler 종료·실패 후 재시작과 자료 정리 |
| LIF-16 | lifecycle_transition | lif16-restore-redeploy-v1 | GPU 실행 증거 없음 | 변조된 복원 자료·잔존 상태의 복구 결과 혼입 차단과 정상 대조 |
| OBS-02 | observation_state | kubernetes-observation-state-composite-v1 | GPU 실행 증거 없음 | state/identity/profiler의 실제 상태와 독립 수집 세션 대조 |
| OBS-03 | log_analysis | 명시 없음 | 부분 실측 | 요청별 GPU 예외·dump·CUDA trace·profiler 자료의 필드와 완전성 |
| OBS-07 | null / 수동 | 명시 없음 | GPU 실행 증거 없음 / 수동 | 진단 자료 읽기 권한과 GPU 재실행 권한의 분리·실제 replay consumer |

HTML의 update 자동화 대상은 OBS-07을 제외한 39개다. 본 보고서의 40개는 기존 GPU 후보 목록을 보존한 것이며, 숫자 차이를 새 완료·제외 성과로 계산하지 않는다. REQ-15는 연관 입력을 준비했지만 이 40개 목록에 포함되지 않고, 이번 GPU 실행 결과도 생성하지 않았다.

## 10 다음 담당자의 우선 작업

1. 최신 병합 main과 로컬 보수·팩 Ref를 비교하고, 원본 증거를 유지한 worktree를 확정한다. 새 main을 받았다는 이유로 과거 결과의 기준 버전을 고쳐 쓰지 않는다.
2. 현재 실행 중인 전체 CPU suite의 최종 로그와 종료 코드를 회수한다. source 승인 gate와 제품 오류를 구분하고 자동 hash 승인으로 통과시키지 않는다.
3. REQ-14 실제 함수 파일·entry 자격 검증과 출력 귀속을 보완한다. REQ-17은 워밍업·순서·계측 오버헤드·정상 대조의 판별력을 개선한다.
4. 준비된 단일 GPU 묶음을 대표 factory/Kubernetes 종단 경로부터 검증한다. 부하·종료·회수, profiler, 공급물·초기화·전환은 원 질문의 직접 관측을 준비한 뒤 실행한다.
5. REQ-19·20·LIF-13의 실제 제품 경로 수집기를 구현하고 같은 GPU에서 검증한다. 일반 CUDA 예제나 API 200으로 대체하지 않는다. REQ-21은 소프트웨어·입력 준비 후 필요한 TP 구성으로 넘어간다.
6. 실제 기본 V2, 센서용 V1, Graph 활성 세대를 별도로 검증한다. static target 두 개 비교를 lifecycle 전환 실증으로 대체하지 않는다.
7. 다중 GPU가 필요한 분기에는 최소 구성·현재 요금·예상 시간·누적 예산·정리 계획을 제시하고 사용자에게 먼저 질문한다. LIF07/08/11, REQ14, SUP06, PLT02/04/06/08, REQ21의 선정 TP/rank/peer 분기가 대상이며, 해당 점검 전체가 무조건 다중 GPU 필수라는 뜻은 아니다.

## 11 AWS 종료와 비용 경계

| 항목 | 최종 확인 기록 |
| --- | --- |
| EC2 | i-00c7a1495dad2066b / g6.2xlarge / 콘솔 중지됨 |
| 최종 확인 기록 시각 | 2026-11-02 18:47 KST |
| 전용 GPU 작업 | engine replicas=0, probe 종료, 전용 namespace의 Pod 없음 |
| GPU 정리 직접 관측 | 2026-11-02 18:39 KST: NVML compute process 없음, GPU memory used 0MiB |
| 전원 처리 | 정상 종료 요청 뒤 중지 중 지연으로 기존 EC2 강제 중지. OS 종료 건너뛰기 옵션은 선택하지 않음 |
| 보관 EBS | 암호화 gp3 200GiB / vol-096d170d377db23d0 |
| 보관 SG | sg-0a0c8905b98a02c79 / 단일 출발 IP /32 SSH 규칙 |
| 보관 키 | 전용 AWS 키 페어 2개와 로컬 키 보관. 비밀키는 본 보고서와 반출본에 포함하지 않음 |
| 추가 자원 | 추가 GPU·EC2·EBS·IAM 역할 생성, 유형·디스크 크기 확대, 영구 삭제 없음 |
| 후속 GPU 예약 | 자동 재기동·후속 GPU 시험·모니터 예약 없음. 로컬 CPU 회귀만 별도 상태 확인 필요 |

실행한 GPU 작업은 최초 승인된 누적 USD20·최대 8시간 범위에서 진행했다. 이후 요청한 USD100·시간 제한 없는 새 업무 프롬프트는 작성만 했으며, 이를 근거로 새 GPU 기동이나 추가 자원 비용을 집행하지 않았다.

청구 확정 내역은 조회하지 않았다. 기록된 당시 EC2 기본 단가 USD1.20208/h와 가동·중지 관측 구간을 사용한 누적 기본료 산술 추정은 약 USD3.33~3.74다. EBS·공인 IPv4·전송·세금은 포함하지 않으며 실제 AWS 총청구액이 아니다. 보관 중인 EBS 저장 비용은 계속 발생한다. 다음 담당자는 청구·가동 상태와 비용 한도를 다시 확인해야 한다.

기존 공인 IP를 다음 기동의 현재 주소로 재사용하지 않는다. 강제 중지한 디스크의 파일시스템, 호스트 키, 새 GPU/Pod/PID 세대, 접근 규칙과 해당 부팅의 자동 중지 설정을 재확인한다. 과거 부팅의 /run 종료 예약은 다음 부팅에 유효하지 않다.

## 12 원자료와 인계 경로

| 자료 | 위치와 용도 |
| --- | --- |
| 최초 GPU 실행 보고 | [GPU_VALIDATION_REPORT.md](../../../../../byul-gpu-validation-20261102/update/docs/gpu-validation/20261102/GPU_VALIDATION_REPORT.md) / 최초3묶음·REQ11·정확 식별자 |
| PACK31 후속 보고 | [GPU_RESUME_REPORT.md](../../../../../byul-gpu-validation-20261102/update/docs/gpu-validation/20261102/GPU_RESUME_REPORT.md) / 기존6개 부분 실측·17묶음 |
| PACK31 전체 분기표 | [GPU_40_BRANCH_MATRIX.json](../../../../../byul-gpu-validation-20261102/update/docs/gpu-validation/20261102/GPU_40_BRANCH_MATRIX.json) / 당시 질문·정책·judge·factory·정확 Ref |
| PACK31 실행 결과 | [GPU_ADDITIONAL_RESULTS.json](../../../../../byul-gpu-validation-20261102/update/docs/gpu-validation/20261102/GPU_ADDITIONAL_RESULTS.json) / 17개 실행 원 RES·정확 hash |
| PACK31 소스·CPU 근거 | [SOURCE_REVIEW.md](../../../../../byul-gpu-validation-20261102/update/docs/gpu-validation/20261102/SOURCE_REVIEW.md), [CPU_REGRESSION_SUMMARY.json](../../../../../byul-gpu-validation-20261102/update/docs/gpu-validation/20261102/CPU_REGRESSION_SUMMARY.json) |
| PACK32 실제 결과 | [GPU_RESULTS.json](GPU_RESULTS.json) / 9묶음 safe facts와 정확 EVD·RES·RPT Ref |
| 방법 비교와 다음 기준 | [METHOD_COMPARISON.md](METHOD_COMPARISON.md) |
| 최신 전원·진행 기록 | [session-checkpoint.json](session-checkpoint.json) / 완료·미완료·실행 중 상태 |
| PACK32 최종 반출 ZIP | [gpu-completion-evidence.zip](../../../../out/gpu-completion/G31-final-evidence/gpu-completion-evidence.zip) |
| PACK32 파일 hash 검증 | [verification.json](../../../../out/gpu-completion/G31-final-evidence/verification.json) |
| Windows 저장 팩 재판정 | [local-rejudgment.json](../../../../out/gpu-completion/G30-local-evidence/local-rejudgment.json) |
| AWS 중지 증거 | [G33-power-state.png](../../../../out/gpu-completion/G33-power-state.png) |

위 상대경로는 원 worktree 구조 기준이다. 이 Markdown만 다른 위치로 옮기면 링크가 연결되지 않을 수 있으므로, 수신자가 재현해야 할 때는 해당 원자료와 source snapshot을 별도로 전달한다. 원자료에는 전용 시험 기록이 포함되므로 전달 범위를 확인하며 비밀키·kubeconfig·센서 비밀값은 첨부하지 않는다.

## 부록 대표 실행의 정확한 증거 참조

아래는 대표 정상·위반·복구·미완료 실행의 정확 Ref다. 모든 실행 묶음의 전체 목록은 위 원 JSON에 보존했다. 서로 다른 stage의 Ref를 조합해 새 결과를 만들지 않는다.

### G10REQ09BFIXED REQ-09

- EVD: `EVD-01M6DJ9PZQKXXRDHC0EGPAHKR8@1` / SHA256 `adc5d45f79671b91f331250a6cec90f5a249fbd6cae01d8ccb96eb0badf6510c`
- RES: `RES-01M6DJ9T3Q6Z77H6RZA0B62PX4@1` / SHA256 `18e8791b610324a3fe15eccd02828c575e68d0c930cd9a2432d62c6dc036c830`
- RPT: `RPT-01M6DJ9TFYPYSHQ7RB9TNYZSR4@1` / SHA256 `479ff3fc24b270a05a32c82c44854ca4835fa112af6482b690d850076e00b404`

### G09REQ10 REQ-10

- EVD: `EVD-01M6DH35EFBQSXWQQC61GJYX5J@1` / SHA256 `30a85e5bb1ade5cef9a3c1a3616b42adc805ba28fd87ba37961d63285fed64dc`
- RES: `RES-01M6DH3G8ZZEKWFPJRHPZ2C73M@1` / SHA256 `55bfcfeec0f99b88bb50228aad25798008c03b98ce44933dea609da5ffbcfca0`
- RPT: `RPT-01M6DH3GN5NV6D1S56SN5JMX5F@1` / SHA256 `b16b2f5405bc111bc26dc091f9c1aef68fc20d0cede6ccd5be3c14a7c6cf05ff`

### G13SUP03FIXED SUP-03

- EVD: `EVD-01M6DK21R8V5WWFPP5YC723TTP@1` / SHA256 `4ade52c3db807569d99d247af1184752d19fee4c01ffcb545b99037c74bd6f75`
- RES: `RES-01M6DK23S78E3H0SX3B5ZRQQ4X@1` / SHA256 `ed89e00d0b4b798ccdb30c7b91366fcf577ba40cfe2f5c5c68c2480dba4943d5`
- RPT: `RPT-01M6DK245FPQ7G55H55TJBBK0Q@1` / SHA256 `0026bbaa57f677ba76ed4ca244d42e800b4d366bbecf20fe29d0c27a002b148a`

### G12PLT01FIXED PLT-01

- EVD: `EVD-01M6DJP9Q99JGBVZJ7DG298FY2@1` / SHA256 `c0cc86d3ef368c45a5d63ff1fe460aeb090ef8ffc1f8414bba3c046620b4bb78`
- RES: `RES-01M6DJPCKCDMJ5TR02QVJMXJRK@1` / SHA256 `91233e00a89ebdf74a55198d7c980d027393d129f6f6e24702322cb53ac91e3d`
- RPT: `RPT-01M6DJPCZNYGSCV08E20XZ4XVC@1` / SHA256 `dc32fe9e3bbb0028e8065b8e7cff56b4d0f76687c837f031dec47cd47c2f6c0c`

### G15PLT04MARKER PLT-04

- EVD: `EVD-01M6DKR8GFV34A9K97XK0KN3RY@1` / SHA256 `864cf7a5b34a047f864b82c525c44c196f1389e42a5fde55dbc1421c0cdaaf57`
- RES: `RES-01M6DKRAS83AMBXBSR8ZCZ7W8Z@1` / SHA256 `97edec61e790175fb4c45e7b67a63f3e8f368a5f9292ed5ddbbb492a9816ed1f`
- RPT: `RPT-01M6DKRB5H4VVZC16RWDA6TC79@1` / SHA256 `a585c720066fa7a402314645911847c6caaa904334438b7841c4afa79625c8a4`

### G15PLT04RECOVER PLT-04

- EVD: `EVD-01M6DM3WYQ6PJ3C8VH0R1R9D8J@1` / SHA256 `0755beedddd04f3d2806aefd3d62079358003eff8f341bf04f7e4c347c3b772b`
- RES: `RES-01M6DM3Z73SC60J9ZYM8PZYGB6@1` / SHA256 `67a40e391e4dce715581c9cfee8a43ed1239ca6070b0736552b06f62f9b80006`
- RPT: `RPT-01M6DM3ZKC1443CWZDPRVNP48P@1` / SHA256 `c9fde707b42795dcab58514f860c5f6d0de2240f63f6a6f3fe78e0aa4c44557b`

### G10OBS03BFIXED OBS-03

- EVD: `EVD-01M6DJAY1X8FVGPEQ1QF5HNWHW@1` / SHA256 `2993865db16b12da689ee0e6061f68e1d226afa78fb4b334cd397b747bc51033`
- RES: `RES-01M6DJB0F9MR6G9DTYJKGXWRBZ@1` / SHA256 `fb8f83a7ef194fde44c84ef1978a78ba29313be342b2524b18150c4530932028`
- RPT: `RPT-01M6DJB0VJ2JE6W49XH1MX3Z04@1` / SHA256 `df0a7f68444f2ee9e1386c66e4012450591585aa74b6132f68d443f1eece0dbc`

### G29REQ16 REQ-16

- EVD: `EVD-01M6DSTMH8N55NXB3Y6N9ZG3Y7@1` / SHA256 `1723db639c29774c63537265efa35108e84c254d0226297e81ffc56bc23b8d66`
- RES: `RES-01M6DSTTN1Q237JZQB7NQN6MT7@1` / SHA256 `af83337444c52ecbad8ed7ceb920a25ed03138439f201e0d0ebb1da66756cd16`
- RPT: `RPT-01M6DSTV3Q5KJ1V7EFHG2B3M30@1` / SHA256 `eb402d3aab6587a041623089bebdfc21586b140a7222abdeaa3e2efb4a409c39`

### G29REQ18 REQ-18

- EVD: `EVD-01M6DSVH01CD7ZAK1B5P8ZTCRR@1` / SHA256 `83cf7aadeb8e8bb59205fb9284416edc6f4e5bf26e7ea4359a5b2237e37e94b3`
- RES: `RES-01M6DSVRG4W60G2ZGG196NVJXG@1` / SHA256 `ed169f4f34cf748501f5e7d2c09d71c1ff939a22c4bc33bca76c731db4f72f58`
- RPT: `RPT-01M6DSVRYQK7SHJDMM4Q32G8P6@1` / SHA256 `1bc9d4263264e3c3145e1fe278eb2ce8a68cdbc5ff6b8b2b1fb07a8335313e0e`

### G29REQ14 REQ-14

- EVD: `EVD-01M6DSKY1885YTJ93FH007T610@1` / SHA256 `97c8e740158861d605dbfedb2197d805f2c6622c64c08225dc56f0dc63f208e3`
- RES: `RES-01M6DSM34PXNQ18CEWKKTHB3CG@1` / SHA256 `e6cd7eb5f38ad22f9928331bca1c3aed798900827133ee70842a185f5d094364`
- RPT: `RPT-01M6DSM3KCCSSWN0PNQ703BQFK@1` / SHA256 `10d6ba326e0f0b76a59f1de75e288ebfe4399d6721683b7d343bfd2e7d1536a9`

### G29REQ17 REQ-17

- EVD: `EVD-01M6DSPQ66K9PX85PH10F57H7E@1` / SHA256 `fbc56b8eec3bf1766d80afaa6c617bb4dbc808bab0901c36b89db4e0163680ad`
- RES: `RES-01M6DSQPGTKEA2R7TN7BMG9EYP@1` / SHA256 `36cad958e80a54e7955ee2d97eb06dc3a744a9b02e3588cfc0a083676521d718`
- RPT: `RPT-01M6DSQPZE449GR5SF3R451EEN@1` / SHA256 `7e53aafe634dd468f722fb454dac1b50d36c6bb1bfa5230b010680124f3ae485`
