# SUP-16 R3 실패 원인·자원 정리·후속 수정 — 2026-11-10

**원 R3의 정지·비용 마감 검증은 완료했고, 후속 코드의 선정 CPU·독립 검토는 완료했고, 새 고정 운영 묶음의 결합은 별도로 진행한다.** 원 실행 실패와 전체 GB18 미반출을 보존한다. 아래 CPU·소스 검토를 수정 후 GPU 완주로 집계하지 않는다.

R3 설치와 host health는 성공했지만 whole은 Failed/exit 1이다. 앞서 상위 `ValueError` prefix만 확인됐던 capture025는 S6의 원 stderr·CLI 선별 완전 회수와 소스 대조로 **B 배치 계획의 필수 sibling 파일 경로 누락**이 원인임을 확인했다. resolver가 필수 파일을 `lstat`하는 단계에서 `FileNotFoundError`로 실패했으며 실제 capture 명령 실행 전이다. GPU 하드웨어 원인으로 단정하지 않는다. 큰 GB18의 원 GET은 900초 안에 전량 반출하지 못했다. 별도 control snapshot은 로컬 반출됐고 S6의 고정 검토 3864대조·불일치 0을 재사용한다. 이 검토 뒤에도 누락 요청 2건은 UNKNOWN이다. 수동 정상 stop 횟수는 0이다. 후속 실제 조회에서 소유3대 stopped·공인IP0을 확인했고 보수 비용 구간을 닫았다. 누락 요청2건의 수락 UNKNOWN과 물리적 정지는 구분한다.

## 실제 관측과 검증 범위

| 항목 | 전달된 실제 관측 | 검증 범위와 한계 |
| --- | --- | --- |
| 설치·host health | R3 install·host health 성공 | 모델 Ready·세 역할·worker·factory·실제 GPU 전체 완주를 뜻하지 않는다. |
| 원 whole | Failed/exit 1 | capture025의 상위 `ValueError` 뒤 원 stderr가 선별 회수됐다. resolver의 필수 sibling 파일 `lstat`에서 `FileNotFoundError`가 발생한 B 배치 경로 누락이며 실제 capture 명령 전 실패다. 원 실패를 성공으로 바꾸지 않는다. |
| 원 probe | 출력이 24000에서 절단됨 | 원 probe만으로 모든 자식의 종료를 확인했다고 쓰지 않는다. |
| 손실 없는 읽기 전용 probe | v3, Success/exit 0 | 원 wrapper·receipt에 연결해 실제 547개 자식의 전체 terminal을 확인한 범위다. rc 0은 544개, rc 1은 3개다. probe Success를 원 whole 또는 capture 성공으로 바꾸지 않는다. |
| 로컬 보존 wrapper | 46843 bytes / SHA prefix `30e2080b` | 원 wrapper 보존 관측이다. 이 문서에는 원 UUID·IID·계정·개인 경로·원 argv를 싣지 않는다. |
| 로컬 보존 receipt | 399 bytes / SHA prefix `dee780fd` | receipt 보존을 모델·역할·제품 실행 성공으로 승격하지 않는다. |
| S6 actual wire 검토 | 22661 bytes / SHA-256 `142cc858e42abaed73c590d5fcdbac534707314c4258494079547c1d4c0456f4`, 39대조·불일치 0 | 위 actual 연결과 547개 terminal의 검토 범위다. 원 capture 원인 해소·큰 ZIP 검증·정지 완료를 뜻하지 않으며 정지의 새 조건이 아니다. |
| S6 prestop control 검토 | `required-transport-api-review-v1.json`, 1840686 bytes / SHA-256 `9ab324eba695eadd82412c04c1bb0cfd44b5820a7635fde33db6c68f967e9a04`, 3864대조·불일치 0 | CLI 1026개·링크 3078개와 접수 445건의 정확 terminal을 대조했다. 444건 Success/exit 0과 원 whole Failed/exit 1을 보존한다. S6 최초 CRC는 5540 members·오류 0·추가 0이다. 이 고정 검토를 참조하며 S4는 CRC나 원자료 재파싱을 하지 않는다. |

