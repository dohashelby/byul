# GPU1 선정 REQ 실제 실행 V13-r4

현재 실행은 REQ-08·09·10의 선정 사례를 실제 L4 1장·TP1 LAB 시스템에 연결한 결과다. 원 43개 전체의 완료 또는 모든 분기의 PASS를 뜻하지 않는다. 기존 EXAMPLE 정책, 외부 파일 소비자와 신원 인증 한계는 유지한다.

## 실제 연결과 결과

기존 인스턴스의 새 boot에서 source V4 `2cb01e9f08f9a123b8fffff00f9e798060115db9`·tree `912cb0924559343af939b5b525e865590a91795c`·tracked 2,163파일·466파일 manifest `ac423c8243af7b824bd66f7dadc5bd4ef5d827026a86d66f0c32dd28f3b4cf8b`를 사용했다. PR의 최신 main 통합 509파일 manifest와 별도 실행 기준선이다. 같은 runtime B 파일 bytes는 `2dec5d3d7ebc506c43e016ba9e1d3270ad42406f0de2974892c3d54ac8f85185`이다.

실제 엔진 Ready는 생성 후 97초였다. 원 90초 성능 조건은 충족하지 못했고, 별도 Pod 600초 허용 범위 안에서 Ready·직접 health·정상 대조 뒤 선정 요청을 수행했다. 실제 이미지 `vllm/utils/__init__.py` 1,609 bytes의 SHA `c58b3f45deeb98bccca22c945526fd15d483a2f5eb8a3c62cf7df218b081885f`와 generator AST hash `0ac1a70b4d0b8f03524306e0a91a8d1826ebeeedcadbb682f45343e797232f92`를 읽었다. source-text hash와 AST hash는 서로 다른 의미다. callback 준비 디렉터리·원 준비 helper가 통과해 선정 제품 수집까지 연결됐다.

| 항목 | 실제 HTTP 관측 | 원 EVD 상태 | 원 RES 기능 / 보안 |
| --- | --- | --- | --- |
| REQ-08 | 선정 3건 모두 HTTP200·응답 ID와 API 기록 대응 3건 | COMPLETED | PASS / PASS |
| REQ-09 | 5건 중 HTTP200 4건·HTTP400 1건; token_id 거부 응답에는 native ID 없음 | ABORTED | UNKNOWN / FAIL |
| REQ-10 | 9건 중 HTTP200 7건·HTTP400 2건; temperature·top_p 거부 응답에는 native ID 없음 | ABORTED | UNKNOWN / UNKNOWN |

HTTP400 응답에 없는 native ID를 만들거나 주변 callback을 후보 탐색해 대응하지 않았다. REQ-08의 `request_id_join_complete=true`도 worker 인증 또는 토큰·tensor 동일성 증명이 아니다. 세 항목의 `native_request_mapping_verified=false`, `producer_authenticated=false`, `runtime_link_complete=false`와 원 EXAMPLE 값은 유지한다. 거부 요청의 관측, 수집 프로그램 종료 성공, 제품 판정 PASS를 구분한다. REQ-09의 보안 FAIL은 원 판정기의 독립 `runner_input_range_exceeded:length` 기록에서 유지됐다. ID 없는 HTTP400만을 보안 FAIL 근거로 제시하지 않는다. 기능 PASS는 수집 ABORTED로 보류됐고, REQ-10도 실제 소비 검증이 부족해 UNKNOWN이다. 세 항목의 성능 축은 UNKNOWN이며 REQ-09·10의 verification_level은 null이다.

실제 EVD 3개·RES 3개와 공유 RPT 1개가 생성됐다. 원 receipt의 정확 Ref 복원과 저장 팩 재판정 일치는 true다. 독립 검토자는 새 별도 DB에 정확 Ref 10개를 복원하고 PACK revision33의 원 시각으로 재판정했다. 264개 대조에서 불일치 0건이며 공유 RPT 의미도 일치했다. 검토 SHA는 `e4c948b79f51347f77b148f3759053f101ac696765abfb1f94399cc9d39d651b`이다. 새 GPU·모델·HTTP·factory 실행은 없고 새 RPT도 저장하지 않았다. 안전 종료의 원 control 검토는 아래 별도 근거로 확인했다. RPT는 official=false이며 실제 실행을 했다는 이유로 공식 결과로 상향하지 않는다. 공개 요약 JSON은 정확 Ref·원 결과·진단만 포함하고 원 요청 body·HMAC 비밀·개인 경로는 게시하지 않는다.

