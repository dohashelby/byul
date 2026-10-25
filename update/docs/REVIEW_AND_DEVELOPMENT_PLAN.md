# 최신 코드 재평가 및 구현·개발 계획

작성: 2026-10-19 (Asia/Seoul). 검토 시작점: `main / ec4f91be3b9017b787e4c23e92f2b07dd7de5161`.
구현 브랜치: `codex/gua-update-mvp`. 이 문서의 구현 결과는 해당 브랜치의 코드 기준이다.

## 1. 결론

**공통 구조를 유지하며 정책 자동화를 확대할 수 있다.** 이전 HTTP 메서드 누락·정책값 미연결은 이미 수정돼 있었다.
이번에는 부분 수집의 거짓 PASS, 팩 폴더 부재 시 재판정 실패, CPU 비교 조건, 업데이트 제품 진입점을 보완했다.
기존 객체 7종·append-only·실행/판정 분리를 유지했다. 전면 재작성은 하지 않았다.

**실제 두 이미지 비교 실증과 신규 점검의 실제 엔진 검증은 완료되지 않았다.**
이 작업 환경은 Windows이며 Docker daemon이 동작하지 않고 모델 캐시도 없다.
새 실행기의 코드/모의 계약 검증과 과거 macOS 엔진 기록을 구분한다.
전체 완료율·기간·점수는 산정하지 않는다.

바로 이어서 할 일:

1. Linux/macOS에서 제공한 managed 설정과 실제 서로 다른 두 digest로 업데이트 시험.
2. PLT-04·REQ-09의 정상/위반/관측 누락 구성을 실제 엔진에서 검증.
3. 정책 확정자·팩 검토자 및 다음 점검 범위를 결정한 뒤 5개 정의의 실측 범위를 확정.

## 2. 목표와 현재 위치

정본은 [SPEC](../SPEC.md)이다. 환경·정책 → 선정 → 실행 → 증거 → 판정 → JSON/HTML 보고서가 기본 흐름이며,
업데이트는 같은 점검을 두 이미지에서 실행해 비교한다.
v1은 vLLM, 단일 노드 Docker, CLI, 우선 5~10개 점검이다.
웹 UI·추가 엔진·다중 노드·77개 전체 자동화·서명 registry는 의도적인 제외 범위다.

| 영역 | 이번 작업 후 상태 | 검증 또는 남은 조건 |
|---|---|---|
| 환경·정책·선정 | 기존 구조 유지, 판정표 참조 사전 검사 추가 | 기본 테스트, 미정 정책은 값 입력 필요 |
| 객체·해시·이력 | 7종 유지, CPU 선택 필드는 없을 때 직렬화에서 생략 | 과거 객체 해시 호환 테스트 |
| 실행기 | 기존 3종 보완 + 자격 노출·입력 경계 실행기 | 신규 2종은 모의 실행 검증 |
| 판정 | 확인된 위반 유지, 부분 프로세스 수집은 PASS 불가 | 회귀 테스트; 실제 /proc 제약 조건은 실증 필요 |
| 보고서 | 보안/기능 비교와 비교 불가 필드 표시 | CPU 성능은 UNKNOWN |
| 업데이트 | external/managed CLI, side 분리, 순차 기동·정리 | 모의 생명주기 검증, 실제 두 이미지 미실증 |
| 설치·배포 | 런타임에 샌드박스 자산 포함, wheel 빌드, Linux CI 정의 | 원격 CI 결과는 별도 확인 |

## 3. 이전 지적사항의 해결 여부