문서에 기재한 짧은 SHA는 모두 prefix이며 완전 SHA-256으로 취급하지 않는다. 이번 진단 자료의 선별 회수로 생성한 제품 Ref는 0이다. 원 verification_level/data_origin/정책/official 값, 원 FAIL·UNKNOWN·null과 기존 정확 Ref를 추정·변경·추가하지 않는다.

## 당시 HTTP·Pod Ready 관측

기존 성공 전송 응답에서 원 stdout6개만 완전히 회수해 당시 **HTTP health probe ready=true/status=200**와 전후 **동일 Pod의 Ready=True**를 함께 확인했다. metadata.generation·observedGeneration은1, namespace·Pod·container/image·node·PodIP는 전후 동일, restartCount0·삭제표식 없음이다. 원 join의 Ready AND 조건에 맞는 관측이다. 준비 컨테이너 메타데이터는 vLLM0.31.0/Python3.12.3·native17 지원을 확인했으며 `engine_imported=false`, `GPU_execution_claimed=false`는 그대로다.

이6개에 필요한 기존 성공응답3chunk/36000bytes만 읽었고, 정확 source/range·header·완전 EOF/size·선택 CRC각1회·원 CLIstdout SHA를 연결했다. 기존 전체 index·검증은 재사용했다. node boot와의 직접 연결·engine 프로세스 직접 조사·실제 추론·GPU kernel·native worker/capture·3역할·factory·미래boot Ready는 이6개로 확인하지 않았다. [정확 bytes·SHA와 관측 범위](sup16-r3-ready-observation-20261110.json)는 실제 전체 GPU 완료 수와 별도다.

S6의 source/body/index/CLI 한정 검토는 54대조·불일치0, SHA-256 `0e576c6c6339f8a418a6e541cbfcd0829bd2aadc94366f0a63e1be748b780f13`이다. 원 요약의 `product_Refs_created=0`은 이번 선별 검토가 새 Ref를 생성하지 않았다는 뜻이다. 원 R3 전체의 Ref 존재 여부는 미검증이며, 전체 GB18을 확인한 결과로 읽지 않는다.

## 원 GB18과 로컬 부분 파일

| 자료 | 전달된 크기·식별 | 상태와 보존 범위 |
| --- | --- | --- |
| EBS의 완성 GB18 | 7313368 bytes / 1656 members / SHA-256 `a8f0ffd0b6325123500eb66cfd517e4c3432da2d498524cea6ced53335bbccd7` | EBS의 완성 원본을 보존한다. 원 archive receipt와 actual wire 검토에 연결한 선정값이다. 로컬 전량 반출 완료를 주장하지 않는다. |
| 원 GET helper | 원 기록 시각 `2026-11-10 09:07:38 UTC`(18:07:38 KST), 원 900초 | 기한 안에 전량 반출하지 못하고 종료됐으며 로컬 자식은 회수됐다. 새 전송 재시도·기한 확대를 수행한 기록이 아니다. |
| 로컬 preallocated partial | 7313368 bytes / SHA prefix `888f9951` | 사전 할당 크기가 원본 크기와 같아도 완성 원본이 아니다. 이 파일 전체의 CRC·의미 검증은 0이며 완성 GB18·제품 복원 대상으로 사용하지 않는다. 기존 양성 GET prefix에서 S6가 수행한 진단 member 2개의 선별 검증은 별도 범위다. |
| 별도 원 control snapshot | 원 기록 시각 `2026-11-10 09:37:48 UTC`(18:37:48 KST), 13400105 bytes / 5540 members / SHA-256 `81ab6950630f1ca137a91d7827eeb1b853b5c924b7843bc9d22b10434fb00987` | 로컬 반출 완료와 S6 고정 검토 3864/0을 재사용한다. 검증 범위는 위 control 검토에 한정한다. GB18 원본 전량 반출·누락 요청 2건·정지·비용의 검증 완료로 확대하지 않는다. 정확 capture stderr의 후속 선별 회수는 아래 별도 검토 근거에 따른다. |

