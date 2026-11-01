# B native20 고정 전달물 수락

A `d8904d0` 위에 B 확정 `70431e9e6fc2243cfe596aa0dbf543d5cb1b8121`의 전용 5파일을 원 bytes로 소비했다. B 소스·시험·문서를 재작성하지 않았으며, A측 변경은 현재 조합을 소비하는 두 수락 시험과 이 기록이다.

검토 조합은 gate `61c29e98ece3c27c3fb978c180b86c6ef1a2ba6bf60edcf9263a2fc3943acab7` / common `adcde856375a4de43d4a5d789c00de875e43e321`이다. 고정 394파일의 manifest는 `0ee4b9da536307f9f5d68b55f3b938c591260da3386dc401d6a01a51a9ebc3c2`이고, B가 전달한 검토값과 전체 파일을 대조했다. B 함수 AST는 유지되고 명시적 새 조합만 추가됐다. 실행 중 읽은 해시를 승인값으로 사용하지 않았다.

B160 원8파일·PR171 fixture 원4파일·B70431 원5파일·과거 네 승인 조합은 Git 원본으로 계속 검증한다. 현재5파일 중 native fixture 한 파일은 B 확정 `3900e2ab832a9ec0a80391306033a00022d99d05`의 원 bytes로 소비하고 나머지4파일은70431 원본을 유지한다. 새 fixture SHA256은 `48fc8801b3ffeaf4e215e8ba130e5b1b0623b45b9ff71a26c76c33c0ada6742a`다. 공개 PR171의392파일 근거는 historicalsource로 보존하며, 그 과거 snapshot이 현재394를 거부하는 것도 검증한다. 미등록·혼합 조합은 reader/remote I/O 전에 거부한다.

직접 검증 대상은 A 수락·공개 HTTP 소비 두 시험이다. 정상 승인 조합의 수집/정제·원본/복원·저장팩 같은 as-of 재판정과 후기 수집/첨부 오류의 유효 관측 보존을 확인한다. B31사례·3제품/18Ref의 검토 근거는 전용5파일과 공통394의 정확 bytes가 같은 범위에서 재사용한다. 결과는 검증 receipt에 기록하며 이 문서 작성만으로 성공을 선언하지 않는다.

설치 wheel의 guard286파일은 이 통합으로 바뀌지 않는다. B 도구는 wheel 밖의 source-only 진입점이므로 설치된 guard를 현재 소스로 자동 승인하지 않는다. 기존64dd wheel/286파일 근거와 설치본 거부 경계를 구분한다.

공개 main `144798c09e0fb4d69208e01163598c0dd5735994`까지의 후속 platform/runtime 확정본을 원본 그대로 결합했다. `update/` 밖은 해당 main과 같으며 A가 소유 영역을 재작성하지 않았다. 실제 PLT 배포는 여전히 source920/wheelCF2의284파일 준비 기록이며, 이 후보로 소급하거나 대체하지 않는다.

이전 공개 후보 a3fa9ad의 정확 CI41292793496은 Python3.12.3에서10146통과/1 B 역사 fixture 실패/19제외, 다른4검사 성공이다. B의3900e2a는 승인된 HTTP common3개/gate5개와 남아 있던 시험 기대값2개/4개의 불일치를 보수한다. 제품 소스·gate·허용 조합은 그대로다. B의 POSIX 전체 native/HTTP/native20 3파일107통과 근거는 정확 전달 bytes 범위에서 이월한다. A는 원본 커밋·새 파일 hash·한 시험 함수만 바뀐 AST 범위를 검증한다. 수정 후보의 직접 검증과 정확 HEAD CI5·설치는 각 receipt로 별도 마감하며 이 문서 작성으로 성공을 선언하지 않는다.

원 c464e2a Windows 전체 시험은 Python3.12.14에서10068통과/65실패/2오류/기존skip13/선정제외19, source unchanged로 종료됐다. 원 로그는 보존하며 전체 Windows 통과로 표현하지 않는다. 소유자 fixture 검증의 Windows104통과/3실패도 symlink 권한·CP949 fixture 경계의 별도 근거다. 새 skip이나 source gate 완화는 하지 않는다. REQ13/OBS03/OBS06/SUP12 후속 통합 초안과 실제 운영기 보수는 별도 작업 공간·인계에 보존하고 이 통합 성과에 포함하지 않는다.

자료 출처 EXAMPLE/DECLARED/MEASURED, 독립 FAIL/UNKNOWN, native0.32 미지원, source/image/lab 경계와 전체77개·A39개 범위를 유지한다. 이번 source admission/시험 수를 새 점검 전체 완료로 집계하지 않는다. 과거 GPU 운영창 종료·실패·Stop 기록은 그대로 보존하며, 이후 사용자가 정한 잔여 적용 크레딧·비용/감시 보호 기준은 운영 인계의 별도 판본이다. 이 PR 작업에는 AWS 변경이 없다.
