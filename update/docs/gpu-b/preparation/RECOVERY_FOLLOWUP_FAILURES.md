# LIF-11 후속 보고의 독립 위반 보존

65f3388 위의 분리 후보로 resource_recovery 실행기만 추가 수정했다. 고정 GPU 제품3bba와 termination-attempts/v1 준비38f7에는 적용하지 않는다.

기존 collector는 선정 자원의 정책 절대 기한 초과를 계산한 뒤 reader 또는 HTTP report가 예외를 내면 빈 ABORTED 경계로 빠져 앞선 독립 기능 FAIL을 UNKNOWN으로 바꿨다. 수집 gap만 있는 경우 이유가 null이기도 했다. 이를 runtime-failure와 partial-initialization 두 기존 분기의 EXAMPLE 계약으로 확인했다.

수정은 최종 report 예외를 고정 진단·불완전 수집/정리로 보존하고 이미 얻은 census/project 관측을 유지한다. 기존 gap의 ABORTED 이유가 없을 때만 고정 진단을 전달하며 기존 이유는 보존한다. 원 예외 문자열은 저장하지 않는다. source/owner/clock·policy 기준·관측·판정·기한·센서·기본 팩은 변경하지 않는다.

초기 재현 harness의 잘못된 branch 이름과 HTTP report 클래스 선택 오류는 원 로그에 보존했다. 실제 enum을 사용해 reader 두 경계와 null 사유를 재현했고, HTTP는 정확한 KubernetesRecoveryHTTP.report를 대상으로 고정 base65f3388의 별도 source에서 두 분기 모두 FAIL→UNKNOWN을 추가 확인했다. 테스트는 fault 경계가 실제 호출됐는지도 검증한다. 기존 assert나 판정은 완화하지 않았다.

최종 소스에서 새 계약5개와 기존 제품 회귀43개, 총48개가 통과했다. 실제 native observer 생산자와 EXAMPLE census/HTTP I/O를 사용하며 기존 registry·EVD/RES/RPT·bundle 복원·저장 팩 동일 RES를 확인한다. 실제 OS/GPU 자원 잔존 실측이나 전체 GPU 회수 완료를 주장하지 않는다. 원 로그는 out/recovery-errors의 before-fix.log, before-fix-valid-fixture.log, before-http-valid-boundary.log, after-fix.log, final-contract-regression.log다.

read-only manifest 비교는424개 중 초기화·stream·resource_recovery 세 GPU소유 source의 차이만 확인했다. source-integration-differences.json은 검토 입력이며 승인이나 manifest 생성물이 아니다. 공통 담당의 공식 소스 통합과 해당 통합본 필수 검증이 남는다. 이전 source·FAIL·UNKNOWN·Ref는 소급 변경하지 않는다.
