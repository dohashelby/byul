# SUP-16 실제 운영 관측 — 2026-11-11

이 기록은 CPU 준비 검증과 실제 시스템 관측을 구분한다. 모델·역할·제품 판정은 해당 원자료가 확인된 범위만 반영한다. 원 PACK33 DRAFT·LAB10/33·FAIL/UNKNOWN/정확 Ref를 변경하지 않는다.

## R6: 실제 API 성공, 역할 실행 전 중단

새 B root client `e359cf357627e3f5c26e1300e489132be045b6ecffd0fa708162bc1d3604581b`를 기존 단일 GPU 환경에 배치했다. UID1000 helper에서 sudo root Python·PID namespace를 거쳐 선정 Kubernetes Namespace API 조회가 성공했고, 해당 요청의 자식 종료가 확인됐다. 이는 실제 권한·API 연결 검증이며 모델·worker·정상/도전/복구 역할의 성공은 아니다.

whole 실행은 `SUP16_ACTION_EXACT_PREPARATION_SOURCES_REQUIRED`로 역할 진입 전에 중단됐다. actionwise의 `ADAPTER_SHA`가 이전 adapter를 참조한 것이 실제 첫 오류였다. 후속 정적 대조에서 join의 `RESOLVER_SHA`도 이전 resolver를 참조함을 확인했으나 이 분기는 R6에서 실행되지 않았다. 원 outer 감독의 빈 `AssertionError`와 stop180 감시 실패는 각각 별도 원기록으로 보존한다.

actionwise의 adapter 지문과 join의 resolver 지문 두 곳, 이에 따른 전이 지문·다음 실행 경로를 보수했다. 두 파일의 다른 AST는 동일하다. 기존 역사 자료의 관계없는 이전 지문은 보존했다. S5 전체 소비 지문 대조를 재사용하고 S4가 수정·경로·staging 54개 자료를 독립 대조해 불일치0을 확인했다. 54는 시험 횟수나 제품 완료 개수가 아니다.

## R6: 종료·정리 확인

시작 actor는 정상 종료했고 실행 actor는 실패 exit1로 회수됐다. 두 actor의 자식 회수·process group 종료·cleanup 오류 없음이 기록됐다. 원 정상 stop은 한 번 수락됐으며 Force·추가 stop은 없었다. 원 stop180 감시 실패 뒤 별도 읽기 전용 조회에서 2026-11-11 02:45:02 KST의 실제 stopped·공인 IP 없음·SSM node0·진행 command0이 확인됐다. 정확한 전원 종료시각과 새 비용은 null이며 관측시각으로 대체하지 않는다.

현재 로컬 운영 묶음은 ZIP 1,426개 member이며 첫 CRC 검사 결과를 재사용했다. 275개 CLI 영수증의 요청·stdout·stderr SHA, 85개 수락(start1/send83/stop1), 83개 command 종료(82 Success/1 Failed)와 정확 CID·대상 연결을 대조했다. 이 수치는 운영 자료 수이며 SUP-16 시험 통과 수가 아니다. 전체 과거 GPU archive 회수 완료로 확대하지 않는다.

검토 원자료 지문:

- 초기 실제 원자료25 독립 검토: `087aa93eab4ee3acbcd9e4c9ef9e5176aadd7a879498512fec57ae5eb1151359`
- 현재 운영·종료 원자료 ZIP: `7c9002efc96f6f2a4a236cb5a573514f1c1e7f89d3a50378bff85e66fe407978`
- 운영·종료 root 대조: `e9d5af6e0d6b54b718a739b1b0a5c13d57425c3c27406b6d7f6e0215d7ebf380`
- 다음 실행 수정·경로·staging 독립 검토: `ddcf4b6b067a0e28fd5d7c383753583d56634a6ed4fdbdd481d79fd586d68d40`

## 다음 실행

R7 실제 모델·역할·반출·정리 결과는 새로운 원자료를 대조한 뒤 별도로 추가한다. R6 성공으로 대체하지 않는다. 원 runtime `2cb01e9f`/466파일과 공개 저장소 계열의 제품528파일도 구분한다. CPU 준비 요약의 actual=false는 작성 당시 범위로 보존한다.
