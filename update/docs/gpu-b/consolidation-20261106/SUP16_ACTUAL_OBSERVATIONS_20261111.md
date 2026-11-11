# SUP-16 실제 운영 관측 — 2026-11-11

이 기록은 S1 정리 시점까지의 CPU 준비 검증과 실제 시스템 관측을 구분한다. 후속 운영 상태는 [PR #240](https://github.com/dohashelby/byul/pull/240) 본문에서 시점별로 갱신한다. 모델·역할·제품 판정은 해당 원자료가 확인된 범위만 반영한다. 원 PACK33 DRAFT·LAB10/33·FAIL/UNKNOWN/정확 Ref를 변경하지 않는다.

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

## R8: 실제 준비 통과, 후보 연결 실패와 정리 완료

같은 기존 인스턴스에서 설치·health·실제 API preflight는 통과했다. whole는 2026-11-11 04:31:38 KST `SUP16_JOIN_ONE_ACTUAL_CANDIDATE_REQUIRED`로 Failed/1 종료했다. 완전히 회수한 challenge local-entry 원자료에서는 후보0, 기록 파일0, collection gap·seal transport 오류를 확인했다. 첫 root client 결과에는 capture 기한 초과·returncode -9가 기록됐다. 자식 회수·그룹 종료는 확인됐으나 이것을 모델·세 역할 성공으로 바꾸지 않는다.

engine Pod는 관측 당시 Running/Ready였고 installer 명령은 rc0이었다. launcher가 lifecycle 설정과 함께 vLLM을 exec하는 구조를 확인했다. 최초 launcher argv의 `-I -S`만으로 hook 미동작의 원인이라고 단정하지 않는다. journal 미생성의 구체 원인은 아직 미확정이며, 추가 installer 출력·실제 설정 등 원자료가 필요하다.

GB12 원 archive 14,636,464B/1,971 member의 전체 회수는 실패했다. 원 GET의 CLI 연결 시간 초과와 고정 파일 12,000B 읽기 SendCommand 한 건의 수락 UNKNOWN을 보존했다. 부분 구간에서 완전히 포함된 개별 local-entry만 bytes·SHA·CRC를 대조했다. 이 검증은 전체 ZIP/원 SHA/1,971 member CRC 성공이 아니다.

실행·전송 actor를 회수하고, 기존 승인된 자원 정리 범위에서 별도 감사 기록으로 정상 stop을 한 번 수락받았다(04:54:30 KST). 원 UNKNOWN과 자동 운영기의 barrier는 해소·수정하지 않았다. stop180 감시 실패 뒤 05:02:06 KST 실제 stopped·공인 IP 없음·SSM node0·활성 command0을 확인했다. 정확 전원 종료시각·새 비용은 null이며 운영 정리는 완료했다.

로컬 control 묶음 7,056 member와 별도 최종 정지 원자료를 대조했다. API 영수증 1,016개 중 정상 1,015/연결 시간 초과1, 수락379/UNKNOWN1, 수락된 SSM command377개의 종료(376 Success/1 Failed)를 확인했다. 이 수치는 운영 기록 수다. root의 자료 대조 9,244개와 후속 raw 연결15개는 불일치0이며 시험 통과 수로 합산하지 않는다.

- 운영·정지 root 대조: `76b5ba5be18fb81e40f494727efa34d75ddcf1b306ba8ce99f14ee8e5bda998d`
- 최종 정지 raw 연결 대조: `9cecbb21f09e686288fe8a138b98854f41562ff4be62e4013ad47e36a45c994d`
- challenge capture 개별 entry 독립 대조: `1561f2fc8aa2cae56e7cbd4ecd277a2ee86105e8288fa8cdcb693a10d9d3ce57`
- 실제 launcher/Pod/installer terminal 개별 entry 독립 대조: `19c17cf452239c055fefbaf406ebb14edb0506ca76225941e348c5801f72fc3e`

## R1: 보관 원본 회수 재개, 압축 상한 초과와 정리 완료

11월 11일 09:01 KST의 새 읽기 전용 조회에서 선정 인스턴스의 stopped·공인 IP 없음·SSM node0·진행 명령0·관련 CloudShell actor0을 확인했다. 원영수증 SHA는 `c2ce57eb37d65f9ff49cc587f54ecbe6c2f8b9c2504a0fe812406f50d1b40c4c`다. 이전 정지 관측을 현재 값으로 복사하지 않았다.

검토된 회수 source11은 그대로 재사용했다. 기존 `driver.start()`를 한 번만 연결하는 새 진입점 `0e7e79bfa7376ddad7bfe73dd795a2e414aec87574b0dac1f467455848412a4a`는 새 영향 모의 CPU 1계약·4사례와 독립 검토를 마쳤다. 후보와 별도인 최종 운영 packet은 `ab44a31f7b17c489dc0d1758bb1703305c073cb378cc12cbd5adbd54cab76115`다. 이 packet의 runtime_ready는 원자료 회수 진입 준비이며 실제 엔진 Ready 관측이 아니다.

09:16 KST 정상 start1이 수락됐고, running·SSM Online과 진행 명령 없음을 확인한 뒤 collect1을 실행했다. 실제 health CID `f5d0e68c-86a2-42c1-9acb-9b81dbef7ad9`는 Success/0이며 새 boot `542a2593-8afe-4d35-9f96-1b329248d104`, L4 1장, 이전 GPU 프로세스0, 기존 2시간 보호 타이머 active·reset=false를 연결했다. 모델·factory 실행은 이 health 범위에 포함되지 않는다.

encoder CID `c4d64e32-fef6-44dc-80e6-763265213f20`는 Failed/1로 종료했다. 실제 stderr는 고정 codec `6de3190f`의 encode 287행에서 `EnvelopeTooLarge: COMPRESSED_TRANSPORT_INPUT_CEILING`을 기록했고 stdout은 비어 있다. 기존 3MiB 상한을 늘리지 않았으며 trusted encoder receipt·archive GET·decode·전체 GB12 SHA/CRC 검증은 실행되지 않았다. 상위 프로그램의 원 오류는 빈 메시지의 `AssertionError`로 따로 보존한다.

정상 stop은 한 번 수행했다. 로컬 stop 기록의 기준 시각은 09:20:30 KST이고, 실제 CLI 요청·응답 구간은 09:20:33~34 KST다. 원180초 감시는 `STOPPED_NOT_YET_OBSERVED`로 실패했고, collect 자식은 09:23:37 KST에 rc1·회수·그룹 종료·cleanup 오류 없음으로 종료했다. 09:26:57~58 KST의 후속 선정 EC2 조회에서 stopped·IP 없음, 이어진 조회에서 SSM node0·진행 명령0·현재 private의 unresolved0을 확인했다. 읽기 전용 관측은 09:27:01 KST에 끝났다. 새 stop·Force·재시도는 없었다.

보수적인 compute/IP 장부 구간은 start 로컬 기록부터 마지막 읽기 전용 관측까지 632.443304초다. 정확 전원 종료시각과 새 비용은 null이며 실청구로 표시하지 않는다. EBS는 보존한다. 새 로컬 control ZIP 392,093B·381 member의 첫 CRC·index와 운영 원문을 독립 대조했지만, 이 ZIP은 미회수 GB12 원 archive가 아니다. 원 R8 UNKNOWN1·whole FAIL·1971 member 전체 CRC/Refs 미완료는 유지한다.

- R1 control ZIP: `072ea1e9c3824887199e3c3d23d4e5af31e515ea90d830a5bc518f15ecbbf57e`
- R1 실제 encoder terminal: `28791257e4b21d1ebc1b25bfe27b60b81a4d84f66c47f0a63ff1c4fd6d02a760`
- R1 후속 selected 정지 원문: `eafe42f2ce06a8aca01e2faddfd71b4142ffdac98c37baf32141d5d9b265e7b1`
- R1 실행·정지·비용 독립 대조: `f3ab591d1f7f1ef9f084c988cfd6ff71de1541a990cf7141db7fae91822cc401`

## Session S1: 설정 객체 결합 실패와 정리

기존 관리 문서 `AWS-StartNonInteractiveCommand`와 CloudShell의 설치된 plugin1.2.835.0을 확인한 뒤 별도 스트림 회수를 준비했다. 10:27 KST 선정 stopped·IP 없음·SSM0·활성 명령0을 새로 조회하고 고정 source15·운영 packet·진입 검사를 연결했다. 당시 진입 검사는 파일·모듈 결합 범위였다.

정상 start1의 실제 CLI 구간은 10:33:49~51 KST이며, 10:35 KST running·SSM Online을 확인했다. collect parent591→child592는 10:37:42 KST에 `RecoveryError: SAME_FIXED_DRIVER_PACKET_REQUIRED`로 즉시 rc1·회수·그룹 종료·cleanup 오류 없음으로 끝났다. 원 entry가 loader 외부에서 파싱한 packet을 원 Controller에 넘겨 `driver.PACKET is packet` 검사가 실패한 것이다. health·clock·Session·stream·모델 실행은 없었으며 세션 권한 거부가 관측된 사례도 아니다.

검토된 loader가 만든 동일 `runtime.packet`을 전달하도록 entry v7 `80dea4be043870b3e757826149a380c003c56879a0c739350a91d60a841b40bb`를 수정했다. 원 `is` 검사는 유지했다. 최종 준비 packet의 `check` 모드는 실제 원 Controller와 새 Recovery 생성자를 API 호출 없이 확인한다. 실제 고정 loader의 생성 결과를 소비하는 새 CPU3과 독립 검토를 연결했다. 첫 하네스는 빈 CLI 저널 디렉터리 생성까지 금지하는 잘못된 기대 때문에 exit1이었고, 요청 파일0/API0으로 기대를 정정해 같은 세 사례를 확인했다. 원 하네스 실패를 보존하며 세 사례를 두 번 가산하지 않는다. 원 core180e·stream8e·loader·Controller는 변경하지 않았다.

정상 stop1의 실제 CLI 요청·응답은 10:39:59~10:40:01 KST다. 180초 감시는 `STOPPED_NOT_YET_OBSERVED`로 실패했다. 후속 10:45 KST 관측은 stopping·IP 있음·SSM Online이었으며, 10:47:40 KST EC2 조회에서 stopped·IP 없음, 이어 SSM node0·활성 명령0·현재 private unresolved0을 확인했다. 읽기 전용 관측은 10:47:43 KST에 끝났다. 추가 stop·Force·타이머 reset은 없었다. 보수 장부는 start 로컬 기록 10:33:44부터 관측 끝까지 839.075131초이며 user 관리·새 비용 null·정확 off 시각 null·EBS 보존을 유지한다. 로컬 기록과 실제 API 요청·응답 시각을 구분한다.

새 control 337,653B·356 member의 첫 CRC와 index355·bytes/SHA, 실행·정지·장부 연결을 독립 대조했다. 이 자료는 전체 GB12 원 archive가 아니다. 원 S1 실패·정지 감시 실패·이전 UNKNOWN1·실제 제품 미실증을 유지한다.

- S1 후속 selected 정지 원문: `5e6eb1c04e4ce947ccf4b314e1c79bd57290e87fb677bdd09eb1f82f83c0cd65`
- S1 실제 운영·정리 검토: `55f56f09f08e01af1138ad225a165effe18c59cb64bc214627f5e00fc7ab996a`
- v7 실제 loader→원 생성자 CPU3: `b1a01a183c3e0eba5f8abecb25b7f6339d00531ea20ce2da99baa59fa9a2e81a`
- v7 최소 수정 독립 검토: `af35625cc2509466cc5ebf18547ba898ca8ae5dc074e5f3d12a0e65ec6f2c008`

## Session S2: CLI 입력 검사 실패와 최소 수정

v7의 실제 고정 loader→원 Controller 생성자 check/API0을 통과한 뒤, 11:03 KST 원 start actor1241의 terminal0/수락을 확인했다. 11:05 KST running·SSM Online1·활성 명령0을 확인하고 11:11:54 KST collect를 정확1회 제출했다. 실제 health·guest clock 이후 StartSession CLI는 11:12:09~10 KST native252·stdout0·stderr273B로 종료했다. `Parameters.stdoutSeparatorPrefix[0]`와 `stderrSeparatorPrefix[0]`의 빈 문자열이 min1 입력 제약을 위반한 `ParamValidation`이다. 권한 거부 또는 실제 Session 생성 성공으로 표시하지 않는다. 원 collect 자식1307은 11:12:12 KST rc1·회수·그룹 종료·cleanup 오류 없음으로 끝났다.

원 core는 CLI 호출 전에 session_submitted를 기록한다. 이 로컬 intent/UNKNOWN과 실제 서버 요청 여부를 구분한다. 원 UNKNOWN 파일을 삭제하지 않고, 원 request·native252·정확 오류·SessionId 없음·History/Active 빈 결과를 별도 정리 근거로 연결했다. 동일 collect/Session 재제출은 없었다. 기존 사용자 정리 권한의 정상 stop1의 로컬 기준은 11:15:37 KST이고 실제 CLI 요청·응답은 11:15:45~48 KST다. 원180초 감시는 11:18:49 KST 실패했다. 후속 EC2 조회 11:23:00 KST stopped·공인 IP 없음, 이어 SSM0·활성 명령0·현재 private unresolved0을 확인했고 읽기 전용 관측은 11:23:03 KST 끝났다. 보수 compute/IP 장부는 원 start 로컬 기록부터 관측 끝까지 1173.390235초/user 관리·새 USD null·정확 off 시각 null·EBS 보존이다. 추가 stop·Force는 없었다.

- S2 후속 선정 상태 원문 9103B: `34f6991e9045e0c7677b98c2ce02acb33c43dc440497ae478186eed9e478fae5`
- 원 v6 core는 `4cf0f085` 커밋의 원문으로 보존한다. v8 core 23600B: `ab804382e3f2a9d15bfeba8dbcaa5155f417b807fb468104ba6423088939681d`
- v8 entry 6679B의 변경은 CORE 핀뿐이다: `cf67a54e1c10669184c2231941530852dacf2aa16ad0c3c96b9863b5da7374f0`

v8은 request_for의 빈 optional prefix 두 키만 생략한다. 고정 관리 문서는 두 prefix의 기본값을 빈 문자열로 선언하며 separateOutputStream=false를 유지한다. 나머지 source AST·Transcript·원900/65·정리·UNKNOWN·동일 runtime.packet 보완은 불변이다. 실제 원 retained 요청의 command·nonce를 새 request_for에 공급하고 두 키만 뺀 expected와 동일한지 확인한 뒤, 같은 CloudShell의 실제 AWS CLI `--generate-cli-skeleton output --no-sign-request`에 전달했다. 11:19:55~56 KST 이 로컬 입력검사1회는 rc0·stderr0·자식 회수·그룹 종료·cleanup 오류 없음이었다. 출력95B는 생성 표본이며 서비스 응답이 아니다. API·plugin·GPU 기동·GB12 읽기/전송은 없었다.

- 실제 CLI 로컬 입력 검사 원영수증: `3d97c1663faf3994fcad763b86cf772e6c0dba34521ca9e48278f0027c8a26c1`
- 최소 source·실제 CLI 영수증 집중 검토: `5f1b0140219ae55a1c252128866ee5e923475056d56dd2b321b75355b3d19246`

원 S2 실패·각 UNKNOWN·정지 감시 실패와 제품 미실증을 보존한다. 새 control 473385B·482 member의 최초 CRC와 index481의 집합·bytes/SHA, 실제 health/clock의 원 wire·terminal, CLI 실패·별도 정리 근거·정지·해당 비용 행을 [독립 검토](retained-stream-components-20261111/session-S2-actual-review.json)에서 대조했다(31/불일치0, `afa63410ba8eeaf537c4b148804d399cb37550b699eb944c4248c12038ab5b07`). 실제 health는 새 boot `d3bcb559-45b2-42cd-9365-eab3df779704`와 L4 1장·이전 GPU 프로세스0·기존2시간 timer active/noreset을 관측했으며 engine/factory는 실행하지 않았다. 원 request·새 request·생성 stdout/stderr 바이트의 [후속 대조](retained-stream-components-20261111/prefix-v8-actual-raw-review-addendum.json)9/불일치0도 완료했다. 이 control 검증은 원 GB12 1971 member 전체 회수/CRC가 아니다. 후속 실제 회수는 새 private/final packet·현재 선정 상태·실제 생성자 사전검사를 결합한 별도 S3로 진행한다. 이 절의 시점은 S2 정리 및 v8 로컬 검사이며, S3 실행·전송 성공을 선행 집계하지 않는다.

## Session S3: 원자료 전체 회수·무결성 완료

v8 core `ab804382`와 entry `cf67a54e`, 원 stream `8e2261a0`의 source15와 새 private/final packet을 실제 사전검사로 결합했다. 기존 선정 인스턴스의 정상 start1이 수락됐고 11:32 KST running·SSM Online1·활성 명령0을 확인한 뒤 11:33:01 KST collect1을 제출했다. 실제 health 이후 원 Session CLI2252는 11:33:19.230594~47.705374 KST rc0·자식 회수·그룹 종료·cleanup 오류 없음·stdout19,631,430B/stderr0으로 끝났다. nonce·새 회수 boot·고정 sender SHA·원격 UID/EUID0·rc0 marker와 정확 SessionId의 Target/Owner/Reason/문서·Terminated/EndDate를 연결했다. receiver는 11:33:49.759793 KST 1,220 frame·14,636,464B를 게시했다.

원 ZIP을 로컬로 반출해 전체 SHA `2681a23c1545647e4b25711cb4c570336efdc6e7a604332de452c9cc2a663219` 일치를 확인했다. S4 단독 최초 전체 CRC1회에서 1,971 member·비압축131,826,305B·오류0을 확인했다. 위험 경로·중복/대소문자 충돌·symlink·암호화는 없었다. safeindex는 764,003B·SHA `5847c9e6938fc7f0f15b672bfe2a7502b918751e9ee2e980f91fdeeaf9379792`다. 전체 CRC는 반복하지 않았다. rawmanifest1970의 파일 집합·bytes/SHA와 정확 phase/ENV/target/Pod UID·설치 payload를 [원문 사실 검토](retained-stream-components-20261111/GB12-archive-facts-review.json)에서 확인했다(29/불일치0, SHA `7d1e43502ee70a397ca3cfc0d12128d9be4ea2e1929d002d440ad3f4ccdd222a`).

| 원 phase | 후보 | 기록 파일 | 봉인 | 원 observe 오류 |
| --- | ---: | ---: | --- | --- |
| normal-before | 1 | 3 | true | null |
| challenge-partial | 0 | 0 | false | SUP16_JOIN_ONE_ACTUAL_CANDIDATE_REQUIRED |
| normal-recovered | 1 | 3 | true | null |

첫 오류는 challenge-partial의 후보 부재와 연결된다. 세 phase 자료의 생성과 정상 전후 봉인 기록을 확인했으며, 원 ENV의 EXAMPLE·engine source UNVERIFIED·producer_authenticated=false·product_connected=false·security UNKNOWN·success_claimed=false를 보존한다. 정확 Ref의 kind/id/revision/content_sha 연결을 확인한 범위이며 Store 복원·POL 재판정·정식 제품 PASS는 수행하지 않았다. 부분 파일 단계의 실제 install 반환·첫 emit·Writer 원예외는 원문에서 아직 확정되지 않았다. 기존 lifecycle source의 지연 Writer 생성 계약과 함께 별도 인과분석 대상으로 둔다.

회수 성공 뒤 원180초 정지 감시는 `STOPPED_NOT_YET_OBSERVED`로 실패했다. 원 collect2178은 11:37:01 KST rc1·회수·그룹 종료·cleanup 오류 없음으로 끝났다. 원 Session 전송/native0·정확 서버 종료·원 ZIP 회수/CRC 성공과 정지 감시 실패를 각각 보존한다. 후속 11:40:53 KST EC2 조회에서 stopped·공인 IP 없음, 이어 SSM node0·활성 명령0·현재 private unresolved0을 확인했고 관측은 11:40:56 KST 끝났다. 정상 stop1·추가 stop/Force/reset0이며 보수 compute/IP 구간581.566939초/user 관리·새 USD null·정확 off 시각 null·EBS 보존이다. 다른2대의 새 상태를 이 선정 조회에서 추정하지 않는다.

- 선정 정지 원문9103B: `30825543086b59eefd40c1fde870ff602d8f10ac54371e27ac7aa898bb7d21d0`
- S3 운영 마감11271B: `8a5a05cfe3ad1368d9b70b1262fc0316870d75198e5073cb4f42faae946552f7`
- 별도 원 control477 member·13,989,782B: `b984b4db9525e20752353d09645627d80fd5386517f5171987379bebd652bd37`

새 control477의 최초CRC·index476과 실제 source15/packet, 원 stdout의 H1/D1220/F1·remote marker, 정확 Session 종료, receiver와 원 ZIP의 전체 SHA, 정지/장부를 [운영 독립 검토](retained-stream-components-20261111/session-S3-actual-review.json)에서 대조했다(31/불일치0, `3f134a9c1e71caf8d3ac5be36c8992b024d4ba5c48a357ba1a2a62d61792c3da`). 이 검토는 receiver/Session을 다시 실행하거나 전체 archive CRC를 반복하지 않고 원 bytes와 최초 검사 결과를 재사용했다.

원 R8 whole FAIL·읽기 요청 수락 UNKNOWN1과 과거 실패·partial을 보존한다. 이번에 해결한 것은 전체 원자료 회수·무결성이며, 제품 세 역할의 판정 완료로 확대하지 않는다.

## 기록 파일0의 해석과 다음 관측

실제 공급된 lifecycle base 원문 56,479B·SHA `2a05816b0fc2b83141409ae929187437e5afddfe1dbf5c6d099859fc57bb4ddd`를 대조했다. install 1032~1052행의 성공은 Observer/Finder 구성이다. Observer는 writer=None으로 시작하고(334~336행), 첫 관측 사건에서 Writer를 만든다(374~391행). Writer의 파일 생성·START 기록은 255~272행이며 emit의 쓰기 예외는 disabled_pid로 흡수된다(393~397행).

따라서 files/recordings0만으로 sitecustomize 미실행·install 실패를 확정할 수 없다. 설치 성공 후 대상 사건 미도달, Writer 생성·쓰기 실패도 구분해야 한다. 후행 SEALED 표시는 설치 시점에 marker가 이미 있었다는 증거가 아니다. 같은 PID/start_ticks/boot의 entrypoint·import origin·install 반환, hook·첫 emit, 파일 FD·원 예외, seal 순서를 최소 관측으로 연결해야 한다. 원인이 입증된 B 수정은 아직 없다. 로컬 분석의 EVD/RES/RPT 별칭은 제품 store의 공식 객체가 아니다.

## B 봉인 전 관측·입력 준비 마감

실제 R8 캡처 helper 19308B/7ed08793의 같은 reader120에서 snapshot 다음·seal 직전 기존 inventory의 원반환과 오류 type/message/errno를 기존 receipt에 보존하는 후보 f868ee1d/20279B를 만들었다. helper와 그 SHA가 전달되는 adapter/resolver 및 상위 코드 핀·설치 metadata만 갱신했다. 기존 A/worker·정책/후보 요구·기한은 바꾸지 않았다.

[고정 소스·직접 검증·최종 검토](retained-preseal-components-20261111/README.md)와 [A 반환 경계](retained-preseal-components-20261111/causal_handoff.md)를 보존했다. 관측CPU4·코드핀7·새metadata4는 각각 선정 범위이며 실제 원2cb 함수의 선택 AST·원native inventory와 명시적 Kubernetes 대역을 소비했다. 전체 main/CLI·43모듈 fanout·실제 GPU를 실행한 것으로 확대하지 않는다. 원 stdin 누락 및 입력 context 누락 하네스 실패를 보존하고 고정 생산 코드는 바꾸지 않았다. 최종 집중 검토16/불일치0을 확인했다. CPU의 성공 읽기35와 거부 전15를 합친50건·고유Ref35는 모두43plan에 연결됐고, 검토 보고의 집계 조건만 정정했다.

정상 전후의 원 PID98 journal은 각각1176행 중 WEIGHT_ITERATION1153/FILE_READ3 뒤 END.reason=BOUND_REACHED, root_operation_pairs_complete=false/root_exhausted=false였다. 후보1·봉인 성공을 root 연산 완전성으로 바꾸지 않는다. challenge의 install/첫emit/Writer 원인은 미확정이며 실제 engine PID/start/boot/import origin·install 반환/원예외·writer/SEALED 순서는 A 공통 반환이 필요하다.

현재 case용 새 Session 수집 binding은 원 driver의 과거 GET ConnectTimeout 경로를 자동 재사용하지 않기 위한 별도 로컬 준비다. 원 GB12 성공은 그 신규 경로의 검증을 대신하지 않는다. 마감 요청에 따라 새 GPU·추가 실측·staging은 시작하지 않고, 현재 고정 변경의 검증 범위와 미완료를 PR에 인계한다. 마지막 실제 자원 상태는 S3 선정 stopped/IP0/SSM0 확인이며 이후 추가 기동은 없다.

## 다음 실행과 범위

B 전용 private 제어·진단 경로와 읽기 전용 조회를 보강했고 R8·R1·S1·S2에서 실제 준비·실패 범위를 확인했다. 이 기록의 마지막 정리 관측은 S3의 11:40 KST다. 이미 확보한 원자료를 진단하고 [archive 압축·복원 준비](SUP16_ARCHIVE_ENVELOPE_PREPARATION_20261111.md)를 CPU에서 검증했지만 실제 GB12는 기존 압축 상한을 넘었다. 기존 Session Manager 관리 문서·설치된 플러그인과 v7 객체 결합·v8 CLI 선택값 수정의 실제 스트림 회수·종료·무결성은 S3에서 확인했다. 원자료 인과 대조와 B 관측 보강의 CPU 준비·한정 검토를 마감했다. 신규 case 수집 binding·새 실제 관측 및 A 반환은 잔여로 인계한다. 기존 output·A 코드의 권한을 바꾸거나 source guard·기한을 완화하지 않는다. 새 모델·정상/도전/복구 세 역할·전체 반출 성공은 해당 실측 원자료로 별도 확인해야 한다.

원 runtime `2cb01e9f`/466파일과 공개 저장소 계열의 제품528파일을 구분한다. CPU 준비 요약의 actual=false는 작성 당시 범위로 보존한다.
