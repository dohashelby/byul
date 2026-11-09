# AWS GPU lab — runtime 배포 층 (초안)

AWS 공통 GPU 시험환경 설치 묶음(`aws-handoff-20261026`, 이하 "AWS 묶음") **위에 얹는** runtime 쪽 파일이다. 엔진·namespace·계정·엔진 격리 정책은 AWS 묶음이 만들고, 여기서는 Gateway·관리 수신부·observer 자리·추가 통신 정책·intent·호스트 수집기만 더한다. 2026-11-01 g6.2xlarge dev에서 AWS-01~08을 단계별로 통과했다(Claude 실행, Codex 교차 검수 전). 이는 기능 연결 확인 1회이며 reference 정식 평가가 아니다. 적용 규칙·제외 규칙·결과와 한계는 [`ACCEPTANCE.md`](ACCEPTANCE.md)에 있다. 저장소의 서버별 값은 `REPLACE…`로 비워 두고 서버 사본에서 채운다.

## 다음 회차: 업데이트 전후 검증 자료 연결

1순위 Tetragon 상태·Lab 보고서와 함께 확인한다. 명령·후보 형식·승인 게시 절차는 [업데이트 검증 후보와 승인](../../docs/업데이트검증_후보와승인.md)에 있다. 승인 기준은 Claude/사용자가 다음 회차에 결정한다. 이번 저장소 작업에서 AWS 실행·승인 자료 생성은 하지 않았다.

**12회차 필수 순서: 설치기 재실행으로 폴더 준비 → vLLM 실제 모델 hostPath 확인·Gateway 서버 사본과 일치 → 폐기 목록 승인본 게시 → 사전 점검 성공 → Gateway 적용/재시작.** 저장소의 모델 경로는 SGLang 매니페스트에서 확인한 값이며, vLLM 엔진의 실제 모델 hostPath는 12회차에서 kubectl로 확인 후 일치시킬 것. 모델 교체 때도 같은 절차를 반복한다. 아래 명령은 다음 회차용이며 이번 작업에서는 실행하지 않았다.

```bash
# 12회차: 엔진의 /models mount 이름과 해당 hostPath를 대조(주소/env/Secret 조회 안 함)
kubectl --context byul-aws-lab -n serving-lab get pods -l app=serving-engine -o jsonpath='{range .items[*]}{range .spec.containers[?(@.name=="engine")].volumeMounts[*]}{.name}{" "}{.mountPath}{" subPath="}{.subPath}{"\n"}{end}{range .spec.volumes[*]}{.name}{" hostPath="}{.hostPath.path}{"\n"}{end}{end}'
# 실제 확인한 값으로 바꾸고, 최종 Gateway 서버 사본의 model.hostPath.path도 동일하게 설정
MODEL_HOST_PATH=/REPLACE_WITH_CONFIRMED_MODEL_HOSTPATH
# 승인본 게시 후 Gateway가 배치될 호스트에서 실행. 실패하면 && 뒤의 적용은 실행하지 않음
sudo /opt/byul/runtime/.venv/bin/python -B -m tools.gpu_lab_gateway_preflight \
  --approved-dir /srv/serving/verification/approved --model-root "$MODEL_HOST_PATH" &&
  kubectl --context byul-aws-lab -n serving-lab apply -k deploy/gpu-lab
```

사전 점검은 두 hostPath 디렉터리 존재/형식과 `revocations.json`의 읽기·기존 Gateway 형식을 검사한다. 누락/오류는 종료 1과 고정 이유 코드로 중단하고 파일·빈 목록·승인을 만들지 않는다. root의 로컬 점검은 실제 Pod UID 읽기·노드 배치·실엔진 경로 일치까지 증명하지 않으므로 별도로 확인한다. 재시작 전에도 같은 점검을 먼저 실행한다. 모델 경로 자동 반영은 이번에 구현하지 않았으며, 후속으로 확인한 경로 하나에서 Kustomize patch와 점검 인자를 함께 생성하는 적용 도구를 제안한다.

