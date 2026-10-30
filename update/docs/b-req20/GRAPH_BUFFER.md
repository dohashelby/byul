# REQ20 선정 Graph 버퍼 관측

`tools/b_req20_graph.py`는 원 PACK31 REQ20 rev3의 세 challenge·정상 대조·질문·prose를 유지하며, 기존 Store를 읽기 전용으로 열어 새 출력에 복원한 뒤 선정 관측을 manual EVD→RES→RPT로 보존한다. 새 팩·registry·판정 규칙을 추가하지 않는다.

```sh
python -m tools.b_req20_graph --store STORE --plan PLAN --pack packs/PACK-BASELINE \
  --target target.json --prepared lifecycle/manifest.json --manifest cohort.json \
  --output /fresh-owned/req20 --entered-by operator
```

기본은 preflight이며 출력·kubectl·요청을 만들지 않는다. `--execute`는 기존 소유 Pod의 요청·세대 조회·session 파일 수집·소유 session seal만 수행한다. 배포·재시작·worker 기동·GPU 설정 변경을 수행하지 않는다. 실행 환경은 명시한 ENV의 vLLM 0.31.0/GPU1/TP1 및 기존 target의 lab/image digest/Pod·container 세대 관문을 통과해야 한다. 모델 저장소 id/revision은 ENV에서, HTTP `model`은 기존 target mapping에서 각각 검증한다.

입력 `byul-req20-cohort/1`은 B 로컬 실행 입력이며 기업 정책이 아니다. `environment_ref`, `target_sha256`, lifecycle `session`, target `run_id`, `model_inventory_sha256`, `owner`, `recovery_owner`, `declared_graph_mode`(FULL/PIECEWISE/FULL_AND_PIECEWISE), `requests`를 명시한다. requests의 A/B 각각은 기존 chat body `{model,messages:[{role:user,content:선정무해문장}],max_tokens:1,temperature:0}`다. 서로 다른 문장만 허용한다. 기존 request-binding profile의 tokenize-A/B로 같은 chat 문장에 대한 실제 token count를 비교하고, 일치할 때 before/A/B/after 1 token chat을 보낸다. 길이가 같아도 실제 native shape·유효 길이·mode와 replay 도달을 대신하지 않는다. 본문·token 배열·응답·원 RID는 메모리에서만 취급하고 count/enum/opaque 객체 식별자만 남긴다.

## 현재 native 연결 경계

공개 HTTP profile는 재사용 가능하다. 필요한 A 입력은 실제 worker의 graph callback namespace와 POINTS의 source/code 고정, callback bytes/code provenance, 실제 ASGI→native request 선택, copy/event 관측의 parser 계약이다. 현재 `NATIVE_CONTRACT=None`이므로 해당 단계는 `NATIVE_GRAPH_CAPABILITY_UNAVAILABLE`로 요청 0회에 닫는다. 실행 HEAD나 cohort에서 승인 literal을 생성하지 않는다. B 단위시험은 EXAMPLE native packet만 사용하며 실제 worker·GPU로 집계하지 않는다.

source/code·세대·session·현재 request가 맞는 graph key, 실제 mode, shape/valid length와 같은 request의 consumer 계보를 묶는다. 동일 call의 CALL/RETURN과 같은 원 event opaque identity가 있어야 event completion을 소비한다. 비차단 query의 미수집(None), pending(False), 완료(True)를 구분한다. RETURN이나 `_version` 변화는 값 정합이나 소비 전 overwrite의 확정 판정이 아니다. 후기에 세대 확인·after 요청·정리·첨부 쓰기가 실패해도 이미 자격을 확인한 원 관측은 보존한다.

현재 observer에는 실제 입력/출력 값 비교 producer가 없다. 따라서 확인 가능한 metadata와 version 변화 후보를 보존하고 원 질문의 세 축은 UNKNOWN으로 유지한다. PASS 조건이나 가상 true flag 기반 FAIL을 만들지 않는다. 확정 위반 판정을 연결하려면 실제 worker copy·GPU write·외부 소비의 원 event 순서와 값 귀속을 생산하는 A sourcepoint가 추가로 필요하다. 기업 정상 대조·수치 오차·성능·장애 기준은 입력되지 않은 상태다.

출력은 신선한 경로만 사용한다. 원 Store·입력 bytes를 보존하며 Store 검증과 동일 as-of의 export/restore 정확 Ref를 확인한다. 선언 증거의 origin은 기존 manual 계약의 DECLARED다. EXAMPLE 관측 출처는 artifact/notes에 별도 표시한다. 새 ENV/POL을 만들지 않고 원 PLAN의 정확 Ref를 소비한다.

순서는 A native recording의 `native_record_seq`와 B callback의 `observer_seq`를 각각 비교한다. init RETURN < get_output CALL < RETURN, 같은 worker/Pod 세대/session/request/owner/event를 요구하고 중간 재초기화나 RETURN의 init 계보 변경을 거부한다. CALL의 transition은 아직 unpaired이며 RETURN의 원 transition이 CALL을 가리키는지 확인한다. 두 sequence 영역의 숫자 크기를 서로 비교하지 않는다.

소유 session seal, 소유 HTTP probe 정리, worker 내부 B observer의 `close()`를 구분한다. seal은 worker의 B cache 해제를 증명하지 않는다. 현재 worker 종료 hook과 `.close()` 호출 연결은 A 소유의 미확정 조건이며 `worker_observer_close_verified=None`으로 남긴다. 호스트 증거 파일은 append-only 보존한다.

`sampled_output_copy_completion`은 AsyncGPUModelRunnerOutput의 sampled_token_ids 복사 event에 한정한다. `graph_output_consumption`은 별도 미수집 상태다. CUDAGraphWrapper의 hidden_states 출력과 sampled_token_ids·CPU 복사본의 opaque identity/shape를 각각 기록하며 같은 tensor로 추정하지 않는다. compute_logits/Sampler 등 중간 계산 계보는 `LOGITS_SAMPLER_LINEAGE_UNOBSERVED`로 남긴다. sampled-copy 완료 True도 graph 출력의 소비 완료·overwrite 안전·현재 값 일치 판정으로 사용하지 않는다.
