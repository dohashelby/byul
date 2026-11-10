# SUP-16 R2 실제 설치·bind 계약 실패·후속 정리 — 2026-11-10

R2는 데이터 ZIP 전체 전송 SHA와 고정41파일 설치 Success0, host health의 실제 L4 1장·기존 GPU process0, 원 timer reset=false, source2cb·system Python3.12.3 preflight·normal7개 core FD 검증/보조 metadata3개·현재 partial copy 준비까지 확인했다. 모델 Ready·GPU 모델 로드·worker·3역할·factory 완료가 아니다. 첫 normal-before bind가 명령파일 게시 전에 실패했고 제품 EVD/RES/RPT 저장 Ref는0이다. ENV EXAMPLE 입력은 원값이며 제품 결과 Ref로 환산하지 않는다.

## 원 실행과 별도 조회 시각

아래 시각은 2026-11-10 KST다. 감독/비용 앵커와 실제 AWS CLI 구간을 분리하며 원180초 관측이나 비용 시작점을 실제 CLI 시각으로 다시 설정하지 않는다.

| 시각(KST) | 구분·원 관측 |
| --- | --- |
| 16:27:47.416797 | 원 start intent·compute/IP 보수 시작 앵커 |
| 16:27:52.818665 | start CLI의 logical 앵커. 실제 CLI 구간과 별도 |
| 16:27:52.823443~16:27:54.657551 | 실제 start CLI·시작 수락 |
| 16:33:48.045647~16:33:49.047373 | first normal-before bind CLI exit1. 원 TypeError, reaped·PG terminal·cleanup 오류0. reserve/commands.json 게시 전 실패 |
| 16:37:52.264529 / 16:37:52.299613 | 원 normal stop 감독 intent / legacy response 앵커. 실제 stop CLI 시각 아님 |
| 16:37:55.364003 | stop CLI의 logical 앵커 |
| 16:37:55.368757~16:37:56.933030 | 실제 normal stop CLI1회·stopping 수락 |
| 16:40:59.365998 | 원 execute exit1·reaped·PG terminal·cleanup 오류0. stop180 실패·supervisor final 부재 보존 |
| 16:42:19.262678~16:42:20.390044 | 원 full8의 EC2 query: 선택 대상 stopping·공인IP1 |
| 16:42:21.491048~16:42:22.410298 | 원 full8의 SSM query: Online1. 명령0/EIP0/EBS440도 각 query의 별도 시각 |
| 16:46:34.883187~16:46:36.103514 | 후속 EC2-only: 소유3대 stopped·IP0. 새 STS/SSM/EIP/EBS 조회가 아님 |

정확한 power-off 전이 시각은 **null**이다. whole Failed1과 probe Success0, 원 명령/request·동일 boot·추적 자식 terminal의 연결로 선택한 main 반출을 검증했다. 설치/health 성공을 whole/role 성공으로 바꾸지 않으며 preserve/quiesce/archive 종료 관측도 원 실패를 취소하지 않는다.

## 배포·원 검증 범위

| 원 배포 자료 | bytes / SHA |
| --- | --- |
| 데이터 ZIP | 834439 / `b9198d12ccf1903fac9fd5625f13b1c6a8b8b4a22f8205932a51b0d92773a3e1` |
| 설치기 | 8949 / `2a07becad011b547ab26a58617146d04302524a3a82c6ca965c029986d979709` |
| 현재 입력 준비 wrapper | 20144 / `57071150084c405c3ae437bf32f90c166d0dd88578c6223224dcd6fb15bdac4a` |
| final operator freeze | 32221 / `ec9f81245b31bf477510fbd9c8e60305c090cbfefb08508e21431640b4531fb8` |

현재 preflight에서 확인한 제품 source는 `2cb01e9f08f9a123b8fffff00f9e798060115db9`·tree `912cb0924559343af939b5b525e865590a91795c`·466파일 manifest `ac423c8243af7b824bd66f7dadc5bd4ef5d827026a86d66f0c32dd28f3b4cf8b`다. PR 소프트웨어510/8ff·기준 head738의 CI14+2 workflow 성공은 별도 소프트웨어 기록이다. generated pycache와 deployment manifest는 runtime artifact로 분리하며 추가 고정 source pin이 아니다.

main은911713bytes·305members/SHA `ddf92de956fc841c87c3018c9b1ed2907785d52a6a0f24baa8fcb803d87aaa49`, control은6125204bytes·2151members/SHA `7cebf76791b3307fb2530eeeaef05a7212a6b4caed463978cff43d5ebdc3967f`다. S6 최초 CRC는 각각1회·오류/중복0이며 기존 cache를 재사용했다. 추가 CRC·기존 시험·DB 복원·재판정·AWS 반복은 하지 않았다.

