# 런타임 노드 설치

AWS 5–8회차의 계정·권한·baseline·systemd 수동 절차를 묶은 **호스트 설치기**다. 클러스터, 엔진, Gateway, sentinel, CRI 도구를 배포하지 않는다. 플랫폼·에이전트는 같은 판을 사용한다. 실제 서버 설치·재부팅 검증은 별도다.

1. Python 3.12 이상과 venv 모듈, sudo(인자 정규식 지원 1.9.10 이상), systemd/systemd-analyze, visudo, kubectl, 계정 관리 도구를 준비한다. `crictl`이 없으면 설치기는 중단한다. 운영자가 버전·체크섬을 확인해 별도 설치하고 `/etc/crictl.yaml`의 CRI endpoint를 검수한다. 설치기는 자동 다운로드하지 않는다.
2. 저장소 밖에 `install.env.example`을 복사해 **0600**으로 만든다. 설정은 literal `KEY=value`이며 따옴표·변수 치환·`export`·인라인 주석을 지원하지 않는다. 토큰과 viewer 입력도 0600, kubeconfig·공개 CA 입력은 소유자 외 쓰기가 없는 일반 파일이어야 한다. 모든 입력은 절대 경로이며 링크를 거부한다. `KUBECONFIG_FILE`은 필수다. kubeconfig는 인증서를 내장하거나 `/etc/serving-runtime` 안의 파일을 참조해야 한다. 홈 경로·외부 credential 실행 플러그인은 별도 검수한다.
3. `OBSERVATION_USER`는 기존 별도 수집기 계정(현재 서버 `ubuntu`)이다. GID 10000/10010은 각각 `serving-bundles`/`serving-observe`여야 하며 다른 그룹과 충돌하면 중단한다. 다른 GID를 쓰면 Gateway 보조 그룹과 사용자 수집기도 함께 맞춘다. Gateway UID·`--trusted-owner-uid`는 배포 정의의 **10000**을 유지한다. 설치 원본과 `/opt/byul/runtime`는 서로 다른 경로여야 한다.

입력 파일의 소유자는 root 또는 설치 수행자여야 한다. `sudo` 실행 시 원래 호출자의 `SUDO_UID`도 허용하므로 같은 0600 입력으로 점검과 실제 설치를 실행할 수 있다.

```bash
# runtime/에서 실행; 기존 설치가 있거나 호스트 파일 읽기 권한이 부족하면 sudo로 점검한다.
sudo bash deploy/install/install-runtime-node.sh --config /secure/install.env --dry-run --capture-baseline
# 정상 monitor 정책의 spec·범위·mode를 먼저 검수한 뒤 최초 설치
sudo bash deploy/install/install-runtime-node.sh --config /secure/install.env --capture-baseline
# 코드 갱신 등 다시 설치할 때도 기본은 --execute 없이(에이전트 dry-run 모드로 설치)
sudo bash deploy/install/install-runtime-node.sh --config /secure/install.env
# 운영 실행이 승인된 경우에만 설치할 에이전트를 execute로 설정
sudo bash deploy/install/install-runtime-node.sh --config /secure/install.env --execute
# 에이전트는 설치기가 켜지 않는다. 사람이 확인한 뒤 직접 시작한다.
sudo systemctl enable --now serving-runtime-agent
```

**에이전트는 자동으로 켜지지 않는다.** 설치기는 에이전트 단위 파일만 설치하고, 마지막 단계에서 `disable`해 부팅 때도 켜지지 않게 둔다. 모드(`--dry-run`/`--execute`)와 상관없다. 에이전트는 승인된 요청으로 서버를 바꿀 수 있으므로, 플랫폼 연결·승인 대기 요청을 확인한 사람만 위 명령으로 시작한다. 다시 설치하면 에이전트는 다시 꺼진 상태가 된다.

설치기의 `--dry-run`은 **호스트 무변경 사전 점검**이다. 계정/파일/서비스/GRUB 변경, 클러스터 조회·접속, 다운로드를 하지 않는다. 실제 `visudo`·systemd 검증과 계정에서의 읽기·정책 수집은 설치 때 수행한다. `--execute`는 설치될 agent의 실행 모드만 바꾸므로 `--dry-run --execute`로 점검할 수 있다. 에이전트 기본은 기존 운영 값의 `--dry-run`이며 LAB 자기 승인·감시 기간 단축을 넣지 않는다. 입력 값, 파일 내용, 렌더링 결과, 외부 명령의 stdout/stderr는 출력하지 않는다. 셸 tracing을 켜지 않는다.

