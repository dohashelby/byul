# PR240 최신 main 통합 검증

실제 GPU 소스 b85와 별도 작업 공간에서 PR240 head `acb2d318401e30b9e54ac57b86e0e1aae6bfdeee`에 main `3bd6a99511b628b6824a7bafd2de0cb7daa614bc`를 병합했다. 유일한 텍스트 충돌은 `update/release/reviewed-source.json`이며, 결합된 실제 파일에서 원 `verify_req10_consumption.py --write`로 재생성했다. 공통 코드·판정·팩을 수동으로 수정하지 않았다.

## 고정 범위와 검토

- 목록 SHA: `3ff0bb695cfd2bf2ea70a1ca80cab8835eb36436f7bb59b1c538b5eade62fe73`, 파일 509개.
- main의 508개에 B capture 신규 1개를 추가했다. 기존 파일의 차이는 B 연결 5개이며, 삭제 0개·원 pack_ref·paths를 유지했다.
- 각 연결 파일에서 명시된 B 연결을 제외한 AST는 최신 main과 동일하다. 신규 B 코드·도구·시험은 acb2의 원 bytes를 유지했다.
- 기존 B 상수 `338c2b6e…`, 원 gate bytes, PR241의 실제 R2 검토 기록 1건을 보존했다.
- 독립 검토: `Codex / 신규 세션6 / 독립 검토`, 실제 검토 시각 `2026-11-09T12:11:31.272029+00:00`. 사람 검토자 신원으로 표시하지 않았다.
- 독립 검토 565개 확인에서 불일치·구체 결함 0. 검토 proof SHA: `ed9625669989a275ac8a8d94d8d36b252dc82f25e08683696a8b4a3ee32a9971`.

실제 검토 뒤 정확 목록 SHA에 대한 검토 기록을 **1건** 추가했다. 목록을 읽거나 재생성하는 동작으로 승인하지 않았고, 다른 SHA의 기존 검토를 현재 SHA에 재사용하지 않았다.

## 검증

| 검증 | 결과와 한계 |
| --- | --- |
| REQ capture·Kubernetes 입력·sampling·SUP16 준비 직접 영향 5개 파일 | 143 passed, pytest 65.59초. root 도구 transcript가 원 증거이며 별도 XML/log와 부모 timer는 기록하지 않았다. 시간 측정만을 위한 재시험은 하지 않았다. |
| 검토 기록 추가 뒤 새 프로세스의 실제 B→A gate | exit 0. 현재 목록 509개·원 고정 B helper·팩/참고 핀과 실제 모듈 origin을 검증했다. |
| source/검토 기록 핀 직접 회귀 | 30 passed, pytest 10.50초 / 부모 벽시계 12.0초. 기존 143개는 반복하지 않았다. |

마지막 30개 로그 SHA는 `4abdf6b680780011a4817c4de2da6afb7883b075eb866832b25f898a397a2ee6`이다. 독립 검토자는 143개 실행을 다시 수행하지 않았으며 root의 transcript 기반 인계라는 한계도 보존했다.

이 결과는 PR 통합본의 CPU·소스 연결 검증이다. GPU에서 사용한 source는 계속 `b85dac21bfe380eaa7f06a7fe2b451a2e168daa2` / manifest `2e400a46…` / 466개다. 통합본을 GPU에서 실행했다고 주장하거나 기존 실제 증거·저장 팩·FAIL/UNKNOWN을 새 목록으로 소급 변경하지 않는다. 원격 새 head CI는 별도로 확인한다.
