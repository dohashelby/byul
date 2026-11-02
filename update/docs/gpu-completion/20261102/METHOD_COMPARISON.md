# 기존 프롬프트와 gpu-handoff.html 비교

2026-11-02. 원 HTML은 수정하지 않았다. 비교 기준은 병합된 main1174da6/PACK32와 실제 AWS 준비·기존 PACK31 증거다.

기존 프롬프트의 증거·승인 규칙을 유지하고 HTML의 최신 구현 지도를 적용한다. 실행 순서는 체크리스트 번호보다 실제 기능·runner·GPU 구성과 공통 수집기에 맞춘다. GPU에 올리는 일과 GPU 내부의 질문에 답하는 일은 구분한다.

| 항목 | 기존 프롬프트 | HTML | 적용할 방법 |
|---|---|---|---|
| 기준선 | c33/PACK31와 과거 실행 상태 | main1174/PACK32 구현 지도 | 새 실측은 병합된 PACK32, 과거 PACK31 Ref·원증거는 그대로 보존 |
| 후보 수 | 40개에 OBS07 수동 경로 포함 | update 대상39개, OBS07 제외 | 39개 자동화 대상과 OBS07 수동 추적을 구분하며 임의 삭제하지 않음 |
| ENV/POL/target/prepared | 기존 자산 확인·재사용 요구 | 아무것도 없다고 기술 | 기존 자료가 있다. 모델 inventory·사례는 재사용하고 새 Pod UID·ENV/POL·팩 Ref는 새로 결합 |
| 코드 지원 | factory·프로파일·실제 도달 확인 | 조건부35개 코드는 있다고 설명 | 등록·생성·API 실행·센서 설치·실제 native 관측을 각각 확인. CPU 시험이나 연결이 실측 완료를 뜻하지 않음 |
| 다중 GPU | 분기별 최소 자원과 승인 | 선택한 TP/rank 분기10개 제시 | 점검 전체를 2GPU 필수로 일반화하지 않음. 단일 GPU 부분과 TP2·CUDA IPC·노드 간 NCCL 부분을 따로 추적 |
| GPU 필수4개 | 직접 kernel/Graph/IPC와 독립 대조 요구 | 수집기 미구현을 명시하나 순서 마지막 | 필요한 관측 지점을 초기에 확인하고 단일 GPU 실험과 함께 구현. 해당 소프트웨어 준비 전 추가 GPU를 유휴 가동하지 않음 |
| 공급물 서명 기록 | 기업 기준·확인자 임의 생성 금지 | HMAC 검증 경로 제공 | 서명은 기록의 인증/무결성을 확인하는 수단이다. 사실 생산과 GPU 최종 소비는 별도로 관측하고 자체 서명으로 기업 승인·실측을 만들지 않음 |
| GPU 메모리·회수 | 직접 관측 요구 | RSS와 요청 수만 관측하는 한계를 명시 | NVML PID/컨테이너/시작 세대와 allocator·KV 점유·요청 완료를 결합. 고정 KV pool·예약 메모리와 누적 잔존을 구분 |
| source hash | 자동 승인 금지 | --write 후 diff 검토 | 새 목록은 검토 후보로만 취급. 승인된 기대값·gate 변경을 생성과 분리 |
| 완료 보고 | 원 질문 전체 완료와 부분 실측 구분 | 점검별 실행 목록 | 분기별 준비/실행/관측완료/판정/재판정 단계를 집계. 원 질문 전체 완료 수는 별도 유지 |

## 실행 순서

1. **계측 준비부터 실제로 확인한다.** 실제 이미지 전체 digest·Python ABI·vLLM 소스·활성 runner, 센서 package import/설치, 기록 파일 쓰기·읽기, 새 Pod/GPU PID 귀속을 확인한다. 센서 불일치는 해당 수집에만 적용하고 다른 독립 사실·FAIL을 지우지 않는다. 준비 시험은 체크리스트 완료로 세지 않는다.
2. **1GPU 기본 경로를 끝까지 확인한다.** 실제 기본 runner의 eager 경로에서 정상→작은 도전→후속 정상→EVD/RES/RPT→반출/복원/저장 팩 재판정을 수행한다. V1 센서를 위해 V2를 끄면 별도 V1 세대라고 명시하며 V2 분기는 남겨 둔다.
3. **1GPU 기능별 묶음을 수행한다.** cache·slot, 부하·종료·회수, profiler·로그, supply·초기화·전환을 상태 독립성이 있는 범위에서 묶는다. Graph 활성 세대는 eager와 분리한다. LoRA·멀티모달·compile cache 등 실제 기능이 필요한 분기는 명시적으로 준비하고 각 원 질문의 직접 관측을 추가한다.
4. **핵심 native4개를 병행 보완한다.** REQ19는 실제 선정 kernel/dtype/shape/buffer와 독립 출력, 필요하면 memcheck/initcheck 등 오류 계측을 결합한다. REQ20은 실제 Graph 입력 갱신·출력 소비·완료·buffer 세대를 추적한다. LIF13은 실제 release 전후 기존 참조와 새 취득을 구분한다. 일반 matmul/Graph 예제를 제품 경로 증거로 대체하지 않는다.
5. **추가 승인 후 TP2를 수행한다.** rank를 마지막 PID로 추정하지 않고 rank↔PID/start↔GPU UUID↔Pod 세대를 결합해 지정 주입한다. 동일 호스트 TP/CUDA IPC와 노드 간 NCCL/Ray는 다른 구성이다. TP2 결과를 PP·PD 또는 다중 노드 전체로 확대하지 않는다.

