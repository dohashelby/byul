# LIF13 선정 zero-copy 기존 참조

`tools/b_lif13_weights.py`는 원 PACK31 LIF13 rev4의 질문·세 challenge·한 prose를 유지한다. host는 native producer를 실행하거나 일반 kubectl GPU exec를 추가하지 않는다. 원 Store의 정확 ENV/POL/PLAN을 확인하고 선택된 기존 Pod에서 실행할 명령을 준비하거나, 이미 만들어진 native capture를 원 manual EVD→RES→RPT/검증/export-restore로 연결한다.

```sh
cd update
python -m tools.b_lif13_weights --store STORE --plan PLAN --pack packs/PACK-BASELINE \
  --target target.json --config native-config.json --output /fresh-owned/lif13 --entered-by operator \
  --pod-config /owned/native-config.json --pod-source-root /approved/vllm --pod-output /owned/capture.json
```

기본은 RPC·GPU import·daemon release 없는 preflight다. 명시한 Pod 경로는 host 경로와 구분한다. 반환 argv는 기존 Pod 내부의 `python -m tools.b_lif13_weights_native --config ... --source-root ... --output ...` 및 실행 opt-in을 준비할 뿐이다. 해당 모듈·원 source·config의 Pod 내 배치까지 확인했다고 표시하지 않는다. feature 선택·daemon 시작·복구·GPU reset을 수행하지 않는다.

선정 daemon·model/rank·UID/PID/start/boot/ns/GPU UUID, 원 cache key 12필드, owner/recovery owner, 전역 release 및 시험 후 empty 허용이 명시된 `byul-lif13-native-config/1`을 소비한다. mode는 zero_copy이며 bounded read는 1..16 원소다. 실제 소스·선정 image·복구 창이 없으면 실행 단계만 대기한다. CPU by-value를 zero-copy 증거로 대체하지 않는다.

이미 저장한 producer capture를 연결하려면 위 명령에 `--capture capture.json --import-capture`를 추가한다. 새 ENV/POL을 만들지 않고 원 canonical Ref를 소비한다. config SHA·target·세 Ref·db952 원 source commit이 일치해야 한다. 두 owned consumer의 baseline/held-after 안전 HMAC digest·shape·dtype·read 완료와 cleanup을 보존하고 digest 관계를 same/changed/unavailable로 나눈다. 이 관계는 선언된 selected tensor prefix의 부분 관측이며 전체 모델 정상·값 승인이 아니다.

세 결과를 따로 남긴다.

- release actor/daemon rank·role·응답과 기업 release 권한 인증을 구분한다.
- 후속 get_state 응답은 실제 fallback 소비와 구분한다. source의 “Weights were released” 오류만으로 정책 FAIL을 만들지 않는다. fallback·copy는 미수집이다.
- 기존 CUDA IPC 참조 read·전후 digest 관계·owned child 정리와 daemon 복구를 구분한다. 종료 후 empty 허용 선언은 복구 완료 증거가 아니다.

원 client 파일 bytes 대조 주장은 실행 daemon의 source나 target/generation/인가 receipt 인증이 아니다. 외부 receipt 없는 standalone capture는 DECLARED/EXAMPLE로만 보존한다. EVD는 기존 manual 계약대로 DECLARED이며 원 정상 대조·challenge 전체 미수행, 세 축 UNKNOWN을 유지한다. 원문 weight·CPU raw bytes·IPC handle·pointer·socket/entry/key 원문을 artifact에 저장하지 않는다.

원 Store와 입력 bytes는 보존하고 fresh 출력에만 기록한다. 첨부 쓰기 실패 시 같은 정제 facts를 원 manual 노트와 반환 summary에 보존한다. 후속 Store/report 저장까지 실패하면 safe_facts와 단계별 recovery를 담은 오류를 반환하고 CLI는 비정상 종료한다. 수집을 재실행하거나 실패를 저장 성공으로 표시하지 않는다.

원 get_state는 모든 CUDA TensorEntry의 IPC sent-counter를 발행한다. native producer는 모든 CUDA entry를 원 rebuild로 한 번 받아 미선정 참조를 정상 drop하고 선정한 한 tensor만 bounded read한다. 전체 값 read·모델 구성 관측으로 확장하지 않는다. payload 제한·timeout·unpickle·부분 rebuild 실패는 release 미시도여도 daemon native handoff에 영향을 남길 수 있다. child 종료·참조 drop과 daemon limbo/counter·allocator 전역 회수를 구분하며 후자는 UNKNOWN이다. 직접 counter를 조작하지 않고 기존 외부 recovery owner와 승인 절차가 필요하다.

baseline·후속 get_state의 received/rebuilt CUDA entry 수와 CPU by-value 수, runtime torch version/reductions 파일 SHA를 선언 facts로 보존한다. original module 검증과 실행 daemon 인증을 구분하며 count 완전성은 daemon counter 회수 완료가 아니다. release dispatch의 real/effective UID도 daemon UID와 별개로 보존하며 UID 일치만으로 기업 release 권한을 승인하지 않는다.
