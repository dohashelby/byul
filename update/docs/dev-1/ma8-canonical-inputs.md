# SUP-08 정본 입력 준비

정본 팩 @34의 SUP-08 revision 5를 사용한다. 판본·이미지는 작업지시서 0.1절에서 읽는다. 기준·후보는 같은 Phi-3 Vision 파생 모델 inventory, 승인/미승인 코드 commit, GPU·드라이버·TP/PP/DP와 비교 조건을 사용한다. 기동 명령과 실행별 import 표식 경로 차이만 정규화한다.

기존 PoC의 `product_update`는 예상 동작의 근거다. 새 실행의 EVD·RES·RPT로 복사하지 않는다. Python 설치 레이어 확인과 엔진 설치 bytes 검토, GPU 실행은 서로 다른 단계다.

[고정 입력 신원](ma8-poc-fixture-contract.json)은 PoC 문서에서 옮긴 모델 ID·snapshot·inventory·코드 저장소·승인/비승인 commit이다. 입력끼리 일치하더라도 이 신원과 다르면 조립 전에 거부한다. 이 파일은 원본 fixture manifest의 복원본이 아니다.

1. 기존 `tools.prepare_sup08_phi3v_fixture`로 고정 모델의 실제 파일을 검증하고 새 fixture를 만든다. 큰 모델 다운로드는 별도 승인 대상이다.
2. `tools.gpu_a_aws_lab`의 코드 선택 세대로 각 판본을 렌더링한다. 같은 fixture의 모델·승인 코드·trust 설정을 넣고 실행별 marker 경로를 분리한다. 실제 기동은 GPU 세션 승인 뒤 진행한다.
3. 실제 설치 판본·GPU·드라이버·모델 inventory를 확인한 ENV와 실제 namespace/Pod에서 조립한 target을 준비한다. 예제 Pod를 실제 입력으로 표시하지 않는다.
4. 아래 조립기는 외부 IO 없이 두 입력을 대조한다.

```sh
uv run python -m tools.mvp_sup08_inputs \
  --fixture <실제-fixture-manifest.json> --control-image <고정-data:image/png;base64,값> \
  --baseline-env <기준-ENV.json> --baseline-target <기준-target.json> \
  --candidate-env <후보-ENV.json> --candidate-target <후보-target.json> \
  --output <새-입력-폴더>
```

출력은 쪽별 environment.json·target.json·policy.yaml·guard.toml과 전체 manifest.json이다. 기존 Kubernetes 입력 검증과 정책·관측 설정 계약을 적용한다. prepared 관측기 자산은 이 HTTP 코드 선택 경로에서 요구하지 않는다. 모델 bytes를 다시 읽었다거나 클러스터를 관측했다고 주장하지 않는다. 정책은 DECLARED/DRAFT이며 확정자·실제 결과·비교 결과는 비어 있다.

단위 시험은 모의 ENV·Pod와 작은 EXAMPLE inventory로 정상 조립, 모델/코드/이미지/GPU/trust 불일치 거부, 과거 예상의 새 판본 상속 금지를 검증한다. 실제 ENV·Pod target이 없어 정본 실행 입력의 최종 조립은 아직 하지 않았다. J02에서 새 입력을 조립하고 정본 팩으로 새 EVD→RES→RPT를 생성해야 한다.

2026-11-13 원자료 탐색: 저장소·관련 worktree·프로젝트 산출물·Windows 문서/다운로드/첨부/임시 폴더·WSL 사용자/캐시/임시 경로와 이전 작업 기록을 확인했다. 원본 `SUP08-evidence-20261111.zip`(998,017 bytes, SHA-256 `719ccd77b9daf34d711e5c9691545b12e17e2644968c4e3bdedc97b73fc6b84a`)이나 실제 fixture manifest·두 ENV/target은 찾지 못했다. 임시/첨부 폴더 ZIP 98개에도 원본 및 내부 Store export 세 개와 같은 크기의 파일이 없었다. 저장소 밖 전달물이라는 PoC 요약과 보존 EBS 기록만 확인했으며, EC2 기동·GPU 실행·모델 다운로드는 하지 않았다. 최종 조립과 실제 입력 사전 점검은 미완료다.