위 UTC는 S2 원 기록과 S6 control 검토의 시각을 따른다. 이 문서 작성 중 추가 원 ZIP CRC·추출·대량 파싱은 하지 않았다.

## 정확 capture 원인과 선별 회수 근거

기존 양성 GET prefix `[0,4404000)`에 대해 S6가 bounded header 선형 관찰을 1회 수행하고, 지정된 진단 member 2개만 완전 EOF·크기·선별 CRC를 각각 1회 확인했다. 이는 full GB18의 로컬 반출 완료나 전체 CRC가 아니다. 두 member 이외 opaque 내용 읽기와 제품 Ref 생성은 0이다. S4는 고정 검토 결과만 참조하며 원 ZIP·prefix·stderr·CLI 원문을 새로 열거나 CRC하지 않았다.

| 고정 자료 | 크기 / SHA-256 | 확인 범위 |
| --- | --- | --- |
| S6 선별 완전 회수 report | 14378 bytes / `604c9d5b07f7338ceb630850f00f1e4f472ef5da3ed5c23db54765f15493ad47` | 원 stderr와 원 CLI member 2개의 완전 회수·크기·선별 CRC 범위다. GB18 전체 회수·opaque 읽기·제품 저장 검증으로 확대하지 않는다. |
| 원 stderr | 1326 bytes / `c174af97d02bfd597e8702e47a70827bcd1f737399bbc2be8089f90512a294f1` | resolver가 필수 sibling을 찾지 못한 `FileNotFoundError`를 확인했다. private PID·개인 경로·원 argv는 이 문서에 복제하지 않는다. |
| 원 CLI 기록 | 218354 bytes / `394a19806f600b6476a0a9013860ff038230d8b3acdbbe32732d726329294a2c` | 공개 가능한 원 실행 구간은 `2026-11-10 08:31:15.490111~08:31:16.492987 UTC`, exit 1·reaped/group terminal·cleanup `[]`·stdout 0이다. |
| S6 source·실제 stderr 연결 finding | 11235 bytes / `49cad4a60a696151b94a8477ba0ec13458e452f2ff91691067971304a61bb238` | 12대조·불일치 0. 실제 실패와 생성 계획·resolver·helper의 소비 계약을 연결했다. |

resolver 320행은 `capture.parent/source_owner_verify.py`의 일반 파일을 `lstat`하며 기존 `fb12` pin의 파일을 요구한다. capture helper 8~11행도 같은 sibling을 요구한다. 반면 원 배치 계획은 `ASSETS/static/assets/source_owner.py`에만 배치했고, 소비자가 요구하는 sibling row가 없었다. 따라서 확정된 원인은 **실제 생성 계획과 소비 파일 경로 계약의 불일치**다. 필요한 파일을 찾는 단계에서 실패했으므로 capture 명령 실행·GPU 하드웨어 장애·GPU 모델 처리 실패를 이 stderr만으로 주장하지 않는다.

S2의 새 producer 후보는 5283 bytes / SHA prefix `d250697c`이며, 동일한 3540 bytes·기존 `fb12` pin의 파일을 정확한 sibling 일반 파일로 배치하는 42-row 계획을 1회 생성했다. 원 41-row 계획은 보존했다. 생성한 계획 후보는 21423 bytes / SHA prefix `c21dd68a`이며 현재 **NOT_DEPLOYABLE**, 새 GPU 배치 0이다. 기존 41-row installer가 42-row 계획을 의도적으로 거부하는 동작을 새 결함으로 판정하지 않는다. S5 직접검증은 실제 생성42 소비3/0, 새 candidate ZIP→실제 설치기 함수→원 소비3/0이다. 실제 REG UID/GID1000·0600·nlink1을 확인한 native 증거와 CI의 휴대 가능한 fixture 범위는 구분한다. 새 원격 배치·운영세대/admission은 없다. 후보 생성과 소비 검증·배포·실제 GPU 성공은 각각 구분한다.

## 전송 접수 신원과 정리 상태

