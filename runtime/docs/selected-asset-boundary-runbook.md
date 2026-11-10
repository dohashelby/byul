# 선정 모델·파생 캐시 경계의 후속 인수

대상은 SUP003/005의 호스트 공급원·필수 파일, SUP007의 실제 cache parent/다른 UID, SUP015의 선언 세대와 실제 cold/warm이다. 기존 `runtime_asset_check.py`·`check_cache`·`assets.snapshot/check`를 사용한다. 실행자는 root/B가 소유한 LAB actor이며 D는 명령과 검토된 helper만 전달한다.

## 1. 기존 근거 재사용과 읽기 전용 수집

기존 34파일 검사·actual Pod binding·모델 EROFS가 같은 현재 세대에 있으면 반복하지 않는다. Pod가 바뀌면 기존 bridge로 갱신하고 before/after의 Pod UID·containerID·image·startedAt을 보존한다. `--model-host-path`는 `/models`가 아니라 승인 hostPath다. 수집 프로세스에서 보는 파일 경로와 hostPath의 같은 저장물 연결도 기록한다.

아래 변수는 공개 파일/경로만 지정한다. 토큰·키·Secret 내용은 인자/환경/출력에 넣지 않는다. `lab_evidence`는 active 모델/cache 밖에 있는 새 전용 결과 디렉터리다. input JSON은 immutable 사본으로 사용한다.

```sh
lab_source=/home/ubuntu/byul-runtime/REVIEWED_SOURCE
lab_python=/home/ubuntu/byul-runtime/venv/bin/python
lab_evidence=/home/ubuntu/byul-aws-lab/evidence/NEW_D_ASSET_RUN
lab_intent=$lab_evidence/approved-intent.json
lab_model_root=/srv/serving/models/APPROVED_REVISION
lab_bridge=$lab_evidence/asset-bridge-report.json

sudo env PYTHONPATH="$lab_source/runtime/src:$lab_source/update/src" \
  "$lab_python" "$lab_source/runtime/tools/selected_asset_boundaries.py" inspect \
  --intent "$lab_intent" --model-host-path "$lab_model_root" \
  --asset-report "$lab_bridge" --engine-gid 0 \
  --trusted-model-writer-uid 0 --trusted-model-writer-uid 1000 \
  --report-out "$lab_evidence/host-metadata-new.json"
```

`1000`은 이 LAB의 Ubuntu 운영자라는 **선언**이다. 실제 운영자/취득 UID가 다르면 해당 공개 선언과 값으로 바꾼다. 입력 intent의 engine UID를 사용하며 GID는 현재 Pod의 effective groups 근거와 대조한다. 이전 선정 SG의 실제 UID2000/GID0을 기본 예시로 사용하며 다른 배포는 관측한 GID로 바꾼다. UID2000/GID0은 root UID와 구별한다. helper는 supplementary groups를 비워 host namespace에서 root/engine/denied UID의 `os.access`만 조회한다. 각 ancestor 및 기존 asset report 승인 파일 목록 전체의 owner/group/mode/device/inode를 남기며 symlink에서는 중단한다. 승인 파일별 owner/mode는 실제 바이트를 읽지 않고 디렉터리 FD와 no-follow stat으로 수집한다. 실제 ACL·Pod RO mount와 이 host DAC 관측은 각각 판정한다. `observed`/exit0은 metadata 수집 성공이며 자동 CHK PASS가 아니다.

현재 asset report의 intent SHA/model hostPath/cache generation과 불일치하면 `source_binding_state=mismatch`·`state=unknown`이다. `check_cache`의 권한문제/다른세대/미검사는 원결과 그대로 남는다. 보고서에는 기존 asset integrity·ready 상태와 actual engine cache consumption 미확인을 보존한다.

## 2. 다른 UID의 무해 쓰기·교체·읽기: 새 격리 사본만

```sh
sudo env PYTHONPATH="$lab_source/runtime/src:$lab_source/update/src" \
  "$lab_python" "$lab_source/runtime/tools/selected_asset_boundaries.py" isolated \
  --intent "$lab_intent" --model-host-path "$lab_model_root" \
  --sandbox-root /var/tmp/byul-selected-assets-NEW_UNIQUE_RUN \
  --engine-gid 0 --denied-uid 65534 \
  --report-out "$lab_evidence/isolated-uid-proof-new.json"
```

sandbox는 존재하지 않는 절대 경로이며 root 소유·역할이 통과할 수 있는 안전한 parent 아래에 둔다. 기존 경로 재사용·원 모델/cache와 경로/alias 겹침을 거부한다. helper는 새 subtree 안에서만 root/engine UID/65534의 실제 UID 전환을 수행한다. 자식 환경은 PATH/locale만 포함한다. root write, engine 정상 read/write, 비허용 overwrite/rename/symlink/chown와 engine 재읽기를 기록한다. ENOENT·UID 전환 실패·probe 실행 실패는 거부 PASS가 아니다. owner 변경/경로교체가 실제 허용되면 실패를 보존한다.

