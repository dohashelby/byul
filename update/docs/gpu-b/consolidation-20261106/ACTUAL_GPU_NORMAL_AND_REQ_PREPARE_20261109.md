# GPU 1장: 실제 정상 응답과 REQ 준비 경계

V12 운영 세대의 source는 계속 b85이며 실제 CloudShell private/shared 경로 세대명은 **V11**이다. 이전 표시의 privateV12 오기는 원 기록을 수정하지 않고 후속 표시로 정정했다. 실제 결과·입력 준비·미측정을 아래처럼 구분한다.

## 확인한 실제 경로

| 단계 | 실제 결과(UTC) |
| --- | --- |
| source·모델 준비 | b85 현재 소스·origin·고정 모델 준비 통과 |
| engine Ready | 12:41:33Z 생성→12:43:10Z 전이, **97초**. 원90초 관측은 false·별도600초 진행 조건은 true |
| 현재 target 결합 | 수정 자산28b101의 실제018 명령 Success0·새 target 생성 |
| 직접 HTTP health | 12:43:16.211552Z, `/health` status200 |
| 정상 chat 요청 | 원 응답 outcome200·body681bytes |
| 정상 completion 요청 | 원 응답 outcome200·body539bytes |
| 정상 대조 종료 | 12:43:23.308507Z, `runtime_Ready_normal=true` |
| 선정 REQ 제품 입력 준비 | 원027에서 `REQ_INPUT_PRIVATE_0700_OWNED_DIRECTORY_REQUIRED`·exit1 |
| 선정 REQ factory·EVD/RES/RPT | 미실행·제품 객체 생성0. 따라서 제품 Ref 반출·복원·재판정 대상도 없음 |

정상 요청 전후의 같은 Pod/컨테이너 세대, 고정 source7·callback 자산 해시·읽기 전용·환경 flags를 확인했다. 실제 API pid1의 callback12개와 worker pid96의16개 관측도 보존했다. 이를 인증된 worker 신원이나 선정 REQ의 native ID 대응 완료로 승격하지 않는다. 정상 영수증의 `native_mapping_admission_verified=false`를 유지한다. 실제 생성한 EXAMPLE·UNVERIFIED 입력 ENV는 제품 객체 생성0과 구분한다.

## 남은 두 연결 경계

1. **준비 디렉터리:** 실제 UID1000·`-I -B` helper가 manifest를 읽은 뒤 `private_directory(prepared_root)`의 posix/디렉터리/0700/현재 EUID 조건에서 거부됐다. 원자료에는 해당 디렉터리의 실제 stat 숫자가 없으므로 0755 또는 root 소유라고 단정하지 않는다. 원 producer API·엄격한 consumer를 유지하고 새 실행의 생성 시 권한 설정을 보완하는 최소 연결을 별도로 검증한다. 과거 폴더를 chmod/chown해 수복하지 않는다.
2. **native 응답 ID:** 정상 원 응답은 `chatcmpl-a950387d06b5f78c`, `cmpl-a4a134d191b5d06f`처럼16hex였다. 고정 b85 B capture의 regex는32hex 전용이다. 고정 db952 소스의 기본 ID 생성 계약과 소비자를 대조한다. 현재 선정 REQ 호출은 없으므로 제품 FAIL이나 ID 대응 성공으로 집계하지 않는다. 임의 suffix 검색·가짜 ID·정책 변경으로 결과를 채우지 않는다.

## 정리·정지·비용

12:44:24.827940Z에 정확 namespace UID/RV 삭제·NotFound·GPU process0을 확인했다. 원 execute4669는12:49:35.155040Z에 exit1·reaped·group terminal·cleanup_errors=[]로 종료했다. stop은1회다. wrapper 시각12:46:28.926802Z와 실제 CLI intent12:46:32.065496Z/accepted12:46:33.410780Z를 구분하고 원180초 관측 실패·supervisor final 부재를 보존한다.

full 조회12:54:39.506993Z~12:54:40.475621Z에서 소유 서울 EC2 3대 stopped·공인IP0·활성SSM0·EIP0·EBS440GiB를 각 query 시각으로 확인했다. 정확 off 순간은 미확인이다. 보수적 서울 실행 장부 추정은 **$75.58237225241045**, $100 예산의 예비 $5를 제외한 잔여는 **$19.41762774758955**다. compute/IP 누적은 닫았으며 EBS 저장비는 계속된다. 확정 청구·크레딧 차감 결과가 아니다.

## 보존 자료와 독립 검토

세션2의 `out/one-gpu-req-sup16-join-20261109-v1/`에 보존했다. 공개 저장소에는 Secret·원 요청 본문·자격 증명을 추가하지 않는다.

| 자료 | bytes / SHA-256 |
| --- | --- |
| `actual-target-import-fixed-main-v12.zip` | 559301 / `3e21d1a190d46b152d828aba5327cc45c89cbc1f4048bc03ab44254270f6e5fd` |
| `actual-target-import-fixed-control-originals-v12.zip` | 2766847 / `be5d90af9861d4b03b4fb6162aa5de8cae418024e91fb004b6a8dbd5e4cce239` |
| 최종 required link | `9f16790e8aacea0925c3e403fa6a756e56fbac3d43d478cac8b1c71b943e7f46` |

main303개 member/302개 payload와 control1369개 member/1368개 payload의 첫 CRC·해시 대조를 통과했다. 독립 main 의미 대조923개·최종 정지/비용 대조210개·후속 required link11개에서 불일치0을 확인했다. start1·SSM65 CID·stop1 및 생산자와 GET49개(stat2/read47) 반출 연결을 보존했다. 시험 통과 수나 제품 PASS 수로 합산하지 않는다.

- main 의미 proof: `c9c0b7a97ffbc4dc86f61e6b3e3ce6914602181f4f05ff96d07b736cc16c52d8`.
- 최종 정지/비용 proof: `1f3776a615d6ec310a3f42b091d254a9b909798e218b8ea68e01cf9876f42db8`.

원 closeout의 pending=true와 옛 TARGET_IMPORT_FAILURE 표시는 불변으로 남기고 후속 required link에서 해소했다. 실제 이번 중단은027 준비 디렉터리 경계다. SUP-16 순차 역할 실측과 전체 paired factory도 아직 별도 미완료다.
