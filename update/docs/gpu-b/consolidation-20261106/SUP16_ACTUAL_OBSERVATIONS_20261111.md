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

## R7: 실제 API 성공, 첫 capture 실패와 부분 회수

같은 기존 인스턴스의 새 기동에서도 실제 API preflight가 성공했다. 후속 normal-before capture 명령은 `GB49_CLI_FAILED:200-sup16-capture-normal-before`로 실패했다. 원 stderr는 helper의 `_private_runtime`이 output 디렉터리를 확인하다 `OWNED_SOURCE_DIRECTORY_REQUIRED`로 실패한 것을 보존한다. component factory와 첫 snapshot에는 도달하지 않았고, capture stdout의 collector transport는 존재하며 `reads=[]`, `capture_executed=false`다. 별도 RootClientError payload와 구분한다. collector의 일부 engine/producer false 필드를 VM·Pod·worker·모델 전체 상태로 확대하지 않는다. 실제 output UID·mode·ACL은 초기 원자료에 없어 어느 조건이 거부됐는지는 미확정이다.

초기 원자료25는 S4가 CID·신원·첫 오류·부분 진입 범위를 독립 대조했다. 66개 자료가 일치했으며 이는 실패 시험이 성공으로 바뀌었다는 뜻이 아니다. API 성공과 capture 실패, 원 outer의 빈 AssertionError 두 건을 각각 보존한다. whole stdout의 명시적 truncation과 작은 원자료 조회 당시 whole-return 부재도 남는다.

큰 GB08 archive 회수는 `ORIGINAL_TOTAL_DEADLINE_NO_NEW_RANGE`로 원 기한 안에 완료되지 못했다. 목적 파일은 원 helper가 전체 크기 7,521,242B를 미리 할당하므로 길이가 같아도 완료 증거가 아니다. 부분 목적 파일의 SHA는 원 archive SHA와 다르며 불완전한 opaque 자료로 보존했다. 이 파일을 완성 ZIP으로 열거나 내부 CRC·Ref 복원을 통과했다고 표시하지 않았다.

전송 actor와 실행 actor는 각각 실패 exit1로 회수됐고 process group 종료·cleanup 오류 없음이 확인됐다. 원 정상 stop 한 번 수락 뒤 180초 감시는 `STOPPED_NOT_YET_OBSERVED`로 실패했다. 후속 대상 한 대의 읽기 전용 조회에서 2026-11-11 03:35:38 KST 실제 stopped·공인 IP 없음·SSM node0·진행 command0을 확인했다. 정확 전원 종료시각과 새 비용은 null이다.

새 로컬 control ZIP은 9,133개 member, 1,241개 CLI 영수증, 511개 수락(start1/send509/stop1), 509개 command 종료(508 Success/1 Failed)를 포함한다. 원 요청·응답 SHA·CID·actor·정지 관측을 대조했다. 이 로컬 묶음의 무결성 검사를 GB08 전체 원자료 회수 성공으로 대체하지 않는다.

- R7 초기 원자료25 독립 검토: `d14265b8534cf7214cf2dd153e85e5dc70e81c17cb9389eefacb80beba9bf0a2`
- R7 로컬 control ZIP: `d6f5d498f07ca2a8c8b2245b6beb1467a6e8480457025940026c19ea83267fb2`
- R7 운영·종료 root 대조: `a3d69875cb71681acaa1229925cc32c21063a043a9b4f572d8359a3a82ff20e5`
- 부분 회수·원 오류 한정 후속 대조: `132b9937b0e06bb890666702e035e73408bd3516377050c0b81939b8b79e213f`

## 다음 실행과 범위

B 전용 private 제어·진단 경로를 일반 capture 출력과 분리하고, 해당 파일의 archive 보존 연결과 읽기 전용 조회 병목을 수정·검증 중이다. 기존 output·A 코드의 권한을 바꾸거나 source guard·기한을 완화하지 않는다. 새 모델·정상/도전/복구 세 역할·전체 반출 성공은 해당 실측 원자료로 별도 확인해야 한다.

원 runtime `2cb01e9f`/466파일과 공개 저장소 계열의 제품528파일을 구분한다. CPU 준비 요약의 actual=false는 작성 당시 범위로 보존한다.