일반 계정의 점검은 필요한 호스트 파일을 읽을 수 있을 때만 가능하다. 기존 `/var/lib/serving-runtime`·`/etc/serving-runtime`·`/etc/sudoers.d` 등의 접근이 거부되면 `PERMISSION_DENIED`로 중단하므로 sudo로 다시 점검한다. 실패 출력은 단계 번호·점검 이름·이유 코드와 고정된 설명을 제공한다(0/9는 입력·사전 점검·설치 잠금/서비스 중지). 예: `단계 4/9; 점검 SUDOERS_VALIDATE; 이유 VISUDO_FAILED`, `PERMISSION_DENIED:/var/lib/serving-runtime`, `COMMAND_FAILED:systemctl`. 권한 오류의 경로는 허용된 고정 시스템 경로만 표시하며 입력 경로·설정 값·토큰·명령 출력은 숨긴다.

설치 전 `/etc/sudoers.d`의 관리 파일 `serving-runtime-agent` 이외 파일에서 `BYUL_BUNDLE_APPLY` 또는 `runtime-agent` 규칙을 발견하면 `SUDOERS_ALIAS_CONFLICT` 또는 `SUDOERS_RULE_CONFLICT`와 **파일 이름만** 보고하고 중단한다. 주석은 제외하며 백업 파일도 점검한다. 자동 삭제하지 않으므로 운영자가 기존 규칙을 검수·통합한 뒤 재실행한다. sudoers를 설치한 뒤 전체 `visudo -c`가 종료 0이고 stderr 경고가 없어야 systemd 설치·서비스 시작을 진행한다. 실패 시 설치한 sudoers는 남으므로 아래 백업·복원 절차를 따른다. context는 검증된 식별자에서 `-`·`_`를 유지하고 정규식의 `.`만 `\.`로 이스케이프한다.

실제 설치는 세 서비스를 먼저 중지하고 계정 → root 소유 코드/venv/.pth → 비밀 설정 → sudoers → systemd → 공유 폴더 → baseline → 서비스 순으로 진행한다. Python 의존성이 없는 현재 runtime에 맞춰 `venv --without-pip`와 두 경로의 `.pth`를 사용하므로 패키지 다운로드도 없다. 동일 파일은 교체하지 않고 권한을 검증한다. 설정 파일은 root 소유 디렉터리(0750 root:runtime-agent)에 두며, agent.token만 0600 runtime-agent, viewer.env/kubeconfig/agent.env/선택 CA는 0640 root:runtime-agent다. 설치 경로의 링크, root 소유/go-w 위반은 거부한다. 실패하면 즉시 중단하며 이미 적용한 단계는 남으므로 원인을 수정한 뒤 같은 명령을 다시 실행한다.

BPF LSM이 활성 목록에 없으면 기본 중단한다. 유지보수 승인을 받은 운영자가 `--enable-bpf-lsm`을 명시하면 커널 지원을 확인하고 현재 목록의 순서를 유지하며 `bpf`를 추가한다. 기존 다른 GRUB 파일의 `lsm=` 인자가 있으면 자동 통합하지 않고 중단한다. 관리 파일을 쓰고 `update-grub`을 통과하면 **종료 2(재부팅 필요)**를 반환한다. 자동 재부팅·서비스 시작·baseline 캡처는 하지 않는다. 운영자가 재부팅 후 같은 명령을 다시 실행한다. 기존 관리 파일도 내용이 같을 때만 재사용하며 다른 설정으로 덮어쓰지 않는다.

baseline이 없을 때만 `--capture-baseline`을 사용한다. runtime-agent로 `umask 022`를 명시하고 후보 파일에 캡처한 뒤 monitor/monitor_only 정책·context·spec 해시를 검증해 채택한다. 기존 baseline은 검증하고 보존한다. 재캡처가 필요하면 서비스를 중지하고 기존 baseline을 별도로 보관·제거한 뒤 정책을 다시 검수한다. 실패 시 남은 `/var/lib/serving-runtime/baseline.capture.json`도 검수·제거해야 한다. baseline 캡처는 실제 클러스터 **조회**를 수행하지만 정책을 변경하지 않는다.

