# B-2 SUP-12 분기 기록

2026-10-28: 기존 PR47을 갱신·CPU 마감한 뒤 공유 기록의 B-1 SUP-10과 다른 SUP-12 decoder
분기를 선점했다. 원격 관련 PR을 확인했으며 이 decoder 기능의 기존 PR은 없었다.
기존 응답·로그 코드를 import하지 않으므로 main 기준에서 독립 검토·통합할 수 있다.

한 일: 공식 SERIAL encoder 시험의 한계와 실제 MsgpackDecoder를 대조했다.
실제 decoder/상수/bytestr 및 env getter를 고정 source bytes로 실행하는 도구와
원문 미저장 수집·명시 정책 관측 helper를 구현했다. 기존 rule_table의 미적용 제안은
독립 위반만 FAIL에 사용할 수 있고 전체 SUP-12 PASS를 생성하지 않는다.

검증: 계약·기존 rule_table 회귀 **12 passed / pytest 0.27s / 부모 0.537s**.
고정 실제 decoder **14사례 / 부모 0.028s**, 자체 참조 assertion·정책 미정 유지·원문 미포함 검사 통과.
native/pickle/cloudpickle/raw view/unknown extension/손상/잘림 × 환경 unset/enabled를 대조했다.
기본에서는 pickle/cloudpickle을 거부하고, enabled에서는 메모리 callback·pickle global 해석을 관측했다.
모든 사례에서 후속 정상 메시지가 처리됐으며 최종 aux_buffers 참조가 비었다.
wheel 빌드 및 whitespace 검사도 통과했다. 최종 HEAD CI는 PR과 외부 인계에 기록한다.

환경: 격리 Python 3.12.14, msgspec 0.19.0/cloudpickle 3.1.2/pyzmq 26.4.0.
제품 의존성·lock·기존 시스템 환경은 바꾸지 않았다. Torch/model/daemon/엔진을 기동하지 않았다.
기존 official 매핑 판본 5d35ce3과 이번 태그 판본 db9527a의 동일성을 가정하지 않는다.

막힌 것: 실제 설치판·선택 consumer/reader 신원·기업 허용 객체/금지 동작 정책·A 제품 연결.
추가 typed Tensor/모델 파일 reader·fallback·weights daemon peer 경로는 미검증이다.
다음: A 인계 시 실제 consumer 대응·정책 Ref를 고정하고 실행→EVD→RES/RPT를 연결한다.
외부 상세 자료는 `byul-b2-evidence-20261028/decoder-{snapshot,cpu,verification}-1.json`에 보존한다.
