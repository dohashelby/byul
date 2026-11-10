# SUP-16 R3 capture 실패·반출·정리 기록 초안 — 2026-11-10

**미확정 초안.** 총괄 세션의 후속 전달을 바탕으로 작성했다. S4가 원 ZIP·원 실행 자료를 새로 검증한 결과가 아니다. 아래 상태는 이번 전달 시점에 한정하며, 최종 종료·비용·정확한 capture 원인은 후속 S2/S6 자료로 갱신한다. 이 초안을 final·stopped·실제 GPU 전체 완료 기록으로 사용하지 않는다.

R3 설치와 host health는 성공했지만 whole은 Failed/exit 1이다. capture025에서는 상위 `ValueError` prefix만 확인됐으며 정확한 capture stderr와 근본 원인은 **UNKNOWN**이다. 큰 GB18의 원 GET은 900초 안에 전량 반출하지 못했다. 별도 control snapshot은 로컬 반출됐고 S6의 고정 검토 3864대조·불일치 0을 재사용한다. 이 검토 뒤에도 누락 요청 2건은 UNKNOWN이다. 이번 전달 시점의 첫 정상 stop 횟수는 0이며 비용 구간은 열려 있다. 정지 성공·최종 비용을 주장하지 않는다.

## 실제 관측과 검증 범위

| 항목 | 전달된 실제 관측 | 검증 범위와 한계 |
| --- | --- | --- |
| 설치·host health | R3 install·host health 성공 | 모델 Ready·세 역할·worker·factory·실제 GPU 전체 완주를 뜻하지 않는다. |
| 원 whole | Failed/exit 1 | capture025에서 상위 `ValueError` prefix만 확인됐다. 정확한 capture stderr는 UNKNOWN이며 원인을 단정하지 않는다. |
| 원 probe | 출력이 24000에서 절단됨 | 원 probe만으로 모든 자식의 종료를 확인했다고 쓰지 않는다. |
| 손실 없는 읽기 전용 probe | v3, Success/exit 0 | 원 wrapper·receipt에 연결해 실제 547개 자식의 전체 terminal을 확인한 범위다. rc 0은 544개, rc 1은 3개다. probe Success를 원 whole 또는 capture 성공으로 바꾸지 않는다. |
| 로컬 보존 wrapper | 46843 bytes / SHA prefix `30e2080b` | 원 wrapper 보존 관측이다. 이 문서에는 원 UUID·IID·계정·개인 경로·원 argv를 싣지 않는다. |
| 로컬 보존 receipt | 399 bytes / SHA prefix `dee780fd` | receipt 보존을 모델·역할·제품 실행 성공으로 승격하지 않는다. |
| S6 actual wire 검토 | 22661 bytes / SHA-256 `142cc858e42abaed73c590d5fcdbac534707314c4258494079547c1d4c0456f4`, 39대조·불일치 0 | 위 actual 연결과 547개 terminal의 검토 범위다. 원 capture 원인 해소·큰 ZIP 검증·정지 완료를 뜻하지 않으며 정지의 새 조건이 아니다. |
| S6 prestop control 검토 | `required-transport-api-review-v1.json`, 1840686 bytes / SHA-256 `9ab324eba695eadd82412c04c1bb0cfd44b5820a7635fde33db6c68f967e9a04`, 3864대조·불일치 0 | CLI 1026개·링크 3078개와 접수 445건의 정확 terminal을 대조했다. 444건 Success/exit 0과 원 whole Failed/exit 1을 보존한다. S6 최초 CRC는 5540 members·오류 0·추가 0이다. 이 고정 검토를 참조하며 S4는 CRC나 원자료 재파싱을 하지 않는다. |

문서에 기재한 짧은 SHA는 모두 prefix이며 완전 SHA-256으로 취급하지 않는다. 새 제품 Ref의 정확 목록·원 verification_level/data_origin/정책/official 값은 이번 전달에 포함되지 않았다. 원 FAIL·UNKNOWN·null 또는 기존 정확 Ref를 추정·변경·추가하지 않는다.

## 원 GB18과 로컬 부분 파일

