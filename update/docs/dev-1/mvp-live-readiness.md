# 69개 실제 실행 준비

`tools.mvp_live_readiness`는 현행 팩의 update 대상 전체를 기준·후보로 펼쳐 입력 파일과 실행기 등록을 확인한다. 기존 `mvp_a_input_preflight`의 27개/54쪽 범위와 별도이며, 공통 파일 읽기·타입·실행 결합 함수를 재사용한다. 관측·실행 승인·RES/RPT는 만들지 않는다.

```bash
uv run python -m tools.mvp_live_readiness --write-template /outside/input-sources.json --output /outside/readiness.json
uv run python -m tools.mvp_live_readiness --index /outside/filled-sources.json --output /outside/readiness-next.json
```

현재 작업지시서의 한 쪽만 독립 실행 준비할 때는 `--side baseline` 또는 `--side candidate`를 명시한다. 판본·이미지는 작업지시서의 해당 행에서 그대로 읽으며, 비교를 실행하거나 다른 쪽 결과를 이월하지 않는다. 한 쪽 모드는69개 행을 모두 남기고 색인의 다른 쪽 입력은 누락시키지 않고 거부한다. 기본 두 쪽138개 행 동작은 유지한다.

```bash
uv run python -m tools.mvp_live_readiness --write-template /outside/selected-sources.json --side candidate --output /outside/selected-readiness.json
```

파일 결합이 유효해도 실제 대상 검증·실행·결과 플래그는 계속 false/null이다. 실제 GPU의 새 ENV/target/prepared와 항목별 실행 입력이 필요하며, 종료한 세대의 원본은 새 실행 준비물로 바꾸지 않는다.

통신 정체 원 기록에서 적용은 확인됐지만 해제 원 사건이 없을 때는 `tools.rank_rpc_fault_inputs --allow-missing-stall-release`를 명시한 별도 부분 수집을 사용할 수 있다. 기본 변환은 계속 엄격하게 거부한다. 이 모드도 적용의 원 source/HMAC/queue/제품 결합·해제 요청 bytes/nonce/기한·클라이언트 메타데이터를 대조한다. 잘못된 해제 원 사건을 미관측으로 바꾸지 않는다. 원 해제 사건이 없으면 `fault_released`를 만들지 않고 `RANK_RPC_RELEASE_UNOBSERVED`, 미등록·불완전·실행 미자격 상태를 유지한다. 다른 장애 종류로 이 모드를 확장하지 않는다.

색인은 `guard-mvp-live-input-sources/1`이며 `pack_ref`와 `inputs`를 가진다. 각 입력은 `check_id`, `side`(baseline/candidate), `environment`, `policy`, `configuration`이다. 파일 세 개는 각각 `path`와 파일 bytes의 `sha256`을 요구한다. 중복·범위 밖 ID·오래된 팩·추가 필드는 거부한다. 빈 입력이어도 현행 팩의 69개/138쪽을 모두 남긴다.

봉인된 ENV의 MVP 엔진/이미지·GPU 기본 사실, 정책 범위/필수 값/타입, 쪽·점검별 설정, real/kubernetes 구현의 판본별 가용성, target의 정확 ENV Ref, 기존 실행 결합을 확인한다. fake/common-input은 실제 능동 실행 입력으로 받지 않는다. EXAMPLE 파일의 형식이 맞아도 실제 자격으로 올리지 않는다. 파일 결합이 유효해도 `ready_for_execution`, `live_target_verified`, `input_preflight_passed`, `engine_executed`는 false이고 결과는 null이다. 실제 노드에서 기존 `guard prepare-update`가 필요하다.

## 2026-11-16 확인 범위

- main `3aab9c8c`에 기반한 기동/프로파일러 준비의 오래된 공통 헬퍼 SHA를 현재의 정확 bytes로 갱신했다. `/31`·`/32`에 쓰는 private 의존성의 `_bytes`·`_codes`·`_function` 의미를 변경 이력과 대조했다. 변조/심볼릭 링크/재해시 거부 관문과 과거 준비물·실측·생산자 등록은 바꾸지 않는다.
- 직접 36개, CPython 3.12.8 관련 170개, 입력 준비 직접 44개가 통과했다. Linux CPython 3.12.3 전체 기본 suite는 13,773 passed / 기존34 skipped / 실패0이며 pytest1023.07초, 부모1026.256초다. 원 engine/docker/network 기본 제외는 유지했다. 로컬 검증이며 새 PR CI·GPU 실측은 별도다.
- 69개/138쪽의 빈 현재 실환경 입력은 모두 미결합이다. 능동 구현이 닫힌 쪽은 기준19·후보2다. SUP-03은 보안 규칙이 비어 있는 관측 전용이다. 연결/소스 검토·승인된 UNKNOWN 근거와 실제 관측·GPU 등록을 구분한다.
- 사용자 요청으로 정상·위반·미수집·지원 밖의 기존 정의를 사용한 사례309개를 준비했다. 사례나 빈 정책을 실행 결과로 세지 않는다.
- 사용자가 “기업 정책을 확정한 뒤 실증”, 이어 “너가 알아서 확정해줘”라고 위임했다. 기존 사용자 확정11개를 유지하며 소유한 격리 MVP 환경의 정책 결정178개를 정했다. 실제 실행 시각·모델 권한 매핑·자격 사본에 의존하는5개 필드는 명시한 규칙으로 실제 환경에 결합한다. 관측값·기업 외부 환경의 신원·서명 승인을 만들지 않는다. 실행별 POL은 결합 전 DRAFT로 유지한다.