S2 원 요약 `0f6332469c628188e7ac961b295e48d329a8938d8b603202eb83074a65fb8888`의 당시 pending과 원 main 검토의 stop/cost pending은 원 이력으로 남는다. S6 최종 `b202b988dd7e17f21f5a7072b476a2dc2c7167b8ba75e4ff56f1345a92dfe06f`의47대조·불일치0, main `1cfdd99e679cea8fa1344b42df08cc1192d87442571e3fe74dfe0242b8e7c1ea`의49대조·불일치0, CLI1124대조·불일치0으로 해당 actual 필수검증은 해소됐다. 원 제품 Ref가 생기지 않아 제품 복원/저장팩 재판정 대상은 없다. 이를 성공한 복원으로 표기하지 않는다.

ENV 입력은 `ENV-01M72616F7FCXFXDQFP0D71GCT@1`, data_origin=EXAMPLE이다. 입력 ENV의 engine.source_commit=null·UNVERIFIED도 그대로이며 host 제품 source 확인을 이 필드에 소급 대입하지 않는다.

## 실제 원인과 한정 후속 수정

결함 검토 `c3aac17c8e18105146d66d0a40b5bf65422c607a12b7d4efd8550909f790935c`가 실제 입력2026bytes/SHA `04b960155711fc03ac5590722b91294442bea30f0afb6fba1c1a3ea519b06d31`와 원 stderr를 연결했다. 6개 값은 dict이고 capture_helper만 str이었다. shared CONFIG(`cfaa394b…`)의 str 계약은 정상이며 B adapter003/5348가 resolver용 `{path,file_sha256}` 투영을 누락했다. resolver SHA `abae956aa5193c2d962d9b2b3d9b276723f5a0dbea5681514564b51056ee8d52`의362 보호 목록·316 capture 계약, 기존 bb9 capture pin·7보호입력은 그대로다. 원 `TypeError: string indices must be integers, not 'str'`와 실패 시도를 보존한다.

S5 adapter 후보는20573bytes/SHA `e0e96e2bb8d8656f35c39db6f51d158f6eab2a020e8dca730fc06343862a4917`, 필드1개 수정·CPU7통과다. S6 `d905cfddec8389fd75fc31ec062d61b25ce1d55e932c84b7694156c05d46c8ee`의47대조·불일치0/범위 내 새 결함0으로 **이 한정 수정 검토는 완료**됐다. 기존5348 byte-inverse·보호/예외/기한을 보존했지만 explicit inputs/role_result/read/reserve/write seams의 CPU 범위다. full OpenRole·MAKE_INPUTS·실제 FD/mount·reserve/write·worker/capture/writer/factory/GPU 양성으로 확대하지 않는다. partial/recovered 선언 override 부재의 S5 정적 sidecar10414171는 후속 S6 검토 `2a34256add8bda6ee08fae805d6a1c2b1366151fe3f2cd774e4499ad08de981c`의7대조·불일치0으로 한정 검토 완료됐다. 이 정적 검토로 새 CPU 실행·실제 미래 proof·실제 역할 실행을 추가하지 않는다.

## R2 종료시점 정리와 당시 다음 범위

R2 최종 EC2-only 조회 시각의 소유3대 stopped·IP0와 jobs0을 확인했다. 이는 R2 종료시점의 이력이며 후속 R3의 현재 상태가 아니다. 보수 누적 **$77.79040265062446**, 예비 $5 제외 기존 한도 잔여 **$17.209597349375542**는 비청구 추정이다. compute/IP 보수 구간은 후속 조회로 닫고 EBS440GiB 저장비는 계속된다. 앞 full8의 stopping/IP1·SSM Online1·명령0/EIP0/EBS440을 마지막 조회의 현재값으로 합치지 않는다.

R2 종료 당시 다음 범위는 검토된 S5 한정 수정의 새 pin-chain과 명시 frame/plan/roots·새 외부 attempt를 S2가 준비하는 것이었으며, 당시 R3 준비 중·기동0이었다. 이 준비 상태는 과거 기록으로 보존하며 후속 R3의 현재 상태로 사용하지 않는다. R2가 설치한 기존 guest R1 CODE/OPERATOR/RUNTIME/ASSETS/HEALTH·원 ZIP14는 보존하고 재사용·삭제·덮어쓰기·전역치환0, 과거 proof/미래 UID 복사0을 유지한다. 원 기한·승인 범위는 그대로이며 새 실행 gate를 추가하지 않는다.

LAB10/33·43행 전체·원 Ref/FAIL/UNKNOWN/EXAMPLE/DRAFT/official·현재 제품510/8ff·기존 GPU466/2cb·기존 assets는 변경하거나 승격하지 않는다. [R1 원 기록](SUP16_R1_TRANSPORT_CLOSEOUT_20261110.md), [순차 준비 이력](SUP16_SEQUENTIAL_PREPARATION_20261110.md), [현재 LAB 업무표](LAB43_CURRENT_20261110.md)는 각각의 고정 시점 근거로 보존한다.