| 쟁점 | 검토 시작점 | 이번 조치 및 현재 근거 |
|---|---|---|
| GET·POST 경로 합침 | 해결 | [메서드 회귀](../tests/test_method_keys.py) 보존 |
| 정책 타입·필수값 | 해결 | [select](../src/guard/core/select.py), 기존 정책 타입 회귀 보존 |
| 정책값 실행·판정 연결 | 기존 3개에서 해결 | [direct_route](../src/guard/executors/direct_route.py), [rule_table](../src/guard/judges/rule_table.py) |
| PLT-01 불완전 worker 누락 | 미해결, 모의 입력에서 PASS 재현 | 기록을 보존하고 process_scan_complete를 PASS 조건에 추가 |
| 빈/깨진 마운트 표 | 새로 확인, 모의 출력에서 PASS 재현 | 불완전 수집은 UNKNOWN; 수집된 위반은 유지 |
| 현재 팩 폴더 없음/손상 | 일부 해결 | [CLI](../src/guard/cli.py)는 정상 보존본으로 대체; 변조 보존본은 거부 |
| 줄바꿈 무결성 | 예방 속성만 추가돼 있었음 | 팩 생성기를 UTF-8/LF로 고정. 기존 바이트를 임의 재해시하지 않음 |
| CPU 비교 | 미해결 | 사용자 결정에 따라 명시적 CPU 프로파일 지원. 미확인 하드웨어는 비교 불가 |
| 실행마다 다른 설정 해시 | 미해결 | [비교 조건 정규화](../src/guard/runtime/comparison.py), 일회성 주소/이름 제거 |
| side별 실행 대상 | 미연결 | [애플리케이션](../src/guard/application.py)과 RunConfig의 side별 설정 |
| guard update | CLI 없음 | 제품 명령 구현. 실제 두 이미지 완료 주장은 보류 |
| 기본 fake·DRAFT | 의도적 데모 설정 | 유지. 실제 설정 예시를 별도 제공하고 official=false 유지 |

팩은 revision 10, DRAFT다. 중간 개발 revision 8/9도 로컬 테스트에 사용했으며 정식 실증 팩을 뜻하지 않는다.
기존 revision 4/5 증거·보고서와 원본 저장소는 덮어쓰지 않았다.
새 수집 완전성 조건은 새 팩에 적용된다. 과거 증거의 누락 관측을 재판정으로 만들어내지는 않는다.
revision 5의 원본 팩이 저장돼 있지 않은 과거 실행은 이 변경만으로 복원되지 않는다.

## 4. 코드 변경과 설계 경계

- [application.py](../src/guard/application.py): 제품 실행 흐름을 조정한다. CLI와 코어는 Docker를 직접 제어하지 않는다.
- [runtime/sandbox.py](../src/guard/runtime/sandbox.py): 기존 lab의 기동/대상 생성/준비 대기를 공유한다.
  lab 진입점은 호환 래퍼이며 설치 패키지는 lab 디렉터리를 필요로 하지 않는다.
- [core/run.py](../src/guard/core/run.py): side별 실행 설정과 단일 side 실행을 지원한다. 판정하지 않는다.
- [container_config.py](../src/guard/executors/container_config.py): 불완전 프로세스를 버리지 않고,
  확인한 UID·capability와 수집 완전성을 분리한다. 알려진 위반은 FAIL로 유지한다.
- [store/packs.py](../src/guard/store/packs.py): 기존 내용 주소 보존을 재사용한다. 새 DB나 객체 종류를 추가하지 않았다.
- [models/objects.py](../src/guard/models/objects.py): CPU 프로파일과 선택 보고서 필드만 추가했다.
  빈 새 필드를 직렬화에서 빼 과거 해시를 유지한다.
- [preflight](../src/guard/core/preflight.py): 선언하지 않은 관측/정책 키·연산자 및 충돌 값을 실행 전에 거부한다.
- [Docker](../src/guard/runtime/docker.py): 정리 실패를 조용히 성공 처리하지 않도록 한다.
  실행 후 정리 실패는 이미 수집한 증거를 빈 ABORTED로 대체하지 않고 최초 보고서 한계와 명령 실패로 남긴다.

CPU 프로파일은 architecture/model/system/kernel/threads를 모두 요구하며 ENV에서 gpu_count=0을 요구한다.
ENV의 하드웨어 프로파일은 입력자가 제공한 조건이다. 이것만으로 하드웨어를 MEASURED로 승격하지 않는다.
GPU·driver가 단순히 null인 과거 기록을 CPU로 추정하지 않는다.
CPU 프로파일은 보안·기능 비교에 사용하고 성능 판정은 UNKNOWN으로 남긴다.

설정 정규화에는 위상, 출발점의 네트워크 관계, Gateway/엔진 프로파일, 보조 이미지,
launch 조건, 역할, 시험 설정이 남는다. IP·컨테이너 이름·실행 ID는 비교 조건에서 제외한다.
기존 외부 target에 비교용 launch 정보가 없으면 config_sha256을 미확인으로 두어 비교를 강제하지 않는다.

## 5. 점검 구현과 검증 수준

