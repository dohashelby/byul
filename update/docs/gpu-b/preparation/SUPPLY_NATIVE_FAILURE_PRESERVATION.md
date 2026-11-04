# SUP05 native 위반과 후속 보고 오류 보존

기존 PLT11-stderr/v1(c503b7c) 자료·도구를 보존한 채 같은 worktree에서 확인한 독립 수정이다. GPU 소유 `kubernetes_supply_boot.py`만 바꾸며 공통 contract/judge/registry/runtime/sensor/승인 manifest·기본 팩·기한은 바꾸지 않는다.

수정 전 새 계약은3실패/2통과였다. 첫째, 양쪽 boot-record가 없으면 실제 native 읽기 후보의 선언 기준 bytes 불일치를 계산하고도 violations/native observations를 미수집으로 바꿔 기존 FAIL을 UNKNOWN으로 지웠다. 둘째, 최종 reader.report 예외가 역할 수집 밖으로 전파돼 다른 역할과 앞선 사실을 버렸다.

수정은 native 수집이 실제 존재할 때 violations와 native_consumption 두 관측만 보존한다. 다른 관측의 기동 기록 요구·정상 대조·인증 false·complete 기준은 유지한다. reader.report 예외는 고정 진단과 정리 미확인으로 남기고 다음 역할도 수집한다. 예외 원문을 증거에 넣지 않는다. 두 기록과 native가 모두 없거나 위반이 없는 불완전 수집은 UNKNOWN이며 PASS를 만들지 않는다.

실제 native17 파일 읽기 센서/observer가 만든 사건을 기존 analyze→native_consumption→등록된 supply_boot:kubernetes factory→EVD/RES/RPT→bundle 복원→저장 팩 동일 RES 경로로 검증했다. Kubernetes 외부 I/O와 모델 객체·해석 경계는 EXAMPLE이다. 실제 vLLM/GPU 실행·기업 승인·reference authority·최종 tensor 적재를 입증하지 않는다. 고정 선언 reference와 실제 읽기 bytes의 차이를 기존 판정 계약이 보존하는지 시험한 것이며 원 질문 전체 완료가 아니다.

수정 후 새5개와 기존 supply/record-access 소비 계약65개, 총70개가27.77초에 통과했다. 원3실패/2통과 로그와 새JUnit은 out/supply-native/before-fix.log, after-contracts.log, after-contracts.xml에 보존했다. 검토용 source/patch·정확 Ref 근거는 공유 prepare/changes/supply-native-preservation/v1/을 참조한다.

역할별 native producer 원문 저장을 시험에1줄 추가한 뒤 직접5개를10.48초에 재확인했다(final-producer-contracts.log/xml). 제품 source는70개 검증 당시와 동일하므로 기존65개는 재사용했다. 새원문·prepared·bundle/저장팩 Ref는 각 supply-native-final 사례 경로에 남겼다. 원3실패를 후속 통과로 덮거나 판정 기준을 고치지 않았다.

공식 source/manifest 통합과 그 고정 통합본의 필수 전체·설치 검증은 남는다. 이 변경을 현재 PR217 fce9cef나 GPU 제품3bba/준비38f7에 소급 적용하지 않았다. 이전 PLT11 원FAIL과 원자료·판정도 불변이다. 공급물의 실제 boot-record 생산자와 정상/도전 세대·정책 입력이 없으므로 이 코드/CPU 결과를 새 GPU packet 준비 완료로 올리지 않는다.
