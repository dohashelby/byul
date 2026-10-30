# 선정 LAB 정책 복귀·보존·수집 관측

기존 `serving_runtime.mvp` 구현과 원 PACK31 manual v3 경로를 재사용했다.
제품 runtime·CLI·registry·팩과 의존성은 수정하지 않았다.
LIF-15·16은 소유한 localhost Gateway와 기존 EXAMPLE MockEngine을 사용했다.
실제 모델·이미지 복원, vLLM 추론, GPU·Kubernetes 실행은 0회다.
각 흐름은 해당 소스·입력의 독립 audit이며 판본 비교쌍으로 사용하지 않는다.

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


## OBS-12 선정 LAB 용량 상한

고정 소스 `a8ea25daeb09d63816649929eb8afb55afdc225e`의 기존 RecordRetention·JsonlStore·QueryService를 사용했다. 관련 runtime 3파일은 이전 dcaf/6c와 같은 bytes다. 새 소유 LAB의 합성 managed journal 3,000bytes에 fixture 상한 2,259bytes를 적용했다. report-only 1회는 파일을 보존했고 enforce 1회에서 오래된 request의 gateway 2행·receipt 1행, 741bytes를 size 사유로 함께 삭제했다. age 삭제는 0행이며 fresh reader의 부재 조회, hold·undated·recent 유지, audit 1개와 새 retention reader의 last_deletion을 확인했다(0.192초).

2,259bytes는 managed journal 합계다. holds·audit 등을 포함한 디렉터리는 3,727bytes다. 기본 5GiB·기업 보존 기준·원격/backup/Q-C12 자산·물리 소거·payload 복구는 미수집이다. ENV는 실제 `byul-runtime-record-retention/0.0.0.dev0`, CPU·EXAMPLE fixture·model/image null을 기록한다. 원 OBS12rev3의 2prose·전체 normal/4challenge 미시도·3UNKNOWN을 유지한다.

원 capture와 기존 age 증거를 보존하고 기존 CLI 11개로 원 manual EVD→RES→RPT·정확 Ref 복원을 확인했다. 후속 메타 정정은 대상 runtime/backend/retention child 0개와 Git helper 최소 4개·동기 회수의 범위를 구분하며, 같은 관측을 사용해 필요한 metadata CLI 8개만 추가했다. 최신 EVD/RES는 revision 2, RPT는 `RPT-01M66W5E4ZAD378EE6XD5BAF3C@1`이다. 집중 검토와 정정 확인을 통과했다.

원 명령/입력은 저장소 밖 `byul-b-record-access-evidence-20261030/obs12-lab-bytecap/capture/{command,config}.json`, 공통 명령은 `evidence-scope-correction/commands.json`, 최신 증거는 같은 폴더의 `final-proof.json`·`report.json`·`evidence.zip`에 있다. 원 capture SHA는 `1c2a5d21ee1149e6a65dc001cbbdea32e16df0a271498fcb98f7521ad3f04e58`다.

## OBS-11 선정 파일 수집·오류 표시·복구

같은 a8 소스의 public heartbeat writer·ObservationFeed·Collector/CollectorManager를 실제 소유 프로세스와 파일에 실행했다. 독립 stdout 발행·stderr receipt를 남기는 publisher의 입력은 EXAMPLE이며 실제 kernel/Tetragon/Pod 사건은 0회다. 기업 5POL는 미입력 그대로이고 PLAN의 NO_EXECUTOR/NO_POLICY_VALUE와 audit records=[]를 보존한다.

첫 cohort는 3행의 실제 발행·fsync receipt와 저장 event key를 대조했다. 정상 → heartbeat updated_at의 +120초 fixture(원 60초 미래 입력 관문) 오류 → 실제 IsADirectoryError → 복구를 관측했고, pending 2행 재수집 후 총3행의 중복·거부·분실은 0이었다(0.451초). 이는 시스템 시계 변경이나 실제 clock uncertainty 측정 결과가 아니다.

독립 cohort2는 실제 기본 heartbeat 15초·collector 15초·interval 5초를 유지했다. publisher 생존과 heartbeat bytes 불변 상태에서 monotonic 15.351760초를 기다렸다. 대기 중 manager는 중지되어 자동 polling은 0회였다. 실제 snapshot은 ok→stale→stale(last_error에 expired)→ok이며, 새 reader의 조회1→1→1→2와 별도 receipt2개가 일치했다(wall 15.945초). 두 cohort는 각각의 PID·collection_id·시간·hash로 구분한다. 자식은 각각 exit0, 강제 종료0, manager thread0·stream close를 확인했다.

원 OBS11rev3의 2prose와 기업 기한이 필요한 normal 미시도, challenge True/True/False, 3UNKNOWN을 유지했다. 운영 경보 기한·기업5POL·종속 RES 자동 전파·실제 시계 오차·연속 polling 탐지는 미수집이다. 원 CLI11개와 expiry append8개 뒤, 최신 limitation을 첫 cohort/두 번째 cohort별로 한정한 metadata CLI8개만 추가했다. 원 두 capture·이전9objects/Refs는 보존하고 EVD/RES revision3→새 RPT `RPT-01M66X9QGFPYKKMK81YE59MN0G@1`, 복원12objects·verify/contract 오류0을 확인했다. 집중 검토와 정정 확인을 통과했다.

원 명령/입력과 독립 receipt는 저장소 밖 `byul-b-record-access-evidence-20261030/obs11-lab-feed/{capture,capture-expiry}`의 `command.json`·`config.json`·`publisher-receipts.jsonl`에 있다. 최신 공통 명령과 원 manual·보고서·반출은 `evidence-scope-correction/{commands.json,manual-evidence.yaml,final-proof.json,report.json,evidence.zip}`에 보존했다. 원 capture SHA는 `0fc6b43a17e302092f17fa7d16ee9a87a1e6e13add586536db11b22f880350b4`, expiry SHA는 `b4cc7dbda1c4f3d22b261ff47787294a1c511aae7395e5f5db49fcd9a3c34b35`다.