| 점검 | 실행기/판정기 | 이번 확인 수준 | 실증 범위와 제한 |
|---|---|---|---|
| REQ-01 | direct_route/rule_table | 기존 회귀 재실행; 과거 실측 보고서 존재 | v0.31 CPU 노출 FAIL·격리 PASS 기록. 이번 실제 엔진 재실행 없음 |
| REQ-03 | role_route/rule_table | 기존 회귀 재실행; 과거 실측 보고서 존재 | 역할 제한 미적용 FAIL·적용 PASS 기록. 새 버전 안전 프로브는 재검토 필요 |
| PLT-01 | container_config/rule_table | 수정된 부분 수집 회귀, 기존 모의 테스트 | PROGRESS의 과거 실제 엔진 성공 기록과 이번 모의 결과를 구분 |
| PLT-04 | credential_exposure/rule_table | PASS/FAIL/UNKNOWN 모의 계약 | 금지 환경변수 이름과 bind/volume 경로만 검사. 값·SDK·호스트 전체 자격은 미검증 |
| REQ-09 | input_validation/rule_table | PASS/FAIL/UNKNOWN·정책 민감도·요청 예산 모의 계약 | token-array의 ID/길이 두 사례. 실제 엔진/모델 경계는 미검증 |

따라서 **실행기와 판정기가 연결된 정의는 5개**, 신규 2개까지 실측 완료된 5개라고 세지 않는다.
현재 체크리스트 77개 중 나머지 72개는 자동화 완료가 아니다.
새 점검의 제한은 CheckDef.limitations를 통해 보고서에도 표시한다.

기업 정책: PLT-04 금지 변수 이름/경로, REQ-09 허용 입력 길이/거부 코드.
실행 설정: target, 타임아웃, 요청 예산, 모델 vocab_size와 출처.
관측: 실제 응답·프로세스 이름·마운트·수집 완전성. 세 범주를 서로 대체하지 않는다.

### 다음 확장 순서

1. 이번 5개 정의의 실제 엔진 검증을 먼저 완료한다.
2. REQ-10은 입력 실행기를 재사용하되 옵션별 한 가지 변화·정상 요청 유지 관측을 추가한다.
3. OBS-03은 합성 표식·로그 수집 범위·수집 완전성부터 정의한다. 로그 없음과 읽기 실패를 구분한다.
4. REQ-13은 응답 허용 필드/표식 출처를 정하고 OBS-03의 표식 생성만 공유한다.
5. REQ-05는 실제 제공되는 모델 식별자에 대한 허용/거부를 입증한다. 존재하지 않는 모델의 404는 인가 근거가 아니다.
6. LIF-09는 초기화 실패와 도구 ABORTED를 구분하고 엔진 ready 상태·복구를 관측한다.

## 6. 후속 개발 작업과 완료 기준

| ID/우선순위 | 작업·산출물 | 완료 확인 | 선행 조건/결정 |
|---|---|---|---|
| V1/P0 | Linux/macOS 설치·패키지 실행 기록 | 새 환경에서 frozen 설치→CLI→verify | Docker, 모델 캐시 |
| V2/P0 | 두 실제 이미지 update 증거 | 서로 다른 digest·같은 비교 조건·양쪽 EVD·JSON/HTML·정리 | 비교할 버전 2개 선정 |
| V3/P0 | PLT-01 수정의 실제 수집 검증 | 읽기 실패/프로세스 종료가 거짓 PASS가 되지 않음 | /proc 권한·프로세스 변화 시험 fixture |
| V4/P1 | PLT-04 실제 표식 시험 | 정상/불필요 전달/수집 실패 구분, 자격값 미기록 | 무해 자격 표식과 금지 목록 |
| V5/P1 | REQ-09 실제 모델 시험 | 정상 token-array·경계 밖 거부·후속 정상 요청 | 실제 vocab 출처, 입력 한도·예산 |
| R1/P1 | 증거 묶음 반출·복원 검증 | 별도 저장소 복사 후 verify 및 재판정 | 실행별 독립 store 사용 |
| P1/P1 | 정책 확정·팩 검토 | 실제 담당자·시각·검토 범위 기록 | 팀 담당자 선정 |
| A1/P1 | AWS 단일 노드 실증 | 동일 설치/실행/반출/정리 재현 | V1~V5, 예산·계정 승인 |
| N1/P2 | REQ-10 | 정책 경계·조합·후속 기능 계약/실측 | 입력 실행기 실측 완료 |
| N2/P2 | OBS-03/REQ-13 | 표식 누출·수집 실패·허용 정보 구분 | 로그/응답 정보 정책 |
| N3/P2 | REQ-05/LIF-09 | 유효 모델 인가·실제 실패/복구 입증 | 모델 허용표·실패 fixture |
| H1/P2 | 저장소 캐시 추적 정리 | 캐시만 추적 해제, 실증 원본 유지 | 기존 산출물 분류 확인 |

