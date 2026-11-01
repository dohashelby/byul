# native16 부분 capture 보수의 제품 소비

A-2 PR143 f8f6025d166d8b85968b64d785a035f6e063f189 전용3파일 원bytes만 소비한다. PR144/native17 diff는 다시 적용하지 않는다. 공개 `guard.collectors.native_weight_iteration.analyze(packet)` 0.1.1, schema `guard-native-weight-selection-iteration/1`을 기존 OBS02 `observation_identity:kubernetes` 0.4.1에서 호출한다. 기존 summary `weight_selection_iteration`과 configured model의 `weight_selection_iteration_candidates` 소비 위치를 재사용한다.

capture_session이 다른 recording 읽기에 실패해 `capture=None`, session_owner_match=false, listed_file_identity_match=false를 반환하면 원 recording 번호와 `weight_recording_capture_unavailable` 공백을 보존한다. 다른 recording의 유효 selection/root/iterator/callback 후보를 지우지 않는다. 누락을 관측으로 만들거나 전체 수집 완료·worker/파일/tensor 적용·승인을 확인했다고 처리하지 않는다. 모순 binding=true·malformed·다른 profile/source는 원 parser와 gate로 계속 거부한다.

선정 제품 검증은 정상, 다른 capture 누락, 부분 iterator, 후속 기록/첨부 오류, 누락+첨부 오류, 전용 소비 오류다. 원 public analyze를 관측하는 시험 spy는 packet/결과를 바꾸지 않으며 source gate/parser는 대역으로 바꾸지 않는다. 기존 CLI 선택→실행→EVD/RES/RPT→verify→원 반출/복원→저장팩 정확 Ref 재판정을 확인한다. 고정 후 직접·동일 계약·인접·설치 결과를 별도로 인계한다. 이 초안은 통과를 미리 선언하지 않는다.

모든 제품 입력/전송은 EXAMPLE이며 native18 실제 C CPU 원 증거는 별도다. 진짜 serving/worker/파일 읽기·최종 tensor/물리 해제·승인·전체 SUP03/05/06/OBS02와 부모/A1 완료로 올리지 않는다. 원 B c9/helper/승인쌍을 보존하고 A측 전체389파일 literal pin만 검토 후 갱신한다. 새 A 쌍은 B 검토 전 거부된다. 새 skip/검사 제외/runtime hash 승인·AWS/Start/IAM/기간/비용·dependency 버전·CI설정 변경은 없다. CI5는 정확 HEAD 성공 전 미확인이다.


## 고정 검증 결과

원 source68ad6d5c7a2e44082c2f3035e0b81f14c95c0977의 직접 시험은 양OS 55통과·1실패였다. 새 부분 fixture가 load 호출 바깥의 iterator를 만들었으므로 원 전용 분석이 귀속 후보를 만들지 않은 정상 경계를 잘못 기대했다. 원 실패·공통 관측·반출물을 보존했다. 원 DefaultModelLoader 호출 안에서 model.load_weights가 iterator를 조기 닫는 구체적 fixture로 바꾼 sourcee8ada7dee9c5aa70a8e785ca561000f68d38b57d의 해당1사례가 양OS 통과했다. 원 후보 기대를 유지하고 root_exhausted=false를 추가 검증했다. runtime/A2/B/gate는 바꾸지 않았으며 전체56개 최신 통과로 합산하지 않는다.

선정 인접은 Windows60통과·기존 INV03skip1, Linux61통과다. weight sensor, native17/18/19 정상 정식 제품과 UNKNOWN/EXAMPLE/미시도/독립FAIL/근거없는false/승인 불변식을 확인했다. 새 skip이나 검사 제외는 없다. 원 f8 전용3파일 bytes와 native17/18/19 원 구현은 보존했다.

wheel SHA256 a7619cb1f0896666dbd516fafddbb00312dbf0f15bb4fdaf89e7f41cbbae89d4의 guard281파일이 source/Windows 설치본과 일치했다. 양쪽 격리 설치본은 정상·다른 capture누락·누락+첨부 오류·전용 소비 오류의 CLI4사례와 정확24Ref가 통과했다. 프로젝트/기존 환경의 dependency 버전은 바꾸지 않았다. 소스의 나머지 원6사례와 보수된 부분1사례는 각OS 원7반출물·정확42Ref이며 initial/v2 source를 구분했다. EVD/RES/RPT·verify·원 evidence.zip bytes·복원·저장팩 재판정이 일치했다. 누락 capture에서는 두 binding=false를 확인하고 다른 recording의 root/selection/source locator를 보존했다.

검토 common4b6c0d264c2809ad43bcd8514a59bc74ebd88ac6, 전체389파일 manifest SHA256 fd0322077122d3b8bb6df10792aef956cb855baf0ba60f103859177042517beb를 고정했다. B 원 c9/helper/세 승인쌍은 보존돼 새 A 쌍을 LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE로 거부한다. B가 자신의 source/common/HTTP pin을 검토하기 전 미승인이다. 런타임 읽은 hash로 승인하지 않는다.

정확 최종 HEAD CI는 별도 인계이며 CI5 성공 전 미확인이다. 앞선 PR166의 정확HEAD3d6f30b CI는 실제 runner에서 default suite 615실패·8553통과·169오류·19제외로 끝났고 CPython3.12.15와 여러 native code gate 거부를 확인했다. 지원 고정 검증 runtime은 Windows3.12.14/Linux3.12.3이며 원 실패 전체를 이 원인 하나로 단정하지 않는다. 결제 차단으로 재분류하거나 gate/skip/CI설정을 바꾸지 않았다. 원문 로그·env/임의 예외는 저장하지 않고 정제 실패 노드/오류코드/runtime만 A1 인계에 남겼다.

실제 serving/worker/GPU·승인/정책/최종tensor/물리해제·전체 SUP03/05/06/OBS02와 부모A/A1 완료는 미완료다. PR164 C CPU 원근거와 이번 EXAMPLE 제품 관측은 별도다. AWS/Start/IAM·기간/비용 상한·CI설정 변경은 없다.
