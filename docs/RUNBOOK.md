# 실행·실증 안내

지원: Python 3.11, Linux/macOS, 로컬 Docker. Windows는 기본 논리 테스트 일부만 확인했다.
실제 엔진 시험에는 사전에 받은 HF 모델 캐시와 호환 아키텍처의 pinned 이미지가 필요하다.
정책/팩 DRAFT이면 공식 보고서가 아니다.

## 설치와 기본 검증

```sh
uv sync --frozen
uv run guard --help
uv run pytest -q
uv build --wheel
```

기본 테스트는 Docker/engine을 제외한다. 새 실행기는 실제 엔진 검증과 구분한다.
실증의 store와 output은 기존 out/5b 밖의 새로운 경로를 사용한다.

M10의 REQ-09는 두 required 정책 `allowed_endpoints`·`allowed_input_formats`도 필요하다.
전체 승인 목록이 정의의 단일 engine POST/token_ids 표본과 일치할 때만 지원하며, 목록을 줄여 맞추지 않는다.
빈 양식은 값을 채우지 않고 미입력은 NOT_RUN, 미지원 범위는 요청 전 ABORTED다.
수동 증거는 `evidence template --include-automated`·비공백 override_reason과 두 generic 범위 관측을 사용한다.
[M10 계약과 검증](POLICY_M10.md)을 확인한다. 실제 엔진/두 이미지 실증은 별도다.

## 기존 audit

새 기업 정책 파일은 아래 명령으로 만들고 설명 주석을 따라 실제 결정값을 입력한다.
빈 양식도 기존 로더/선정에서 읽히지만 선언된 필수 키가 없으면 `NO_POLICY_VALUE`다.
기존 출력 파일은 덮어쓰지 않고 입력 팩 내부 출력도 거부한다. [입력 계약·설명 출처](POLICY_TEMPLATE.md)를 함께 확인한다.

```sh
uv run guard policy template --pack packs/PACK-BASELINE --out my-policy.yaml
```

```sh
uv run guard select --pack packs/PACK-BASELINE --env examples/env.yaml --policy examples/policy.yaml
uv run guard run --plan PLAN-ID
uv run guard judge --plan PLAN-ID
uv run guard report --plan PLAN-ID --json report.json --html report.html
uv run guard verify
```

기본 guard.toml은 FakeExecutor 시연이다. 실제 엔진 결과로 해석하지 않는다.

수동 증거는 `guard evidence template --plan PLAN-ID --out manual-v2.yaml`로 생성해 입력한다.
SUP-14/LIF-07은 항목·case·첨부 연결이 필요하며 /1 읽기 호환과 재판정 영향은
[M4 수동 증거 계약](POLICY_TEMPLATE.md)을 따른다. 필수 목록만 입력했다고 PASS가 되지 않는다.

## Managed 업데이트

[managed 설정 예시](../examples/update-managed.toml)를 복사하고 환경/정책을 준비한다.

각 ENV에는 engine.name/version/image_digest(repository@sha256:64hex),
model.id/revision/inventory_sha256, runtime.gpu_count를 명시한다.
HF 캐시는 `models_dir/hub/models--ORG--MODEL/snapshots/REVISION`을 사용한다.
model inventory는 snapshot 전체 파일의 정렬 목록 해시다.

```sh
uv run python tools/model_inventory.py /cache/hub/models--ORG--MODEL/snapshots/REVISION
uv run guard update --pack packs/PACK-BASELINE --baseline env-a.yaml --candidate env-b.yaml --policy policy.yaml --config update.toml --store .guard-update --output out/updates
uv run guard verify --store .guard-update
```

각 side는 다른 컨테이너/target으로 순서대로 기동·실행·정리된다.
ENV의 버전/이미지 digest와 실제 준비된 엔진이 다르면 ABORTED로 기록한다.
기동 실패가 있어도 가능한 다른 side와 보고서를 남긴다.
종료 코드 0은 파이프라인 실행 성공이며 보안 PASS를 뜻하지 않는다.
기동/실행/필수 자동화 미실행 실패는 종료 코드 1이다. 수동 전용 점검은 보고서에서 UNKNOWN으로 남는다.

CPU ENV 예시(실제 확인한 환경값으로 작성):

```yaml
runtime:
  gpu_count: 0
  cpu:
    architecture: x86_64
    model: REPLACE_WITH_ACTUAL_CPU
    system: Linux
    kernel: REPLACE_WITH_ACTUAL_KERNEL
    threads: 2
```

이 값은 ENV 신고값이며 자체 하드웨어 계측 증거는 아니다. CPU 성능 축은 UNKNOWN이다.
GPU 사용 시 cpu 프로파일을 넣지 말고 실제 GPU/driver를 제공한다.