S6 control 검토에서 접수 445건의 정확 terminal을 확인했고, 마지막으로 알려진 접수 CID 2건도 양성 연결됐다. 이 집계는 actual wrapper의 자식 547개와 다른 모집단이며 합산하지 않는다. 반면 missing send 2건과 원 poll 2건의 결과 누락은 해소되지 않았다. 원 GET의 900초·-9와 별도 실행의 exit 1도 원 실패로 보존한다.

첫 missing RID는 개별 projected 조회 1회의 `[]`만 있고, 두 번째에는 별도 projected 조회가 없다. CloudTrail audit 7개와 SSM 7개의 대조에서 두 RID의 match가 0이라는 사실은 미수락·거부·종료의 증명이 아니다. 두 요청의 현재 판정은 **UNKNOWN**이다. 원 실제 UUID와 그 접두값·계정·IID·비공개 경로는 이 초안에 복제하지 않는다.

로컬 파일 tag, AWS Comment의 rid, RequestId, CommandId는 서로 다른 키다. S6 key-domain 후속 검토는 25대조·불일치0(SHA-256 `b8d67a166fd7e5154fa39359eb20cabd8f6746c64fd4bd3bf881ade6bab35a07`)이다. 원 Comment로 7개 intent와 감사·SSM 기록을 연결했고 키 혼동은 없었다. 누락 2건은 여전히 일치0이며 수락·종료 UNKNOWN을 유지한다. 감사 기록에서 가려진 parameters를 원문 대조 완료로 세지 않는다.

원 GET의 로컬 종료·자식 회수, 전송 요청의 수락·종료, EC2 정지는 서로 다른 관측이다. 사용자 설정 활성화 후 브라우저 제어를 복구했다. 원 보호의 active 앵커는 `2026-11-10 08:24:57 UTC`이며 reset=false다. 경계 후 EC2는 `Client.InstanceInitiatedShutdown` 사유의 stopping으로 관측됐고, 아래 후속 조회에서 실제 stopped를 확인했다. 특정 timer 서비스의 실행과 정확한 power-off 시각은 미관측/null이며 수동 정상 stop·Force는 각각0이다.

| 최종 관측·검증 | 실제 범위 |
| --- | --- |
| EC2 `10:33:20.847329~10:33:21.680887 UTC` | 동일 계정의 소유3대 stopped·공인IP0 |
| 뒤 SSM 조회, 마지막 종료 `10:33:23.619583 UTC` | managed node0·pending0·executing0, 각각 별도 조회 시각 |
| EIP·EBS | 앞 `10:22 UTC` full8 조회의 EIP0·EBS440GiB를 보존. 최종4조회에서 새로 조사한 값으로 합치지 않음 |
| 새 control delta | 새6개 항목 최초 CRC1회/오류0. 기존 base81ab 5540개 무결성·GET·원stderr 검증은 재사용 |
| S6 최종 검토 | 83대조·불일치0, 42120bytes / SHA-256 `4c96dabb9b2198fdae311ce7f8cfd4f5eb5efceabc293618ba838d848fa7f507` |
| 비용 | 시작 intent부터 마지막 조회의 보수 상한까지 닫은 누적 추정 **$80.53692960227087**, 예비$5 제외 기존한도 잔여 **$14.463070397729126**. 확정 청구가 아님 |
| 이후 보존비 | compute·공인IP 추정 rate0. EBS440GiB 보존비 `$0.055748/hour`는 계속됨 |

비용 마감은 원 whole Failed1·전송900초 timeout·기존 stop0·미확인 수락2건을 성공으로 바꾸지 않는다. 전체 GB18의 무결성과 제품 Ref/복원·재판정은 미검증이다.

S2의 정리·정지에 이 문서 검토나 추가 원자료 검증을 새 조건으로 붙이지 않는다. 새 GPU 기동·전송 재시도·Force·기한 확대는 없다.

## 확인된 문제와 해결 상태

