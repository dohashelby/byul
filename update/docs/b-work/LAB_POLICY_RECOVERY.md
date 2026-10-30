# LIF-15·16 선정 LAB 정책 복귀·복원 관측

기존 `serving_runtime.mvp` 구현과 원 PACK31 manual v3 경로를 재사용했다.
제품 runtime·CLI·registry·팩과 의존성은 수정하지 않았다. 실제 실행 대상은
소유한 localhost Gateway이며 backend는 기존 EXAMPLE MockEngine이다.
실제 모델·이미지 복원, vLLM 추론, GPU·Kubernetes 실행은 0회다.
두 흐름은 서로 다른 소스·입력의 독립 시험이며 판본 비교쌍으로 사용하지 않는다.

## LIF-15 현재 승인과 정책 복귀

고정 소스 `dcafb1dc20e150ca9e0776b19f7865691e41c6eb`에서 실제 Gateway
5세대와 MockEngine 1개를 사용했다. v1 승인 → v2 승인 → 여전히 승인된 v1 복귀
→ v2 현재 승인·v1 철회 → 보존한 정확 v1 bytes로 새 Gateway 복귀를 한 흐름으로
관측했다. HTTP는 `200/200/200/200/503`이었고 마지막 사유는 `POLICY_REVOKED`,
approval은 unverified였다. 마지막 요청 전후 backend 수신 수는 `4/4`였다.

실제 수집 1회, 3.083초. Gateway 5개·MockEngine 1개 종료, 소유 소켓 6개 닫힘과
잔존 스레드 0개를 확인했다. 실제 이미지·모델 배포 복귀는 남은 범위다.

- EVD: `EVD-01M66ERXV745MMXHTF7GTD5ZY9`
- RES: `RES-01M66ERY5K8121Y6WHEQPF5THK`
- RPT: `RPT-01M66ERYFV9SK180R47NS7XHSX`

원 LIF-15 revision 2의 null 관측·전체 정상 대조·3개 challenge 미시도와 세 축
UNKNOWN을 유지했다. 선정 정책 gate 관측은 첨부로 구분했다. 원 Store를 보존하고
공통 CLI 11개로 manual import, judge/report, 반출·복원과 정확 Ref·verify를 확인했다.
집중 검토에서 발견사항은 없었다.

## LIF-16 실제 CLI의 bundle 거부와 원본 복구

고정 소스 `6c360dcbe942389f86d10942667b5f43fc452e97`의 실제 runtime CLI를
사용했다. 기존 CLI 환경에 distribution metadata가 없어 help 단계에서 중단된
준비 오류를 보존한 뒤, 외부 소유 사본을 정상 wheel로 빌드하고 `--no-deps --target`으로
설치했다. 44개 Python 파일이 고정 원본과 같았다. 실행한 distribution 버전은
`0.0.0.dev0`, wheel SHA-256은
`b2cf12d9f606a6e1102a7ba6c13ddc61edff6c0e4bfc95fd71bda7f3b8190874`다.
원 제품 소스·venv는 그대로 두었다.

실제 실행 명령은 다음 형태이며 포트·폴더는 모두 시험 소유 자원이다.
`PYTHONPATH`에는 정상 설치한 runtime 배포물 경로를 사용한다.

```text
python -m serving_runtime mvp --backend mock --policy-bundle LAB_BUNDLE \
  --gateway-port OWNED_GATEWAY_PORT --engine-port OWNED_MOCK_PORT \
  --data-dir OWNED_DATA
```

실제 CLI 4회로 원본 정상 시작 → 정책 파일 무해 변조 → 물리 파일 누락 → 원본 bytes
복구를 관측했다. 변조는 SHA-256 불일치, 누락은 파일 부재로 rc1 종료했고 시작 표식과
소유 listener가 없었다. argparse 거부와 구분했다. 원본·복구본은 rc0, 정책·status API
200과 실제 startup snapshot의 정책 bytes/id/version 일치를 확인했다. 정상 2개
프로세스는 SIGINT 후 rc0으로 종료했고 소유 listener·startup snapshot이 정리됐다.
수집 묶음은 1회, 2.687초다.

