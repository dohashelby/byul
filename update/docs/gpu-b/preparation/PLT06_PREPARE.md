# PLT-06 전용 준비 도구와 환경 파일

`tools.plt06_prepare`는 GPU 담당 소유 준비 도구다. Docker/AWS를 호출하거나 엔진·네트워크를 생성·정리하지 않는다. 기존 `unix_ipc:real / worker-trust-scope-v1`의 실제 factory와 정책 검증을 사용하고, 기존 mapper source를 그대로 준비 자산으로 낸다. Kubernetes PLT-06 지원을 새로 만들거나 다른 backend로 대체하지 않는다.

## 산출물과 계약

고정 소스 검사는 준비 도구·recipe뿐 아니라 실제 import하는 출력 예약·manifest 검증·파생팩 helper와 검증 helper가 사용하는 builders도 포함한다. 이 파일의 미커밋 변경을 실제 Git diff로 거부하며 정본 manifest나 기존 승인값을 바꾸지 않는다.

`prepare`는 고정 HEAD·clean 제품/준비 소스·정본 source manifest·원 PACK-BASELINE 무결성을 확인한 뒤 새 외부 0700 출력에 다음을 생성한다.

- `environment.recipe.json`: GB35의 기존 고정 a4 이미지·Python probe 이미지·mp backend·512/2/eager 설정과 자원 제한을 재사용하는 Docker 환경 recipe.
- `process-map.py`: 실제 기존 `_MAPPER`와 동일 bytes. source/name grouping만 지원하며 역할 인증이나 worker 명령 영향 producer가 아니다.
- `policy.draft.json`: PLT-06만 scope에 넣은 EXAMPLE/DRAFT 정책 shorthand. 기업 승인값·confirmed_by·POL ID·runtime 유효창은 생성하지 않는다.
- `runtime-inputs.json`: 실제 운영자가 채울 8개 입력을 null로 둔다. 미래 UID/GPU/Pod/ENV 신원을 만들지 않는다.
- `preparation.json`: 고정 source/tool/원 pack Ref와 생성 bytes·지원 범위·미구현을 기록한다. 자체 무결성 receipt이며 새로운 source 해시 승인 목록이 아니다.

공유 신뢰와 독립 경계 두 입력 예시는 `tools/fixtures/plt06_policy_values.{shared,independent}.draft.json`이다. **LAB 초안이며 기업 정책이 아니다.** 입력을 명시해야 하고 도구가 격리 요구를 자동 선택하지 않는다. shared 제안은 과거 independent-uid DRAFT의 GB35 FAIL을 소급 변경하지 않는다. 독립 UID는 채택 정책이 실제로 요구할 때의 분기다.

```text
uv run python -m tools.plt06_prepare prepare --source-commit <고정40자리commit> --manifest-sha256 <정본SHA> --policy-values <명시한DRAFT입력JSON> --output <새외부준비경로>
```

## 환경 인자 렌더링

운영 입력은 `run_id`, `uid`, `gid`, `gpu_devices`, `tp`, `model_root`, `served_model_name`, `host_port`다. `gpu_devices`는 운영자가 실제 수집한 GPU UUID 목록이며 TP1/TP2와 같은 개수의 중복 없는 UUID를 요구한다. 목록의 형식·개수 검사는 실제 물리 GPU inventory 검증을 대신하지 않는다. UID/GID는 실행 그룹 전체의 비root 사용자 설정이며 core/worker 역할별 UID 분리를 구현했다고 주장하지 않는다.

```text
uv run python -m tools.plt06_prepare render --prepared <준비경로> --bindings <운영입력JSON> --output <새렌더경로>
```