| 경계 | 확인된 사실 / 원인 확정 수준 | 해결·준비 상태 | 현재 R3 적용·실행 검증 |
| --- | --- | --- | --- |
| capture025 | 원 stderr·CLI 완전 회수와 S6 finding 12/0으로 B 계획의 필수 sibling 경로 누락 확정. resolver의 `lstat`에서 `FileNotFoundError`, 실제 capture 명령 전 실패 | S2 producer 후보가 동일 3540-byte `fb12` 파일을 정확 sibling 일반 파일로 넣는 42-row 계획을 1회 생성. 원 41-row 보존. 실제 생성42 소비3/0·installer 소비3/0, 원41 누락 실패도 재현 | 계획 후보 NOT_DEPLOYABLE·새 GPU 배치 0. 원인 진단·선정 CPU 소비 검증은 완료. 통합 후보 검토·실제 GPU 재실행 성공과는 별도다. |
| 큰 archive 반출 | 원 helper의 900초 내 전량 반출 미완료. preallocated partial은 원본이 아님 | S5가 원 900초 deadline 전달·partial range 경계의 최소 후보와 direct CPU 구현을 진행 중이다. 선정 함수의 CPU·source 검토를 완료했다. 실제 운영 적용·대용량 전량 전송 성공은 별도다. | 현재 R3에 적용 0. 새 전송 성공 또는 재시도를 주장하지 않는다. |
| 진단 자료 선행 회수 | 큰 archive 전량 반출은 미완료지만 기존 양성 prefix에서 S6가 원 stderr·CLI 2 member를 선별 완전 회수해 capture 원인을 확인함 | S5의 capsule 함수와 실제 wrapper 호출 연결은 CPU 및 source 검토를 완료했다. 기존 자료의 선별 회수와 새 capsule 기능 검증을 구분한다. | 새 capsule 후보의 현재 R3 적용 0. 선별 회수 성공을 새 기능의 실행 성공으로 바꾸지 않는다. |
| 접수 CID 보존 | 접수 445건 terminal과 마지막 known CID 2건의 양성 연결 확인. missing RID 2건·원 poll 2건 결과 누락은 미해소. no-match는 미수락·거부·종료의 증명이 아님 | S5가 accepted CID 즉시 원문 보존의 최소 후보와 direct CPU 구현을 진행 중이다. 선정 함수의 CPU·source 검토를 완료했다. 실제 운영 적용·대용량 전량 전송 성공은 별도다. | 현재 R3에 적용 0. control 검토 3864/0만으로 missing RID를 해결 처리하지 않는다. |
| 공개 소프트웨어 통합 | root가 선정 adapter `e0e96`·actionwise `74d3`·whole `fdc9` exact 3개를 공개 tools에 로컬 통합 | S6 exact-public-three 검토12대조·불일치0(SHA-256 `b3456218b0b92f78a424e90b1c3e1289fe4a3fc3fb98f1b48725c712a5bc45de`). 원 R3 선정3파일과 bytes·LF·SHA가 일치한 당시 검토이며 당시 제품 manifest510/8ff 기준은 이력으로 보존한다. root가 문서·공개 코드 commit prefix `b53a5bc8` 이후 main prefix `7a985`와 병합 중인 후속 통합과 별개다. 신규 전송 보강 후보와도 구분한다. | 로컬 통합을 현재 R3 guest 적용·실제 GPU 성공으로 소급하지 않는다. |

이번 보강은 사용자 요청에 따른 B 운영 경계의 최소 수정이다. 생성기→소비 계약의 준비·직접 검증과 실제 오류 원인 해소·현재 GPU 적용 여부를 구분한다. 후속 코드·CPU 시험의 결과는 수신 전까지 완료로 바꾸지 않는다.

## 기준선과 후속 문서 갱신