1. **자산:** 설치기로 `verification/{candidates,approved}`(2750)를 준비하고, 실행 엔진의 `/models` hostPath가 Gateway의 모델 snapshot 경로와 같은지 확인한다. `/models`에 tokenizer도 있으며 `/cache`는 가변 캐시다. UID 10000의 모델 읽기·쓰기 거부를 확인한다. 새 `tools.verification_material assets`로 실제 파일 해시 **후보**를 만들고 외부 근거와 검토한 뒤 `approve`로 게시한다. 승인본 없는 collector의 `ASSET_MANIFEST_NOT_APPROVED`·미설정/미승인 → 게시 후 일치/변경 표시를 확인한다. 원본 모델을 변조하지 말고 별도 시험 snapshot으로 변경/누락/읽기 오류를 점검한다.
2. **기준/선언:** 환경 후보의 확인 필드로 `declaration --kind baseline`과 `--kind manifest` 후보를 각각 만들고 승인할 필드·값을 검토한다. baseline을 현재값으로 자동 채택하지 않는다. 승인 전 `not_configured`·승인본 없음, 승인 후 Gateway 재시작과 `/api/baseline`·대상 화면의 선언/실측 구분을 확인한다. 업데이트 후 기존 baseline을 보존하고 새 manifest와 비교한다.
3. **폐기 목록:** `approved/revocations.json`을 사람이 검토·게시한다(빈 목록도 명시 승인). **새 배포 인자에서 파일 없음/읽기 오류는 기존 fail-closed 동작으로 추론 503을 만든다.** 운영 재배포 전에 게시하고, 유지보수용 시험 경로에서 파일 없음·읽기 오류·유효한 빈 목록이 화면에 서로 다르게 표시되는지 확인한다. 목록 변경 후 Gateway 재시작이 필요하다.
4. **환경:** `tools.aws_environment_candidate`는 ubuntu로 실행한다. root:serving-bundles·2750 묶음은 serving-observe만 가진 ubuntu가 읽지 못하므로 운영자가 sudo 읽기만으로 같은 current 세대의 target/Gateway policy를 전용 0700 폴더·0400 사본으로 준비한다. [후보 생성 명령](../../docs/업데이트검증_후보와승인.md#후보-생성-runtime-디렉터리에서)을 따른다. 도구 전체를 sudo로 실행하면 ubuntu kubeconfig와 달라질 수 있으므로 피한다. 고정 이유코드·입력이름으로 권한/누락/JSON 실패를 구분하고 환경 후보 성공 후 declaration을 실행한다. 단일 Ready 엔진의 imageID digest, 해당 노드 커널·GPU/드라이버·Tetragon/Cilium 판본을 후보에서 대조한다. 권한 실패·다중 대상은 ‘미확인+이유’이며 선언 digest로 대체하지 않는다. 계약에 자리가 없는 커널/에이전트 판본은 동반 관측 자료에 둔다. 필수 미확인 필드가 생략된 환경 후보는 `import_ready: false`이므로 source commit·모델 출처 revision·사실·승인 intent를 사람이 완성하고 **기존 계약 검증 → 환경/정책 새 판본 → 묶음 내보내기**까지 확인한다. `null` 허용 여부는 다음 회차 결정 사항이며 자동 입력하지 않는다.

5. **외부 규칙 최초 적용:** 승인·검증된 current 묶음과 Gateway 활성 상태를 먼저 확인한 뒤 [외부 규칙 처음 적용·동기화](../../docs/외부규칙_최초적용.md)에 따라 `enforcement.apply`를 요청하고 별도 approver 승인을 받는다. 기본 dry-run 결과를 검토한 뒤 새 요청을 실행한다. Tetragon은 monitor로만 만들며 Cilium 실제 적용은 기본 꺼짐이다. 외부 정책 spec이 바뀌면 수집 상태·baseline·모니터 창·시험 근거를 다시 확인하고, 그 다음 기존 readiness와 `tetragon.mode`로 차단 전환한다. `unknown`은 재실행하지 말고 실제 객체·Gateway·복원을 먼저 확인한다.

Gateway의 새 mount는 모두 readOnly이고 후보 폴더는 마운트하지 않는다. 설치기는 승인본·빈 폐기 목록을 만들지 않는다. 승인 도구가 명시 0640 원자 저장하며, 설치 후 그룹/모드와 Gateway 읽기·쓰기 거부를 확인한다. SGLang은 같은 Gateway 설정을 상속한다.

## AWS 묶음이 이미 만드는 것 (여기서 다시 만들지 않음)

| 항목 | AWS 묶음 파일 |
| --- | --- |
| namespace `serving-lab`, RuntimeClass `nvidia`, SA `engine`·`gateway`·`observer`·`probe`·`artifact-acquire`, observer Pod 조회 Role | `foundation.yaml` |
| `serving-engine` Deployment(UID 2000:0, 공식 비root wrapper, GPU 1, 모델 `/models` 읽기 전용, 캐시 `/cache`, served model `test-model`)·Service 8000 | `engine.yaml.template` (03이 적용) |
| 엔진 ingress는 Gateway→8000만, 엔진 egress 없음 | `engine-isolation.yaml` |
| context 이름 `byul-aws-lab` | `02-cluster.sh` |

## 이 폴더

| 파일 | 내용 |
| --- | --- |
| `kustomization.yaml` | 아래 자원 묶음. `kubectl kustomize deploy/gpu-lab`로 렌더링 |
| `gateway.yaml` | Gateway(SA `gateway`, UID 10000·보조 그룹 10010). 정책 묶음·인증 Secret·관측 기록(읽기 전용)·기록 폴더를 hostPath로 받음 |
| `backend-config.yaml` | vLLM backend 설정(`test-model`) |
| `engine-management-sidecar.patch.yaml` | AWS 엔진 Deployment에 관리 수신부 사이드카(8001)를 더하는 patch |
| `management-service.yaml` | 관리 수신부용 Service `serving-engine-management`(8001) |
| `network-policies.yaml` | observer→8001만 추가 허용(8000은 그대로 Gateway만) |
| `observer.yaml` | observer 자리(SA `observer`, UID 10004) |
| `test-pods.yaml` | 시험 receiver·other-client(SA `probe`, UID 10003) |
| `intent.aws-lab.json` | AWS 값으로 만든 통제 의도(엔진 UID 2000, `/models`, 포트 8000/8001) |
| `target.aws-lab.json` | 정책 묶음의 `target` 역할에 넣을 대상 등록 예시. image digest를 실제 값으로 교체해야 함 |
| `host/byul-observation-feed.service` | AWS 호스트에서 수집기를 계속 돌리는 systemd 사용자 서비스 |
| `host/byul-tetragon-identities.service` | 엔진·proof·sentinel의 procfs 식별 자료를 수집하는 systemd 사용자 서비스 |
| `tetragon-sentinel.json`·`tetragon-sentinel-policy.json` | Gateway 이미지의 Python 3.12로 가짜 파일을 읽는 노드별 sentinel과 Post 전용 TG-HEALTH-01 정책 |
| [`ACCEPTANCE.md`](ACCEPTANCE.md) | AWS 첫 완료 기준 8개 사례의 실행·증거·비용 절약 절차 |
| `policy.aws-lab.json` | 정책 묶음의 gateway policy 역할에 넣을 LAB 시험 정책(아래 "LAB 시험값") |
| `policy-approvals.aws-lab.json` | 위 정책의 LAB 승인 목록(SHA-256 대조). runtime 자체 기록이며 정식 승인 아님 |
| `perf-plan.aws-lab.json` | B0/B1/B2 성능 비교 계획(`tools/perf_compare.py`) |

## BPF LSM 서버 준비와 파일 거부 방식

호스트 계정·권한·에이전트/수집기 systemd 설치는 [노드 설치기](../install/README.md)로 묶었다. 무변경 `--dry-run`, 명시적 BPF LSM 설정, 최초 baseline 캡처와 되돌리기 절차를 제공한다. 아래 사용자 수집기·Gateway·sentinel 배치는 별도로 준비한다.

Tetragon 1.7.1의 `lsm_file_open`은 BPF LSM 활성화가 필요하다. 다음은 서버 관리자가 유지보수 시간에 수행하는 호스트 부팅 설정이며, 이 문서 작성 작업에서는 실행하지 않았다.

1. `uname -r`, `/boot/config-$(uname -r)`의 `CONFIG_BPF_LSM=y`, `cat /sys/kernel/security/lsm`을 확인한다. 현재 목록의 순서를 보존하고 `bpf`가 없을 때만 끝에 추가한다. 기존 GRUB의 `lsm=` 설정이 있다면 중복 인자가 생기지 않게 기존 설정을 통합한다.
2. `/etc/default/grub.d/99-byul-bpf-lsm.cfg`에 다음을 기록한다. `<현재 목록>`은 바로 확인한 쉼표 구분 목록으로 바꾸며, 이미 `bpf`가 있으면 다시 추가하지 않는다.

   ```bash
   GRUB_CMDLINE_LINUX="$GRUB_CMDLINE_LINUX lsm=<현재 목록>,bpf"
   ```

3. `sudo update-grub` 실행 후 `sudo reboot`한다. 재접속 뒤 `/proc/cmdline`, `/sys/kernel/security/lsm`의 `bpf`, Tetragon의 `tetra probe` 출력 `lsm: true`를 확인한다. 설정 파일만 작성된 상태를 활성화로 판정하지 않는다.

방식 추천 순서는 `lsm_file_open` → `override_syscall` → `sigkill_fd_install`이다. 추천은 intent나 기존 묶음을 자동 변경하지 않는다. 사전 조건이 확인된 후보만 추천하며, `fd_install`은 정적 점검만으로 지원을 확정하지 않는다. 실제 정책 로드·파일 거부·monitor 복귀 증거와 운영 안전 조건은 계속 필요하다.

| 방식 | 동작과 한계 |
| --- | --- |
| `lsm_file_open` | `lsmhooks.file_open`의 정확한 파일 경로 + Post·Override(-13). 파일 열기만 거부, 프로세스 유지. 기존에 열린 descriptor는 보호하지 않는다. 7회차에서 상대 경로도 거부됐으나 모든 별칭·파일시스템을 보장한 것은 아니다. |
| `override_syscall` | openat 경로 문자열 + Post·Override(-13). 파일 열기만 거부, 프로세스 유지. 상대 경로·symlink·TOCTOU·openat2·io_uring 등 우회 한계가 있어 포괄적인 파일 보호로 간주하지 않는다. args 2개 + 식별 data 3개로 최대 5개를 지킨다. |
| `sigkill_fd_install` | 해당 프로세스를 종료한다. 엔진 본체 접근이면 엔진 종료·Gateway 503·재시작 위험이 있다. 파일 읽기 거부 증거와 프로세스 종료 증거를 구분한다. |
| 기존 `override_lsm` | security_file_permission kprobe + Override·Sigkill. 새 BPF LSM 방식과 다르며 기존 intent 호환용이다. AWS 7회차에서 security_* error injection 부재로 사용할 수 없었다. |

`lsm_file_open`의 `data`는 [1.7.1 LsmHookSpec](https://github.com/cilium/tetragon/blob/v1.7.1/pkg/k8s/apis/cilium.io/v1alpha1/types.go#L401-L419)에 필드가 없어 생략한다. `process_lsm`에도 반환값·data가 없으므로 커널 반환값만으로 `kernel_read_denied`를 만들지 않는다. native 프로세스·Pod UID·정책·정확한 경로·시간창과 userspace EPERM/EACCES 증거가 필요하며 귀속이 부족하면 `unknown`을 유지한다. trial은 CRI init PID와 native 이벤트 PID도 대조한다.

openat Equal 값 끝의 `\u0000`은 기존 코드가 명시적으로 붙인 종결자다. [1.7.1 writeMatchStrings](https://github.com/cilium/tetragon/blob/v1.7.1/pkg/selectors/kernel.go#L701-L707)는 string 타입에 trimNulSuffix를 켜고, [ArgStringSelectorValue](https://github.com/cilium/tetragon/blob/v1.7.1/pkg/selectors/selectors.go#L353-L359)는 끝 NUL을 제거한다. 따라서 string Equal 비교에는 필수가 아니며 실측에서 매치된 것과도 일치한다. 기존 생성값의 호환성을 위해 유지한다. 제거 대조 실험은 미실행이며, 파일 객체 경로를 쓰는 LSM 값에는 붙이지 않는다.

새 정책은 monitor로 로드한다. 기존 syscall+Sigkill 묶음은 바꾸지 않으며 수집한 실제 action의 `contains_sigkill`에 따라 화면·API·에이전트가 `ENGINE_RESTART` 확인을 요구한다. 이전 상태 자료에 이 정보가 없으면 기존 Sigkill 계열 방식의 확인을 보수적으로 유지한다. 상세 실측은 [7회차 결과](../../docs/AWS_GPU_서버7회차_결과_20261108.md)를 참조한다.

## Gateway 시간 제한과 수집

`backend-config.yaml`의 `timeouts.status_timeout_s`는 엔진 health·models/version·metrics 조회와 상태 API health 확인에 쓰며 기본값은 5초다. 각 엔진 조회는 이 값으로 제한된다. 추론 및 토큰 계산·모의 receipt 조회는 `relay_timeout_s`(이 배포 예시 60초)를 계속 사용한다. 엔진 health·info·metrics는 엔진 전용 스레드 하나에서 순차 수집하고, Hubble/Tetragon 관측·Cilium·대상 Pod 수집은 별도 일반 수집 루프에서 진행한다. 한 엔진 조회가 지연되면 같은 그룹의 다음 조회와 기준선 재검증도 늦어질 수 있다. 상태 API의 수집원별 표시 형식은 같다.

일반 응답의 `request_deadline_s`(이 배포 예시 300초)는 동시 요청 슬롯 획득 시점부터 토큰 계산과 엔진 연결·요청 전송·응답 헤더·본문 수신까지 적용된다. 일반 응답 본문은 최대 64 MiB다. completions SSE는 유효한 이벤트가 완성되는 즉시 전달하며, 이후 잘림·오류·기한 초과는 이미 시작한 HTTP 200의 최종 요청 기록에서 구분한다. AWS-01·18의 stream 성공은 `[DONE]`과 Gateway 최종 기록을 함께 확인한다.

## LAB 시험값 (2026-10-27, runtime 선정 · 다른 담당 확인 전)

정책/검증·시험 담당의 값을 받기 전에 runtime이 정한 시험 출발값이다. 기업 운영값이나 정식 승인값이 아니다. update 점검(REQ-01·09·10·13, OBS-11)과 같은 시험에서 결과를 이으려면 같은 값을 써야 한다.

| 값 | 설정 | 근거 |
| --- | --- | --- |
| 허용 모델 | `test-model` | AWS 묶음의 served model(Qwen2.5-0.5B-Instruct) |
| 답변 기본/최대 | 128 / 256 토큰 | AWS-03이 최대+1(257)의 403을 확인 |
| 질문+답변 한도 | 2048 토큰 | 엔진 `max model len 2048`과 같게 둬 Gateway 통과 뒤 엔진 거부가 섞이지 않게 함 |
| 본문 크기 / 답변 개수 | 64 KiB / 1 | 기존 값 유지 |
| 비-stream 응답 대기 | 60초 | 최대 256토큰의 GPU 생성 속도를 아직 측정하지 않아 5초 504 오판을 피하는 LAB 시험값(요청 deadline 300초 미만) |
| 정책 기간 | 2026-10-27 ~ 2026-12-31 UTC | 만료 시 503이므로 발표 뒤까지 둠 |
| 요청 옵션 | 추가 옵션 없음(schema 1.1) | 첫 인수 사례 8개에 필요 없음. 옵션 범위 시험은 별도 판본에서 정함 |
| 성능 비교 | 동시 2, 답변 128, 예열 20·일반 100·stream 100, 3블록 | 엔진 `max num seqs 2`, `perf_compare.py` 기본 예산 |

정책 파일을 한 글자라도 고치면 SHA-256이 바뀌어 Gateway가 503으로 막는다. 고칠 때는 `policy_version`을 올리고 `sha256sum policy.aws-lab.json` 값으로 승인 목록을 함께 갱신한다. 성능 계획의 B0/B1/B2 주소는 운영자가 준비하는 Gateway 구성의 자리값이다. 인증이 고정된 이 Gateway로 성능 비교를 실행할 때는 소유자만 읽는 인수용 token 파일에서 inference token을 읽힌다. 옵션을 생략하면 기존처럼 Authorization 헤더를 보내지 않는다.

```bash
.venv/bin/python -B tools/perf_compare.py deploy/gpu-lab/perf-plan.aws-lab.json \
  --token-env-file ~/runtime-acceptance.env \
  --out-dir evidence/perf
```

UID: 엔진 2000은 AWS 묶음(vLLM 0.31.0 공식 비root 지원)을 따른다(`runtime/docs/결정기록_엔진UID.md`). Gateway 10000·observer 10004·공유 그룹 10010은 겹치지 않게 고른 프로젝트 선택값이다.

## 대시보드 상태와 Lab 보고서 연결

GPU 지표(`--gpu-state /srv/serving/observation/gpu-status/gpu-state.json`, 설치기의 `serving-runtime-gpu-status` 서비스)와 선택 기능인 엔진 로그 수신 기록(`--engine-receipts`, 기본 꺼짐)은 [엔진 수신 기록과 GPU 지표](../../docs/엔진수신기록_GPU지표.md)의 순서로 확인한다.

Gateway는 기존 `/srv/serving/observation` readOnly mount에서 `--tetragon-state /srv/serving/observation/tetragon-status/tetragon-state.json`과 `--lab-report /srv/serving/observation/lab-reports`를 읽는다. SGLang 배포도 이 Gateway 정의를 상속한다. [호스트 설치기](../install/README.md)가 하위 폴더 소유자·관측 그룹·2750을 준비한다. 설치기를 쓰지 않으면 관리자가 다음을 준비한다(상위 관측 폴더와 그룹은 아래 1번대로 준비).

```bash
sudo install -d -o runtime-agent -g 10010 -m 2750 /srv/serving/observation/tetragon-status
sudo install -d -o ubuntu -g 10010 -m 2750 /srv/serving/observation/lab-reports
```

상태 서비스는 원본을 유지하고 `--public-output` 사본을 5초마다 0640으로 원자 교체한다. 노드·에이전트·컨테이너 식별값은 사본 내 별칭이며 임의 필드·오류 원문·명령 출력은 제외한다. 공유 폴더 전체를 쓰기 가능하게 만들지 않는다. 서비스의 쓰기 허용 경로는 상태 전용 하위 폴더다. 설치 후 서비스 재시작과 새 Gateway 이미지/매니페스트 반영이 모두 필요하다.

`external_enforcement`는 **Tetragon의 실제 로드 모드**다. 현재 Gateway 시작 시 검증한 묶음의 적용 intent를 렌더해 이름·namespace·spec SHA-256을 계산하고, 같은 context의 최신 상태에서 모든 에이전트의 기대 정책이 ENABLED인지 대조한다. 원격 set-mode는 spec을 바꾸지 않으므로 초기 monitor 선언과 현재 모드를 구분한다. 초기 기대값은 시작 시 고정하고 디스크 파일을 다시 읽어 바꾸지 않는다.

| 값 | 의미 |
| --- | --- |
| `not_connected` | 상태 자료 없음 |
| `error` | 파일 읽기·형식/context 검증 오류 또는 수집기 오류 |
| `unknown` | 20초 초과/미래 5초 초과, 묶음 대조 불가, 노드/정책 누락·중복, spec 불일치, ENABLED·모드 미확인 |
| `monitor` / `monitor_only` / `enforce` | 모든 기대 정책·노드가 대조되고 같은 실제 모드 |
| `mixed` | 모든 기대 정책·노드가 대조되지만 실제 모드가 섞임; 정책별 화면에서 확인 |

이 값은 실제 차단 실측이나 감시 무공백을 뜻하지 않는다. 파일 읽기 거부·프로세스 종료는 별도의 시험 근거로, 감시 복구는 기존 heartbeat/식별/새 이벤트의 4조건으로 확인한다. Cilium 상태는 이 집계에 포함하지 않는다. 현재 AWS Gateway에는 Cilium 상태 수집기가 없으며 Kubernetes 권한을 추가하지 않는다.

AWS Lab 보고서는 [`ACCEPTANCE.md`](ACCEPTANCE.md)의 준비 조건과 사례별 허용 플래그를 확인한 운영자가 **실제 인수 시험 실행 시** 게시한다. 관측 폴더 소유 사용자 ubuntu의 kubeconfig와 기존 시험용 토큰 파일을 사용한다. 먼저 비파괴 사례만 선택하는 예:

```bash
# 서버 runtime/에서 실행. 토큰 파일 내용은 출력하지 않는다.
umask 027
.venv/bin/python -B tools/aws_lab_acceptance.py \
  --kube-context byul-aws-lab --namespace serving-lab \
  --token-env-file "$HOME/runtime-acceptance.env" \
  --observation-dir /srv/serving/observation \
  --evidence-dir "$HOME/.local/state/serving-runtime/aws-lab" \
  --cases AWS-01,AWS-02,AWS-03,AWS-04,AWS-05,AWS-06 \
  --public-report-dir /srv/serving/observation/lab-reports
```

`--public-report-dir`는 설치기가 준비한 관측 GID·2750 폴더에 실행 결과를 **0640 원자 게시**한다. 새 회차 파일만 생성하며 기존 회차를 덮어쓰지 않는다. 공개 형식 `serving-runtime-aws-lab-report/1.0`을 Gateway가 kind 형식과 구분해 읽는다. AWS-01~20 중 선택한 사례의 passed/failed/unknown/not_run만 그대로 표시하며 전체 결과도 그 선택 범위다. run_id·시작/완료 시각·환경(context/namespace)·엔진을 보존한다. 주소/클라우드 식별자/자격값이 든 환경명은 안정된 해시 별칭으로 표시한다. 원시 명령·facts·예외 이유·주소는 공개하지 않고, 상세 JSON/Markdown은 기존 evidence 회차 폴더의 0600 파일로 남긴다. 공개 게시 실패는 종료 2이며 상세 evidence는 남는다. 실패·미확인 실행도 게시하고 해당 인수 시험 종료 코드(1/3)를 유지한다. 실행 전 오류로 결과가 없으면 게시하지 않는다.

**kind 전용 `tools/k8s_lab_check.py`는 fake engine과 관리 정책을 생성·삭제하므로 AWS 엔진에 사용하지 않는다.** kind의 `--public-report-dir`은 kind 시험 workload·유지보수 복원 절차가 갖춰진 별도 환경에서만 사용한다. AWS에서 Lab 칸을 채우려고 kind checker나 과거 JSON을 실행/복사하지 않는다. 공용 계약 `runtime-lab-report`가 kind L-01~L-08과 AWS-01~AWS-20을 함께 허용하며(2026-11-08 사용자 승인), 웹은 ID·환경·시각을 그대로 표시한다.

Gateway는 디렉터리의 최신 JSON(mtime, 동률이면 파일명)을 선택한다. 과거 보고서를 복사해 이번 결과로 삼지 말고 화면의 실행 시각·환경을 확인한다. 아직 JSON이 없으면 `missing`으로 표시하며 배포 때 예시를 채우지 않는다. 서버에서는 `stat`으로 상위/하위 폴더 2750·관측 GID와 두 공개 JSON의 0640·GID를 확인하고, Gateway UID 10000/보조 그룹 10010으로 읽기 성공·쓰기 거부, `/api/runtime/tetragon-status`의 fresh/오류·실제 모드 및 `/api/runtime/lab-report`의 missing→실행 결과·시각을 대조한다. 이번 수정에서는 AWS 접속·서버 실행을 하지 않았다.

## 순서 (AWS 묶음 01→02→03 성공 뒤)

1. **호스트 폴더·그룹 준비** (관리자)
   ```bash
   sudo groupadd -g 10010 serving-observe && sudo usermod -aG serving-observe ubuntu   # 재로그인
   sudo install -d -o ubuntu -g 10010 -m 2750 /srv/serving/observation
   sudo install -d -o 10000 -g 10000 -m 0700 /srv/serving/runtime/data
   sudo install -d -o root -g 10000 -m 0750 /srv/serving/bundles
   ```
   `/srv/serving/runtime`이 없으면 먼저 만든다. `usermod` 뒤에는 이미 떠 있는 systemd 사용자 매니저가 예전 그룹 목록을 유지하므로(다른 SSH·tmux 세션이나 linger가 있으면 재로그인만으로 부족) `sudo systemctl restart user@$(id -u ubuntu)` 후 `systemd-run --user --wait --pipe id -G`에 10010이 보이는지 확인한다. 그룹이 없으면 수집기는 명확한 오류와 함께 종료 코드 2로 멈춘다(재시작 반복 없음).

   운영 수집기(`runtime-agent`)가 Gateway 정규화 기록을 읽을 때는 해당 계정을 그룹 10000에 넣고 `/srv/serving/runtime/data`를 그룹 10000·0750으로 준비한다(수집기가 없으면 기존 0700 유지). Gateway의 `observations.jsonl`은 0644, `tetragon-health-receipts.json`은 0640이다. 기존 0600 receipt는 새 Gateway가 다음 sentinel receipt를 기록하면 0640으로 교체된다. monitor window 수집에는 `--trusted-owner-uid 10000`을 명시한다. 추가 UID는 반복 지정할 수 있고, 생략하면 root·자기 UID만 신뢰하며 그룹/기타 쓰기 가능한 입력은 계속 거부한다.

   `runtime-agent`로 수집기를 실행할 때 Ubuntu의 `umask 002`에 주의한다. `tools.tetragon_status` 코드는 상태 파일·baseline을 명시 권한 0644, recovery를 0600으로 써서 그룹/기타 쓰기를 허용하지 않는다.

   지속 운영용 상태·모니터 수집기 systemd 예시는 `deploy/agent/`에 있다. baseline 최초 캡처·그룹/폴더 권한·설치 검증, HTTPS/사설 CA 연결, 운영 토큰 발급·회전과 LAB 전용 값은 [원격운영 사용과 검증 §14~15](../../docs/원격운영_사용과검증.md#14-운영-배치-수집기https토큰)를 따른다.

   호스트의 CRI 확인에는 `crictl`도 필요하다(kubeadm 설치에 포함되지 않음). 클러스터 Kubernetes 버전에 맞는 `cri-tools` 버전을 별도로 설치하고, containerd 환경의 `/etc/crictl.yaml`을 아래처럼 준비한다. 설치 후 `sudo crictl info`로 연결을 확인한다.
   ```yaml
   runtime-endpoint: unix:///run/containerd/containerd.sock
   image-endpoint: unix:///run/containerd/containerd.sock
   timeout: 10
   debug: false
   ```

   첫 버전의 정책 묶음은 사람이 [`정책_수동적용_절차.md`](정책_수동적용_절차.md)에 따라 `/srv/serving/bundles/<bundle_id>/`에 쓰고 `current`가 그 폴더를 가리키게 한다(`runtime/docs/결정기록_정책묶음_수신.md`). Gateway 컨테이너에는 이 폴더가 `/etc/serving/bundles`로 보이므로 **`current`는 상대 링크**(`ln -sfn <bundle_id> current`)여야 한다. 절대 링크(`/srv/serving/bundles/<id>`)는 컨테이너 안에서 풀리지 않아 Gateway가 시작하지 않는다. 묶음 폴더와 파일은 Gateway(UID 10000)가 읽을 수 있어야 한다(예: 그룹 10000, 폴더 0750·파일 0640). 묶음은 03 완료 뒤 서버의 `images.lock`에 기록된 엔진 digest를 확인하고 서버 사본에서 만든다. 묶음의 gateway policy는 `allowed_models`에 `test-model`을, `enforcement_intent` 역할에 `intent.aws-lab.json`(값을 채운 것)을 넣는다. 이 역할의 항목 이름은 `schema_version`이지만 intent 본문은 `schema`를 쓴다. `target` 역할에는 `target.aws-lab.json`의 `REPLACE_IMAGE_DIGEST_HEX`를 실제 엔진 image digest hex로 바꾸고 넣는다. gateway policy의 `target_id`, bundle의 `target_ref`, target 등록의 `target_id`는 모두 `serving-engine-aws-lab`로 맞춘다. 대상 등록의 Kubernetes 값은 context `byul-aws-lab`, namespace `serving-lab`, selector `app=serving-engine`, container `engine`이어야 한다. vLLM 판본은 등록 schema가 허용하므로 이 환경의 `0.31.0`을 선언한다.
2. **수집기 실행** — byul 저장소를 `~/byul`에 두고 `host/byul-observation-feed.service`의 주석대로 설치한다. `--kube-context byul-aws-lab --reader-group 10010 --target-selector app=serving-engine --target-interval 5 --phase-timeline`으로 돌며, Tetragon 프로세스 인자를 지운 뒤 기록한다. Hubble/Tetragon 상태는 `/srv/serving/observation/*.heartbeat.json`, 대상 Pod는 `/srv/serving/observation/target-pods.json`, 현재 엔진 Pod 로그의 단계는 `/srv/serving/observation/phase-timeline.json`에 원자 교체로 기록한다. Pod 조회 실패나 4초의 kubectl 제한 초과 때 이전 items를 재사용하지 않고 `state: error`로 바꾼다. 단계 수집 스레드는 별개이며 호스트 kubectl에는 기존 `get pods` 외에 해당 namespace의 `get pods/log` 권한이 필요하다. Gateway에는 kubectl 권한을 추가하지 않는다.

   식별 수집기도 같은 호스트 사용자로 설치한다. `/srv/serving/observation`의 setgid(2750)와 서비스 `UMask=0027`로 매번 교체되는 `tetragon-identities.json`을 그룹 10010·0640으로 만든다. 관측 수집기는 시작할 때 기존 폴더의 setgid를 보존한다. 이전 버전이 0750으로 바꾼 폴더는 두 수집기를 멈추고 1번의 `install -d` 명령으로 2750·그룹 10010을 복구한 뒤 다시 시작한다. 재시작 후 `stat -c '%a %g %n' /srv/serving/observation`으로 `2750 10010`을 확인한다. 기존 파일·`.next`가 있다면 수집기를 멈춘 상태에서 그룹 10010·0640인지 먼저 확인·수정한다.
   ```bash
   cp deploy/gpu-lab/host/byul-tetragon-identities.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now byul-tetragon-identities
   stat -c '%a %g %n' /srv/serving/observation/tetragon-identities.json
   ```
   selector는 `app in (serving-engine,byul-tetragon-proof,byul-tetragon-sentinel)`이다. 호스트 계정에는 serving-lab Pod·namespaced 정책 조회, kube-system Tetragon Pod 조회·exec 권한이 필요하다. Gateway는 보조 그룹 10010으로 `--tetragon-identities /srv/serving/observation/tetragon-identities.json`을 읽는다. SGLang도 같은 식별 서비스를 사용한다.

   AWS sentinel은 아래 Gateway 이미지와 같은 태그를 사용하며 `imagePullPolicy: Never`, command `/usr/local/bin/python3.12`로 실행한다. 정책은 기존 로컬 후보 `~/.local/state/serving-runtime/tetragon-monitoring/runtime-tg-health-01-sentinel-v2.json`에서 복사했다. 이번 수정 환경에서는 kind API 소켓 접근이 거부되어 현재 live 정책과의 직접 대조는 미확인이다. `TG-HEALTH-01`의 가짜 파일 읽기·Post 전용 범위를 검토한 뒤 배포한다. Tetragon CRD가 먼저 설치되어 있어야 하며 정책은 `runtime-tg-health-01-sentinel-v2`라는 별도 이름을 사용한다. 운영 스트림을 `tools.tetragon_stream`으로 수집한다면 이 정책 이름도 `--policy`에 포함하고, 기대 정책 baseline은 사람이 검토해 갱신한다. 정책 설치만으로 감시 정상이나 복구 완료로 판정하지 않는다.

   플랫폼의 `BYUL_OPERATION_MONITOR_SECONDS`는 운영 기본 86400초다. LAB에서만 플랫폼 환경에 `BYUL_OPERATION_MONITOR_SECONDS=60`을 주고 에이전트에도 `--monitor-seconds 60`을 지정한다. 에이전트는 자체 최소 기간과 플랫폼 기간 중 큰 값을 사용하므로 한쪽만 줄이면 60초 창으로 통과하지 않는다.

   대상 스냅샷의 타이밍은 **writer 간격 + kubectl timeout + Gateway poll 간격 < Gateway stale window**여야 한다. Gateway 기본값은 poll 5초 / stale 15초이고, 이 서비스는 writer 5초 / kubectl timeout 4초이므로 최악 age는 14초로 15초보다 작다. CLI는 이 기본 조합을 지키기 위해 `--target-interval`의 5초 초과 값을 거부한다. Gateway 재시작 뒤에는 시작 전에 쓴 파일을 인정하지 않아 Pod 준비 완료 후에도 새 쓰기·읽기까지 최대 약 10초(조회 시간 추가 가능) `TARGET_UNAVAILABLE` 503이 날 수 있다.
3. **Gateway 이미지 넣기** — 레지스트리가 없으므로 노드의 containerd에 직접 넣는다.
   ```bash
   # 노트북(runtime/ 폴더). base는 digest로 고정
   docker build -f deploy/gateway/Dockerfile --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:<digest> -t serving-gateway:<tag> .
   docker inspect --format '{{.Id}}' serving-gateway:<tag>        # 빌드 기록의 이미지 digest
   docker save -o serving-gateway-<tag>.tar serving-gateway:<tag>
   ssh ubuntu@<host> 'sudo ctr -n k8s.io images import -' < serving-gateway-<tag>.tar
   ```
   매니페스트는 `serving-gateway:<tag>` + `imagePullPolicy: Never`로 참조한다(레지스트리 digest가 없어 tag 참조). 이 실행에서 `docker inspect Id`와 서버의 `sudo ctr -n k8s.io images ls` digest는 manifest digest였고, Pod의 `imageID`는 다른 config digest였다. `ctr images ls` digest는 빌드 기록의 manifest digest와 대조한다. tar의 `manifest.json`에서 `Config` 파일명을 읽어 `tar -xOf serving-gateway-<tag>.tar <Config 파일명> | sha256sum`으로 config digest를 구하고, 이를 `kubectl get pod -o jsonpath='{..imageID}'`의 `sha256:…`와 대조한다. 두 digest를 직접 같다고 판정하지 않는다. `REPLACE_GATEWAY_TAG`를 `<tag>`로 바꾼다.
4. **인증 Secret** — `kubectl --context byul-aws-lab -n serving-lab create secret generic serving-gateway-auth --from-file=auth.json`(토큰 해시만 든 `runtime-auth/1.0`).
5. **적용** — 위 12회차 순서대로 폴더·실제 모델 경로를 확인하고 승인본을 게시한 뒤 실행한다. `MODEL_HOST_PATH`는 최종 Gateway 서버 사본과 같은 확인값이어야 한다. 점검 실패 시 아래 적용을 진행하지 않는다.
   ```bash
   sudo /opt/byul/runtime/.venv/bin/python -B -m tools.gpu_lab_gateway_preflight \
     --approved-dir /srv/serving/verification/approved --model-root "$MODEL_HOST_PATH" &&
   kubectl --context byul-aws-lab apply -k deploy/gpu-lab &&
   kubectl --context byul-aws-lab -n serving-lab patch deployment serving-engine \
     --type strategic --patch-file deploy/gpu-lab/engine-management-sidecar.patch.yaml
   ```
   patch는 묶음 `engine.yaml.template`의 엔진 args를 유지하면서 `--enable-log-requests`, `--enable-request-id-headers`를 더한다. 첫 옵션은 AWS-01의 `Received request` 로그 매칭에 필요한 전제이고, 둘째는 응답의 `X-Request-Id` 헤더를 켠다. patch는 엔진 Pod를 다시 만든다(Recreate: GPU 반납 후 재기동·cold start). 시험 시간 계획에 넣는다. vLLM 0.31.0의 `request_logger.py`는 INFO에 요청 ID·sampling params를, DEBUG에는 prompt·토큰 ID·JSON 본문을 남길 수 있다. 시험에는 무해한 고정 문장만 쓰고 원시 엔진 로그를 반출할 때는 요청 내용이 담긴 기록으로 취급한다.
   적용한 이미지 tag·patch·실제 Deployment/Pod imageID·정책과 run ID는 **통합 기록**으로 저장하고, 03의 `evidence/smoke-*`(기반 점검)와 섞지 않는다. 일반 시험 중에는 사이드카를 그대로 둔다. 03을 다시 돌릴 때만 아래 "03 기준 상태와 runtime 통합 층" 절차로 뗐다가 다시 붙인다.
6. **세부 통제 정책** — `serving-runtime enforce render --intent deploy/gpu-lab/intent.aws-lab.json --out <dir>`로 Cilium·Tetragon 후보를 만들어 검토 후 적용한다. 이 intent의 빈 `engine_egress_allow`는 DNS 허용 없이 Cilium의 deny-all egress 규칙으로 렌더돼 AWS 묶음의 "엔진 egress 없음"과 맞는다. AWS 인수 확인은 [`ACCEPTANCE.md`](ACCEPTANCE.md)의 `tools/aws_lab_acceptance.py`를 사용한다. kind 전용 `tools/k8s_lab_check.py`는 fake engine과 관리 정책을 생성·삭제하므로 AWS 엔진에 사용하지 않는다.
7. 접속: 서버에서 `kubectl --context byul-aws-lab -n serving-lab port-forward deploy/serving-gateway 8080`, 노트북에서 SSH 터널(`ssh -L 18081:127.0.0.1:8080 ubuntu@<host>`). 노트북의 개발 서버(`byul-dev`)가 8080을 쓰므로 로컬 포트는 다른 번호(예: 18081)를 쓴다.

## 03 기준 상태와 runtime 통합 층 (AWS 묶음 방침)

AWS 묶음의 03(`03-engine-smoke.sh`)은 **엔진 컨테이너만 있는 기준 상태**를 점검한다. runtime의 사이드카·관리 조회 정책·Cilium/Tetragon 통제는 엔진 템플릿(`engine.yaml.template`)에 넣지 않고, 이 폴더의 patch와 `apply -k`로 **별도 통합 층**으로 붙인다. 03은 남은 추가 컨테이너·init·volume과 추가 NetworkPolicy·TracingPolicy를 발견하면 중단하며 다른 팀 설정을 자동 삭제하지 않는다. `scale 0`이나 `apply`만으로는 patch로 붙인 사이드카가 지워지지 않는다.

03 재검증이 필요할 때의 순서 (`check_baseline.py`는 엔진 args를 검사하지 않으므로 직접 복원한다):

1. 통합 증거(Gateway 기록·인수 시험 `evidence/`·통합 기록)를 먼저 반출한다.
2. 아래 회수 목록을 실제로 적용한 자원 이름 그대로 회수한다.
3. 엔진을 `kubectl --context byul-aws-lab -n serving-lab scale deployment/serving-engine --replicas=0` 하고 Pod 종료를 확인한다.
4. 묶음 안내대로 03을 실행하고 `evidence/smoke-*`를 기반 점검 결과로 보관한다.
5. 위 5·6단계로 통합 층을 다시 붙이고 통합 기록을 새로 저장한다.

| 회수 대상 | 회수 방법 |
| --- | --- |
| 사이드카 컨테이너 `management-listener`·엔진 로그 args | `engine-management-sidecar.patch.yaml` 주석의 단일 strategic patch 명령으로 사이드카를 삭제하고 묶음의 엔진 args 15개 항목을 복원 |
| NetworkPolicy `engine-management-observer` | `kubectl --context byul-aws-lab -n serving-lab delete networkpolicy engine-management-observer` |
| 6단계에서 적용한 CiliumNetworkPolicy·TracingPolicy | 적용할 때 기록한 정확한 이름으로만 삭제 |
| Service `serving-engine-management` 등 나머지 runtime 자원 | 03 검사 대상이 아니면 유지 가능. 전부 내릴 때는 `kubectl --context byul-aws-lab delete -k deploy/gpu-lab` |

사이드카만 떼는 것으로 03 기준 복원이 끝나지 않는다. 회수 후 실제 Deployment의 엔진 args가 묶음 `engine.yaml.template`과 같은지도 확인한다. 회수와 재적용 모두 적용 기준 commit과 회수 목록을 함께 기록한다.

엔진 캐시는 묶음 구조(현재 세대 폴더만 컨테이너 `/cache`에 마운트)를 그대로 쓴다. intent에 `assets.cache_mount_path: "/cache"`를 두어 Tetragon 캐시 규칙(TG-CACHE-01)이 `/cache`를 감시하고, 호스트 쪽 `cache_root`(`/srv/serving/cache`)와 세대 경로는 03이 만드는 `/srv/serving/cache/vllm/<digest hex>/<revision>/<profile>-profile-01`과 같게 둔다. 엔진은 다른 세대를 볼 수 없으므로 컨테이너 안 쓰기는 모두 "현재 세대", 그 밖은 "캐시 밖"으로 분류된다. 호스트에서 `serving-runtime enforce cache-check --intent deploy/gpu-lab/intent.aws-lab.json`으로 세대·권한을 점검한다.

**주의 — CACHE-02(다른 세대 쓰기)의 근거가 바뀐다.** 이 구성에서는 runtime 사건으로 다른 세대 쓰기를 탐지하지 않는다. "엔진 Pod에는 현재 세대 폴더만 붙는다"는 배치 사실이 그 근거이며, runtime은 이 사실을 스스로 확인하지 않는다. 만약 `/cache`에 cache root 전체가 붙으면 다른 세대 쓰기도 "현재 세대"로 분류되어 조용히 놓친다. 그래서 배포·03 실행·프로파일 변경 뒤마다 아래를 확인해 증거로 남긴다.
```bash
kubectl --context byul-aws-lab -n serving-lab get deploy serving-engine -o jsonpath='{range .spec.template.spec.volumes[?(@.name=="cache")]}{.hostPath.path}{"\n"}{end}{range .spec.template.spec.containers[?(@.name=="engine")].volumeMounts[?(@.name=="cache")]}{.mountPath}{"\n"}{end}'
# 첫 줄 = intent의 cache_root/<engine>/<image_id>/<model_revision>/<compile_profile>, 둘째 줄 = /cache 여야 한다
```

## intent에서 서버 확인 후 채울 값

- `REPLACE_IMAGE_DIGEST_HEX`, `REPLACE_PROFILE`(dev/reference): AWS 묶음 03이 정한 캐시 경로와 같게(`images.lock`과 `LAB_PROFILE`). **dev↔reference를 바꾸면 intent의 `compile_profile`도 바꾼다**(안 바꾸면 cache-check가 `current_generation_missing`, 이전 프로파일 세대는 `other_generations_present`로 보임).
- `allowed_engine_binaries`·`phase_binaries`: 공식 이미지의 실제 실행 파일. 지금 값(`/usr/local/bin/vllm-nonroot-entrypoint.sh`, `/usr/bin/python3`, `/usr/bin/python3.12`)은 시작안이며 첫 Tetragon 관측으로 확정한다.
- `suspect_binaries`: `/bin/sh`, `/usr/bin/sh`, `/bin/bash`, `/usr/bin/bash`, `/bin/dash`, `/usr/bin/dash`, `/usr/bin/curl`, `/usr/bin/wget`. 허용 목록과 겹치지 않는다.
- 가짜 보호 파일은 `probe-file-configmap.yaml`의 `FAKE` 내용이며 engine UID 2000이 읽을 수 있도록 mode 0444로 단일 파일을 읽기 전용 마운트한다. ConfigMap을 먼저 적용한 뒤 `engine-management-sidecar.patch.yaml`을 적용한다. intent 변경 시 정책 묶음을 다시 작성하고 Tetragon 후보를 렌더·검토·재적용한 뒤 엔진을 재시작해야 새 마운트와 판정을 함께 시험할 수 있다. `network-policies.yaml` 등 다른 배포 파일은 바꾸지 않았다.
- 단계 연결 사전 분석과 정상 실행 후보는 [`AWS_GPU_단계_사전분석.md`](../../docs/AWS_GPU_단계_사전분석.md)에 있다. GPU 로그 수집 주체와 오래된 시간표 처리 방식을 결정하기 전 `--observation-timeline`은 배포에 추가하지 않았다.
- `enforce render`의 `pod-security-candidate.json`은 엔진 그룹을 UID와 같게(2000) 쓰고 캐시 환경변수도 `VLLM_CACHE_ROOT=/cache` 하나만 둔다. AWS 묶음은 GID 0이고 `VLLM_CACHE_ROOT=/cache/vllm`, `HOME=/cache/home` 등 하위 폴더를 쓰므로 이 후보 파일을 그대로 적용하지 않는다(참고용).

## 확인 방법과 확인하지 않은 것

```bash
kubectl kustomize deploy/gpu-lab                          # 렌더링만
serving-runtime enforce render --intent deploy/gpu-lab/intent.aws-lab.json --out /tmp/render
```

- reference 환경의 실제 AWS 적용·실행, Gateway의 hostPath 읽기 권한(10010 그룹)에 대한 다양한 조건·SELinux/AppArmor 영향
- 다른 노드 조건에서 host `target-pods.json` 조회·rollout 이력 재현(dev에서는 `instance_changed`/`pod_replaced` 확인)
- reference 환경에서 nvidia RuntimeClass Pod의 사이드카 기동 재현
- 관리 수신부 우회(같은 Pod의 loopback 공유) 시험 — AWS 묶음 README도 별도 시험 대상으로 둠
- observer 수집 프로그램, Gateway의 클러스터 노출 방식(현재 127.0.0.1 bind)
- SGLang 전환(AWS 묶음 범위는 vLLM만)
