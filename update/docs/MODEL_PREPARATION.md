# 모델·inventory와 모델 mount 준비

## 2026-10-29 inventory 수집 중 교체 보수

기존 `inventory`·`build_manifest`·`inspect_snapshot` 제품 경로에서 파일을
읽는 descriptor의 device/inode/크기/mtime을 선택한 파일과 대조한다.
경로와 descriptor의 ctime 의미가 다른 OS가 있어 각각의 관측원 안에서 전후
ctime 안정성을 확인하며 두 값을 같은 시각이라고 가정하지 않는다.
각 읽기 후와 전체 수집 종료 시 논리 경로의 해석 대상·파일 상태·선정 목록·root
신원이 바뀌면 완전 inventory를 반환하지 않는다. snapshot 소비는 기존
`MODEL_SNAPSHOT_UNAVAILABLE`을 유지하며 임의 예외 원문을 저장하지 않는다.
동일한 파일의 출력 형식·정렬·bytes·정규 inventory hash와 symlink 추적 규칙은 같다.

이것은 조사 도중 발견한 교체를 누락하지 않는 보수다. 파일시스템의 원자적
snapshot/잠금·불변성·후속 worker의 같은 bytes 소비·tensor/rank 완전 적재를
보장하지 않는다. 실제 Kubernetes worker의 open/read/load와 source/model 세대
귀속은 공통 관측과 별도 연결해야 하며 SUP03/05/06 보안 완료로 집계하지 않는다.
같은 metadata를 유지한 in-place 변경/복원이나 마지막 조회 이후의 변경까지
탐지·차단한다고 주장하지 않는다. metadata 점검을 위해 모델 전체 bytes를
중복해서 읽거나 새로운 파일시스템 잠금·시간 기준을 추가하지 않는다.

아래는 최초 준비 당시 기록이다. 공통 모델 매핑/mount 제품 소비는 `ef75639`
및 현재 공동 기준 `c9af729`에 통합됐으며, 이 보수는 그 코드를 재적용하지 않는다.

이 문서는 A-2 전용 준비 흐름이다. 공통 기준은 `fcb6c40f`의 profile8,
`validate_capture_target`과 `verify_owned_pod`다. 새 공통 CLI·전송·등록·판정은
추가하지 않는다. `model_mapping`과 `model_mounts`는 공통 연결 전의 제안 소비자다.
helper 검증을 제품 연결 완료 또는 실제 배포 검증으로 집계하지 않는다.

## 모델 이름과 경로

AWS 인계 원본은 다음 기술 선언을 가진다. 실제 파일·배포 관측은 아직 없다.

| 사실 | 인계 값 |
|---|---|
| 저장소 ID | Qwen/Qwen2.5-0.5B-Instruct |
| revision | 7ae557604adf67be50417f59c2c2f167def9a775 |
| API served 이름 | test-model |
| 호스트 snapshot | /srv/serving/models/7ae557604adf67be50417f59c2c2f167def9a775 |
| 컨테이너 mount | /models |
| 실제 파일 inventory·worker 소비 | 미수집 |

`guard.model-request-mapping/1`은 ENV Ref·저장소 ID·revision·기존 inventory 해시와
별칭·두 경로의 명시적 대응이다. 이 계약은 저장소 ID를 가진 ENV에 한정한다.
`ENV.model.id`를 API 별칭으로 덮어쓰지 않는다. inventory가 없으면 null로 남기며
매핑 검증은 inventory·실제 serving의 증명이 아니다. `request_model_name`의 반환값은
A-1의 기존 요청 템플릿 채우기와 정상 응답 모델 대조에 함께 연결해야 한다.
연결 전 `functional_requests`는 여전히 ENV 모델 ID를 사용한다. 이 문서가 새
요청 생성·응답 판정 경로를 추가하지 않는다. 과거 입력에 매핑이 없으면 기존 동작을 유지한다.