| 자료 | 전달된 크기·식별 | 상태와 보존 범위 |
| --- | --- | --- |
| EBS의 완성 GB18 | 7313368 bytes / 1656 members / SHA-256 `a8f0ffd0b6325123500eb66cfd517e4c3432da2d498524cea6ced53335bbccd7` | EBS의 완성 원본을 보존한다. 원 archive receipt와 actual wire 검토에 연결한 선정값이다. 로컬 전량 반출 완료를 주장하지 않는다. |
| 원 GET helper | 원 기록 시각 `2026-11-10 09:07:38 UTC`(18:07:38 KST), 원 900초 | 기한 안에 전량 반출하지 못하고 종료됐으며 로컬 자식은 회수됐다. 새 전송 재시도·기한 확대를 수행한 기록이 아니다. |
| 로컬 preallocated partial | 7313368 bytes / SHA prefix `888f9951` | 사전 할당 크기가 원본 크기와 같아도 완성 원본이 아니다. 원 GB18로 사용하거나 CRC·의미 검증·제품 복원 대상으로 삼지 않는다. |
| 별도 원 control snapshot | 원 기록 시각 `2026-11-10 09:37:48 UTC`(18:37:48 KST), 13400105 bytes / 5540 members / SHA-256 `81ab6950630f1ca137a91d7827eeb1b853b5c924b7843bc9d22b10434fb00987` | 로컬 반출 완료와 S6 고정 검토 3864/0을 재사용한다. 검증 범위는 위 control 검토에 한정한다. GB18 원본 반출·정확 capture stderr·누락 요청 2건·정지·비용의 검증 완료로 확대하지 않는다. |

위 UTC는 S2 원 기록과 S6 control 검토의 시각을 따른다. 이 문서 작성 중 추가 원 ZIP CRC·추출·대량 파싱은 하지 않았다.

## 전송 접수 신원과 정리 상태

S6 control 검토에서 접수 445건의 정확 terminal을 확인했고, 마지막으로 알려진 접수 CID 2건도 양성 연결됐다. 이 집계는 actual wrapper의 자식 547개와 다른 모집단이며 합산하지 않는다. 반면 missing send 2건과 원 poll 2건의 결과 누락은 해소되지 않았다. 원 GET의 900초·-9와 별도 실행의 exit 1도 원 실패로 보존한다.

첫 missing RID는 개별 projected 조회 1회의 `[]`만 있고, 두 번째에는 별도 projected 조회가 없다. CloudTrail audit 7개와 SSM 7개의 대조에서 두 RID의 match가 0이라는 사실은 미수락·거부·종료의 증명이 아니다. 두 요청의 현재 판정은 **UNKNOWN**이다. 원 실제 UUID와 그 접두값·계정·IID·비공개 경로는 이 초안에 복제하지 않는다.

로컬 파일 tag, AWS Comment의 rid, RequestId, CommandId는 서로 다른 키다. S6 key-domain 후속 검토는 25대조·불일치0(SHA-256 `b8d67a166fd7e5154fa39359eb20cabd8f6746c64fd4bd3bf881ade6bab35a07`)이다. 원 Comment로 7개 intent와 감사·SSM 기록을 연결했고 키 혼동은 없었다. 누락 2건은 여전히 일치0이며 수락·종료 UNKNOWN을 유지한다. 감사 기록에서 가려진 parameters를 원문 대조 완료로 세지 않는다.

원 GET의 로컬 종료·자식 회수, 원격 전송 명령의 수락·종료 신원, EC2의 실제 정지는 서로 다른 관측이다. 이번 전달 시점에는 첫 정상 stop 0·비용 구간 open이며 최종 stopped·정확 power-off 시각·최종 비용은 미확정이다. 원 2시간 보호의 active 앵커는 `2026-11-10 08:24:57 UTC`(17:24:57 KST)이며 reset=false다. 예정 종료 시각 `10:24:57 UTC`(19:24:57 KST)는 실제 정지로 기록하지 않는다. 실제 전이는 후속 원자료로 확인한다.

