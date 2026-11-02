# GPU 후보 40개 실증 현황과 인계 요약

기준일: 2026-11-02 KST

단일 NVIDIA L4 환경에서 기존 실행기·체크리스트를 실제 실행하고 오류를 보수했다. **40개 중 8개 부분 실측, 2개 실행·수집 미완료, 30개 GPU 실행 증거 없음이며 원 질문의 모든 적용 분기 완료는 0개다.** 이 문서는 실증 현황 기록이다. 아래 결과를 제품 코드 통합·source 승인·운영 수락으로 확대하지 않는다.

## 진행량

| 구분 | 수 | 내용 |
| --- | --- | --- |
| GPU 후보 전체 | 40 | 기존 확정 목록 유지 |
| 부분 실측 | 8 | REQ-09, REQ-10, SUP-03, PLT-01, PLT-04, OBS-03, REQ-16, REQ-18 |
| 실행했으나 실측 집계 미포함 | 2 | REQ-14와 REQ-17의 수집·대조 미완료 |
| 이번 업무의 GPU 실행 증거 없음 | 30 | 공급물·초기화·전환·회수·profiler·native GPU·TP 등 남은 분기 |
| 원 질문 전체 완료 | 0 | 부분 실측 8개에도 미완료 분기 존재 |
| 별도 보조 시험 | REQ-11 | JSON·SSE 8사례. 40개 분모에 미포함 |
| 제품 실행 증거 묶음 | 29 | 최초 3 + PACK31 후속 17 + PACK32 후속 9. 실패·중단·수정·복구 포함 |

부분 실측은 GPU 엔진이 있는 환경에서 일부 사실을 관측했다는 뜻이다. API·프로세스·컨테이너 관측과 실제 GPU kernel·tensor 최종 소비의 증명을 구분한다. CPU·EXAMPLE·대역·factory 생성·일반 CUDA 환경 진단은 GPU 실측 수에 포함하지 않는다.

## 실제 환경과 기준

- AWS 서울 g6.2xlarge 1대, NVIDIA L4 1장, TP1. vLLM 0.31.0, Torch 2.13.0+cu130, CUDA runtime 13.0, Python 3.12.3.
- 이미지: `vllm/vllm-openai@sha256:a4a4c0437bf7240089da5f08aa370c4aee17ae5290f7a3b468825ee26c4c3a6b`.
- 모델: `Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775`, 검증된 API 별칭 `byul-gpu-lab`.
- 초기·PACK31 묶음은 V2/eager, PACK32 callback 묶음은 V1/MP/eager/prefix caching 활성 구성이다. Graph와 다중 GPU는 미실측이다.
- PACK31 기록은 c33f29a 기준 제품 보수8c7770a, PACK32 기록은 병합 main1174da6 및 제품 보수0b6b644/b54148d 기준이다. 서로 다른 팩·세대·backend의 증거를 합성하지 않았다.
- 정책은 기술 시험용 DRAFT/DECLARED, `confirmed_by=null`이다. 기업 승인자·승인 기록을 생성하지 않았다.

## 부분 실측 8개

| 점검 | 실제 수행과 관측 | 남은 분기 |
| --- | --- | --- |
| REQ-09 | 범위 밖 token ID·길이 입력 HTTP400, 전후 정상 추론. 모델 별칭 비교 오류 보수 후 재시험 | 내부 거부 지점과 runner 최종 소비, embedding·mask·offset 등 적용 입력 |
| REQ-10 | temperature/top_p/min_tokens 비허용 값 거부, 정상·병행·후속 대조. 별칭 보수 후 재시험 | 요청별 최종 GPU sampler 설정·소비, 추가 옵션·조합·구조화 출력 |
| SUP-03 | template 신원·입력 투영·정상 대조. real 요청의 별칭 보수 후 재시험 | 공급물 전체 승인 범위, worker 최종 소비와 비승인 공급물 거부 |
| PLT-01 | root·쓰기 등 DRAFT 기준 위반. DeviceRequests 미확인 범위를 보수하면서 독립 FAIL 보존 | 유효 GPU 장치 접근 권한과 host 집행 범위 |
| PLT-04 | 정상 추론, 공개 시험 표식 세대의 위반, 제거·복구 세대를 각각 수집 | GPU worker 현재 환경·자격/provider 실제 사용. 프로세스 합집합을 worker 전체로 확대하지 않음 |
| OBS-03 | 정상→무해 오류→정상과 로그 수집. 별칭·저장 버전 호환 보수 후 재시험 | 요청별 GPU 예외·dump·CUDA trace·profiler 자료의 필드와 수집 완전성 |
| REQ-16 | native cache metadata로 동일 salt 재사용·변경 분리·원래 salt 복귀를 관측하고 센서 변경 후 재확인 | LoRA·멀티모달·모델 세대 등 나머지 문맥과 최종 소비 |
| REQ-18 | V1 슬롯 재사용과 후속 출력의 독립 정상 대조 일치를 관측하고 재확인 | V2 상태·count, device 측 mask·buffer·ordering 및 적용 TP 분기 |

원 RES의 선정 프로파일 판정은 보존했다. PLT-01의 독립 FAIL, PLT-04 표식·복구 세대의 서로 다른 결과, REQ-16/18의 제한 프로파일 결과를 전체 시스템 판정으로 확대하지 않는다.

## 실행했지만 수집이 완료되지 않은 점검