fixture 내용은 공개 고정 문자열이다. `fixture_asset_check.intact=false`는 허용 engine 추가 파일이 원 manifest에 없기 때문일 수 있으며 원결과를 그대로 보존한다. `original_fixture_unchanged`와 read hash를 따로 검증한다. sandbox 결과가 passed여도 native compiler 오염 소비 차단을 주장하지 않는다. 현 선정 경계가 다른 UID의 변경 자체 거부 방식이면, 실제 cache parent 권한·UID·mount 연결과 이 동일 DAC fixture 결과를 함께 기록한다. 같은 UID 공격은 조건부 지원으로 남긴다. fixture는 증거 보존을 위해 자동 삭제하지 않는다.

## 3. SUP015 실제 조건변경·cold/warm: root actor 실행 순서

새 배포/승인 권한을 만들지 않는다. 원 bundle/intent/기존 cache를 보존하고 기존 exporter/bundle 도구로 **동일 승인 내용의 새 LAB 선언**과 하나의 새 compile profile/cache leaf를 준비한다. 기존 compile 관련 argv와 env가 바뀌지 않았는지 B의 승인 launch와 대조한다. compiler profile 이름만 바꾸고 실제 관련 조건은 그대로면 경로 격리 시험으로 기록하며 실제 compiler 조건변경 재생성으로 세지 않는다.

1. 승인 image/model/실제 compiler 옵션 중 선정한 관련 조건 하나와 대응 profile을 명시한다. 기존 조건 G0의 cache·정상 receipt가 있으면 재사용한다. 새 G1 leaf는 비어 있고 source copy0/symlink0이며 UID/mode/parent metadata를 수집한다.
2. B가 G1 hostPath를 `/cache`에 연결한 새 Pod를 기동한다. 기존 `runtime_asset_check.py`로 actual command/args/image/model/mount/generation을 검사하고 그 actual Pod를 정상 요청/engine receipt와 결합한다. active G0 파일은 쓰거나 지우지 않는다.
3. root actor가 기존 정상 요청 도구로 고정 normal/SSE를 보낸다. 시작 전/후의 G1 파생 파일 목록·hash/size는 기존 `serving-runtime assets snapshot --root G1 --out NEW_FILE`로 기록한다. 빈 디렉터리는 이 도구가 오류로 반환하므로 시작 전 0건의 별도 `find`/stat 원결과를 남기며 빈 manifest를 승인으로 만들지 않는다.
4. 같은 조건의 재기동/정상 요청을 수행해 실제 engine cache 선택·읽기/rebuild 로그 또는 기존 계측 근거를 확보한다. 파일 생성과 정상 HTTP만 있으면 생성·정상 기능까지만 확인한다. source-backed cache-read/rebuild witness가 없으면 재사용은 UNKNOWN이다. 원 engine 로그의 Secret/admin 값은 기존 공개 allowlist/비밀 메모리 scan을 거친 선택 필드만 반출한다.
5. unrelated 변경은 cache 관련 조건이 동일한 사본의 관측 전용 run/표시 metadata 변경으로 한정한다. 원 generation이 유지되고 normal인 대조를 기록한다. cache 조건/명령이 함께 바뀌면 이 대조에 쓰지 않는다.
6. 모든 before/after의 intent/target/approved launch/POD SHA·UID/CID·현재 generation과 결과를 보존한다. mismatch/미계측/기동 실패를 정상으로 덮지 않는다. 복귀/cleanup은 root/B 기존 절차로 수행한다.

## 4. SUP003 누락 파일과 LIF009 인수 재사용

원 모델을 수정하지 않는다. root/B가 별도 1GB 수준의 일반 파일 사본을 만들고 필수 `config.json` 하나만 그 **새 사본**에서 누락한다. 원 inode를 공유하는 hardlink를 수정하거나 원판본의 chmod/chown을 바꾸지 않는다. source 인벤토리와 새 사본의 기존 asset checker 결과에서 missing1을 확인한다. selected 사본을 실제 `/models`로 선택한 별도 Pod/attempt에서 읽기·readiness false·Gateway 보류/미전달 및 engine 원오류를 대조한다. 원 config를 새 사본에 복원/새 정상 사본으로 교체한 뒤 정상기동/receipt를 확인한다. D의 CPU hash negative만으로 이 actual use-refusal을 닫지 않는다. startup attempt를 나눠 정상복구와 실패를 각각 기록한다.

## 판정 경계

SUP005 host metadata/readonly access, 기존 actual model EROFS, isolated root/engine/other-UID fixture는 별도 근거다. SUP007은 registered actual cache의 권한 연결과 무해 fixture 변경 거부까지만 닫는다. SUP015는 실제 조건변경 생성/선택/읽기 범위에 맞춰 판정한다. 전체 dependency 서명·동일 UID 위협·모든 compiler key provenance·전체 GPU 회수는 이 도구의 새 요구가 아니다.
