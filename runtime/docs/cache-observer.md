# 지속 캐시 관측

`cache_observation_feed.py`는 root 호스트 서비스에서 기존 캐시 메타데이터 검사와 실제 Pod 바인딩 검사를 주기적으로 실행한다. Gateway는 root 소유·명시한 reader group의 0640 스냅샷을 읽는다. 네이티브 요청 센서의 초기화·성공 여부와 독립적으로 동작한다.

승인 입력은 root 소유 0600 JSON으로 보관한다.

```json
{
  "schema": "runtime-cache-observer-settings/1",
  "target_path": "/etc/serving-runtime/target.json",
  "target_sha256": "<target 파일 SHA256>",
  "intent_path": "/etc/serving-runtime/intent.json",
  "intent_sha256": "<intent 파일 SHA256>",
  "policy_sha256": "<현재 정책 SHA256>",
  "bundle_id": "<현재 bundle_id>",
  "model_host_path": "/srv/model",
  "expected_command": ["vllm"],
  "expected_args": ["serve", "/models"],
  "expected_cache_environment": {"VLLM_CACHE_ROOT": "/cache"}
}
```

`target`은 현재 Kubernetes context/namespace/selector/container, 엔진 이미지, 모델 판본을 지정한다. `intent.assets`는 실제 cache root, engine UID, engine/image/model/compile profile 세대와 컨테이너 경로를 지정한다. `expected_command`와 `expected_args`에는 실제 승인한 기동 인자를 넣는다. `expected_cache_environment`는 채택한 캐시 경로 환경 변수만 지정하며 실제 Pod와 해시로 대조한다. 간접 envFrom·valueFrom으로 값을 확인할 수 없으면 관측 불가로 남긴다.

Pod의 컨테이너·Pod securityContext 순서로 선언 UID/GID를 확인하고 intent의 engine UID와 대조한다. UID 미선언은 관측 불가, 불일치는 재검증 요청으로 남긴다. 출처는 `pod_security_context`로 기록하며 실제 프로세스 UID는 별도 실측 근거로 확인한다. capability·privileged·권한 상승 설정은 메타데이터로 보존한다.

서비스는 현재 Pod를 두 번 조회하여 사이에 Pod/CID·이미지·기동·마운트 바인딩이 바뀌었는지 확인한다. 캐시 메타데이터는 최대 512개 항목을 방문한다. 방문 한도·조회 실패·입력 판본 변경·동시 재기동은 관측 불가로 표시한다. 다른 격리 세대의 존재만으로 현재 세대를 실패 처리하지 않는다.

설치기는 `/opt/byul/runtime/.venv/bin/python`과 `/etc/serving-runtime`을 공통으로 사용한다. `install.env`의 `CACHE_SETTINGS_FILE`, `CACHE_SETTINGS_SHA256`, `CACHE_KUBECONFIG_FILE`을 모두 명시한 경우에만 `byul-cache-observer.service`를 설치·시작한다. 설정 해시가 다르거나 일부만 지정하면 설치 전에 중단한다. 기존 설치의 기본 수집 범위는 유지한다.

설치기는 승인 설정을 `/etc/serving-runtime/cache-observer.json`, 전용 kubeconfig를 `cache-kubeconfig`, 환경 파일을 `cache-observer.env`로 root 소유 0600 복사한다. `CACHE_KUBECTL`은 root 소유·go-w 금지 실행 파일의 절대 경로이며 기본값은 `/usr/local/bin/kubectl`이다. 전용 Kubernetes principal에는 승인된 엔진 namespace의 Pod get/list만 부여한다. Gateway와 다른 수집기에는 이 자격 파일을 공유하지 않는다.

생산자는 `CacheObservationProducer.__init__`의 root 검사, `read_protected`의 root 소유 0600/명시 GID 0640 입력 검사, `atomic_snapshot`의 root 소유 출력 검사를 유지한다. 엔진 소유 0700 캐시 디렉터리의 metadata traversal에도 호스트 읽기 권한이 필요하다. root 실행은 이 경계를 보존하며 별도 UID를 위해 승인 파일 권한을 넓히지 않는다. unit은 `CAP_DAC_READ_SEARCH`와 출력 reader GID 지정용 `CAP_CHOWN`만 남기고 시스템·커널·cgroup 쓰기와 권한 상승을 제한한다. 쓰기 허용은 `/run/byul-cache`이다.

스냅샷 디렉터리는 root:GATEWAY_DATA_GID 0750, 파일은 같은 소유자·그룹의 0640이다. Gateway에는 디렉터리를 읽기 전용으로 마운트한다. reader GID를 바꾸면 설치 설정·Gateway 인자·마운트 읽기 권한을 함께 맞춘다. 새 설정·세대 적용 시 설치기를 다시 실행하고 최신 스냅샷의 producer/binding 해시와 unavailable 상태를 확인한다.

Gateway 옵션:

```text
--cache-observation /run/byul-cache/current.json
--cache-observation-reader-gid 10000
--cache-observation-producer-sha256 <source_bundle_sha256()>
--cache-observation-binding-sha256 <digest(_binding(settings))>
```

생산자 소스 해시는 `cache_observation.source_bundle_sha256()`로 계산한다. 생산자·캐시 검사·Pod 바인딩·intent/target·보호 파일 읽기·JSON 파서·서비스 진입점의 정확한 소스 SHA256 목록을 정렬된 JSON으로 해시하며, 스냅샷에 같은 모듈 목록을 남긴다. 최종 설치 판본에서 계산한 값을 Gateway 설정에 지정한다.

바인딩 해시는 `cache_observation.digest(cache_observation._binding(settings))`로 계산한다. Gateway는 이 해시와 producer 판본, 현재 target·policy·bundle·실행 인스턴스, 생성 시각을 대조한다. 정책·대상·캐시 세대를 바꾸면 승인 입력과 해시도 새 판본으로 배치한다.

불일치는 기존 관측 화면과 `asset_changed` 재검증 요청에 표시되며, cache 전용 scope에 저장되어 모델 파일의 미해결 변경을 해소하지 않는다. API 증거에는 세대 경로·소유/권한 문제·실제 Pod 바인딩과 launch 해시를 남긴다. 원 Pod, 환경 변수, 기동 인자 값은 저장하지 않는다.

이 경로의 확인 범위는 캐시 디렉터리 메타데이터와 Pod API에 선언된 경로·기동 구성이다. 캐시 파일 내용의 출처, 실제 worker의 읽기·재생성·오염 소비, cache root 위의 디렉터리와 실제 mount namespace 권한은 각 실측 근거와 함께 판단한다.