REQ17은 공개·무해한 시험 prefix로 워밍업을 분리하고, 사례 순서를 교차/무작위화하며, 정상 대조의 판별력이 있는지 확인한다. 센서가 cache hit마다 수행하는 작업이 시간차를 만들거나 숨기는지 별도 대조한다. 실제 GPU 완료를 기다리는 진단 경로와 비동기 경쟁을 보존하는 경로를 분리하고, 측정을 위해 모든 요청을 강제 동기화해 원래 경쟁을 숨기지 않는다. 기업 통계·성능 기준이 없으면 DRAFT 시험과 공식 판정을 구분한다. 표본 수를 늘리거나 기준값을 바꾸어 원하는 판정을 만들지 않는다.

## 현재 준비에서 확인한 사례

- Linux3.12.3 새 main+기존 보수 직접/인접279개 통과(8.91s). Windows에서는 lab_activation의 POSIX signal 상수를 로딩하다 실패해 원격 Linux용 숫자로 보수했고 관련40개가 통과했다(2.74s). 전체 suite 통과를 뜻하지 않는다.
- Kubernetes Service 이름 vllm이 VLLM_PORT URI를 자동 주입해 MP worker 초기화가 실패했다. 원문 보존 후 전용 Pod에 enableServiceLinks=False를 적용한 새 세대가 Ready가 됐다.
- root/DAC capability 제한과 호스트 센서 자산의 owner1000/mode0700이 맞지 않아 센서 package와 기록 디렉터리에 접근하지 못했다. REQ14 최초3개 API 정상 결과와 DECLARED/ABORTED EVD·UNKNOWN RES를 보존했다. 전용 파일의 owner/group·모드를 맞춘 새 세대에서 센서 기록을 확인했다. 컨테이너 capability나 AWS 권한을 확대하지 않았다.
- 실제 factory/Kubernetes 전송으로 REQ17을 실행했을 때30요청은 성공했지만 시간 표본은0개였다. 기존 stream probe의 dispatch/end 시계와 CPU 전송의 elapsed_ms 계약이 달랐다. 기존 probe·고정 hash는 바꾸지 않고 소비부를 보수했다. 재시험에서는4집단 각각6개 표본을 얻었으나 정상 대조 AUC0으로 판별력을 확보하지 못해 UNKNOWN을 보존했다. 성능·cache 누출의 부재가 증명된 것은 아니다.
- REQ14 async 출력 확장 후보는 직접·인접 CPU 계약을 통과했으나 실제 GPU 함수의 파일 검증에서 거부됐다. source hash는 기존 고정값과 일치했으나 wrap preflight는 WORKER_STARTUP_SOURCE_UNAVAILABLE이었다. 파일 크기 등 artifact 경계의 추가 확인이 필요하며 현재 지원·실측 완료로 세지 않는다. gate나 hash를 자동 변경하지 않았다.
- REQ16 salt 문맥과 REQ18 V1 슬롯 재사용은 실제 GPU MEASURED/COMPLETED 관측을 얻고 센서 보수 후에도 재확인했다. DRAFT 프로파일 범위의 부분 실측이며, 원 질문의 모든 문맥·runner·다중 GPU 완료를 뜻하지 않는다.
- 실패를 포함한9개 실행 묶음을 로컬 반출하고 Windows3.12.14에서 원 저장 팩 재판정·EVD/RES/RPT 정확 Ref 일치를 확인했다. 최종 원자료2032개 파일 SHA를 검증했다.9개는 실행 묶음 수이며 서로 다른9개 점검 수가 아니다.

이 사례들은 준비·연결 성공과 실제 native 수집을 나눠 확인할 필요를 보여 준다. 현재 파일은 방법 비교이며 새 GPU 점검 완료 주장이나 source 승인이 아니다.

## 참고 자료

- [NVIDIA Compute Sanitizer](https://docs.nvidia.com/compute-sanitizer/ComputeSanitizer/index.html): memcheck는 범위/정렬, initcheck는 초기화 전 접근, racecheck는 shared-memory hazard, synccheck는 동기화 primitive 오류에 서로 다른 범위를 가진다. 도구의 무오류를 전체 수명·보안 판정으로 확대하지 않는다.
- [vLLM Parallelism and Scaling](https://docs.vllm.ai/en/latest/serving/parallelism_scaling/): 단일 노드와 다중 노드의 분산 경로가 다르다. 옵션의 실제 지원은 고정된0.31.0 이미지 소스·CLI를 기준으로 확인한다.
- [AWS GPU 인스턴스 표](https://docs.aws.amazon.com/batch/latest/userguide/gpu-jobs.html): 현재 g6.2xlarge는1GPU이며 g6.12xlarge는4GPU다. 물리2GPU 시험이라고 EC2도 반드시2GPU SKU인 것은 아니며, 생성 전에 구성·가격·한도를 다시 확인하고 승인받는다.