2026-11-13 후속 지시("현재 여유 공간 보고 나머지 작업 진행")에 따라 실제 C: 여유를 확인하고 같은 고정 모델의 가중치 두 개, 합계 8,293,330,888 bytes를 로컬에 받았다. 1GiB를 남기는 용량 확인에서 한 번 멈췄으나, 이후 여유 증가를 확인하고 부분 파일에서 재개했다. 사용자 파일을 삭제하거나 보호 기준을 낮추지 않았다. 각 가중치 SHA-256과 14개 모델 inventory·snapshot·두 코드 식별값을 대조해 모두 PoC와 일치했다. 가중치는 원본과 파생 모델 사이의 하드링크여서 한 번만 저장한다. 이는 새 로컬 fixture의 준비이며 과거 실측 원본 복구가 아니다.

[실제 fixture manifest](ma8-local-fixture-manifest.json)와 [로컬 준비 기록](ma8-local-preparation-20261113.json)에 파일·검증·미완료 범위를 남겼다. 두 판본의 engine/probe YAML·렌더 기록·EXAMPLE/DRAFT 정책·target 결합 대기 TOML 및 코드 캐시 배치를 만들었다. 기동 인자·환경·GPU 요청량의 판본 차이 정규화 결과가 같고 정책/설정 검사를 통과했다. 새 무해한 PNG의 CRC·압축 해제를 검증했으며 과거 PoC PNG 원본과 동일하다고 주장하지 않는다.

실제 ENV/target은 만들지 않았다. 새 fixture를 실제 조립기에 넣었을 때 고정 신원 검사는 통과하고 실제 ENV가 없는 지점에서 중단되며 최종 출력도 생기지 않음을 확인했다. `input_preflight_passed=false`, `cluster_observed=false`, `engine_executed=false`다. 노드에 모델·캐시를 배치하고 권한·정상 요청을 확인한 뒤 실제 ENV/target으로 최종 조립하는 단계가 남았다.


## 실제 파일 재검증·기동 명령 보완

2026-11-13 후속에서 기존 로컬 기동 자산의 후보 명령이 과거 인계용 wrapper 경로를 그대로 쓰는 것을 확인했다. 공개 판본 profile 명령과 일치하지 않았으며 실제 이미지에서 그 wrapper가 존재하는지는 확인하지 않았다. `tools.gpu_a_aws_lab --release-launch`는 정확 이미지 쌍을 확인해 두 판본의 profile 명령을 선택한다. 과거 기본 렌더·고정 자료의 기대값은 유지한다.

새 자산은 작업 폴더의 `inputs/sup08-rebuild-20261113/launch-inputs-v2`에 있다. 원래 자산은 그대로 보존했다. [실제 로컬 확인 보고서](ma8-local-preflight-20261113.json)는 모델14개 총8,295,393,861 bytes·코드 캐시5개·기동 파일12개의 실제 SHA를 다시 확인한 결과다. 가중치 두 개의 bytes는8,293,330,888이며 새 다운로드·중복 모델 복사는 없다. 양쪽 정책·관측 설정·기동 비교 조건도 확인했다.

```sh
uv run python -m tools.mvp_sup08_local_audit \
  --fixture <실제-fixture-manifest.json> \
  --launch-manifest <launch-inputs-v2/manifest.json> \
  --output <새-로컬-확인-보고서.json>
```

이 도구는 모델 파일을 스트림으로 읽고 검증 중 파일 신원·크기 변경을 거부한다. 코드 캐시의 승인/미승인 bytes와 기본 ref, 기동 파일 SHA, 정확 이미지·명령·모델·코드·GPU 설정을 확인한다. 미입력은 보고서에 남기고 종료 코드2를 반환한다. ENV/target을 제공하면 기존 조립기를 임시 폴더에서 실행해 계약만 대조하고 임시 복사본을 정리한다. EXAMPLE ENV는 실제 입력으로 승격하지 않는다. 기존 보고서는 덮어쓰지 않는다.

기존 WSL minikube와 Windows kind의 API는 읽기 전용 조회에서 모두 연결 거부였다. 실제 ENV/target 네 파일은 확보하지 못했고 최종 입력 계약은 미검증이다. `local_assets_valid=true`와 `model_bytes_rechecked=true`는 로컬 파일 검증 결과이며 `input_preflight_passed=false`·`cluster_observed=false`·`engine_executed=false`·`normal_request_verified=false`를 유지한다. 노드 배치/권한·GPU/드라이버·실제 Pod 신원·정상 요청 관측 뒤에만 최종 조립·실행 사전 점검을 진행한다.