## 외부에서 준비한 두 대상

[external 예시](../examples/update-external.toml)처럼 side별 target 경로를 지정한다.
같은 target 파일을 두 side에서 재사용하지 않는다. 각 실제 환경과 target을 별도로 확인해야 한다.
각 target은 lab=false, observed_version, engine.image_digest를 포함하고 해당 ENV와 일치해야 한다.
target의 `comparison_config`에는 엔진 이미지 자체를 제외한 실제 launch/보안 조건을 기록한다.
이 정보가 없는 과거 target은 보안 시험은 가능하지만 설정 조건 미확인으로 비교 불가다.
시험 역할 키는 이름으로만 설정하고 값은 환경변수에 둔다.

## 신규 점검

PLT-04의 executor 이름은 credential_exposure다. 정책은 forbidden_env_names/list[str],
forbidden_mount_paths/list[str]이며 컨테이너 환경변수 이름/마운트 경로만 확인한다.
처음에는 실제 자격 대신 GUARD_TEST_CREDENTIAL 같은 무해한 표식을 사용한다.
자격값은 수집하지 않으며 읽기 실패가 있으면 PASS가 되지 않는다.

REQ-09의 executor 이름은 input_validation이다. 정책은 max_input_tokens/int,
rejection_codes/list[str]이며 예시는 ["400", "422"]다.
실행 설정에는 vocab_size/int와 vocab_source/str를 pinned 모델 근거와 함께 넣는다.
max_probe_tokens는 기본 4096이며 예산보다 큰 시험은 요청 전 ABORTED다.
정상 token-array [0]가 통과한 뒤 범위 밖 ID와 입력 길이를 시험한다.
다른 입력 형식/모든 경계 조합/실제 모델 범위 전체를 검증한 것으로 해석하지 않는다.
두 신규 점검은 이번에 실제 엔진 실증하지 않았다. 활성화 전 정책과 설정을 검토한다.

## 실제 두 이미지 통합 테스트

```sh
export GUARD_UPDATE_CONFIG=/absolute/update.toml
export GUARD_UPDATE_BASELINE=/absolute/env-a.yaml
export GUARD_UPDATE_CANDIDATE=/absolute/env-b.yaml
export GUARD_UPDATE_POLICY=/absolute/policy.yaml
uv run pytest -m engine tests/invariants/test_inv_09.py -q
```

조건이 없으면 해당 테스트는 사유와 함께 skip한다. managed/real 설정 및 서로 다른 이미지 digest를 강제한다.
기본 테스트의 통과 수를 이 실증의 성공으로 대체하지 않는다.

## 증거 보존·복원

update 출력에는 plan.txt와 JSON/HTML이 있고 store에는 ENV/POL/PLAN/EVD/RES/RPT와 팩 보존본/artifacts가 있다.
전용 store 전체와 실행 설정을 바이트 그대로 보관한다. 키·불필요한 환경변수 파일은 보관하지 않는다.
복사본에서 verify를 실행하고 plan.txt의 PLAN으로 judge/report를 실행한다.
현재 packs 폴더가 없어도 정상 팩 보존본이 있으면 재판정할 수 있다.
과거 팩이 없는 경우 정확한 git revision에서 꺼낸 팩을 pack import로 먼저 넣는다.
현재 저장소의 과거 exposed/isolated store에는 참조 증거 파일이 각각 1개 누락돼 있다.
원본을 확보하기 전에는 해당 저장소의 verify/완전한 재판정 성공을 보장하지 않는다.
기존 evidence를 새 버전으로 재판정하면 새 RES revision이 생기며 과거 결과는 수정하지 않는다.
정리 실패 같은 실행 생명주기 한계는 최초 update 보고서도 함께 보관한다.
judge/report 재실행은 시험 자원 정리를 다시 실행하거나 확인하는 명령이 아니다.

## AWS 실증 준비

위 Linux 절차를 재사용한다. 실제 계정/리전/자원 생성·가격 산정은 이번 작업에서 수행하지 않았다.
이미지 아키텍처·모델 캐시·CPU/GPU 조건·운영자 권한·엔진 격리·실행 시간 상한·자원 보관 기간을 먼저 정한다.
시험 종료 후 guard.run 라벨의 컨테이너/네트워크, 인스턴스, 볼륨, 로그/증거 보관 자원을 확인한다.
정리 실패 시 보고서만 보고 자원 정리까지 완료됐다고 판단하지 않는다.


## 실증 준비와 증거 묶음 명령

