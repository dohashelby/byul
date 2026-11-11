# B03 공급·기동 실제 파일 사전검증

`tools/b03_input_materialize.py`에 `b03-supply-pair-inputs/1` 입력 모드를 추가했다.
기존 선정7개와 PLT-01의 `b03-static-input-selection/1` 모드는 유지한다.

## 용도와 범위

실제 반환된 파일이 있을 때 GPU를 켜기 전에 다음을 확인한다.

- 봉인된 ENV JSON의 content hash와 파일 SHA
- 현재 PACK Ref와 정책의 pack ID/revision, EXAMPLE/DECLARED 출처
- 기존 공급·기동 정책 검증기의 필수값·타입과 유효 시각 범위
- normal/challenge의 별도 target·prepared 파일 SHA와 설정 경로 일치
- 원 `KubernetesSupplyBootExecutor._load()`를 통한 target/prepared 객체 해시·lab 조건·준비 자산 검증
- target의 ENV Ref 일치, 서로 다른 session과 target
- 원 파일 소비 뒤 exact pointer 재대조

검사 범위는 로컬 파일·정책·역할 결합이다. 전체 Kubernetes target 검증, 현재 worker/boot 신원, 생산자 자격·모든 native 지점·정책 승인권한·실제 실행/정리는 별도다.
최소 EXAMPLE target도 이 한정 검사에서는 통과할 수 있다. 결과의 `ready_for_GPU`는 항상 false다.
원 `_load`의 prepared 검증 시점과 로컬 포인터 재검사 시점을 기록하는 것이며 모든 자산의 영구 불변성이나 열린 FD lease를 발급하는 기능이 아니다.

## 입력 형식

모든 `path/sha256`은 **파일 bytes SHA**다. 정책의 `supply_*_target_sha256`와 `supply_*_prepared_sha256`는 기존 계약대로 **canonical JSON 객체 SHA**다. 둘을 서로 복사하지 않는다.

```json
{
  "schema": "b03-supply-pair-inputs/1",
  "check_id": "SUP-07",
  "pack_ref": {"kind":"PACK","id":"PACK-BASELINE","revision":33,"sha256":"<현재 PACK 객체 SHA>"},
  "environment": {"path":"<봉인 ENV JSON>","sha256":"<파일 SHA>"},
  "policy": {"path":"<정책 JSON>","sha256":"<파일 SHA>"},
  "config": {"path":"<검사용 config JSON>","sha256":"<파일 SHA>"},
  "roles": {
    "normal": {
      "target":{"path":"<normal target>","sha256":"<파일 SHA>"},
      "prepared":{"path":"<normal manifest.json>","sha256":"<파일 SHA>"}
    },
    "challenge": {
      "target":{"path":"<challenge target>","sha256":"<파일 SHA>"},
      "prepared":{"path":"<challenge manifest.json>","sha256":"<파일 SHA>"}
    }
  }
}
```

예시의 꺾쇠 값은 실행 가능한 SHA·Ref가 아니다. 새 ENV/POL/target이나 관측 결과를 생성하지 않는다.
정책은 EXAMPLE/DECLARED·DRAFT/CONFIRMED 중 원본 상태를 유지하며 `values`는 지정 점검 하나만 포함한다.
CONFIRMED라는 문자열만으로 승인권한이나 official 판정을 확인했다고 주장하지 않는다.
검사용 config는 JSON이며 guard 실행 CLI용 TOML과 구분한다.

```json
{
  "adapter":{"name":"<ENV의 engine.name>"},
  "executors":{"supply_boot":"kubernetes"},
  "supply_boot":{
    "normal":{"target":"<절대 경로>","prepared":"<절대 manifest 경로>"},
    "challenge":{"target":"<절대 경로>","prepared":"<절대 manifest 경로>"}
  }
}
```

점검 지원 여부와 필수 정책은 기존 `supply_boot_contract.CHECK_PROFILES`·`policy_types()`를 사용한다. B04의 SUP-06도 파일 사전검증만 가능하며 rank 배정·TP2 준비 완료를 뜻하지 않는다.
`worker_shard_input` 부가 입력은 이 모드에서 소비하지 않는다. 이를 제거해 점검 완료 범위를 축소하지 말고 B04의 별도 공통 입력 검증으로 인계한다.

## 실행

```text
cd update
uv run python -m tools.b03_input_materialize --spec <위 spec.json> --source-root <저장소 절대 경로> --output <저장소 밖의 새 출력>
```

기존 파일을 덮어쓰지 않으며 입력 검증이 실패하면 결과 디렉터리를 만들지 않는다.
이 모드는 `run`, `_collect_role`, Docker, kubectl, AWS 또는 Store/판정을 호출하지 않는다.
통과 결과는 `supply-pair-preflight.json`이고 EVD/RES/RPT가 아니다.

실제 운영 전에 다시 시각·신원·파일·정책을 확인해야 한다. 오래된 CPU 검사 결과로 현재 유효창을 대체하지 않는다.

## 고정 포인터 검증과 시험 범위

ENV·정책·config 세 포인터와 두 역할의 target/prepared 네 포인터는 모두 path/sha256 정확 두 키를 요구한다. path는 비어 있지 않은 문자열·NUL 없음, SHA는 정확64자리 소문자 hex여야 하며 null·잘못된 형식은 파일을 열기 전에 거부한다. 기존 다른 모드의 pinned/dependency 의미는 바꾸지 않았다.

원9개 시험 후 독립 검토에서 null-SHA 우회를 발견해 이 새 모드에만 보완했다. 최종 CPU25개는 기존9와 추가16(포인터별 null7, 비정규 SHA5, path4)의 범위다. 원 prepare_assets→원 executor._load 연결을 소비했고 GPU·원격 run/collection·제품 판정은 실행하지 않았다. 통과는 로컬 파일·정책·연결 검사이며 ready_for_GPU=false를 유지한다.