- **REQ-14:** 정상 요청3개와 세션 경계 요청을 실행했다. 센서 파일 접근 문제 정정 후 native events216개를 수집했으나 출력 귀속 batch 대조가 없었다. async 출력 확장 후보는 CPU 계약을 통과했어도 실제 GPU 함수 파일 검증에서 `WORKER_STARTUP_SOURCE_UNAVAILABLE`로 거부됐다. source SHA 일치만으로 설치를 승인하지 않았고 UNKNOWN을 보존했다. 실제 출력 귀속과 중간 tensor/buffer lineage는 미완료다.
- **REQ-17:** 30요청과 세션 경계 요청을 실행했다. 실제 probe의 dispatch/end 시계와 CPU elapsed 계약의 불일치를 소비부에서 수정했다. 재시험은 네 집단 각각6개 표본을 수집했지만 control AUC0%, cross AUC50%로 정상 대조의 판별력이 없어 UNKNOWN이다. native cross hit5라는 값만으로 누출을 확정하지 않는다. 워밍업·사례 순서·계측 오버헤드 및 통계 정책 대조가 남아 있다.

## 증거와 재판정

기존 선정→실제 factory 실행→EVD→기존 judge의 RES→RPT→반출→새 경로 복원→저장 팩 재판정을 수행했다.

- 최초3묶음은 정확 RES revision·전체 SHA를 재현했다.
- 후속17묶음과 PACK32의9묶음은 Windows에서도 정확 EVD·RES·RPT revision·전체 SHA가 일치했다.
- PACK31 후속 원자료363개, PACK32 최종 원자료2032개를 각 manifest 기준으로 검증했다. 반복 저장 자료를 전역 고유 파일 수나 완료 점검 수로 합산하지 않는다.
- PACK31 후속 ZIP SHA256: `3977b50650ed14b27d5286ec93b022e5c31fe650e1cae95423e01ea54a2b2d21`.
- PACK32 최종 ZIP SHA256: `b20d6c1a27a203a1331b63a8d9452e6954b426121989a94e8fe4aa6256ae9904`.
- 비밀키·kubeconfig·callback settings 비밀값은 전달용 반출본에 넣지 않았다. 실제 원자료는 로컬 보존본이며 이 문서 PR에 업로드하지 않는다.

## 검증 상태

아래 시험은 기록된 제품 snapshot의 결과이며, 이 문서 PR의 HEAD를 실행한 결과는 아니다.

| 대상 | 실제 결과 | 해석 |
| --- | --- | --- |
| b54148d 직접·인접 CPU | Linux213 passed / 5.58초, 부모6.107초. Windows 신규 직접50 passed / 10.34초 | CPU/EXAMPLE 계약이며 GPU 완료 수에 미포함 |
| Windows 신호 import 보수 | 40 passed / 2.74초 | 원격 Linux용 신호 의미 유지 |
| 별도 Windows callback 관련 | 91 passed / 1 failed | GAP 기록 파일 없음. 기존 O_NOFOLLOW 경로의 호환 문제 미해결 |
| b54148d 기본 전체 suite | **10,657 passed / 41 failed / 19 deselected, exit1** | 2026-11-02 19:02 KST 종료. pytest1968.36초, 부모1972.414초. 전체 통과 아님 |
| 과거 PACK31 전체 suite | 10,061 passed / 38 failed / 19 deselected / 기존2 xfailed | 별도 과거 기준. fixture1개 개별 보수 후 gate37개가 남았으며 현재41개 분류와 혼동하지 않음 |

기존 기본 marker 제외 조건을 유지하고 프로젝트 밖의 basetemp 경로를 명시했다. 정확한 실행 argv는 원 receipts에 보존했다.

현재41개 실패는 8개 테스트 파일에서 발생했다. 공통 source 승인·고정 helper 소비·boundary egress 관련 실패가 포함되며, 각 원인의 상세 분류는 후속 작업이다. 원 명령·종료 코드·stdout/stderr·부모 시간은 로컬에 보존했다. 자동 hash 승인·gate 완화·새 skip/xfail로 통과시키지 않았다.

## 인계할 다음 작업

1. source/팩/세대별 원본과 로컬 보수를 유지하고, 완료한 전체 CPU 회귀41개 실패를 분류한다.
2. REQ-14 실제 함수 관측과 REQ-17 정상 시간 대조를 보완한다.
3. 준비된 단일 GPU 묶음에서 실제 factory/Kubernetes 종단 경로를 먼저 확인하고 확대한다. V1/V2와 Graph 활성 세대는 분리한다.
4. REQ-19·20·21·LIF-13의 선정 native 수집기를 구현한다. 일반 CUDA 예제·API200으로 대체하지 않는다.
5. 부분 실측8개의 남은 분기를 포함해 계속 추적한다. OBS-07은 수동 경로로 보존하며 HTML의 자동화39개와 기존 GPU 후보40개의 차이를 유지한다.
6. 필요한 TP/rank/peer 분기에 대해서는 최소 GPU 구성·현재 비용·정리 계획을 제시하고 추가 승인 후 진행한다. 지금까지 TP2·다중 노드 실증은 수행하지 않았다.

## 자원과 비용 경계

- 2026-11-02 18:39 KST에 전용 Pod·GPU 프로세스 종료와 NVML0MiB를 확인했다. 정상 종료 지연 후 기존 EC2 강제 중지를 마쳤으며 OS 종료 건너뛰기 옵션은 선택하지 않았다. 18:47 KST 기록에서 EC2 Stopped를 확인했다.
- 사용자 선택대로 EBS·전용 키·보안 그룹은 보관한다. EBS 저장 과금은 계속되며 최종 청구액은 확인하지 않았다.
- 실제 GPU 작업은 기존 누적USD20·최대8시간 승인 범위에서 진행했다. 이후 USD100·시간 제한 없는 프롬프트는 작성만 했고 이를 근거로 자원을 새로 기동하지 않았다.
- 현재 GPU/CPU 시험·자동 재기동·후속 실증 예약은 없다. 추가 GPU·자원·권한 확대·영구 삭제를 수행하지 않았다.