바로 시작할 작업은 V1 → V2, V3~V5다. 실제 장비가 준비되면 V3~V5는 서로 독립적으로 진행할 수 있다.
테스트가 통과한 코드 작성과 외부 실증 완료를 같은 상태로 표기하지 않는다.

## 7. 검증 기록

실행 환경: Windows x86_64, Python 3.11.16, 기존 개발 가상환경, 별도 LF worktree.
원래 main의 로컬 체크아웃은 기존 CRLF 해시 불일치가 있으므로 그대로 보존했다.
새 의존성은 추가하지 않았다. wheel 빌드에는 기존 build-system의 hatchling이 사용됐다.

| 실행 | 결과 | 해석 |
|---|---|---|
| 수정 전 새 회귀 6건 | 6 failed | 불완전 worker/빈 마운트/팩 부재·손상 문제를 먼저 재현 |
| 수정 후 관련 회귀 | 31 passed | 당시 단계의 수집·팩 계약 |
| 업데이트/CPU/신규 점검 묶음 | 16 passed | 모의 실행이며 실제 엔진 증거가 아님 |
| 첫 전체 실행 | 288 passed, 2 failed, 4 skipped, 13 deselected, 149 setup errors | 임시 경로 부모 부재 + 경계/POSIX 경로 가정; 이후 수정 |
| 경로 수정 후 전체 실행 | 438 passed, 1 failed, 4 skipped, 13 deselected | CLI의 runtime 직접 참조를 경계 검사에서 발견 |
| 애플리케이션 분리 후 집중 검증 | 45 passed | side 분리·실패·수집 계약 포함 |
| 최종 전체 테스트 | 451 passed, 4 skipped, 13 deselected / 159.44s | 최종 코드 기준, 아래 명령 |
| wheel 빌드/새 가상환경 설치 | 성공, frozen 의존성 및 설치본 CLI help 확인 | 샌드박스 자산 포함; 실제 엔진 실행은 아님 |
| 과거 store 복사본 검증 | exposed 1건 누락, isolated 1건 누락, inv15 0건 | 기존 파일 보존; 정상 원본 확보 필요 |
| 설치본 독립 CLI 파이프라인 | select/run/judge/report/verify 통과 | EXAMPLE 시연; 소스 PYTHONPATH 없이 설치본 사용 |
| 실제 두 이미지 opt-in 테스트 | 1 skipped, 12 deselected | 필요한 GUARD_UPDATE_* 설정 미제공 |
| 실제 엔진/Docker stub | 미실행 | Docker daemon 없음, 모델 캐시 없음 |
| 원격 Linux CI | 워크플로 정의 완료, PR 생성 후 확인 | 원격 결과는 별도 확인 |

기본 테스트 명령(프로젝트 루트, 가상환경의 Python):

```sh
python -B -m pytest -q --basetemp=.review-tests-verified --junitxml=out/test-runs/review-verified.xml
uv build --wheel --out-dir out/test-runs/dist
```

Windows 건너뜀은 연결 거부/timeout 차이 3건과 symlink 권한 1건이다.
마운트 read-only 테스트는 플랫폼별 Path 문자열을 사용하도록 고쳤고 read-only 계약은 유지했다.
지원 플랫폼은 여전히 macOS/Linux다. Windows 전체 지원을 완료한 것은 아니다.

과거 기록은 [PROGRESS Step 3/4](../PROGRESS.md)의 10 passed·1 skipped(실제 엔진) 및 421 passed·13 deselected(기본)다.
신규 실제 엔진 결과로 재사용하지 않았다. 최신 PLT-01 전체 원본 실행 산출물은 이 커밋에서 확보되지 않는다.
기본 테스트와 모의 실행 성공만으로 official이나 운영 가능을 주장하지 않는다.