수집 서비스(status → gpu-status → monitor-window)만 enable/start하고 에이전트는 disable·중지 상태로 둔다. 활성 여부와 최신 상태의 복구 상태·4조건 충족 수만 출력한다. `active`와 `recovering`을 준비 완료로 해석하지 않는다. 기존 실측 evidence, 관측 feed·식별 자료·sentinel·Gateway 관측 JSONL이 있어야 복구가 healthy가 되고 모니터 창이 쌓인다. 이들 파일이 없으면 기존 운영 안전 조건이 전환을 거부한다. 서비스 시작 실패는 종료 1, 서비스 활성·설치 완료는 종료 0이다. 복구 unknown/recovering은 운영자가 최신 상태를 다시 확인해야 한다.

## 별도 사용자 서비스

공유 폴더 단계 `6–7/9`의 `VERIFICATION_PERMISSIONS`는 `/srv/serving/verification`을 root 소유·OBSERVE_GID·2750으로, `approved`를 root 소유·GATEWAY_DATA_GID·2750으로, `candidates`를 OBSERVATION_USER 소유·OBSERVE_GID·2750으로 멱등 준비한다. 계정 단계는 OBSERVATION_USER의 관측 그룹 가입도 확인·보완한다. 기존 사용자 서비스는 재시작 후 그룹을 확인한다. 승인 파일은 자동 생성/교체하지 않는다. [업데이트 검증 후보와 승인](../../docs/업데이트검증_후보와승인.md)의 도구가 후보/승인 문서·검토 기록을 명시 0640으로 원자 저장한다. Gateway에는 approved만 읽기 전용으로 붙인다. 모델 파일의 UID 10000 읽기 권한과 엔진/Gateway의 snapshot 일치는 운영자가 확인한다. 모델/구성 기준선은 설치기의 Tetragon baseline과 별개다. 새 Gateway의 폐기 목록 인자를 적용하기 전에 빈 목록 여부까지 사람이 검토·게시해야 하며, 파일 없음/읽기 오류는 기존 규칙대로 추론을 비활성화한다.

`byul-observation-feed`·`byul-tetragon-identities`는 현재 서버처럼 **ubuntu 사용자 서비스**로 유지한다. 설치기는 이 둘을 중지·설치·시작하지 않는다. [GPU LAB README](../gpu-lab/README.md)의 식별 수집 절차에 따라 설치기의 root 소유 코드·context·namespace·GID를 맞추고 사용자 manager의 그룹과 필요 시 linger를 검수한다. `/srv/serving/observation`의 소유자는 `OBSERVATION_USER`, GID는 10010, mode는 2750이며 새 식별 자료는 명시 0640이다. 실제 사용자 서비스 재시작 뒤 폴더 setgid와 파일 GID·0640, Gateway 읽기를 확인한다. 같은 경로에 수집기를 중복 실행하지 않는다.

설치기는 관측 폴더 하위 `tetragon-status`를 runtime-agent 소유·관측 GID·2750으로, `lab-reports`를 OBSERVATION_USER 소유·관측 GID·2750으로 준비한다. 상위 2750에는 runtime-agent의 쓰기 권한이 없으므로 상태 전용 하위 폴더를 사용한다. 상태 서비스의 `--public-output`과 `ReadWritePaths`는 그 하위 폴더로 제한한다. 원본 `/var/lib/serving-runtime/tetragon-state.json`과 baseline/evidence 권한·위치는 유지한다. 공개 사본만 명시 0640으로 원자 교체하며 부모 setgid와 그룹을 보존한다. 최초 baseline 캡처에도 공개 사본 옵션을 쓴다.

공개 사본은 허용 필드만 남기고 노드명·Pod UID·컨테이너 ID를 사본 내 별칭으로 바꾼다. raw 명령/출력·임의 부가 필드·오류 원문·복구 epochs는 내보내지 않는다. 모드·spec 해시·커널 기능과 재판정에 필요한 시험 이벤트/수치·시각은 유지한다. 별칭은 호스트의 실제 식별값이 아니며 다른 회차와 대조할 식별자로 사용하지 않는다. 상세 오류·원본 근거는 호스트 전용 기록에서 확인한다. Gateway는 기존 관측 readOnly mount로 사본을 읽으며 root 상태 폴더나 Kubernetes 자격을 받지 않는다.

