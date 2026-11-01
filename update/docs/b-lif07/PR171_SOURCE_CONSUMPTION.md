# PR171 B source 소비 보수

기준은 PR171 `dd5077ce4989853c369ffce9fdcb7795d9801ec0`이다. B 전용 변경이며 공유 gate·registry·팩·core/models/store/judges를 수정하지 않는다. 원 B 스택은 재적용하지 않는다.

- 선행 fixture-only 보수: `9a99d7045464df32b4a91053513cb1d0695ab315`. 역사 c39 Git blob과 SHA5ac820…를 사용하며 원 assert·세 승인 pair를 보존한다. 직접14+보존 인접3 통과.
- native 소비: `22d3eb4`(원 author1448a23). gate를 읽는 source `dd5077c`, 그 gate의 검토 common `df6255e96a877ebfbe185230ed87320aa84d2c30`, gate SHA `00979cf6b5139e0337d1f7976acad2b816e1018c4422cf8a44a087b0c28fc233`의 정확 pair만 추가한다. 새 fixture도 dd5077c 전체 Git bytes를 사용한다. 직접 신규 영향16 통과.
- HTTP 소비: `7428d42`(원 authorb387315). 기존 f277과 검토된 df625만 HTTP 기능 범위로 허용하고 실제 proof common을 저장한다. 사전 exact pair/fullscope gate를 먼저 통과해야 한다. 원 collector/binding/probe 세 SHA는 동일하다. 직접 영향7 통과.

원 다섯 roots 전체392 manifest는 `e42b1633ce5966e0781400b61477f8fe15aa72d9562036620008f56e88f241cb`와 일치했다. 원 helper858/c9 pin·기존 세 정확 pair·native21/b88/callbacka583·lab/source/image/ABI/native0.32 미지원 경계는 보존한다. 새 ABI/source 자산을 준비하며 과거 prepared/record를 새 판본으로 재표기하지 않는다.

## 공개 경로와 증거

코드 고정 HEAD7428d42에서 실제 helper·공개 source gate·capture/project·main(argv)·Store를 사용했다. 전송만 EXAMPLE이며 실제 Kubernetes/worker/GPU는0이다. 로컬 Python은3.12.13이고 CI3.12.3 설정을 변경하지 않았다.

- native main: 1통과1.23초, 부모1.430초. `RPT-01M6AMTV6E58692BE08PHT1FH0@1`, SHA `0c561c4e71c3aca756657d02ec71c69e14da572f202168ff76f2ce949fe77466`.
- HTTP main: Running/Unready engine·Ready probe의 EXAMPLE3요청, 실제 gate392·public API/parser, 0.820초. `RPT-01M6AMXFANCX8S4JFZ8Q38JG4S@1`, SHA `c160620ec752b18f589ad3add380d2fc20f46c553d091e50f1b4392fca84d888`.
- 두 Store의 정확6Ref·verify/반출/복원 일치. 저장 PACK31로 read-only 재판정한 RES Ref도 각각 원 ID/revision/SHA와 같고 새 객체·bytes 변경0이다. as_of는 저장 RPT 생성시각을 명시 사용했으며 원 별도 판정시각과 같다고 주장하지 않는다. POL exceptions는 비어 있다.
- 실제 source gate 알고리즘을 바꾸지 않고 common만 미등록 값으로 둔 음성 조합은 `LIF07_NATIVE_APPROVED_SOURCE_GATE_UNAVAILABLE`로 reader·process/network 진입 전에 닫혔다.
- native 수집·finally/seal/snapshot/project와 run/manual/report/Store/정리 함수 AST는 PR171과 동일하다. 후기 보존과 독립 FAIL/UNKNOWN 코어 계약은 원 CI9999통과 및 직접 인접 근거를 재사용한다. 이번에 새 worker/READY/GPU 위반을 실측한 것은 아니다. 두 새 보고서는 원 정상/challenge 미시도·3UNKNOWN을 유지한다.

## A-1 소유 시험 후속

`tests/test_a1_B_native21_consumption.py`의 다음 네 기대는 B160 원본/세 pair/현재 df 미승인을 전제로 하므로 새 B 전달물을 소비할 때 A-1이 갱신해야 한다. B는 이 파일을 수정하지 않았다.

1. `test_original_B160_eight_files_are_exact_confirmed_bytes`: B160 원본 검증은 역사 blob으로 보존하고, 현재 소스에는 B 확정 후속 bytes를 별도로 사용한다.
2. `test_new_A_full_common_pin_passes_while_unreviewed_B160_pair_is_closed`: 현재 검토 pair의 정상 허용과 full392/제외0/helper858 보존을 확인한다.
3. `test_real_B_gate_still_rejects_an_unapproved_pair_before_reader_or_remote_IO`: 승인된 현재 pair 대신 명시적 미등록/혼합 조합을 사용해 I/O 전 거부를 유지한다. 예를 들어 current gate bytes와 common0*40 조합은 계속 거부된다.
4. `test_B_reviewed_exact_A152_A156_A158_pairs_are_closed_and_literal`: 원 세 pair와 새 PR171 pair를 정확한 네 쌍으로 고정한다. SHA와 common을 독립 허용 목록으로 만들지 않는다.

최종 PR171 Python3.12.3 CI 성공은 이 A 소유 기대 정정과 B 전달물 적용 뒤 확인해야 한다. source 승인·EXAMPLE 전송을 실제 worker/READY/GPU 완료로 집계하지 않는다. 정제 원 증거·소스 동결·명령·setup 실패 보존은 외부 `byul-b-record-access-evidence-20261030/pr171-stabilization-20261101`에 둔다.