독립 실제 의미 검토는 702개 대조에서 불일치 0건이다. 최종 검토 SHA는 `60260c35fbe6f13fa6301ee368e8aca366a2adf2cd57cfc8bc5866712d5fcec3`이다. 최초 검토의 두 불일치는 선행 capture projection과 후속 factory 결과가 완전히 같아야 한다는 검토 가정 오류였다. 원 factory가 ID 없는 400 응답에 `API_RESULT_MISMATCH` 진단을 추가함을 소스로 확인해 후속 검토에서 구분했으며, 최초 기록·실제 제품·정확 Ref·FAIL/UNKNOWN은 바꾸지 않았다.

## 반출과 종료

원 main ZIP `actual-native-id-private-root-main-v13.zip`은 3,613,976 bytes·1,054 members이며 SHA256은 `6fd222f7bdfc87dd84d6cd75d68903a9af1b10a2a515eaf37aedeb710ab90492`이다. 원 GET 900초 내에 로컬 회수했다. namespace는 원 UID/RV로 삭제하고 NotFound·GPU 프로세스 0을 확인했다. 원 stop1 뒤 180초 안의 정지 관측은 실패해 그대로 보존했다. 후속 2026-11-09 14:45:25.788320~26.105363 UTC 조회에서 소유 EC2 3대 모두 stopped·공인 IP0을 확인했다. 원 14:44 full 조회의 stopping과 이 후속 stopped 시각을 합치지 않는다. 당시 UI 근거 서울 누적 장부 마감 추정은 $76.24573072675766이며 예비 $5를 제외한 잔여는 $18.75426927324234이다. 원 control ZIP 13,516,800 bytes·4,559 members/SHA `4de2bdab4e29c0c8fe7bd7bd705e1dfd3ad9105a4a6af85c0e6a873a964b2f1f`도 로그인 복구 후 읽기 전용 6조각으로 반출·정확 재조립했다. 독립 최종 운영·비용 검토200개 대조에서 불일치0을 확인했다. 검토 SHA는 `6734bd958d9b4288b1d07b2ff1b6c3b99bac5c9399ec727e58ef62be1e310435`이다. 원 start1·stop1·SSM347개와 미종료명령0, source설치·운영terminal을 대조했다. 원 실행은 stop180 미관측으로 exit1/reaped/PGterminal/cleanup오류0이며 이를 성공 exit0으로 바꾸지 않는다. 정확 전원 종료 시각은 알 수 없다. 당시 UI 마감의 pending 원기록은 보존하고 회수된 원자료와의 일치를 후속 기록으로 해소했다. 금액·asof·비용delta0을 확인했다. 비용은 서울 보수 장부 추정이며 확정 청구가 아니고 EBS 저장비는 계속된다.

## SUP-16 및 후속 범위

이 REQ 실행에서 SUP-16을 실측하지 않았다. 순차 normal-before→challenge-partial→normal-recovered 준비는 outer caller v2까지 CPU 8개 경계 검증을 끝냈지만, 실제 CONFIG·현재 boot/source/model proof·원 phase 감독기·독립 aggregate archive producer 연결과 실제 역할 관측은 별도로 남는다. 이 준비를 GPU 실측 또는 전체 paired factory 완료로 계산하지 않는다.

현재 건은 원 오류와 UNKNOWN을 보존해 검토·인계한다. REQ-09·10의 거부 응답 대응과 실제 인증/전체 정책 범위, SUP-16 실제 역할 실행, 두 live target/prepared의 전체 pair, TP2 분기와 과거 원자료 확보는 각각 다른 남은 작업이다. 같은 입력을 반복 실행하거나 43개 상태를 자동 상향하지 않는다.
