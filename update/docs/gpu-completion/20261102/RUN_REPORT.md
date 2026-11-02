# PACK32 GPU 실증 묶음 기록

소스 b54148d, 병합 기준1174da6. 아래9행은 실행 묶음 수이며9개 점검 수가 아니다. 모든 POL은 기술 시험용 DRAFT/DECLARED, confirmed_by=null이다. 원 질문 전체 완료 수는0이며 이전6개와 신규 REQ16/18을 합친40후보 중8개가 부분 실측이다. REQ11 보조 시험은 이 수에 포함하지 않는다.

| 묶음 | 점검 | EVD | 직접 관측과 한계 |
|---|---|---|---|
| G27REQ14 | REQ-14 | DECLARED/ABORTED | 센서 파일/출력 귀속 수집 미완료. UNKNOWN 보존. |
| G28REQ14 | REQ-14 | DECLARED/ABORTED | 센서 파일/출력 귀속 수집 미완료. UNKNOWN 보존. |
| G28REQ16 | REQ-16 | MEASURED/COMPLETED | salt 문맥 분리·재사용. LoRA/멀티모달/모델세대 분기 미실측. |
| G28REQ17 | REQ-17 | DECLARED/ABORTED | 시간 표본0개(수정 전) |
| G28REQ18 | REQ-18 | MEASURED/COMPLETED | V1 슬롯 재사용·독립 출력 대조. V2·TP 분기 미실측. |
| G29REQ14 | REQ-14 | DECLARED/ABORTED | 센서 파일/출력 귀속 수집 미완료. UNKNOWN 보존. |
| G29REQ16 | REQ-16 | MEASURED/COMPLETED | salt 문맥 분리·재사용. LoRA/멀티모달/모델세대 분기 미실측. |
| G29REQ17 | REQ-17 | DECLARED/ABORTED | 4집단 각각6개. 정상 대조 판별력 없음. UNKNOWN 보존. |
| G29REQ18 | REQ-18 | MEASURED/COMPLETED | V1 슬롯 재사용·독립 출력 대조. V2·TP 분기 미실측. |

9묶음 모두 실제 registry factory의 kubernetes 구현으로 선정→실행→EVD→RES→RPT→원격 복원/저장 팩 재판정을 거쳤다. Windows에서도9묶음의 정확 Ref 재판정이 일치했다. 최종2032개 파일과 ZIP SHA b20d6c1a27a203a1331b63a8d9452e6954b426121989a94e8fe4aa6256ae9904를 검증했다. 상세 Ref·facts는 GPU_RESULTS.json, 원자료는 out/gpu-completion/G31-final-evidence에 있다.

제품 보수: Windows import의 POSIX signal 접근(0b6b644), 실제 Kubernetes stream 시계와 CPU elapsed 계약 소비(b54148d). async GPU 출력 확장 후보는 CPU 통과 후 실제 설치에서 거부돼 GPU 지원 완료로 보지 않는다. source·entry·policy gate와 고정 hash를 자동 변경하지 않았다.

검증: Linux3.12.3 준비279개/8.91초, Windows 신호 관련40개/2.74초, 최종 Linux 직접·인접213개/5.58초(부모6.1066초), Windows 신규 직접50개/10.34초. 별도 Windows 관련92개 실행은91통과/1실패이며 기존 O_NOFOLLOW 기록 경로에서 GAP 파일이 없었다. 이 실패를 제거하거나 skip하지 않았다. 기본 전체 suite는 b54148d Linux snapshot에서 2026-11-02 19:02 KST에 exit1로 종료했다. 10,657passed/41failed/19deselected, pytest1968.36초/부모1972.414초이며 전체 통과를 선언하지 않는다.

전원: 전용 namespace의 engine replicas0, probe 종료, GPU 프로세스 없음·NVML0MiB 확인 후 증거를 반출했다. EC209:40:22UTC 종료 예약,09:41:22UTC poweroff. 정상 종료가 중지 중 상태로 지연되어 기존 인스턴스 강제 중지를 수행했다(OS 종료 건너뛰기는 선택하지 않음). AWS 콘솔에서 중지됨을 최종 확인했고 G33-power-state.png를 보존했다. 기존 총USD20/최초8시간 상한은 재설정하지 않았고 추가GPU·자원·권한은 만들지 않았다. EBS·키·SG는 중지 보관 선택대로 유지한다.

다음 작업: METHOD_COMPARISON.md의 모드별 순서. 미실측·미구현·정책 미확정·다중GPU 필요를 분기별로 구분한다. 본 기록은 모든 GPU 시험 완료 보고가 아니다.
