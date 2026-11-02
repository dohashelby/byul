# GPU 보수 통합 소스 검토

제품 diff 검토 기준은 #189가 병합된 main `93a3674b82f829d1271cfd9e308d47bdd9e92924`다. 작업 중 병합된 runtime·공용 계약 PR #190의 main `3e9d19a`도 통합한다. 해당 병합은 update 제품·시험·팩·승인 소스 범위를 바꾸지 않는다. 기존 #188과 원 실측 증거는 보존하고, 검증된 보수와 GPU에서 거부된 후보를 분리한다. 이 문서와 해시 변경은 상대 소유자의 검토를 받을 PR 후보이며 기업 정책·새 native 기능의 승인 기록이 아니다.

## 가져온 변경

- REQ-09·REQ-10·SUP-03·OBS-03의 정상 대조와 요청은 검증된 API 모델 별칭을 사용한다. ENV의 모델 저장소 ID·revision·inventory 결합은 유지한다.
- PLT-04는 실제 정상 추론 시도를 기록한다. 이 기록을 GPU worker 전체의 자격 관측으로 확대하지 않는다.
- PLT-01은 Docker DeviceRequests가 있을 때 Devices 목록만으로 유효 장치 범위를 완전 수집했다고 표시하지 않는다. 확인한 독립 root·쓰기 위반은 보존한다.
- REQ-17은 기존 Kubernetes probe의 요청별 monotonic 시계를 사용한다. kubectl 부모 벽시계·무지원 timing basis·불완전 응답은 시간 표본으로 사용하지 않는다.
- #189의 LIF-06·09·16 보강과 Windows 신호 수정, REQ-17 방향 반전 구분을 그대로 유지한다.

REQ-14의 `AsyncGPUModelRunnerOutput.get_output` callback 설치·출력 투영 후보와 해당 후보 시험은 이번 통합에 넣지 않는다. GPU 설치에서 거부된 원 결과와 후보 코드는 #188에 남아 있다. REQ-14 지원 완료나 새 GPU 실측을 주장하지 않는다.

## 39개 실패의 분류

#188 GitHub 실행 41432341172의 결과는 39 failed / 10,659 passed / 19 deselected였다. 과거 로컬 b54148d의 41 failed와 별도 기록이다.

| 분류 | 실패 수 | 원인과 처리 |
| --- | --- | --- |
| 공통 source 고정·실제 B helper 소비 | 9 | 보수 소스가 기존 승인 목록과 달라 거부됨. 이번에 선택한 전체 파일 diff와 변경 목록을 검토하고, 실제 통합 소스의 목록 후보를 생성함. 원 b1 helper/default c9 pin과 gate 조건은 유지 |
| REQ-13 capture·installed adapter | 30 | SUP-03 별칭·버전 보수로 template_validation.py bytes가 바뀌어 전체 파일 pin이 거부됨. 소비 함수와 모듈 diff를 확인하고 두 소비자의 고정 pin 후보를 일치시킴 |

이 분류는 실제 실패 메시지와 소스 경로를 확인한 결과다. 최종 검증 결과는 같은 PR의 검증 기록에서 별도로 보고한다. 목록 재생성 자체를 소유자 승인으로 간주하지 않는다.

## REQ-13의 전체 파일 pin 검토

- 이전 template_validation.py SHA256: `6c68fc91d1a9cb93ed83036c53a391381efe9e653a581e965a1e8290003b4b78`
- 보수 파일 SHA256: `0f96a26959180ed783160ec7b3d5a13e0c0e5d5f4d913ffe257f4b84926efe27`
- 변경 내용은 TemplateValidationExecutor 버전과 검증된 API 별칭 사용이다. REQ-13이 소비하는 `_token_ids` AST는 기준과 같다.
- `_token_ids` AST SHA256: `5f3af3fa7746e1cd448b1594e22b32eb24809257715ae70645306ec628924991` — `ast.dump(..., include_attributes=False)` 기준.
- `guard.adapters.kubernetes_response_error`와 `tools.b2_response_kubernetes_capture`에 같은 고정 전체 파일 pin을 제안한다. 실행 시 bytes에서 승인을 학습하거나 환경/target의 주장으로 pin을 채우지 않는다.
- 다른 3개 helper 파일·함수 출처 검사·A HTTP capability pin은 그대로다. 파일의 주석 한 줄 변조도 전체 파일 mismatch로 거부하며, 다른 모듈에서 온 `_token_ids`도 거부하는 직접 회귀를 추가했다.
- REQ-13 실제 Kubernetes/native handler 지원 범위는 바뀌지 않는다. EXAMPLE transport를 MEASURED로 승격하지 않는다. 후속 수집 실패 뒤 확인된 위반을 보존하는 기존 회귀도 유지한다.

공통 `reviewed-source.json`은 이번 선택 소스 12개의 전체 bytes 변경만 반영한다. 원래 결합 후보의 13개를 그대로 승인하지 않고, 미지원 callback·투영 후보를 제외하고 REQ-13 소비자 변경을 포함한 결과다. 팩·framework·의존성·고정 B helper 3개는 이번 변경에서 수정하지 않는다.

## 별도 CI 오탐 수정

main 25a88d4 실행의 Graph 시험은 값 비노출 확인용 문자열 `999`가 정상적인 64자리 식별 해시에 들어가 실패했다. 실제 값 영역의 비노출 assert를 유지하면서 이름이 정해진 `*_token`·`*_sha256` 필드의 올바른 64자리 hex 식별자를 구분한다. 다른 필드의 숫자·문자열·중첩 값과 PRIVATE 표식은 계속 검사한다. 시험이나 assert를 삭제하거나 새 skip/xfail을 넣지 않는다.

## 검토 범위

새 GPU 요청·AWS 기동·권한 변경·정책 확정은 수행하지 않는다. 과거 PACK31/32·EVD·RES·RPT·원 ZIP·원 FAIL/UNKNOWN은 그대로 보존한다. 현재 팩 revision과 체크리스트 질문·judge·required_observations는 변경하지 않는다.

## 검증 경로

Windows Python 3.12.14 직접 353개, Linux Python 3.12.3 직접·팩/source gate 502개가 통과했다. Windows의 합성 source fixture는 UTF-8/LF bytes를 명시해 같은 pin을 검증한다. Linux 검증은 과거 B160 고정 커밋까지 포함하는 전체 Git 이력을 공급하고 수행했다. 각각 최초 준비/fixture 실패와 수정 후 결과를 별도 로그로 보존했다.

`tools.checklist_status --check`는 checks=77, real_connected=23, supported_connected=68로 통과했다. 연결 수를 새 실측 완료 수로 해석하지 않는다. 기본 전체 suite·설치 wheel·GitHub CI의 최종 결과는 이 PR 본문에 명령·실행 커밋·pytest 및 부모 시간과 함께 기록한다.
