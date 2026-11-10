# UPDATE 엔진 관측과 runtime 요청 연결

기준은 be07c2bf/PACK33이다. `tools/update_engine_link.py`는 기존 UPDATE 수집기와 projection을 호출한다. 제품 source를 가져오는 경로는 `PYTHONPATH=runtime/src:update/src:runtime`이다.

## 실행

1. 기존 `stream_probe` 요청 배열 JSON에 Gateway 두 route 중 지원 route와 합성 본문을 지정한다. 느린 수신자와 정상 주체의 인증은 각각 기존 headers에 지정한다. 요청 파일은 접근을 제한하고 사용 후 운영 정책에 따라 관리한다.

```sh
python runtime/tools/update_engine_link.py probe --requests requests.json --timeout 30 --normal-id normal --max-normal-ms 1000 --output probe.json
```

`max-normal-ms`는 해당 환경이 정한 HTTP 예산을 입력한다. 두 연결의 송신·헤더·종결 시각, HTTP 완료, byte/hash, Gateway 발급 request ID를 남긴다. 본문·토큰·인증 headers는 결과에 저장하지 않는다. HTTP 예산과 엔진 내부 자원 상한은 별도 관측이다.

2. source-qualified native sensor가 설치된 기존 대상에서 세션을 읽는다. 준비 자산과 ENV·target은 기존 UPDATE 생성물을 사용한다. 새 엔진 시작·설치·장애 주입을 수행하지 않는다.

```sh
python runtime/tools/update_engine_link.py capture --environment ENV.json --target native-target.json --prepared native-assets/manifest.json --duration 30 --output packet.json
python runtime/tools/update_engine_link.py termination --records gateway_records.jsonl --request-id GATEWAY_UUID32 --packet packet.json --max-delay-ms 1000 --required-scopes frontend_request engine_core scheduler_state worker_batch kv_references --output termination.json
```

취소 projection은 기존 vLLM source-qualified completions 계약을 사용한다. Chat·SGLang은 `unsupported_native_route_or_engine`으로 남는다. Gateway 종결, native binding·scope별 사건, 기한 뒤 active state 후보, GPU 회수는 각각 출력한다. 기존 `termination_verified`·`worker_gpu_release_verified=False`를 보존한다. 동일 소스/Python/image 자산·정상 대조·센서 설치/recording 완전성은 기존 UPDATE 계약에 따른다.

3. 기존 실행기가 생성한 EVD와 그 원본 artifact를 runtime에 결속한다.

```sh
python runtime/tools/update_engine_link.py link --records gateway_records.jsonl --request-id GATEWAY_UUID32 --evidence EVD.json --artifact request-termination.json --artifact-name request-termination.json --output linked.json
```

EVD의 seal/hash와 정확한 `EVD/ENV/PLAN Ref`, artifact의 이름·bytes·SHA256, request의 시작/종결 snapshot을 확인한다. 서로 다른 정책·세대의 시작/종결, 중복 final, bytes 변조는 거부한다. 선택 transport의 Pod UID/container ID/imageID·run ID와 관측기간을 대조하며 관측 누락은 `binding.gaps`에 남긴다. REQ12/LIF10은 기존 `native_projection`의 실제 ASGI/external 지문을 runtime outbound ID와 비교한다.

worker readiness/failure와 pressure의 EVD는 `target_generation_and_interval` 범위로 연결한다. 실제 수집기의 기동/실패/health 원관측, 자원 측정, 판정은 원 EVD와 정확 Ref로 유지한다. 요청별 인과와 worker/GPU 준비·복구 전체 충족은 원 source 관측 수준에 따른다. cache callback EVD에는 transport와 runtime-request binding 지문이 추가된다.

## 요청 ID와 세대

Gateway의 공개 request ID는 UUID32를 유지한다. vLLM outbound ID는 `byul-cb-{UUID32}`이며 요청 record의 `upstream_request_id`에 기록한다. 기존 UPDATE 센서가 native prefix/suffix에서 이 tag를 source-qualified 방식으로 복원하므로, callback 수집 순서와 무관하게 동일 요청을 찾는다. mock와 SGLang ID는 기존 형태를 유지한다. 내부 ID와 공개 ID를 비교할 때 이 필드를 사용한다.

요청 시작 때 policy/target/bundle을 snapshot으로 고정한다. 종결 때 같은 snapshot을 남기므로 실행 중 정책 교체나 같은 Pod의 container 재기동이 새 요청에 섞이지 않는다. phase log 수집도 containerID/restartCount/startedAt 변경 때 cursor와 이전 phase를 초기화하고 fetch 전후 세대가 바뀌면 폐기한다.

## 확인 범위

로컬 대역·source fixture·실제 socketpair/loopback 회귀와 실행 가능한 CLI를 제공한다. 실제 엔진/GPU·worker 네 장애·배치별 다중 worker·정상 주체의 실환경 기회와 자원회수는 해당 배포에서 원 EVD·runtime 기록과 함께 인수한다. 도구는 새 security verdict를 만들지 않고 원 실행 상태·관측·data_origin을 보존한다. 산출물은 기존 파일에 덮어쓰지 않는다.