과거 저장소 누락 파일은 exposed의 artifacts/b0ea15f258b9e5a1d6467a84fb0cedf9de84e9397814cdd9ee92ac3a6bcfd550,
isolated의 artifacts/abab6bbb7271c144094d16d85d033083029e17350f29b22edb7a5642ab81399f다.
시작 커밋과 확인한 6d9ad83/13bb4d3에서도 해당 파일을 찾지 못했다.
이것은 이번 수정에서 발생한 파일 변경이 아니며 해시를 바꿔 맞추거나 증거를 재생성하지 않았다.
필요한 다음 산출물은 정확한 원본 바이트의 복구 또는 새로운 실제 실행의 별도 증거다.

## 8. AWS 샌드박스 실증 계획

실제 자원은 만들지 않았다. [실행 안내](RUNBOOK.md)의 동일 CLI를 Linux 단일 노드에서 먼저 사용한다.

| 순서 | 산출물 | 검증 |
|---|---|---|
| 환경 선택 | 리전·아키텍처·메모리·디스크·이미지 목록 | CPU arm64 기존 결과를 x86/GPU 결과로 확대하지 않음 |
| 접근·격리 | 운영자 접근, 엔진/Gateway/시험 출발점 구성 | 엔진 공개 범위와 신뢰 출발점 확인 |
| 설치 | 고정 커밋·frozen 의존성·모델 revision | 설치·모델 inventory 사전 검사 |
| 실행 | 실제 두 이미지의 독립 store·비교 보고서 | 조건 일치/불일치·실패 원인·기능 표본 |
| 증거 보관 | store 전체와 팩·설정의 바이트 보존 묶음 | 복사본에서 verify/재판정 |
| 종료 | 시간 상한·잔여 컨테이너/네트워크·클라우드 자원 점검 | 종료 실패를 별도 실패로 기록 |
| 비용 | 사용 시간·디스크·전송량·보관 기간 | 예산 및 실행 시간 제한을 따로 관리 |

Session Manager는 인바운드 관리 포트/SSH 키 없이 접속하는 방식을 지원한다.
[AWS 공식 문서](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager.html).
예산 알림은 지연될 수 있으므로 실시간 비용 차단으로 간주하지 않는다.
[AWS 공식 문서](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-managing-costs.html).
현재 가격·인스턴스 종류·기간은 장비/예산 요구가 정해지지 않아 제시하지 않는다.

## 9. 팀 결정과 MVP 완료 기준

확정된 결정: 사용자 승인에 따라 명시적 CPU 조건으로 보안/기능 비교를 지원하고 CPU 성능은 미검증으로 둔다.
GPU/driver null을 임의 CPU 값으로 채우지 않는다. 나머지 정책 임계값은 임의 확정하지 않았다.

남은 결정: 실제 비교할 이미지 두 개, 실제 모델 경계와 기업 입력 한도, 금지 자격 범위,
정책 확정자·팩 검토자, AWS 환경/예산, 성능 수용 기준과 대표 하드웨어.

MVP 완료 확인:

- 정한 범위의 5~10개 점검이 실제 엔진에서 실행되고 범위/미시험이 보고된다.
- 위반·누락·실패가 PASS로 승격되지 않는다.
- 실제 서로 다른 두 이미지가 CLI에서 비교되고 비교 불가 이유가 설명된다.
- 과거 팩 보존본으로 저장된 증거를 오프라인 재판정한다.
- 새 Linux 환경에서 설치·실행·증거 반출·정리가 재현된다.
- 공식 제출은 별도로 정책 확정·팩 검토·실증 환경 적합성을 확인한다.

후속 확장: 대표 GPU 성능, TP/다중 노드, 기업 승인/IdP/배포 시스템, 추가 엔진.
개발 기간은 인력과 실증 환경이 정해진 뒤 산정한다.

## 작업 방식 갱신

최종 검증 후 사용자가 구현 묶음 완료 → 테스트 → 실패 수정 → 필요한 재검증 순서를 지시했다. 이후 작업부터 적용하며, 위 검증 기록은 실제 수행한 과거 순서를 그대로 보존한다. 이번 추가 변경은 작업 지침 문서뿐이므로 전체 테스트를 반복하지 않는다.


## 후속 구현 연결

4d97dba 이후 실증 준비·증거 묶음·실패 처리 보강은 [실증 안내](VALIDATION.md)에 정리했다.
위 검증 표는 4d97dba까지의 과거 결과다. 후속 직접 실행 결과는 PROGRESS.md의 별도 항목에서 확인한다.