Lab 보고서는 자동 생성하지 않는다. [GPU LAB의 보고서 생성 절차](../gpu-lab/README.md#대시보드-상태와-lab-보고서-연결)에 따라 운영자가 유지보수 시 실행하고 `lab-reports`에 0640 최소 JSON을 게시한다. 새 설치·아직 시험하지 않은 환경은 Gateway API에서 `missing`이다. 코드·상태 서비스·Gateway 이미지를 같은 판으로 갱신하고 수집기 재시작 뒤 Gateway 경로와 그룹 읽기를 확인한다.

## 되돌리기

설치 전 `/etc/systemd/system/serving-runtime-{agent,tetragon-status,monitor-window}.service`, `/etc/sudoers.d/serving-runtime-agent`, `/etc/serving-runtime`, `/opt/byul/runtime`, 관련 폴더 소유자·권한 및 세 서비스의 활성/enable 상태를 root 전용 장소에 백업한다. 토큰이 포함된 백업은 출력하거나 Git에 넣지 않는다.

서비스 세 개를 `systemctl disable --now`로 중지한 뒤 기존 단위·코드·설정을 복원한다. sudoers 복원본은 반드시 `visudo -cf`를 통과한 뒤 설치하고 `systemctl daemon-reload`한다. 신규 설치 제거 시에는 이 설치기가 만든 세 단위와 sudoers만 제거한다. 원래 활성/enable 상태는 기록에 맞춰 복구한다. 비밀 설정과 코드 삭제는 경로를 직접 확인한 후 수행한다. 공유 데이터·묶음·baseline/evidence와 사용자 수집기는 보존하고, 계정·그룹은 다른 사용자가 없음을 확인하기 전 삭제하지 않는다. BPF LSM 변경을 되돌리려면 관리 파일만 백업/제거하고 기존 GRUB 설정을 복원해 `update-grub` 후 유지보수 재부팅한다. 자동 uninstall·자동 재부팅은 제공하지 않는다.

근거: [AWS 5회차](../../docs/AWS_GPU_서버5회차_결과_20261106.md), [6회차](../../docs/AWS_GPU_서버6회차_결과_20261108.md), [7회차](../../docs/AWS_GPU_서버7회차_결과_20261108.md), [8회차](../../docs/AWS_GPU_서버8회차_결과_20261108.md), [원격운영 §14](../../docs/원격운영_사용과검증.md#14-운영-배치-수집기https토큰).

## Scoped Tetragon stream으로 명시 전환

상태 writer는 설치기의 `serving-runtime-tetragon-status.service` 하나다. root 소유 `/opt/byul/runtime` 코드·venv, `/etc/serving-runtime/kubeconfig`, `/var/lib/serving-runtime/baseline.json`·evidence와 기존 복구 4조건을 유지한다. 이전 LAB `byul-tetragon-status.service`는 설치 중 disable/stop 및 중지 확인 후 canonical unit으로 대체한다. 이전 `/srv/serving/runtime/data`의 baseline·evidence는 삭제·자동 병합하지 않는다. 기존 canonical baseline이 없으면 정상 최초 캡처·검수 절차를 따른다.

`byul-observation-feed` template은 기존 Hubble+legacy Tetragon 기본 동작을 유지한다. template을 복사하는 것만으로 Tetragon을 끄지 않는다. 전환 시 기존 observation owner로 다음 한 명령을 실행한다. root system 설치기를 먼저 완료하고, `prepare_tetragon_deployment`로 생성·검토한 정책 이름과 실제 적용 정책·sentinel·현재 context를 대조한다. 이 명령은 클러스터 정책과 canonical 상태 서비스를 변경하지 않는다.

기존 user feed가 `/usr/bin/python3`나 `%h` 코드 사본을 사용한다면, 먼저 동일 observation 사용자로 unit만 이행한다. 기존 unit과 실행/enable 상태를 사용자 전용 0700 백업 위치에 보존하고, root 설치기에서 복사한 새 template이 legacy Tetragon을 계속 포함하는지 검토한다. 다음 단계는 새 template의 기존 context·output·reader-group·selector·interval을 해당 서버의 원래 값과 대조한 뒤 수행한다. 값이 다르면 검토된 같은 값으로 조정하고 기존 unit을 덮기 전에 백업한다.

```sh
byul_unit_backup=$(mktemp -d "$HOME/.byul-observation-backup.XXXXXX")
chmod 700 "$byul_unit_backup"
cp -p "$HOME/.config/systemd/user/byul-observation-feed.service" "$byul_unit_backup/"
chmod 600 "$byul_unit_backup/byul-observation-feed.service"
systemctl --user is-active byul-observation-feed.service > "$byul_unit_backup/active-state.txt"
systemctl --user is-enabled byul-observation-feed.service > "$byul_unit_backup/enabled-state.txt"
cp /opt/byul/runtime/deploy/gpu-lab/host/byul-observation-feed.service "$byul_unit_backup/reviewed-feed.service"
```

`reviewed-feed.service`의 context·output·reader-group·selector·interval을 기존 unit과 대조·조정한 뒤 다음을 실행한다. interpreter와 코드 경로는 `/opt/byul/runtime`을 유지한다.

```sh
cp "$byul_unit_backup/reviewed-feed.service" "$HOME/.config/systemd/user/byul-observation-feed.service"
systemctl --user daemon-reload
export BYUL_FEED_RESTARTED_AT=$(date +%s)
systemctl --user restart byul-observation-feed.service
systemctl --user is-active --quiet byul-observation-feed.service
# 새 프로세스의 Hubble/Tetragon heartbeat를 최대 30초 기다려 검증한다.
/opt/byul/runtime/.venv/bin/python - <<'PYVERIFY'
from datetime import datetime
import json, os, time
from pathlib import Path
started = float(os.environ['BYUL_FEED_RESTARTED_AT'])
for attempt in range(30):
    try:
        rows = [json.loads((Path('/srv/serving/observation') / (kind+'.heartbeat.json')).read_bytes()) for kind in ('hubble','tetragon')]
        def valid(row):
            at = datetime.fromisoformat(row['updated_at'].replace('Z','+00:00')).timestamp()
            began = datetime.fromisoformat(row['started_at'].replace('Z','+00:00')).timestamp()
            return (row['state']=='running' and row['kube_context']=='byul-aws-lab' and started <= began <= at <= time.time() and time.time()-at <= 10 and bool(row['processes']) and all(p['running'] is True for p in row['processes']))
        if all(valid(row) for row in rows):
            break
    except (OSError, ValueError, KeyError, TypeError):
        pass
    time.sleep(1)
else:
    raise SystemExit('Legacy feed freshness failed: restore the backed-up unit before rollout.')
PYVERIFY
```

이 단계는 legacy 수집을 유지하며 source 경로만 root 설치본으로 이행한다. restart가 실패하면 백업 unit을 원래 위치에 복원하고 daemon-reload/restart한 뒤 active를 확인한다. 이행 완료 후에만 아래 rollout을 **같은 observation 사용자**로 실행한다. root와 관측 디렉터리 소유자가 아닌 UID는 잠금 파일 생성 전 거부한다.

```sh
/opt/byul/runtime/.venv/bin/python /opt/byul/runtime/deploy/install/rollout_tetragon_stream.py --prepared-dir /path/to/reviewed-prepared-directory
```

기존 사용자 feed가 active이고 별도 stream unit이 없는 최초 전환만 허용한다. 기존 feed 파일은 그대로 보존하고, feed를 중지한 뒤 해시가 일치하는 prepared stream을 시작한다. 현재 전환 이후 시작한 heartbeat가 10초 이내이며 올바른 context·feed 경로·running 프로세스·해소된 gap과 session namespace/정책 목록을 만족해야 `--hubble-only`를 기존 feed 인자에 추가한다. feed를 재시작한 뒤 stream freshness를 다시 확인하고 stream을 enable한다. 기존 사용자 manager의 kubeconfig와 권한은 유지한다. stream에는 기존 Tetragon agent discovery·scoped exec 권한이 필요하며 설치기는 RBAC를 확대하지 않는다.

진행·실패는 `/srv/serving/observation/tetragon-rollout.json`에 남긴다. 실패하면 새 stream을 중지·disable하고 기존 feed의 정확한 바이트를 복원·재시작해 active를 확인한다. 복구 실패는 `failed_manual_recovery_required`와 종료 1로 남긴다. 이 경우 두 수집기의 상태·로그를 비밀값 없이 검수하고 기존 feed를 복구한다. 기존 heartbeat가 남아 있거나 source scope가 다르면 완료로 처리하지 않는다. 전환 중 gap은 연속 관측 성공으로 해석하지 않는다.

전환 성공은 수집 서비스 연결 결과다. 기존 canonical 상태의 baseline 일치·writer/identity/event 복구 조건, Gateway freshness와 공개 사본 읽기 확인은 계속 필요하다. 새 unit의 코드는 사용자 홈 사본 대신 설치기의 root 소유 `/opt/byul/runtime`을 사용한다.

## 선택 캐시 관측

[캐시 관측 설정](../../docs/cache-observer.md)의 root 승인 파일·전용 namespace 한정 read-only kubeconfig를 준비하고 `install.env.example`의 CACHE 세 필드를 함께 채운다. 비어 있으면 설치/시작하지 않는다. 기존 agent 수동 시작, baseline 보존, BPF 재부팅 필요 시 수집 서비스 중지 규칙은 그대로 적용한다.
