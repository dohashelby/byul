# native16 부분 capture 보수의 제품 소비

A-2 PR143 f8f6025d166d8b85968b64d785a035f6e063f189 전용3파일 원bytes만 소비한다. PR144/native17 diff는 다시 적용하지 않는다. 공개 `guard.collectors.native_weight_iteration.analyze(packet)` 0.1.1, schema `guard-native-weight-selection-iteration/1`을 기존 OBS02 `observation_identity:kubernetes` 0.4.1에서 호출한다. 기존 summary `weight_selection_iteration`과 configured model의 `weight_selection_iteration_candidates` 소비 위치를 재사용한다.

capture_session이 다른 recording 읽기에 실패해 `capture=None`, session_owner_match=false, listed_file_identity_match=false를 반환하면 원 recording 번호와 `weight_recording_capture_unavailable` 공백을 보존한다. 다른 recording의 유효 selection/root/iterator/callback 후보를 지우지 않는다. 누락을 관측으로 만들거나 전체 수집 완료·worker/파일/tensor 적용·승인을 확인했다고 처리하지 않는다. 모순 binding=true·malformed·다른 profile/source는 원 parser와 gate로 계속 거부한다.

선정 제품 검증은 정상, 다른 capture 누락, 부분 iterator, 후속 기록/첨부 오류, 누락+첨부 오류, 전용 소비 오류다. 원 public analyze를 관측하는 시험 spy는 packet/결과를 바꾸지 않으며 source gate/parser는 대역으로 바꾸지 않는다. 기존 CLI 선택→실행→EVD/RES/RPT→verify→원 반출/복원→저장팩 정확 Ref 재판정을 확인한다. 고정 후 직접·동일 계약·인접·설치 결과를 별도로 인계한다. 이 초안은 통과를 미리 선언하지 않는다.

모든 제품 입력/전송은 EXAMPLE이며 native18 실제 C CPU 원 증거는 별도다. 진짜 serving/worker/파일 읽기·최종 tensor/물리 해제·승인·전체 SUP03/05/06/OBS02와 부모/A1 완료로 올리지 않는다. 원 B c9/helper/승인쌍을 보존하고 A측 전체389파일 literal pin만 검토 후 갱신한다. 새 A 쌍은 B 검토 전 거부된다. 새 skip/검사 제외/runtime hash 승인·AWS/Start/IAM/기간/비용·dependency 버전·CI설정 변경은 없다. CI5는 정확 HEAD 성공 전 미확인이다.
