# A에 요청할 REQ-09·10 무ID 거부 관측 반환

현재 V13의 실제 요청·EVD/RES/RPT와 별도 DB 복원·저장 팩 재판정은 끝났다. 남은 것은 무ID HTTP400과 서버 내부 거부의 인과 연결이다. 같은 시험 반복이나 B의 후보 매핑으로 채우지 않는다.

현재 커밋된 A1~A3 반환은 `b4d93cdb566853049cdb36cf818c31176dcb850d`에서 확인했고 PR618c 기준에 포함돼 있다. 원격 확인 시 p1=8402ff89, k8s=4cecc819, main=3bd6a995였으며 미커밋 A 파일을 구현 기준으로 쓰지 않았다. S5 읽기 근거60d8/8627과 독립 검토 `1eee4c1364c6ce7255067d482ad46e26175341bc4b7fa17116a109dc7f901500`의85개 대조는 불일치0이다.

| 요청 | 담당 | 정확히 필요한 반환 |
| --- | --- | --- |
| REQ09 거부 인과 참조 | A 공통 관측·계약·projection / 엔진 adapter·provider | 같은 HTTP dispatch/response와 서버 거부/native call 사이의 실제 인과 참조, 원 source-qualified process/call/session/generation, 거부 stage와 관측 한계 |
| REQ10 거부 관측 범위 | A observer·provider·profile·공통 projection | temperature/top_p 400의 실제 거부 call과 원 예외/응답 연결. 정확한 원 V13 거부 단계를 확인하기 전 특정 handler 원인으로 단정하지 않음 |
| 새 /2 생산자 지원을 선택할 때의 기존 자격 | A 등록·release 담당 | 정확 commit·source SHA·schema·fixture·projection과 유효 qualification 범위, 또는 미지원 gap. 기존 legacy/1은 원등록 지원을 유지 |

REQ09에는 native api_rejected 한 행이 있지만 HTTP400에 응답 ID가 없어 그 행을 해당 사례에 붙일 독립 근거가 없다. REQ10의 원 선택 관측에는 native api_rejected가 없다. A3의 typed_rejected CPU EXAMPLE은 별도 세션이며 V13 관측으로 옮기지 않는다. /2 decoder가 있다는 사실과 새 생산자의 자격은 다르다. 현재 qualification receipt0을 기존 legacy/1 미지원으로 확대하지 않는다.

## 바로 보낼 요청 문구

> A 공통 관측 담당께: PR240의 V13 REQ-09·10 원자료에서는 ID 없는 HTTP400이 보존돼 있으나 서버 거부와 사례의 인과 연결이 남아 있습니다. REQ09은 기존 native 거부 행과 같은 HTTP dispatch/response의 원참조, REQ10은 실제 거부 call·원예외/응답·stage 관측을 반환해 주세요. commit·파일/source SHA·schema·fixture·projection·지원/미지원 범위를 함께 부탁드립니다. ID를 새로 만들거나 남은 한 행·시각·행 수로 매핑하지 않으며 worker 도달을 추정하지 않습니다. 새 /2 생산자를 선택해야 한다면 해당 source의 기존 qualification 범위도 함께 알려주세요. 원 V13 FAIL/UNKNOWN·ABORTED와 정확 Ref는 그대로 유지합니다.

[요청별 기계 판독 원목록](a-req-noid-return-requests-20261110.json)을 연결한다. 이 요청은 해당 관측 공백에 한정하며 다른 B 개발·LAB 업무, 기존 수행 완료나 TP2 준비의 전역 차단 조건이 아니다. 이 문서와 요청 문구를 작성했으며 별도 A 이슈 생성·담당자 메시지 발송은 하지 않았다.