## 기존 inventory 규칙

`model_snapshot.inspect_snapshot`은 기존 `build_manifest`·`inventory(scope="all")`와
manifest 검증기를 소비한다. 파일별 `path, bytes, sha256`를 정렬해 기존 정규 JSON으로
해시하며 `guard.model-manifest/1`을 그대로 반환한다. 새 inventory 형식/해시는 없다.
`vllm` scope는 참고용이고 `all` 비교 해시를 대체하지 않는다.

AWS `model_manifest.py`는 `size`와 별도 파일 제외 규칙을 사용하며 symlink를 거부한다.
guard는 `.cache`·`.byul-model-ready.json`도 snapshot에 있으면 `all`에 포함하고,
HF 파일 symlink를 따라가 실제 내용으로 해시한다. AWS 출력 bytes/hash를 guard
inventory로 이름만 바꾸지 않는다. 실제 마운트 root를 guard 도구로 다시 조사한다.

정상은 MATCH, 내용 변경·누락·추가·부분 준비는 MISMATCH, 다른 root·없는 snapshot·
깨진 symlink·읽기 오류는 UNAVAILABLE다. 불완전 자료에 완전 inventory 해시를 붙이지 않는다.
MATCH도 승인·필수 tensor 충족·worker의 읽기/적재/소비를 증명하지 않는다.
소유 fixture의 가중치 bytes는 실제 모델 형식이 아니며 모델을 로드하지 않는다.
Windows 회귀는 symlink 권한 부족 때문에 기존 resolver 경계를 명시적으로 대역화한다.
Linux CI의 같은 시험은 실제 symlink를 쓰며 기존 inventory symlink 회귀도 유지한다.

## 선정 관측: 모델 hostPath mount 선언

`executors.model_mounts.analyze_model_mount`는 A-1 수집부가 메모리로 제공할 전후 Pod/Namespace
자료를 기존 binding 검증기에 전달한다. ENV Ref·namespace/Pod UID·container ID·
image ID·restart·startedAt·Ready가 고정된 같은 세대에서, `/models`와 선언한 호스트
snapshot의 단일 hostPath/Directory 연결 및 readOnly 설정만 투영한다.
subPath·중복·누락·세대/설정 변화는 해당 구간을 미수집으로 남긴다.
false인 readOnly도 선언 사실이며 기업 정책이 없는 자동 FAIL로 바꾸지 않는다.

원문 Pod·env·annotations·토큰·기동 인자는 출력하지 않는다. owner는 기존 정규 해시만
남긴다. Pod spec과 실제 mount flags·엔진 worker의 접근 결과·독립 프로브 결과는
서로 다른 사실이다. 현재 공통 reader는 원문 Pod pair 소비·기존 EVD 관측 연결을
공개하지 않아 수집/제품 연결은 제안 상태다. 공통 reader를 복제하거나 별도 kubectl을
호출하지 않는다. 후속 구간 실패가 이미 저장된 독립 증거·첨부·FAIL을 지우지 않아야 하며,
그 EVD/RES/RPT·첨부 실패/정확 Ref 회귀는 A-1 통합 head에서 확인해야 한다.

## GPU 승인 후

GPU 이미지의 repository@digest와 실제 image ID 의미, vLLM/source commit 및 profile8의
source26 파일 hash를 CPU 계약과 대조한다. 일치하지 않으면 공통 계측 등록을 A-1에
요청한다. CPU 이미지 제한을 풀거나 새 GPU 계약을 임의 등록하지 않는다.
선정 GPU/driver·기동 argv·mount·모델 revision/all inventory와 실제 worker의 PID/start·
모델 세대·읽은 bytes/적재 tensor·rank/shard를 확인한다. TP=1·GPU1장으로 TP 내부 보호를
실증했다고 주장하지 않는다. AWS/GPU 실행·모델 다운로드는 이 준비 흐름에서 하지 않는다.
