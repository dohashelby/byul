# 불변 팩 원본

`PACK-BASELINE-31/`은 커밋 c8265f1의 `packs/PACK-BASELINE` 전체 bytes다. 파생 빌더의 `--seed`, 시험 fixture, 과거 Ref 재판정이 읽는다.
수정·재생성·자동 갱신하지 않는다. 정확한 Ref는 `tools/build_kubernetes_api_pack.py`의 `SEED_REF`다.
현행 팩은 `packs/PACK-BASELINE`이고 `tools/import_checklist.py`로 생성한다.

`PACK-BASELINE-33/`은 커밋 `4fd7ae37`의 정본 전체 bytes다. 과거 GPU18·Gateway·profiler 파생 절차의 정확한 seed Ref를 보존한다. 현행 @34로 덮어쓰거나 지원 고정값을 넓히지 않는다.