ENV의 초기 engine 이름을 실제 대상 `byul-runtime-mvp-policy-gateway`로 정정했다.
원본 ENV·PLAN·EVD·RES·RPT와 초기 proof를 보존하고 동일 capture로 새 객체를 추가했다.
실측·runtime CLI·wheel·설치를 반복하지 않았다. 최종 ENV의 version은 위 실제 배포물
버전이며 model 세 필드와 image digest는 null이다.

- 최종 EVD: `EVD-01M66ESBTSWPE44AYAMCY339PP`
- 최종 RES: `RES-01M66ESC7XH9CC4TKW0WQ9P5YY`
- 최종 RPT: `RPT-01M66ESCN76N9RCSZDEFZYR3XR`

원 LIF-16 revision 2의 전체 정상 대조·4개 challenge 미시도와 세 축 UNKNOWN을 유지했다.
선정 bundle 파일 외의 이미지·모델·자격·volume·Gateway 외부 정책·원격 backup 복원은
미수집이다. 원 Store 불변, 새·기존 정확 Ref 보존, 반출·복원 verify를 확인했다.
집중 검토의 ENV 지적은 증거 단계만 정정한 뒤 해결을 확인했다.

## 증거와 재사용

저장소 밖 `byul-b-record-access-evidence-20261030` 아래의
`lif15-lab-policy-rollback`과 `lif16-lab-policy-bundle`에 고정 입력, controller·명령,
stdout/stderr, source 전후 hash, 정제 capture, Store·반출본과 SHA-256 manifest를 보존했다.
두 case의 최종 proof·Store는 `canonical-evidence` 아래에 있다. LIF-16의 실행 대상 정정은
`revised-evidence`에 있으며 초기·중간 증거도 보존했다.
기존 suite·다른 OS·통과한 source CPU 시험은 반복하지 않았다.

## 객체 ID와 실제 플랫폼 결과 조회

기존 B 도구가 만든 설명형 ENV·POL ID가 SPEC §2.1 및 공통 Ref 계약의 종류-ULID
규칙과 맞지 않아 플랫폼에서 보고서를 형식 오류로 분류하는 결함을 확인했다.
B 전용 6개 producer의 ENV/POL 생성식 12개를 기존 `guard.models.new_id()`로 바꿨다.
사용자가 제공한 Ref·팩 ID·과거 객체와 실제 수집·판정·sampling 경로는 유지했다.
변경된 gate 파일의 의존 SHA도 갱신했고 전체 component·helper858·fixtureac8 기준은
유지했다. 새 producer 회귀 6개·인접 guard 18개와 실제 7개 strict 검증 경로를 확인했다.
집중 검토에서 발견사항은 없었다. 실제 source CPU 실험은 반복하지 않았다.

두 LAB의 같은 capture를 새 canonical ENV/POL→PLAN→EVD→RES→RPT에 연결했다.
원본·이전 Ref는 사본 Store에 보존했고 원본 Store도 그대로 두었다. 원 capture의
Ref나 bytes를 바꾸지 않았다. 최종 `final-proof.json`은 최초 proof를 보존한 채
artifact 80개와 DB를 포함한 bundle 파일 81개를 구분해 기록한다.

기존 플랫폼 source `6c360dcbe942389f86d10942667b5f43fc452e97`를 소유 localhost에서
실행하고 정확 보고서 2개만 별도 파일 입력으로 공급했다. 기존 의존성으로 현재 웹 소스를
1회 빌드했고 공통 API·UI·계약 소스는 수정하지 않았다.

- 목록: `GET /api/update/results` → 200, 유효 2건·형식 오류 0건.
- 상세: `GET /api/update/results/{report_id}` → 두 정확 RPT 모두 200.
- 원 RPT의 체크 원문·inputs manifest·RES Ref·한계와 DECLARED·manual·세 축 UNKNOWN 일치.
- 실제 Chrome에서 두 보고서의 정확 ID, 수동 신고·미확인 표시와
  ENV/POL/PLAN/EVD/RES 추적 정보를 확인했다.

초기 형식 오류 2건의 원 응답·보고서는 별도로 보존했다. 플랫폼은 RPT 파일을 소비하며
EVD 본문·첨부를 직접 조회하는 API는 제공하지 않는다. 이번 확인 범위는 보고서와
정확 Ref의 조회다. 플랫폼 증거는 같은 외부 증거 루트의 `platform-results-consumption`에
보존한다. 소유 서버·포트와 브라우저 탭을 정리했으며 기존 사용자 자료·서비스는 유지했다.