새 PR 제품 목록은 **528파일 / manifest SHA-256 `6ba867aa1600ccd653c588387764c91196ec453904fa2e6ad2cffbab841b8e33`**다. main `7a985052`와의 목록·검토 이력 충돌 2개를 정리해 `46747623`에 병합했다. 기존 검토 6행을 보존하고 S6가 작성한 새 행 1개를 원문 그대로 통합했다. S6 검토30대조·불일치0의 SHA-256은 `1694bc40ef87da3df5bf86d5605d83f3468af6237d2559172a3fc684d3fe76c7`이며, 통합 ledger는 `0e6ddac33000e52d5e476541708e983e503f568c0b9fccd7010d1d0bac96cd1a`다. 원 hash gate는 528파일 검증/exit0이고 실제 CPU 실행은 false다. 기존 head `738e42d8`의 CI14 성공과 새 병합 head의 CI는 구분한다. 아직 새 변경 push 전이며 최종 head CI와 GPU 배포 완료를 주장하지 않는다.

기존 소프트웨어 510파일/manifest prefix `8ff`는 이력 기준이며 실제 GPU 466파일/source prefix `2cb`와도 별도로 보존한다. 새 528파일 목록·배치 계획 42 row·기존 41-row 계획은 서로 다른 수량이다. 정확 source·독립 검토·배포 적용 신원은 후속 고정 자료를 연결하며 한 기준선의 성공을 다른 기준선에 대입하지 않는다. 원 43행·정확 Refs·판정·LAB 완료 10/잔여 33·기존 source/assets·R1/R2 이력은 보존한다.

원자료 검증은 위 S6 고정 검토를 재사용한다. 이 문서 편집을 위해 원 ZIP 전체 검사·DB 복원·GPU 실험을 반복하지 않았다.

후속 수정의 generic CPU20/0과 실제 생성·설치 소비3/0·3/0은 서로 다른 범위다. 실험 ID26개로 합산하지 않는다. v2 private 후보의 observer attach 누락·driver 기한 정의/GET argv 누락은 v4의 실제 wrapper·driver 연결 CPU3/0·native root finally1/0으로 보수했고, S6 112대조/불일치0(`a10cd2a9e3cd7f4e60857801f3afb247836b45ac71fee37cb501698ee774b23e`)으로 확인했다. 원 실패와 fixture 준비 오류를 보존한다.

추가로 boundedqueue에서 동시 worker 실패의 최초 예외/traceback 선택 순서가 원51cc와 달라진 회귀를 발견했다. v5는 원 lock·최초(exception,traceback)·stopped 동작만 복원했고 결정적 동시 실패1CPU/실패0으로 확인했다. S6 코드/CPU75대조·불일치0(`b5eb0b2b27d66264f5b1fdf3f5f08bf07df187b308fc12a373f912329f9200a9`)·고정본 연결58대조/불일치0(`4fe05f0db4fa4081742c60eb9cdd239ba5a99e9d6ffd86bc8313264cdf967347`)이다. 최종 source-freeze는 `c6cf0ab62ebba83904ba92a78373016c88ec7cbafbdb135861e8bebb13f951c8`, 공개18개 파일과 private 운영 후보를 구분하며 원함수의 변경 없는 범위는 재사용했다.

공개 보강은 [실제 배치 producer](../../../tools/gpu_b_sup16_source_owner_layout.py), [실패 capsule](../../../tools/gpu_b_failure_capsule.py), [전송 계약](../../../tools/gpu_b_transfer_contract.py)이다. operator 경로·계정·실instance 설정을 담은 private 후보는 공개하지 않았다.

남은 것은 새 운영 묶음의 실제 source/입력/pin 결합·실측과 최종 PR CI다. 사용자가 이후 기존 선택GPU1대/TP1의 SUP16 정상→부분파일→복구를 즉시 재개하도록 명시 승인했고, 자원·예산 확인은 사용자가 맡는다. fresh 전체자원·비용 재계산·새예산 ACK·PR CI를 이번 기동의 선행조건으로 두지 않는다. 원2시간·단계기한·정확 대상/코드/입력·원자료 보존·소유 정리·정상stop·실stopped 확인은 유지한다. 원R3의 과거 proof를 새 boot/실측으로 복사하지 않는다. 원 수락2건 UNKNOWN·전체 GB18 미반출·제품 Ref 미검증도 그대로 남는다. SDK 내부의 외부 SIGKILL 전 미영속 pipe 응답은 helper 보강만으로 복구되지 않는 잔여다.
