# B 현재 인계

이 문서는 PR #240의 현행 인계다. CPU 준비·부분 관측과 전체 제품 실측을 구분한다. 현재 요청 범위는 검토 의견 수정과 CI 확인이며 GPU 기동은 하지 않는다.

## 현재 구현과 검증

| 범위 | 확인한 것 | 남은 것 |
| --- | --- | --- |
| REQ-08·09·10 | 지원표·provider 프로필로 소스 핀·응답 ID·환경 조건을 받는다. 요청 연결·저장·복원·오류 보존은 CPU 시험으로 확인했다. | 새 프로필·팩/입력의 실제 runtime·Ready·요청/worker 대응과 최종 제품 판정 |
| 입력 준비 | `guard.executors.runner_inputs`가 핀·파일 신원·독립 scope·사례 대응을 검사한다. `tools/b_runner_inputs.py`는 CLI만 제공한다. | 실제 운영 입력의 신원·권한·정책·지원 범위 확인 |
| B03·PLT-01 | 공급 입력의 실제 파일 SHA와 ENV·정책 결합, 기존 생성기·CLI 연결을 준비했다. | 실제 worker/boot 관측과 기업 정책·대상 입력 |
| B02 | 기존 공통 입력의 소비 경로를 연결했다. | A의 새 공통 계약과 REQ-12 점검 선언 |
| B04 | TP2의 LIF-08·11/SUP-06 정상·장애·복구 입력 계획을 준비했다. | 실제 rank 배정·shard/자원 관측과 TP2 환경 |
| SUP-16 | GB12 회수 자료의 SHA·CRC를 확인했다. root client·수집·pre-seal 후보는 CPU 준비까지 진행했다. | 정상·부분 파일·복구의 실제 수집과 제품 전체 판정 |

CPython 3.12.3 직접 영향 시험 73개가 통과했다(68.44초). 체크아웃 밖에 설치한 wheel의 Python `-I`에서 실제 준비→소비, 원 record 바이트 보존, 변조 거부를 확인했고 `tools`를 import하지 않았다. CPU·EXAMPLE 결과를 MEASURED나 제품 PASS로 집계하지 않는다. 전체 시험의 최종 판정은 변경 head의 Linux CI로 확인한다.

과거 V13의 선정 LAB 3사례 결과는 당시 소스·팩·원 출처와 정확 Ref로 보존한다. 이를 새 입력이나 모든 미측정 분기의 실측 완료로 확대하지 않는다.

## SUP-16에서 보존할 사실

GB12 자료는 14,636,464바이트이며 SHA-256은 `2681a23c1545647e4b25711cb4c570336efdc6e7a604332de452c9cc2a663219`다. 최초 CRC 확인은 1,971개 항목, raw manifest는 1,970개다. 회수 성공은 전체 실측 PASS가 아니다. 원 R8 전체 FAIL과 읽기 수락 UNKNOWN은 유지한다.

정상 전·복구 후에도 journal은 `BOUND_REACHED`이며 root 연산 완전성이 확인되지 않았다. 부분 파일 단계는 후보와 기록이 없고 install·첫 emit·Writer의 원인은 미확정이다. 부분 파일의 누락을 정상 결과나 새 PASS로 채우지 않는다.

마지막 기록된 실제 정지 확인은 2026-11-11 11:40:53 KST의 EC2 stopped·공인 IP 없음과 후속 SSM·활성 명령 0건이다. 이번 문서 정리에서 AWS 상태를 새로 조회하지 않았다. 당시 감시 실패, 정확한 off 시각 null, 비청구 비용 추정과 EBS 보존 사실은 과거 원문에 남긴다.

## 다음 입력과 담당

- A: 공통 입력 `/4`의 schema·단위·완결성 의미·fixture·생산자 소스 SHA, REQ-12의 실제 질문에 맞는 사건/점검 선언/투영 함수를 반환한다. B는 미지원 checks를 임의 확장하지 않는다.
- A: impact와 확정 정책·지원 범위를 반영한 새 팩과 정확한 Ref를 반환한다. TP2는 LIF-08 peer/rank 제어, LIF-11 실패 group 회수·복구, SUP-06 rank별 required/assigned/loaded의 지원 범위와 원자료가 필요하다.
- B 실행 담당(기존 세션2): 향후 GPU 실행이 승인된 경우에만 현재 ENV·target·prepared·정책·이미지/모델 고정값과 실제 runtime/Ready부터 확인한다. 지금 GPU는 기동하지 않는다.
- B 소비 담당(기존 세션5): 실제 helper→adapter→resolver 입력과 원 오류·부분 기록을 보존한다. 준비 도구의 성공을 실제 root 진입·부모 기한·wire 연결 성공으로 바꾸지 않는다.
- B 검토 담당(기존 세션4/6): 실행기 코드·최종 목록·출처와 원 FAIL/UNKNOWN을 대조하고, 실제 EVD→RES→RPT·정확 Ref·반출·복원·저장 팩 재판정은 원자료가 있을 때만 확인한다.

## 과거 기록과 정확 복원

PR이 추가했던 날짜별 문서·원자료·검토 JSON·미통합 코드 144개는 아래 고정 커밋에 남아 있다. 현재 docs에 중복 보관하지 않는다. 기존 main의 GPU 공개 결과 요약과 `sources/`, 로컬·AWS 원본은 삭제하지 않았다.

- [실제 관측·정리 원문](https://github.com/dohashelby/byul/blob/451292dbdae4013f9437537c608a1e9f7205b792/update/docs/gpu-b/consolidation-20261106/SUP16_ACTUAL_OBSERVATIONS_20261111.md)
- [입력 요구와 A 반환 요청 원문](https://github.com/dohashelby/byul/blob/451292dbdae4013f9437537c608a1e9f7205b792/update/docs/gpu-b/consolidation-20261106/next-input-audit-20261111/PR240_HANDOFF.md)
- [pre-seal 후보와 미확인 연결](https://github.com/dohashelby/byul/blob/451292dbdae4013f9437537c608a1e9f7205b792/update/docs/gpu-b/consolidation-20261106/retained-preseal-components-20261111/README.md)
- [현재 case용 수집 후보의 미통합 범위](https://github.com/dohashelby/byul/blob/451292dbdae4013f9437537c608a1e9f7205b792/update/docs/gpu-b/consolidation-20261106/retained-current-archive-draft-20261111/README.md)

저장소 루트에서 다음 명령으로 원문·후보를 별도 ZIP에 복원할 수 있다. 이 명령은 파일을 반출할 뿐, 후보 실행이나 GPU 기동을 하지 않는다. 같은 이름의 ZIP이 있으면 다른 새 출력 이름을 사용한다.

```powershell
git archive --format=zip --output=out/gpu-b-pr240-history.zip 451292dbdae4013f9437537c608a1e9f7205b792 update/docs/gpu-b
```

당시 작업 트리와 Git blob 사이에 4개 파일의 CRLF/LF 차이가 있었다. 위 복원 명령은 Git blob을 복원한다. 로컬 정리 전 원바이트는 별도 `out/team-handoff/PR240-docs-before-cleanup.zip`에 SHA·CRC를 검증해 보관했으며, Git blob과 작업 트리 원바이트가 모두 같다고 주장하지 않는다.

후보 코드를 이후 실행할 때는 원 source manifest·SHA·설치 metadata를 함께 복원하고 현재 제품·입력과 다시 대조한다. 과거 Python/기동 조건·환경 신원·완료 상태를 현재 상태로 재사용하지 않는다.