생성 `network.argv.json`·`launch.argv.json`은 shell 문자열이 아닌 인자 배열이다. 원 `guard.run` 소유 라벨·전용 이름·이미지 pull-never·모델 RO mount·cap-drop/no-new-privileges·자원 한도와 loopback publish를 유지한다. GPU device CSV의 쉼표 인용은 Docker 인자 parser에 전달할 literal double quote이며 shell escaping이 아니다([Docker GPU 문서](https://docs.docker.com/engine/containers/gpu/)). `render.json`은 선언 설정일 뿐 실제 engine/GPU 기동 증거가 아니다.

실제 Docker·NVIDIA runtime·캐시 이미지·원 모델 inventory와 fresh run/network/container 부재 확인, argv 적용과 실패 정리는 세션2의 기존 소유 운영 절차다. 이미 있는 다른 세대/소유자의 이름·라벨을 재사용하지 않는다. 도구는 새 원격 실행 계층이나 자동 cleanup을 만들지 않는다.

원 GB35의 `docker network create --internal`을 유지하고, 제공된 실제 network inspect에서도 `Internal=true`를 요구한다. TP2는 한 Docker 호스트의 실제 GPU 두 UUID를 지정하는 형식이며 두 노드 TP 구성을 준비한 것이 아니다.

## 실제 입력 결합과 세대 비교

엔진 준비 뒤 세션2가 수집한 기존 sealed ENV, 실제 `docker inspect`와 `docker network inspect` JSON을 입력한다. source image·소유 label/name·Running/StartedAt·UID/GID·모델 RO mount·PID namespace·network ID/owner/endpoint를 검증한다. 원 ENV/Ref는 바꾸지 않고 기존 model mapping 계약으로 served 별칭을 결합한다. env.json을 복사하거나 미래 ENV를 생성하지 않는다.

```text
uv run python -m tools.plt06_prepare bind --rendered <렌더경로> --environment <실제sealedENV.json> --inspection <실제container-inspect.json> --network-inspection <실제network-inspect.json> --output <새결합경로>
uv run python -m tools.plt06_prepare verify-generation --bound <결합경로> --inspection <실행전후각각새로수집한inspect.json>
```

`target.json`과 `guard.toml`은 기존 real factory 입력 형식이다. `binding.json`은 입력 파일/ENV Ref/실제 제공된 container image/StartedAt/network generation을 보존한다. 입력 JSON을 검증한 것이며 live Docker를 직접 조회한 사실이 아니다. 실제 factory의 live 소유권 검사는 그대로 실행된다. `verify-generation`은 새 제공 inspect에서 동일 세대인지 대조하고 ENV/target/config/준비 자산 교체도 거부한다. worker 재시작·인증된 전체 roster·명령 영향까지 검증하는 것은 아니다. 실패해도 기존 증거·FAIL·UNKNOWN·Ref를 고치지 않는다.

## 남은 공통 producer와 실행 범위

현재 collector는 실제 UID·namespace·공유 FD 지도와 독립 UID 공유 위반을 수집한다. `worker_command_impact_verified=false`·정상 편입 미시도·역할 인증 미지원은 보존한다. 따라서 이 도구는 환경/입력 자산 준비 완료와 **전체 PLT-06 GPU 실험 준비 완료를 구분**하며 `measurement_packet_ready=false`를 낸다. 전체 UNKNOWN 실행기를 추가하거나 지원 연결 수를 늘리는 wrapper가 아니다.

개발26 공통 담당에게 필요한 작업은 source-bound 실제 admitted worker의 sender→reader/consume·전체 roster/PID-start/model/generation·독립 무해 효과/복구 producer다. 정확 source point는 고정 vLLM db9527a의 `MultiprocExecutor.collective_rpc`, `WorkerProc._init_message_queues`, `worker_busy_loop`, `_execute_worker_rpc`다. executor 송신을 worker 송신 권한으로 세지 않는다. 상세 입력·관측·담당 요청은 공유 `prepare/PLT06-next/v2/native-producer-request.md`를 참조한다. 세션1은 해당 계약에 맞는 자신의 consumer·실험 전용 자극과 계약 시험을 연결한다. 기존 공통 파일 권한을 확대하지 않는다.

## CPU 검증의 의미

직접 시험은 실제 asset 생성→DRAFT loader→기존 real factory의 정책/identity 소비 형식, asset/환경/세대 교체·소유 불일치·링크 거부와 명시 TP device 계약을 확인한다. 별도 CPU Linux 프로세스 2개에 생성된 실제 mapper를 실행해 bounded stdout→기존 helper/parser→실제 real factory collector로 연결하고 UID 공유 위반을 확인한다. 이 프로세스들은 vLLM worker가 아니며 EXAMPLE 계약이다. Docker inspect/HTTP 경계의 수작업 시험값을 실제 GPU 신원으로 세지 않는다. CPU나 선언 device UUID를 GPU MEASURED로 집계하지 않는다.