`guard prepare-update`로 입력과 로컬 Docker/이미지를 먼저 확인한다.
`guard evidence export/restore`로 검증된 저장소를 새 위치로 반출/복원한다.
명령·제약·5개 점검의 PASS/FAIL/UNKNOWN 사례 입력은 [실증 안내](VALIDATION.md)를 따른다.
REQ-09의 `max_probe_tokens`는 실제 경계 초과 요청(max_input_tokens+1)을 포함하는 요청당 상한이다.
probe 정리 실패는 증거를 보존하고 execution.cleanup_errors와 재판정 보고서에 남으며 명령은 실패한다.


수동 입력·대체 옵션과 반출/복원을 함께 사용하는 절차는 [통합 안내](INTEGRATION.md)를 따른다.

## M5 승인 기록 체계 입력

정책 양식의 SUP-01/SUP-02 아래에 각각 기업이 정한 approval_record_system 문자열을 입력한다. 실제 키/암호·계정 허용표를 대신 넣지 않는다. 미입력은 NO_POLICY_VALUE이며 공백은 수동 PASS를 UNKNOWN으로 보류한다. 값만 채워도 승인/배포 권한이 검증되는 것은 아니다.

기존 evidence template → 관측 설명/첨부 입력 → evidence import → judge → report 절차를 사용한다. 두 점검에는 항목별 policy_evidence를 만들지 않는다. JSON/HTML의 승인 체계 문맥·POLICY_CONTEXT·미인증 자기 신고·official 여부를 함께 확인한다. 과거 revision 11 보존 팩의 SUP-01/SUP-02는 신규 전제를 소급하지 않는다. 과거 LIF-07 추론 충돌 때문에 보류된 확인 위반은 declared 0.4.0 재판정에서 FAIL이 될 수 있으며 원본 EVD/RES는 보존된다.

OBS-11의 필수 기록·기한·시계 오차·장애 대응은 [후속 결정표](POLICY_M5_PLAN.md)를 먼저 확정한다. collector 장애 자체를 공격 성공이나 종속 시험 PASS로 처리하지 않는다.

## OBS-11 수동 검증 (/3)

정책 양식에서 OBS-11의 필수 목록과 사건별4개 정수map을 기업 기준으로 입력한다. 실제 값/담당/승인은 자동 생성되지 않는다. 다른 점검처럼 select → evidence template → 각case 관측·주장별 첨부 입력 → evidence import → judge → report를 사용한다. 필수 누락에 경보가 필요한 최초 프로파일의 적용 근거를 입력하고 적용 불명/조건부 범위는 UNKNOWN으로 남긴다.

부재는 고장 난 collector 로그만으로 확정하지 않는다. 독립 발생과 전체 조회 시작/종료·경보 수신점·시계 오차를 확인한다. 모든 시각은 timezone을 포함해 따옴표로 쓰고 소수초는3자리 이하다. 누락 관측을 OBS11 FAIL과 동일시하지 말고 정상 처리/경보 부재의 추가 근거를 구별한다. 미수신·조회 실패·창 미종료는 부재 확정이 아니다. 실제 collector 주입이나 운영 중단을 이 명령이 수행하지 않는다.

관측의 실제 필드·정책/시간 의미·구형 /1·/2 처리·보고서 제한은 [M7 계약](POLICY_M7.md)을 따른다. JSON/HTML의 AUDIT_COLLECTION 이유·미인증 신고·official 상태를 함께 확인한다. 복구 사실은 기록하지만 복구 SLA·작성자 인증·실제 다른 RES 전파는 검증하지 않는다.

## M8 usage/replay 정책 문맥 확인

현재 팩14에서 정책 템플릿을 생성하면 OBS04/05의 usage_allowed, OBS07/08의 replay_allowed가
주석으로 안내되고 실제 값은 비어 있다. [정책 입력 안내](POLICY_TEMPLATE.md)를 따라 기업이 정한 bool만 입력한다.
false는 비활성 관측이 아니며 true는 실제 구현·권한·승인 증명이 아니다. 같은 범위의 공유 값은 업무 검토한다.

기존 select → evidence template(/3) → evidence import → judge → report 흐름을 사용한다.
새 증거 payload나 실행 명령은 없다. POLICY_CONTEXT의 실제 true/false·분기 사유와 JSON/HTML의
DRAFT·official·미인증 자기 신고·자동 검증 한계를 함께 확인한다. OBS04 false / 나머지 true 외의 문맥은
신고 PASS를 UNKNOWN으로 보류하며 FAIL은 보존한다. false를 비적용이나 자동 제외로 해석하지 않는다.
전체 분기·기업 결정·미검증 범위는 [M8 계약](POLICY_M8.md)에 있다.
