# UPDATE inventory를 runtime 자산 검사에 연결

`tools/runtime_asset_check.py`는 UPDATE `guard.adapters.model_inventory.inventory(scope="all")`의 전체 파일 목록을 runtime manifest로 변환한다. 원 목록의 `inventory_sha256`·파일 수·총 크기를 검증하고 `bytes`를 `size`로 옮긴다. 원 inventory 해시와 runtime manifest 파일 해시는 서로 다른 값으로 보존한다.

```sh
PYTHONPATH=runtime/src python runtime/tools/runtime_asset_check.py \
  --inventory approved-inventory.json --root /observer-visible/model \
  --intent intent.json --target target.json --pod engine-pod.json \
  --approved-launch approved-launch.json --model-host-path /srv/serving/model \
  --manifest-out runtime-manifest.json --report-out asset-report.json
```

- `--inventory`는 검토·승인한 전체 목록이다. `scope=vllm` 부분 목록은 거부한다.
- `--root`는 검사 프로세스에서 실제 파일이 보이는 경로다. `--model-host-path`는 Pod의 hostPath와 대조하는 호스트 경로다. 두 경로의 동일 저장물 연결은 운영자가 확인한다.
- `--pod`는 같은 실행에서 확보한 Kubernetes Pod JSON이다. Pod UID·container ID·재시작 수·startedAt을 보고서에 보존한다. 수집 context·시각과 대상 Pod 선택은 증거 수집자가 기록한다.
- `--approved-launch`는 승인한 `{"command": [...], "args": [...]}` 문서다. 보고서는 인자 원문 대신 두 배열의 해시를 남긴다. 파일 자체는 보호된 승인 자료로 관리한다.
- model mount의 hostPath·readOnly와 cache mount의 현재 세대 hostPath를 비교한다. 동일 경로의 중복·하위 mount, subPath, 다른 이미지 digest·model revision·command/args는 불일치로 보고한다.
- 출력 파일은 덮어쓰지 않고 0600으로 새로 생성한다. 파일 비교나 배치 대조가 불일치하면 종료값 1이다. 형식 오류는 예외로 종료한다.

생성된 `runtime-manifest.json`은 기존 `serving-runtime mvp --asset-manifest runtime-manifest.json --asset-interval 60 ...`에 연결한다. 기존 collector가 디스크 변화를 주기 검사하고 revalidation 경로가 확인된 변경을 처리한다. Pod 설정과 세대의 연결 보고서는 일회 대조다. 다른 인스턴스 또는 변경된 배포에서는 새 Pod 증거로 다시 실행한다.

UPDATE HF inventory는 symlink 대상 bytes를 해시한다. runtime 파일 검사는 symlink를 거부하므로 실제 배치에서 일반 파일을 사용하는 조건을 유지한다. bridge가 symlink를 복사하거나 승인으로 바꾸지 않는다.

Pod API는 선언된 mount·실행 구성을 제공한다. 실제 worker의 파일 소비, mount namespace의 유효 권한, 패키지 inventory와 캐시 cold/warm 소비·호환성은 별도 실증 항목이다. 도구는 승인 기록을 생성하거나 배포를 변경하지 않는다.