S2의 정리·정지에 이 문서 검토나 추가 원자료 검증을 새 조건으로 붙이지 않는다. 새 GPU 기동·전송 재시도·Force·기한 확대는 없다.

## 확인된 문제와 해결 상태

| 경계 | 확인된 사실 / 원인 확정 수준 | 해결·준비 상태 | 현재 R3 적용·실행 검증 |
| --- | --- | --- | --- |
| capture025 | whole Failed/exit 1과 상위 `ValueError` prefix만 확인. 정확 stderr·근본 원인 UNKNOWN | 정확 stderr와 S5 원인표 수신 전에는 해결 완료를 주장하지 않는다. | capture 성공·실제 GPU 전체 완주는 확인되지 않았다. |
| 큰 archive 반출 | 원 helper의 900초 내 전량 반출 미완료. preallocated partial은 원본이 아님 | S5가 원 900초 deadline 전달·partial range 경계의 최소 후보와 direct CPU 구현을 진행 중이다. 소스·시험은 미완료다. | 현재 R3에 적용 0. 새 전송 성공 또는 재시도를 주장하지 않는다. |
| 진단 자료 선행 회수 | 큰 archive 반출 미완료와 정확 capture stderr 미확정이 남아 있음 | S5가 capsule 선행 회수의 최소 운영 경계 후보와 direct CPU 구현을 진행 중이다. 준비를 실제 회수 성공으로 계산하지 않는다. | 현재 R3에 적용 0. capsule 회수 검증 완료를 주장하지 않는다. |
| 접수 CID 보존 | 접수 445건 terminal과 마지막 known CID 2건의 양성 연결 확인. missing RID 2건·원 poll 2건 결과 누락은 미해소. no-match는 미수락·거부·종료의 증명이 아님 | S5가 accepted CID 즉시 원문 보존의 최소 후보와 direct CPU 구현을 진행 중이다. 소스·시험은 미완료다. | 현재 R3에 적용 0. control 검토 3864/0만으로 missing RID를 해결 처리하지 않는다. |
| 공개 소프트웨어 통합 | root가 선정 adapter `e0e96`·actionwise `74d3`·whole `fdc9` exact 3개를 공개 tools에 로컬 통합 | S6 exact-public-three 검토12대조·불일치0(SHA-256 `b3456218b0b92f78a424e90b1c3e1289fe4a3fc3fb98f1b48725c712a5bc45de`). 원 R3 선정3파일과 bytes·LF·SHA가 일치하며 제품 manifest510/8ff는 그대로다. 아직 push 전이며 신규 전송 보강 후보와 별도다. | 로컬 통합을 현재 R3 guest 적용·실제 GPU 성공으로 소급하지 않는다. |

이번 보강은 사용자 요청에 따른 B 운영 경계의 최소 수정이다. 생성기→소비 계약의 준비·직접 검증과 실제 오류 원인 해소·현재 GPU 적용 여부를 구분한다. 후속 코드·CPU 시험의 결과는 수신 전까지 완료로 바꾸지 않는다.

## 기준선과 후속 문서 갱신

현재 소프트웨어 510파일/manifest prefix `8ff`와 실제 GPU 466파일/source prefix `2cb`는 별도 기준선이다. 정확 source·리뷰·배포 적용 신원은 후속 고정 자료를 연결하며 한 기준선의 성공을 다른 기준선에 대입하지 않는다. 원 43행·정확 Refs·판정·LAB 완료 10/잔여 33·기존 source/assets·R1/R2 이력은 보존한다.

S4는 자기 `out/`의 이 초안 1개만 작성했다. root의 기존 6개 파일·통합 diff·중앙 현황·비용 장부·원자료는 수정하지 않았다. 새 코드·시험·CRC·DB·AWS 작업·배정·정지 조건은 추가하지 않았다.

S2/S6 최종 자료가 전달되면 이 초안에 정확 capture stderr와 원인, 전송 수락·종료 신원, 원본 반출/검증 범위, 실제 stop·후속 조회·비용을 각각 고정한다. 그때까지 불명확한 값은 UNKNOWN 또는 미확정으로 남기며, 최종 통합·PR push는 root가 담당한다.
