# OBS-06 선정 Pod 로그 조회 — 기존 수동 증거 연결

원 PACK31의 OBS-06 revision 4 질문·관측 설명 2개·챌린지 6개를 그대로 사용한다.
새 모듈은 고정 `kubernetes_record_access.capture_record_access` 수집 결과를 기존
`build_template`/`import_evidence`에 연결한다. 새 CLI 등록·팩·POL 필드·판정기는 없다.
원 Store는 읽기 전용으로 검증하고 기존 bundle export/restore로 새 output/store에
복제한다. 수동 증거·판정·보고서는 이 사본에만 추가한다.

## 입력과 실행

설치한 제품의 Python 3.12에서 실행한다. 기본 실행은 입력·원본 Ref·대상 계약만
검사하며 출력 디렉터리 생성과 Kubernetes I/O를 수행하지 않는다.

```text
python -m guard.collectors.record_access_evidence \
  --store EXISTING_STORE --plan EXISTING_PLAN_ID --pack ORIGINAL_PACK31 \
  --target OWNED_TARGET_JSON --subjects SUBJECT_PLAN_JSON \
  --output FRESH_OUTPUT --entered-by DECLARED_OPERATOR
```

`--execute`를 붙이면 고정 collector를 한 번 호출한다. A의 기존 lifecycle target
계약과 정확 PLAN ENV/POL/PACK Ref를 검증하며 audit/single PLAN만 받는다.
target은 `guard-kubernetes-lifecycle-target/1`의 명시 소유 Pod·context·세대·이미지와
`lab=true`를 가진다. ENV는 해당 PLAN이 참조한 Store 객체에서 읽는다.

subject plan은 로컬 실행 입력이며 기존 POL의 정확 Ref에 결합한다.

```text
schema: guard-record-access-subjects/1
environment_ref: <PLAN의 정확 ENV Ref>
target_sha256: <target canonical JSON SHA256>
policy_ref: <PLAN의 정확 POL Ref>
subjects:
  - role: authorized_reader
    username: <명시된 허용 독자>
    groups: [<명시 그룹>]
  - role: unauthorized_reader
    username: <명시된 비허용 독자>
    groups: [<명시 그룹>]
```

두 username은 달라야 하고 groups는 명시한 비어 있지 않은 목록이어야 한다.
subject plan의 canonical hash로 같은 실행 입력을 연결한다. 주체 계획은 신고이며
기업의 승인 진위나 `entered_by`의 신원 인증을 생성하지 않는다. subject 누락,
Ref 불일치, 다른 PLAN mode는 collector 호출 전에 거부한다.

collector의 행은 `allowed-before`, `unauthorized`, `allowed-after`이며 각각의
주체·전후 대상 세대·완전한 제한 수집 결과를 직접 확인한다. 실제 whoami/logs와
읽기 권한의 집행·impersonation 권한 부족·불명확한 403·빈 결과는 collector의
수집 계약을 따른다. bridge는 역할 이름이나 같은 로컬 UID로 대상 권한을 추정하지 않는다.

## 결과 범위

- `record-access.json`: 정확 ENV/POL·대상/subject-plan hash, 수집 출처·시각,
  case/role·identity/binding/read 상태·returncode·byte_count·정제 gap만 보존한다.
  로그 본문·내용 hash·whoami 원문·username/groups·예외 본문은 저장하지 않는다.
- 원본 normal control에는 필요한 저장 암호화도 포함된다. 수동 양식에는 전체
  normal control을 `not_attempted`로 기록하고 선정 read의 True/False/None은
  artifact와 첫 관측 설명에 따로 남긴다. 챌린지는 비허용 조회 단계만 실제 시도
  상태를 연결하며 나머지 부분 범위·암호화 단계는 false다.
- 비허용 행에서 identity/binding/read가 확인되고 rc=0·nonempty·행 gap 없음이며
  collector의 `unauthorized_read_verified=true`이면 원 질문의 직접 위반을
  DECLARED security FAIL로 제출한다. after·cleanup gap이나 artifact 저장 실패는
  이미 확인한 이 위반을 지우지 않는다. artifact 저장 실패 때 기존 manual importer의
  무첨부 FAIL 보존 계약을 사용한다. 그 밖의 security 결과는 UNKNOWN이다.
- 저장 암호화·복호화 권한은 두 번째 관측 설명에 UNKNOWN으로 남긴다. 원본·collector·
  backup 전체 사본, 독립 감사, 전체 정상 기능·성능도 UNKNOWN이다. PASS는 제출하지 않는다.
- 기존 수동 EVD의 data_origin은 항상 DECLARED다. EXAMPLE collector fixture는
  artifact와 관측 설명에 EXAMPLE을 표시하며 실제 Kubernetes 실행으로 세지 않는다.

같은 as-of로 judge/report를 실행한 뒤 기존 verify/export/restore를 재사용한다.
복원 Store의 기존 정확 Ref를 대조하고 같은 PLAN/as-of의 재판정과 보고서 내용을
확인한다. 보고서 재구성의 새 RPT ID는 기존 Ref 보존 검사와 구분한다. 원 Store·
입력 파일의 bytes는 전후 대조한다. 새로운 output을 요구하며 기존 출력은 덮어쓰지 않는다.
수동 import 이후 후처리 실패가 발생해도 새 Store에 저장한 EVD/RES를 삭제하지 않는다.

## 전용 검증

전용 시험은 정식 원 PACK31과 기존 Store/manual/core/공개 CLI를 사용한다.
collector 경계 한 곳만 EXAMPLE fixture로 대체한다. 실제 Kubernetes collector와
합치는 검증은 해당 고정 소스의 별도 통합 단계에서 한 번 수행한다.

```text
uv sync --frozen
python -B -m pytest tests/test_record_access_evidence.py -q --basetemp=EXTERNAL_FRESH_TEST_DIR
```

원 질문/2개 관측 설명/6개 단계·정확 Ref·전체 normal 미시도, 독립 FAIL·불확실
UNKNOWN·암호화 미수집·후기 실패·출력 append-only·bundle 복원·원문 비저장을 확인한다.
실제 Pod 로그 접근, 저장 암호화 또는 실제 principal 권한 시험의 결과를 생성하지 않는다.

## 통합 검증 결과 — 2026-10-30

collector49개·후속수정 직접7개·증거연결22개의 고정 결과를 재사용했다. root가 실제collector→공개A metadata reader→EXAMPLE 전송→원manual import/판정/보고서/Store/반출복원을 1회 연결해 통과했다(0.56초). Pod 이름 logs가metadata조회 시도로 오인되던1건을 수정했고 검토자가 해당diff 해결을 확인했다. 원Store와 입력은 불변, 원문/주체/로그hash는 첨부에 없다. 전체normal 미시도·저장암호화UNKNOWN과 독립FAIL이 복원Store에도 유지됐다. wheel166개 source/설치 bytes와 신규2모듈 import가 일치하며 Torch/vLLM은 부모에 로딩되지 않았다. 실제principal/Kubernetes/저장암호화 실행은0회다.