## 실제 촬영 진입 조건

1. 최종 소스 목록과 정확 이미지/모델·판본·인터프리터를 고정한다.
2. 판본별 닫힌 관측 경로는 실제 자격/지원·등록 조건을 충족하거나 기존 승인된 UNKNOWN의 정확 범위를 남긴다. 미승인 공백을 완료로 바꾸지 않는다.
3. 새 비용 승인 범위의 GPU에서 현재 ENV/target/세대/세션·prepared를 만들고 정책 규칙을 실환경에 결합한다. 과거 종료한 Pod의 파일은 재사용하지 않는다.
4. 상태 독립성이 확인된 시험만 기동을 공유한다. 모델·토폴로지·장애/재시작 조건이 달라지면 독립 실행 묶음으로 나눈다.
5. 기존 제품 `guard prepare-update` → `guard update` → `guard verify`의 결과와 실제 EVD/RES/RPT Ref를 보존한다. 여러 묶음의 원본 RPT를 하나인 것처럼 다시 만들지 않는다.
6. 실행/실패/미수집/비적용·승인된 UNKNOWN을 분리해 플랫폼 원본 보고서와 촬영 기록에 연결한다. 실제 실행 전의 설명 영상·EXAMPLE/NOT_RUN 보고서는 촬영 근거에서 제외한다.

새 GPU 시간·비용·대규모 다운로드와 이후 실제 등록은 별도 세션 범위에서 진행한다. 지난 $20/2시간 세션은 정리됐으므로 재사용 가능한 실행 승인이 아니다.

## LIF-11 로컬 노드의 GPU 참조 관측

기존 `[resource_recovery]`의 target·prepared·selection에 아래 옵션을 추가하면, 선택한 GPU worker의 커널 PID/시작 시각/UID/namespace를 호스트 PID와 연결하고 GPU UUID·장치 minor·NVML 사용 PID·장치 FD를 전후 수집한다. 기본 동작은 유지한다.

```toml
[resource_recovery]
node_gpu_processes = true
node_gpu_containerd_address = "/run/k3s/containerd/containerd.sock"
```

guard가 대상 Linux 노드에서 실행되어야 한다. 기존 live containerd 이미지·task·커널 루트 마운트 증거를 재사용하고, 그 task의 PID1/시작 시각/PID namespace 및 실제 노드·Pod·CID를 전후 대조한다. 다른 호스트나 임의 Unix socket의 자료를 대신 받지 않는다. 조회 명령과 입력은 고정하고, 출력·PID·FD·시간 범위를 제한한다. 사용자 입력이나 GPU 메모리 내용은 읽지 않는다.

NVML 조회를 FD 수집 앞뒤에 반복하고 실제 host `/proc` 범위를 확인한다. PID 재사용·다른 GPU 작업·빈 조회 뒤 남은 FD·부분 수집·N/A/식별 불가·MIG 활성 상태·노드 교체·시계/바이너리 변경을 성공적인 부재로 바꾸지 않는다. 장치 식별과 상속 핸들 조건의 근거는 [NVIDIA nvidia-smi 명세](https://docs.nvidia.com/deploy/nvidia-smi/)다.

원 packet과 비교는 LIF-11 EVD artifact의 `node_gpu_process_censuses`·`node_gpu_process_projection`에 남아 RPT·반출·복원으로 이어진다. 마지막 수집이 실패해도 앞서 확보한 관측을 지우지 않는다. 새 자료는 GPU 메모리 전체 해제, 접근 권한, 재기동·정상 추론이나 전체 그룹 회복을 증명하지 않는다. 새 수집기 GPU 자격·등록은 미완료이며, `gpu_release_verified`와 `whole_group_recovery_verified`는 계속 false다. 원 정책·판정 기준은 변경하지 않았다.
